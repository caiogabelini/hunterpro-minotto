from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BuscaLeadsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    iniciado_por_id: str
    iniciado_em: datetime
    concluido_em: datetime | None = None
    status: str
    total_cnpjs_encontrados: int | None = None
    total_cnpjs_selecionados: int | None = None
    total_leads_processados: int | None = None
    erros: list[str] | None = None
