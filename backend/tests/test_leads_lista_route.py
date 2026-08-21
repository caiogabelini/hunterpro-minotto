"""
Testes de GET /api/leads/lista (busca/filtros/ordenação/paginação) --
endpoint próprio e aditivo pra tela "Lista de Leads", que NÃO reaproveita
nem modifica GET /api/leads (usado pelo Kanban). Ver
app/api/routes/leads.py::listar_leads_paginado.

Mesmo padrão de fixtures de test_leads_routes.py: SQLite em memória
(StaticPool + check_same_thread=False), injetado via
app.dependency_overrides[get_db].

Um teste aqui (`test_get_leads_antigo_continua_bare_list_sem_paginacao`)
confirma explicitamente que GET /api/leads (o antigo) não mudou de
formato -- além disso, a suíte inteira de test_leads_routes.py (não
tocada nesta sessão) precisa continuar passando 100%, o que é a prova
real de que nada quebrou (rodar `pytest tests/` cobre isso).
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.lead import Lead
from app.models.user import User, UserRole

SENHA = "senha-forte-123"


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def client(db_session):
    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def usuario(db_session):
    usuario = User(email="cliente@minotto.com.br", senha_hash=hash_password(SENHA), role=UserRole.CLIENT.value, ativo=True)
    db_session.add(usuario)
    db_session.commit()
    db_session.refresh(usuario)
    return usuario


@pytest.fixture()
def token(usuario):
    return create_access_token({"user_id": usuario.id, "role": usuario.role})


def _criar_lead(db_session, **overrides) -> Lead:
    dados = dict(cnpj="11.111.111/0001-11", razao_social="Clínica Exemplo")
    dados.update(overrides)
    lead = Lead(**dados)
    db_session.add(lead)
    db_session.commit()
    db_session.refresh(lead)
    return lead


# --- busca ---


def test_busca_por_nome_parcial(client, db_session, token):
    _criar_lead(db_session, cnpj="11.111.111/0001-11", razao_social="Clínica Cardiológica São Paulo")
    _criar_lead(db_session, cnpj="22.222.222/0001-22", razao_social="Consultório Odontológico ABC")

    resp = client.get("/api/leads/lista", params={"busca": "cardio"}, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["total"] == 1
    assert len(corpo["items"]) == 1
    assert corpo["items"][0]["razao_social"] == "Clínica Cardiológica São Paulo"


def test_busca_por_nome_fantasia_parcial(client, db_session, token):
    _criar_lead(db_session, cnpj="11.111.111/0001-11", razao_social="Razão Social LTDA", nome_fantasia="Clínica Vida")
    _criar_lead(db_session, cnpj="22.222.222/0001-22", razao_social="Outra Razão LTDA", nome_fantasia="Consultório Saúde")

    resp = client.get("/api/leads/lista", params={"busca": "vida"}, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["total"] == 1
    assert corpo["items"][0]["nome_fantasia"] == "Clínica Vida"


def test_busca_por_cnpj_parcial(client, db_session, token):
    _criar_lead(db_session, cnpj="11.222.333/0001-81", razao_social="Clínica A")
    _criar_lead(db_session, cnpj="99.888.777/0001-66", razao_social="Clínica B")

    resp = client.get("/api/leads/lista", params={"busca": "222.333"}, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["total"] == 1
    assert corpo["items"][0]["cnpj"] == "11.222.333/0001-81"


def test_busca_case_insensitive(client, db_session, token):
    """Maiúsculas/minúsculas em ASCII -- funciona igual no SQLite daqui
    e no Postgres de produção. Deliberadamente SEM acento no trecho que
    varia de caixa (ex.: "í"/"Í"): o `lower()` embutido do SQLite só
    normaliza ASCII -- 'MAIÚSCULA'.lower() no SQLite não vira
    'maiúscula' de verdade (o Í maiúsculo não é convertido), então um
    teste assim passaria contra Postgres real (que faz folding de
    Unicode corretamente) mas falharia aqui só por essa limitação do
    SQLite de teste, não por um bug do endpoint -- ver a mesma nota no
    docstring de `listar_leads_paginado`."""
    _criar_lead(db_session, razao_social="Clínica GRANDE Porte")

    resp = client.get("/api/leads/lista", params={"busca": "grande"}, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_busca_sem_correspondencia_retorna_vazio(client, db_session, token):
    _criar_lead(db_session, razao_social="Clínica Exemplo")

    resp = client.get("/api/leads/lista", params={"busca": "não existe nada assim"}, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["total"] == 0
    assert corpo["items"] == []


# --- ordenação ---


def test_ordenacao_padrao_e_score_desc(client, db_session, token):
    _criar_lead(db_session, cnpj="11.111.111/0001-11", razao_social="Score Baixo", score_total=20.0)
    _criar_lead(db_session, cnpj="22.222.222/0001-22", razao_social="Score Alto", score_total=90.0)

    resp = client.get("/api/leads/lista", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    nomes = [item["razao_social"] for item in resp.json()["items"]]
    assert nomes == ["Score Alto", "Score Baixo"]


def test_ordenacao_score_asc(client, db_session, token):
    _criar_lead(db_session, cnpj="11.111.111/0001-11", razao_social="Score Baixo", score_total=20.0)
    _criar_lead(db_session, cnpj="22.222.222/0001-22", razao_social="Score Alto", score_total=90.0)

    resp = client.get(
        "/api/leads/lista", params={"ordenar_por": "score_total", "ordem": "asc"}, headers={"Authorization": f"Bearer {token}"}
    )

    assert resp.status_code == 200
    nomes = [item["razao_social"] for item in resp.json()["items"]]
    assert nomes == ["Score Baixo", "Score Alto"]


def test_ordenacao_por_created_at(client, db_session, token):
    from datetime import datetime

    _criar_lead(db_session, cnpj="11.111.111/0001-11", razao_social="Mais antigo", created_at=datetime(2026, 1, 1))
    _criar_lead(db_session, cnpj="22.222.222/0001-22", razao_social="Mais recente", created_at=datetime(2026, 6, 1))

    resp = client.get(
        "/api/leads/lista", params={"ordenar_por": "created_at", "ordem": "desc"}, headers={"Authorization": f"Bearer {token}"}
    )

    assert resp.status_code == 200
    nomes = [item["razao_social"] for item in resp.json()["items"]]
    assert nomes == ["Mais recente", "Mais antigo"]


def test_ordenar_por_invalido_retorna_422(client, token):
    resp = client.get("/api/leads/lista", params={"ordenar_por": "cnpj"}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 422


def test_ordem_invalida_retorna_422(client, token):
    resp = client.get("/api/leads/lista", params={"ordem": "aleatoria"}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 422


# --- paginação ---


def test_paginacao_total_correto_e_tamanho_de_pagina(client, db_session, token):
    for i in range(7):
        _criar_lead(db_session, cnpj=f"11.111.111/0001-{i:02d}", razao_social=f"Clínica {i}", score_total=float(i))

    resp = client.get("/api/leads/lista", params={"por_pagina": 3}, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["total"] == 7
    assert len(corpo["items"]) == 3
    assert corpo["pagina"] == 1
    assert corpo["por_pagina"] == 3


def test_paginacao_segunda_pagina_traz_itens_diferentes(client, db_session, token):
    for i in range(7):
        _criar_lead(db_session, cnpj=f"11.111.111/0001-{i:02d}", razao_social=f"Clínica {i}", score_total=float(i))

    resp1 = client.get(
        "/api/leads/lista", params={"por_pagina": 3, "pagina": 1}, headers={"Authorization": f"Bearer {token}"}
    )
    resp2 = client.get(
        "/api/leads/lista", params={"por_pagina": 3, "pagina": 2}, headers={"Authorization": f"Bearer {token}"}
    )
    resp3 = client.get(
        "/api/leads/lista", params={"por_pagina": 3, "pagina": 3}, headers={"Authorization": f"Bearer {token}"}
    )

    ids_pagina_1 = {item["id"] for item in resp1.json()["items"]}
    ids_pagina_2 = {item["id"] for item in resp2.json()["items"]}
    assert len(resp1.json()["items"]) == 3
    assert len(resp2.json()["items"]) == 3
    assert len(resp3.json()["items"]) == 1  # sobra do total de 7
    assert ids_pagina_1.isdisjoint(ids_pagina_2)
    assert resp3.json()["pagina"] == 3


def test_por_pagina_acima_do_maximo_retorna_422(client, token):
    resp = client.get("/api/leads/lista", params={"por_pagina": 101}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 422


def test_pagina_abaixo_de_um_retorna_422(client, token):
    resp = client.get("/api/leads/lista", params={"pagina": 0}, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 422


# --- filtros combinados ---


def test_filtros_combinados_busca_prioridade_e_kanban_status(client, db_session, token):
    _criar_lead(
        db_session,
        cnpj="11.111.111/0001-11",
        razao_social="Clínica Cardiológica Alpha",
        prioridade="A",
        kanban_status="novo_lead",
    )
    _criar_lead(
        db_session,
        cnpj="22.222.222/0001-22",
        razao_social="Clínica Cardiológica Beta",
        prioridade="B",  # prioridade diferente -- não deve aparecer
        kanban_status="novo_lead",
    )
    _criar_lead(
        db_session,
        cnpj="33.333.333/0001-33",
        razao_social="Clínica Cardiológica Gama",
        prioridade="A",
        kanban_status="ganho",  # status diferente -- não deve aparecer
    )
    _criar_lead(
        db_session,
        cnpj="44.444.444/0001-44",
        razao_social="Consultório Odontológico Delta",  # não bate a busca -- não deve aparecer
        prioridade="A",
        kanban_status="novo_lead",
    )

    resp = client.get(
        "/api/leads/lista",
        params={"busca": "cardio", "prioridade": "A", "kanban_status": "novo_lead"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["total"] == 1
    assert corpo["items"][0]["razao_social"] == "Clínica Cardiológica Alpha"


def test_sem_token_retorna_401(client):
    resp = client.get("/api/leads/lista")
    assert resp.status_code == 401


# --- confirma que GET /api/leads (o antigo) não mudou ---


def test_get_leads_antigo_continua_bare_list_sem_paginacao(client, db_session, token):
    """Regressão explícita: GET /api/leads continua devolvendo uma
    lista simples (não o envelope {items, total, pagina, por_pagina} do
    endpoint novo) -- prova de que listar_leads_paginado é aditivo, não
    uma modificação de list_leads."""
    _criar_lead(db_session, razao_social="Clínica Exemplo")

    resp = client.get("/api/leads", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    corpo = resp.json()
    assert isinstance(corpo, list)
    assert len(corpo) == 1
    assert corpo[0]["razao_social"] == "Clínica Exemplo"
