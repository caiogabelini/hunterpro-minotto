"""cria tabela users

Revision ID: 6102ef9fc509
Revises: e119018824e8
Create Date: 2026-08-20T16:52:08.542418

⚠️ ESCRITA À MÃO, NÃO POR `alembic revision --autogenerate`: o Postgres
configurado em `settings.DATABASE_URL` (o real, do `.env` deste projeto)
continua inacessível neste sandbox (autenticação falha mesmo usando a
URL real) — sem conexão com ELE especificamente, `--autogenerate` não
tem contra o que comparar. Ver app/models/user.py pro modelo que esta
migration espelha.

Religada em 20/08/2026 pra vir depois de `e119018824e8_cria_tabela_leads.py`
(a baseline de `leads`, que faltava — ver o docstring dela pro histórico
completo). Sem dependência de dado entre as duas tabelas; a ordem é só
"o modelo mais antigo do projeto migra primeiro".

✅ VALIDADO CONTRA POSTGRES REAL EM 20/08/2026 (upgrade + downgrade +
schema conferido coluna por coluna) — ver o docstring de
`e119018824e8_cria_tabela_leads.py` pro relato completo (Postgres 16
temporário provisionado neste sandbox especificamente pra esse teste,
não o `.env` do projeto).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "6102ef9fc509"
down_revision: Union[str, Sequence[str], None] = "e119018824e8"
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
