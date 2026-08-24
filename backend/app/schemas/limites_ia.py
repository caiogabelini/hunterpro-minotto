"""
Schema da resposta do reset de limite de IA (ver
app/api/routes/limites_ia.py pro racional do controle de custo).
"""
from pydantic import BaseModel


class ResetLimiteIaRead(BaseModel):
    lead_id: str
    tipo: str
    # Contagem por tipo já ATUALIZADA + o limite vigente, no mesmo formato
    # de `LeadRead.geracoes_ia` -- assim o frontend pode reaproveitar o
    # estado sem refazer um GET do lead depois do reset.
    geracoes_ia: dict[str, int]
