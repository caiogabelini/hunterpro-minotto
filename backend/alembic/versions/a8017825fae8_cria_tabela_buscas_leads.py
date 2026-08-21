"""cria tabela buscas_leads

Revision ID: a8017825fae8
Revises: 99454b995104
Create Date: 2026-08-22T00:00:00.000000

⚠️ ESCRITA À MÃO, NÃO POR `alembic revision --autogenerate` — mesma
limitação de sempre neste sandbox (Postgres de `settings.DATABASE_URL`
inacessível). Ver app/models/busca_leads.py pro modelo que esta
migration espelha.

`down_revision` aponta pra `99454b995104` (dashboard_premissas em
users), a head atual da árvore no momento em que esta migration foi
escrita.

Tabela nova (`buscas_leads`), não uma coluna em `users`/`leads` —
mesma justificativa de `lead_messages` (`c47a9e2b1d05`): é um histórico
de execuções (uma linha por disparo de busca mensal), não um dado
singular por usuário. `iniciado_por_id` tem uma foreign key pra
`users.id` (sem cascade explícito, mesma convenção de
`lead_messages.lead_id`) e um índice (consultas de "quem disparou"
filtrariam por ele, mesmo raciocínio de índice em toda FK deste
projeto). `status` é `String(20)` guardando `StatusBusca.value`, mesmo
padrão de string-enum já usado em `Lead.kanban_status`/`User.role`.
`concluido_em`/`total_cnpjs_encontrados`/`total_leads_processados`/`erros`
são todos nullable, sem `server_default` -- `NULL` já é o valor
correto pra "a busca ainda não terminou"/"essa contagem ainda não foi
calculada", sem precisar de backfill (mesma convenção de
`mensagem_abordagem`/`insights_ia` em `leads`).

✅ VALIDADO CONTRA POSTGRES REAL EM 22/08/2026 — mesmo processo das
migrations anteriores: cluster 16 temporário e descartável, porta
própria, socket Unix curto fora do diretório de scratch (ver a nota
sobre o limite de 107 bytes nas migrations anteriores), apagado ao
final. `alembic upgrade head` aplica as oito migrations encadeadas na
ordem certa (`leads` → `users` → `kanban_status`/`motivo_perda` →
`mensagem_abordagem`/PGFN → `lead_messages` → `insights_ia`/
`insights_gerado_em` → `dashboard_premissas` → esta), `alembic
downgrade -1` reverte só esta (as sete tabelas/colunas anteriores
intactas), `alembic upgrade head` de novo reaplica limpo, e o schema
final de `buscas_leads` bate exato com `app/models/busca_leads.py`
(tipos, nullability, FK, índice).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a8017825fae8"
down_revision: Union[str, Sequence[str], None] = "99454b995104"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "buscas_leads",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("iniciado_por_id", sa.String(), nullable=False),
        sa.Column("iniciado_em", sa.DateTime(), nullable=False),
        sa.Column("concluido_em", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("total_cnpjs_encontrados", sa.Integer(), nullable=True),
        sa.Column("total_leads_processados", sa.Integer(), nullable=True),
        sa.Column("erros", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["iniciado_por_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_buscas_leads_iniciado_por_id"), "buscas_leads", ["iniciado_por_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_buscas_leads_iniciado_por_id"), table_name="buscas_leads")
    op.drop_table("buscas_leads")
