"""
Ponto ÚNICO de registro dos modelos SQLAlchemy.
================================================================================

Importar `app.models` registra TODOS os modelos em `Base.metadata`. Todo
ponto de entrada de processo (`app/main.py`, `app/workers/celery_app.py`,
`alembic/env.py`, scripts em `backend/scripts/`) importa este módulo cedo,
antes de qualquer uso do banco.

⚠️ **POR QUE ISTO EXISTE — bug real, 23/08/2026.** Este arquivo estava
vazio, e cada módulo importava só os modelos que usava diretamente, quase
sempre dentro da própria função. Rodando `executar_busca_mensal` num worker
Celery de verdade:

    sqlalchemy.exc.NoReferencedTableError: Foreign key associated with
    column 'buscas_leads.iniciado_por_id' could not find table 'users'

O banco estava correto e as migrations aplicadas — o problema era de
import. `BuscaLeads.iniciado_por_id` tem FK pra `users.id`, mas SQLAlchemy
só resolve uma FK contra o que está em `Base.metadata` **naquele processo**.
`executar_busca_mensal` importava `app.models.busca_leads` e mais nada, e o
worker nunca tinha motivo pra importar `app.models.user` — então `users`
não existia no metadata e a FK não resolvia, na hora do flush.

O que tornava isso difícil de perceber: **dependia do ponto de entrada**.
`alembic/env.py` já importava os quatro modelos (por isso as migrations
sempre funcionaram, e o banco realmente estava certo). O `uvicorn` importa
`app/main.py` → rotas → `app/api/deps.py`, que importa `User` pra
autenticação — ou seja, a API carregava tudo por acidente e nunca falhava.
Só o worker Celery, que não passa por nenhum dos dois caminhos, subia com o
metadata incompleto. Testes via `TestClient` também mascaravam o problema
pelo mesmo motivo (ver `tests/test_registro_de_modelos.py`, que roda em
SUBPROCESSO justamente pra não ser mascarado).

⚠️ **AO CRIAR UM MODELO NOVO, adicione-o aqui.** Não basta criar o arquivo
em `app/models/`: sem a linha neste `__init__`, o modelo só é registrado se
alguém importar o módulo dele, e o bug acima volta na primeira FK que
apontar pra ele. `tests/test_registro_de_modelos.py::test_todo_modulo_de_modelo_esta_registrado`
falha se um arquivo novo em `app/models/` ficar de fora.
"""
from app.core.database import Base
from app.models.busca_leads import BuscaLeads, StatusBusca
from app.models.lead import KanbanStatus, Lead
from app.models.lead_message import CanalMensagem, LeadMessage
from app.models.user import User, UserRole

__all__ = [
    "Base",
    "BuscaLeads",
    "CanalMensagem",
    "KanbanStatus",
    "Lead",
    "LeadMessage",
    "StatusBusca",
    "User",
    "UserRole",
]
