"""
Testes da validação de WhatsApp via Evolution API.

Usa httpx.MockTransport (não precisa de rede real) — mesmo padrão de
test_cnes.py e test_receita_federal.py.
"""
import json

import httpx
import pytest

from app.services.whatsapp import check_whatsapp, formatar_numero_whatsapp

# --- formatar_numero_whatsapp ---


def test_formata_numero_sem_codigo_do_pais_celular():
    assert formatar_numero_whatsapp("11987654321") == "5511987654321"  # 11 dígitos -> +55 = 13


def test_formata_numero_sem_codigo_do_pais_fixo():
    assert formatar_numero_whatsapp("1133334444") == "551133334444"  # 10 dígitos -> +55 = 12


def test_formata_numero_ja_com_codigo_do_pais_fica_igual():
    assert formatar_numero_whatsapp("5511987654321") == "5511987654321"  # já 13 dígitos


def test_formata_numero_remove_pontuacao():
    assert formatar_numero_whatsapp("(11) 98765-4321") == "5511987654321"


def test_formata_numero_ddd_55_nao_e_confundido_com_codigo_do_pais():
    """DDD 55 é do Rio Grande do Sul -- um número nacional de 11 dígitos
    que começa com '55' (o DDD, não o código do país) ainda precisa
    ganhar o prefixo 55 do país. Ver ressalva no módulo sobre decidir por
    tamanho, não por prefixo."""
    assert formatar_numero_whatsapp("55991234567") == "5555991234567"


def test_formata_numero_invalido_poucos_digitos_retorna_none():
    assert formatar_numero_whatsapp("1234") is None


def test_formata_numero_vazio_retorna_none():
    assert formatar_numero_whatsapp("") is None
    assert formatar_numero_whatsapp(None) is None


def test_formata_numero_muitos_digitos_retorna_none():
    assert formatar_numero_whatsapp("551198765432199999") is None


# --- check_whatsapp ---


def test_check_whatsapp_numero_invalido_nao_bate_na_api():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("não deveria chamar a API com número inválido")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fake")
    resultado = check_whatsapp("123", client=client)

    assert resultado.numero_valido is False
    assert resultado.numero_formatado == ""
    assert resultado.tem_whatsapp is False


def test_check_whatsapp_numero_com_whatsapp_ativo():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.startswith("/chat/whatsappNumbers/")
        assert "apikey" in request.headers
        return httpx.Response(
            200,
            json=[{"exists": True, "jid": "5511987654321@s.whatsapp.net", "number": "5511987654321"}],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fake")
    resultado = check_whatsapp("11987654321", client=client)

    assert resultado.numero_valido is True
    assert resultado.numero_formatado == "5511987654321"
    assert resultado.tem_whatsapp is True
    assert resultado.jid == "5511987654321@s.whatsapp.net"


def test_check_whatsapp_numero_sem_whatsapp():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"exists": False, "number": "5511987654321"}])

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fake")
    resultado = check_whatsapp("11987654321", client=client)

    assert resultado.numero_valido is True
    assert resultado.tem_whatsapp is False
    assert resultado.jid is None


def test_check_whatsapp_envia_body_com_numero_formatado():
    corpo_recebido = {}

    def handler(request: httpx.Request) -> httpx.Response:
        corpo_recebido.update(json.loads(request.content))
        return httpx.Response(200, json=[{"exists": True, "jid": "x@s.whatsapp.net"}])

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fake")
    check_whatsapp("(11) 98765-4321", client=client)

    assert corpo_recebido == {"numbers": ["5511987654321"]}


def test_check_whatsapp_erro_http_propaga_excecao():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://fake")
    with pytest.raises(httpx.HTTPStatusError):
        check_whatsapp("11987654321", client=client)
