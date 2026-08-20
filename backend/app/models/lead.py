"""
Modelo de Lead — HunterPro Minotto Contabilidade.

Campos divididos em três blocos:
  1. Padrão (mesmo schema já validado no projeto SR Contábil)
  2. Específicos do nicho de saúde (RQE via CNES/inferência)
  3. Específicos da Minotto (dívida ativa PGFN, Zona Franca)

Este é o esqueleto do modelo — os campos de enriquecimento (ETAPA 1 a
12 do pipeline) serão preenchidos pelos workers do Celery nas próximas
sessões, seguindo o mesmo padrão do projeto anterior.
"""
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class KanbanStatus(str, Enum):
    """Colunas fixas do quadro Kanban (frontend) — ordem de funil, não
    ordem alfabética. `motivo_perda` só faz sentido quando o status é
    PERDIDO (validado na rota, não a nível de banco)."""

    NOVO_LEAD = "novo_lead"
    QUALIFICACAO = "qualificacao"
    CONTATADO = "contatado"
    RESPONDEU = "respondeu"
    REUNIAO = "reuniao"
    PROPOSTA_ENVIADA = "proposta_enviada"
    NEGOCIACAO = "negociacao"
    GANHO = "ganho"
    PERDIDO = "perdido"


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    # --- Identificação (Receita Federal) ---
    cnpj: Mapped[str] = mapped_column(String(18), unique=True, index=True)
    razao_social: Mapped[str] = mapped_column(String(255))
    nome_fantasia: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cnae_principal: Mapped[str | None] = mapped_column(String(10), nullable=True)
    municipio: Mapped[str | None] = mapped_column(String(120), nullable=True)
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True)
    anos_mercado: Mapped[int | None] = mapped_column(Integer, nullable=True)
    simples_nacional: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    situacao_cadastral: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # --- Contato ---
    telefone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    whatsapp_ativo: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_validado: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    decisor_nome: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Presença digital ---
    site: Mapped[str | None] = mapped_column(String(255), nullable=True)
    instagram: Mapped[str | None] = mapped_column(String(120), nullable=True)
    linkedin_empresa: Mapped[str | None] = mapped_column(String(255), nullable=True)
    linkedin_decisor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    google_avaliacoes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    servicos: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Específico do nicho de saúde ---
    rqe_confirmado: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    rqe_fonte: Mapped[str | None] = mapped_column(String(50), nullable=True)  # "cnes" | "inferencia_ia" | None
    cnes_codigo: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # --- Específico da Minotto ---
    divida_ativa_qtd_inscricoes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # valor_total e algum_ajuizado -- app/services/pgfn.py::PgfnDebtSummary já calcula os
    # dois (build_pgfn_index/lookup_debt_from_redis), mas até 20/08/2026 só a contagem
    # (qtd_inscricoes_ativas) era persistida no Lead. Adicionados nesta sessão.
    divida_ativa_valor_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    divida_ativa_ajuizado: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    zona_franca: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # --- Score ---
    score_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_detalhes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    prioridade: Mapped[str | None] = mapped_column(String(1), nullable=True)  # A | B | C

    # --- Kanban (pipeline comercial) ---
    kanban_status: Mapped[str] = mapped_column(String(20), default=KanbanStatus.NOVO_LEAD.value)
    motivo_perda: Mapped[str | None] = mapped_column(Text, nullable=True)  # só relevante quando kanban_status == "perdido"

    # --- Abordagem ---
    # ⚠️ DEPRECATED (20/08/2026) -- substituído por app/models/lead_message.py::LeadMessage,
    # que guarda uma mensagem por CANAL (email/whatsapp) com histórico completo (nunca
    # sobrescreve), em vez de uma única mensagem genérica sobrescrita a cada regeneração.
    # Mantido só pra não perder dado de teste já gravado; sem uso novo -- não remover a
    # coluna, mas não escrever nem ler daqui em código novo. Ver "Suggested approach
    # message generation" no CLAUDE.md pro histórico completo da migração pra LeadMessage.
    mensagem_abordagem: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
