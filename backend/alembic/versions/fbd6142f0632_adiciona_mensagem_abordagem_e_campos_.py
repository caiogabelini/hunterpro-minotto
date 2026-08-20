"""adiciona mensagem_abordagem e campos de valor/ajuizamento da PGFN em leads

Revision ID: fbd6142f0632
Revises: a3f7c1d92b4e
Create Date: 2026-08-20T00:00:00.000000

⚠️ ESCRITA À MÃO, NÃO POR `alembic revision --autogenerate` — mesma
limitação de sempre neste sandbox (Postgres de `settings.DATABASE_URL`
inacessível). Ver app/models/lead.py (`Lead.mensagem_abordagem`,
`Lead.divida_ativa_valor_total`, `Lead.divida_ativa_ajuizado`) pro
modelo que esta migration espelha.

`down_revision` aponta pra `a3f7c1d92b4e` (kanban_status/motivo_perda),
a head atual da árvore no momento em que esta migration foi escrita.

Três colunas novas, todas nullable (sem `server_default` — diferente de
`kanban_status` na migration anterior, que precisava de um valor pra
backfill; aqui `NULL` já é o valor correto pra "ainda não sabemos"):

  - `mensagem_abordagem` (Text) — só o espaço reservado nesta sessão; a
    geração via IA fica pra uma sessão futura, não implementada aqui.
  - `divida_ativa_valor_total` (Float) e `divida_ativa_ajuizado`
    (Boolean) — `app/services/pgfn.py::PgfnDebtSummary` já calcula os
    dois (`valor_total`, `algum_ajuizado`) desde a sessão que implementou
    o parser da PGFN, mas o `Lead` nunca persistiu esses dois campos, só
    `divida_ativa_qtd_inscricoes`. Fechando esse gap agora.

✅ VALIDADO CONTRA POSTGRES REAL EM 20/08/2026 (mesmo padrão das
migrations anteriores — cluster 16 temporário e descartável, próprio,
porta 5433, socket Unix curto fora do diretório de scratch — ver a nota
sobre o limite de 107 bytes do path do socket na migration anterior,
`a3f7c1d92b4e` —, apagado ao final): `alembic upgrade head` aplica as
quatro migrations encadeadas na ordem certa (`leads` → `users` →
`kanban_status`/`motivo_perda` → esta), `alembic downgrade -1` reverte
só esta, `alembic upgrade head` de novo reaplica limpo, e o schema final
das três colunas novas bate exato com `app/models/lead.py` (todas
nullable, sem default) — ver CLAUDE.md "Migrations" pro relato completo.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "fbd6142f0632"
down_revision: Union[str, Sequence[str], None] = "a3f7c1d92b4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("leads", sa.Column("divida_ativa_valor_total", sa.Float(), nullable=True))
    op.add_column("leads", sa.Column("divida_ativa_ajuizado", sa.Boolean(), nullable=True))
    op.add_column("leads", sa.Column("mensagem_abordagem", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("leads", "mensagem_abordagem")
    op.drop_column("leads", "divida_ativa_ajuizado")
    op.drop_column("leads", "divida_ativa_valor_total")
