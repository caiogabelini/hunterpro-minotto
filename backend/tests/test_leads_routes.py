"""
Testes de integração de GET /api/leads (filtro por kanban_status),
GET /api/leads/{lead_id} (dossiê), PATCH /api/leads/{lead_id}/status,
POST /api/leads/{lead_id}/gerar-abordagem/{canal} e
GET /api/leads/{lead_id}/mensagens.

Mesmo padrão de fixtures de test_auth_routes.py: SQLite em memória
(StaticPool + check_same_thread=False, ver docstring lá pro porquê),
injetado via app.dependency_overrides[get_db] -- não precisa de Postgres
real rodando.
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.lead import KanbanStatus, Lead
from app.models.lead_message import LeadMessage
from app.models.user import User, UserRole
from app.scoring.compute_lead_score import montar_score_lead
from app.services.ai_enrichment import MensagemGerada

SENHA = "senha-forte-123"


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def client(db_session):
    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def usuario(db_session):
    usuario = User(email="cliente@minotto.com.br", senha_hash=hash_password(SENHA), role=UserRole.CLIENT.value, ativo=True)
    db_session.add(usuario)
    db_session.commit()
    db_session.refresh(usuario)
    return usuario


@pytest.fixture()
def token(usuario):
    return create_access_token({"user_id": usuario.id, "role": usuario.role})


def _criar_lead(db_session, **overrides) -> Lead:
    dados = dict(cnpj="11.111.111/0001-11", razao_social="Clínica Exemplo")
    dados.update(overrides)
    lead = Lead(**dados)
    db_session.add(lead)
    db_session.commit()
    db_session.refresh(lead)
    return lead


# --- GET /api/leads/{lead_id} ---


def test_get_lead_traz_todos_os_campos_do_dossie(client, db_session, token):
    lead = _criar_lead(
        db_session,
        cnae_principal="8630-5/03",
        anos_mercado=7,
        simples_nacional=True,
        situacao_cadastral="ATIVA",
        telefone="47999998888",
        site="https://clinicaexemplo.com.br",
        instagram="clinicaexemplo",
        linkedin_empresa="https://linkedin.com/company/clinicaexemplo",
        linkedin_decisor="https://linkedin.com/in/decisor",
        google_rating=4.7,
        google_avaliacoes=132,
        servicos="Cardiologia, Check-up",
        rqe_confirmado=True,
        rqe_fonte="cnes",
        cnes_codigo="1234567",
        divida_ativa_qtd_inscricoes=2,
        divida_ativa_valor_total=15234.78,
        divida_ativa_ajuizado=True,
        score_total=63.5,
        score_detalhes={"breakdown": [{"key": "divida_ativa_pgfn", "label": "Dívida ativa PGFN", "weight": 20, "layer": "estruturado", "points": 6.67}]},
        mensagem_abordagem="Olá, Dr. Fulano, vimos que...",
    )

    resp = client.get(f"/api/leads/{lead.id}", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["cnae_principal"] == "8630-5/03"
    assert corpo["anos_mercado"] == 7
    assert corpo["simples_nacional"] is True
    assert corpo["situacao_cadastral"] == "ATIVA"
    assert corpo["telefone"] == "47999998888"
    assert corpo["site"] == "https://clinicaexemplo.com.br"
    assert corpo["instagram"] == "clinicaexemplo"
    assert corpo["linkedin_empresa"] == "https://linkedin.com/company/clinicaexemplo"
    assert corpo["linkedin_decisor"] == "https://linkedin.com/in/decisor"
    assert corpo["google_rating"] == 4.7
    assert corpo["google_avaliacoes"] == 132
    assert corpo["servicos"] == "Cardiologia, Check-up"
    assert corpo["rqe_confirmado"] is True
    assert corpo["rqe_fonte"] == "cnes"
    assert corpo["cnes_codigo"] == "1234567"
    assert corpo["divida_ativa_qtd_inscricoes"] == 2
    assert corpo["divida_ativa_valor_total"] == 15234.78
    assert corpo["divida_ativa_ajuizado"] is True
    assert corpo["score_detalhes"]["breakdown"][0]["key"] == "divida_ativa_pgfn"
    assert corpo["mensagem_abordagem"] == "Olá, Dr. Fulano, vimos que..."
    assert corpo["updated_at"] is not None


def test_get_lead_campos_novos_default_none_quando_nao_preenchidos(client, db_session, token):
    """`mensagem_abordagem`, `divida_ativa_valor_total` e
    `divida_ativa_ajuizado` são todos opcionais -- um lead que nunca
    passou pela PGFN/geração de abordagem devolve null nesses campos,
    não erro nem valor ausente na resposta."""
    lead = _criar_lead(db_session)

    resp = client.get(f"/api/leads/{lead.id}", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["mensagem_abordagem"] is None
    assert corpo["divida_ativa_valor_total"] is None
    assert corpo["divida_ativa_ajuizado"] is None
    assert corpo["rqe_fonte"] is None
    assert corpo["cnes_codigo"] is None


def test_get_lead_com_score_calculado_retorna_breakdown_nao_vazio(client, db_session, token):
    """
    Regressão de bug reportado: o dossiê mostrava "Score ainda não
    calculado" mesmo com `score_detalhes` preenchido no banco. Os testes
    anteriores só cobriam um breakdown com 1 item hand-crafted -- este
    usa o motor de score de verdade (`montar_score_lead`, mesma função
    que `compute_lead_score` chama em app/workers/celery_app.py) pra
    gerar os 9 critérios completos, persiste no mesmo shape exato que
    `_persistir_enriquecimento_lead` usa (`{"breakdown": [...]}`, com
    `layer` já como string via `.value` -- não o Enum), e confere que a
    API devolve um `score_detalhes.breakdown` não-vazio com todos os
    campos que o frontend (`ScoreBreakdownItem` em `frontend/src/api.ts`)
    espera: `key`, `label`, `weight`, `layer`, `points`.
    """
    enriquecimentos = {
        "enrich_pgfn_divida_ativa": {"qtd_inscricoes_ativas": 3},
        "enrich_zona_franca": True,
        "enrich_cnes": {"encontrado_cnes": True},
        "enrich_receita_federal": {"decisor_nome": "Fulano de Tal", "situacao_cadastral": "ATIVA"},
        "enrich_site_firecrawl": {
            "rqe_mencionado": True,
            "scrape_sucesso": True,
            "site_content_resumo": "Clínica de cardiologia",
            "instagram": "clinicaexemplo",
        },
        "validate_whatsapp": {"whatsapp_ativo": True},
        "enrich_email": {"email_aprovado": True},
    }
    resultado = montar_score_lead(enriquecimentos)
    assert len(resultado.breakdown) == 9  # os 9 critérios de SCORING_CRITERIA

    lead = _criar_lead(
        db_session,
        score_total=resultado.score_total,
        score_detalhes={
            "breakdown": [
                {"key": d.key, "label": d.label, "weight": d.weight, "layer": d.layer.value, "points": d.points}
                for d in resultado.breakdown
            ]
        },
    )

    resp = client.get(f"/api/leads/{lead.id}", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    breakdown = resp.json()["score_detalhes"]["breakdown"]
    assert len(breakdown) == 9
    for item in breakdown:
        assert set(item.keys()) == {"key", "label", "weight", "layer", "points"}
    chaves = {item["key"] for item in breakdown}
    assert "divida_ativa_pgfn" in chaves
    assert "zona_franca_alc" in chaves
    assert "rqe_especialista" in chaves


def test_get_lead_inexistente_retorna_404(client, token):
    resp = client.get("/api/leads/id-que-nao-existe", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_get_lead_sem_token_retorna_401(client, db_session):
    lead = _criar_lead(db_session)
    resp = client.get(f"/api/leads/{lead.id}")
    assert resp.status_code == 401


# --- POST /api/leads/{lead_id}/gerar-abordagem/{canal} ---
#
# `gerar_mensagem_abordagem` (a chamada real à Anthropic) é mockada aqui
# via monkeypatch no módulo da rota (app.api.routes.leads), mesmo padrão
# de app/workers/celery_app.py mockar as etapas do pipeline por nome --
# o contrato da própria função (prompt/parsing/tratamento de erro por
# canal) já é coberto em tests/test_ai_enrichment.py.


def test_gerar_abordagem_email_com_sucesso_cria_lead_message(client, db_session, token, monkeypatch):
    lead = _criar_lead(db_session, razao_social="Clínica Exemplo")
    monkeypatch.setattr(
        "app.api.routes.leads.gerar_mensagem_abordagem",
        lambda dados_lead, canal: MensagemGerada(conteudo="Corpo do e-mail gerado.", assunto="Assunto gerado"),
    )

    resp = client.post(f"/api/leads/{lead.id}/gerar-abordagem/email", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["canal"] == "email"
    assert corpo["conteudo"] == "Corpo do e-mail gerado."
    assert corpo["assunto"] == "Assunto gerado"
    assert corpo["lead_id"] == lead.id

    linhas = db_session.query(LeadMessage).filter_by(lead_id=lead.id).all()
    assert len(linhas) == 1
    assert linhas[0].canal == "email"


def test_gerar_abordagem_whatsapp_sem_assunto(client, db_session, token, monkeypatch):
    lead = _criar_lead(db_session)
    monkeypatch.setattr(
        "app.api.routes.leads.gerar_mensagem_abordagem",
        lambda dados_lead, canal: MensagemGerada(conteudo="Mensagem curta de WhatsApp.", assunto=None),
    )

    resp = client.post(f"/api/leads/{lead.id}/gerar-abordagem/whatsapp", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["canal"] == "whatsapp"
    assert corpo["assunto"] is None


def test_gerar_abordagem_chama_service_com_canal_correto(client, db_session, token, monkeypatch):
    """Confirma que a rota passa o canal certo pra
    gerar_mensagem_abordagem -- pegaria um bug de tom trocado entre
    email/whatsapp."""
    canais_recebidos = []

    def fake(dados_lead, canal):
        canais_recebidos.append(canal)
        return MensagemGerada(conteudo="Mensagem.", assunto=None)

    monkeypatch.setattr("app.api.routes.leads.gerar_mensagem_abordagem", fake)
    lead = _criar_lead(db_session)

    client.post(f"/api/leads/{lead.id}/gerar-abordagem/whatsapp", headers={"Authorization": f"Bearer {token}"})
    client.post(f"/api/leads/{lead.id}/gerar-abordagem/email", headers={"Authorization": f"Bearer {token}"})

    assert canais_recebidos == ["whatsapp", "email"]


def test_gerar_abordagem_nao_sobrescreve_historico(client, db_session, token, monkeypatch):
    """Cada geração cria um LeadMessage novo -- gerar duas vezes pro
    mesmo canal deixa as duas linhas no banco (histórico), não
    sobrescreve a anterior."""
    lead = _criar_lead(db_session)
    respostas = iter(["Primeira versão.", "Segunda versão, regenerada."])
    monkeypatch.setattr(
        "app.api.routes.leads.gerar_mensagem_abordagem",
        lambda dados_lead, canal: MensagemGerada(conteudo=next(respostas), assunto=None),
    )

    client.post(f"/api/leads/{lead.id}/gerar-abordagem/whatsapp", headers={"Authorization": f"Bearer {token}"})
    resp2 = client.post(f"/api/leads/{lead.id}/gerar-abordagem/whatsapp", headers={"Authorization": f"Bearer {token}"})

    assert resp2.status_code == 200
    linhas = db_session.query(LeadMessage).filter_by(lead_id=lead.id, canal="whatsapp").all()
    assert len(linhas) == 2
    assert {l.conteudo for l in linhas} == {"Primeira versão.", "Segunda versão, regenerada."}


def test_gerar_abordagem_canal_invalido_retorna_422(client, db_session, token):
    lead = _criar_lead(db_session)
    resp = client.post(f"/api/leads/{lead.id}/gerar-abordagem/instagram", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 422


def test_gerar_abordagem_lead_inexistente_retorna_404(client, token):
    resp = client.post("/api/leads/id-que-nao-existe/gerar-abordagem/email", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_gerar_abordagem_resposta_vazia_da_ia_retorna_502_sem_criar_registro(client, db_session, token, monkeypatch):
    """gerar_mensagem_abordagem nunca lança -- devolve conteudo="" em
    erro/resposta vazia (ver test_ai_enrichment.py). A rota trata isso
    como 502, sem criar um LeadMessage vazio no banco."""
    lead = _criar_lead(db_session)
    monkeypatch.setattr(
        "app.api.routes.leads.gerar_mensagem_abordagem",
        lambda dados_lead, canal: MensagemGerada(conteudo="", assunto=None),
    )

    resp = client.post(f"/api/leads/{lead.id}/gerar-abordagem/email", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 502
    assert db_session.query(LeadMessage).filter_by(lead_id=lead.id).count() == 0


def test_gerar_abordagem_sem_token_retorna_401(client, db_session):
    lead = _criar_lead(db_session)
    resp = client.post(f"/api/leads/{lead.id}/gerar-abordagem/email")
    assert resp.status_code == 401


# --- POST /api/leads/{lead_id}/gerar-insights ---
#
# `gerar_insights_estrategicos` (a chamada real à Anthropic) é mockada
# aqui via monkeypatch no módulo da rota, mesmo padrão de
# gerar_mensagem_abordagem acima -- o contrato da própria função (prompt/
# parsing/tratamento de erro) já é coberto em tests/test_ai_enrichment.py.


_INSIGHTS_GERADOS = {
    "resumo_estrategico": "Lead com bom potencial, RQE confirmado e boa presença digital.",
    "potencial_oportunidade": "alto",
    "recomendacao_abordagem": ["Mencionar o rating no Google", "Focar em WhatsApp"],
    "estrategia_comunicacao": "Tom consultivo, direto ao ponto.",
    "cta_sugerido": "Podemos conversar 15 minutos essa semana?",
}


def test_gerar_insights_com_sucesso_persiste_e_retorna_lead(client, db_session, token, monkeypatch):
    lead = _criar_lead(db_session, razao_social="Clínica Exemplo")
    monkeypatch.setattr(
        "app.api.routes.leads.gerar_insights_estrategicos",
        lambda dados_lead: dict(_INSIGHTS_GERADOS),
    )

    resp = client.post(f"/api/leads/{lead.id}/gerar-insights", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["insights_ia"] == _INSIGHTS_GERADOS
    assert corpo["insights_gerado_em"] is not None

    db_session.refresh(lead)
    assert lead.insights_ia == _INSIGHTS_GERADOS
    assert lead.insights_gerado_em is not None


def test_gerar_insights_regenerar_sobrescreve_o_anterior(client, db_session, token, monkeypatch):
    """Diferente de LeadMessage (histórico completo), insights_ia não
    guarda histórico -- "Gerar novamente" sobrescreve o valor anterior."""
    lead = _criar_lead(db_session, insights_ia={"resumo_estrategico": "Antigo"}, insights_gerado_em=datetime(2026, 1, 1))
    monkeypatch.setattr(
        "app.api.routes.leads.gerar_insights_estrategicos",
        lambda dados_lead: dict(_INSIGHTS_GERADOS),
    )

    resp = client.post(f"/api/leads/{lead.id}/gerar-insights", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["insights_ia"] == _INSIGHTS_GERADOS
    assert corpo["insights_gerado_em"] != "2026-01-01T00:00:00"


def test_gerar_insights_passa_dados_completos_do_lead(client, db_session, token, monkeypatch):
    """Confirma que a rota alimenta gerar_insights_estrategicos com o
    contexto rico (score, PGFN, presença digital etc.), não só o
    subconjunto usado por gerar_mensagem_abordagem."""
    lead = _criar_lead(
        db_session,
        score_total=82.0,
        prioridade="A",
        divida_ativa_qtd_inscricoes=1,
        site="https://clinica.com.br",
        anos_mercado=9,
    )
    dados_recebidos = {}

    def fake(dados_lead):
        dados_recebidos.update(dados_lead)
        return dict(_INSIGHTS_GERADOS)

    monkeypatch.setattr("app.api.routes.leads.gerar_insights_estrategicos", fake)

    client.post(f"/api/leads/{lead.id}/gerar-insights", headers={"Authorization": f"Bearer {token}"})

    assert dados_recebidos["score_total"] == 82.0
    assert dados_recebidos["prioridade"] == "A"
    assert dados_recebidos["divida_ativa_qtd_inscricoes"] == 1
    assert dados_recebidos["site"] == "https://clinica.com.br"
    assert dados_recebidos["anos_mercado"] == 9


def test_gerar_insights_lead_inexistente_retorna_404(client, token):
    resp = client.post("/api/leads/id-que-nao-existe/gerar-insights", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_gerar_insights_resposta_vazia_da_ia_retorna_502_sem_persistir(client, db_session, token, monkeypatch):
    """gerar_insights_estrategicos nunca lança -- devolve {} em erro/
    resposta malformada (ver test_ai_enrichment.py). A rota trata isso
    como 502, sem sobrescrever insights_ia no banco."""
    lead = _criar_lead(db_session)
    monkeypatch.setattr("app.api.routes.leads.gerar_insights_estrategicos", lambda dados_lead: {})

    resp = client.post(f"/api/leads/{lead.id}/gerar-insights", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 502
    db_session.refresh(lead)
    assert lead.insights_ia is None
    assert lead.insights_gerado_em is None


def test_gerar_insights_sem_token_retorna_401(client, db_session):
    lead = _criar_lead(db_session)
    resp = client.post(f"/api/leads/{lead.id}/gerar-insights")
    assert resp.status_code == 401


def test_get_lead_expoe_insights_ia_e_insights_gerado_em(client, db_session, token):
    lead = _criar_lead(
        db_session,
        insights_ia=dict(_INSIGHTS_GERADOS),
        insights_gerado_em=datetime(2026, 8, 20, 12, 0, 0),
    )

    resp = client.get(f"/api/leads/{lead.id}", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["insights_ia"] == _INSIGHTS_GERADOS
    assert corpo["insights_gerado_em"] is not None


def test_get_lead_sem_insights_gerados_retorna_null(client, db_session, token):
    lead = _criar_lead(db_session)

    resp = client.get(f"/api/leads/{lead.id}", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["insights_ia"] is None
    assert corpo["insights_gerado_em"] is None


# --- GET /api/leads/{lead_id}/mensagens ---


def test_listar_mensagens_retorna_so_a_mais_recente_por_canal(client, db_session, token):
    # gerado_em explícito (não o default() do modelo) pra garantir ordem
    # determinística entre as duas mensagens de email -- não depender da
    # resolução do relógio entre dois datetime.utcnow() em sequência.
    lead = _criar_lead(db_session)
    db_session.add(
        LeadMessage(
            lead_id=lead.id,
            canal="email",
            conteudo="E-mail antigo",
            assunto="Assunto antigo",
            gerado_em=datetime(2026, 1, 1, 10, 0, 0),
        )
    )
    db_session.add(
        LeadMessage(
            lead_id=lead.id,
            canal="email",
            conteudo="E-mail mais recente",
            assunto="Assunto novo",
            gerado_em=datetime(2026, 1, 2, 10, 0, 0),
        )
    )
    db_session.add(LeadMessage(lead_id=lead.id, canal="whatsapp", conteudo="WhatsApp único"))
    db_session.commit()

    resp = client.get(f"/api/leads/{lead.id}/mensagens", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert len(corpo) == 2  # uma por canal, não as 3 linhas do histórico

    por_canal = {m["canal"]: m for m in corpo}
    assert por_canal["email"]["conteudo"] == "E-mail mais recente"
    assert por_canal["whatsapp"]["conteudo"] == "WhatsApp único"


def test_listar_mensagens_lead_sem_mensagens_retorna_lista_vazia(client, db_session, token):
    lead = _criar_lead(db_session)
    resp = client.get(f"/api/leads/{lead.id}/mensagens", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_listar_mensagens_lead_inexistente_retorna_404(client, token):
    resp = client.get("/api/leads/id-que-nao-existe/mensagens", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_listar_mensagens_sem_token_retorna_401(client, db_session):
    lead = _criar_lead(db_session)
    resp = client.get(f"/api/leads/{lead.id}/mensagens")
    assert resp.status_code == 401


# --- PATCH /api/leads/{lead_id}/status ---


def test_muda_status_valido(client, db_session, token):
    lead = _criar_lead(db_session)

    resp = client.patch(
        f"/api/leads/{lead.id}/status",
        json={"kanban_status": "qualificacao"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["kanban_status"] == "qualificacao"
    assert corpo["motivo_perda"] is None


def test_lead_novo_comeca_com_status_default(client, db_session, token):
    lead = _criar_lead(db_session)
    assert lead.kanban_status == KanbanStatus.NOVO_LEAD.value


def test_perdido_sem_motivo_retorna_erro(client, db_session, token):
    lead = _criar_lead(db_session)

    resp = client.patch(
        f"/api/leads/{lead.id}/status",
        json={"kanban_status": "perdido"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 422


def test_perdido_com_motivo_funciona(client, db_session, token):
    lead = _criar_lead(db_session)

    resp = client.patch(
        f"/api/leads/{lead.id}/status",
        json={"kanban_status": "perdido", "motivo_perda": "Fechou com concorrente"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["kanban_status"] == "perdido"
    assert corpo["motivo_perda"] == "Fechou com concorrente"


def test_ganho_sem_dados_de_fechamento_retorna_erro(client, db_session, token):
    lead = _criar_lead(db_session)

    resp = client.patch(
        f"/api/leads/{lead.id}/status",
        json={"kanban_status": "ganho"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 422
    db_session.refresh(lead)
    assert lead.kanban_status != "ganho"  # não persistiu nada


def test_ganho_sem_servicos_vendidos_retorna_erro(client, db_session, token):
    lead = _criar_lead(db_session)

    resp = client.patch(
        f"/api/leads/{lead.id}/status",
        json={"kanban_status": "ganho", "tipo_contrato": "pontual", "valor_fechamento": 1500.0},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 422
    assert "servicos_vendidos" in resp.json()["detail"]


def test_ganho_com_lista_de_servicos_vazia_retorna_erro(client, db_session, token):
    lead = _criar_lead(db_session)

    resp = client.patch(
        f"/api/leads/{lead.id}/status",
        json={
            "kanban_status": "ganho",
            "servicos_vendidos": [],
            "tipo_contrato": "pontual",
            "valor_fechamento": 1500.0,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 422
    assert "servicos_vendidos" in resp.json()["detail"]


def test_ganho_sem_tipo_contrato_retorna_erro(client, db_session, token):
    lead = _criar_lead(db_session)

    resp = client.patch(
        f"/api/leads/{lead.id}/status",
        json={
            "kanban_status": "ganho",
            "servicos_vendidos": ["contabilidade_consultiva"],
            "valor_fechamento": 1500.0,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 422
    assert "tipo_contrato" in resp.json()["detail"]


def test_ganho_com_tipo_contrato_invalido_retorna_erro(client, db_session, token):
    lead = _criar_lead(db_session)

    resp = client.patch(
        f"/api/leads/{lead.id}/status",
        json={
            "kanban_status": "ganho",
            "servicos_vendidos": ["contabilidade_consultiva"],
            "tipo_contrato": "anual",
            "valor_fechamento": 1500.0,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 422
    assert "tipo_contrato" in resp.json()["detail"]


def test_ganho_sem_valor_fechamento_retorna_erro(client, db_session, token):
    lead = _criar_lead(db_session)

    resp = client.patch(
        f"/api/leads/{lead.id}/status",
        json={"kanban_status": "ganho", "servicos_vendidos": ["contabilidade_consultiva"], "tipo_contrato": "pontual"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 422
    assert "valor_fechamento" in resp.json()["detail"]


def test_ganho_com_valor_fechamento_zero_ou_negativo_retorna_erro(client, db_session, token):
    lead = _criar_lead(db_session)

    for valor in (0, -100.0):
        resp = client.patch(
            f"/api/leads/{lead.id}/status",
            json={
                "kanban_status": "ganho",
                "servicos_vendidos": ["contabilidade_consultiva"],
                "tipo_contrato": "pontual",
                "valor_fechamento": valor,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422
        assert "valor_fechamento" in resp.json()["detail"]


def test_ganho_com_todos_os_campos_persiste_corretamente(client, db_session, token):
    lead = _criar_lead(db_session)

    resp = client.patch(
        f"/api/leads/{lead.id}/status",
        json={
            "kanban_status": "ganho",
            "servicos_vendidos": ["contabilidade_consultiva", "planejamento_tributario"],
            "tipo_contrato": "recorrente",
            "valor_fechamento": 2500.0,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["kanban_status"] == "ganho"
    assert corpo["servicos_vendidos"] == ["contabilidade_consultiva", "planejamento_tributario"]
    assert corpo["tipo_contrato"] == "recorrente"
    assert corpo["valor_fechamento"] == 2500.0

    db_session.refresh(lead)
    assert lead.servicos_vendidos == ["contabilidade_consultiva", "planejamento_tributario"]
    assert lead.tipo_contrato == "recorrente"
    assert lead.valor_fechamento == 2500.0


def test_mover_lead_ganho_pra_outra_etapa_nao_apaga_dados_de_fechamento(client, db_session, token):
    """Diferente de motivo_perda (limpo ao sair de "perdido"), os dados
    de fechamento representam uma venda que já aconteceu -- continuam
    persistidos mesmo que o lead seja movido de volta por engano ou
    reclassificado depois (decisão documentada no docstring da rota)."""
    lead = _criar_lead(db_session)
    client.patch(
        f"/api/leads/{lead.id}/status",
        json={
            "kanban_status": "ganho",
            "servicos_vendidos": ["contabilidade_consultiva"],
            "tipo_contrato": "pontual",
            "valor_fechamento": 900.0,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = client.patch(
        f"/api/leads/{lead.id}/status",
        json={"kanban_status": "negociacao"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["kanban_status"] == "negociacao"
    assert corpo["servicos_vendidos"] == ["contabilidade_consultiva"]
    assert corpo["tipo_contrato"] == "pontual"
    assert corpo["valor_fechamento"] == 900.0


def test_ganho_com_servico_outro_texto_livre_persiste_na_lista(client, db_session, token):
    """`servicos_vendidos` aceita qualquer string -- inclusive texto
    livre digitado em "Outro" no frontend, sem validar contra os 4
    valores fixos aqui no backend (validação de opções é do frontend)."""
    lead = _criar_lead(db_session)

    resp = client.patch(
        f"/api/leads/{lead.id}/status",
        json={
            "kanban_status": "ganho",
            "servicos_vendidos": ["abertura_legalizacao", "Auditoria fiscal personalizada"],
            "tipo_contrato": "pontual",
            "valor_fechamento": 800.0,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["servicos_vendidos"] == ["abertura_legalizacao", "Auditoria fiscal personalizada"]


def test_mover_pra_etapa_intermediaria_nao_exige_dados_de_fechamento_nem_de_perda(client, db_session, token):
    """Mover pra uma etapa que não é "ganho" nem "perdido" continua sem
    exigir motivo_perda NEM os 3 campos de fechamento."""
    lead = _criar_lead(db_session)

    resp = client.patch(
        f"/api/leads/{lead.id}/status",
        json={"kanban_status": "proposta_enviada"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["kanban_status"] == "proposta_enviada"
    assert corpo["servicos_vendidos"] is None
    assert corpo["tipo_contrato"] is None
    assert corpo["valor_fechamento"] is None


def test_status_invalido_retorna_erro(client, db_session, token):
    lead = _criar_lead(db_session)

    resp = client.patch(
        f"/api/leads/{lead.id}/status",
        json={"kanban_status": "status-que-nao-existe"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 422


def test_lead_inexistente_retorna_404(client, token):
    resp = client.patch(
        "/api/leads/id-que-nao-existe/status",
        json={"kanban_status": "contatado"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_patch_status_sem_token_retorna_401(client, db_session):
    lead = _criar_lead(db_session)
    resp = client.patch(f"/api/leads/{lead.id}/status", json={"kanban_status": "contatado"})
    assert resp.status_code == 401


# --- GET /api/leads?kanban_status=... ---


def test_filtro_por_kanban_status(client, db_session, token):
    _criar_lead(db_session, cnpj="11.111.111/0001-11", razao_social="Novo", kanban_status="novo_lead")
    _criar_lead(db_session, cnpj="22.222.222/0001-22", razao_social="Em negociação", kanban_status="negociacao")

    resp = client.get("/api/leads", params={"kanban_status": "negociacao"}, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert len(corpo) == 1
    assert corpo[0]["razao_social"] == "Em negociação"


def test_sem_filtro_retorna_todos(client, db_session, token):
    _criar_lead(db_session, cnpj="11.111.111/0001-11", razao_social="A")
    _criar_lead(db_session, cnpj="22.222.222/0001-22", razao_social="B", kanban_status="ganho")

    resp = client.get("/api/leads", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert len(resp.json()) == 2
