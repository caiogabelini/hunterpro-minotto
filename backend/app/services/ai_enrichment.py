"""
Inferência sobre o conteúdo de um site via Claude — menção a RQE e
serviços/especialidades oferecidos.
================================================================================

Usa a Messages API da Anthropic direto por HTTP (não o SDK oficial
`anthropic`), pra manter o mesmo padrão do resto deste projeto: client
`httpx` injetável, testado com `httpx.MockTransport`, sem framework por
cima. Contrato confirmado contra a documentação oficial (raw HTTP —
docs.claude.com, seção cURL/raw HTTP):

  POST https://api.anthropic.com/v1/messages
  Headers: x-api-key: {ANTHROPIC_API_KEY}, anthropic-version: 2023-06-01
  Body:   {"model": ..., "max_tokens": ..., "messages": [{"role": "user", "content": "..."}]}
  Resposta: {"content": [{"type": "text", "text": "..."}], ...}

Modelo: `claude-haiku-4-5-20251001` — o mais barato da linha atual,
adequado pra esse tipo de classificação/extração de texto curto (não
precisa de raciocínio complexo).

O QUE ESTE MÓDULO FAZ:
  - `inferir_dados_site` manda o texto do site (já extraído por
    app/services/site_scraping.py) num prompt pedindo resposta em JSON:
    `rqe_mencionado` (bool), `rqe_numero` (string ou null) e `servicos`
    (lista de até 8 strings)
  - Trata resposta que não vem em JSON válido sem quebrar: a IA pode
    variar o formato mesmo instruída a responder só em JSON (embrulhar
    em bloco de código markdown, acrescentar texto antes/depois) —
    `_extrair_json` tenta algumas variações antes de desistir; se nenhuma
    funcionar, `inferir_dados_site` devolve valores vazios/null em vez de
    lançar exceção

O QUE ESTE MÓDULO **NÃO** FAZ (de propósito):
  - Não calcula pontos de score — devolve o sinal bruto; a conversão pro
    critério `rqe_especialista` (rules.py) fica pro `compute_lead_score`,
    ainda não implementado. Ver também app/services/cnes.py, que reforça
    (não substitui) esse mesmo sinal.
  - Não trunca de forma inteligente — só corta o texto do site em
    `MAX_CARACTERES_TEXTO_SITE` caracteres antes de mandar pro modelo,
    pra não estourar o prompt com uma página gigante. Sites reais raramente
    precisam de mais que isso pra esse tipo de classificação.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

import httpx

from app.core.config import settings

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_TIMEOUT = 30.0
MAX_TOKENS_RESPOSTA = 1024

# Corta o texto do site antes de montar o prompt -- página real raramente
# precisa de mais que isso pra esse tipo de classificação/extração curta.
MAX_CARACTERES_TEXTO_SITE = 12000

PROMPT_TEMPLATE = """Você vai analisar o conteúdo (em markdown) do site de uma clínica ou \
consultório médico no Brasil. Responda APENAS com um objeto JSON válido, \
sem nenhum texto antes ou depois, sem bloco de código markdown, no \
formato exato abaixo:

{{
  "rqe_mencionado": true ou false -- se o texto menciona "RQE" (Registro \
de Qualificação de Especialista) em algum lugar,
  "rqe_numero": "o número do RQE encontrado, como string" ou null se não \
encontrar nenhum número específico,
  "servicos": ["lista de até 8 especialidades/serviços médicos oferecidos, \
como strings curtas, ex.: \\"Cardiologia\\", \\"Consulta de rotina\\""]
}}

Conteúdo do site:
---
{texto_site}
---
"""


@dataclass
class InferenciaSiteResult:
    rqe_mencionado: bool
    rqe_numero: str | None
    servicos: list[str]


def _extrair_json(texto: str) -> dict | None:
    """
    Tenta parsear `texto` como um objeto JSON. A IA pode variar o formato
    mesmo instruída a responder só em JSON — às vezes embrulha em bloco
    de código markdown (```json ... ```), às vezes acrescenta uma frase
    antes/depois do objeto. Tenta essas variações antes de desistir.
    Devolve None se nenhuma delas funcionar (nunca lança exceção).
    """
    candidatos = [texto.strip()]

    bloco_codigo = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", texto, re.DOTALL)
    if bloco_codigo:
        candidatos.append(bloco_codigo.group(1))

    primeiro_objeto = re.search(r"\{.*\}", texto, re.DOTALL)
    if primeiro_objeto:
        candidatos.append(primeiro_objeto.group(0))

    for candidato in candidatos:
        try:
            resultado = json.loads(candidato)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(resultado, dict):
            return resultado

    return None


def _get_client(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    if client is not None:
        return client, False
    return httpx.Client(timeout=DEFAULT_TIMEOUT), True


def inferir_dados_site(texto_site: str, client: httpx.Client | None = None) -> InferenciaSiteResult:
    """
    Manda o texto do site pro Claude Haiku e devolve rqe_mencionado,
    rqe_numero e servicos já parseados. Se a resposta não vier em JSON
    válido (de nenhuma das variações tentadas por `_extrair_json`),
    devolve valores vazios/null em vez de lançar exceção.
    """
    client, owns_client = _get_client(client)

    try:
        prompt = PROMPT_TEMPLATE.format(texto_site=texto_site[:MAX_CARACTERES_TEXTO_SITE])

        resp = client.post(
            ANTHROPIC_MESSAGES_URL,
            headers={
                "x-api-key": settings.ANTHROPIC_API_KEY,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": MAX_TOKENS_RESPOSTA,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()

        blocos = resp.json().get("content") or []
        texto_resposta = next((b.get("text", "") for b in blocos if b.get("type") == "text"), "")

        dados = _extrair_json(texto_resposta)
        if dados is None:
            return InferenciaSiteResult(rqe_mencionado=False, rqe_numero=None, servicos=[])

        servicos_bruto = dados.get("servicos")
        servicos = [str(s) for s in servicos_bruto][:8] if isinstance(servicos_bruto, list) else []

        return InferenciaSiteResult(
            rqe_mencionado=bool(dados.get("rqe_mencionado")),
            rqe_numero=dados.get("rqe_numero") or None,
            servicos=servicos,
        )
    finally:
        if owns_client:
            client.close()
