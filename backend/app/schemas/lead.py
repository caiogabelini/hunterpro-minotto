from datetime import datetime

from pydantic import BaseModel, ConfigDict


class LeadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    cnpj: str
    razao_social: str
    nome_fantasia: str | None = None
    cnae_principal: str | None = None
    municipio: str | None = None
    uf: str | None = None
    anos_mercado: int | None = None
    simples_nacional: bool | None = None
    situacao_cadastral: str | None = None
    telefone: str | None = None
    whatsapp_ativo: bool | None = None
    email: str | None = None
    email_validado: bool | None = None
    decisor_nome: str | None = None
    site: str | None = None
    instagram: str | None = None
    linkedin_empresa: str | None = None
    linkedin_decisor: str | None = None
    google_rating: float | None = None
    google_avaliacoes: int | None = None
    servicos: str | None = None
    rqe_confirmado: bool | None = None
    rqe_fonte: str | None = None
    cnes_codigo: str | None = None
    divida_ativa_qtd_inscricoes: int | None = None
    divida_ativa_valor_total: float | None = None
    divida_ativa_ajuizado: bool | None = None
    zona_franca: bool | None = None
    score_total: float | None = None
    score_detalhes: dict | None = None
    prioridade: str | None = None
    kanban_status: str
    motivo_perda: str | None = None
    mensagem_abordagem: str | None = None
    created_at: datetime
    updated_at: datetime


class LeadStatusUpdate(BaseModel):
    kanban_status: str
    motivo_perda: str | None = None
