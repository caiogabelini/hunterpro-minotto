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
