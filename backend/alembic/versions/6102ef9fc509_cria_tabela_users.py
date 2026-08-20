"""cria tabela users

Revision ID: 6102ef9fc509
Revises:
Create Date: 2026-08-20T16:52:08.542418

⚠️ ESCRITA À MÃO, NÃO POR `alembic revision --autogenerate`: não há
Postgres acessível com as credenciais do `.env` neste sandbox (a
autenticação falha mesmo usando a `DATABASE_URL` real configurada) —
sem conexão de verdade, `--autogenerate` não tem contra o que comparar
o metadata. Ver app/models/user.py pro modelo que esta migration espelha.

⚠️ ACHADO NESTA SESSÃO: `alembic/versions/` estava vazio antes desta
migration — não existe nenhuma migration baseline pra tabela `leads`
(criada por `app/models/lead.py`). Isso significa que rodar
`alembic upgrade head` num banco totalmente vazio cria só `users`, NÃO
`leads` — a tabela `leads` precisa continuar sendo criada por outro
meio (ex.: `Base.metadata.create_all()` manual, ou como já estava sendo
feito antes desta sessão) até alguém gerar a migration baseline dela
também. Não é escopo desta sessão consertar isso, só documentar pra não
ser uma surpresa.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "6102ef9fc509"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("senha_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
