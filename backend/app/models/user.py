"""
Modelo de usuário — autenticação enxuta do HunterPro Minotto Contabilidade.

Cadastro é feito por admin via `scripts/create_user.py`, não por endpoint
público de self-service — decisão consciente, já que só a Minotto e a
4Hands usam o sistema por enquanto. Ver README "O que falta" pras
decisões de escopo deixadas de fora de propósito nesta sessão (refresh
token, recuperação de senha, registro público).
"""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserRole(str, Enum):
    ADMIN = "admin"
    CLIENT = "client"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    senha_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(10), default=UserRole.CLIENT.value)  # "admin" | "client"
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
