"""
Testes da resolução de município + UF -> código IBGE / código CNES.

Usa httpx.MockTransport (não precisa de rede real) — mesmo padrão de
test_cnes.py e test_receita_federal.py.
"""
import httpx

from app.services.ibge_municipios import (
    codigo_ibge_para_cnes,
    fetch_municipios_por_uf,
    resolve_codigo_municipio_ibge,
)

MUNICIPIOS_RR = [
    {"id": 1400100, "nome": "Boa Vista"},
    {"id": 1400159, "nome": "Bonfim"},
    {"id": 1400456, "nome": "Pacaraima"},
]


def _client_rr() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/localidades/estados/RR/municipios"
        return httpx.Response(200, json=MUNICIPIOS_RR)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fake")


# --- fetch_municipios_por_uf ---

def test_fetch_municipios_por_uf_parseia_lista():
    municipios = fetch_municipios_por_uf("RR", client=_client_rr())
    assert len(municipios) == 3
    assert municipios[0].codigo_ibge == 1400100
    assert municipios[0].nome == "Boa Vista"


def test_fetch_municipios_por_uf_maiuscula_sigla():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/localidades/estados/RR/municipios"
        return httpx.Response(200, json=MUNICIPIOS_RR)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fake")
    municipios = fetch_municipios_por_uf("rr", client=client)  # minúscula
    assert len(municipios) == 3


# --- resolve_codigo_municipio_ibge ---

def test_resolve_encontra_match_exato():
    codigo = resolve_codigo_municipio_ibge("Boa Vista", "RR", client=_client_rr())
    assert codigo == 1400100


def test_resolve_ignora_acento_e_caixa():
    codigo = resolve_codigo_municipio_ibge("pacaraima", "RR", client=_client_rr())
    assert codigo == 1400456


def test_resolve_nao_encontrado_retorna_none():
    codigo = resolve_codigo_municipio_ibge("Município Inexistente", "RR", client=_client_rr())
    assert codigo is None


# --- codigo_ibge_para_cnes ---

def test_codigo_ibge_para_cnes_sao_paulo():
    """Confirmado por teste manual contra a API real do CNES em 19/08/2026
    (ver docstring do módulo)."""
    assert codigo_ibge_para_cnes(3550308) == 355030


def test_codigo_ibge_para_cnes_boa_vista():
    assert codigo_ibge_para_cnes(1400100) == 140010
