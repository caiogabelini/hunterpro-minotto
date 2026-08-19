from datetime import datetime

from pydantic import BaseModel, ConfigDict


class LeadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    cnpj: str
    razao_social: str
    nome_fantasia: str | None = None
    municipio: str | None = None
    uf: str | None = None
    whatsapp_ativo: bool | None = None
    email: str | None = None
    email_validado: bool | None = None
    decisor_nome: str | None = None
    rqe_confirmado: bool | None = None
    divida_ativa_qtd_inscricoes: int | None = None
    zona_franca: bool | None = None
    score_total: float | None = None
    score_detalhes: dict | None = None
    prioridade: str | None = None
    created_at: datetime
