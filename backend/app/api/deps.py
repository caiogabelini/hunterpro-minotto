"""
Dependencies de autenticação/autorização do FastAPI.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.user import User, UserRole

# auto_error=False -- sem isso, o próprio HTTPBearer devolve 403 quando
# o header Authorization vem ausente, o que mistura "não autenticado"
# (401) com "autenticado mas sem permissão" (403, ver require_admin
# abaixo). Tratamos a ausência de token manualmente pra manter os dois
# casos de "sem token"/"token inválido" consistentes em 401.
_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Lê o token Bearer do header Authorization, decodifica, busca o
    usuário no banco e injeta na rota. 401 se o token estiver ausente,
    inválido ou expirado, se o usuário não existir mais, ou se estiver
    inativo.
    """
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Não autenticado")

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido ou expirado")

    usuario = db.execute(select(User).where(User.id == payload.get("user_id"))).scalar_one_or_none()
    if usuario is None or not usuario.ativo:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuário não encontrado ou inativo")

    return usuario


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Só deixa passar se `current_user.role == "admin"` — usar em rotas
    administrativas futuras (nenhuma existe ainda)."""
    if current_user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso restrito a administradores")
    return current_user
