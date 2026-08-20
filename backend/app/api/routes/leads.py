from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.lead import KanbanStatus, Lead
from app.models.lead_message import CanalMensagem, LeadMessage
from app.models.user import User
from app.schemas.lead import LeadRead, LeadStatusUpdate
from app.schemas.lead_message import LeadMessageRead
from app.services.ai_enrichment import gerar_insights_estrategicos, gerar_mensagem_abordagem

router = APIRouter()

_KANBAN_STATUS_VALIDOS = {status_.value for status_ in KanbanStatus}
_CANAIS_VALIDOS = {canal.value for canal in CanalMensagem}


def _montar_dados_lead(lead: Lead) -> dict:
    """Extrai do ORM só os campos que `gerar_mensagem_abordagem` usa pra
    montar o prompt — mesmo dict simples nos dois endpoints de
    abordagem, pra não duplicar a lista de campos."""
    return {
        "razao_social": lead.razao_social,
        "decisor_nome": lead.decisor_nome,
        "municipio": lead.municipio,
        "uf": lead.uf,
        "divida_ativa_qtd_inscricoes": lead.divida_ativa_qtd_inscricoes,
        "rqe_confirmado": lead.rqe_confirmado,
        "google_rating": lead.google_rating,
        "google_avaliacoes": lead.google_avaliacoes,
        "situacao_cadastral": lead.situacao_cadastral,
    }


def _montar_dados_lead_insights(lead: Lead) -> dict:
    """Versão mais completa de `_montar_dados_lead` -- inclui score,
    breakdown, presença digital e anos de mercado, além de tudo que a
    versão "abordagem" já extrai. Só usada por `gerar_insights_estrategicos`,
    que precisa do quadro inteiro do lead (ver docstring da função em
    app/services/ai_enrichment.py) pra apontar o que está mais forte/fraco
    -- diferente de `_montar_dados_lead`, que de propósito só extrai o
    subconjunto relevante pro corpo de uma mensagem de e-mail/WhatsApp."""
    return {
        "razao_social": lead.razao_social,
        "decisor_nome": lead.decisor_nome,
        "municipio": lead.municipio,
        "uf": lead.uf,
        "anos_mercado": lead.anos_mercado,
        "situacao_cadastral": lead.situacao_cadastral,
        "score_total": lead.score_total,
        "score_detalhes": lead.score_detalhes,
        "prioridade": lead.prioridade,
        "divida_ativa_qtd_inscricoes": lead.divida_ativa_qtd_inscricoes,
        "divida_ativa_valor_total": lead.divida_ativa_valor_total,
        "divida_ativa_ajuizado": lead.divida_ativa_ajuizado,
        "zona_franca": lead.zona_franca,
        "rqe_confirmado": lead.rqe_confirmado,
        "rqe_fonte": lead.rqe_fonte,
        "site": lead.site,
        "instagram": lead.instagram,
        "linkedin_empresa": lead.linkedin_empresa,
        "linkedin_decisor": lead.linkedin_decisor,
        "google_rating": lead.google_rating,
        "google_avaliacoes": lead.google_avaliacoes,
        "servicos": lead.servicos,
        "whatsapp_ativo": lead.whatsapp_ativo,
        "email_validado": lead.email_validado,
    }


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


@router.post("/{lead_id}/gerar-abordagem/{canal}", response_model=LeadMessageRead)
def gerar_abordagem(
    lead_id: str,
    canal: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Gera uma NOVA mensagem de abordagem pro canal indicado ("email" ou
    "whatsapp") via IA (`app/services/ai_enrichment.py::gerar_mensagem_abordagem`)
    e cria um registro novo em `lead_messages` — nunca sobrescreve um
    registro existente, cada geração fica no histórico (ver `GET
    /{lead_id}/mensagens` pra só a mais recente de cada canal). Requer
    autenticação (qualquer papel), mesma proteção do resto das rotas de
    lead. Substitui o antigo `POST /{lead_id}/gerar-abordagem` (sem
    canal, escrevia em `Lead.mensagem_abordagem` — deprecated, ver
    app/models/lead.py).

    422 pra canal fora de `CanalMensagem`. 404 se o lead não existir.
    502 se a IA não conseguir gerar nada — `gerar_mensagem_abordagem`
    nunca lança, devolve `conteudo=""` tanto em erro de rede/HTTP quanto
    em resposta vazia/malformada; é aqui que isso vira um erro explícito
    pro frontend tratar (mostrar mensagem amigável), em vez de
    silenciosamente criar um `LeadMessage` vazio.
    """
    if canal not in _CANAIS_VALIDOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Canal inválido. Valores aceitos: {sorted(_CANAIS_VALIDOS)}",
        )

    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado")

    resultado = gerar_mensagem_abordagem(_montar_dados_lead(lead), canal=canal)

    if not resultado.conteudo:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Não foi possível gerar a mensagem agora. Tente novamente.",
        )

    mensagem = LeadMessage(
        lead_id=lead.id,
        canal=canal,
        conteudo=resultado.conteudo,
        assunto=resultado.assunto,
    )
    db.add(mensagem)
    db.commit()
    db.refresh(mensagem)
    return mensagem


@router.post("/{lead_id}/gerar-insights", response_model=LeadRead)
def gerar_insights(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Gera (ou regenera) a análise estratégica da aba "Insights" do dossiê
    via IA (`app/services/ai_enrichment.py::gerar_insights_estrategicos`)
    e sobrescreve `Lead.insights_ia`/`insights_gerado_em` -- diferente de
    `gerar_abordagem`, não mantém histórico (não foi pedido; cada geração
    substitui a anterior, mesmo padrão de "Gerar novamente"). Requer
    autenticação (qualquer papel), mesma proteção do resto das rotas de
    lead. 404 se o lead não existir.

    502 se a IA não conseguir gerar nada -- `gerar_insights_estrategicos`
    nunca lança, devolve `{}` tanto em erro de rede/HTTP quanto em
    resposta vazia/malformada; é aqui que isso vira um erro explícito pro
    frontend tratar, em vez de persistir um `insights_ia` vazio.
    """
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado")

    resultado = gerar_insights_estrategicos(_montar_dados_lead_insights(lead))

    if not resultado:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Não foi possível gerar os insights agora. Tente novamente.",
        )

    lead.insights_ia = resultado
    lead.insights_gerado_em = datetime.utcnow()
    db.commit()
    db.refresh(lead)
    return lead


@router.get("/{lead_id}/mensagens", response_model=list[LeadMessageRead])
def listar_mensagens(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Devolve a mensagem mais recente de cada canal já gerado pro lead —
    uma por canal, não o histórico completo (o histórico continua no
    banco, só não é exposto por esta rota). 404 se o lead não existir.

    Filtra "mais recente por canal" em Python, não com uma query
    `DISTINCT ON`/window function — mais simples e portável (SQLite nos
    testes, Postgres em produção) pro volume esperado (no máximo 2
    canais por lead hoje).
    """
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado")

    todas = db.execute(
        select(LeadMessage).where(LeadMessage.lead_id == lead_id).order_by(LeadMessage.gerado_em.desc())
    ).scalars().all()

    mais_recente_por_canal: dict[str, LeadMessage] = {}
    for msg in todas:
        mais_recente_por_canal.setdefault(msg.canal, msg)

    return list(mais_recente_por_canal.values())
