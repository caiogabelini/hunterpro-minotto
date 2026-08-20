"""cria tabela leads

Revision ID: e119018824e8
Revises:
Create Date: 2026-08-20T16:52:08.000000

⚠️ ESCRITA À MÃO, NÃO POR `alembic revision --autogenerate`: o Postgres
configurado em `settings.DATABASE_URL` (o real, do `.env` deste projeto)
continua inacessível neste sandbox — autenticação falha mesmo usando a
URL real. Sem conexão com ELE especificamente, `--autogenerate` não tem
contra o que comparar. Ver app/models/lead.py pro modelo que esta
migration espelha, coluna por coluna.

Esta é a migration BASELINE que faltava pra `leads` — o modelo existe
desde o primeiro commit do projeto, mas `alembic/versions/` estava
vazio até a sessão de 20/08/2026 (quando a migration de `users` foi
criada primeiro, sem essa). Corrigido agora: esta migration não tem
`down_revision` (é a raiz da árvore) e `6102ef9fc509_cria_tabela_users.py`
foi religada pra vir depois dela — `leads` é o modelo mais antigo do
projeto, é o que faz sentido rodar primeiro, mesmo não havendo
dependência de dado entre as duas tabelas.

✅ VALIDADO CONTRA POSTGRES REAL EM 20/08/2026 — mas não o do `.env` do
projeto (inacessível, ver acima): um Postgres 16 temporário e descartável
foi provisionado neste sandbox (`initdb` + `pg_ctl`, cluster próprio numa
pasta de scratch, porta 5433, socket Unix, nada exposto por TCP, apagado
ao final), especificamente pra rodar `alembic upgrade head` de verdade
sem arriscar o Postgres já em uso na porta 5432 (esse é de outro
serviço do sistema, sem relação com este projeto). Confirmado: as duas
migrations aplicam sem erro (`leads` → `users`, na ordem certa),
`alembic downgrade base` reverte as duas sem erro, e `alembic upgrade
head` de novo reaplica limpo — schema final conferido coluna por coluna
via `\d leads`/`\d users` contra os modelos em app/models/, bate exato
(tipos, nullability, os dois índices únicos). O que ainda não foi
validado é especificamente contra a `DATABASE_URL` real do projeto —
só contra Postgres 16 em geral.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e119018824e8"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "leads",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("cnpj", sa.String(length=18), nullable=False),
        sa.Column("razao_social", sa.String(length=255), nullable=False),
        sa.Column("nome_fantasia", sa.String(length=255), nullable=True),
        sa.Column("cnae_principal", sa.String(length=10), nullable=True),
        sa.Column("municipio", sa.String(length=120), nullable=True),
        sa.Column("uf", sa.String(length=2), nullable=True),
        sa.Column("anos_mercado", sa.Integer(), nullable=True),
        sa.Column("simples_nacional", sa.Boolean(), nullable=True),
        sa.Column("situacao_cadastral", sa.String(length=50), nullable=True),
        sa.Column("telefone", sa.String(length=20), nullable=True),
        sa.Column("whatsapp_ativo", sa.Boolean(), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("email_validado", sa.Boolean(), nullable=True),
        sa.Column("decisor_nome", sa.String(length=255), nullable=True),
        sa.Column("site", sa.String(length=255), nullable=True),
        sa.Column("instagram", sa.String(length=120), nullable=True),
        sa.Column("linkedin_empresa", sa.String(length=255), nullable=True),
        sa.Column("linkedin_decisor", sa.String(length=255), nullable=True),
        sa.Column("google_rating", sa.Float(), nullable=True),
        sa.Column("google_avaliacoes", sa.Integer(), nullable=True),
        sa.Column("servicos", sa.Text(), nullable=True),
        sa.Column("rqe_confirmado", sa.Boolean(), nullable=True),
        sa.Column("rqe_fonte", sa.String(length=50), nullable=True),
        sa.Column("cnes_codigo", sa.String(length=20), nullable=True),
        sa.Column("divida_ativa_qtd_inscricoes", sa.Integer(), nullable=True),
        sa.Column("zona_franca", sa.Boolean(), nullable=True),
        sa.Column("score_total", sa.Float(), nullable=True),
        sa.Column("score_detalhes", sa.JSON(), nullable=True),
        sa.Column("prioridade", sa.String(length=1), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_leads_cnpj"), "leads", ["cnpj"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_leads_cnpj"), table_name="leads")
    op.drop_table("leads")
