"""
Testes da busca via Google Places API (New).

Usa httpx.MockTransport (não precisa de rede real) — mesmo padrão de
test_cnes.py e test_site_scraping.py. O contrato foi confirmado contra a
documentação oficial do Google (ver docstring de google_places.py), não
contra uma chamada real -- limitação de ambiente desta sessão, não falta
de tentativa.
"""
import json

import httpx
import pytest

from app.services.google_places import FIELD_MASK, buscar_estabelecimento

NOME = "Clinica Teste"


def _resposta_com_lugar(**overrides) -> dict:
    base = {
        "id": "ChIJteste123",
        "displayName": {"text": "Clinica Teste", "languageCode": "pt-BR"},
        "formattedAddress": "Rua Teste, 100 - Sao Paulo, SP",
        "websiteUri": "https://clinicateste.com.br",
        "rating": 4.5,
        "userRatingCount": 120,
        "nationalPhoneNumber": "(11) 98765-4321",
    }
    base.update(overrides)
    return {"places": [base]}


def test_busca_encontra_estabelecimento():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/places:searchText"
        assert request.headers["x-goog-api-key"] != ""
        assert request.headers["x-goog-fieldmask"] == FIELD_MASK
        corpo = json.loads(request.content)
        assert corpo["textQuery"] == f"{NOME} SP"
        assert corpo["regionCode"] == "BR"
        return httpx.Response(200, json=_resposta_com_lugar())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = buscar_estabelecimento(NOME, uf="SP", client=client)

    assert resultado is not None
    assert resultado.place_id == "ChIJteste123"
    assert resultado.nome == "Clinica Teste"
    assert resultado.site == "https://clinicateste.com.br"
    assert resultado.rating == 4.5
    assert resultado.avaliacoes == 120
    assert resultado.telefone == "(11) 98765-4321"


def test_busca_sem_uf_manda_so_o_nome():
    def handler(request: httpx.Request) -> httpx.Response:
        corpo = json.loads(request.content)
        assert corpo["textQuery"] == NOME
        return httpx.Response(200, json=_resposta_com_lugar())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    buscar_estabelecimento(NOME, client=client)


def test_busca_sem_resultado_retorna_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"places": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert buscar_estabelecimento(NOME, client=client) is None


def test_busca_resposta_sem_campo_places_retorna_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert buscar_estabelecimento(NOME, client=client) is None


def test_busca_lida_com_campos_ausentes_no_lugar_encontrado():
    """Nem todo estabelecimento tem site/rating/telefone preenchidos --
    não deve quebrar."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"places": [{"id": "ChIJminimo"}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = buscar_estabelecimento(NOME, client=client)

    assert resultado is not None
    assert resultado.place_id == "ChIJminimo"
    assert resultado.nome == ""
    assert resultado.site is None
    assert resultado.rating is None
    assert resultado.avaliacoes is None
    assert resultado.telefone is None


def test_busca_usa_o_primeiro_resultado_entre_varios():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "places": [
                    {"id": "primeiro", "displayName": {"text": "Primeiro"}},
                    {"id": "segundo", "displayName": {"text": "Segundo"}},
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = buscar_estabelecimento(NOME, client=client)

    assert resultado.place_id == "primeiro"


def test_busca_erro_http_propaga_excecao():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        buscar_estabelecimento(NOME, client=client)
