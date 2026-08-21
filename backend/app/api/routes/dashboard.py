"""
Dashboard (Fase 1) -- resumo executivo, consumo do plano, ações
recomendadas e simulador de receita. Ver app/schemas/dashboard.py pros
shapes de resposta e CLAUDE.md "Dashboard (Fase 1)" pro racional
completo de cada métrica/regra.
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.lead import Lead
from app.models.user import User
from app.schemas.dashboard import AcaoRecomendada, DashboardPremissas, DashboardSummary

router = APIRouter()

_STATUS_GANHO = "ganho"
_STATUS_NOVO_LEAD = "novo_lead"
_STATUS_EM_NEGOCIACAO = ("proposta_enviada", "negociacao")
_STATUS_PGFN_NAO_ABORDADO = ("novo_lead", "qualificacao")

# Defaults do simulador de receita quando o usuário nunca salvou
# premissas próprias (`User.dashboard_premissas is None`) -- taxa de
# fechamento e ticket médio são só um chute inicial razoável (não vêm de
# nenhum dado real), `leads_qualificados` já é calculado a partir da
# base de leads existente (ver `_leads_qualificados_padrao`).
_TAXA_FECHAMENTO_PADRAO = 20.0
_TICKET_MEDIO_PADRAO = 1500.0


def _inicio_do_mes_atual() -> datetime:
    agora = datetime.utcnow()
    return datetime(agora.year, agora.month, 1)


@router.get("/summary", response_model=DashboardSummary)
def get_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Resumo executivo do dashboard. Requer autenticação (qualquer papel),
    mesma proteção do resto da API -- não é uma métrica por usuário
    (todos os leads são compartilhados entre Minotto/4Hands, mesmo
    critério já usado em `GET /api/leads`).
    """
    total_leads = db.execute(select(func.count()).select_from(Lead)).scalar() or 0

    leads_no_mes = db.execute(
        select(func.count()).select_from(Lead).where(Lead.created_at >= _inicio_do_mes_atual())
    ).scalar() or 0

    # func.avg ignora NULL automaticamente (score_total é nullable) --
    # um lead que nunca passou pelo compute_lead_score não entra na
    # média. Sem nenhum lead pontuado, o AVG do SQL devolve NULL
    # (vira None em Python) -- tratado como 0.0, não como erro.
    score_medio_bruto = db.execute(select(func.avg(Lead.score_total))).scalar()
    score_medio = round(score_medio_bruto, 1) if score_medio_bruto is not None else 0.0

    leads_em_negociacao = db.execute(
        select(func.count()).select_from(Lead).where(Lead.kanban_status.in_(_STATUS_EM_NEGOCIACAO))
    ).scalar() or 0

    leads_ganhos = db.execute(
        select(func.count()).select_from(Lead).where(Lead.kanban_status == _STATUS_GANHO)
    ).scalar() or 0
    taxa_conversao = round(leads_ganhos / total_leads * 100, 1) if total_leads > 0 else 0.0

    return DashboardSummary(
        leads_no_mes=leads_no_mes,
        leads_no_mes_limite=settings.PLANO_LEADS_MES,
        score_medio=score_medio,
        leads_em_negociacao=leads_em_negociacao,
        taxa_conversao=taxa_conversao,
    )


@router.get("/acoes-recomendadas", response_model=list[AcaoRecomendada])
def get_acoes_recomendadas(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lista de até 4 categorias fixas de "próxima ação sugerida", cada uma
    com sua própria contagem -- categorias com contagem zero são
    omitidas (mostrar "0 leads" não ajuda ninguém a priorizar nada).
    Ordem fixa (não por contagem) -- a mesma ordem em que o cliente
    pediu as 4 regras.
    """
    candidatas = [
        AcaoRecomendada(
            titulo="Leads com decisor identificado prontos para abordagem",
            quantidade=db.execute(
                select(func.count())
                .select_from(Lead)
                .where(Lead.decisor_nome.is_not(None), Lead.kanban_status == _STATUS_NOVO_LEAD)
            ).scalar()
            or 0,
            kanban_status_filtro=_STATUS_NOVO_LEAD,
            filtro_chave="decisor_identificado",
        ),
        AcaoRecomendada(
            titulo="Leads de alta prioridade ainda não contatados",
            quantidade=db.execute(
                select(func.count())
                .select_from(Lead)
                .where(Lead.prioridade == "A", Lead.kanban_status == _STATUS_NOVO_LEAD)
            ).scalar()
            or 0,
            kanban_status_filtro=_STATUS_NOVO_LEAD,
            filtro_chave="prioridade_a",
        ),
        AcaoRecomendada(
            titulo="Leads que precisam de revisão manual",
            quantidade=db.execute(
                select(func.count())
                .select_from(Lead)
                .where(Lead.decisor_nome.is_(None), Lead.telefone.is_(None), Lead.email.is_(None))
            ).scalar()
            or 0,
            kanban_status_filtro=None,
            filtro_chave="revisao_manual",
        ),
        AcaoRecomendada(
            titulo="Leads com dívida ativa PGFN ainda não abordados",
            quantidade=db.execute(
                select(func.count())
                .select_from(Lead)
                .where(Lead.divida_ativa_qtd_inscricoes > 0, Lead.kanban_status.in_(_STATUS_PGFN_NAO_ABORDADO))
            ).scalar()
            or 0,
            kanban_status_filtro=None,
            filtro_chave="divida_pgfn_nao_abordada",
        ),
    ]

    return [acao for acao in candidatas if acao.quantidade > 0]


def _leads_qualificados_padrao(db: Session) -> int:
    return db.execute(
        select(func.count()).select_from(Lead).where(Lead.prioridade.in_(("A", "B")))
    ).scalar() or 0


@router.get("/premissas", response_model=DashboardPremissas)
def get_premissas(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Premissas do Simulador de Receita do usuário logado. Se nunca foram
    salvas (`dashboard_premissas is None`), devolve um padrão calculado
    -- `leads_qualificados` a partir da base real (contagem de leads
    prioridade A+B agora), `taxa_fechamento`/`ticket_medio` fixos (não há
    dado histórico de conversão/ticket real ainda pra derivar isso).
    Nunca persiste esse padrão sozinho -- só quando o usuário confirma
    via `PUT`.
    """
    if current_user.dashboard_premissas is not None:
        return DashboardPremissas(**current_user.dashboard_premissas)

    return DashboardPremissas(
        leads_qualificados=_leads_qualificados_padrao(db),
        taxa_fechamento=_TAXA_FECHAMENTO_PADRAO,
        ticket_medio=_TICKET_MEDIO_PADRAO,
    )


@router.put("/premissas", response_model=DashboardPremissas)
def put_premissas(
    dados: DashboardPremissas,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Salva as premissas do Simulador de Receita pro usuário logado --
    por usuário, não global (cada um pode estar testando um cenário
    diferente)."""
    current_user.dashboard_premissas = dados.model_dump()
    db.commit()
    db.refresh(current_user)
    return DashboardPremissas(**current_user.dashboard_premissas)
