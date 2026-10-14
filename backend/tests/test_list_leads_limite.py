"""
GET /api/leads -- validação do `limit` (ge=1, le=1000) e ordenação
determinística (score desc, created_at desc, id), 14/10/2026.

Com 120 leads no banco o Kanban mostrava só 50 (default do `limit`, que
ele não passava) e sem desempate os leads empatados na fronteira podiam
variar entre carregamentos. Reaproveita as fixtures de test_leads_routes.py.
"""
from datetime import datetime

import pytest

from app.api.routes.leads import LIMITE_MAXIMO_LIST_LEADS
from app.models.lead import Lead
from tests.test_leads_routes import client, db_session, token, usuario  # noqa: F401  (fixtures)


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _criar(db_session, i: int, score=None, created_at=None) -> Lead:
    lead = Lead(
        cnpj=f"{i:014d}",
        razao_social=f"Clinica {i}",
        score_total=score,
        created_at=created_at or datetime(2026, 10, 1),
    )
    db_session.add(lead)
    db_session.commit()
    return lead


@pytest.mark.parametrize("limit", [0, -1, -50])
def test_limit_zero_ou_negativo_e_recusado(client, token, limit):
    assert client.get(f"/api/leads?limit={limit}", headers=_h(token)).status_code == 422


def test_teto_1000_aceito_e_1001_recusado(client, token):
    assert LIMITE_MAXIMO_LIST_LEADS == 1000
    assert client.get("/api/leads?limit=1000", headers=_h(token)).status_code == 200
    assert client.get("/api/leads?limit=1001", headers=_h(token)).status_code == 422


def test_default_continua_50(client, db_session, token):
    for i in range(55):
        _criar(db_session, i, score=10.0)

    assert len(client.get("/api/leads", headers=_h(token)).json()) == 50


def test_limit_explicito_traz_mais_que_o_default(client, db_session, token):
    """O caso de produção: 120 leads, Kanban pedindo o teto, todos voltam."""
    for i in range(120):
        _criar(db_session, i, score=float(i % 7))

    r = client.get(f"/api/leads?limit={LIMITE_MAXIMO_LIST_LEADS}", headers=_h(token))

    assert len(r.json()) == 120


def test_ordenacao_estavel_com_empate_de_score(client, db_session, token):
    """
    20 leads com o MESMO score e o MESMO created_at, `limit` cortando no
    meio do empate: duas chamadas devolvem o mesmo conjunto na mesma
    ordem, e essa ordem é a do `id` (último critério de desempate).
    """
    leads = [_criar(db_session, i, score=42.0) for i in range(20)]

    primeira = [l["id"] for l in client.get("/api/leads?limit=7", headers=_h(token)).json()]
    segunda = [l["id"] for l in client.get("/api/leads?limit=7", headers=_h(token)).json()]

    assert primeira == segunda
    assert primeira == sorted(l.id for l in leads)[:7]


def test_desempate_por_created_at_desc_antes_do_id(client, db_session, token):
    antigo = _criar(db_session, 1, score=30.0, created_at=datetime(2026, 8, 1))
    novo = _criar(db_session, 2, score=30.0, created_at=datetime(2026, 10, 1))
    maior = _criar(db_session, 3, score=90.0, created_at=datetime(2026, 1, 1))
    sem_score = _criar(db_session, 4, score=None, created_at=datetime(2026, 12, 1))

    ids = [l["id"] for l in client.get("/api/leads?limit=10", headers=_h(token)).json()]

    assert ids == [maior.id, novo.id, antigo.id, sem_score.id]
