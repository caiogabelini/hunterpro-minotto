"""
Dashboard (Fases 1 e 2) -- resumo executivo, consumo do plano, ações
recomendadas, simulador de receita, funil de conversão e motivos de
perda. Ver app/schemas/dashboard.py pros shapes de resposta e
CLAUDE.md "Dashboard (Fase 1)"/"Dashboard (Fase 2)" pro racional
completo de cada métrica/regra -- em especial a limitação do funil (ver
docstring de `get_funil` abaixo antes de mexer nele).
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
from app.schemas.dashboard import AcaoRecomendada, DashboardPremissas, DashboardSummary, FunilEtapa, MotivoPerda

router = APIRouter()

_STATUS_GANHO = "ganho"
_STATUS_NOVO_LEAD = "novo_lead"
_STATUS_PERDIDO = "perdido"
_STATUS_EM_NEGOCIACAO = ("proposta_enviada", "negociacao")
_STATUS_PGFN_NAO_ABORDADO = ("novo_lead", "qualificacao")
_TIPO_CONTRATO_PONTUAL = "pontual"
_TIPO_CONTRATO_RECORRENTE = "recorrente"

# Ordem fixa do funil -- mesma ordem de funil já usada em
# app/models/lead.py::KanbanStatus e frontend/src/kanbanStatuses.ts,
# **incluindo "perdido" como 9ª e última entrada** (mudou nesta sessão --
# antes era excluído; ver o docstring de `get_funil` pro racional do
# percentual). Rótulos em português espelham KANBAN_COLUMNS no
# frontend -- se uma etapa nova for adicionada num dos dois lugares,
# adicionar aqui também (nada sincroniza isso automaticamente, mesma
# observação já feita pro par KanbanStatus/KANBAN_COLUMNS).
_ETAPAS_FUNIL = [
    ("novo_lead", "Novo Lead"),
    ("qualificacao", "Qualificação"),
    ("contatado", "Contatado"),
    ("respondeu", "Respondeu"),
    ("reuniao", "Reunião"),
    ("proposta_enviada", "Proposta Enviada"),
    ("negociacao", "Negociação"),
    ("ganho", "Ganho"),
    ("perdido", "Perdido"),
]

_LIMITE_MOTIVOS_PERDA = 10

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

    `receita_fechada_*` é receita REAL (leads com `kanban_status ==
    "ganho"`, valores vindos de `Lead.valor_fechamento` -- ver "Closing
    data on 'Ganho'" no CLAUDE.md), diferente da receita ESTIMADA que o
    Simulador de Receita calcula em cima das premissas do usuário
    (`GET /premissas`) -- os dois números não devem ser confundidos no
    frontend, por isso ficam em campos com nomes bem distintos aqui.

    ⚠️ `receita_fechada_total` soma `receita_fechada_pontual` (um valor
    ÚNICO por contrato) com `receita_fechada_recorrente_mensal` (um
    valor MENSAL recorrente) -- **misturando naturezas diferentes de
    receita** (dinheiro que entrou uma vez x dinheiro que entra todo
    mês). É deliberadamente só uma "visão geral" pra um número único no
    card do dashboard, não uma métrica financeira precisa (ex.: não é
    MRR, não é "receita total do ano") -- os dois componentes vêm
    separados também (`receita_fechada_pontual`/
    `receita_fechada_recorrente_mensal`) pra quem precisar da distinção
    de verdade. `func.sum` ignora `NULL` automaticamente (mesmo
    raciocínio de `func.avg` pro `score_medio` acima) -- um lead "ganho"
    sem `valor_fechamento` preenchido (não deveria existir via a API,
    que exige o campo pra mover pra "ganho", mas pode acontecer com dado
    antigo/manual) não quebra a soma, só não contribui com nada; sem
    nenhum lead "ganho" de um tipo, a soma vira `NULL` no SQL, tratado
    aqui como `0.0`, não como erro.
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

    receita_fechada_pontual = db.execute(
        select(func.sum(Lead.valor_fechamento)).where(
            Lead.kanban_status == _STATUS_GANHO, Lead.tipo_contrato == _TIPO_CONTRATO_PONTUAL
        )
    ).scalar() or 0.0
    receita_fechada_recorrente_mensal = db.execute(
        select(func.sum(Lead.valor_fechamento)).where(
            Lead.kanban_status == _STATUS_GANHO, Lead.tipo_contrato == _TIPO_CONTRATO_RECORRENTE
        )
    ).scalar() or 0.0

    return DashboardSummary(
        leads_no_mes=leads_no_mes,
        leads_no_mes_limite=settings.PLANO_LEADS_MES,
        score_medio=score_medio,
        leads_em_negociacao=leads_em_negociacao,
        taxa_conversao=taxa_conversao,
        receita_fechada_pontual=receita_fechada_pontual,
        receita_fechada_recorrente_mensal=receita_fechada_recorrente_mensal,
        receita_fechada_total=receita_fechada_pontual + receita_fechada_recorrente_mensal,
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


@router.get("/funil", response_model=list[FunilEtapa])
def get_funil(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Funil de conversão -- as 9 etapas de `_ETAPAS_FUNIL` (todo
    `KanbanStatus`, "perdido" incluído como última entrada desde
    21/08/2026 -- ver histórico abaixo), sempre todas as 9, mesmo com
    `quantidade=0` (diferente de `get_acoes_recomendadas`, que omite
    zero -- aqui o zero É informação: "essa etapa do funil está vazia
    agora" é um dado válido de se mostrar, não ruído).

    ⚠️ LIMITAÇÃO CONHECIDA, LEIA ANTES DE ALTERAR ESTE ENDPOINT: isto é
    uma FOTOGRAFIA da distribuição atual dos leads por `kanban_status`,
    NÃO uma taxa de conversão real de coorte entre etapas. `Lead` só
    guarda o status ATUAL de cada lead -- não existe nenhuma tabela de
    histórico de transição (tipo "em que data cada lead passou por cada
    coluna") neste projeto. Sem esse histórico, não há como calcular
    "de todo lead que passou pela etapa X, quantos avançaram pra Y" --
    isso exigiria reconstruir a sequência temporal de mudanças de
    status por lead, dado que não existe. `percentual` aqui é só
    "quantidade nesta etapa ÷ total de leads (todos, incluindo
    perdidos) × 100" -- uma proporção do momento, não uma taxa de
    avanço. Não adicione nenhum campo/cálculo que implique causalidade
    ou progressão histórica (ex.: "taxa de conversão de X pra Y") sem
    antes construir a tabela de histórico que isso exigiria.

    ⚠️ "perdido" foi excluído do funil e do denominador do percentual
    até 21/08/2026 (a ideia original era "funil é sobre progressão,
    perda tem sua própria seção") -- mudou por pedido explícito, pelos
    seguintes motivos, que valem se alguém cogitar reverter: (1) com
    "perdido" fora do denominador, a soma dos percentuais das 8 etapas
    somava ~100% de um universo que NÃO era "todos os leads", o que é
    enganoso ao lado de outros números do dashboard; (2) o total agora
    bate exatamente com a contagem real de leads no Kanban e com o
    denominador que `get_summary` já usa pra `taxa_conversao` (também
    `COUNT(*)` sobre TODOS os leads, "perdido" incluído) -- os dois
    endpoints agora concordam sobre o que é "o total". "perdido" ainda
    é visualmente distinto no frontend (cor própria, não navy nem
    verde) precisamente pra deixar claro que é uma saída do funil, não
    mais uma etapa de progresso, mesmo estando na mesma lista.
    """
    contagem_por_status = dict(
        db.execute(select(Lead.kanban_status, func.count()).group_by(Lead.kanban_status)).all()
    )
    total_leads = sum(contagem_por_status.values())

    def _percentual(quantidade: int) -> float:
        return round(quantidade / total_leads * 100, 1) if total_leads > 0 else 0.0

    return [
        FunilEtapa(
            status=status,
            label=label,
            quantidade=contagem_por_status.get(status, 0),
            percentual=_percentual(contagem_por_status.get(status, 0)),
        )
        for status, label in _ETAPAS_FUNIL
    ]


@router.get("/motivos-perda", response_model=list[MotivoPerda])
def get_motivos_perda(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Ranking dos motivos de perda mais comuns -- leads com
    `kanban_status="perdido"` e `motivo_perda` preenchido, agrupados
    pelo texto EXATO (sem normalização/agrupamento semântico via IA --
    "Preço alto" e "Achou caro" contam como dois motivos distintos hoje;
    ver README "O que falta" se uma normalização mais esperta for
    pedida numa sessão futura), ordenado por contagem decrescente,
    limitado a `_LIMITE_MOTIVOS_PERDA` (10). Lista vazia (não erro)
    quando não há nenhum lead perdido com motivo -- o frontend trata
    esse caso com um estado vazio de tom positivo, não como falha.
    """
    linhas = db.execute(
        select(Lead.motivo_perda, func.count())
        .where(Lead.kanban_status == _STATUS_PERDIDO, Lead.motivo_perda.is_not(None), Lead.motivo_perda != "")
        .group_by(Lead.motivo_perda)
        .order_by(func.count().desc())
        .limit(_LIMITE_MOTIVOS_PERDA)
    ).all()

    return [MotivoPerda(motivo=motivo, quantidade=quantidade) for motivo, quantidade in linhas]
