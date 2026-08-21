"""adiciona dashboard_premissas em users

Revision ID: 99454b995104
Revises: f80919b2f7e2
Create Date: 2026-08-21T00:00:00.000000

⚠️ ESCRITA À MÃO, NÃO POR `alembic revision --autogenerate` — mesma
limitação de sempre neste sandbox (Postgres de `settings.DATABASE_URL`
inacessível). Ver app/models/user.py (`User.dashboard_premissas`) pro
modelo que esta migration espelha.

`down_revision` aponta pra `f80919b2f7e2` (insights_ia/insights_gerado_em
em leads), a head atual da árvore no momento em que esta migration foi
escrita.

Uma coluna nova, nullable, sem `server_default` (mesmo padrão de
`insights_ia`/`mensagem_abordagem` -- `NULL` já é o valor correto pra
"usuário nunca salvou premissas do simulador", sem backfill):

  - `dashboard_premissas` (JSON) -- objeto
    {leads_qualificados, taxa_fechamento, ticket_medio} do Simulador de
    Receita (dashboard, Fase 1), ver
    app/api/routes/dashboard.py::put_premissas.

✅ VALIDADO CONTRA POSTGRES REAL EM 21/08/2026 (mesmo padrão das
migrations anteriores -- cluster 16 temporário e descartável, próprio,
porta não-default, socket Unix curto fora do diretório de scratch --
ver a nota sobre o limite de 107 bytes do path do socket em
`a3f7c1d92b4e`, apagado ao final): `alembic upgrade head` aplica as sete
migrations encadeadas na ordem certa (`leads` → `users` →
`kanban_status`/`motivo_perda` → `mensagem_abordagem`/PGFN →
`lead_messages` → `insights_ia`/`insights_gerado_em` → esta), `alembic
downgrade -1` reverte só esta, `alembic upgrade head` de novo reaplica
limpo, e a coluna `users.dashboard_premissas` (`json`, nullable, sem
default) bate exato com `app/models/user.py`. Cluster parado e apagado
imediatamente depois.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "99454b995104"
down_revision: Union[str, Sequence[str], None] = "f80919b2f7e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("dashboard_premissas", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "dashboard_premissas")
