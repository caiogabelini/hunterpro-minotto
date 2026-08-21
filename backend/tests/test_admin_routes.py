"""
Testes de POST/GET /api/admin/buscas e GET /api/admin/buscas/{busca_id}.

Mesmo padrão de fixtures de test_leads_routes.py: SQLite em memória
(StaticPool + check_same_thread=False), injetado via
app.dependency_overrides[get_db]. `executar_busca_mensal.delay(...)` é
substituída por um fake registrador (nunca conecta num broker Celery
real) -- mesmo raciocínio de "monkeypatch no call-site", ver
app/api/routes/admin.py (`from app.workers.celery_app import
executar_busca_mensal` -- o nome vive no namespace do módulo da rota).
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.busca_leads import BuscaLeads, StatusBusca
from app.models.user import User, UserRole

SENHA = "senha-forte-123"


class _FakeTask:
    """Registra as chamadas a `.delay(...)` sem tocar num broker real."""

    def __init__(self):
        self.chamadas: list[str] = []

    def delay(self, busca_id: str):
        self.chamadas.append(busca_id)


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
def fake_task(monkeypatch):
    fake = _FakeTask()
    monkeypatch.setattr("app.api.routes.admin.executar_busca_mensal", fake)
    return fake


@pytest.fixture()
def admin(db_session):
    usuario = User(email="admin@minotto.com.br", senha_hash=hash_password(SENHA), role=UserRole.ADMIN.value, ativo=True)
    db_session.add(usuario)
    db_session.commit()
    db_session.refresh(usuario)
    return usuario


@pytest.fixture()
def cliente(db_session):
    usuario = User(email="douglas@minotto.com.br", senha_hash=hash_password(SENHA), role=UserRole.CLIENT.value, ativo=True)
    db_session.add(usuario)
    db_session.commit()
    db_session.refresh(usuario)
    return usuario


@pytest.fixture()
def token_admin(admin):
    return create_access_token({"user_id": admin.id, "role": admin.role})


@pytest.fixture()
def token_cliente(cliente):
    return create_access_token({"user_id": cliente.id, "role": cliente.role})


def _criar_busca(db_session, admin, **overrides) -> BuscaLeads:
    dados = dict(iniciado_por_id=admin.id, status=StatusBusca.EXECUTANDO.value)
    dados.update(overrides)
    busca = BuscaLeads(**dados)
    db_session.add(busca)
    db_session.commit()
    db_session.refresh(busca)
    return busca


# --- POST /api/admin/buscas ---


def test_post_busca_admin_cria_registro_e_dispara_task(client, db_session, admin, token_admin, fake_task):
    resp = client.post("/api/admin/buscas", headers={"Authorization": f"Bearer {token_admin}"})

    assert resp.status_code == 201
    corpo = resp.json()
    assert corpo["status"] == "executando"
    assert corpo["iniciado_por_id"] == admin.id
    assert corpo["concluido_em"] is None

    # a task foi disparada de verdade, com o id do registro recém-criado
    assert fake_task.chamadas == [corpo["id"]]

    # e o registro existe no banco
    persistido = db_session.get(BuscaLeads, corpo["id"])
    assert persistido is not None
    assert persistido.status == StatusBusca.EXECUTANDO.value


def test_post_busca_client_retorna_403(client, token_cliente, fake_task):
    resp = client.post("/api/admin/buscas", headers={"Authorization": f"Bearer {token_cliente}"})

    assert resp.status_code == 403
    assert fake_task.chamadas == []  # nunca chega a disparar a task


def test_post_busca_sem_token_retorna_401(client, fake_task):
    resp = client.post("/api/admin/buscas")
    assert resp.status_code == 401


def test_post_busca_com_busca_em_andamento_retorna_409_sem_criar_registro(
    client, db_session, admin, token_admin, fake_task
):
    em_andamento = _criar_busca(
        db_session, admin, status=StatusBusca.EXECUTANDO.value, iniciado_em=datetime(2026, 8, 22, 9, 15)
    )

    resp = client.post("/api/admin/buscas", headers={"Authorization": f"Bearer {token_admin}"})

    assert resp.status_code == 409
    assert "22/08/2026 09:15" in resp.json()["detail"]
    assert fake_task.chamadas == []  # nunca dispara a task

    # nenhum registro novo foi criado -- só a busca em andamento original continua lá
    assert db_session.query(BuscaLeads).count() == 1
    assert db_session.query(BuscaLeads).first().id == em_andamento.id


@pytest.mark.parametrize("status_anterior", [StatusBusca.CONCLUIDO.value, StatusBusca.ERRO.value])
def test_post_busca_apos_anterior_terminar_cria_normalmente(
    client, db_session, admin, token_admin, fake_task, status_anterior
):
    _criar_busca(db_session, admin, status=status_anterior, concluido_em=datetime(2026, 8, 22, 9, 15))

    resp = client.post("/api/admin/buscas", headers={"Authorization": f"Bearer {token_admin}"})

    assert resp.status_code == 201
    assert resp.json()["status"] == "executando"
    assert len(fake_task.chamadas) == 1
    assert db_session.query(BuscaLeads).count() == 2  # a anterior + a nova


# --- GET /api/admin/buscas ---


def test_get_buscas_lista_mais_recente_primeiro(client, db_session, admin, token_admin):
    mais_antiga = _criar_busca(db_session, admin, iniciado_em=datetime(2026, 1, 1))
    mais_recente = _criar_busca(db_session, admin, iniciado_em=datetime(2026, 6, 1))

    resp = client.get("/api/admin/buscas", headers={"Authorization": f"Bearer {token_admin}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert len(corpo) == 2
    assert corpo[0]["id"] == mais_recente.id
    assert corpo[1]["id"] == mais_antiga.id


def test_get_buscas_respeita_limit(client, db_session, admin, token_admin):
    for i in range(5):
        _criar_busca(db_session, admin, iniciado_em=datetime(2026, 1, 1) + timedelta(days=i))

    resp = client.get("/api/admin/buscas", params={"limit": 2}, headers={"Authorization": f"Bearer {token_admin}"})

    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_get_buscas_client_retorna_403(client, token_cliente):
    resp = client.get("/api/admin/buscas", headers={"Authorization": f"Bearer {token_cliente}"})
    assert resp.status_code == 403


def test_get_buscas_sem_token_retorna_401(client):
    resp = client.get("/api/admin/buscas")
    assert resp.status_code == 401


# --- GET /api/admin/buscas/{busca_id} ---


def test_get_busca_por_id_retorna_status_atual(client, db_session, admin, token_admin):
    busca = _criar_busca(
        db_session,
        admin,
        status=StatusBusca.CONCLUIDO.value,
        total_cnpjs_encontrados=10,
        total_leads_processados=9,
        erros=["cnpj-x: falhou"],
        concluido_em=datetime(2026, 6, 1, 12, 0, 0),
    )

    resp = client.get(f"/api/admin/buscas/{busca.id}", headers={"Authorization": f"Bearer {token_admin}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["status"] == "concluido"
    assert corpo["total_cnpjs_encontrados"] == 10
    assert corpo["total_leads_processados"] == 9
    assert corpo["erros"] == ["cnpj-x: falhou"]


def test_get_busca_por_id_inexistente_retorna_404(client, token_admin):
    resp = client.get("/api/admin/buscas/id-que-nao-existe", headers={"Authorization": f"Bearer {token_admin}"})
    assert resp.status_code == 404


def test_get_busca_por_id_client_retorna_403(client, db_session, admin, token_cliente):
    busca = _criar_busca(db_session, admin)
    resp = client.get(f"/api/admin/buscas/{busca.id}", headers={"Authorization": f"Bearer {token_cliente}"})
    assert resp.status_code == 403


def test_get_busca_por_id_sem_token_retorna_401(client, db_session, admin):
    busca = _criar_busca(db_session, admin)
    resp = client.get(f"/api/admin/buscas/{busca.id}")
    assert resp.status_code == 401
