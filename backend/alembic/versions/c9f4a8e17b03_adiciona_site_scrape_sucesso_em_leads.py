"""adiciona site_scrape_sucesso em leads

Revision ID: c9f4a8e17b03
Revises: d3b8f41c62a7
Create Date: 2026-08-23

Distingue "site lido, não publica o dado" de "site nunca lido" -- ver o
comentário da coluna em app/models/lead.py.

Nullable, sem server_default. Aqui o NULL não é só "ainda não sei": é um
dos TRÊS estados com significado próprio ("a etapa nem rodou"), então um
server_default de `false` seria ativamente errado -- marcaria como
"falhou ao ler" todo lead que nunca teve site pra ler, inclusive as
linhas antigas anteriores a esta migration.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9f4a8e17b03"
down_revision: Union[str, Sequence[str], None] = "d3b8f41c62a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("site_scrape_sucesso", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "site_scrape_sucesso")
