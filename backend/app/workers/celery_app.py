"""
Celery app — HunterPro Minotto Contabilidade.

Cada etapa do pipeline de busca/enriquecimento vira uma task. A ordem
de execução por lead é a mesma definida no fluxo de execução
apresentado no kickoff (documento 03_Fluxo_Execucao.html):

  1. search_google_places      → busca inicial por nicho + região
  2. enrich_receita_federal    → CNAE, sócios/decisor, porte, anos de
                                  mercado, situação cadastral (BrasilAPI) —
                                  também resolve codigo_municipio_cnes,
                                  usado pela ETAPA 5
  3. enrich_pgfn_divida_ativa  → cruzamento por CNPJ (sem contato, só sinal)
  4. enrich_zona_franca        → lookup estático de município (sem custo externo)
  5. enrich_cnes               → cruzamento por CNPJ (confirma unidade de
                                  saúde registrada; API não expõe especialidade,
                                  ver app/services/cnes.py)
  6. enrich_site_firecrawl     → varredura de site + IA (serviços, RQE inferido)
  7. enrich_instagram          → localizado a partir do site
  8. enrich_linkedin           → busca por razão social/decisor
  9. validate_whatsapp         → Evolution API
  10. enrich_email             → Hunter.io + validação MX/ZeroBounce
  11. compute_lead_score       → aplica app/scoring/rules.py

Implementadas até agora: enrich_receita_federal, enrich_pgfn_divida_ativa
(+ refresh_pgfn_index), enrich_zona_franca, enrich_cnes. As demais
continuam placeholder — implementação entra sessão a sessão.
"""
from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "hunterpro_minotto",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="America/Sao_Paulo",
    enable_utc=True,
)


@celery_app.task(name="search_google_places")
def search_google_places(nicho: str, regiao: str) -> list[dict]:
    """TODO: busca inicial via Google Places API (ETAPA 1 do pipeline)."""
    raise NotImplementedError("Implementar na próxima sessão.")


@celery_app.task(name="enrich_receita_federal")
def enrich_receita_federal(cnpj: str) -> dict:
    """
    Consulta a Receita Federal (via BrasilAPI, ver app/services/receita_federal.py)
    pra CNAE, porte, situação cadastral, anos de mercado e decisor.

    Também resolve `codigo_municipio_cnes`, já convertido do código IBGE
    de 7 dígitos devolvido aqui pro formato de 6 dígitos que
    `enrich_cnes` espera (ver app/services/ibge_municipios.py) — fecha o
    gap deixado em aberto na sessão anterior.
    """
    from app.services.ibge_municipios import codigo_ibge_para_cnes
    from app.services.receita_federal import anos_mercado, fetch_empresa, identificar_decisor

    empresa = fetch_empresa(cnpj)
    if empresa is None:
        return {"cnpj": cnpj, "encontrado": False}

    decisor = identificar_decisor(empresa.socios)
    codigo_municipio_cnes = (
        codigo_ibge_para_cnes(empresa.codigo_municipio_ibge)
        if empresa.codigo_municipio_ibge
        else None
    )

    return {
        "cnpj": empresa.cnpj,
        "encontrado": True,
        "razao_social": empresa.razao_social,
        "nome_fantasia": empresa.nome_fantasia,
        "situacao_cadastral": empresa.situacao_cadastral,
        "cnae_principal": empresa.cnae_principal_codigo,
        "porte": empresa.porte,
        "municipio": empresa.municipio,
        "uf": empresa.uf,
        "codigo_municipio_ibge": empresa.codigo_municipio_ibge,
        "codigo_municipio_cnes": codigo_municipio_cnes,
        "anos_mercado": anos_mercado(empresa.data_inicio_atividade),
        "simples_nacional": empresa.opcao_pelo_simples,
        "decisor_nome": decisor.nome if decisor else None,
    }


@celery_app.task(name="refresh_pgfn_index")
def refresh_pgfn_index() -> dict:
    """
    Reconstrói o índice CNPJ → dívida ativa a partir dos CSVs baixados
    em `settings.PGFN_DADOS_ABERTOS_DIR`, e salva no Redis.

    Roda quando o arquivo trimestral da PGFN é atualizado — NÃO por
    lead. Ver `enrich_pgfn_divida_ativa` para a consulta por lead.
    """
    import redis as redis_lib

    from app.services.pgfn import build_pgfn_index, find_pgfn_csv_files, save_index_to_redis

    csv_paths = find_pgfn_csv_files(settings.PGFN_DADOS_ABERTOS_DIR)
    if not csv_paths:
        return {"status": "sem_arquivos", "dir": settings.PGFN_DADOS_ABERTOS_DIR}

    index = build_pgfn_index(csv_paths)
    redis_client = redis_lib.from_url(settings.REDIS_URL)
    total = save_index_to_redis(index, redis_client)

    return {"status": "ok", "cnpjs_indexados": total, "arquivos_processados": [str(p) for p in csv_paths]}


@celery_app.task(name="enrich_pgfn_divida_ativa")
def enrich_pgfn_divida_ativa(cnpj: str) -> dict:
    """
    Consulta rápida (Redis) do CNPJ do lead contra o índice da PGFN.

    Retorna a quantidade de inscrições ativas E os pontos já calculados
    pra esse critério do score (fórmula confirmada com o Douglas em
    19/08/2026 — ver app/scoring/rules.py::pgfn_points).

    Não retorna contato nenhum (a PGFN não tem esse dado; contato vem
    de outras etapas do pipeline, cruzado pelo mesmo CNPJ).
    """
    import redis as redis_lib

    from app.scoring.rules import pgfn_points
    from app.services.pgfn import lookup_debt_from_redis, normalize_cnpj

    redis_client = redis_lib.from_url(settings.REDIS_URL)
    normalized_cnpj = normalize_cnpj(cnpj) or cnpj
    summary = lookup_debt_from_redis(cnpj, redis_client)

    qtd = summary.qtd_inscricoes_ativas if summary else 0

    return {
        "cnpj": normalized_cnpj,
        "qtd_inscricoes_ativas": qtd,
        "valor_total": summary.valor_total if summary else 0.0,
        "algum_ajuizado": summary.algum_ajuizado if summary else False,
        "pontos_score": pgfn_points(qtd),
    }


@celery_app.task(name="enrich_zona_franca")
def enrich_zona_franca(municipio: str, uf: str) -> bool:
    """Lookup estático — já implementado em app/scoring/zona_franca.py."""
    from app.scoring.zona_franca import is_zona_franca

    return is_zona_franca(municipio, uf)


@celery_app.task(name="enrich_cnes")
def enrich_cnes(cnpj: str, codigo_municipio: int) -> dict:
    """
    Cruza o CNPJ do lead contra os estabelecimentos de saúde cadastrados
    no CNES para o município informado (código IBGE de 6 dígitos — precisa
    vir de uma etapa anterior do pipeline, ex.: enrich_receita_federal).

    A API do CNES não tem filtro por CNPJ nem retorna especialidade médica
    (ver app/services/cnes.py para as limitações confirmadas) — este
    enriquecimento só confirma se o CNPJ tem unidade registrada no CNES.
    Isso reforça (não substitui) a inferência de RQE via IA feita em
    `enrich_site_firecrawl`; a decisão de como combinar os dois no score
    do critério `rqe_especialista` ainda não foi confirmada com o cliente.
    """
    from app.services.cnes import fetch_estabelecimentos_por_municipio, find_estabelecimento_by_cnpj
    from app.services.pgfn import normalize_cnpj

    estabelecimentos = fetch_estabelecimentos_por_municipio(codigo_municipio)
    encontrado = find_estabelecimento_by_cnpj(estabelecimentos, cnpj)
    normalized_cnpj = normalize_cnpj(cnpj) or cnpj

    if encontrado is None:
        return {"cnpj": normalized_cnpj, "encontrado_cnes": False}

    return {
        "cnpj": encontrado.numero_cnpj,
        "encontrado_cnes": True,
        "cnes_codigo": encontrado.codigo_cnes,
        "codigo_tipo_unidade": encontrado.codigo_tipo_unidade,
    }


@celery_app.task(name="enrich_site_firecrawl")
def enrich_site_firecrawl(site_url: str) -> dict:
    """TODO: varredura do site + IA (serviços oferecidos, RQE inferido)."""
    raise NotImplementedError("Implementar na próxima sessão.")


@celery_app.task(name="validate_whatsapp")
def validate_whatsapp(telefone: str) -> bool:
    """TODO: validação via Evolution API (mesmo padrão do projeto SR Contábil)."""
    raise NotImplementedError("Implementar na próxima sessão.")


@celery_app.task(name="enrich_email")
def enrich_email(dominio: str, decisor_nome: str | None) -> dict:
    """TODO: Hunter.io + validação MX/ZeroBounce."""
    raise NotImplementedError("Implementar na próxima sessão.")


@celery_app.task(name="compute_lead_score")
def compute_lead_score(lead_id: str) -> dict:
    """TODO: aplicar app/scoring/rules.py sobre os dados enriquecidos do lead."""
    raise NotImplementedError("Implementar na próxima sessão.")
