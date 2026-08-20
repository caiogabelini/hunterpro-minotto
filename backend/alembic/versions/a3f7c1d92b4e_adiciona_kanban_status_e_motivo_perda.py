"""adiciona kanban_status e motivo_perda em leads

Revision ID: a3f7c1d92b4e
Revises: 6102ef9fc509
Create Date: 2026-08-20T00:00:00.000000

⚠️ ESCRITA À MÃO, NÃO POR `alembic revision --autogenerate` — mesma
limitação de sempre neste sandbox (Postgres de `settings.DATABASE_URL`
inacessível). Ver app/models/lead.py (`Lead.kanban_status`,
`Lead.motivo_perda`, enum `KanbanStatus`) pro modelo que esta migration
espelha.

`down_revision` aponta pra `6102ef9fc509` (users), a head atual da árvore
no momento em que esta migration foi escrita.

`kanban_status` é `String(20)` com `server_default="novo_lead"` (não
`nullable=True`) pra que linhas já existentes recebam o valor default do
funil ao aplicar o upgrade, em vez de ficar NULL — mesma convenção
pragmática de string simples (não enum nativo do Postgres) já usada em
`Lead.prioridade`/`Lead.rqe_fonte`/`User.role`. `motivo_perda` é opcional
(nullable, sem default) — só é preenchido quando `kanban_status ==
"perdido"`, e essa regra é validada na rota (`PATCH /api/leads/{id}/status`),
não a nível de banco.

✅ VALIDADO CONTRA POSTGRES REAL EM 20/08/2026 (mesmo padrão das duas
migrations anteriores — cluster 16 temporário e descartável, próprio,
porta 5433, socket Unix, apagado ao final): `alembic upgrade head`
aplica as três migrations encadeadas na ordem certa (`leads` → `users` →
esta), `alembic downgrade -1` reverte só esta (volta pra `users`, sem
tocar nas outras duas tabelas), `alembic upgrade head` de novo reaplica
limpo, e o schema final de `kanban_status`/`motivo_perda` bate exato com
`app/models/lead.py` (`varchar(20) NOT NULL DEFAULT 'novo_lead'` /
`text` nullable) — ver CLAUDE.md "Migrations" pro relato completo,
inclusive uma pegadinha nova: o socket Unix não pode morar dentro do
diretório de scratch profundo (nome de sessão longo), estoura o limite
de 107 bytes do path do socket — precisou de um diretório separado e
curto só pro socket.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a3f7c1d92b4e"
down_revision: Union[str, Sequence[str], None] = "6102ef9fc509"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "leads",
        sa.Column("kanban_status", sa.String(length=20), nullable=False, server_default="novo_lead"),
    )
    op.add_column("leads", sa.Column("motivo_perda", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("leads", "motivo_perda")
    op.drop_column("leads", "kanban_status")
