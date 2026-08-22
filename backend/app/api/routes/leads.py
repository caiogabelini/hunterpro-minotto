from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.tempo import agora_utc
from app.models.lead import KanbanStatus, Lead
from app.models.lead_message import CanalMensagem, LeadMessage
from app.models.user import User
from app.schemas.lead import LeadListaResponse, LeadRead, LeadStatusUpdate
from app.schemas.lead_message import LeadMessageRead
from app.services.ai_enrichment import gerar_insights_estrategicos, gerar_mensagem_abordagem

router = APIRouter()

_KANBAN_STATUS_VALIDOS = {status_.value for status_ in KanbanStatus}
_CANAIS_VALIDOS = {canal.value for canal in CanalMensagem}
_TIPOS_CONTRATO_VALIDOS = {"pontual", "recorrente"}

# Colunas ordenáveis de GET /leads/lista -- chave é o valor aceito no
# query param `ordenar_por`, valor é a coluna real do modelo.
_COLUNAS_ORDENACAO_LISTA = {"score_total": Lead.score_total, "created_at": Lead.created_at}
_ORDENS_VALIDAS_LISTA = {"asc", "desc"}


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


@router.get("/lista", response_model=LeadListaResponse)
def listar_leads_paginado(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    busca: str | None = Query(default=None, description="Filtra por razão social, nome fantasia ou CNPJ contendo o texto"),
    prioridade: str | None = Query(default=None, description="Filtrar por A, B ou C"),
    kanban_status: str | None = Query(default=None, description="Filtrar por coluna do Kanban"),
    ordenar_por: str = Query(default="score_total"),
    ordem: str = Query(default="desc"),
    pagina: int = Query(default=1, ge=1),
    por_pagina: int = Query(default=25, ge=1, le=100),
):
    """
    Lista paginada de leads pra tela "Lista de Leads" (busca/filtros/
    ordenação/paginação) -- endpoint PRÓPRIO e ADITIVO, não reaproveita
    nem modifica `list_leads`/`GET /api/leads` (que continua servindo o
    Kanban exatamente como antes, sem paginação e sem busca por texto,
    de propósito -- o Kanban carrega tudo de uma vez pras 9 colunas,
    não faz sentido paginar isso). Requer autenticação (qualquer papel),
    mesma proteção do resto das rotas de lead.

    `busca` filtra por `razao_social` OU `nome_fantasia` OU `cnpj`
    contendo o texto, case-insensitive (`ILIKE` -- SQLAlchemy compila
    isso tanto pro operador nativo do Postgres quanto pra um `lower(...)
    LIKE lower(...)` portável no SQLite dos testes, sem precisar de
    lógica condicional por dialeto aqui). `prioridade`/`kanban_status`
    são os mesmos filtros de igualdade já usados em `list_leads`.

    ⚠️ Case-insensitive com acento (ex.: "í"/"Í") só funciona de verdade
    contra Postgres real em produção -- o `lower()` embutido do SQLite
    (usado nos testes) só normaliza ASCII, então "CLÍNICA".lower() no
    SQLite não vira "clínica" (o Í maiúsculo fica intocado). Não é um
    bug deste endpoint, é uma limitação conhecida do SQLite de teste --
    ver o mesmo aviso em `tests/test_leads_lista_route.py::test_busca_case_insensitive`,
    que testa case-insensitivity só com ASCII por esse motivo.

    `ordenar_por`/`ordem` validados contra listas fechadas (422 se fora
    delas) -- mesmo padrão de validação explícita já usado em
    `update_lead_status` pro `kanban_status`, em vez de deixar o SQL
    falhar silenciosamente ou ordenar por uma coluna arbitrária.

    `total` é contado ANTES de aplicar `pagina`/`por_pagina` (sobre a
    mesma query já filtrada, via subquery) -- é o total de leads que
    batem com os filtros, não o total geral de leads nem o tamanho da
    página atual.
    """
    if ordenar_por not in _COLUNAS_ORDENACAO_LISTA:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"ordenar_por inválido. Valores aceitos: {sorted(_COLUNAS_ORDENACAO_LISTA)}",
        )
    if ordem not in _ORDENS_VALIDAS_LISTA:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"ordem inválida. Valores aceitos: {sorted(_ORDENS_VALIDAS_LISTA)}",
        )

    stmt = select(Lead)
    if busca:
        padrao = f"%{busca}%"
        stmt = stmt.where(
            or_(
                Lead.razao_social.ilike(padrao),
                Lead.nome_fantasia.ilike(padrao),
                Lead.cnpj.ilike(padrao),
            )
        )
    if prioridade:
        stmt = stmt.where(Lead.prioridade == prioridade.upper())
    if kanban_status:
        stmt = stmt.where(Lead.kanban_status == kanban_status)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0

    coluna = _COLUNAS_ORDENACAO_LISTA[ordenar_por]
    coluna_ordenada = coluna.asc().nullslast() if ordem == "asc" else coluna.desc().nullslast()
    stmt_pagina = stmt.order_by(coluna_ordenada).offset((pagina - 1) * por_pagina).limit(por_pagina)
    items = db.execute(stmt_pagina).scalars().all()

    return LeadListaResponse(items=items, total=total, pagina=pagina, por_pagina=por_pagina)


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
    "perdido"`; `servicos_vendidos`/`tipo_contrato`/`valor_fechamento`
    são obrigatórios quando `kanban_status == "ganho"` (mesmo padrão de
    validação condicional, do lado positivo) — tudo validado aqui, não
    a nível de banco (as colunas ficam nullable, já que só fazem
    sentido nesses dois status específicos).

    Mover pra qualquer outra etapa (nem "ganho" nem "perdido") não
    exige nenhum desses campos, e os campos de fechamento/perda de uma
    transição anterior NÃO são limpos automaticamente ao sair de
    "ganho"/"perdido" pra outra etapa — diferente de `motivo_perda`
    (que já era limpo ao sair de "perdido", comportamento mantido sem
    mudança), os campos de fechamento representam um evento de negócio
    que já aconteceu (uma venda real) e continuam válidos como registro
    histórico mesmo que o lead seja movido de volta por engano ou
    reclassificado depois.
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
    if dados.kanban_status == KanbanStatus.GANHO.value:
        if not dados.servicos_vendidos:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="servicos_vendidos é obrigatório (lista não-vazia) quando kanban_status é 'ganho'",
            )
        if not dados.tipo_contrato:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="tipo_contrato é obrigatório quando kanban_status é 'ganho'",
            )
        if dados.tipo_contrato not in _TIPOS_CONTRATO_VALIDOS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"tipo_contrato inválido. Valores aceitos: {sorted(_TIPOS_CONTRATO_VALIDOS)}",
            )
        if dados.valor_fechamento is None or dados.valor_fechamento <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="valor_fechamento é obrigatório e deve ser maior que zero quando kanban_status é 'ganho'",
            )

    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado")

    lead.kanban_status = dados.kanban_status
    lead.motivo_perda = dados.motivo_perda if dados.kanban_status == KanbanStatus.PERDIDO.value else None
    if dados.kanban_status == KanbanStatus.GANHO.value:
        lead.servicos_vendidos = dados.servicos_vendidos
        lead.tipo_contrato = dados.tipo_contrato
        lead.valor_fechamento = dados.valor_fechamento
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
    lead.insights_gerado_em = agora_utc()
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
