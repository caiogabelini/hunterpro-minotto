"""
Testes da integração com a API do CNES.

Usa httpx.MockTransport (não precisa de rede real) — mesmo espírito do
FakeRedis em test_pgfn_cache.py: simula só o subconjunto de comportamento
da API que o módulo realmente usa, incluindo as limitações confirmadas por
teste manual em 19/08/2026 (sem filtro por CNPJ, página menor que o limit
pedido).
"""
import httpx
import pytest

from app.services.cnes import (
    CnesEstabelecimento,
    fetch_estabelecimentos_por_municipio,
    find_estabelecimento_by_cnpj,
    get_estabelecimento_by_codigo_cnes,
)

CNPJ_CLINICA_A = "24072393000107"
CNPJ_CLINICA_A_FORMATADO = "24.072.393/0001-07"


def _estabelecimento_raw(codigo_cnes: int, nome_fantasia: str, numero_cnpj: str | None, codigo_tipo_unidade: int = 22) -> dict:
    """Uma linha de resposta da API, no formato real observado (ver docstring de cnes.py)."""
    return {
        "codigo_cnes": codigo_cnes,
        "nome_fantasia": nome_fantasia,
        "nome_razao_social": nome_fantasia,
        "numero_cnpj": numero_cnpj,
        "codigo_tipo_unidade": codigo_tipo_unidade,
        "codigo_municipio": 313890,
        "codigo_uf": 31,
        "data_atualizacao": "2025-09-03",
    }


def _estabelecimento(codigo_cnes: str, numero_cnpj: str | None) -> CnesEstabelecimento:
    """Constrói um CnesEstabelecimento já parseado, pros testes de
    find_estabelecimento_by_cnpj que não precisam passar pela API."""
    return CnesEstabelecimento(
        codigo_cnes=codigo_cnes,
        nome_fantasia="CLINICA TESTE",
        nome_razao_social="CLINICA TESTE LTDA",
        numero_cnpj=numero_cnpj,
        codigo_tipo_unidade=22,
        codigo_municipio=313890,
        codigo_uf=31,
        data_atualizacao="2025-09-03",
    )


# --- fetch_estabelecimentos_por_municipio: paginação ---

def test_fetch_pagina_unica_para_no_vazio():
    """Página com resultado seguida de página vazia -> para, sem terceira chamada."""
    chamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(dict(request.url.params))
        offset = int(request.url.params["offset"])
        if offset == 0:
            return httpx.Response(200, json={"estabelecimentos": [
                _estabelecimento_raw(1, "CLINICA A", CNPJ_CLINICA_A),
                _estabelecimento_raw(2, "CLINICA B", None),
            ]})
        return httpx.Response(200, json={"estabelecimentos": []})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fake")
    resultado = fetch_estabelecimentos_por_municipio(313890, client=client)

    assert len(resultado) == 2
    assert len(chamadas) == 2  # buscou a página cheia + a página vazia que sinaliza o fim
    assert chamadas[0]["codigo_municipio"] == "313890"
    assert chamadas[0]["offset"] == "0"
    assert chamadas[1]["offset"] == "2"  # avança pelo tamanho REAL devolvido, não um valor fixo


def test_fetch_avanca_offset_por_tamanho_real_da_pagina():
    """API sempre limita a página a um valor menor que o `limit` pedido
    (observado: 20, mesmo pedindo 500) -- o loop precisa respeitar isso."""
    paginas = [
        [_estabelecimento_raw(i, f"CLINICA {i}", None) for i in range(3)],  # página "cheia" com 3 (simula o cap real)
        [],
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        indice_pagina = 0 if offset == 0 else 1
        return httpx.Response(200, json={"estabelecimentos": paginas[indice_pagina]})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fake")
    resultado = fetch_estabelecimentos_por_municipio(313890, client=client)

    assert len(resultado) == 3


def test_fetch_municipio_sem_estabelecimentos_retorna_lista_vazia():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"estabelecimentos": []})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fake")
    resultado = fetch_estabelecimentos_por_municipio(999999, client=client)

    assert resultado == []


def test_fetch_erro_http_propaga_excecao():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fake")
    with pytest.raises(httpx.HTTPStatusError):
        fetch_estabelecimentos_por_municipio(313890, client=client)


# --- find_estabelecimento_by_cnpj: cruzamento local (a API não filtra por CNPJ) ---

def test_find_by_cnpj_encontra_match_normalizado():
    estabelecimentos = [
        _estabelecimento("1", CNPJ_CLINICA_A),
        _estabelecimento("2", None),
    ]

    encontrado = find_estabelecimento_by_cnpj(estabelecimentos, CNPJ_CLINICA_A_FORMATADO)
    assert encontrado is not None
    assert encontrado.codigo_cnes == "1"


def test_find_by_cnpj_nao_encontrado_retorna_none():
    estabelecimentos = [_estabelecimento("1", None)]
    assert find_estabelecimento_by_cnpj(estabelecimentos, "99999999000199") is None


def test_find_by_cnpj_invalido_retorna_none():
    assert find_estabelecimento_by_cnpj([], "cpf-invalido") is None


# --- get_estabelecimento_by_codigo_cnes ---

def test_get_by_codigo_cnes_encontrado():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/cnes/estabelecimentos/9629904"
        return httpx.Response(200, json=_estabelecimento_raw(9629904, "CLINIMED", CNPJ_CLINICA_A))

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fake")
    resultado = get_estabelecimento_by_codigo_cnes(9629904, client=client)

    assert resultado is not None
    assert resultado.nome_fantasia == "CLINIMED"
    assert resultado.numero_cnpj == CNPJ_CLINICA_A


def test_get_by_codigo_cnes_nao_encontrado_retorna_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fake")
    assert get_estabelecimento_by_codigo_cnes(999999, client=client) is None
