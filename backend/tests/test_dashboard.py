"""
Testes de GET /api/dashboard/summary, GET /api/dashboard/acoes-recomendadas
e GET/PUT /api/dashboard/premissas.

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
