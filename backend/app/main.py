"""
HunterPro — Minotto Contabilidade
Entrypoint da API FastAPI.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Registra todos os modelos em `Base.metadata` (ver app/models/__init__.py).
# Hoje as rotas já importam os quatro por acaso, o que mascarou o bug de
# 23/08/2026 no worker -- explícito aqui pra que a API não dependa desse
# acidente se alguma rota deixar de importar algum modelo.
import app.models  # noqa: F401
from app.api.routes import admin, auth, dashboard, health, leads
from app.core.config import settings

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    description="Motor de prospecção HunterPro parametrizado para o nicho de clínicas/consultórios médicos (Minotto Contabilidade).",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(leads.router, prefix="/api/leads", tags=["leads"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])


@app.get("/")
def root():
    return {"project": settings.PROJECT_NAME, "status": "ok"}
