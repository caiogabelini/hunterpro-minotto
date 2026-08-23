"""
Varredura de site via Firecrawl — extrai o conteúdo em markdown/texto
limpo, e localiza o link do Instagram por regex (sem precisar de IA pra
essa parte).
================================================================================

Fonte: Firecrawl (firecrawl.dev) — serviço de scraping que converte
páginas em markdown "pronto pra LLM", lidando com JS renderizado, proxies
etc. Mesmo serviço já usado no projeto SR Contábil pra essa etapa
(varredura de site + inferência via IA).

⚠️ ENDPOINT E SCHEMA CONFIRMADOS CONTRA O OPENAPI.JSON OFICIAL DO
FIRECRAWL (github.com/firecrawl/firecrawl/blob/main/apps/api/openapi.json,
verificado em 19/08/2026 e novamente em 20/08/2026) — implementado
contra a documentação oficial, mas **ainda NÃO testado contra a API
real**, apesar de a `FIRECRAWL_API_KEY` já estar preenchida no `.env`
desde 20/08/2026: o sandbox desta sessão não permite chamada de rede
autenticada (POST + header customizado) por nenhuma ferramenta
disponível (`curl` via Bash bloqueado pela política do ambiente,
`WebFetch` só faz GET simples sem headers customizados). O usuário
optou explicitamente por não contornar essa limitação (a única forma
factível teria exposto a chave num lugar visível, o que já aconteceu
sem querer com a GOOGLE_API_KEY nesta mesma sessão — ver
app/services/google_places.py). Pendente de validação com uma chamada
real numa sessão/ambiente que permita, não por falta de tentativa.

  POST https://api.firecrawl.dev/v1/scrape
  Header: Authorization: Bearer {FIRECRAWL_API_KEY}
  Body:   {"url": "<url>", "formats": ["markdown"]}
  Resposta (sucesso): {"success": true, "data": {"markdown": "...", "metadata": {...}}}
  Resposta (falha reportada pelo Firecrawl, não erro HTTP): {"success": false, ...}

⚠️ v1 vs v2: checado de novo em 20/08/2026, como pedido — o repositório
oficial (github.com/firecrawl/firecrawl) só tem `v1-openapi.json` e
`openapi.json` (este último também v1.0.0, `servers` apontando pra
`/v1`); não existe nenhum `v2-openapi.json` commitado no repo, apesar de
a documentação renderizada do site promover a v2 como atual. Decisão:
continuar na v1 — é a única versão com contrato verificável a partir de
uma fonte primária (o spec bruto do próprio repositório), não só da SPA
de documentação (que não expõe exemplos por scraping simples, mesmo
problema do Swagger do CNES). Migrar pra v2 sem um spec confiável pra
conferir seria inventar schema, o que este projeto evita de propósito.
Reconsiderar quando houver um jeito de verificar a v2 com a mesma
confiança (spec bruto publicado, ou uma chamada real de teste).

O QUE ESTE MÓDULO FAZ:
  - `scrape_site` busca o markdown de uma URL via Firecrawl
  - `extrair_instagram` acha o link de perfil do Instagram no
    texto/HTML por regex — não precisa de IA pra isso, é busca de padrão
    simples. Ignora paths reservados do Instagram (p, reel, reels,
    stories, explore, accounts, share), que são rotas do site, não
    perfis de usuário.
  - `extrair_whatsapp` acha o número de WhatsApp divulgado no site
    (links `wa.me/...` e `api.whatsapp.com/send?phone=...`), pela mesma
    lógica de regex. Adicionado em 23/08/2026 pra resolver um problema
    concreto de dado — ver a ressalva abaixo.

⚠️ POR QUE `extrair_whatsapp` EXISTE (23/08/2026):
Na primeira busca real, os 6 leads voltaram com "WhatsApp não confirmado".
Não era bug: o schema da Evolution batia, a instância respondia, e o
`exists: false` era a resposta correta — porque os 6 telefones vinham do
Google Places, que devolve o telefone principal publicado, e em
clínica/hospital isso é a **central telefônica**. Todos os 6 eram números
FIXOS (8 dígitos locais começando com 2 ou 3); celular brasileiro tem 9
dígitos começando com 9. Fixo raramente tem WhatsApp.

O número que a clínica realmente atende no WhatsApp costuma estar no
próprio site, num botão flutuante ou no rodapé. Como o scrape do
Firecrawl JÁ acontece pra inferir RQE/serviços, extrair isso do mesmo
markdown **não custa nenhuma chamada adicional** — mesma economia que já
justificou o `extrair_instagram` não virar uma etapa própria.

O QUE ESTE MÓDULO **NÃO** FAZ (de propósito):
  - Não interpreta o conteúdo do site (serviços oferecidos, menção a
    RQE) — isso é `app/services/ai_enrichment.py`, que consome o
    markdown devolvido aqui.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from app.core.config import settings

FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v1/scrape"
DEFAULT_TIMEOUT = 30.0  # scraping pode demorar mais que uma consulta de API comum

# Paths do Instagram que são rotas do site, não perfis de usuário -- ver
# extrair_instagram.
INSTAGRAM_PATHS_RESERVADOS = {"p", "reel", "reels", "stories", "explore", "accounts", "share"}

INSTAGRAM_REGEX = re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})", re.IGNORECASE)

# Os dois formatos de link de WhatsApp que aparecem em site brasileiro --
# ver `extrair_whatsapp`. Ambos toleram separadores no meio dos dígitos
# (`wa.me/55 11 99999-9999` acontece, ainda que fora do padrão) porque a
# limpeza fica com `formatar_numero_whatsapp`.
#
# ⚠️ `wa.me` também serve pra links de convite de grupo
# (`wa.me/<letras e números>`), por isso o padrão exige DÍGITO logo após
# a barra -- um código de convite alfanumérico não casa.
WHATSAPP_REGEXES = (
    re.compile(r"wa\.me/(\+?[\d][\d\s().-]{9,19})", re.IGNORECASE),
    re.compile(r"api\.whatsapp\.com/send\?[^\s\"'<>]*?phone=(\+?[\d][\d\s().-]{9,19})", re.IGNORECASE),
    re.compile(r"web\.whatsapp\.com/send\?[^\s\"'<>]*?phone=(\+?[\d][\d\s().-]{9,19})", re.IGNORECASE),
)


@dataclass
class SiteScrapeResult:
    url: str
    sucesso: bool
    markdown: str


def _get_client(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    """Mesmo padrão de client injetável usado em app/services/cnes.py."""
    if client is not None:
        return client, False
    return httpx.Client(timeout=DEFAULT_TIMEOUT), True


def scrape_site(url: str, client: httpx.Client | None = None) -> SiteScrapeResult:
    """
    Busca o conteúdo de uma URL em markdown limpo via Firecrawl.

    Devolve `sucesso=False` (markdown vazio) quando o Firecrawl reporta
    falha no campo "success" da resposta (ex.: site fora do ar,
    bloqueado por robots.txt) — isso não é um erro HTTP, é uma resposta
    200 válida com `success: false`. Erro HTTP de verdade (4xx/5xx da
    própria API do Firecrawl) ainda propaga via `raise_for_status`.
    """
    client, owns_client = _get_client(client)

    try:
        resp = client.post(
            FIRECRAWL_SCRAPE_URL,
            headers={"Authorization": f"Bearer {settings.FIRECRAWL_API_KEY}"},
            json={"url": url, "formats": ["markdown"]},
        )
        resp.raise_for_status()
        body = resp.json()

        if not body.get("success"):
            return SiteScrapeResult(url=url, sucesso=False, markdown="")

        markdown = (body.get("data") or {}).get("markdown") or ""
        return SiteScrapeResult(url=url, sucesso=True, markdown=markdown)
    finally:
        if owns_client:
            client.close()


def extrair_instagram(texto: str) -> str | None:
    """
    Acha o primeiro link de perfil do Instagram no texto/HTML do site,
    ignorando paths reservados (posts, reels, stories etc, que não são
    perfis). Devolve None se não achar nenhum link de perfil.
    """
    for match in INSTAGRAM_REGEX.finditer(texto or ""):
        candidato = match.group(1).rstrip(".")
        if candidato.lower() not in INSTAGRAM_PATHS_RESERVADOS:
            return candidato

    return None


def extrair_whatsapp(texto: str) -> str | None:
    """
    Acha o primeiro número de WhatsApp divulgado no texto/HTML do site,
    nos formatos `wa.me/NUMERO`, `api.whatsapp.com/send?phone=NUMERO` e
    `web.whatsapp.com/send?phone=NUMERO`.

    Devolve o número JÁ NORMALIZADO por
    `app.services.whatsapp.formatar_numero_whatsapp` (só dígitos, com
    código do país) -- ou seja, no mesmo formato que a Evolution API
    espera. Um link com número implausível (curto demais, código de
    convite de grupo, telefone estrangeiro fora do padrão de 12-13
    dígitos) é DESCARTADO e a busca continua nos links seguintes, em vez
    de devolver lixo pra etapa de validação.

    `None` quando não há nenhum link utilizável -- mesmo contrato de
    `extrair_instagram`, e o chamador (`enrich_site_firecrawl`) não
    precisa tratar caso especial.

    Import local de `formatar_numero_whatsapp` de propósito: evita ciclo
    entre `site_scraping` e `whatsapp`, e mantém a normalização com uma
    dona só -- reimplementar a heurística de código do país aqui seria
    exatamente o risco de transcrição silenciosa que este projeto já
    documentou ao espelhar essa função no frontend.
    """
    from app.services.whatsapp import formatar_numero_whatsapp

    for regex in WHATSAPP_REGEXES:
        for match in regex.finditer(texto or ""):
            numero = formatar_numero_whatsapp(match.group(1))
            if numero:
                return numero

    return None
