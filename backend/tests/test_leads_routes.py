"""
Testes de integração de GET /api/leads (filtro por kanban_status),
GET /api/leads/{lead_id} (dossiê) e PATCH /api/leads/{lead_id}/status.

Mesmo padrão de fixtures de test_auth_routes.py: SQLite em memória
(StaticPool + check_same_thread=False, ver docstring lá pro porquê),
injetado via app.dependency_overrides[get_db] -- não precisa de Postgres
real rodando.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.lead import KanbanStatus, Lead
from app.models.user import User, UserRole
from app.scoring.compute_lead_score import montar_score_lead

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


# --- POST /api/leads/{lead_id}/gerar-abordagem ---
#
# `gerar_mensagem_abordagem` (a chamada real à Anthropic) é mockada aqui
# via monkeypatch no módulo da rota (app.api.routes.leads), mesmo padrão
# de app/workers/celery_app.py mockar as etapas do pipeline por nome --
# o contrato da própria função (prompt/parsing/tratamento de erro) já é
# coberto em tests/test_ai_enrichment.py.


def test_gerar_abordagem_com_sucesso(client, db_session, token, monkeypatch):
    lead = _criar_lead(db_session, razao_social="Clínica Exemplo")
    monkeypatch.setattr(
        "app.api.routes.leads.gerar_mensagem_abordagem",
        lambda dados_lead: "Olá! Vimos que a Clínica Exemplo pode se beneficiar de uma conversa rápida.",
    )

    resp = client.post(f"/api/leads/{lead.id}/gerar-abordagem", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["mensagem_abordagem"] == "Olá! Vimos que a Clínica Exemplo pode se beneficiar de uma conversa rápida."


def test_gerar_abordagem_mensagem_e_persistida_no_banco(client, db_session, token, monkeypatch):
    lead = _criar_lead(db_session)
    monkeypatch.setattr("app.api.routes.leads.gerar_mensagem_abordagem", lambda dados_lead: "Mensagem gerada pela IA.")

    resp = client.post(f"/api/leads/{lead.id}/gerar-abordagem", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200

    db_session.refresh(lead)
    assert lead.mensagem_abordagem == "Mensagem gerada pela IA."


def test_gerar_abordagem_lead_inexistente_retorna_404(client, token):
    resp = client.post("/api/leads/id-que-nao-existe/gerar-abordagem", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_gerar_abordagem_resposta_vazia_da_ia_retorna_502_sem_persistir(client, db_session, token, monkeypatch):
    """gerar_mensagem_abordagem nunca lança -- devolve "" em erro/resposta
    vazia (ver test_ai_enrichment.py). A rota trata isso como 502, sem
    sobrescrever mensagem_abordagem com uma string vazia."""
    lead = _criar_lead(db_session, mensagem_abordagem="Mensagem antiga, não deve ser apagada")
    monkeypatch.setattr("app.api.routes.leads.gerar_mensagem_abordagem", lambda dados_lead: "")

    resp = client.post(f"/api/leads/{lead.id}/gerar-abordagem", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 502
    db_session.refresh(lead)
    assert lead.mensagem_abordagem == "Mensagem antiga, não deve ser apagada"


def test_gerar_abordagem_sem_token_retorna_401(client, db_session):
    lead = _criar_lead(db_session)
    resp = client.post(f"/api/leads/{lead.id}/gerar-abordagem")
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
