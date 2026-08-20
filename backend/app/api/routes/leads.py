from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.lead import KanbanStatus, Lead
from app.models.user import User
from app.schemas.lead import LeadRead, LeadStatusUpdate

router = APIRouter()

_KANBAN_STATUS_VALIDOS = {status_.value for status_ in KanbanStatus}


@router.get("", response_model=list[LeadRead])
def list_leads(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    prioridade: str | None = Query(default=None, description="Filtrar por A, B ou C"),
    zona_franca: bool | None = Query(default=None),
    kanban_status: str | None = Query(default=None, description="Filtrar por coluna do Kanban"),
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
    if kanban_status:
        stmt = stmt.where(Lead.kanban_status == kanban_status)
    stmt = stmt.order_by(Lead.score_total.desc().nullslast()).limit(limit)

    return db.execute(stmt).scalars().all()


@router.get("/{lead_id}", response_model=LeadRead)
def get_lead(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Detalhe de um lead (dossiê) — todos os campos, inclusive os que
    `list_leads` não usa pra montar o Kanban (dados cadastrais, contato,
    presença digital, breakdown do score). Requer autenticação (qualquer
    papel), mesma proteção de `GET /api/leads`.
    """
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado")
    return lead


@router.patch("/{lead_id}/status", response_model=LeadRead)
def update_lead_status(
    lead_id: str,
    dados: LeadStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Move um lead entre colunas do Kanban. Requer autenticação (qualquer
    papel). `motivo_perda` é obrigatório quando `kanban_status ==
    "perdido"` — validado aqui, não a nível de banco (a coluna é
    nullable, já que só faz sentido nesse status).
    """
    if dados.kanban_status not in _KANBAN_STATUS_VALIDOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"kanban_status inválido. Valores aceitos: {sorted(_KANBAN_STATUS_VALIDOS)}",
        )
    if dados.kanban_status == KanbanStatus.PERDIDO.value and not dados.motivo_perda:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="motivo_perda é obrigatório quando kanban_status é 'perdido'",
        )

    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado")

    lead.kanban_status = dados.kanban_status
    lead.motivo_perda = dados.motivo_perda if dados.kanban_status == KanbanStatus.PERDIDO.value else None
    db.commit()
    db.refresh(lead)
    return lead
