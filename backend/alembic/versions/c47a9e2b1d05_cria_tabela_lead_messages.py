"""cria tabela lead_messages

Revision ID: c47a9e2b1d05
Revises: fbd6142f0632
Create Date: 2026-08-20T00:00:00.000000

⚠️ ESCRITA À MÃO, NÃO POR `alembic revision --autogenerate` — mesma
limitação de sempre neste sandbox (Postgres de `settings.DATABASE_URL`
inacessível). Ver app/models/lead_message.py pro modelo que esta
migration espelha.

`down_revision` aponta pra `fbd6142f0632` (mensagem_abordagem/valor
PGFN/ajuizado), a head atual da árvore no momento em que esta migration
foi escrita.

Tabela nova (`lead_messages`), não uma coluna em `leads` — substitui
`Lead.mensagem_abordagem` (mantida, mas deprecated, ver
app/models/lead.py) por um histórico de mensagens por canal
(email/whatsapp), uma linha por geração, nunca sobrescrita. `lead_id`
tem uma foreign key pra `leads.id` (`ondelete` não especificado — sem
regra de cascade explícita nesta sessão, não foi pedido) e um índice
(consultas sempre filtram por `lead_id`). `conteudo` é `NOT NULL` (sempre tem alguma coisa — a rota só cria o
registro quando a IA gerou algo não-vazio); `assunto` é nullable (só o
canal "email" preenche) e `gerado_em` é `NOT NULL` sem `server_default`
— mesma convenção de `created_at`/`updated_at` em `leads`
(`e119018824e8`), que também confiam só no `default=datetime.utcnow` do
lado do SQLAlchemy, não num default do banco.

✅ VALIDADO CONTRA POSTGRES REAL EM 20/08/2026 — mesmo processo das
migrations anteriores: cluster 16 temporário e descartável, porta
própria, socket Unix curto fora do diretório de scratch (ver a nota
sobre o limite de 107 bytes nas migrations anteriores), apagado ao
final. `alembic upgrade head` aplica as cinco migrations encadeadas na
ordem certa (`leads` → `users` → `kanban_status`/`motivo_perda` →
`mensagem_abordagem`/PGFN → esta), `alembic downgrade -1` reverte só
esta (as outras quatro tabelas/colunas intactas), `alembic upgrade
head` de novo reaplica limpo, e o schema final de `lead_messages` bate
exato com `app/models/lead_message.py` (tipos, nullability, FK, índice).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c47a9e2b1d05"
down_revision: Union[str, Sequence[str], None] = "fbd6142f0632"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "lead_messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("lead_id", sa.String(), nullable=False),
        sa.Column("canal", sa.String(length=10), nullable=False),
        sa.Column("conteudo", sa.Text(), nullable=False),
        sa.Column("assunto", sa.Text(), nullable=True),
        sa.Column("gerado_em", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_lead_messages_lead_id"), "lead_messages", ["lead_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_lead_messages_lead_id"), table_name="lead_messages")
    op.drop_table("lead_messages")
