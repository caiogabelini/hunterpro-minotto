"""
Configuração central do HunterPro — Minotto Contabilidade.

Todas as variáveis sensíveis (chaves de API, credenciais de banco) vêm
de variáveis de ambiente — nunca hardcode aqui. Ver `.env.example`.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Identidade do projeto ---
    PROJECT_NAME: str = "HunterPro — Minotto Contabilidade"
    ENVIRONMENT: str = "development"  # development | staging | production

    # --- Banco de dados ---
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/hunterpro_minotto"

    # --- Redis / Celery ---
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # --- Auth ---
    SECRET_KEY: str = "changeme-in-env"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12  # 12h

    # --- APIs de enriquecimento (mesmo stack validado no projeto SR Contábil) ---
    GOOGLE_API_KEY: str = ""
    # Quantas vezes cada lead pode ter CADA tipo de geração de IA
    # (e-mail, WhatsApp, insights) antes de exigir liberação do admin --
    # ver app/api/routes/limites_ia.py. Controle de custo: cada geração é
    # uma chamada paga à Anthropic, e a tela vai ficar acessível a
    # usuários "client".
    #
    # `0` (ou negativo) DESLIGA o limite em vez de bloquear tudo -- o
    # sentido do erro numa configuração zerada por engano é liberar, não
    # travar o produto inteiro.
    LIMITE_GERACOES_IA_POR_LEAD: int = 2

    HUNTER_API_KEY: str = ""
    # 🔴 DESLIGADO POR PADRÃO -- LIGAR SÓ DEPOIS DE FAZER UPGRADE DO PLANO.
    #
    # Quando `True`, `enriquecer_email` cai pro endpoint `domain-search`
    # do Hunter se o `email-finder` não achar o e-mail do decisor (ver
    # app/services/email_enrichment.py::buscar_emails_do_dominio).
    #
    # ⚠️ CONTA DE CONSUMO -- é por isso que o default é False:
    #   plano Hunter atual = Free, 50 buscas/mês
    #   cota do produto    = 50 leads/mês (settings.PLANO_LEADS_MES)
    #   sem fallback       = até 1 chamada por lead  -> até  50/mês (no limite exato)
    #   com fallback       = até 2 chamadas por lead -> até 100/mês (2x o limite)
    #
    # Ou seja, mesmo DESLIGADO o plano Free já está no limite exato. Na
    # amostra real de 23/08/2026 o `email-finder` falhou em 4 de 5
    # domínios, então o consumo com fallback ficaria perto do teto de 2x,
    # não perto de 1x.
    HUNTER_DOMAIN_SEARCH_FALLBACK: bool = False
    ZEROBOUNCE_API_KEY: str = ""
    SERP_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    FIRECRAWL_API_KEY: str = ""

    EVOLUTION_URL: str = ""
    EVOLUTION_KEY: str = ""
    EVOLUTION_INSTANCE: str = ""

    # --- Fontes específicas do nicho de saúde (Minotto) ---
    # Caminho local onde o CSV trimestral de dados abertos da PGFN fica armazenado
    # após download manual/agendado. Ver app/scoring/pgfn.py (próxima sessão).
    PGFN_DADOS_ABERTOS_DIR: str = "./data/pgfn"

    # Base URL da API pública do CNES. Endpoint confirmado em 19/08/2026
    # (GET {base}/cnes/estabelecimentos) — ver app/services/cnes.py para as
    # limitações da API já validadas (sem filtro por CNPJ, paginação com
    # página menor que o `limit` pedido, sem campo de especialidade).
    CNES_API_BASE_URL: str = "https://apidadosabertos.saude.gov.br"

    # Base URL da BrasilAPI, usada pro enriquecimento via Receita Federal
    # (CNAE, sócios, porte, situação cadastral). Espelha os Dados Abertos
    # do CNPJ da própria Receita — pública, sem autenticação. Ver
    # app/services/receita_federal.py.
    RECEITA_FEDERAL_API_BASE_URL: str = "https://brasilapi.com.br"

    # Base URL da API de Localidades do IBGE, usada só pra resolver
    # município + UF -> código IBGE (necessário pro codigo_municipio do
    # enrich_cnes). Ver app/services/ibge_municipios.py.
    IBGE_API_BASE_URL: str = "https://servicodados.ibge.gov.br"

    # Caminho local onde os arquivos ESTABELECIMENTOS e EMPRESAS dos
    # Dados Abertos do CNPJ da Receita Federal ficam armazenados após
    # download manual (mesmo padrão do PGFN_DADOS_ABERTOS_DIR) — a
    # Receita publica os dois tipos de arquivo juntos, no mesmo lote
    # mensal, então usam a mesma pasta. Ver
    # app/services/receita_federal_bulk.py.
    RECEITA_FEDERAL_DADOS_ABERTOS_DIR: str = "./data/receita_federal"

    # --- CORS ---
    FRONTEND_ORIGIN: str = "http://localhost:5173"

    # --- Pré-seleção de leads por busca ---
    # Quantos leads a busca mensal deve efetivamente ENTREGAR. É o número
    # que importa pro cliente e o mesmo que o dashboard mostra em
    # "Consumo do Plano" (PLANO_LEADS_MES abaixo).
    LEADS_POR_BUSCA: int = 50

    # Margem de segurança: o pipeline completo roda em
    # ceil(LEADS_POR_BUSCA * LEADS_MARGEM_PRE_SELECAO) candidatos, não em
    # LEADS_POR_BUSCA. 1.2 -> 60 candidatos pra entregar 50.
    #
    # Existe porque a pré-seleção usa só 3 sinais gratuitos (PGFN, Zona
    # Franca, CNPJ ativo) e não tem como saber, antes de gastar, quais
    # candidatos vão render pouco no enriquecimento completo: CNPJ sem
    # site, Google Places sem resultado, Firecrawl falhando, ou o próprio
    # process_lead_pipeline estourando. Sem margem, uma execução com 10
    # falhas entrega 40 leads em vez de 50.
    #
    # 20% é um chute inicial deliberado -- não há histórico de taxa de
    # falha do pipeline em produção pra calibrar (nunca rodou de verdade).
    # Depois da primeira execução real, comparar `total_leads_processados`
    # com `total_cnpjs_selecionados` em `buscas_leads` dá a taxa real e
    # permite ajustar este número com dado em vez de palpite.
    LEADS_MARGEM_PRE_SELECAO: float = 1.2

    # --- Dashboard ---
    # Limite de leads/mês do plano contratado -- hoje um valor fixo (não
    # há ainda um conceito de "plano" por cliente/usuário), usado só pra
    # mostrar "X / 50 leads este mês" na seção "Consumo do Plano" do
    # dashboard (GET /api/dashboard/summary). Ver
    # app/api/routes/dashboard.py.
    PLANO_LEADS_MES: int = 50


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
