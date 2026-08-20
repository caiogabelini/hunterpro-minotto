"""
Integração com a Receita Federal via BrasilAPI — CNAE, sócios/decisor,
porte, situação cadastral e anos de mercado.
================================================================================

Fonte: BrasilAPI (https://brasilapi.com.br/api/cnpj/v1/{cnpj}) — API
pública, gratuita, sem autenticação, que espelha os Dados Abertos do CNPJ
da própria Receita Federal (mesmas informações do cartão CNPJ). Escolhida
no lugar de baixar o arquivo bruto de Dados Abertos do CNPJ (dezenas de GB,
atualização mensal) por ser uma consulta pontual por CNPJ -- mesmo
raciocínio do CNES (API viva por registro, não um arquivo gigante pra
indexar como a PGFN).

⚠️ NOMES DE CAMPO: confirmados batendo em exemplos reais da API em
19/08/2026 (mesmo tipo de verificação manual já feita pro CNES -- ver
app/services/cnes.py). Ainda assim, todo o parsing usa `.get()` com
defaults, então um campo ausente/renomeado numa resposta real não quebra
o parser, só deixa aquele dado específico vazio -- ajustar as chaves em
`_parse_empresa` se o formato real divergir.

O QUE ESTE MÓDULO FAZ:
  - Consulta uma empresa por CNPJ: razão social, nome fantasia, CNAE
    principal, porte, situação cadastral, data de início de atividade
    (-> `anos_mercado`), Simples Nacional/MEI, quadro de sócios (QSA) e o
    código IBGE do município (`codigo_municipio_ibge`, 7 dígitos)
  - `identificar_decisor` escolhe, dentre os sócios, o mais provável
    decisor por palavra-chave na qualificação (ver `QUALIFICACOES_DECISOR`
    -- não existe uma tabela oficial confiável de códigos de qualificação
    decorada de memória pra usar aqui, então o critério é o texto mesmo)
  - `codigo_municipio_ibge` retornado aqui é o mesmo dado que faltava pro
    `enrich_cnes` (ver app/services/cnes.py) -- convertê-lo com
    `app.services.ibge_municipios.codigo_ibge_para_cnes` antes de repassar

O QUE ESTE MÓDULO **NÃO** FAZ (de propósito):
  - Não calcula pontos de score -- assim como PGFN (antes da fórmula ser
    confirmada) e CNES, devolve dado bruto; a conversão pros critérios
    `unidade_cnpj_ativo` e `decisor_identificavel` fica pro
    `compute_lead_score`, ainda não implementado.
  - Não trata rate limit da BrasilAPI (a API pública tem limite por IP,
    não documentado com precisão) -- fora do escopo desta sessão.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import httpx

from app.core.config import settings

CNPJ_PATH = "/api/cnpj/v1/{cnpj}"
DEFAULT_TIMEOUT = 15.0

# Qualificações de sócio que indicam poder de decisão -- heurística por
# palavra-chave no texto de `qualificacao_socio` (ver ressalva no topo do
# módulo sobre não haver uma tabela de códigos confiável disponível).
QUALIFICACOES_DECISOR = (
    "ADMINISTRADOR",
    "PRESIDENTE",
    "DIRETOR",
    "TITULAR",
    "SOCIO-GERENTE",
    "SÓCIO-GERENTE",
)


@dataclass
class Socio:
    nome: str
    qualificacao: str


@dataclass
class ReceitaFederalEmpresa:
    """Uma empresa, já normalizada a partir da resposta da BrasilAPI."""

    cnpj: str
    razao_social: str
    nome_fantasia: str
    situacao_cadastral: str
    cnae_principal_codigo: str | None
    cnae_principal_descricao: str | None
    porte: str | None
    municipio: str | None
    uf: str | None
    codigo_municipio_ibge: int | None
    data_inicio_atividade: str | None
    opcao_pelo_simples: bool | None
    opcao_pelo_mei: bool | None
    socios: list[Socio] = field(default_factory=list)


def _parse_empresa(raw: dict) -> ReceitaFederalEmpresa:
    socios = [
        Socio(nome=s.get("nome_socio") or "", qualificacao=s.get("qualificacao_socio") or "")
        for s in (raw.get("qsa") or [])
    ]
    cnae_fiscal = raw.get("cnae_fiscal")

    return ReceitaFederalEmpresa(
        cnpj=str(raw.get("cnpj") or ""),
        razao_social=raw.get("razao_social") or "",
        nome_fantasia=raw.get("nome_fantasia") or "",
        situacao_cadastral=raw.get("descricao_situacao_cadastral") or "",
        cnae_principal_codigo=str(cnae_fiscal) if cnae_fiscal else None,
        cnae_principal_descricao=raw.get("cnae_fiscal_descricao"),
        porte=raw.get("porte"),
        municipio=raw.get("municipio"),
        uf=raw.get("uf"),
        codigo_municipio_ibge=raw.get("codigo_municipio_ibge"),
        data_inicio_atividade=raw.get("data_inicio_atividade"),
        opcao_pelo_simples=raw.get("opcao_pelo_simples"),
        opcao_pelo_mei=raw.get("opcao_pelo_mei"),
        socios=socios,
    )


def _get_client(client: httpx.Client | None) -> tuple[httpx.Client, bool]:
    """Mesmo padrão de client injetável usado em app/services/cnes.py."""
    if client is not None:
        return client, False
    return httpx.Client(base_url=settings.RECEITA_FEDERAL_API_BASE_URL, timeout=DEFAULT_TIMEOUT), True


def fetch_empresa(cnpj: str, client: httpx.Client | None = None) -> ReceitaFederalEmpresa | None:
    """Consulta uma empresa por CNPJ. Devolve None se o CNPJ não existir (404)."""
    client, owns_client = _get_client(client)

    try:
        resp = client.get(CNPJ_PATH.format(cnpj=cnpj))
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return _parse_empresa(resp.json())
    finally:
        if owns_client:
            client.close()


def anos_mercado(data_inicio_atividade: str | None, hoje: date | None = None) -> int | None:
    """Converte `data_inicio_atividade` ('YYYY-MM-DD') em anos completos
    de mercado. Devolve None se a data não vier preenchida."""
    if not data_inicio_atividade:
        return None

    inicio = date.fromisoformat(data_inicio_atividade)
    hoje = hoje or date.today()
    anos = hoje.year - inicio.year - ((hoje.month, hoje.day) < (inicio.month, inicio.day))
    return max(anos, 0)


def identificar_decisor(socios: list[Socio]) -> Socio | None:
    """
    Escolhe o sócio mais provável de ser o decisor: o primeiro cuja
    qualificação contém uma palavra-chave de poder de decisão. Se nenhum
    bater, cai pro primeiro sócio da lista (melhor um nome do que nenhum,
    já que o critério `decisor_identificavel` só precisa de um nome
    identificável). Devolve None se não houver sócio nenhum.
    """
    for socio in socios:
        qualificacao_upper = socio.qualificacao.upper()
        if any(palavra in qualificacao_upper for palavra in QUALIFICACOES_DECISOR):
            return socio

    return socios[0] if socios else None
