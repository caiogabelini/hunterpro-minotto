"""
Histórico de mensagens de abordagem geradas por canal (e-mail/WhatsApp)
pra um lead.

Substitui `Lead.mensagem_abordagem` (ver nota de deprecação em
app/models/lead.py) — aquele campo guardava uma única mensagem
genérica, sobrescrita a cada regeneração, sem canal e sem histórico.
Cada geração aqui vira uma linha nova (nunca sobrescreve); `GET
/api/leads/{lead_id}/mensagens` devolve só a mais recente por canal,
mas o histórico completo continua no banco.
"""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.tempo import agora_utc


class CanalMensagem(str, Enum):
    """Canais suportados pra geração de mensagem de abordagem. Instagram
    de propósito fora por enquanto (ver PARTE 2 do pedido que criou este
    módulo) — não mostrar essa opção no dossiê ainda."""

    EMAIL = "email"
    WHATSAPP = "whatsapp"


class LeadMessage(Base):
    __tablename__ = "lead_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    lead_id: Mapped[str] = mapped_column(String, ForeignKey("leads.id"), index=True)
    canal: Mapped[str] = mapped_column(String(10))  # "email" | "whatsapp" (CanalMensagem.value)
    conteudo: Mapped[str] = mapped_column(Text)
    assunto: Mapped[str | None] = mapped_column(Text, nullable=True)  # só faz sentido pro canal "email"
    gerado_em: Mapped[datetime] = mapped_column(DateTime, default=agora_utc)
