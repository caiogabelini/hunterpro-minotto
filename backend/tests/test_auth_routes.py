"""
Testes de integração de autenticação — POST /api/auth/login e a proteção
de GET /api/leads via get_current_user.

Usa um banco SQLite em memória (mesmo padrão de test_process_lead_pipeline.py)
injetado via `app.dependency_overrides[get_db]` — não precisa de Postgres
real rodando.
"""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import JWT_ALGORITHM, create_access_token, hash_password
from app.main import app
from app.models.lead import Lead  # noqa: F401 -- garante que a tabela leads também é criada no metadata
from app.models.user import User, UserRole

SENHA = "senha-forte-123"


@pytest.fixture()
def db_session():
    # StaticPool + check_same_thread=False -- TestClient roda o endpoint
    # numa worker thread diferente da que monta a fixture (via
    # anyio.to_thread.run_sync). Sem isso, cada thread pegaria uma
    # conexão sqlite3 diferente e, como é ":memory:", um banco vazio
    # diferente -- "no such table: users" mesmo com create_all já rodado.
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
def usuario_admin(db_session):
    usuario = User(email="admin@minotto.com.br", senha_hash=hash_password(SENHA), role=UserRole.ADMIN.value, ativo=True)
    db_session.add(usuario)
    db_session.commit()
    db_session.refresh(usuario)
    return usuario


# --- POST /api/auth/login ---


def test_login_credenciais_corretas_gera_token_valido(client, usuario_admin):
    resp = client.post("/api/auth/login", json={"email": usuario_admin.email, "senha": SENHA})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["token_type"] == "bearer"

    payload = jwt.decode(corpo["access_token"], settings.SECRET_KEY, algorithms=[JWT_ALGORITHM])
    assert payload["user_id"] == usuario_admin.id
    assert payload["role"] == "admin"


def test_login_senha_errada_retorna_401(client, usuario_admin):
    resp = client.post("/api/auth/login", json={"email": usuario_admin.email, "senha": "senha-errada"})
    assert resp.status_code == 401


def test_login_email_inexistente_retorna_401(client):
    resp = client.post("/api/auth/login", json={"email": "ninguem@minotto.com.br", "senha": "qualquer"})
    assert resp.status_code == 401


def test_login_usuario_inativo_retorna_401(client, db_session):
    inativo = User(email="inativo@minotto.com.br", senha_hash=hash_password(SENHA), role=UserRole.CLIENT.value, ativo=False)
    db_session.add(inativo)
    db_session.commit()

    resp = client.post("/api/auth/login", json={"email": inativo.email, "senha": SENHA})
    assert resp.status_code == 401


# --- GET /api/leads (protegida) ---


def test_rota_protegida_sem_token_retorna_401(client):
    resp = client.get("/api/leads")
    assert resp.status_code == 401


def test_rota_protegida_com_token_invalido_retorna_401(client):
    resp = client.get("/api/leads", headers={"Authorization": "Bearer token-qualquer-invalido"})
    assert resp.status_code == 401


def test_rota_protegida_com_token_expirado_retorna_401(client, usuario_admin):
    token_expirado = jwt.encode(
        {"user_id": usuario_admin.id, "role": usuario_admin.role, "exp": datetime.now(UTC) - timedelta(minutes=1)},
        settings.SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )
    resp = client.get("/api/leads", headers={"Authorization": f"Bearer {token_expirado}"})
    assert resp.status_code == 401


def test_rota_protegida_com_token_valido_retorna_200(client, usuario_admin):
    token = create_access_token({"user_id": usuario_admin.id, "role": usuario_admin.role})
    resp = client.get("/api/leads", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_rota_protegida_aceita_role_client_tambem(client, db_session):
    """A rota exige só autenticação, não um papel específico -- qualquer
    usuário ativo (admin ou client) consegue listar leads."""
    cliente = User(email="cliente@minotto.com.br", senha_hash=hash_password(SENHA), role=UserRole.CLIENT.value, ativo=True)
    db_session.add(cliente)
    db_session.commit()
    db_session.refresh(cliente)

    token = create_access_token({"user_id": cliente.id, "role": cliente.role})
    resp = client.get("/api/leads", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_rota_protegida_token_de_usuario_inexistente_retorna_401(client):
    """Token com assinatura válida mas apontando pra um user_id que não
    existe (usuário deletado depois de emitir o token, por exemplo)."""
    token = create_access_token({"user_id": "id-que-nao-existe", "role": "admin"})
    resp = client.get("/api/leads", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
