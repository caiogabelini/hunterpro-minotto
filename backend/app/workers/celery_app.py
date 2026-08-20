"""
Celery app — HunterPro Minotto Contabilidade.

Cada etapa do pipeline de busca/enriquecimento vira uma task. A ordem
de execução por lead é a mesma definida no fluxo de execução
apresentado no kickoff (documento 03_Fluxo_Execucao.html), com uma
mudança em 19/08/2026: a entrada do pipeline deixou de ser uma busca
livre no Google Places e passou a ser a "semente" filtrada a partir do
arquivo de Estabelecimentos da Receita Federal — ver ETAPA 0.

  0. gerar_semente_leads       → NÃO faz parte do fluxo por lead abaixo,
                                  roda uma vez sobre o(s) arquivo(s) de
                                  Estabelecimentos da Receita Federal
                                  (baixado(s) manualmente, mesmo padrão
                                  da PGFN) e gera a lista inicial de
                                  CNPJs, já filtrados por CNAE de
                                  consultório médico + UF/região (ver
                                  app/services/receita_federal_bulk.py)
  1. search_google_places      → enriquecimento por CNPJ já filtrado na
                                  ETAPA 0 (busca por nome_fantasia → site,
                                  rating, nº de avaliações) — NÃO é mais o
                                  ponto de entrada do pipeline
  2. enrich_receita_federal    → CNAE, sócios/decisor, porte, anos de
                                  mercado, situação cadastral (BrasilAPI) —
                                  também resolve codigo_municipio_cnes,
                                  usado pela ETAPA 5
  3. enrich_pgfn_divida_ativa  → cruzamento por CNPJ (sem contato, só sinal)
  4. enrich_zona_franca        → lookup estático de município (sem custo externo)
  5. enrich_cnes               → cruzamento por CNPJ (confirma unidade de
                                  saúde registrada; API não expõe especialidade,
                                  ver app/services/cnes.py)
  6. enrich_site_firecrawl     → varredura de site (Firecrawl, ver
                                  app/services/site_scraping.py) + IA
                                  (Claude Haiku, ver
                                  app/services/ai_enrichment.py) —
                                  serviços/especialidades, RQE mencionado,
                                  e o link do Instagram (extraído por
                                  regex direto do markdown, cobre a
                                  ETAPA 7 também — não precisou virar
                                  task separada)
  7. enrich_instagram          → coberta pela ETAPA 6 (extração de
                                  Instagram já sai do mesmo scrape); sem
                                  task própria
  8. enrich_linkedin           → busca por razão social/decisor
  9. validate_whatsapp         → Evolution API — normaliza o telefone
                                  (código do país por tamanho, não por
                                  prefixo, ver app/services/whatsapp.py)
                                  e confirma WhatsApp ativo
  10. enrich_email             → Hunter.io (encontra) + MX (dnspython) +
                                  ZeroBounce (valida entregabilidade) —
                                  ver app/services/email_enrichment.py
  11. compute_lead_score       → aplica app/scoring/rules.py

Implementadas até agora: gerar_semente_leads, enrich_receita_federal,
enrich_pgfn_divida_ativa (+ refresh_pgfn_index), enrich_zona_franca,
enrich_cnes, enrich_site_firecrawl, validate_whatsapp, enrich_email. As
demais continuam placeholder — implementação entra sessão a sessão.
`search_google_places` tem a assinatura nova (recebe estabelecimentos já
filtrados) mas a chamada real à Places API ainda não foi implementada
(falta validar contra uma GOOGLE_API_KEY real). `enrich_site_firecrawl`
também não foi testada contra as APIs reais do Firecrawl/Anthropic nesta
sessão (sem FIRECRAWL_API_KEY disponível; o schema do Firecrawl foi
confirmado contra o openapi.json oficial, não por chamada real — ver
app/services/site_scraping.py).
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


@celery_app.task(name="gerar_semente_leads")
def gerar_semente_leads(
    ufs: list[str] | None = None,
    regiao: str | None = None,
) -> dict:
    """
    Gera a "semente" de leads a partir do(s) arquivo(s) ESTABELECIMENTOS +
    EMPRESAS baixados em `settings.RECEITA_FEDERAL_DADOS_ABERTOS_DIR`
    (mesmo padrão de `refresh_pgfn_index`) — filtra ESTABELECIMENTOS por
    CNAE de consultório médico e por UF/região, depois junta a razão
    social do arquivo EMPRESAS pela raiz do CNPJ (ver
    app/services/receita_federal_bulk.py).

    Razão social é o campo principal do resultado (obrigatório no
    cadastro de CNPJ); nome_fantasia é complementar/opcional — ver
    `search_google_places`, que prefere razão social e só cai pra
    nome_fantasia se ela vier vazia.

    NÃO roda por lead — roda uma vez (ou quando o cliente quiser ampliar
    a busca pra outras UFs/região) e alimenta `search_google_places` com
    os CNPJs encontrados.
    """
    from app.services.receita_federal_bulk import (
        enriquecer_com_razao_social,
        filtrar_semente_leads,
        find_empresas_rfb_files,
        find_estabelecimentos_rfb_files,
    )

    estabelecimentos_paths = find_estabelecimentos_rfb_files(settings.RECEITA_FEDERAL_DADOS_ABERTOS_DIR)
    if not estabelecimentos_paths:
        return {"status": "sem_arquivos", "dir": settings.RECEITA_FEDERAL_DADOS_ABERTOS_DIR}

    estabelecimentos = list(
        filtrar_semente_leads(estabelecimentos_paths, ufs=set(ufs) if ufs else None, regiao=regiao)
    )

    empresas_paths = find_empresas_rfb_files(settings.RECEITA_FEDERAL_DADOS_ABERTOS_DIR)
    if empresas_paths:
        estabelecimentos = enriquecer_com_razao_social(estabelecimentos, empresas_paths)

    return {
        "status": "ok",
        "total_encontrado": len(estabelecimentos),
        "arquivos_processados": [str(p) for p in estabelecimentos_paths + empresas_paths],
        "estabelecimentos": [
            {
                "cnpj": e.cnpj,
                "razao_social": e.razao_social,
                "nome_fantasia": e.nome_fantasia,
                "uf": e.uf,
                "cnae_fiscal_principal": e.cnae_fiscal_principal,
            }
            for e in estabelecimentos
        ],
    }


def nome_busca_google_places(estabelecimento: dict) -> str | None:
    """
    Escolhe o nome a usar na busca por um estabelecimento no Google
    Places: preferencialmente `razao_social` (campo obrigatório no
    cadastro de CNPJ), caindo pro `nome_fantasia` só se ela vier vazia —
    o que não deveria acontecer com os arquivos reais (só em cenários de
    EMPRESAS parcial/teste, ver `enriquecer_com_razao_social` em
    app/services/receita_federal_bulk.py). Devolve None se nenhum dos
    dois estiver preenchido.
    """
    return estabelecimento.get("razao_social") or estabelecimento.get("nome_fantasia") or None


@celery_app.task(name="search_google_places")
def search_google_places(estabelecimentos: list[dict]) -> list[dict]:
    """
    Enriquecimento via Google Places — NÃO é mais o ponto de entrada do
    pipeline (ver ETAPA 0 no docstring do módulo: a entrada agora é
    `gerar_semente_leads`, que já filtra os CNPJs por CNAE de consultório
    médico + UF/região e já junta a razão social do arquivo EMPRESAS).

    Recebe a lista de estabelecimentos já filtrados (formato devolvido
    por `gerar_semente_leads`: cnpj, razao_social, nome_fantasia, uf, ...)
    e busca cada um no Google Places pelo nome escolhido por
    `nome_busca_google_places` (razão social, com fallback pra
    nome_fantasia), pra trazer site, rating e nº de avaliações.

    TODO: implementar a chamada real à Places API (Text Search pelo nome
    de `nome_busca_google_places` + UF, depois Place Details pro
    site/rating/avaliações). Ainda não dá pra confirmar o formato de
    resposta contra a API real nesta sessão — diferente do
    CNES/IBGE/BrasilAPI (públicas, sem chave), a Places API exige uma
    GOOGLE_API_KEY válida pra testar, que não está configurada.
    """
    raise NotImplementedError(
        "Implementar a chamada à Places API numa sessão com GOOGLE_API_KEY configurada."
    )


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


SITE_CONTENT_RESUMO_MAX_CARACTERES = 500


@celery_app.task(name="enrich_site_firecrawl")
def enrich_site_firecrawl(site_url: str) -> dict:
    """
    Varre o site do lead via Firecrawl (ver app/services/site_scraping.py)
    e infere, via IA (Claude Haiku, ver app/services/ai_enrichment.py),
    menção a RQE e serviços/especialidades oferecidos. Também extrai o
    link do Instagram por regex direto do markdown — não precisa de IA
    pra isso.

    `site_content_resumo` é só um recorte do markdown (primeiros
    `SITE_CONTENT_RESUMO_MAX_CARACTERES` caracteres) pra referência —
    o texto completo não é devolvido aqui, só usado internamente pra
    montar o prompt da IA.
    """
    from app.services.ai_enrichment import inferir_dados_site
    from app.services.site_scraping import extrair_instagram, scrape_site

    resultado_scrape = scrape_site(site_url)
    if not resultado_scrape.sucesso:
        return {
            "site_url": site_url,
            "scrape_sucesso": False,
            "site_content_resumo": "",
            "instagram": None,
            "rqe_mencionado": False,
            "rqe_numero": None,
            "servicos": [],
        }

    instagram = extrair_instagram(resultado_scrape.markdown)
    inferencia = inferir_dados_site(resultado_scrape.markdown)

    return {
        "site_url": site_url,
        "scrape_sucesso": True,
        "site_content_resumo": resultado_scrape.markdown[:SITE_CONTENT_RESUMO_MAX_CARACTERES],
        "instagram": instagram,
        "rqe_mencionado": inferencia.rqe_mencionado,
        "rqe_numero": inferencia.rqe_numero,
        "servicos": inferencia.servicos,
    }


@celery_app.task(name="validate_whatsapp")
def validate_whatsapp(telefone: str) -> dict:
    """
    Valida se um telefone tem WhatsApp ativo via Evolution API (ver
    app/services/whatsapp.py — endpoint e contrato já validados em
    produção no projeto SR Contábil). O número é normalizado antes de
    bater na API; se não normalizar pra um formato plausível, a task
    devolve `numero_valido=False` sem chamar a Evolution API.
    """
    from app.services.whatsapp import check_whatsapp

    resultado = check_whatsapp(telefone)

    return {
        "telefone_original": telefone,
        "numero_formatado": resultado.numero_formatado,
        "numero_valido": resultado.numero_valido,
        "whatsapp_ativo": resultado.tem_whatsapp,
    }


@celery_app.task(name="enrich_email")
def enrich_email(dominio: str, decisor_nome: str | None) -> dict:
    """
    Encontra e valida o e-mail do decisor: Hunter.io Email Finder ->
    validação de MX (dnspython) -> ZeroBounce (ver
    app/services/email_enrichment.py — contrato das duas APIs já
    validado em produção no projeto SR Contábil). Só considera o e-mail
    "aprovado" se o ZeroBounce devolver status "valid" ou "catch-all".
    """
    from app.services.email_enrichment import enriquecer_email

    resultado = enriquecer_email(dominio, decisor_nome)

    return {
        "dominio": dominio,
        "email": resultado.email,
        "hunter_score": resultado.hunter_score,
        "mx_valido": resultado.mx_valido,
        "zerobounce_status": resultado.zerobounce_status,
        "email_aprovado": resultado.aprovado,
    }


@celery_app.task(name="compute_lead_score")
def compute_lead_score(lead_id: str) -> dict:
    """TODO: aplicar app/scoring/rules.py sobre os dados enriquecidos do lead."""
    raise NotImplementedError("Implementar na próxima sessão.")
