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
    emails_secundarios: list | None = None
    decisor_nome: str | None = None
    site: str | None = None
    site_scrape_sucesso: bool | None = None
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
    servicos_vendidos: list[str] | None = None
    tipo_contrato: str | None = None
    valor_fechamento: float | None = None
    mensagem_abordagem: str | None = None
    insights_ia: dict | None = None
    insights_gerado_em: datetime | None = None
    created_at: datetime
    updated_at: datetime


class LeadStatusUpdate(BaseModel):
    kanban_status: str
    motivo_perda: str | None = None
    # Só obrigatórios quando kanban_status == "ganho" -- validado na
    # rota (PATCH /{lead_id}/status), não aqui no schema, mesmo padrão
    # de motivo_perda (só obrigatório quando kanban_status == "perdido").
    servicos_vendidos: list[str] | None = None
    tipo_contrato: str | None = None
    valor_fechamento: float | None = None


class LeadListaResponse(BaseModel):
    """Resposta paginada de GET /api/leads/lista (ver
    app/api/routes/leads.py::listar_leads_paginado) -- endpoint
    independente de `list_leads`/`GET /api/leads`, não um substituto."""

    items: list[LeadRead]
    total: int
    pagina: int
    por_pagina: int
