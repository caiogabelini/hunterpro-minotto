"""
Contagem e limite das gerações de IA por lead.
================================================================================

⚠️ **CONTROLE DE CUSTO, não de UX.** Cada "Gerar mensagem"/"Gerar
Insights" no dossiê é uma chamada paga à API da Anthropic. Até 24/08/2026
os botões eram clicáveis sem limite, e o acesso vai ser aberto pra
usuários "client" (Douglas/Sulamita) -- ou seja, alguém explorando a tela
podia gerar indefinidamente sem que nada segurasse.

Este módulo é a fonte ÚNICA da regra: as duas rotas de geração
(`gerar_abordagem`, `gerar_insights`), a rota de reset do admin, o
`GET /api/leads/{id}` (que informa o frontend quantas restam) e o
Dashboard passam todos por aqui. Duplicar a contagem em cada lugar seria
a receita pra frontend e backend discordarem sobre quantas gerações
sobraram.

DUAS FORMAS DE CONTAR, porque os dois dados são guardados diferente:

  - **e-mail/WhatsApp**: cada geração cria uma linha em `lead_messages`
    (histórico, nunca sobrescrito). A contagem é `COUNT(*)` por
    `lead_id` + `canal`.
  - **insights**: `Lead.insights_ia` é SOBRESCRITO a cada geração, não há
    histórico. Por isso existe o contador `Lead.insights_geracoes_count`.

⚠️ **Reset não apaga histórico.** Pra e-mail/WhatsApp, zerar o contador
deletando linhas de `lead_messages` destruiria justamente o que aquela
tabela existe pra guardar. Em vez disso o reset grava um instante em
`Lead.ia_limite_resetado_em[tipo]`, e a contagem passa a considerar só as
gerações posteriores a ele. Pra insights, que não tem histórico, o reset
zera o inteiro mesmo (e ainda grava o carimbo, como trilha de auditoria
de quando um admin liberou).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.lead import Lead
from app.models.lead_message import CanalMensagem, LeadMessage

TIPO_INSIGHTS = "insights"

# Os tipos de geração que o limite cobre. Os canais de mensagem saem de
# `CanalMensagem` pra não virar uma segunda lista que alguém esquece de
# atualizar ao criar um canal novo.
TIPOS_GERACAO_IA: tuple[str, ...] = tuple(c.value for c in CanalMensagem) + (TIPO_INSIGHTS,)


def _resetado_em(lead: Lead, tipo: str) -> datetime | None:
    """Instante da última liberação deste tipo, ou None se nunca houve.

    Tolera carimbo corrompido/ilegível devolvendo None -- o efeito é
    contar TODAS as gerações, ou seja, errar pro lado restritivo. Um
    limite de custo que falha aberto é pior do que um que falha fechado.
    """
    marcas = lead.ia_limite_resetado_em or {}
    if not isinstance(marcas, dict):
        return None
    bruto = marcas.get(tipo)
    if not isinstance(bruto, str):
        return None
    try:
        return datetime.fromisoformat(bruto)
    except ValueError:
        return None


def contar_geracoes(db: Session, lead: Lead, tipo: str) -> int:
    """
    Quantas gerações deste tipo já aconteceram pro lead DESDE a última
    liberação do admin (ou desde sempre, se nunca houve liberação).
    """
    if tipo == TIPO_INSIGHTS:
        return lead.insights_geracoes_count or 0

    stmt = (
        select(func.count())
        .select_from(LeadMessage)
        .where(LeadMessage.lead_id == lead.id, LeadMessage.canal == tipo)
    )
    resetado_em = _resetado_em(lead, tipo)
    if resetado_em is not None:
        stmt = stmt.where(LeadMessage.gerado_em > resetado_em)
    return db.execute(stmt).scalar() or 0


def limite_atingido(db: Session, lead: Lead, tipo: str) -> bool:
    """
    True se o lead já esgotou as gerações deste tipo.

    Limite `<= 0` desliga a checagem (libera geral) em vez de bloquear
    tudo -- ver `settings.LIMITE_GERACOES_IA_POR_LEAD`.
    """
    limite = settings.LIMITE_GERACOES_IA_POR_LEAD
    if limite <= 0:
        return False
    return contar_geracoes(db, lead, tipo) >= limite


def resumo_geracoes(db: Session, lead: Lead) -> dict[str, int]:
    """
    Contagem por tipo + o limite vigente, no formato que o dossiê consome
    pra desabilitar os botões ANTES do clique.

    O limite vai junto de propósito: sem ele o frontend teria que
    hardcodar `2`, e mudar `LIMITE_GERACOES_IA_POR_LEAD` no `.env`
    passaria a exigir deploy do frontend pra continuar coerente.
    """
    resumo = {tipo: contar_geracoes(db, lead, tipo) for tipo in TIPOS_GERACAO_IA}
    resumo["limite"] = settings.LIMITE_GERACOES_IA_POR_LEAD
    return resumo


def registrar_reset(lead: Lead, tipo: str, agora: datetime) -> None:
    """
    Libera novas gerações deste tipo pro lead.

    Reatribui `ia_limite_resetado_em` a um dict NOVO em vez de mutar o
    existente: `Lead.ia_limite_resetado_em` é uma coluna JSON, e o
    SQLAlchemy não detecta mutação in-place de dict/list sem
    `MutableDict` -- alterar a chave sem reatribuir não marcaria o objeto
    como sujo e o UPDATE não sairia. Erro clássico e silencioso com
    colunas JSON.
    """
    marcas = dict(lead.ia_limite_resetado_em or {})
    marcas[tipo] = agora.isoformat()
    lead.ia_limite_resetado_em = marcas

    if tipo == TIPO_INSIGHTS:
        lead.insights_geracoes_count = 0
