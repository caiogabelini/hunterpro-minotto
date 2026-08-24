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
from app.core.tempo import agora_utc


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
    # Demais e-mails que o `domain-search` do Hunter trouxe na MESMA
    # resposta que produziu `email` -- lista de
    # {"email", "tipo": "personal"|"generic"|None, "confidence"}.
    #
    # Só é populado quando o fallback `domain-search` roda
    # (settings.HUNTER_DOMAIN_SEARCH_FALLBACK, desligada por padrão) E
    # a resposta traz mais de um endereço. Guardar o resto não custa
    # crédito nenhum: o Hunter cobra por REQUISIÇÃO, não por e-mail
    # devolvido, e a chamada já paga traz até 10.
    #
    # ⚠️ `NULL` = "o domain-search não rodou pra este lead"; lista vazia
    # não é gravada (vira NULL). O e-mail escolhido como principal
    # (`email`) NUNCA aparece aqui -- ver
    # app/services/email_enrichment.py::buscar_emails_do_dominio.
    emails_secundarios: Mapped[list | None] = mapped_column(JSON, nullable=True)
    decisor_nome: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Presença digital ---
    site: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # O Firecrawl conseguiu LER o site? Três estados, e a diferença entre
    # os dois últimos é o ponto da coluna:
    #   None  -> a etapa nunca rodou (lead sem site conhecido, ou
    #            FIRECRAWL_API_KEY ausente)
    #   True  -> leu com sucesso; campos de presença digital vazios
    #            significam "o site não publica isso"
    #   False -> tentou e não leu (Firecrawl devolveu success:false, ou a
    #            etapa levantou exceção)
    #
    # ⚠️ Sem esta coluna, os dois últimos casos eram INDISTINGUÍVEIS no
    # banco e no dossiê: instagram/serviços/whatsapp_site nulos podiam
    # significar "não tem" ou "nunca lemos". São conclusões comerciais
    # opostas -- a primeira encerra o assunto, a segunda pede nova
    # tentativa. Achado em 23/08/2026 ao investigar por que 5 de 6 leads
    # não tinham WhatsApp: um deles (IBEN) simplesmente nunca teve o site
    # lido, e só deu pra descobrir por inferência indireta no
    # `score_detalhes` (o critério `presenca_digital` zerado).
    site_scrape_sucesso: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
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

    # --- Fechamento (quando kanban_status == "ganho") ---
    # Espelha motivo_perda acima, do lado positivo -- captura o que foi
    # efetivamente vendido quando o lead fecha. Validado na rota (não a
    # nível de banco, mesma razão de motivo_perda: as colunas ficam
    # nullable porque só fazem sentido nesse status específico -- ver
    # PATCH /{lead_id}/status em app/api/routes/leads.py).
    # `servicos_vendidos` aceita qualquer string na lista -- os 4 valores
    # fixos que o frontend oferece como checkbox ("abertura_legalizacao",
    # "contabilidade_consultiva", "consultoria_empresarial",
    # "planejamento_tributario") não são um enum fechado aqui; texto
    # livre digitado em "Outro" também é uma entrada válida da lista.
    # Validação de opções fixas + "outro" é responsabilidade do
    # frontend, não deste modelo.
    servicos_vendidos: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tipo_contrato: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "pontual" | "recorrente"
    valor_fechamento: Mapped[float | None] = mapped_column(Float, nullable=True)  # R$ -- único se pontual, mensal se recorrente

    # --- Abordagem ---
    # ⚠️ DEPRECATED (20/08/2026) -- substituído por app/models/lead_message.py::LeadMessage,
    # que guarda uma mensagem por CANAL (email/whatsapp) com histórico completo (nunca
    # sobrescreve), em vez de uma única mensagem genérica sobrescrita a cada regeneração.
    # Mantido só pra não perder dado de teste já gravado; sem uso novo -- não remover a
    # coluna, mas não escrever nem ler daqui em código novo. Ver "Suggested approach
    # message generation" no CLAUDE.md pro histórico completo da migração pra LeadMessage.
    mensagem_abordagem: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Insights estratégicos (IA) ---
    # Objeto estruturado (resumo_estrategico, potencial_oportunidade,
    # recomendacao_abordagem, estrategia_comunicacao, cta_sugerido) gerado
    # sob demanda por app/services/ai_enrichment.py::gerar_insights_estrategicos,
    # mesmo padrão de "botão Gerar" já usado pra LeadMessage -- não roda
    # automaticamente no pipeline. insights_gerado_em fica None até a
    # primeira geração; cada nova geração sobrescreve os dois campos (sem
    # histórico, diferente de LeadMessage -- não foi pedido).
    insights_ia: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    insights_gerado_em: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Quantas vezes os insights já foram gerados pra este lead. Cada
    # geração é uma chamada paga à API da Anthropic, e `insights_ia` é
    # SOBRESCRITO a cada uma (não há histórico, diferente de
    # `lead_messages`) -- sem este contador não haveria como saber quantas
    # aconteceram. Ver `app/api/routes/limites_ia.py`.
    insights_geracoes_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Marca d'água de liberação do limite de IA, por tipo:
    # {"email": "<iso>", "whatsapp": "<iso>", "insights": "<iso>"}.
    #
    # ⚠️ Existe porque e-mail/WhatsApp contam gerações pelas LINHAS de
    # `lead_messages`, e resetar o limite deletando essas linhas destruiria
    # o histórico -- que é a razão de aquela tabela existir. Em vez disso,
    # o reset grava um instante, e a contagem passa a considerar só as
    # gerações POSTERIORES a ele. Pra `insights` o reset zera
    # `insights_geracoes_count` e o carimbo aqui fica só como trilha de
    # auditoria (quando um admin liberou).
    ia_limite_resetado_em: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=agora_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=agora_utc, onupdate=agora_utc)
