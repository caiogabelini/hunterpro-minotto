from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.lead import Lead
from app.models.user import User
from app.schemas.lead import LeadRead

router = APIRouter()


@router.get("", response_model=list[LeadRead])
def list_leads(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    prioridade: str | None = Query(default=None, description="Filtrar por A, B ou C"),
    zona_franca: bool | None = Query(default=None),
    limit: int = Query(default=50, le=200),
):
    """
    Lista leads, com filtros básicos. Ordenado por score decrescente.
    Requer autenticação (qualquer papel — `admin` ou `client`); não
    filtra por dono/cliente ainda, isso é decisão de produto pra uma
    sessão futura.

    NOTA: a busca/enriquecimento em si (Google Places, Receita, PGFN,
    CNES...) ainda não está implementada — isso é o próximo passo,
    via workers do Celery. Este endpoint já deixa pronta a forma como
    o frontend vai consumir os leads assim que existirem no banco.
    """
    stmt = select(Lead)
    if prioridade:
        stmt = stmt.where(Lead.prioridade == prioridade.upper())
    if zona_franca is not None:
        stmt = stmt.where(Lead.zona_franca == zona_franca)
    stmt = stmt.order_by(Lead.score_total.desc().nullslast()).limit(limit)

    return db.execute(stmt).scalars().all()
