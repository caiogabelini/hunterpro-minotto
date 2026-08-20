"""
Autenticação — login enxuto (e-mail + senha -> JWT).

Sem registro público: usuários são criados por admin via
scripts/create_user.py (decisão consciente, ver README "O que falta" —
só a Minotto e a 4Hands usam o sistema por enquanto).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token, verify_password
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
def login(dados: LoginRequest, db: Session = Depends(get_db)):
    """
    Valida e-mail + senha contra o banco e devolve um access_token (JWT)
    com `user_id` e `role` no payload.

    401 com a mesma mensagem genérica tanto pra e-mail inexistente
    quanto pra senha errada (e pra usuário inativo) — não vazar quais
    e-mails têm cadastro no sistema.
    """
    usuario = db.execute(select(User).where(User.email == dados.email)).scalar_one_or_none()

    if usuario is None or not usuario.ativo or not verify_password(dados.senha, usuario.senha_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="E-mail ou senha inválidos")

    token = create_access_token({"user_id": usuario.id, "role": usuario.role})
    return TokenResponse(access_token=token)
