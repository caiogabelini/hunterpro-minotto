from datetime import datetime

from pydantic import BaseModel, ConfigDict


class LeadMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    lead_id: str
    canal: str
    conteudo: str
    assunto: str | None = None
    gerado_em: datetime
