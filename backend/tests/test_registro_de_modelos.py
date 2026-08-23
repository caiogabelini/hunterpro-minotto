"""
Registro dos modelos SQLAlchemy por PONTO DE ENTRADA do processo.
================================================================================

Guarda do bug real de 23/08/2026 (ver `app/models/__init__.py` pro
histórico completo): rodar `executar_busca_mensal` num worker Celery de
verdade quebrava com

    NoReferencedTableError: Foreign key associated with column
    'buscas_leads.iniciado_por_id' could not find table 'users'

porque o worker nunca importava `app.models.user`, e SQLAlchemy só resolve
uma FK contra o que está em `Base.metadata` **naquele processo**.

⚠️ **POR QUE TUDO AQUI RODA EM SUBPROCESSO.** Este é o ponto do arquivo, não
um detalhe de implementação. O bug depende de *quais módulos foram
importados no processo*, e dentro do pytest isso não dá pra observar: o
`conftest` e os outros ~30 arquivos de teste já importaram `app.main`,
`app.api.deps`, `User`, tudo. Qualquer asserção in-process passaria mesmo
com o `app/models/__init__.py` vazio -- exatamente como o `TestClient`
mascarava o problema na API. Um interpretador novo, importando só o que o
ponto de entrada real importa, é a única forma de reproduzir a condição.
"""
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ_BACKEND = Path(__file__).resolve().parent.parent
TABELAS_ESPERADAS = ["buscas_leads", "lead_messages", "leads", "users"]

# Como o worker sobe em produção e no dev local:
#     celery -A app.workers.celery_app worker
# O que esse comando importa é, essencialmente, este módulo.
ENTRYPOINT_WORKER = "app.workers.celery_app"
# Como a API sobe: uvicorn app.main:app
ENTRYPOINT_API = "app.main"


def _rodar_em_processo_novo(codigo: str) -> subprocess.CompletedProcess:
    """Executa `codigo` num interpretador limpo, com o mesmo cwd/venv que o
    processo real do worker usaria."""
    return subprocess.run(
        [sys.executable, "-c", codigo],
        cwd=RAIZ_BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("entrypoint", [ENTRYPOINT_WORKER, ENTRYPOINT_API])
def test_entrypoint_registra_todos_os_modelos(entrypoint: str):
    """Importar o ponto de entrada, e mais nada, tem que registrar os 4
    modelos -- não só os que aquele processo usa diretamente."""
    resultado = _rodar_em_processo_novo(
        f"import {entrypoint}\n"
        "from app.core.database import Base\n"
        "print(','.join(sorted(Base.metadata.tables)))\n"
    )
    assert resultado.returncode == 0, resultado.stderr
    registradas = resultado.stdout.strip().split(",")
    assert registradas == TABELAS_ESPERADAS


@pytest.mark.parametrize("entrypoint", [ENTRYPOINT_WORKER, ENTRYPOINT_API])
def test_entrypoint_resolve_as_foreign_keys(entrypoint: str):
    """`create_all` resolve toda FK contra o metadata -- é literalmente a
    operação que levantava NoReferencedTableError."""
    resultado = _rodar_em_processo_novo(
        f"import {entrypoint}\n"
        "from sqlalchemy import create_engine\n"
        "from app.core.database import Base\n"
        "Base.metadata.create_all(create_engine('sqlite:///:memory:'))\n"
        "print('OK')\n"
    )
    assert resultado.returncode == 0, resultado.stderr
    assert resultado.stdout.strip() == "OK"


def test_worker_persiste_busca_leads_com_fk_para_users():
    """O caminho exato que falhava: processo do worker cria e commita uma
    `BuscaLeads` apontando pra um `User`, com FK sendo de fato verificada
    pelo banco (`PRAGMA foreign_keys=ON` -- SQLite não checa FK por padrão,
    e sem isso o teste passaria mesmo com a FK quebrada)."""
    resultado = _rodar_em_processo_novo(
        # Só o que o worker importa -- nada de app.main, nada de deps.
        f"import {ENTRYPOINT_WORKER}\n"
        "from sqlalchemy import create_engine, event\n"
        "from sqlalchemy.orm import sessionmaker\n"
        "from app.core.database import Base\n"
        "from app.models.busca_leads import BuscaLeads, StatusBusca\n"
        "from app.models.user import User, UserRole\n"
        "engine = create_engine('sqlite:///:memory:')\n"
        "@event.listens_for(engine, 'connect')\n"
        "def _fk_on(dbapi_conn, _rec):\n"
        "    dbapi_conn.execute('PRAGMA foreign_keys=ON')\n"
        "Base.metadata.create_all(engine)\n"
        "db = sessionmaker(bind=engine)()\n"
        "admin = User(email='a@b.c', senha_hash='x', role=UserRole.ADMIN.value)\n"
        "db.add(admin); db.commit()\n"
        "busca = BuscaLeads(iniciado_por_id=admin.id, status=StatusBusca.EXECUTANDO.value)\n"
        "db.add(busca); db.commit()\n"
        "assert db.get(BuscaLeads, busca.id).iniciado_por_id == admin.id\n"
        "print('OK')\n"
    )
    assert resultado.returncode == 0, resultado.stderr
    assert resultado.stdout.strip() == "OK"


def test_importar_qualquer_submodulo_de_modelo_registra_todos():
    """Defesa em profundidade que caiu de brinde com a correção, e vale
    registrar porque não é óbvia: importar `app.models.busca_leads` carrega
    o pacote pai `app.models` ANTES do submódulo (semântica de import do
    Python), e o `__init__` do pacote agora importa os quatro modelos.

    Ou seja, o código antigo -- `from app.models.busca_leads import
    BuscaLeads` dentro da função, sem nenhum import no topo -- já não
    quebraria. O import explícito em `celery_app.py`/`main.py` continua lá
    de propósito: garante o registro mesmo que um dia alguém acesse o banco
    por um caminho que não passe por `app.models.*`, e deixa a dependência
    visível em vez de implícita numa regra de import."""
    resultado = _rodar_em_processo_novo(
        "import app.models.busca_leads  # e mais nada\n"
        "from app.core.database import Base\n"
        "print(','.join(sorted(Base.metadata.tables)))\n"
    )
    assert resultado.returncode == 0, resultado.stderr
    assert resultado.stdout.strip().split(",") == TABELAS_ESPERADAS


def test_metadata_incompleto_realmente_levanta_o_erro():
    """Anti-tautologia: prova que o erro que este arquivo guarda é real, e
    que os testes acima passam por mérito da correção, não porque
    SQLAlchemy tolera FK pendente.

    Monta um `MetaData` isolado com a MESMA forma do bug -- uma tabela com
    FK pra outra que não está registrada -- e confirma que `create_all`
    levanta `NoReferencedTableError`. Isolado do `Base` do projeto de
    propósito: depois da correção não há mais como reproduzir a condição
    usando os modelos reais (ver o teste anterior), então a demonstração
    tem que ser sintética."""
    resultado = _rodar_em_processo_novo(
        "from sqlalchemy import Column, ForeignKey, MetaData, String, Table, create_engine\n"
        "from sqlalchemy.exc import NoReferencedTableError\n"
        "md = MetaData()\n"
        "Table('filha', md, Column('pai_id', String, ForeignKey('pai_ausente.id')))\n"
        "try:\n"
        "    md.create_all(create_engine('sqlite:///:memory:'))\n"
        "except NoReferencedTableError:\n"
        "    print('LEVANTOU')\n"
        "else:\n"
        "    print('NAO_LEVANTOU')\n"
    )
    assert resultado.returncode == 0, resultado.stderr
    assert resultado.stdout.strip() == "LEVANTOU"


def test_todo_modulo_de_modelo_esta_registrado():
    """Pega o caso "criei app/models/novo.py e esqueci do __init__" -- e
    também pegaria alguém esvaziando o `__init__.py` de volta.

    Em subprocesso pelo mesmo motivo do resto do arquivo: dentro do pytest,
    os outros testes já importaram todos os módulos de modelo, então
    `sys.modules` estaria populado mesmo com o `__init__` vazio."""
    modulos_no_disco = sorted(
        caminho.stem
        for caminho in (RAIZ_BACKEND / "app" / "models").glob("*.py")
        if caminho.stem != "__init__"
    )
    resultado = _rodar_em_processo_novo(
        "import sys\n"
        "import app.models\n"
        "nomes = {n.rsplit('.', 1)[-1] for n in sys.modules if n.startswith('app.models.')}\n"
        "print(','.join(sorted(nomes)))\n"
    )
    assert resultado.returncode == 0, resultado.stderr
    importados = resultado.stdout.strip().split(",")
    faltando = sorted(set(modulos_no_disco) - set(importados))
    assert not faltando, (
        f"módulo(s) de modelo não registrado(s) em app/models/__init__.py: {faltando}. "
        "Adicione o import lá -- ver a docstring daquele arquivo."
    )
