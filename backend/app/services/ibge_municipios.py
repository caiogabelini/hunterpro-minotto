"""
Resolução de município + UF -> código IBGE, e conversão pro formato usado
pela API do CNES.
================================================================================

Fonte: API pública de Localidades do IBGE
(servicodados.ibge.gov.br/api/v1/localidades) — oficial, gratuita, sem
autenticação.

⚠️ POR QUE API AO VIVO E NÃO UMA TABELA ESTÁTICA (diferente da Zona
Franca): o Brasil tem ~5.570 municípios. A lista da Zona Franca
(app/scoring/zona_franca.py) tem 10 entradas, verificáveis uma a uma
contra a página oficial da Suframa. Uma tabela de 5.570 códigos digitada/
copiada de memória tem risco real de erro silencioso — um dígito trocado
não dá erro nenhum, só faz o cruzamento com o CNES falhar pro município
errado, sem ninguém perceber. Por isso este módulo consulta a API oficial
do IBGE ao vivo (mesmo padrão do CNES: fonte pública por HTTP, não um
arquivo baixado manualmente como a PGFN).

CONFIRMADO por teste manual em 19/08/2026: o `codigo_municipio` usado pela
API do CNES (ver app/services/cnes.py) é o código IBGE de 7 dígitos SEM o
dígito verificador final -- ex.: São Paulo é 3550308 no IBGE e 355030 no
CNES. Testado batendo direto no endpoint do CNES com o valor truncado e
recebendo estabelecimentos de São Paulo de volta. `codigo_ibge_para_cnes`
faz essa conversão.

O QUE ESTE MÓDULO **NÃO** FAZ (de propósito):
  - Não cacheia nada entre chamadas -- cada resolução bate na API do IBGE.
    Como cada chamada busca todos os municípios de uma UF de uma vez (não
    um por um), o custo já é baixo (só 27 UFs possíveis), mas cachear por
    UF (ex.: em Redis, mesmo padrão da PGFN) é uma otimização óbvia pra
    quando o volume de leads justificar.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

import httpx

from app.core.config import settings

MUNICIPIOS_POR_UF_PATH = "/api/v1/localidades/estados/{uf}/municipios"
DEFAULT_TIMEOUT = 15.0


@dataclass
class MunicipioIbge:
    codigo_ibge: int
    nome: str


def _normalize(text: str) -> str:
    """Remove acentos e normaliza pra comparação -- mesmo espírito do
    `_normalize` em app/scoring/zona_franca.py."""
    sem_acento = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return sem_acento.strip().upper()


def _get_client(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    """Mesmo padrão de client injetável usado em app/services/cnes.py."""
    if client is not None:
        return client, False
    return httpx.Client(base_url=settings.IBGE_API_BASE_URL, timeout=DEFAULT_TIMEOUT), True


def fetch_municipios_por_uf(uf: str, client: httpx.Client | None = None) -> list[MunicipioIbge]:
    """Busca todos os municípios de uma UF (sigla de 2 letras) na API do IBGE."""
    client, owns_client = _get_client(client)

    try:
        resp = client.get(MUNICIPIOS_POR_UF_PATH.format(uf=uf.upper()))
        resp.raise_for_status()
        return [
            MunicipioIbge(codigo_ibge=item["id"], nome=item["nome"])
            for item in resp.json()
        ]
    finally:
        if owns_client:
            client.close()


def resolve_codigo_municipio_ibge(
    municipio: str, uf: str, client: httpx.Client | None = None
) -> int | None:
    """
    Resolve o nome do município (comparação sem acento/case, mesmo padrão
    de is_zona_franca) pro código IBGE de 7 dígitos. Retorna None se o
    nome não bater com nenhum município da UF informada.
    """
    alvo = _normalize(municipio)
    municipios = fetch_municipios_por_uf(uf, client=client)
    return next((m.codigo_ibge for m in municipios if _normalize(m.nome) == alvo), None)


def codigo_ibge_para_cnes(codigo_municipio_ibge: int) -> int:
    """Remove o dígito verificador final do código IBGE (7 dígitos) pra
    chegar no formato de 6 dígitos que a API do CNES espera -- ver nota
    no topo do módulo sobre a confirmação empírica dessa conversão."""
    return codigo_municipio_ibge // 10
