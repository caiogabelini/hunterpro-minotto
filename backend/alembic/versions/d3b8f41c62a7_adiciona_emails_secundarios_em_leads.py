"""adiciona emails_secundarios em leads

Revision ID: d3b8f41c62a7
Revises: b5e2c30a71f4
Create Date: 2026-08-23

Guarda os demais e-mails que o `domain-search` do Hunter traz na mesma
resposta que produziu `leads.email` -- ver o comentário da coluna em
app/models/lead.py.

Nullable, sem server_default: NULL já é o valor correto pra "o
domain-search não rodou pra este lead" (que é o caso de 100% das linhas
existentes, já que o fallback nasce desligado). Nenhum backfill
necessário -- mesma decisão de `insights_ia`/`motivo_perda`, e diferente
de `kanban_status`, que precisou de server_default pra preencher linhas
antigas numa coluna NOT NULL.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3b8f41c62a7"
down_revision: Union[str, Sequence[str], None] = "b5e2c30a71f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("emails_secundarios", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "emails_secundarios")
