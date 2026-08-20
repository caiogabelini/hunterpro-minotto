"""
Testes da inferência de dados do site via Claude (Messages API, raw HTTP).

Usa httpx.MockTransport (não precisa de rede real) — mesmo padrão de
test_cnes.py e test_site_scraping.py.
"""
import json

import httpx
import pytest

from app.services.ai_enrichment import (
    ANTHROPIC_MODEL,
    gerar_insights_estrategicos,
    gerar_mensagem_abordagem,
    inferir_dados_site,
)

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


# --- gerar_mensagem_abordagem ---


LEAD_BASE = {"razao_social": "Clínica Exemplo Ltda"}


def test_gera_mensagem_email_com_sucesso():
    """Canal "email" pede JSON (assunto + corpo) -- MensagemGerada.assunto
    vem preenchido."""
    json_ia = json.dumps({
        "assunto": "Uma ideia pra Clínica Exemplo",
        "corpo": "Olá! Vimos que a Clínica Exemplo tem uma presença digital forte. Podemos conversar 15 minutos essa semana?",
    })

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        corpo = json.loads(request.content)
        assert corpo["model"] == ANTHROPIC_MODEL
        assert "Clínica Exemplo Ltda" in corpo["messages"][0]["content"]
        assert "E-MAIL" in corpo["messages"][0]["content"]
        assert "3 a 4 frases" in corpo["messages"][0]["content"]
        return httpx.Response(200, json=_resposta_anthropic(json_ia))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = gerar_mensagem_abordagem(LEAD_BASE, canal="email", client=client)

    assert resultado.assunto == "Uma ideia pra Clínica Exemplo"
    assert resultado.conteudo == "Olá! Vimos que a Clínica Exemplo tem uma presença digital forte. Podemos conversar 15 minutos essa semana?"


def test_gera_mensagem_whatsapp_com_sucesso():
    """Canal "whatsapp" pede texto puro (sem JSON) -- MensagemGerada.assunto
    fica sempre None."""
    texto_gerado = "Oi! Vi que vocês têm ótimas avaliações no Google, topam bater um papo rápido?"

    def handler(request: httpx.Request) -> httpx.Response:
        corpo = json.loads(request.content)
        assert "WHATSAPP" in corpo["messages"][0]["content"]
        assert "1 a 2 frases" in corpo["messages"][0]["content"]
        return httpx.Response(200, json=_resposta_anthropic(texto_gerado))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = gerar_mensagem_abordagem(LEAD_BASE, canal="whatsapp", client=client)

    assert resultado.conteudo == texto_gerado
    assert resultado.assunto is None


def test_gera_mensagem_inclui_sinais_relevantes_no_prompt():
    """Sinais presentes no lead (dívida PGFN, RQE, rating) entram no
    prompt; a regra de tom consultivo pra PGFN também vai junto. Testado
    só uma vez (canal email) -- os dois canais compartilham
    _montar_contexto_lead, não precisa duplicar por canal."""
    lead = {
        "razao_social": "Clínica Exemplo Ltda",
        "decisor_nome": "Dr. Fulano",
        "divida_ativa_qtd_inscricoes": 3,
        "rqe_confirmado": True,
        "google_rating": 4.8,
        "google_avaliacoes": 200,
    }

    prompt_recebido = {}

    def handler(request: httpx.Request) -> httpx.Response:
        corpo = json.loads(request.content)
        prompt_recebido["texto"] = corpo["messages"][0]["content"]
        return httpx.Response(200, json=_resposta_anthropic(json.dumps({"assunto": "Oi", "corpo": "Mensagem gerada."})))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gerar_mensagem_abordagem(lead, canal="email", client=client)

    prompt = prompt_recebido["texto"]
    assert "Dr. Fulano" in prompt
    assert "3 inscrição" in prompt
    assert "RQE" in prompt
    assert "4.8" in prompt
    assert "tom consultivo" in prompt  # instrução de tom junto ao dado de dívida


def test_gera_mensagem_canal_invalido_levanta_value_error():
    with pytest.raises(ValueError):
        gerar_mensagem_abordagem(LEAD_BASE, canal="instagram")


def test_gera_mensagem_email_resposta_sem_json_valido_nao_quebra():
    """Resposta que não é JSON válido (formato inesperado da IA pro
    canal email) -- devolve conteudo="" em vez de lançar."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_resposta_anthropic("Desculpe, não consigo ajudar com isso."))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = gerar_mensagem_abordagem(LEAD_BASE, canal="email", client=client)

    assert resultado.conteudo == ""
    assert resultado.assunto is None


def test_gera_mensagem_resposta_vazia_nao_quebra():
    """Content vazio (sem bloco de texto) -- devolve conteudo="" em vez
    de lançar, nos dois canais."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    assert gerar_mensagem_abordagem(LEAD_BASE, canal="email", client=client).conteudo == ""
    assert gerar_mensagem_abordagem(LEAD_BASE, canal="whatsapp", client=client).conteudo == ""


def test_gera_mensagem_erro_http_nao_quebra():
    """Diferente de inferir_dados_site, erro HTTP aqui NÃO propaga --
    devolve conteudo="" pra rota decidir o que fazer."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = gerar_mensagem_abordagem(LEAD_BASE, canal="whatsapp", client=client)

    assert resultado.conteudo == ""


def test_gera_mensagem_erro_de_rede_nao_quebra():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("conexão recusada", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = gerar_mensagem_abordagem(LEAD_BASE, canal="email", client=client)

    assert resultado.conteudo == ""


# --- gerar_insights_estrategicos ---


LEAD_INSIGHTS_COMPLETO = {
    "razao_social": "Clínica Exemplo Ltda",
    "decisor_nome": "Dr. Fulano",
    "municipio": "São Paulo",
    "uf": "SP",
    "anos_mercado": 12,
    "situacao_cadastral": "ATIVA",
    "score_total": 78.5,
    "prioridade": "A",
    "score_detalhes": {
        "breakdown": [
            {"key": "rqe_especialista", "label": "RQE / Especialista", "weight": 20, "points": 20, "layer": "estruturado"},
        ]
    },
    "divida_ativa_qtd_inscricoes": 2,
    "divida_ativa_valor_total": 15000.0,
    "divida_ativa_ajuizado": True,
    "zona_franca": False,
    "rqe_confirmado": True,
    "rqe_fonte": "cnes",
    "site": "https://clinica.exemplo.com.br",
    "instagram": None,
    "google_rating": 4.7,
    "google_avaliacoes": 150,
}


def _insights_ia_json(**overrides) -> str:
    base = {
        "resumo_estrategico": "Lead forte, com RQE confirmado via CNES e boa presença digital.",
        "potencial_oportunidade": "alto",
        "recomendacao_abordagem": ["Mencionar a revisão financeira", "Elogiar o rating no Google"],
        "estrategia_comunicacao": "Tom consultivo, focado em oportunidade.",
        "cta_sugerido": "Podemos conversar 15 minutos essa semana?",
    }
    base.update(overrides)
    return json.dumps(base)


def test_gera_insights_com_sucesso():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        corpo = json.loads(request.content)
        assert corpo["model"] == ANTHROPIC_MODEL
        prompt = corpo["messages"][0]["content"]
        assert "Clínica Exemplo Ltda" in prompt
        assert "Dr. Fulano" in prompt
        assert "78.5/100" in prompt
        assert "RQE / Especialista: 20/20 pontos" in prompt
        assert "2 inscrição" in prompt
        assert "tom consultivo" in prompt
        return httpx.Response(200, json=_resposta_anthropic(_insights_ia_json()))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = gerar_insights_estrategicos(LEAD_INSIGHTS_COMPLETO, client=client)

    assert resultado["resumo_estrategico"] == "Lead forte, com RQE confirmado via CNES e boa presença digital."
    assert resultado["potencial_oportunidade"] == "alto"
    assert resultado["recomendacao_abordagem"] == ["Mencionar a revisão financeira", "Elogiar o rating no Google"]
    assert resultado["estrategia_comunicacao"] == "Tom consultivo, focado em oportunidade."
    assert resultado["cta_sugerido"] == "Podemos conversar 15 minutos essa semana?"


def test_gera_insights_normaliza_potencial_oportunidade():
    """Maiúscula/espaço na resposta da IA não deve vazar pro frontend --
    normalizado (strip + lowercase), mesmo se a IA responder "Alto "."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_resposta_anthropic(_insights_ia_json(potencial_oportunidade=" Médio ")))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = gerar_insights_estrategicos(LEAD_INSIGHTS_COMPLETO, client=client)

    assert resultado["potencial_oportunidade"] == "médio"


def test_gera_insights_limita_recomendacao_a_tres_itens():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_resposta_anthropic(_insights_ia_json(recomendacao_abordagem=[f"Recomendação {i}" for i in range(6)])),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = gerar_insights_estrategicos(LEAD_INSIGHTS_COMPLETO, client=client)

    assert len(resultado["recomendacao_abordagem"]) == 3


def test_gera_insights_resposta_nao_e_json_devolve_dict_vazio():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_resposta_anthropic("Desculpe, não consegui analisar esse lead."))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = gerar_insights_estrategicos(LEAD_INSIGHTS_COMPLETO, client=client)

    assert resultado == {}


def test_gera_insights_json_com_sintaxe_invalida_devolve_dict_vazio():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_resposta_anthropic('{"resumo_estrategico": [invalido]}'))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = gerar_insights_estrategicos(LEAD_INSIGHTS_COMPLETO, client=client)

    assert resultado == {}


def test_gera_insights_resposta_vazia_devolve_dict_vazio():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = gerar_insights_estrategicos(LEAD_INSIGHTS_COMPLETO, client=client)

    assert resultado == {}


def test_gera_insights_erro_http_nao_quebra():
    """Mesmo padrão defensivo de gerar_mensagem_abordagem -- erro HTTP
    NÃO propaga, devolve {} pra rota decidir o que fazer."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = gerar_insights_estrategicos(LEAD_INSIGHTS_COMPLETO, client=client)

    assert resultado == {}


def test_gera_insights_erro_de_rede_nao_quebra():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("conexão recusada", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = gerar_insights_estrategicos(LEAD_INSIGHTS_COMPLETO, client=client)

    assert resultado == {}


def test_gera_insights_com_lead_minimo_nao_quebra():
    """Lead só com razao_social (sem score, sem PGFN, sem presença
    digital) -- contexto reduzido, mas não deve lançar."""
    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content)["messages"][0]["content"]
        assert "nenhuma confirmada" in prompt  # presença digital vazia
        return httpx.Response(200, json=_resposta_anthropic(_insights_ia_json()))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resultado = gerar_insights_estrategicos({"razao_social": "Clínica Mínima"}, client=client)

    assert resultado["potencial_oportunidade"] == "alto"
