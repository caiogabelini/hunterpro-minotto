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
