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
    HUNTER_API_KEY: str = ""
    ZEROBOUNCE_API_KEY: str = ""
    SERP_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

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

    # --- CORS ---
    FRONTEND_ORIGIN: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
