"""
Integração com a API pública do CNES (Cadastro Nacional de Estabelecimentos
de Saúde) — apidadosabertos.saude.gov.br.
================================================================================

O QUE ESTE MÓDULO FAZ:
  - Busca, via HTTP, os estabelecimentos de saúde cadastrados no CNES para
    um município (código IBGE de 6 dígitos, sem dígito verificador — mesmo
    padrão usado em outras bases do DATASUS)
  - Pagina automaticamente até esgotar os resultados
  - Cruza o CNPJ do lead contra a lista de estabelecimentos do município,
    pra confirmar se aquele CNPJ tem unidade registrada no CNES

⚠️ LIMITAÇÕES DA API, CONFIRMADAS POR TESTE MANUAL EM 19/08/2026 (a
documentação em apidadosabertos.saude.gov.br/v1/ é uma SPA que não expõe o
spec facilmente por scraping — validado batendo direto no endpoint):
  - NÃO existe filtro por CNPJ (`numero_cnpj`/`cnpj` como query param são
    ignorados pelo servidor, que devolve resultados não filtrados). Por
    isso o cruzamento por CNPJ é feito no lado do cliente, em
    `find_estabelecimento_by_cnpj`, depois de buscar todos os
    estabelecimentos do município.
  - `codigo_municipio` (código IBGE) É respeitado como filtro.
  - Mesmo pedindo `limit` alto (testado com 500), o servidor sempre limita
    o tamanho da página a um valor bem menor (observado: 20). O loop de
    paginação usa o tamanho real devolvido a cada página, não assume um
    valor fixo.
  - A resposta do estabelecimento NÃO tem nenhum campo de especialidade
    médica/RQE — só dados cadastrais/administrativos (endereço, tipo de
    unidade, gestão, etc.). Por isso este módulo só confirma "esse CNPJ
    tem unidade de saúde registrada no CNES", não a especialidade — o
    critério `rqe_especialista` (app/scoring/rules.py) continua dependendo
    de inferência via IA, com o CNES como reforço estrutural do sinal, não
    como confirmação isolada.

O QUE ESTE MÓDULO **NÃO** FAZ (de propósito):
  - Não resolve nome de município -> código IBGE. `codigo_municipio` tem
    que vir de uma etapa anterior do pipeline (ex.: enriquecimento via
    Receita Federal, se a fonte usada devolver o código, ou uma tabela de
    municípios ainda não implementada neste projeto).
  - Não converte a presença no CNES em pontos de score — isso é feito em
    `compute_lead_score` (app/workers/celery_app.py), ainda não
    implementado, junto com os outros critérios que faltam.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.services.pgfn import normalize_cnpj

ESTABELECIMENTOS_PATH = "/cnes/estabelecimentos"

DEFAULT_TIMEOUT = 15.0

# Trava de segurança contra loop infinito, caso a API mude de comportamento
# e pare de devolver página vazia como sinal de fim. Nenhum município real
# tem estabelecimentos suficientes pra chegar perto disso com página de ~20.
MAX_PAGINAS = 500


@dataclass
class CnesEstabelecimento:
    """Um estabelecimento de saúde, já normalizado a partir da resposta da API."""

    codigo_cnes: str
    nome_fantasia: str
    nome_razao_social: str
    numero_cnpj: str | None
    codigo_tipo_unidade: int | None
    codigo_municipio: int | None
    codigo_uf: int | None
    data_atualizacao: str | None


def _parse_estabelecimento(raw: dict) -> CnesEstabelecimento:
    return CnesEstabelecimento(
        codigo_cnes=str(raw.get("codigo_cnes") or ""),
        nome_fantasia=raw.get("nome_fantasia") or "",
        nome_razao_social=raw.get("nome_razao_social") or "",
        numero_cnpj=normalize_cnpj(raw.get("numero_cnpj")),
        codigo_tipo_unidade=raw.get("codigo_tipo_unidade"),
        codigo_municipio=raw.get("codigo_municipio"),
        codigo_uf=raw.get("codigo_uf"),
        data_atualizacao=raw.get("data_atualizacao"),
    )


def _get_client(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    """Devolve (client, owns_client). Cria um client novo se nenhum foi
    injetado — mesmo padrão do `redis_client` em app/services/pgfn.py, pra
    manter as funções testáveis sem bater na API real."""
    if client is not None:
        return client, False
    return httpx.Client(base_url=settings.CNES_API_BASE_URL, timeout=DEFAULT_TIMEOUT), True


def fetch_estabelecimentos_por_municipio(
    codigo_municipio: int | str,
    client: httpx.Client | None = None,
) -> list[CnesEstabelecimento]:
    """
    Busca todos os estabelecimentos de saúde cadastrados no CNES para um
    município, paginando automaticamente até a API devolver uma página
    vazia (ver limitações no topo do módulo — a página real é menor que o
    `limit` pedido, então o avanço usa o tamanho devolvido, não um fixo).
    """
    client, owns_client = _get_client(client)

    try:
        estabelecimentos: list[CnesEstabelecimento] = []
        offset = 0

        for _ in range(MAX_PAGINAS):
            resp = client.get(
                ESTABELECIMENTOS_PATH,
                params={"codigo_municipio": codigo_municipio, "limit": 20, "offset": offset},
            )
            resp.raise_for_status()
            pagina = resp.json().get("estabelecimentos", [])
            if not pagina:
                break

            estabelecimentos.extend(_parse_estabelecimento(item) for item in pagina)
            offset += len(pagina)

        return estabelecimentos
    finally:
        if owns_client:
            client.close()


def find_estabelecimento_by_cnpj(
    estabelecimentos: list[CnesEstabelecimento], cnpj: str
) -> CnesEstabelecimento | None:
    """
    Cruzamento local por CNPJ contra uma lista já buscada (ver nota no
    topo do módulo: a API não filtra por CNPJ do lado do servidor).
    """
    normalized = normalize_cnpj(cnpj)
    if normalized is None:
        return None
    return next((e for e in estabelecimentos if e.numero_cnpj == normalized), None)


def get_estabelecimento_by_codigo_cnes(
    codigo_cnes: int | str,
    client: httpx.Client | None = None,
) -> CnesEstabelecimento | None:
    """Consulta direta por código CNES, quando já conhecido (ex.: reprocessamento
    de um lead já enriquecido antes). Devolve None se o código não existir (404)."""
    client, owns_client = _get_client(client)

    try:
        resp = client.get(f"{ESTABELECIMENTOS_PATH}/{codigo_cnes}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return _parse_estabelecimento(resp.json())
    finally:
        if owns_client:
            client.close()
