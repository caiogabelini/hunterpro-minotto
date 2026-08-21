"""
Testes de GET /api/dashboard/summary, GET /api/dashboard/acoes-recomendadas,
GET/PUT /api/dashboard/premissas, GET /api/dashboard/funil e
GET /api/dashboard/motivos-perda.

Mesmo padrão de fixtures de test_leads_routes.py: SQLite em memória
(StaticPool + check_same_thread=False), injetado via
app.dependency_overrides[get_db] -- não precisa de Postgres real rodando.
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.lead import Lead
from app.models.user import User, UserRole

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
    dados = dict(cnpj=f"11.111.111/0001-{overrides.pop('sufixo', '11')}", razao_social="Clínica Exemplo")
    dados.update(overrides)
    lead = Lead(**dados)
    db_session.add(lead)
    db_session.commit()
    db_session.refresh(lead)
    return lead


# --- GET /api/dashboard/summary ---


def test_summary_leads_no_mes_conta_so_leads_do_mes_corrente(client, db_session, token):
    agora = datetime.utcnow()
    inicio_mes = datetime(agora.year, agora.month, 1)
    _criar_lead(db_session, sufixo="11", created_at=inicio_mes + timedelta(days=1))
    _criar_lead(db_session, sufixo="22", created_at=inicio_mes + timedelta(days=2))
    _criar_lead(db_session, sufixo="33", created_at=inicio_mes - timedelta(days=5))  # mês anterior

    resp = client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["leads_no_mes"] == 2
    assert corpo["leads_no_mes_limite"] == settings.PLANO_LEADS_MES == 50


def test_summary_score_medio_ignora_leads_sem_score(client, db_session, token):
    _criar_lead(db_session, sufixo="11", score_total=80.0)
    _criar_lead(db_session, sufixo="22", score_total=60.0)
    _criar_lead(db_session, sufixo="33", score_total=None)

    resp = client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["score_medio"] == 70.0


def test_summary_score_medio_zero_sem_nenhum_lead_pontuado(client, db_session, token):
    _criar_lead(db_session, sufixo="11", score_total=None)

    resp = client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["score_medio"] == 0.0


def test_summary_leads_em_negociacao_conta_proposta_e_negociacao(client, db_session, token):
    _criar_lead(db_session, sufixo="11", kanban_status="proposta_enviada")
    _criar_lead(db_session, sufixo="22", kanban_status="negociacao")
    _criar_lead(db_session, sufixo="33", kanban_status="contatado")
    _criar_lead(db_session, sufixo="44", kanban_status="ganho")

    resp = client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["leads_em_negociacao"] == 2


def test_summary_taxa_conversao_calculada_corretamente(client, db_session, token):
    _criar_lead(db_session, sufixo="11", kanban_status="ganho")
    _criar_lead(db_session, sufixo="22", kanban_status="ganho")
    _criar_lead(db_session, sufixo="33", kanban_status="novo_lead")
    _criar_lead(db_session, sufixo="44", kanban_status="perdido")

    resp = client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["taxa_conversao"] == 50.0  # 2/4 * 100


def test_summary_taxa_conversao_zero_sem_leads_nao_quebra(client, token):
    resp = client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["taxa_conversao"] == 0.0
    assert corpo["score_medio"] == 0.0
    assert corpo["leads_no_mes"] == 0
    assert corpo["leads_em_negociacao"] == 0
    assert corpo["receita_fechada_pontual"] == 0.0
    assert corpo["receita_fechada_recorrente_mensal"] == 0.0
    assert corpo["receita_fechada_total"] == 0.0


def test_summary_receita_fechada_soma_pontual_e_recorrente_separadamente(client, db_session, token):
    _criar_lead(db_session, sufixo="11", kanban_status="ganho", tipo_contrato="pontual", valor_fechamento=1000.0)
    _criar_lead(db_session, sufixo="22", kanban_status="ganho", tipo_contrato="pontual", valor_fechamento=500.0)
    _criar_lead(db_session, sufixo="33", kanban_status="ganho", tipo_contrato="recorrente", valor_fechamento=300.0)
    _criar_lead(db_session, sufixo="44", kanban_status="ganho", tipo_contrato="recorrente", valor_fechamento=200.0)
    # não deve entrar na soma -- não é "ganho"
    _criar_lead(db_session, sufixo="55", kanban_status="negociacao", tipo_contrato="pontual", valor_fechamento=9999.0)

    resp = client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["receita_fechada_pontual"] == 1500.0
    assert corpo["receita_fechada_recorrente_mensal"] == 500.0
    assert corpo["receita_fechada_total"] == 2000.0


def test_summary_receita_fechada_zero_sem_nenhum_ganho(client, db_session, token):
    _criar_lead(db_session, sufixo="11", kanban_status="novo_lead")
    _criar_lead(db_session, sufixo="22", kanban_status="perdido")

    resp = client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["receita_fechada_pontual"] == 0.0
    assert corpo["receita_fechada_recorrente_mensal"] == 0.0
    assert corpo["receita_fechada_total"] == 0.0


def test_summary_receita_fechada_ignora_ganho_sem_valor_fechamento(client, db_session, token):
    """Lead "ganho" sem valor_fechamento preenchido (não deveria
    acontecer via a API, que exige o campo pra mover pra "ganho", mas
    pode existir em dado antigo/manual) não quebra a soma -- só não
    contribui com nada, tratado como 0."""
    _criar_lead(db_session, sufixo="11", kanban_status="ganho", tipo_contrato="pontual", valor_fechamento=None)
    _criar_lead(db_session, sufixo="22", kanban_status="ganho", tipo_contrato="pontual", valor_fechamento=700.0)

    resp = client.get("/api/dashboard/summary", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["receita_fechada_pontual"] == 700.0
    assert corpo["receita_fechada_total"] == 700.0


def test_summary_sem_token_retorna_401(client):
    resp = client.get("/api/dashboard/summary")
    assert resp.status_code == 401


# --- GET /api/dashboard/acoes-recomendadas ---


def test_acoes_recomendadas_retorna_as_quatro_categorias(client, db_session, token):
    # sufixo=22 e 44 recebem telefone/e-mail só pra não caírem também na
    # categoria "revisão manual" (que é exclusivamente decisor_nome +
    # telefone + email todos nulos) -- cada lead aqui deve contar pra
    # exatamente 1 das 4 categorias, não mais de uma.
    _criar_lead(db_session, sufixo="11", decisor_nome="Dr. Fulano", kanban_status="novo_lead")
    _criar_lead(db_session, sufixo="22", prioridade="A", kanban_status="novo_lead", telefone="47999998888")
    _criar_lead(db_session, sufixo="33", decisor_nome=None, telefone=None, email=None)
    _criar_lead(
        db_session,
        sufixo="44",
        divida_ativa_qtd_inscricoes=2,
        kanban_status="qualificacao",
        email="contato@clinicaexemplo.com.br",
    )

    resp = client.get("/api/dashboard/acoes-recomendadas", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert len(corpo) == 4
    chaves = {item["filtro_chave"] for item in corpo}
    assert chaves == {"decisor_identificado", "prioridade_a", "revisao_manual", "divida_pgfn_nao_abordada"}
    for item in corpo:
        assert item["quantidade"] == 1


def test_acoes_recomendadas_decisor_identificado_exige_novo_lead(client, db_session, token):
    """decisor_nome preenchido mas já fora de novo_lead não deve contar
    pra essa categoria."""
    _criar_lead(db_session, sufixo="11", decisor_nome="Dr. Fulano", kanban_status="contatado")

    resp = client.get("/api/dashboard/acoes-recomendadas", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json() == []


def test_acoes_recomendadas_omite_categorias_com_zero(client, db_session, token):
    _criar_lead(db_session, sufixo="11", prioridade="A", kanban_status="novo_lead", telefone="47999998888")

    resp = client.get("/api/dashboard/acoes-recomendadas", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert len(corpo) == 1
    assert corpo[0]["filtro_chave"] == "prioridade_a"
    assert corpo[0]["kanban_status_filtro"] == "novo_lead"


def test_acoes_recomendadas_lista_vazia_sem_nenhum_lead(client, token):
    resp = client.get("/api/dashboard/acoes-recomendadas", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_acoes_recomendadas_sem_token_retorna_401(client):
    resp = client.get("/api/dashboard/acoes-recomendadas")
    assert resp.status_code == 401


# --- GET/PUT /api/dashboard/premissas ---


def test_get_premissas_devolve_padrao_quando_nunca_salvou(client, db_session, token):
    _criar_lead(db_session, sufixo="11", prioridade="A")
    _criar_lead(db_session, sufixo="22", prioridade="B")
    _criar_lead(db_session, sufixo="33", prioridade="C")

    resp = client.get("/api/dashboard/premissas", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["leads_qualificados"] == 2  # A + B, não C
    assert corpo["taxa_fechamento"] == 20.0
    assert corpo["ticket_medio"] == 1500.0


def test_put_premissas_persiste_e_get_devolve_o_salvo(client, db_session, token, usuario):
    resp_put = client.put(
        "/api/dashboard/premissas",
        json={"leads_qualificados": 30, "taxa_fechamento": 25.5, "ticket_medio": 2000.0},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp_put.status_code == 200
    assert resp_put.json() == {"leads_qualificados": 30, "taxa_fechamento": 25.5, "ticket_medio": 2000.0}

    db_session.refresh(usuario)
    assert usuario.dashboard_premissas == {"leads_qualificados": 30, "taxa_fechamento": 25.5, "ticket_medio": 2000.0}

    resp_get = client.get("/api/dashboard/premissas", headers={"Authorization": f"Bearer {token}"})
    assert resp_get.status_code == 200
    assert resp_get.json() == {"leads_qualificados": 30, "taxa_fechamento": 25.5, "ticket_medio": 2000.0}


def test_put_premissas_e_depois_deste_usuario_nao_afeta_outro(client, db_session, token):
    client.put(
        "/api/dashboard/premissas",
        json={"leads_qualificados": 30, "taxa_fechamento": 25.5, "ticket_medio": 2000.0},
        headers={"Authorization": f"Bearer {token}"},
    )

    outro = User(email="outro@minotto.com.br", senha_hash=hash_password(SENHA), role=UserRole.CLIENT.value, ativo=True)
    db_session.add(outro)
    db_session.commit()
    db_session.refresh(outro)
    token_outro = create_access_token({"user_id": outro.id, "role": outro.role})

    resp = client.get("/api/dashboard/premissas", headers={"Authorization": f"Bearer {token_outro}"})
    assert resp.status_code == 200
    assert resp.json()["leads_qualificados"] == 0  # padrão calculado, não o valor salvo pelo primeiro usuário


def test_get_premissas_sem_token_retorna_401(client):
    resp = client.get("/api/dashboard/premissas")
    assert resp.status_code == 401


def test_put_premissas_sem_token_retorna_401(client):
    resp = client.put(
        "/api/dashboard/premissas",
        json={"leads_qualificados": 10, "taxa_fechamento": 20.0, "ticket_medio": 1000.0},
    )
    assert resp.status_code == 401


# --- GET /api/dashboard/funil ---


def test_funil_retorna_as_nove_etapas_mesmo_com_zero(client, db_session, token):
    _criar_lead(db_session, sufixo="11", kanban_status="novo_lead")

    resp = client.get("/api/dashboard/funil", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert len(corpo) == 9
    assert [item["status"] for item in corpo] == [
        "novo_lead",
        "qualificacao",
        "contatado",
        "respondeu",
        "reuniao",
        "proposta_enviada",
        "negociacao",
        "ganho",
        "perdido",
    ]
    # todas as outras 8 etapas ficam com quantidade 0, não são omitidas
    por_status = {item["status"]: item for item in corpo}
    assert por_status["novo_lead"]["quantidade"] == 1
    assert por_status["qualificacao"]["quantidade"] == 0
    assert por_status["qualificacao"]["label"] == "Qualificação"


def test_funil_inclui_perdido_por_ultimo_com_label(client, db_session, token):
    _criar_lead(db_session, sufixo="11", kanban_status="perdido", motivo_perda="Sem orçamento")

    resp = client.get("/api/dashboard/funil", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo[-1]["status"] == "perdido"
    assert corpo[-1]["label"] == "Perdido"
    assert corpo[-1]["quantidade"] == 1


def test_funil_percentual_calculado_sobre_total_de_leads_incluindo_perdidos(client, db_session, token):
    _criar_lead(db_session, sufixo="11", kanban_status="novo_lead")
    _criar_lead(db_session, sufixo="22", kanban_status="novo_lead")
    _criar_lead(db_session, sufixo="33", kanban_status="ganho")
    _criar_lead(db_session, sufixo="44", kanban_status="perdido", motivo_perda="Preço alto")

    resp = client.get("/api/dashboard/funil", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    por_status = {item["status"]: item for item in resp.json()}
    # total = 4 leads, "perdido" agora ENTRA no denominador
    assert por_status["novo_lead"]["quantidade"] == 2
    assert por_status["novo_lead"]["percentual"] == pytest.approx(50.0)
    assert por_status["ganho"]["quantidade"] == 1
    assert por_status["ganho"]["percentual"] == pytest.approx(25.0)
    assert por_status["perdido"]["quantidade"] == 1
    assert por_status["perdido"]["percentual"] == pytest.approx(25.0)


def test_funil_soma_dos_percentuais_fecha_em_cem(client, db_session, token):
    """Com "perdido" no denominador, a soma das 9 barras precisa fechar
    em ~100% (tolerância só pelo arredondamento de 1 casa decimal por
    etapa) -- o motivo inteiro da mudança desta sessão."""
    _criar_lead(db_session, sufixo="11", kanban_status="novo_lead")
    _criar_lead(db_session, sufixo="22", kanban_status="qualificacao")
    _criar_lead(db_session, sufixo="33", kanban_status="contatado")
    _criar_lead(db_session, sufixo="44", kanban_status="ganho")
    _criar_lead(db_session, sufixo="55", kanban_status="ganho")
    _criar_lead(db_session, sufixo="66", kanban_status="perdido", motivo_perda="Sem orçamento")
    _criar_lead(db_session, sufixo="77", kanban_status="perdido", motivo_perda="Preço alto")

    resp = client.get("/api/dashboard/funil", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    soma_percentuais = sum(item["percentual"] for item in corpo)
    assert soma_percentuais == pytest.approx(100.0, abs=0.5)

    # e o total de leads contados bate com a contagem real no banco
    soma_quantidades = sum(item["quantidade"] for item in corpo)
    total_no_banco = db_session.query(Lead).count()
    assert soma_quantidades == total_no_banco == 7


def test_funil_sem_nenhum_lead_todas_as_etapas_zeradas_sem_quebrar(client, token):
    resp = client.get("/api/dashboard/funil", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert len(corpo) == 9
    assert all(item["quantidade"] == 0 and item["percentual"] == 0.0 for item in corpo)


def test_funil_sem_token_retorna_401(client):
    resp = client.get("/api/dashboard/funil")
    assert resp.status_code == 401


# --- GET /api/dashboard/motivos-perda ---


def test_motivos_perda_agrupa_por_texto_exato_ordenado_por_quantidade(client, db_session, token):
    _criar_lead(db_session, sufixo="11", kanban_status="perdido", motivo_perda="Preço alto")
    _criar_lead(db_session, sufixo="22", kanban_status="perdido", motivo_perda="Preço alto")
    _criar_lead(db_session, sufixo="33", kanban_status="perdido", motivo_perda="Preço alto")
    _criar_lead(db_session, sufixo="44", kanban_status="perdido", motivo_perda="Fechou com concorrente")
    _criar_lead(db_session, sufixo="55", kanban_status="perdido", motivo_perda="Achou caro")  # texto diferente, não agrupa com "Preço alto"

    resp = client.get("/api/dashboard/motivos-perda", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo[0] == {"motivo": "Preço alto", "quantidade": 3}
    assert {"motivo": "Fechou com concorrente", "quantidade": 1} in corpo
    assert {"motivo": "Achou caro", "quantidade": 1} in corpo
    assert len(corpo) == 3


def test_motivos_perda_ignora_leads_nao_perdidos_e_sem_motivo(client, db_session, token):
    _criar_lead(db_session, sufixo="11", kanban_status="novo_lead")  # nem perdido
    _criar_lead(db_session, sufixo="22", kanban_status="perdido", motivo_perda=None)  # perdido sem motivo (não deveria existir via API, mas defensivo)

    resp = client.get("/api/dashboard/motivos-perda", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json() == []


def test_motivos_perda_lista_vazia_sem_nenhum_lead_perdido(client, token):
    resp = client.get("/api/dashboard/motivos-perda", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_motivos_perda_limita_a_dez_resultados(client, db_session, token):
    for i in range(12):
        _criar_lead(db_session, sufixo=f"{i:02d}", kanban_status="perdido", motivo_perda=f"Motivo {i}")

    resp = client.get("/api/dashboard/motivos-perda", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert len(resp.json()) == 10


def test_motivos_perda_sem_token_retorna_401(client):
    resp = client.get("/api/dashboard/motivos-perda")
    assert resp.status_code == 401
