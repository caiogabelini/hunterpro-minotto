"""
Testes de integração de GET /api/leads (filtro por kanban_status) e
PATCH /api/leads/{lead_id}/status.

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
