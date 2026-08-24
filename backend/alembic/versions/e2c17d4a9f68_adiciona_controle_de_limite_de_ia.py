"""adiciona controle de limite de geracoes de IA em leads

Revision ID: e2c17d4a9f68
Revises: c9f4a8e17b03
Create Date: 2026-08-24

Controle de custo das gerações de IA no dossiê -- ver
app/api/routes/limites_ia.py.

`insights_geracoes_count`: NOT NULL com server_default "0", diferente de
quase toda coluna nova deste projeto (que é nullable sem default). Aqui
NULL não teria significado próprio: um lead que nunca gerou insights tem
ZERO gerações, não "não sei quantas". E o `server_default` é o que
preenche as linhas existentes -- sem ele o ALTER falharia num NOT NULL
com tabela populada. Mesma decisão do `kanban_status` (server_default
"novo_lead"), pelo mesmo motivo.

`ia_limite_resetado_em`: JSON nullable sem default -- aqui NULL É o
estado correto ("nenhum admin liberou este lead ainda"), que vale pra
100% das linhas existentes.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2c17d4a9f68"
down_revision: Union[str, Sequence[str], None] = "c9f4a8e17b03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("insights_geracoes_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("leads", sa.Column("ia_limite_resetado_em", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("leads", "ia_limite_resetado_em")
    op.drop_column("leads", "insights_geracoes_count")
