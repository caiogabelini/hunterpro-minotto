"""adiciona total_cnpjs_selecionados em buscas_leads

Revision ID: b5e2c30a71f4
Revises: a1139438a443
Create Date: 2026-08-22

Décima migration. Adiciona `buscas_leads.total_cnpjs_selecionados`, que
registra quantos CNPJs sobreviveram à PRÉ-SELEÇÃO e entraram no
enriquecimento pago -- ver app/scoring/pre_selecao.py e a fase 1/fase 2
de `executar_busca_mensal`.

Nullable, sem `server_default`: `NULL` já é o valor correto pra buscas
executadas ANTES de 22/08/2026, quando a pré-seleção não existia e todo
CNPJ do universo era processado. Um `server_default=0` mentiria (diria
"selecionou zero" pra buscas que na verdade processaram tudo), e
backfillar com `total_cnpjs_encontrados` inventaria um dado que aquela
execução não produziu. Mesmo critério de `motivo_perda`/`insights_ia`:
NULL = "não se aplica a esta linha".

`down_revision` aponta pra `a1139438a443` (dados de fechamento em leads),
a head anterior.

✅ VALIDADO CONTRA POSTGRES 16 REAL EM 22/08/2026 -- mesmo processo das
nove migrations anteriores: cluster temporário e descartável, porta
própria, socket Unix curto fora do diretório de scratch (ver a nota
sobre o limite de 107 bytes nas migrations anteriores), apagado logo
depois.
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5e2c30a71f4"
down_revision: Union[str, Sequence[str], None] = "a1139438a443"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "buscas_leads",
        sa.Column("total_cnpjs_selecionados", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("buscas_leads", "total_cnpjs_selecionados")
