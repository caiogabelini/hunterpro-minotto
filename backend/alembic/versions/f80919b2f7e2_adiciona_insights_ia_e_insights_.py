"""adiciona insights_ia e insights_gerado_em em leads

Revision ID: f80919b2f7e2
Revises: c47a9e2b1d05
Create Date: 2026-08-20T00:00:00.000000

⚠️ ESCRITA À MÃO, NÃO POR `alembic revision --autogenerate` — mesma
limitação de sempre neste sandbox (Postgres de `settings.DATABASE_URL`
inacessível). Ver app/models/lead.py (`Lead.insights_ia`,
`Lead.insights_gerado_em`) pro modelo que esta migration espelha.

`down_revision` aponta pra `c47a9e2b1d05` (lead_messages), a head atual
da árvore no momento em que esta migration foi escrita.

Duas colunas novas, ambas nullable, sem `server_default` (mesmo padrão
de `fbd6142f0632` — `NULL` já é o valor correto pra "insights ainda não
gerados", não precisa de backfill):

  - `insights_ia` (JSON) — objeto estruturado com os 5 campos da análise
    estratégica gerada sob demanda (resumo_estrategico,
    potencial_oportunidade, recomendacao_abordagem,
    estrategia_comunicacao, cta_sugerido), ver
    app/services/ai_enrichment.py::gerar_insights_estrategicos.
  - `insights_gerado_em` (DateTime) — timestamp da última geração;
    sobrescrito a cada "Gerar novamente" (sem histórico, diferente de
    `lead_messages`).

✅ VALIDADO CONTRA POSTGRES REAL EM 20/08/2026 (mesmo padrão das
migrations anteriores — cluster 16 temporário e descartável, próprio,
porta não-default, socket Unix curto fora do diretório de scratch —
ver a nota sobre o limite de 107 bytes do path do socket em
`a3f7c1d92b4e` —, apagado ao final): `alembic upgrade head` aplica as
cinco migrations encadeadas na ordem certa (`leads` → `users` →
`kanban_status`/`motivo_perda` → `mensagem_abordagem`/PGFN →
`lead_messages` → esta), `alembic downgrade -1` reverte só esta,
`alembic upgrade head` de novo reaplica limpo, e o schema final das duas
colunas novas bate exato com `app/models/lead.py` (ambas nullable, sem
default) — ver CLAUDE.md "Migrations" pro relato completo.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f80919b2f7e2"
down_revision: Union[str, Sequence[str], None] = "c47a9e2b1d05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("leads", sa.Column("insights_ia", sa.JSON(), nullable=True))
    op.add_column("leads", sa.Column("insights_gerado_em", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("leads", "insights_gerado_em")
    op.drop_column("leads", "insights_ia")
