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

⚠️ Ainda **NÃO testado contra a API real**, apesar de a
`ANTHROPIC_API_KEY` já estar preenchida no `.env` desde 20/08/2026 — o
contrato acima veio de fonte oficial (não foi inventado), mas o sandbox
desta sessão não permite chamada de rede autenticada (POST + header
customizado) por nenhuma ferramenta disponível (`curl` bloqueado,
`WebFetch` só faz GET simples). Mesma ressalva documentada em
app/services/site_scraping.py e app/services/google_places.py — o
usuário optou explicitamente por não contornar essa limitação nesta
sessão.

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
    critério `rqe_especialista` é feita em
    app/scoring/compute_lead_score.py::rqe_especialista_points, que
    cruza esse sinal com o do CNES (app/services/cnes.py reforça, não
    substitui, esse mesmo sinal).
  - Não trunca de forma inteligente — só corta o texto do site em
    `MAX_CARACTERES_TEXTO_SITE` caracteres antes de mandar pro modelo,
    pra não estourar o prompt com uma página gigante. Sites reais raramente
    precisam de mais que isso pra esse tipo de classificação.

`gerar_mensagem_abordagem` (adicionada 20/08/2026, expandida pra
multi-canal no mesmo dia numa sessão seguinte) gera as mensagens
exibidas na seção "Abordagem sugerida" do dossiê do lead — hoje
persistidas em `LeadMessage` (app/models/lead_message.py, uma linha por
geração, histórico completo por canal), não mais em
`Lead.mensagem_abordagem` (deprecated, ver app/models/lead.py). Rota:
`POST /api/leads/{lead_id}/gerar-abordagem/{canal}` em
app/api/routes/leads.py. Mesmo modelo/client injetável de
`inferir_dados_site`, mas **defensiva de um jeito que `inferir_dados_site`
não é**: essa função nunca lança em erro HTTP/rede (diferente de
`inferir_dados_site`, que deixa `raise_for_status()` propagar — ver
`test_erro_http_propaga_excecao` em test_ai_enrichment.py). Erro de
rede/HTTP e resposta vazia/malformada viram `MensagemGerada(conteudo="",
assunto=None)`; é a rota que decide o que fazer com um `conteudo` vazio
(não persiste, devolve 502 pro frontend mostrar erro amigável — ver
"Suggested approach message generation" no CLAUDE.md). Essa chamada
específica, diferente das outras integrações deste módulo, **pode ser
validada de verdade pelo usuário final através da própria tela do
dossiê** (os botões "Gerar mensagem com IA" por canal), já que ele
acessa o sistema com a `ANTHROPIC_API_KEY` real configurada no ambiente
de produção — não fica pendente de validação como `inferir_dados_site`.
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


def _get_client(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    if client is not None:
        return client, False
    return httpx.Client(timeout=DEFAULT_TIMEOUT), True


def _extrair_json(texto: str) -> dict | None:
    """
    Tenta parsear `texto` como um objeto JSON. A IA pode variar o formato
    mesmo instruída a responder só em JSON — às vezes embrulha em bloco
    de código markdown (```json ... ```), às vezes acrescenta uma frase
    antes/depois do objeto. Tenta essas variações antes de desistir.
    Devolve None se nenhuma delas funcionar (nunca lança exceção).

    Compartilhada por `inferir_dados_site` (mais abaixo) e
    `gerar_mensagem_abordagem` (canal "email", que pede assunto+corpo em
    JSON) — mesma tolerância a formato inconsistente da IA nos dois usos.
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


_REGRAS_DE_TOM = """Regras de tom, siga à risca:
- Se houver dívida ativa na PGFN listada abaixo, mencione de forma \
consultiva, como uma oportunidade de ajuda -- NUNCA como cobrança ou \
acusação. Exemplo do tom certo: "identificamos uma oportunidade de \
revisão financeira que pode beneficiar sua empresa". Exemplo do tom \
ERRADO, nunca use: "sua empresa está endividada" ou "vocês devem para a \
União".
- Pode citar RQE confirmado ou um bom rating no Google como reconhecimento \
positivo, se estiverem na lista.
- NÃO invente nenhum dado que não esteja explicitamente na lista abaixo."""

PROMPT_ABORDAGEM_EMAIL_TEMPLATE = """Você é um redator de prospecção B2B pra uma empresa de contabilidade \
(Minotto Contabilidade) que atende clínicas e consultórios médicos no Brasil.

Escreva um E-MAIL de PRIMEIRO CONTATO -- tom profissional, cordial e \
consultivo, corpo com 3 a 4 frases. A mensagem deve soar genuinamente \
personalizada (mencione pelo menos um dado real da lista abaixo) e \
terminar convidando a uma conversa breve.

{regras_de_tom}

Dados do lead:
{{dados_lead}}

Responda APENAS com um objeto JSON válido, sem texto antes ou depois, \
sem bloco de código markdown, no formato exato abaixo:

{{{{
  "assunto": "assunto curto e direto do e-mail, sem saudação, até 60 caracteres",
  "corpo": "o corpo do e-mail em si -- sem saudação tipo \\"Prezado(a)\\", \
sem assinatura, sem aspas ao redor do texto"
}}}}
""".format(regras_de_tom=_REGRAS_DE_TOM)

PROMPT_ABORDAGEM_WHATSAPP_TEMPLATE = """Você é um redator de prospecção B2B pra uma empresa de contabilidade \
(Minotto Contabilidade) que atende clínicas e consultórios médicos no Brasil.

Escreva uma mensagem de WHATSAPP de PRIMEIRO CONTATO -- tom direto e \
casual (mas sempre respeitoso), só 1 a 2 frases curtas. A mensagem deve \
soar genuinamente personalizada (mencione pelo menos um dado real da \
lista abaixo) e terminar convidando a uma conversa breve.

{regras_de_tom}

Dados do lead:
{{dados_lead}}

Responda APENAS com o texto da mensagem em si -- sem saudação tipo \
"Prezado(a)", sem assinatura, sem explicações antes ou depois, sem aspas \
ao redor do texto, no máximo 1 emoji e só se fizer sentido.
""".format(regras_de_tom=_REGRAS_DE_TOM)


def _montar_contexto_lead(lead: dict) -> str:
    """
    Monta a lista de sinais reais do lead que entra no prompt --
    só inclui uma linha quando o dado existe, pra não dar munição pra IA
    "preencher lacuna" inventando algo. `lead` é um dict simples (não o
    ORM `Lead` direto) montado pela rota, mesmo padrão de
    `montar_score_lead` em app/scoring/compute_lead_score.py aceitar um
    dict já extraído em vez de um objeto de banco.
    """
    linhas = [f"- Razão social: {lead.get('razao_social') or 'não informado'}"]

    if lead.get("decisor_nome"):
        linhas.append(f"- Nome do decisor/responsável: {lead['decisor_nome']}")

    if lead.get("municipio"):
        uf = lead.get("uf") or ""
        linhas.append(f"- Localização: {lead['municipio']}/{uf}")

    qtd_pgfn = lead.get("divida_ativa_qtd_inscricoes")
    if qtd_pgfn:
        linhas.append(
            f"- Possui {qtd_pgfn} inscrição(ões) ativa(s) na dívida ativa da União (PGFN) "
            "-- mencionar com tom consultivo, como oportunidade de revisão financeira, "
            "nunca como cobrança ou acusação"
        )

    if lead.get("rqe_confirmado"):
        linhas.append("- RQE (Registro de Qualificação de Especialista) confirmado")

    if lead.get("google_rating"):
        avaliacoes = lead.get("google_avaliacoes") or 0
        linhas.append(f"- Rating no Google: {lead['google_rating']} ({avaliacoes} avaliações)")

    if lead.get("situacao_cadastral"):
        linhas.append(f"- Situação cadastral na Receita Federal: {lead['situacao_cadastral']}")

    return "\n".join(linhas)


@dataclass
class MensagemGerada:
    """Resultado de `gerar_mensagem_abordagem`. `assunto` só vem
    preenchido pro canal "email" -- WhatsApp não tem conceito de
    assunto, fica sempre `None` nesse canal."""

    conteudo: str
    assunto: str | None = None


CANAIS_VALIDOS = ("email", "whatsapp")


def gerar_mensagem_abordagem(lead: dict, canal: str, client: httpx.Client | None = None) -> MensagemGerada:
    """
    Gera uma mensagem de primeiro contato personalizada com dados reais
    do lead, com tom/tamanho ajustados pelo `canal`:
      - "email": mais formal, 3-4 frases, também gera `assunto` (a IA
        responde em JSON — `{"assunto": ..., "corpo": ...}` — parseado
        com o mesmo `_extrair_json` tolerante a formato usado por
        `inferir_dados_site`)
      - "whatsapp": mais direto/casual, 1-2 frases, sem assunto (resposta
        em texto puro, sem JSON)
    As duas variantes reforçam a mesma regra de tom consultivo pra
    dívida ativa PGFN (`_REGRAS_DE_TOM`) — ver os templates acima.

    Levanta `ValueError` pra um `canal` fora de `CANAIS_VALIDOS` — erro
    de programação (a rota já valida contra `CanalMensagem` antes de
    chamar; ver app/models/lead_message.py), não uma resposta da IA.

    Nunca lança por causa da chamada à API em si — diferente de
    `inferir_dados_site` (que deixa erro HTTP propagar via
    `raise_for_status()`), esta função captura qualquer `httpx.HTTPError`
    (erro de rede OU status HTTP de erro) e resposta vazia/malformada,
    devolvendo `MensagemGerada(conteudo="", assunto=None)` em qualquer
    um desses casos. É a rota (`POST
    /api/leads/{lead_id}/gerar-abordagem/{canal}` em
    app/api/routes/leads.py) que decide o que fazer com um `conteudo`
    vazio — não persiste em `LeadMessage`, devolve 502 pro frontend
    mostrar um erro amigável.
    """
    if canal not in CANAIS_VALIDOS:
        raise ValueError(f"Canal desconhecido: {canal!r}. Use um de {CANAIS_VALIDOS}.")

    client, owns_client = _get_client(client)

    try:
        dados_lead = _montar_contexto_lead(lead)
        template = PROMPT_ABORDAGEM_EMAIL_TEMPLATE if canal == "email" else PROMPT_ABORDAGEM_WHATSAPP_TEMPLATE
        prompt = template.format(dados_lead=dados_lead)

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

        if canal == "whatsapp":
            return MensagemGerada(conteudo=texto_resposta.strip(), assunto=None)

        dados = _extrair_json(texto_resposta)
        if dados is None:
            return MensagemGerada(conteudo="", assunto=None)

        corpo = str(dados.get("corpo") or "").strip()
        assunto_bruto = dados.get("assunto")
        assunto = str(assunto_bruto).strip() or None if assunto_bruto else None
        return MensagemGerada(conteudo=corpo, assunto=assunto)
    except httpx.HTTPError:
        return MensagemGerada(conteudo="", assunto=None)
    finally:
        if owns_client:
            client.close()


@dataclass
class InferenciaSiteResult:
    rqe_mencionado: bool
    rqe_numero: str | None
    servicos: list[str]


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
