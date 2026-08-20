"""
Testes da integração com a Receita Federal (via BrasilAPI).

Usa httpx.MockTransport (não precisa de rede real) — mesmo padrão de
test_cnes.py.
"""
from datetime import date

import httpx
import pytest

from app.services.receita_federal import (
    Socio,
    anos_mercado,
    fetch_empresa,
    identificar_decisor,
)

CNPJ_CLINICA = "19131243000197"


def _empresa_raw(**overrides) -> dict:
    """Uma resposta da BrasilAPI, no formato real observado (ver docstring
    de receita_federal.py). `overrides` sobrescreve campos específicos por teste."""
    base = {
        "cnpj": CNPJ_CLINICA,
        "razao_social": "CLINICA TESTE LTDA",
        "nome_fantasia": "CLINICA TESTE",
        "descricao_situacao_cadastral": "ATIVA",
        "cnae_fiscal": 8630501,
        "cnae_fiscal_descricao": "Atividade médica ambulatorial com recursos para realização de procedimentos cirúrgicos",
        "porte": "DEMAIS",
        "municipio": "SAO PAULO",
        "uf": "SP",
        "codigo_municipio_ibge": 3550308,
        "data_inicio_atividade": "2013-10-03",
        "opcao_pelo_simples": True,
        "opcao_pelo_mei": None,
        "qsa": [
            {"nome_socio": "FULANO DE TAL", "qualificacao_socio": "Sócio"},
            {"nome_socio": "CICLANA ADMINISTRADORA", "qualificacao_socio": "Sócio-Administrador"},
        ],
    }
    base.update(overrides)
    return base


# --- fetch_empresa ---

def test_fetch_empresa_encontrada():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/cnpj/v1/{CNPJ_CLINICA}"
        return httpx.Response(200, json=_empresa_raw())

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fake")
    empresa = fetch_empresa(CNPJ_CLINICA, client=client)

    assert empresa is not None
    assert empresa.razao_social == "CLINICA TESTE LTDA"
    assert empresa.cnae_principal_codigo == "8630501"
    assert empresa.codigo_municipio_ibge == 3550308
    assert len(empresa.socios) == 2


def test_fetch_empresa_nao_encontrada_retorna_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fake")
    assert fetch_empresa("99999999000199", client=client) is None


def test_fetch_empresa_erro_http_propaga_excecao():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fake")
    with pytest.raises(httpx.HTTPStatusError):
        fetch_empresa(CNPJ_CLINICA, client=client)


def test_parse_lida_com_campos_ausentes():
    """Resposta mínima (sem qsa, sem cnae, sem código de município) não quebra o parser."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"cnpj": CNPJ_CLINICA, "razao_social": "MINIMA LTDA"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fake")
    empresa = fetch_empresa(CNPJ_CLINICA, client=client)

    assert empresa is not None
    assert empresa.razao_social == "MINIMA LTDA"
    assert empresa.socios == []
    assert empresa.cnae_principal_codigo is None
    assert empresa.codigo_municipio_ibge is None


# --- anos_mercado ---

def test_anos_mercado_calcula_completos():
    assert anos_mercado("2013-10-03", hoje=date(2026, 8, 19)) == 12


def test_anos_mercado_ainda_nao_completou_aniversario_este_ano():
    assert anos_mercado("2013-12-01", hoje=date(2026, 8, 19)) == 12


def test_anos_mercado_data_ausente_retorna_none():
    assert anos_mercado(None) is None


def test_anos_mercado_empresa_aberta_este_ano_e_zero():
    assert anos_mercado("2026-06-01", hoje=date(2026, 8, 19)) == 0


# --- identificar_decisor ---

def test_identificar_decisor_encontra_por_palavra_chave():
    socios = [
        Socio(nome="FULANO DE TAL", qualificacao="Sócio"),
        Socio(nome="CICLANA ADMINISTRADORA", qualificacao="Sócio-Administrador"),
    ]
    decisor = identificar_decisor(socios)
    assert decisor is not None
    assert decisor.nome == "CICLANA ADMINISTRADORA"


def test_identificar_decisor_sem_match_cai_pro_primeiro():
    socios = [
        Socio(nome="FULANO DE TAL", qualificacao="Sócio"),
        Socio(nome="BELTRANO", qualificacao="Sócio"),
    ]
    decisor = identificar_decisor(socios)
    assert decisor is not None
    assert decisor.nome == "FULANO DE TAL"


def test_identificar_decisor_lista_vazia_retorna_none():
    assert identificar_decisor([]) is None
