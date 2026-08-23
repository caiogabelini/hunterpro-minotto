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
  11. compute_lead_score       → combina o resultado de todas as etapas
                                  acima nos 9 critérios de
                                  app/scoring/rules.py (via
                                  app/scoring/compute_lead_score.py) —
                                  score total 0-100 + breakdown por
                                  critério

`process_lead_pipeline(cnpj, ...)`, adicionada em 20/08/2026, é a task
orquestradora: dispara as ETAPAs 1-11 acima na ordem que respeita as
dependências reais entre elas (não a ordem arbitrária da lista — ver o
comentário de dependências logo antes da task no arquivo), junta os
resultados, chama compute_lead_score e persiste tudo num Lead (upsert
por CNPJ). Uma etapa que falha não derruba as outras; uma etapa cuja
dependência de dado não está disponível (site_url/telefone, que nenhuma
etapa implementada produz ainda; ou uma API key não configurada) é
pulada com aviso no log, registrada em `etapas_puladas` no resultado.

Implementadas até agora: gerar_semente_leads, enrich_receita_federal,
enrich_pgfn_divida_ativa (+ refresh_pgfn_index), enrich_zona_franca,
enrich_cnes, enrich_site_firecrawl, validate_whatsapp, enrich_email,
compute_lead_score, process_lead_pipeline. As demais continuam
placeholder — implementação entra sessão a sessão. `search_google_places`
tem a assinatura nova (recebe estabelecimentos já filtrados) mas a
chamada real à Places API ainda não foi implementada (falta validar
contra uma GOOGLE_API_KEY real) — `process_lead_pipeline` pula essa
etapa com aviso, não trava o pipeline. `enrich_site_firecrawl` também
não foi testada contra as APIs reais do Firecrawl/Anthropic (sem
FIRECRAWL_API_KEY disponível; o schema do Firecrawl foi confirmado
contra o openapi.json oficial, não por chamada real — ver
app/services/site_scraping.py); `process_lead_pipeline` pula essa etapa
também se a chave não estiver configurada ou se não houver `site_url`.
"""
import logging
import math

from celery import Celery

# ⚠️ Import de efeito colateral, obrigatório e no TOPO -- registra TODOS os
# modelos em `Base.metadata` assim que o processo do worker sobe. Sem ele,
# `executar_busca_mensal` quebra com NoReferencedTableError ao persistir
# `BuscaLeads` (FK -> users), porque o worker não tem nenhum outro motivo
# pra importar `app.models.user`. Ver a docstring de `app/models/__init__.py`
# pro histórico do bug. NÃO trocar por um import dentro da função: o ponto é
# justamente não depender de qual task roda primeiro.
import app.models  # noqa: F401
from app.core.config import settings
from app.core.segredos import redigir_segredos
from app.core.tempo import agora_utc

logger = logging.getLogger(__name__)

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

    # Deduplica por CNPJ. Necessário desde 23/08/2026, quando os finders
    # passaram a reconhecer TAMBÉM os .zip da Receita: uma pasta com
    # `Estabelecimentos0.zip` e o CSV extraído dele lado a lado devolve os
    # dois caminhos, e sem isto o mesmo CNPJ entraria duas vezes na semente
    # -- gastando o dobro de chamadas pagas no pipeline pra gravar o mesmo
    # Lead (o upsert por CNPJ esconderia o desperdício no banco).
    #
    # Deduplicar por CNPJ em vez de escolher um formato "vencedor" é
    # deliberado: correlacionar zip e CSV extraído pelo nome não é
    # confiável, e preferir um formato descartaria fatias de quem extraiu
    # só parte dos .zip. Também protege contra sobreposição entre fatias,
    # qualquer que seja a causa.
    total_bruto = len(estabelecimentos)
    vistos: set[str] = set()
    unicos = []
    for estabelecimento in estabelecimentos:
        if estabelecimento.cnpj in vistos:
            continue
        vistos.add(estabelecimento.cnpj)
        unicos.append(estabelecimento)
    estabelecimentos = unicos

    if total_bruto != len(estabelecimentos):
        logger.warning(
            "gerar_semente_leads: %s CNPJs duplicados descartados (%s -> %s). "
            "Causa provável: a pasta %s tem o .zip E o CSV extraído do mesmo lote.",
            total_bruto - len(estabelecimentos),
            total_bruto,
            len(estabelecimentos),
            settings.RECEITA_FEDERAL_DADOS_ABERTOS_DIR,
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
                # Os dois campos abaixo alimentam a PRÉ-SELEÇÃO
                # (app/scoring/pre_selecao.py), não o pipeline de
                # enriquecimento. `municipio_codigo_rfb` é o código
                # INTERNO da Receita (não é IBGE -- ver o aviso no topo
                # de receita_federal_bulk.py); só serve pra resolver o
                # NOME do município contra a tabela de domínio MUNICCSV e
                # daí decidir Zona Franca de graça, sem gastar uma
                # chamada da BrasilAPI por CNPJ.
                "municipio_codigo_rfb": e.municipio_codigo_rfb,
                "situacao_cadastral": e.situacao_cadastral,
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
    nome_fantasia), pra trazer site, rating e nº de avaliações — um só
    Text Search por estabelecimento, sem Place Details (a API New já
    devolve site/rating/avaliações no Text Search, ver
    app/services/google_places.py).

    Contrato implementado contra a documentação oficial do Google em
    20/08/2026 — NÃO validado contra a API real (limitação de ambiente
    desta sessão: sem chamada de rede autenticada disponível via
    nenhuma ferramenta; ver ressalva completa em
    app/services/google_places.py).

    Estabelecimentos sem nome pra buscar (razão social e nome fantasia
    ambos vazios) ou cuja busca falhar/não achar nada entram no
    resultado com site/rating/avaliações como None — não quebra o lote
    inteiro por causa de um estabelecimento.
    """
    from app.services.google_places import buscar_estabelecimento

    resultado = []
    for estabelecimento in estabelecimentos:
        nome_busca = nome_busca_google_places(estabelecimento)
        lugar = None
        if nome_busca:
            try:
                lugar = buscar_estabelecimento(nome_busca, estabelecimento.get("uf"))
            except Exception:
                logger.exception(
                    "Busca no Google Places falhou pro CNPJ %s -- seguindo sem esse sinal.",
                    estabelecimento.get("cnpj"),
                )

        resultado.append({
            **estabelecimento,
            "google_place_id": lugar.place_id if lugar else None,
            "site": lugar.site if lugar else None,
            "rating": lugar.rating if lugar else None,
            "avaliacoes": lugar.avaliacoes if lugar else None,
            "telefone_google": lugar.telefone if lugar else None,
        })

    return resultado


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
def enrich_cnes(cnpj: str, codigo_municipio: int, cache_municipios: dict | None = None) -> dict:
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

    `cache_municipios` — dict {codigo_municipio: [CnesEstabelecimento]}
    -------------------------------------------------------------------
    Como a API não filtra por CNPJ do lado do servidor, cada chamada baixa
    a lista INTEIRA de estabelecimentos de saúde do município, paginando
    de 20 em 20 até o teto de `MAX_PAGINAS` (500) — ou seja, até 500
    requisições HTTP por lead. Sem cache, dois leads do mesmo município
    refazem a paginação completa do zero; num lote com várias clínicas da
    mesma capital, isso multiplica um trabalho já caro em tempo.

    O cache é um dict COMUM, passado de fora (mesma convenção de injeção
    de `client=None`/`db=None` no resto do projeto), e não um cache de
    módulo. Isso é deliberado: quem cria o dict define o tempo de vida
    dele. `executar_busca_mensal` cria um por execução, então os leads de
    uma mesma busca compartilham o resultado e buscas diferentes nunca
    reaproveitam dados de uma execução anterior (o cadastro do CNES muda
    entre um mês e outro, e um cache de módulo entregaria dado velho sem
    ninguém perceber). Chamada avulsa, sem o parâmetro, mantém o
    comportamento antigo: sempre busca da API.
    """
    from app.services.cnes import fetch_estabelecimentos_por_municipio, find_estabelecimento_by_cnpj
    from app.services.pgfn import normalize_cnpj

    if cache_municipios is None:
        estabelecimentos = fetch_estabelecimentos_por_municipio(codigo_municipio)
    else:
        # A chave é normalizada pra string: o código pode chegar como int
        # (de enrich_receita_federal) ou str (de um chamador manual), e
        # 355030 != "355030" num dict -- sem isso o cache erraria o alvo
        # silenciosamente e voltaria a paginar tudo de novo.
        chave = str(codigo_municipio)
        if chave not in cache_municipios:
            cache_municipios[chave] = fetch_estabelecimentos_por_municipio(codigo_municipio)
            logger.info("CNES: município %s baixado e guardado no cache da execução.", chave)
        else:
            logger.info("CNES: município %s reaproveitado do cache -- paginação evitada.", chave)
        estabelecimentos = cache_municipios[chave]

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

    # ⚠️ Guarda de configuração -- mesmo padrão de `enrich_email` e do
    # FIRECRAWL_API_KEY. Fica DENTRO da task (não no
    # `process_lead_pipeline`) por dois motivos: a task é chamável
    # sozinha, e assim um teste que substitui `validate_whatsapp` inteira
    # não fica dependendo do que está configurado no ambiente de quem
    # roda a suíte.
    #
    # Sem isso, `httpx.Client(base_url="")` (app/services/whatsapp.py)
    # falharia em toda chamada, e `_rodar_etapa` engoliria o erro: o
    # painel não distinguiria "Evolution não configurada" de "esse número
    # não tem WhatsApp".
    faltando = [
        nome
        for nome, valor in (
            ("EVOLUTION_URL", settings.EVOLUTION_URL),
            ("EVOLUTION_KEY", settings.EVOLUTION_KEY),
            ("EVOLUTION_INSTANCE", settings.EVOLUTION_INSTANCE),
        )
        if not valor
    ]
    if faltando:
        motivo = f"{' e '.join(faltando)} não configurada(s)"
        logger.warning("Pulando validate_whatsapp pro telefone informado: %s", motivo)
        return {
            "telefone_original": telefone,
            "numero_formatado": None,
            "whatsapp_ativo": None,
            "etapa_pulada": motivo,
        }

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

    # ⚠️ Guarda de configuração -- mesmo padrão do FIRECRAWL_API_KEY em
    # process_lead_pipeline. Sem isso a chamada sai com a chave vazia e o
    # Hunter devolve 401 "No user found for the API key supplied", que
    # `_rodar_etapa` engole: a etapa vira None e o painel não distingue
    # "não configurado" de "não achou e-mail". Foi exatamente o que
    # aconteceu nos 6 leads da primeira busca real (23/08/2026) -- 6
    # falhas silenciosas, zero pistas.
    #
    # As duas chaves são exigidas juntas de propósito: sem ZeroBounce o
    # Hunter até acharia um e-mail, mas `email_aprovado` ficaria sempre
    # False e o critério `email_validado` do score nunca pontuaria --
    # gastaria consulta paga no Hunter pra produzir um sinal inútil.
    faltando = [
        nome
        for nome, valor in (
            ("HUNTER_API_KEY", settings.HUNTER_API_KEY),
            ("ZEROBOUNCE_API_KEY", settings.ZEROBOUNCE_API_KEY),
        )
        if not valor
    ]
    if faltando:
        motivo = f"{' e '.join(faltando)} não configurada(s)"
        logger.warning("Pulando enrich_email pro domínio %s: %s", dominio, motivo)
        return {
            "dominio": dominio,
            "email": None,
            "email_aprovado": False,
            "etapa_pulada": motivo,
        }

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
def compute_lead_score(lead_id: str, enriquecimentos: dict) -> dict:
    """
    Aplica os pesos e a lógica de app/scoring/rules.py (via
    app/scoring/compute_lead_score.py) sobre o resultado já rodado de
    cada task de enriquecimento pra esse lead, e devolve o score total
    (0-100) + o breakdown por critério.

    `enriquecimentos` é um dict chaveado pelo nome de cada task de
    enriquecimento (ex.: "enrich_pgfn_divida_ativa", "enrich_cnes", ...)
    — ver `montar_score_lead` em app/scoring/compute_lead_score.py pro
    formato exato esperado de cada chave (esse mesmo arquivo documenta
    as regras de `rqe_especialista` e `presenca_digital`, confirmadas
    pelo Caio/produto em 19/08/2026).

    NÃO busca esses resultados sozinha — continua sendo uma função pura
    de combinação, não de orquestração. Quem monta `enriquecimentos` e
    chama essa task é `process_lead_pipeline` (ver mais abaixo), que
    roda as etapas de enriquecimento na ordem certa pra um CNPJ e junta
    os resultados antes de chamar aqui.
    """
    from app.scoring.compute_lead_score import montar_score_lead

    resultado = montar_score_lead(enriquecimentos)

    return {
        "lead_id": lead_id,
        "score_total": resultado.score_total,
        "breakdown": [
            {
                "key": detalhe.key,
                "label": detalhe.label,
                "weight": detalhe.weight,
                "layer": detalhe.layer.value,
                "points": detalhe.points,
            }
            for detalhe in resultado.breakdown
        ],
    }


# ---------------------------------------------------------------------------
# process_lead_pipeline — orquestra as etapas de enriquecimento pra um CNPJ
# ---------------------------------------------------------------------------
#
# Grafo de dependências reais entre as etapas (não é uma ordem arbitrária):
#
#   enrich_receita_federal(cnpj)          [só precisa do cnpj]
#       ├─→ municipio, uf              -> enrich_zona_franca(municipio, uf)
#       ├─→ codigo_municipio_cnes      -> enrich_cnes(cnpj, codigo_municipio_cnes)
#       └─→ decisor_nome (opcional)    -> enrich_email(dominio, decisor_nome)
#
#   enrich_pgfn_divida_ativa(cnpj)        [independente, só precisa do cnpj]
#
#   search_google_places(estabelecimento)  [implementada em 20/08/2026 --
#                                           busca pelo nome (razão social,
#                                           com fallback pra nome_fantasia)
#                                           + UF; ver app/services/
#                                           google_places.py]
#       ├─→ site                       -> site_url_efetivo (fonte AUTOMÁTICA
#       │                                  de site_url, quando o chamador
#       │                                  não passou uma explicitamente)
#       └─→ rating, avaliacoes, telefone_google
#
#   enrich_site_firecrawl(site_url)       [precisa de site_url_efetivo (do
#                                           Google Places acima, ou passado
#                                           explicitamente) E de
#                                           FIRECRAWL_API_KEY configurada]
#       └─→ domínio (extraído da URL)   -> enrich_email(dominio, decisor_nome)
#
#   validate_whatsapp(telefone)           [precisa de telefone -- hoje SEM
#                                           fonte automática nenhuma no
#                                           pipeline; ver parâmetro telefone]
#
#   enrich_email(dominio, decisor_nome)   [precisa do domínio (de
#                                           site_url) -- decisor_nome (de
#                                           enrich_receita_federal) é
#                                           opcional: enrich_email já
#                                           aceita None (Hunter.io busca
#                                           só por domínio nesse caso, ver
#                                           app/services/email_enrichment.py),
#                                           então a etapa roda mesmo sem
#                                           decisor identificado, só com
#                                           busca menos precisa]
#
#   compute_lead_score(lead_id, enriquecimentos)  [por último, sempre --
#                                                   combina tudo que rodou]
#
# ⚠️ GAP DE DADO QUE NÃO É BUG DESTA TASK: `telefone` não sai de NENHUMA
# etapa implementada do pipeline. `search_google_places` devolve um
# `telefone_google`, mas ele é persistido no Lead sem ser reaproveitado
# como entrada de `validate_whatsapp` -- ligar os dois é uma mudança de
# comportamento (passaria a gastar chamadas da Evolution API que hoje
# nunca acontecem), não uma correção óbvia, então segue como está até
# alguém pedir. Sem `telefone` explícito, `validate_whatsapp` é pulada
# com aviso no log, não trava o pipeline.
#
# `site_url` NÃO é mais um gap: desde 20/08/2026 `search_google_places`
# é a fonte automática dele (ver o grafo acima). O parâmetro opcional
# continua existindo e tem precedência sobre o Places.


def _rodar_etapa(nome_etapa: str, funcao) -> dict | bool | None:
    """
    Roda uma etapa de enriquecimento isolada — se ela lançar qualquer
    exceção (erro de rede, API fora do ar, timeout, o que for), loga e
    devolve None em vez de derrubar o resto do pipeline. Uma etapa que
    falha vira "sem sinal" pros critérios dela em compute_lead_score,
    não interrompe as etapas seguintes nem a persistência do que já deu
    certo.
    """
    try:
        return funcao()
    except Exception as exc:
        # ⚠️ `redigir_segredos` é obrigatório aqui, não zelo extra: este é
        # o ponto por onde TODA falha de etapa vira log, e a mensagem de
        # uma exceção do httpx carrega a URL completa da chamada -- com
        # api_key na query string, quando a API só aceita assim (ex.:
        # ZeroBounce). Ver app/core/segredos.py pro histórico.
        #
        # `logger.error(..., exc_info=False)` em vez de `logger.exception`:
        # o traceback do httpx repete a URL em vários frames, e não há
        # como sanitizar um traceback já formatado sem reescrevê-lo. Perde
        # o traceback, ganha a garantia -- o nome da etapa e a mensagem
        # já localizam o problema.
        logger.error(
            "Etapa %s falhou -- seguindo o resto do pipeline sem esse sinal. Erro: %s",
            nome_etapa,
            redigir_segredos(exc),
        )
        return None


def _extrair_dominio(url: str) -> str | None:
    """Extrai o domínio (sem 'www.') de uma URL, pra usar como parâmetro
    de busca no Hunter.io (enrich_email). Tolera URL sem esquema
    ('http://'/'https://')."""
    from urllib.parse import urlparse

    parsed = urlparse(url if "//" in url else f"//{url}")
    netloc = parsed.netloc
    if not netloc:
        return None
    return netloc[4:] if netloc.startswith("www.") else netloc


def _get_db_session(db):
    """Mesmo padrão de client injetável usado nos módulos HTTP deste
    projeto (ver `_get_client` em app/services/cnes.py) — cria uma
    sessão nova se nenhuma foi injetada, pra manter a task testável sem
    precisar de um Postgres real rodando."""
    if db is not None:
        return db, False

    from app.core.database import SessionLocal

    return SessionLocal(), True


def _persistir_enriquecimento_lead(
    db,
    cnpj: str,
    razao_social: str | None,
    receita_federal: dict | None,
    pgfn: dict | None,
    zona_franca: bool | None,
    cnes: dict | None,
    google_places: dict | None,
    site_firecrawl: dict | None,
    site_url: str | None,
    whatsapp: dict | None,
    email: dict | None,
):
    """
    Upsert do Lead por CNPJ (campo `unique`/indexado no modelo, ver
    app/models/lead.py) — atualiza se já existir uma linha pra esse CNPJ
    de uma rodada anterior do pipeline, cria uma nova se não.

    Cada bloco de campos só é sobrescrito se a etapa correspondente
    realmente tiver rodado (dict/valor não-None) — uma etapa pulada ou
    que falhou nesta rodada não apaga um dado já persistido numa rodada
    anterior bem-sucedida pro mesmo lead.

    Não seta `score_total`/`score_detalhes` aqui — isso é feito por
    `process_lead_pipeline` depois de chamar `compute_lead_score`, já
    com o `lead.id` real (gerado nesta função na primeira vez que o
    lead é criado).
    """
    from sqlalchemy import select

    from app.models.lead import Lead

    lead = db.execute(select(Lead).where(Lead.cnpj == cnpj)).scalar_one_or_none()
    if lead is None:
        # razao_social é NOT NULL no modelo -- usa o que já tiver em mãos
        # (parâmetro explícito, ex.: vindo de gerar_semente_leads) e cai
        # pro próprio CNPJ como último recurso, só pra não violar a
        # constraint; o bloco de receita_federal abaixo sobrescreve com o
        # valor de verdade assim que essa etapa tiver rodado com sucesso.
        lead = Lead(cnpj=cnpj, razao_social=razao_social or cnpj)
        db.add(lead)
    elif razao_social:
        lead.razao_social = razao_social

    if receita_federal and receita_federal.get("encontrado"):
        lead.razao_social = receita_federal.get("razao_social") or lead.razao_social
        lead.nome_fantasia = receita_federal.get("nome_fantasia")
        lead.cnae_principal = receita_federal.get("cnae_principal")
        lead.municipio = receita_federal.get("municipio")
        lead.uf = receita_federal.get("uf")
        lead.anos_mercado = receita_federal.get("anos_mercado")
        lead.simples_nacional = receita_federal.get("simples_nacional")
        lead.situacao_cadastral = receita_federal.get("situacao_cadastral")
        lead.decisor_nome = receita_federal.get("decisor_nome")

    if pgfn is not None:
        lead.divida_ativa_qtd_inscricoes = pgfn.get("qtd_inscricoes_ativas")

    if zona_franca is not None:
        lead.zona_franca = zona_franca

    if site_firecrawl is not None or cnes is not None:
        rqe_mencionado = bool((site_firecrawl or {}).get("rqe_mencionado"))
        encontrado_cnes = bool((cnes or {}).get("encontrado_cnes"))
        lead.rqe_confirmado = rqe_mencionado or encontrado_cnes
        # Prioriza "inferencia_ia" quando os dois sinais estão presentes --
        # decisão de rotulagem simples, não afeta o score (que já pondera
        # os dois sinais separadamente em rqe_especialista_points).
        lead.rqe_fonte = "inferencia_ia" if rqe_mencionado else ("cnes" if encontrado_cnes else None)

    if cnes is not None:
        lead.cnes_codigo = cnes.get("cnes_codigo")

    if google_places is not None:
        lead.google_rating = google_places.get("rating")
        lead.google_avaliacoes = google_places.get("avaliacoes")
        # ⚠️ Corrigido em 23/08/2026. `search_google_places` sempre
        # devolveu `telefone_google`, mas NADA lia esse valor -- a única
        # atribuição a `lead.telefone` no arquivo inteiro era a do bloco
        # `whatsapp` abaixo, que por sua vez só roda se já houver
        # telefone. Circular: o telefone só seria gravado pela etapa que
        # precisa dele pra rodar. Resultado nos 6 leads da primeira busca
        # real: `telefone` e `whatsapp_ativo` nulos em todos.
        #
        # `or lead.telefone` preserva o que já estava lá quando o Google
        # não devolve telefone nesta rodada -- mesma regra de "etapa que
        # não rodou não apaga dado de rodada anterior" do resto da função.
        lead.telefone = google_places.get("telefone_google") or lead.telefone

    if site_url is not None:
        # Independente de site_firecrawl ter rodado -- site_url pode ter
        # vindo só do Google Places (ex.: Firecrawl pulado por falta de
        # FIRECRAWL_API_KEY, mas o site em si já é conhecido).
        lead.site = site_url

    if site_firecrawl is not None:
        lead.instagram = site_firecrawl.get("instagram")
        servicos = site_firecrawl.get("servicos") or []
        lead.servicos = ", ".join(servicos) if servicos else None

    if whatsapp is not None:
        # Roda DEPOIS do bloco do Google Places de propósito: quando a
        # validação aconteceu, o número normalizado pela Evolution
        # (`numero_formatado`, com código do país) é melhor que o formato
        # cru do Google -- ver app/services/whatsapp.py.
        lead.telefone = (
            whatsapp.get("numero_formatado") or whatsapp.get("telefone_original") or lead.telefone
        )
        lead.whatsapp_ativo = whatsapp.get("whatsapp_ativo")

    if email is not None:
        lead.email = email.get("email")
        lead.email_validado = email.get("email_aprovado")

    db.flush()
    return lead


@celery_app.task(name="process_lead_pipeline")
def process_lead_pipeline(
    cnpj: str,
    razao_social: str | None = None,
    site_url: str | None = None,
    telefone: str | None = None,
    db=None,
    cache_cnes: dict | None = None,
) -> dict:
    """
    Orquestra o pipeline de enriquecimento completo pra um CNPJ: dispara
    cada etapa na ordem que respeita as dependências reais entre elas
    (ver o diagrama de dependências logo acima deste bloco), junta os
    resultados, chama `compute_lead_score` e persiste tudo num `Lead`
    (upsert por CNPJ, ver `_persistir_enriquecimento_lead`).

    `cnpj` é o único parâmetro obrigatório — corresponde ao "busca o
    CNPJ na semente já gerada" do pedido original: quem chama essa task
    já rodou `gerar_semente_leads` e está iterando sobre os CNPJs
    encontrados, um por vez (essa task não faz esse loop sozinha).
    `razao_social` é opcional, mas ajuda a não criar o Lead com o CNPJ
    como razão social provisória se `enrich_receita_federal` falhar.

    `site_url` é opcional: desde 20/08/2026 `search_google_places` está
    implementada e é a fonte automática dele (o `site` que o Places
    devolve). Passar explicitamente ainda funciona e tem precedência.
    `telefone` continua sem nenhuma fonte automática no pipeline — sem
    ele, `validate_whatsapp` é pulada com aviso no log.

    `cache_cnes` é um dict compartilhado entre várias chamadas desta task
    dentro de uma MESMA execução de busca, pra que leads do mesmo
    município não repitam a paginação inteira do CNES — ver a explicação
    no docstring de `enrich_cnes`. `None` (padrão) desliga o cache.

    Cada etapa roda isolada via `_rodar_etapa` — uma falha (exceção) numa
    etapa não derruba as outras nem a persistência do que já funcionou;
    só fica registrada no log e a etapa correspondente conta como "sem
    sinal" pro score.

    Devolve um resumo: CNPJ, id do Lead persistido, quais etapas
    realmente rodaram (`etapas_executadas`), quais foram puladas e por
    quê (`etapas_puladas`), o score total e o breakdown.
    """
    enriquecimentos: dict = {}
    etapas_puladas: dict[str, str] = {}

    # ETAPA 1 -- Receita Federal: base pra quase tudo que vem depois
    # (município/UF pra Zona Franca, código do município pro CNES,
    # decisor pro e-mail). Roda primeiro de propósito.
    receita_federal = _rodar_etapa("enrich_receita_federal", lambda: enrich_receita_federal(cnpj))
    if receita_federal is not None:
        enriquecimentos["enrich_receita_federal"] = receita_federal

    # ETAPA 2 -- PGFN: independente, só precisa do CNPJ. Pode rodar a
    # qualquer momento em relação à Receita Federal; mantida em sequência
    # aqui por simplicidade (sem paralelismo real via Celery group nesta
    # sessão).
    pgfn = _rodar_etapa("enrich_pgfn_divida_ativa", lambda: enrich_pgfn_divida_ativa(cnpj))
    if pgfn is not None:
        enriquecimentos["enrich_pgfn_divida_ativa"] = pgfn

    # ETAPA 3 -- Zona Franca: depende de município/UF vindos da Receita
    # Federal (a semente de gerar_semente_leads só tem UF, não município).
    municipio = (receita_federal or {}).get("municipio")
    uf = (receita_federal or {}).get("uf")
    if municipio and uf:
        zona_franca = _rodar_etapa("enrich_zona_franca", lambda: enrich_zona_franca(municipio, uf))
        if zona_franca is not None:
            enriquecimentos["enrich_zona_franca"] = zona_franca
    else:
        motivo = "sem município/UF (enrich_receita_federal não encontrou o CNPJ ou falhou)"
        logger.warning("Pulando enrich_zona_franca pro CNPJ %s: %s", cnpj, motivo)
        etapas_puladas["enrich_zona_franca"] = motivo

    # ETAPA 4 -- CNES: depende do codigo_municipio_cnes vindo da Receita
    # Federal -- dependência explícita, não dá pra rodar antes da ETAPA 1.
    codigo_municipio_cnes = (receita_federal or {}).get("codigo_municipio_cnes")
    if codigo_municipio_cnes:
        cnes = _rodar_etapa(
            "enrich_cnes",
            lambda: enrich_cnes(cnpj, codigo_municipio_cnes, cache_municipios=cache_cnes),
        )
        if cnes is not None:
            enriquecimentos["enrich_cnes"] = cnes
    else:
        motivo = "sem codigo_municipio_cnes (depende de enrich_receita_federal ter encontrado o CNPJ e resolvido o código IBGE do município)"
        logger.warning("Pulando enrich_cnes pro CNPJ %s: %s", cnpj, motivo)
        etapas_puladas["enrich_cnes"] = motivo

    # ETAPA 5 -- Google Places: implementada em 20/08/2026 (ver
    # search_google_places acima e app/services/google_places.py). Busca
    # pelo nome que já tivermos (razão social preferida, com fallback pra
    # nome_fantasia -- mesma lógica de nome_busca_google_places) + UF, se
    # a Receita Federal já resolveu. Como o resultado inclui `site`, essa
    # etapa também é a fonte automática de `site_url` pras etapas
    # seguintes quando o chamador não passou uma explicitamente -- fecha
    # parte do gap que antes só dava pra contornar passando o parâmetro
    # na mão.
    site_url_efetivo = site_url
    google_places_resultado = None
    estabelecimento_busca = {
        "cnpj": cnpj,
        "razao_social": razao_social or (receita_federal or {}).get("razao_social"),
        "nome_fantasia": (receita_federal or {}).get("nome_fantasia"),
        "uf": (receita_federal or {}).get("uf"),
    }
    nome_busca = nome_busca_google_places(estabelecimento_busca)
    if nome_busca:
        google_places_resultado = _rodar_etapa(
            "search_google_places",
            lambda: search_google_places([estabelecimento_busca])[0],
        )
        if google_places_resultado is not None:
            enriquecimentos["search_google_places"] = google_places_resultado
            if not site_url_efetivo and google_places_resultado.get("site"):
                site_url_efetivo = google_places_resultado["site"]
    else:
        motivo = "sem nome (razão social/nome fantasia) pra buscar no Google Places"
        logger.warning("Pulando search_google_places pro CNPJ %s: %s", cnpj, motivo)
        etapas_puladas["search_google_places"] = motivo

    # ETAPA 6 -- Firecrawl + IA: precisa de site_url (da ETAPA 5 ou
    # passada explicitamente) e de FIRECRAWL_API_KEY configurada.
    if not site_url_efetivo:
        motivo = "sem site_url (nem do Google Places, nem passada explicitamente)"
        logger.warning("Pulando enrich_site_firecrawl pro CNPJ %s: %s", cnpj, motivo)
        etapas_puladas["enrich_site_firecrawl"] = motivo
        site_firecrawl = None
    elif not settings.FIRECRAWL_API_KEY:
        motivo = "FIRECRAWL_API_KEY não configurada"
        logger.warning("Pulando enrich_site_firecrawl pro CNPJ %s: %s", cnpj, motivo)
        etapas_puladas["enrich_site_firecrawl"] = motivo
        site_firecrawl = None
    else:
        site_firecrawl = _rodar_etapa("enrich_site_firecrawl", lambda: enrich_site_firecrawl(site_url_efetivo))
        if site_firecrawl is not None:
            enriquecimentos["enrich_site_firecrawl"] = site_firecrawl

    # ETAPA 7 -- WhatsApp: precisa de telefone.
    #
    # ⚠️ Corrigido em 23/08/2026. O telefone agora sai do Google Places
    # (ETAPA 5, que roda antes -- `telefone_google`), exatamente como
    # `site_url_efetivo` já fazia com o `site`. Antes disso NENHUMA etapa
    # fornecia telefone, então esta aqui era sempre pulada e
    # `Lead.telefone`/`Lead.whatsapp_ativo` ficavam nulos em 100% dos
    # leads -- confirmado nos 6 da primeira busca real.
    #
    # Um `telefone` explícito do chamador continua vencendo o do Google,
    # mesma precedência de `site_url`.
    telefone_efetivo = telefone or (google_places_resultado or {}).get("telefone_google")
    if not telefone_efetivo:
        motivo = "sem telefone (Google Places não devolveu telefone e nenhum foi passado explicitamente)"
        logger.warning("Pulando validate_whatsapp pro CNPJ %s: %s", cnpj, motivo)
        etapas_puladas["validate_whatsapp"] = motivo
    else:
        whatsapp = _rodar_etapa("validate_whatsapp", lambda: validate_whatsapp(telefone_efetivo))
        if whatsapp is not None:
            enriquecimentos["validate_whatsapp"] = whatsapp

    # ETAPA 8 -- E-mail: precisa do domínio (extraído de site_url_efetivo).
    # decisor_nome (da Receita Federal, ETAPA 1) é opcional -- enrich_email
    # já aceita None (busca só por domínio nesse caso), então essa etapa
    # não é pulada por falta de decisor, só por falta de domínio.
    dominio = _extrair_dominio(site_url_efetivo) if site_url_efetivo else None
    decisor_nome = (receita_federal or {}).get("decisor_nome")
    if not dominio:
        motivo = "sem domínio (depende de site_url, que não está disponível)"
        logger.warning("Pulando enrich_email pro CNPJ %s: %s", cnpj, motivo)
        etapas_puladas["enrich_email"] = motivo
    else:
        email = _rodar_etapa("enrich_email", lambda: enrich_email(dominio, decisor_nome))
        if email is not None:
            enriquecimentos["enrich_email"] = email

    # ETAPA 9 -- persistência dos dados brutos (upsert por CNPJ)
    db_session, owns_session = _get_db_session(db)
    try:
        lead = _persistir_enriquecimento_lead(
            db_session,
            cnpj=cnpj,
            razao_social=razao_social,
            receita_federal=enriquecimentos.get("enrich_receita_federal"),
            pgfn=enriquecimentos.get("enrich_pgfn_divida_ativa"),
            zona_franca=enriquecimentos.get("enrich_zona_franca"),
            cnes=enriquecimentos.get("enrich_cnes"),
            google_places=enriquecimentos.get("search_google_places"),
            site_firecrawl=enriquecimentos.get("enrich_site_firecrawl"),
            site_url=site_url_efetivo,
            whatsapp=enriquecimentos.get("validate_whatsapp"),
            email=enriquecimentos.get("enrich_email"),
        )

        # ETAPA 10 -- score final, já com o lead_id real (o Lead pode ter
        # acabado de ser criado na etapa anterior)
        from app.scoring.compute_lead_score import derivar_prioridade

        resultado_score = compute_lead_score(lead.id, enriquecimentos)

        lead.score_total = resultado_score["score_total"]
        lead.score_detalhes = {"breakdown": resultado_score["breakdown"]}
        # 🔴 CORTES PROVISÓRIOS, NÃO VALIDADOS COM O DOUGLAS -- ver
        # app/scoring/compute_lead_score.py::derivar_prioridade.
        # Fica aqui, e não dentro de `_persistir_enriquecimento_lead`,
        # porque a prioridade deriva do `score_total`, que só existe
        # depois de `compute_lead_score` rodar -- é a mesma segunda
        # passada que já grava score_total/score_detalhes.
        lead.prioridade = derivar_prioridade(resultado_score["score_total"])
        db_session.commit()
        db_session.refresh(lead)

        return {
            "cnpj": cnpj,
            "lead_id": lead.id,
            "etapas_executadas": list(enriquecimentos.keys()),
            "etapas_puladas": etapas_puladas,
            "score_total": resultado_score["score_total"],
            "prioridade": lead.prioridade,
            "breakdown": resultado_score["breakdown"],
        }
    finally:
        if owns_session:
            db_session.close()


# ---------------------------------------------------------------------------
# executar_busca_mensal — orquestra uma "busca mensal" completa (painel admin)
# ---------------------------------------------------------------------------
#
# Diferente de process_lead_pipeline (que roda 1 CNPJ e é chamada num
# loop por quem quer que itere sobre a semente), esta task É o loop --
# roda gerar_semente_leads uma vez, depois process_lead_pipeline pra
# CADA CNPJ encontrado, e persiste o progresso/resultado num registro
# BuscaLeads (app/models/busca_leads.py) ao longo do caminho, pra
# app/api/routes/admin.py::get_busca poder devolver o status via
# polling do frontend enquanto ela roda.
#
# ⚠️ CUSTO REAL: cada CNPJ na semente dispara todo o pipeline de
# enriquecimento (Receita Federal, PGFN, CNES, Google Places, Firecrawl+IA,
# WhatsApp, Hunter.io/ZeroBounce -- ver o docstring do módulo, topo deste
# arquivo) -- não é uma simulação. Só admins disparam isso
# (app/api/deps.py::require_admin), por isso o painel existe fora do
# fluxo normal de qualquer usuário "client".

_CRITERIO_ERRO_TAXA_FALHA = 0.5  # >50% dos CNPJs SELECIONADOS falharam -> status "erro"

# Abaixo de 10% da cota, o universo da semente é suspeito de estar
# incompleto -- ver `_aviso_universo_suspeito`.
_LIMIAR_UNIVERSO_SUSPEITO = 0.10


def _aviso_universo_suspeito(total_encontrado: int, top_n: int, diretorio: str) -> str | None:
    """
    Devolve um aviso se a semente trouxe CNPJs demais de menos, ou None se
    o volume parece normal.

    O nicho é nacional (CNAE 8630 em todo o Brasil), então o universo
    esperado é de centenas de milhares. Um punhado de CNPJs quase nunca
    significa "o nicho tem poucas empresas" -- significa arquivo faltando,
    arquivo mal nomeado, ou só uma fatia dos .zip na pasta.

    Esse cenário era INDISTINGUÍVEL de uma busca legítima até 23/08/2026:
    a busca concluía "com sucesso" trazendo quase nada e nada na tela
    explicava por quê. O caso mais grave (só .zip na pasta, todos
    ignorados por glob case-sensitive) foi eliminado na mesma data, quando
    os finders passaram a reconhecer os dois formatos -- este aviso cobre
    o que sobrou: fatia faltando, arquivo renomeado, download parcial.

    O limiar é 10% da cota (com a cota padrão de 60, dispara abaixo de 6).
    É proposital que seja bem baixo: o objetivo é pegar "claramente algo
    errado", não opinar sobre um universo apenas menor que o esperado.
    """
    limite = top_n * _LIMIAR_UNIVERSO_SUSPEITO
    if total_encontrado >= limite:
        return None
    return (
        f"Volume suspeito: a semente trouxe apenas {total_encontrado} CNPJs, menos de "
        f"{_LIMIAR_UNIVERSO_SUSPEITO:.0%} da cota de {top_n}. Para um filtro NACIONAL de CNAE 8630 "
        f"isso quase certamente indica arquivo da Receita ausente, incompleto ou mal nomeado em "
        f"{diretorio} -- e não que o nicho tem poucas empresas. Confira se os arquivos "
        f"Estabelecimentos*.zip (ou os CSVs *ESTABELE*) estão todos lá."
    )


def _top_n_pre_selecao() -> int:
    """
    Quantos candidatos entram no enriquecimento pago:
    `ceil(LEADS_POR_BUSCA * LEADS_MARGEM_PRE_SELECAO)` -> 60 no padrão
    (50 leads contratados + 20% de margem). Ver o comentário das duas
    variáveis em app/core/config.py pro racional da margem.
    """
    return math.ceil(settings.LEADS_POR_BUSCA * settings.LEADS_MARGEM_PRE_SELECAO)


def _coletar_sinais_gratuitos(
    estabelecimentos: list[dict],
    redis_client=None,
    municipios_por_codigo: dict[str, str] | None = None,
) -> list[dict]:
    """
    Anexa a cada estabelecimento da semente os 3 sinais que dá pra obter
    sem gastar nenhuma chamada de API paga, pra que
    `selecionar_melhores_candidatos` possa ranquear o universo inteiro de
    graça (ver app/scoring/pre_selecao.py).

    Sinais anexados:
      - `qtd_inscricoes_ativas` — um único MGET em lote no índice PGFN do
        Redis (`lookup_debts_from_redis`), não um GET por CNPJ: o
        universo tem ordem de centenas de milhares de itens e um
        round-trip por item levaria dezenas de minutos só nisso.
      - `zona_franca` — resolve o código RFB do município pro nome via
        `municipios_por_codigo` e compara contra a lista da Suframa.
      - `situacao_ativa` — comparação local do código de situação
        cadastral que já veio no arquivo bulk.

    É a metade "com I/O" da pré-seleção; a pontuação em si é pura e mora
    em app/scoring/pre_selecao.py. Mesma divisão de
    `process_lead_pipeline` (I/O) vs. `montar_score_lead` (puro).

    `municipios_por_codigo` vazio/None faz `zona_franca` ser False pra
    todo mundo — degradação consciente, sinalizada por quem chama (ver
    `executar_busca_mensal`), não silenciosa.
    """
    import redis as redis_lib

    from app.scoring.zona_franca import is_zona_franca
    from app.services.pgfn import lookup_debts_from_redis
    from app.services.receita_federal_bulk import SITUACAO_CADASTRAL_ATIVA

    if redis_client is None:
        redis_client = redis_lib.from_url(settings.REDIS_URL)

    cnpjs = [e.get("cnpj") or "" for e in estabelecimentos]
    dividas = lookup_debts_from_redis(cnpjs, redis_client)

    municipios = municipios_por_codigo or {}
    candidatos: list[dict] = []
    for estabelecimento in estabelecimentos:
        cnpj = estabelecimento.get("cnpj") or ""
        divida = dividas.get(cnpj)

        codigo_municipio = (estabelecimento.get("municipio_codigo_rfb") or "").strip()
        nome_municipio = municipios.get(codigo_municipio)
        uf = estabelecimento.get("uf") or ""
        zona_franca = bool(nome_municipio) and is_zona_franca(nome_municipio, uf)

        situacao = (estabelecimento.get("situacao_cadastral") or "").strip()

        candidatos.append(
            {
                **estabelecimento,
                "qtd_inscricoes_ativas": divida.qtd_inscricoes_ativas if divida else 0,
                "zona_franca": zona_franca,
                "situacao_ativa": situacao.zfill(2) == SITUACAO_CADASTRAL_ATIVA,
            }
        )

    return candidatos


def _carregar_municipios_rfb() -> dict[str, str]:
    """
    Índice {código RFB: nome do município}, lido da tabela de domínio
    MUNICCSV baixada junto com os outros arquivos da Receita.

    Devolve dict vazio se o arquivo não estiver na pasta -- não levanta.
    Quem chama decide o que fazer com o vazio (ver
    `executar_busca_mensal`, que registra a degradação em vez de
    escondê-la).
    """
    from app.services.receita_federal_bulk import build_municipios_index, find_municipios_rfb_files

    paths = find_municipios_rfb_files(settings.RECEITA_FEDERAL_DADOS_ABERTOS_DIR)
    if not paths:
        return {}
    return build_municipios_index(paths)


def _indice_pgfn_populado(redis_client) -> bool:
    """
    Confirma que `refresh_pgfn_index` já rodou alguma vez, checando a
    chave de carimbo de data que ele grava (`pgfn:index_updated_at`).

    Guarda de CUSTO, não de correção. Se o índice nunca foi populado,
    todo CNPJ do universo devolve "sem dívida", os candidatos empatam em
    5 pontos (ou 25, com Zona Franca) e a seleção dos 60 vira, na
    prática, "os 60 primeiros por ordem de CNPJ" -- ou seja, gastaria
    dinheiro de verdade num conjunto praticamente aleatório, sem nada na
    tela indicando que o ranqueamento não aconteceu. É indistinguível,
    olhando só o resultado, de uma busca legítima em que ninguém tem
    dívida ativa.

    Por isso `executar_busca_mensal` ABORTA quando isto devolve False,
    em vez de degradar. Foi uma decisão desta sessão, não um pedido
    explícito: dado que a premissa inteira da pré-seleção é ranquear por
    PGFN, rodar sem o índice contradiz o motivo de ela existir.
    """
    from app.services.pgfn import REDIS_KEY_UPDATED_AT

    return redis_client.get(REDIS_KEY_UPDATED_AT) is not None


@celery_app.task(name="executar_busca_mensal")
def executar_busca_mensal(busca_id: str, db=None) -> dict:
    """
    Orquestra uma busca mensal completa, em DUAS fases separadas por
    custo:

      FASE 1 -- de graça, sobre o universo inteiro:
        `gerar_semente_leads()` traz todo CNPJ ativo de CNAE 8630 do país
        (ordem de centenas de milhares). `_coletar_sinais_gratuitos`
        anexa PGFN (MGET em lote no Redis), Zona Franca (lista estática)
        e situação cadastral (já vem no arquivo). Nada disso custa
        chamada de API paga.

      FASE 2 -- pago, só nos selecionados:
        `selecionar_melhores_candidatos` ranqueia por score preliminar e
        corta nos `_top_n_pre_selecao()` melhores (60 no padrão).
        `process_lead_pipeline` roda SÓ nesses -- é aqui que Google
        Places, Firecrawl, Anthropic, Hunter.io e ZeroBounce são
        acionados.

    ⚠️ Antes de 2026-08-22 não existia a fase 1: o loop chamava
    `process_lead_pipeline` pra TODO CNPJ da semente. O custo era
    proporcional ao universo, não ao plano contratado. Se alguém for
    mexer neste fluxo, a invariante a preservar é: **nenhuma etapa paga
    roda antes do corte do top N**.

    Resiliência, em três níveis aninhados:
      - `_rodar_etapa` (dentro de `process_lead_pipeline`) -- uma ETAPA
        que falha não derruba as outras etapas daquele CNPJ.
      - o loop aqui -- um CNPJ que falha inteiro não derruba os outros;
        vira uma string em `busca.erros`.
      - `busca.status` só vira "erro" se mais de
        `_CRITERIO_ERRO_TAXA_FALHA` (50%) falharam. A taxa é calculada
        sobre os SELECIONADOS, não sobre o universo -- senão 60
        processados de 300 mil encontrados leria como 99,98% de falha.

    Casos que abortam ANTES de gastar dinheiro (status "erro", nenhum
    `process_lead_pipeline` chamado):
      - `gerar_semente_leads()` levantando exceção;
      - o índice PGFN nunca ter sido populado (ver
        `_indice_pgfn_populado` pro racional);
      - falha na coleta dos sinais gratuitos (ex.: Redis fora do ar).

    Semente vazia ou "sem_arquivos" continua sendo "concluído" com zero
    CNPJs -- não há CNPJ que tenha falhado, e não se gastou nada.
    """
    from app.models.busca_leads import BuscaLeads, StatusBusca
    from app.scoring.pre_selecao import resumir_composicao, selecionar_melhores_candidatos

    db_session, owns_session = _get_db_session(db)
    try:
        busca = db_session.get(BuscaLeads, busca_id)
        if busca is None:
            logger.error("executar_busca_mensal: BuscaLeads %s não encontrada -- abortando.", busca_id)
            return {"status": "erro", "motivo": "busca não encontrada"}

        busca.status = StatusBusca.EXECUTANDO.value
        db_session.commit()

        def _abortar(mensagem: str) -> dict:
            """Marca a busca como erro sem ter gasto nada e devolve o resumo."""
            logger.error("executar_busca_mensal (%s): %s", busca_id, mensagem)
            busca.status = StatusBusca.ERRO.value
            busca.erros = [mensagem]
            busca.concluido_em = agora_utc()
            db_session.commit()
            return {"busca_id": busca_id, "status": busca.status, "erros": busca.erros}

        # --- FASE 1a: universo ------------------------------------------------
        try:
            semente = gerar_semente_leads()
        except Exception as exc:
            logger.exception("executar_busca_mensal: gerar_semente_leads falhou pra busca %s.", busca_id)
            return _abortar(f"gerar_semente_leads: {exc}")

        estabelecimentos = semente.get("estabelecimentos") or []
        busca.total_cnpjs_encontrados = len(estabelecimentos)
        db_session.commit()

        # Avisos de degradação -- não são erros, mas ficam em `busca.erros`
        # pra aparecerem no painel admin em vez de só no log do container.
        avisos: list[str] = []
        top_n = _top_n_pre_selecao()
        aviso_volume = _aviso_universo_suspeito(
            len(estabelecimentos), top_n, settings.RECEITA_FEDERAL_DADOS_ABERTOS_DIR
        )
        if aviso_volume:
            logger.warning("executar_busca_mensal (%s): %s", busca_id, aviso_volume)
            avisos.append(aviso_volume)

        if not estabelecimentos:
            busca.status = StatusBusca.CONCLUIDO.value
            busca.total_cnpjs_selecionados = 0
            busca.total_leads_processados = 0
            busca.erros = avisos
            busca.concluido_em = agora_utc()
            db_session.commit()
            logger.warning(
                "executar_busca_mensal (%s): semente vazia (status=%s) -- nada a processar.",
                busca_id,
                semente.get("status"),
            )
            return {
                "busca_id": busca_id,
                "status": busca.status,
                "total_cnpjs_encontrados": 0,
                "total_cnpjs_selecionados": 0,
                "total_leads_processados": 0,
                "erros": avisos,
            }

        # --- FASE 1b: sinais gratuitos + pré-seleção --------------------------
        import redis as redis_lib

        try:
            redis_client = redis_lib.from_url(settings.REDIS_URL)
            if not _indice_pgfn_populado(redis_client):
                return _abortar(
                    "Índice da PGFN nunca foi populado (chave pgfn:index_updated_at ausente no Redis). "
                    "Rode a task refresh_pgfn_index antes da busca -- sem ele a pré-seleção não consegue "
                    "ranquear os candidatos e a busca gastaria API num conjunto praticamente aleatório."
                )

            municipios_por_codigo = _carregar_municipios_rfb()
            candidatos = _coletar_sinais_gratuitos(
                estabelecimentos,
                redis_client=redis_client,
                municipios_por_codigo=municipios_por_codigo,
            )
        except Exception as exc:
            logger.exception("executar_busca_mensal: coleta de sinais gratuitos falhou pra busca %s.", busca_id)
            return _abortar(f"pré-seleção (sinais gratuitos): {exc}")

        if not municipios_por_codigo:
            # Degradação real, registrada onde o admin vê. Sem a tabela de
            # municípios não dá pra saber quem está em Zona Franca antes de
            # gastar, e o ranking passa a considerar só PGFN.
            aviso = (
                "Tabela de municípios da Receita (*MUNICCSV*) não encontrada em "
                f"{settings.RECEITA_FEDERAL_DADOS_ABERTOS_DIR} -- o critério Zona Franca "
                "NÃO participou da pré-seleção desta busca (ranking só por dívida ativa PGFN)."
            )
            logger.warning("executar_busca_mensal (%s): %s", busca_id, aviso)
            avisos.append(aviso)

        selecionados = selecionar_melhores_candidatos(candidatos, top_n)
        busca.total_cnpjs_selecionados = len(selecionados)
        db_session.commit()

        composicao = resumir_composicao(selecionados)
        logger.info(
            "executar_busca_mensal (%s): %s candidatos no universo -> %s selecionados (cota %s). "
            "FASE 1: %s por dívida ativa (de %s a %s inscrições). "
            "FASE 2: %s para completar cota, sem dívida. "
            "%s em Zona Franca. Score preliminar de %.1f a %.1f.",
            busca_id,
            len(candidatos),
            len(selecionados),
            top_n,
            composicao["por_divida_ativa"],
            composicao["inscricoes_pgfn_min"],
            composicao["inscricoes_pgfn_max"],
            composicao["para_completar_cota"],
            composicao["com_zona_franca"],
            composicao["score_preliminar_min"],
            composicao["score_preliminar_max"],
        )
        if composicao["para_completar_cota"] > 0:
            # Não é erro, mas é o sinal de que o universo com dívida ativa
            # -- o critério de venda principal do Douglas -- se esgotou
            # antes de encher a cota. Vale saber: ou o filtro está estreito
            # demais, ou o índice da PGFN está desatualizado.
            logger.warning(
                "executar_busca_mensal (%s): só %s candidatos com dívida ativa disponíveis; "
                "%s vagas preenchidas com candidatos SEM dívida.",
                busca_id,
                composicao["por_divida_ativa"],
                composicao["para_completar_cota"],
            )

        # --- FASE 2: enriquecimento pago, só nos selecionados -----------------
        # Cache de município do CNES compartilhado por TODA esta execução e
        # por mais nenhuma: leads da mesma cidade não repetem a paginação
        # (até 500 requisições por município), e a próxima busca começa com
        # cache limpo, sem reaproveitar cadastro do CNES de um mês atrás.
        # Ver o docstring de `enrich_cnes`.
        cache_cnes: dict = {}

        erros: list[str] = []
        total_processados = 0
        for candidato in selecionados:
            cnpj = candidato.get("cnpj")
            try:
                process_lead_pipeline(
                    cnpj,
                    razao_social=candidato.get("razao_social"),
                    cache_cnes=cache_cnes,
                )
                total_processados += 1
            except Exception as exc:
                logger.exception("executar_busca_mensal: CNPJ %s falhou na busca %s.", cnpj, busca_id)
                erros.append(f"{cnpj}: {exc}")

        taxa_falha = len(erros) / len(selecionados) if selecionados else 0.0
        busca.status = StatusBusca.ERRO.value if taxa_falha > _CRITERIO_ERRO_TAXA_FALHA else StatusBusca.CONCLUIDO.value
        busca.total_leads_processados = total_processados
        busca.erros = avisos + erros
        busca.concluido_em = agora_utc()
        db_session.commit()

        return {
            "busca_id": busca_id,
            "status": busca.status,
            "total_cnpjs_encontrados": busca.total_cnpjs_encontrados,
            "total_cnpjs_selecionados": busca.total_cnpjs_selecionados,
            "total_leads_processados": busca.total_leads_processados,
            "composicao_selecao": composicao,
            "municipios_cnes_baixados": len(cache_cnes),
            "erros": busca.erros,
        }
    finally:
        if owns_session:
            db_session.close()
