"""
Hash de senha (passlib/bcrypt) e JWT (python-jose) — autenticação enxuta
do HunterPro Minotto Contabilidade.

⚠️ SOBRE O PIN DE `bcrypt==3.2.2` EM requirements.txt: passlib==1.7.4
(não mantido desde 2020) checa a versão do backend lendo
`bcrypt.__about__.__version__`, atributo removido no bcrypt 4.x — sem o
pin, `hash_password()` quebra com um `ValueError` enganoso ("password
cannot be longer than 72 bytes"), mesmo pra senha curta. Confirmado
batendo nesse bug de verdade em 20/08/2026 (bcrypt 4.0.1 é o que instala
por padrão via `passlib[bcrypt]`, sem uma versão travada). Não trocar a
versão do bcrypt sem revalidar essa combinação.
"""
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

JWT_ALGORITHM = "HS256"

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Gera o hash bcrypt de uma senha em texto puro. Nunca armazenar a
    senha original — só o resultado desta função."""
    return _pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Confere uma senha em texto puro contra um hash já gerado por
    `hash_password`."""
    return _pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict) -> str:
    """
    Gera um JWT assinado (HS256, `SECRET_KEY`) a partir de `data` (ex.:
    `{"user_id": ..., "role": ...}`), com expiração em
    `settings.ACCESS_TOKEN_EXPIRE_MINUTES` minutos a partir de agora.
    """
    to_encode = data.copy()
    to_encode["exp"] = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """
    Decodifica e valida um JWT (assinatura + expiração). Devolve o
    payload se válido, ou None se o token for inválido, expirado ou
    adulterado — nunca lança exceção, quem chama só precisa checar None.
    """
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None
