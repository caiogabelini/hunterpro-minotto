"""
Celery app — HunterPro Minotto Contabilidade.

Cada etapa do pipeline de busca/enriquecimento vira uma task. A ordem
de execução por lead é a mesma definida no fluxo de execução
apresentado no kickoff (documento 03_Fluxo_Execucao.html):

  1. search_google_places      → busca inicial por nicho + região
  2. enrich_receita_federal    → CNAE, sócios, porte, anos de mercado
  3. enrich_pgfn_divida_ativa  → cruzamento por CNPJ (sem contato, só sinal)
  4. enrich_zona_franca        → lookup estático de município (sem custo externo)
  5. enrich_cnes               → especialidade oficial de saúde
  6. enrich_site_firecrawl     → varredura de site + IA (serviços, RQE inferido)
  7. enrich_instagram          → localizado a partir do site
  8. enrich_linkedin           → busca por razão social/decisor
  9. validate_whatsapp         → Evolution API
  10. enrich_email             → Hunter.io + validação MX/ZeroBounce
  11. compute_lead_score       → aplica app/scoring/rules.py

Cada task abaixo é um placeholder — implementação entra sessão a
sessão, começando pelas fontes específicas da Minotto (PGFN, Zona
Franca, CNES) por serem a prioridade combinada com o cliente.
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
    """TODO: CNAE, sócios, porte, anos de mercado, situação cadastral."""
    raise NotImplementedError("Implementar na próxima sessão.")


@celery_app.task(name="enrich_pgfn_divida_ativa")
def enrich_pgfn_divida_ativa(cnpj: str) -> dict:
    """
    TODO: cruzar CNPJ com o CSV trimestral de dados abertos da PGFN.

    Retorna nº de inscrições ativas (não retorna contato — ver
    app/scoring/rules.py para a fórmula de pontuação, ainda a validar
    com o cliente).
    """
    raise NotImplementedError("Implementar na próxima sessão.")


@celery_app.task(name="enrich_zona_franca")
def enrich_zona_franca(municipio: str, uf: str) -> bool:
    """Lookup estático — já implementado em app/scoring/zona_franca.py."""
    from app.scoring.zona_franca import is_zona_franca

    return is_zona_franca(municipio, uf)


@celery_app.task(name="enrich_cnes")
def enrich_cnes(cnpj: str) -> dict:
    """TODO: consultar API do CNES para especialidade oficial de saúde."""
    raise NotImplementedError("Implementar na próxima sessão.")


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
