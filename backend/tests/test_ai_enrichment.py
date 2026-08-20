"""
Testes da inferência de dados do site via Claude (Messages API, raw HTTP).

Usa httpx.MockTransport (não precisa de rede real) — mesmo padrão de
test_cnes.py e test_site_scraping.py.
"""
import json

import httpx
import pytest

from app.services.ai_enrichment import ANTHROPIC_MODEL, inferir_dados_site

TEXTO_COM_RQE = "Somos especialistas em cardiologia. RQE 98765. Atendemos com hora marcada."
TEXTO_SEM_RQE = "Clínica geral, atendimento de segunda a sexta."


def _resposta_anthropic(texto_resposta: str) -> dict:
    """Formato real da Messages API: content é uma lista de blocos."""
    return {
        "id": "msg_teste",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": texto_resposta}],
        "model": ANTHROPIC_MODEL,
        "stop_reason": "end_turn",
    }


# --- inferir_dados_site: casos de sucesso ---


def test_rqe_encontrado():
    json_ia = json.dumps({
        "rqe_mencionado": True,
        "rqe_numero": "98765",
        "servicos": ["Cardiologia"],
    })

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert "x-api-key" in request.headers
        assert request.headers["anthropic-version"] == "2023-06-01"
        corpo = json.loads(request.content)
        assert corpo["model"] == ANTHROPIC_MODEL
        assert TEXTO_COM_RQE in corpo["messages"][0]["content"]
        return httpx.Response(200, json=_resposta_anthropic(json_ia))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = inferir_dados_site(TEXTO_COM_RQE, client=client)

    assert resultado.rqe_mencionado is True
    assert resultado.rqe_numero == "98765"
    assert resultado.servicos == ["Cardiologia"]


def test_rqe_nao_mencionado():
    json_ia = json.dumps({
        "rqe_mencionado": False,
        "rqe_numero": None,
        "servicos": ["Clínica geral"],
    })

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_resposta_anthropic(json_ia))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = inferir_dados_site(TEXTO_SEM_RQE, client=client)

    assert resultado.rqe_mencionado is False
    assert resultado.rqe_numero is None
    assert resultado.servicos == ["Clínica geral"]


def test_json_embrulhado_em_bloco_de_codigo_markdown():
    """A IA às vezes responde com ```json ... ``` mesmo instruída a não
    fazer isso -- não deve quebrar o parsing."""
    texto_resposta = (
        "```json\n"
        + json.dumps({"rqe_mencionado": True, "rqe_numero": "111", "servicos": []})
        + "\n```"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_resposta_anthropic(texto_resposta))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = inferir_dados_site(TEXTO_COM_RQE, client=client)

    assert resultado.rqe_mencionado is True
    assert resultado.rqe_numero == "111"


def test_json_com_texto_extra_antes_e_depois():
    texto_resposta = (
        "Aqui está a análise solicitada:\n"
        + json.dumps({"rqe_mencionado": False, "rqe_numero": None, "servicos": ["Pediatria"]})
        + "\nEspero ter ajudado!"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_resposta_anthropic(texto_resposta))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = inferir_dados_site(TEXTO_SEM_RQE, client=client)

    assert resultado.servicos == ["Pediatria"]


def test_servicos_limitado_a_oito_itens():
    servicos_longos = [f"Servico {i}" for i in range(15)]
    json_ia = json.dumps({"rqe_mencionado": False, "rqe_numero": None, "servicos": servicos_longos})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_resposta_anthropic(json_ia))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = inferir_dados_site(TEXTO_SEM_RQE, client=client)

    assert len(resultado.servicos) == 8


# --- resposta malformada: não pode quebrar ---


def test_resposta_nao_e_json_devolve_valores_vazios():
    texto_resposta = "Desculpe, não consegui analisar esse conteúdo."

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_resposta_anthropic(texto_resposta))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = inferir_dados_site(TEXTO_SEM_RQE, client=client)

    assert resultado.rqe_mencionado is False
    assert resultado.rqe_numero is None
    assert resultado.servicos == []


def test_json_com_sintaxe_invalida_devolve_valores_vazios():
    texto_resposta = '{"rqe_mencionado": true, "servicos": [Cardiologia, Pediatria]}'  # sem aspas -- inválido

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_resposta_anthropic(texto_resposta))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = inferir_dados_site(TEXTO_SEM_RQE, client=client)

    assert resultado.rqe_mencionado is False
    assert resultado.servicos == []


def test_resposta_sem_bloco_de_texto_devolve_valores_vazios():
    """Content vazio ou só com blocos que não são "text" -- também não
    pode quebrar."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = inferir_dados_site(TEXTO_SEM_RQE, client=client)

    assert resultado.rqe_mencionado is False
    assert resultado.servicos == []


def test_erro_http_propaga_excecao():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        inferir_dados_site(TEXTO_SEM_RQE, client=client)
