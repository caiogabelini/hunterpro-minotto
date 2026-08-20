"""
Testes de app/core/security.py — hash de senha (bcrypt/passlib) e JWT
(python-jose).
"""
from datetime import datetime, timedelta

from jose import jwt

from app.core.config import settings
from app.core.security import (
    JWT_ALGORITHM,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

SENHA = "minhasenha123"


# --- hash_password / verify_password ---


def test_hash_password_nunca_igual_ao_texto_original():
    hash_gerado = hash_password(SENHA)
    assert hash_gerado != SENHA


def test_hash_password_usa_salt_aleatorio():
    """bcrypt gera um salt aleatório a cada chamada -- dois hashes da
    mesma senha não podem bater, mesmo sendo a mesma senha."""
    assert hash_password(SENHA) != hash_password(SENHA)


def test_verify_password_senha_correta():
    hash_gerado = hash_password(SENHA)
    assert verify_password(SENHA, hash_gerado) is True


def test_verify_password_senha_errada():
    hash_gerado = hash_password(SENHA)
    assert verify_password("outrasenha", hash_gerado) is False


# --- create_access_token / decode_access_token ---


def test_create_e_decode_token_roundtrip():
    token = create_access_token({"user_id": "abc123", "role": "admin"})
    payload = decode_access_token(token)

    assert payload is not None
    assert payload["user_id"] == "abc123"
    assert payload["role"] == "admin"
    assert "exp" in payload


def test_decode_token_invalido_retorna_none():
    assert decode_access_token("isso-nao-e-um-jwt") is None


def test_decode_token_expirado_retorna_none():
    token_expirado = jwt.encode(
        {"user_id": "abc123", "exp": datetime.utcnow() - timedelta(minutes=1)},
        settings.SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )
    assert decode_access_token(token_expirado) is None


def test_decode_token_assinado_com_outra_chave_retorna_none():
    """Token adulterado ou gerado por outro sistema -- assinatura não
    bate com a SECRET_KEY configurada, não pode ser aceito."""
    token_de_outra_chave = jwt.encode({"user_id": "abc123"}, "chave-diferente", algorithm=JWT_ALGORITHM)
    assert decode_access_token(token_de_outra_chave) is None
