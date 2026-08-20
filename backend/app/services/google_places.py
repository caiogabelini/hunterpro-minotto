"""
Busca de estabelecimentos via Google Places API (New) — site, rating e
número de avaliações.
================================================================================

Endpoint: Text Search (New)
  POST https://places.googleapis.com/v1/places:searchText
  Headers: Content-Type: application/json,
           X-Goog-Api-Key: {GOOGLE_API_KEY},
           X-Goog-FieldMask: <campos pedidos, sem lista padrão>
  Body:    {"textQuery": "<nome> <UF>", "regionCode": "BR"}
  Resposta: {"places": [{"id", "displayName": {"text", "languageCode"},
             "formattedAddress", "websiteUri", "rating", "userRatingCount",
             "nationalPhoneNumber", ...}], "nextPageToken"}

⚠️ Contrato confirmado contra a documentação oficial do Google
(developers.google.com/maps/documentation/places/web-service/text-search
e .../place-details) em 20/08/2026 — **NÃO testado contra a API real**.
O sandbox desta sessão não permite chamada de rede autenticada (POST +
header customizado) por nenhuma ferramenta disponível: `curl` via Bash
está bloqueado pela política de permissão do ambiente, e o `WebFetch`
disponível só faz GET simples, sem headers customizados nem corpo POST.
A alternativa (API legada, que aceita a chave como query string — logo
testável via GET) foi descartada de propósito: o usuário optou
explicitamente por não expor a `GOOGLE_API_KEY` numa URL só pra
contornar essa limitação de ferramental, depois de eu ter vazado a
chave sem querer numa tentativa anterior nesta mesma sessão. Mesma
cautela já usada antes pro Firecrawl: implementado contra o contrato
documentado, não inventado, mas pendente de validação com uma chamada
real numa sessão/ambiente que permita.

⚠️ SÓ TEXT SEARCH, SEM PLACE DETAILS: a API (New) já devolve `websiteUri`,
`rating` e `userRatingCount` diretamente na resposta do Text Search,
desde que pedidos via `X-Goog-FieldMask` — não precisa de uma segunda
chamada a Place Details só pra isso. Reduz pela metade as chamadas (e o
custo — esses três campos são do tier "Enterprise" da API, o mais caro
por chamada).

O QUE ESTE MÓDULO FAZ:
  - `buscar_estabelecimento` faz o Text Search por nome (+ UF, se
    disponível, pra ajudar a desambiguar) e devolve o primeiro resultado
    (o Google já ordena por relevância), já normalizado.

O QUE ESTE MÓDULO **NÃO** FAZ (de propósito):
  - Não pagina nem tenta desambiguar entre múltiplos resultados — usa
    só o primeiro item de `places`. Se isso se mostrar impreciso na
    prática (nome comum, clínica errada encontrada), revisar com
    critério adicional (ex.: cruzar endereço/município do lead) numa
    sessão futura, quando houver como validar contra a API real.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import settings

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DEFAULT_TIMEOUT = 15.0

# Só pede os campos que a gente realmente usa -- a API (New) não tem
# lista padrão de campos, e cobra por tier de campo (estes são
# "Enterprise", os mais caros por chamada).
FIELD_MASK = (
    "places.id,places.displayName,places.websiteUri,"
    "places.rating,places.userRatingCount,places.nationalPhoneNumber"
)


@dataclass
class GooglePlaceResult:
    place_id: str
    nome: str
    site: str | None
    rating: float | None
    avaliacoes: int | None
    telefone: str | None


def _get_client(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    """Mesmo padrão de client injetável usado em app/services/cnes.py."""
    if client is not None:
        return client, False
    return httpx.Client(timeout=DEFAULT_TIMEOUT), True


def buscar_estabelecimento(
    nome: str,
    uf: str | None = None,
    client: httpx.Client | None = None,
) -> GooglePlaceResult | None:
    """
    Busca um estabelecimento por nome (+ UF, se disponível) via Text
    Search. Devolve o primeiro resultado (mais relevante segundo o
    ranking do próprio Google), ou None se a busca não achar nada.
    """
    client, owns_client = _get_client(client)
    texto_busca = f"{nome} {uf}" if uf else nome

    try:
        resp = client.post(
            TEXT_SEARCH_URL,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": settings.GOOGLE_API_KEY,
                "X-Goog-FieldMask": FIELD_MASK,
            },
            json={"textQuery": texto_busca, "regionCode": "BR"},
        )
        resp.raise_for_status()
        places = resp.json().get("places") or []
        if not places:
            return None

        primeiro = places[0]
        return GooglePlaceResult(
            place_id=primeiro.get("id", ""),
            nome=(primeiro.get("displayName") or {}).get("text", ""),
            site=primeiro.get("websiteUri"),
            rating=primeiro.get("rating"),
            avaliacoes=primeiro.get("userRatingCount"),
            telefone=primeiro.get("nationalPhoneNumber"),
        )
    finally:
        if owns_client:
            client.close()
