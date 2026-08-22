"""
Registro de execução de uma "busca mensal" -- o disparo manual (só
admin, ver app/api/routes/admin.py) do pipeline completo:
`gerar_semente_leads` uma vez, depois `process_lead_pipeline` pra cada
CNPJ encontrado. Uma linha por execução, nunca sobrescrita -- histórico
de quando cada busca rodou, quantos CNPJs/leads processou e que erros
individuais aconteceram (sem que um erro isolado derrube a busca
inteira, ver app/workers/celery_app.py::executar_busca_mensal).
"""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.tempo import agora_utc


class StatusBusca(str, Enum):
    EXECUTANDO = "executando"
    CONCLUIDO = "concluido"
    ERRO = "erro"


class BuscaLeads(Base):
    __tablename__ = "buscas_leads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    iniciado_por_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    iniciado_em: Mapped[datetime] = mapped_column(DateTime, default=agora_utc)
    # None até a task terminar (com sucesso ou erro) -- é o próprio
    # `concluido_em is None` que sinaliza "ainda executando" pro
    # polling do frontend, junto com `status`.
    concluido_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=StatusBusca.EXECUTANDO.value)
    # None até gerar_semente_leads terminar (a busca começa sem saber
    # quantos CNPJs vai encontrar).
    total_cnpjs_encontrados: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Quantos CNPJs sobreviveram à PRÉ-SELEÇÃO e de fato entraram no
    # enriquecimento pago (top N por score preliminar -- ver
    # app/scoring/pre_selecao.py). Fica entre `total_cnpjs_encontrados`
    # (o universo, ordem de centenas de milhares) e
    # `total_leads_processados` (quantos concluíram sem estourar).
    # Sem esta coluna o painel mostraria "250.000 encontrados / 58
    # processados", que se lê como 99,98% de falha em vez de "o corte
    # funcionou". None em buscas anteriores a 22/08/2026, quando a
    # pré-seleção não existia e todo CNPJ do universo era processado.
    total_cnpjs_selecionados: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # None até a task terminar de processar todos os CNPJs.
    total_leads_processados: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Lista de strings "CNPJ: mensagem de erro" -- um erro por CNPJ que
    # falhou, não um traceback completo (ver executar_busca_mensal).
    # None enquanto a busca ainda não terminou (mesmo com zero erros até
    # agora) -- vira lista (mesmo que vazia, `[]`) só quando conclui.
    erros: Mapped[list | None] = mapped_column(JSON, nullable=True)
