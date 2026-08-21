from pydantic import BaseModel


class DashboardSummary(BaseModel):
    leads_no_mes: int
    leads_no_mes_limite: int
    score_medio: float
    leads_em_negociacao: int
    taxa_conversao: float


class AcaoRecomendada(BaseModel):
    titulo: str
    quantidade: int
    # Coluna única do Kanban pra filtrar, quando a regra cabe numa só
    # (ex.: "novo_lead"); None quando a regra abrange mais de uma coluna
    # ou nenhuma (ex.: "revisão manual" não depende de kanban_status).
    kanban_status_filtro: str | None = None
    # Slug estável identificando a regra em si -- o frontend ainda não
    # aplica filtro real ao navegar pro Kanban (ver DashboardPage.tsx),
    # mas esse campo já deixa pronta a chave que uma versão futura do
    # link "Ver leads" usaria pra filtrar de verdade.
    filtro_chave: str


class DashboardPremissas(BaseModel):
    leads_qualificados: int
    taxa_fechamento: float
    ticket_medio: float


class FunilEtapa(BaseModel):
    """
    Uma etapa do funil -- FOTOGRAFIA da distribuição atual dos leads por
    coluna do Kanban (quantos estão em cada uma agora), não uma taxa de
    conversão real de coorte. Não existe uma tabela de histórico de
    transição de status neste projeto -- só o `kanban_status` atual de
    cada `Lead` -- então não há como saber "quantos leads que passaram
    pela etapa X avançaram pra Y" sem reconstruir esse histórico. Ver
    `app/api/routes/dashboard.py::get_funil` e CLAUDE.md "Dashboard
    (Fase 2)" pra essa limitação por extenso.

    9 etapas no total desde 21/08/2026, "perdido" incluído como última
    entrada -- `percentual` é sobre o total de leads (todos, perdidos
    inclusive), não mais só os "não perdidos" (mudou na mesma sessão,
    pro total bater com `DashboardSummary.taxa_conversao` e com a
    contagem real do Kanban).
    """

    status: str
    label: str
    quantidade: int
    percentual: float


class MotivoPerda(BaseModel):
    motivo: str
    quantidade: int
