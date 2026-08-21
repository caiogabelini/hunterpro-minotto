"""adiciona dados de fechamento (servicos_vendidos, tipo_contrato, valor_fechamento) em leads

Revision ID: a1139438a443
Revises: a8017825fae8
Create Date: 2026-08-23T00:00:00.000000

⚠️ ESCRITA À MÃO, NÃO POR `alembic revision --autogenerate` — mesma
limitação de sempre neste sandbox (Postgres de `settings.DATABASE_URL`
inacessível). Ver app/models/lead.py (`Lead.servicos_vendidos`,
`Lead.tipo_contrato`, `Lead.valor_fechamento`) pro modelo que esta
migration espelha.

`down_revision` aponta pra `a8017825fae8` (tabela buscas_leads), a head
atual da árvore no momento em que esta migration foi escrita.

Três colunas novas em `leads`, todas nullable, sem `server_default` --
mesma convenção de `mensagem_abordagem`/`insights_ia`/`divida_ativa_*`:
`NULL` já é o valor correto pra "lead ainda não fechou como ganho",
sem precisar de backfill. Espelham `motivo_perda` do lado positivo
("ganho" em vez de "perdido"), validadas em
`PATCH /{lead_id}/status` (app/api/routes/leads.py), não a nível de
banco:

  - `servicos_vendidos` (JSON) -- lista de strings, sem enum fechado no
    banco (os 4 valores fixos que o frontend oferece + texto livre de
    "Outro" cabem igual aqui; validação de opções acontece no frontend).
  - `tipo_contrato` (String(20)) -- "pontual" | "recorrente", validado
    contra esse conjunto fechado na rota.
  - `valor_fechamento` (Float) -- R$, único se pontual, mensal se
    recorrente.

✅ VALIDADO CONTRA POSTGRES REAL EM 23/08/2026 (mesmo processo das
migrations anteriores — cluster 16 temporário e descartável, porta
própria, socket Unix curto fora do diretório de scratch, ver a nota
sobre o limite de 107 bytes nas migrations anteriores, apagado ao
final): `alembic upgrade head` aplica as dez migrations encadeadas na
ordem certa, `alembic downgrade -1` reverte só esta, `alembic upgrade
head` de novo reaplica limpo, e as três colunas novas de `leads` batem
exato com `app/models/lead.py` (tipos, nullability, sem default).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a1139438a443"
down_revision: Union[str, Sequence[str], None] = "a8017825fae8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("leads", sa.Column("servicos_vendidos", sa.JSON(), nullable=True))
    op.add_column("leads", sa.Column("tipo_contrato", sa.String(length=20), nullable=True))
    op.add_column("leads", sa.Column("valor_fechamento", sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("leads", "valor_fechamento")
    op.drop_column("leads", "tipo_contrato")
    op.drop_column("leads", "servicos_vendidos")
