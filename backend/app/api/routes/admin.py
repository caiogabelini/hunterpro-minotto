"""
Rotas administrativas -- hoje só o painel de disparo de "busca mensal"
(ver app/models/busca_leads.py e
app/workers/celery_app.py::executar_busca_mensal). Toda rota aqui usa
`require_admin` (app/api/deps.py), não `get_current_user` -- um usuário
"client" autenticado recebe 403 (não 401: ele TEM um token válido, só
não tem permissão), nunca alcança o painel nem por acesso direto à URL
de uma rota.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.models.busca_leads import BuscaLeads, StatusBusca
from app.models.user import User
from app.schemas.busca_leads import BuscaLeadsRead
from app.workers.celery_app import executar_busca_mensal

router = APIRouter()


@router.post("/buscas", response_model=BuscaLeadsRead, status_code=status.HTTP_201_CREATED)
def disparar_busca(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Dispara uma nova busca mensal -- CUSTO REAL de API em produção
    (Receita Federal, PGFN, CNES, Google Places, Firecrawl+IA, Evolution,
    Hunter.io/ZeroBounce por cada CNPJ encontrado), não uma simulação.

    Cria o registro `BuscaLeads` com status "executando" e dispara
    `executar_busca_mensal.delay(busca.id)` -- assíncrono, não bloqueia a
    resposta HTTP (a task roda no worker do Celery, não neste request).
    Devolve o registro criado imediatamente; o frontend acompanha o
    progresso via polling em `GET /buscas/{id}`.

    409 se já existe uma busca com status "executando" -- trava simples
    contra disparo duplo (dois admins clicando ao mesmo tempo, ou o
    mesmo admin clicando duas vezes antes do frontend atualizar). Não é
    um lock de banco (`SELECT ... FOR UPDATE`) nem previne uma corrida
    genuína entre dois requests simultâneos chegando no mesmo instante
    -- é uma checagem best-effort, suficiente pro caso real que motivou
    o pedido (clique duplo/dois admins na mesma tela), não uma garantia
    formal sob concorrência.
    """
    busca_em_andamento = db.execute(
        select(BuscaLeads).where(BuscaLeads.status == StatusBusca.EXECUTANDO.value)
    ).scalars().first()
    if busca_em_andamento is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Já existe uma busca em andamento, iniciada em "
                f"{busca_em_andamento.iniciado_em.strftime('%d/%m/%Y %H:%M')}."
            ),
        )

    busca = BuscaLeads(iniciado_por_id=current_user.id, status=StatusBusca.EXECUTANDO.value)
    db.add(busca)
    db.commit()
    db.refresh(busca)

    executar_busca_mensal.delay(busca.id)

    return busca


@router.get("/buscas", response_model=list[BuscaLeadsRead])
def listar_buscas(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    limit: int = Query(default=20, le=100),
):
    """Histórico de buscas já disparadas, mais recente primeiro."""
    stmt = select(BuscaLeads).order_by(BuscaLeads.iniciado_em.desc()).limit(limit)
    return db.execute(stmt).scalars().all()


@router.get("/buscas/{busca_id}", response_model=BuscaLeadsRead)
def get_busca(
    busca_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Status atual de uma busca específica -- usado pelo frontend pra
    fazer polling enquanto `status == "executando"`."""
    busca = db.get(BuscaLeads, busca_id)
    if busca is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Busca não encontrada")
    return busca
