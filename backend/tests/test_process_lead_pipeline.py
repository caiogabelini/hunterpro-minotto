"""
Testes da task orquestradora `process_lead_pipeline` (app/workers/celery_app.py).

Cada etapa sibling (enrich_receita_federal, enrich_cnes, ...) é
substituída por um fake via monkeypatch, direto no namespace do módulo
`app.workers.celery_app` — evita rede real, Redis real e Postgres real
(a persistência usa uma sessão SQLAlchemy em SQLite em memória, injetada
via `db=`, mesmo padrão de client injetável do resto do projeto).
"""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.lead import Lead
from app.workers import celery_app

CNPJ = "11222333000181"

RECEITA_FEDERAL_OK = {
    "cnpj": CNPJ,
    "encontrado": True,
    "razao_social": "CLINICA TESTE LTDA",
    "nome_fantasia": "Clinica Teste",
    "situacao_cadastral": "ATIVA",
    "cnae_principal": "8630501",
    "porte": "DEMAIS",
    "municipio": "SAO PAULO",
    "uf": "SP",
    "codigo_municipio_ibge": 3550308,
    "codigo_municipio_cnes": 355030,
    "anos_mercado": 5,
    "simples_nacional": True,
    "decisor_nome": "CICLANA ADMINISTRADORA",
}


def _db_session_sqlite():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _fake_receita_federal(cnpj):
    return dict(RECEITA_FEDERAL_OK, cnpj=cnpj)


def _fake_pgfn(cnpj):
    return {"cnpj": cnpj, "qtd_inscricoes_ativas": 3, "valor_total": 1000.0, "algum_ajuizado": False, "pontos_score": 10.0}


def _fake_zona_franca(municipio, uf):
    assert municipio == "SAO PAULO"
    assert uf == "SP"
    return False


def _fake_cnes(cnpj, codigo_municipio):
    assert codigo_municipio == 355030  # prova que veio de enrich_receita_federal, não de um valor fixo
    return {"cnpj": cnpj, "encontrado_cnes": True, "cnes_codigo": "9629866", "codigo_tipo_unidade": 22}


def _fake_whatsapp(telefone):
    return {"telefone_original": telefone, "numero_formatado": f"55{telefone}", "numero_valido": True, "whatsapp_ativo": True}


def _fake_email(dominio, decisor_nome):
    # decisor_nome é opcional de verdade (enrich_email aceita None -- ver
    # celery_app.py) -- quando presente, tem que ser o valor real vindo
    # de enrich_receita_federal, não None nem um valor fixo qualquer.
    assert decisor_nome in ("CICLANA ADMINISTRADORA", None)
    return {
        "dominio": dominio,
        "email": f"contato@{dominio}",
        "hunter_score": 90,
        "mx_valido": True,
        "zerobounce_status": "valid",
        "email_aprovado": True,
    }


def _fake_site_firecrawl(site_url):
    return {
        "site_url": site_url,
        "scrape_sucesso": True,
        "site_content_resumo": "# Clinica Teste\nAtendemos com RQE 12345.",
        "instagram": "clinicateste",
        "rqe_mencionado": True,
        "rqe_numero": "12345",
        "servicos": ["Cardiologia"],
    }


def _patch_todas_as_etapas(monkeypatch):
    monkeypatch.setattr(celery_app, "enrich_receita_federal", _fake_receita_federal)
    monkeypatch.setattr(celery_app, "enrich_pgfn_divida_ativa", _fake_pgfn)
    monkeypatch.setattr(celery_app, "enrich_zona_franca", _fake_zona_franca)
    monkeypatch.setattr(celery_app, "enrich_cnes", _fake_cnes)
    monkeypatch.setattr(celery_app, "validate_whatsapp", _fake_whatsapp)
    monkeypatch.setattr(celery_app, "enrich_email", _fake_email)
    monkeypatch.setattr(celery_app, "enrich_site_firecrawl", _fake_site_firecrawl)
    monkeypatch.setattr(celery_app.settings, "FIRECRAWL_API_KEY", "chave-de-teste")


# --- ordem de execução respeitando dependências ---


def test_ordem_respeita_dependencias(monkeypatch):
    chamadas: list[str] = []

    def receita_federal_rastreada(cnpj):
        chamadas.append("enrich_receita_federal")
        return _fake_receita_federal(cnpj)

    def zona_franca_rastreada(municipio, uf):
        chamadas.append("enrich_zona_franca")
        return _fake_zona_franca(municipio, uf)

    def cnes_rastreado(cnpj, codigo_municipio):
        chamadas.append("enrich_cnes")
        return _fake_cnes(cnpj, codigo_municipio)

    def email_rastreado(dominio, decisor_nome):
        chamadas.append("enrich_email")
        return _fake_email(dominio, decisor_nome)

    def pgfn_rastreado(cnpj):
        chamadas.append("enrich_pgfn_divida_ativa")
        return _fake_pgfn(cnpj)

    monkeypatch.setattr(celery_app, "enrich_receita_federal", receita_federal_rastreada)
    monkeypatch.setattr(celery_app, "enrich_pgfn_divida_ativa", pgfn_rastreado)
    monkeypatch.setattr(celery_app, "enrich_zona_franca", zona_franca_rastreada)
    monkeypatch.setattr(celery_app, "enrich_cnes", cnes_rastreado)
    monkeypatch.setattr(celery_app, "validate_whatsapp", _fake_whatsapp)
    monkeypatch.setattr(celery_app, "enrich_email", email_rastreado)
    monkeypatch.setattr(celery_app, "enrich_site_firecrawl", _fake_site_firecrawl)
    monkeypatch.setattr(celery_app.settings, "FIRECRAWL_API_KEY", "chave-de-teste")

    celery_app.process_lead_pipeline(
        cnpj=CNPJ,
        site_url="https://clinicateste.com.br",
        telefone="11987654321",
        db=_db_session_sqlite(),
    )

    # enrich_receita_federal precisa vir antes de tudo que depende dos
    # dados dela -- não é uma ordem arbitrária, é a dependência real
    assert chamadas.index("enrich_receita_federal") < chamadas.index("enrich_zona_franca")
    assert chamadas.index("enrich_receita_federal") < chamadas.index("enrich_cnes")
    assert chamadas.index("enrich_receita_federal") < chamadas.index("enrich_email")
    # PGFN é independente -- só garantimos que ela roda, não uma posição fixa
    assert "enrich_pgfn_divida_ativa" in chamadas


def test_cnes_recebe_codigo_municipio_vindo_da_receita_federal(monkeypatch):
    """Prova que a dependência é de DADO, não só de ordem -- se
    enrich_cnes recebesse um valor fixo em vez do codigo_municipio_cnes
    de fato devolvido por enrich_receita_federal, o assert dentro de
    _fake_cnes (chamado via process_lead_pipeline) quebraria."""
    _patch_todas_as_etapas(monkeypatch)

    resultado = celery_app.process_lead_pipeline(
        cnpj=CNPJ,
        site_url="https://clinicateste.com.br",
        telefone="11987654321",
        db=_db_session_sqlite(),
    )

    assert "enrich_cnes" in resultado["etapas_executadas"]


def test_email_recebe_decisor_nome_vindo_da_receita_federal(monkeypatch):
    """Mesma prova de dependência de dado que o teste do CNES acima, pro
    decisor_nome que flui de enrich_receita_federal pra enrich_email."""
    recebido = {}

    def email_capturado(dominio, decisor_nome):
        recebido["decisor_nome"] = decisor_nome
        return _fake_email(dominio, decisor_nome)

    monkeypatch.setattr(celery_app, "enrich_receita_federal", _fake_receita_federal)
    monkeypatch.setattr(celery_app, "enrich_pgfn_divida_ativa", _fake_pgfn)
    monkeypatch.setattr(celery_app, "enrich_zona_franca", _fake_zona_franca)
    monkeypatch.setattr(celery_app, "enrich_cnes", _fake_cnes)
    monkeypatch.setattr(celery_app, "enrich_email", email_capturado)

    celery_app.process_lead_pipeline(cnpj=CNPJ, site_url="https://clinicateste.com.br", db=_db_session_sqlite())

    assert recebido["decisor_nome"] == "CICLANA ADMINISTRADORA"


# --- pipeline completo, tudo mockado ---


def test_pipeline_completo_score_e_etapas(monkeypatch):
    _patch_todas_as_etapas(monkeypatch)
    db = _db_session_sqlite()

    resultado = celery_app.process_lead_pipeline(
        cnpj=CNPJ,
        razao_social="CLINICA TESTE LTDA",
        site_url="https://clinicateste.com.br",
        telefone="11987654321",
        db=db,
    )

    etapas_esperadas = {
        "enrich_receita_federal",
        "enrich_pgfn_divida_ativa",
        "enrich_zona_franca",
        "enrich_cnes",
        "enrich_site_firecrawl",
        "validate_whatsapp",
        "enrich_email",
    }
    assert set(resultado["etapas_executadas"]) == etapas_esperadas
    assert "search_google_places" in resultado["etapas_puladas"]  # task não implementada, sempre pulada

    assert resultado["score_total"] > 0
    assert len(resultado["breakdown"]) == 9  # os 9 critérios de SCORING_CRITERIA

    pontos = {item["key"]: item["points"] for item in resultado["breakdown"]}
    assert pontos["divida_ativa_pgfn"] == 10.0  # pgfn_points(3) com peso 20
    assert pontos["zona_franca_alc"] == 0.0
    assert pontos["rqe_especialista"] == 15.0  # IA e CNES concordam positivo
    assert pontos["whatsapp_ativo"] == 10.0
    assert pontos["email_validado"] == 10.0
    assert pontos["decisor_identificavel"] == 10.0
    assert pontos["unidade_cnpj_ativo"] == 5.0


# --- comportamento quando uma etapa falha ---


def test_falha_em_uma_etapa_nao_derruba_as_outras(monkeypatch):
    def cnes_com_falha(cnpj, codigo_municipio):
        raise ConnectionError("CNES fora do ar")

    monkeypatch.setattr(celery_app, "enrich_receita_federal", _fake_receita_federal)
    monkeypatch.setattr(celery_app, "enrich_pgfn_divida_ativa", _fake_pgfn)
    monkeypatch.setattr(celery_app, "enrich_zona_franca", _fake_zona_franca)
    monkeypatch.setattr(celery_app, "enrich_cnes", cnes_com_falha)
    monkeypatch.setattr(celery_app, "validate_whatsapp", _fake_whatsapp)
    monkeypatch.setattr(celery_app, "enrich_email", _fake_email)
    monkeypatch.setattr(celery_app, "enrich_site_firecrawl", _fake_site_firecrawl)
    monkeypatch.setattr(celery_app.settings, "FIRECRAWL_API_KEY", "chave-de-teste")

    resultado = celery_app.process_lead_pipeline(
        cnpj=CNPJ,
        site_url="https://clinicateste.com.br",
        telefone="11987654321",
        db=_db_session_sqlite(),
    )

    assert "enrich_cnes" not in resultado["etapas_executadas"]
    # as outras etapas, incluindo as que rodam DEPOIS do CNES na ordem,
    # continuam rodando normalmente
    assert "enrich_email" in resultado["etapas_executadas"]
    assert "validate_whatsapp" in resultado["etapas_executadas"]
    assert "enrich_site_firecrawl" in resultado["etapas_executadas"]
    assert resultado["score_total"] is not None  # score ainda foi calculado


def test_falha_em_receita_federal_pula_etapas_dependentes_mas_nao_as_independentes(monkeypatch):
    def receita_federal_com_falha(cnpj):
        raise TimeoutError("BrasilAPI não respondeu")

    monkeypatch.setattr(celery_app, "enrich_receita_federal", receita_federal_com_falha)
    monkeypatch.setattr(celery_app, "enrich_pgfn_divida_ativa", _fake_pgfn)
    monkeypatch.setattr(celery_app, "enrich_zona_franca", _fake_zona_franca)
    monkeypatch.setattr(celery_app, "enrich_cnes", _fake_cnes)
    monkeypatch.setattr(celery_app, "validate_whatsapp", _fake_whatsapp)
    monkeypatch.setattr(celery_app, "enrich_email", _fake_email)
    monkeypatch.setattr(celery_app, "enrich_site_firecrawl", _fake_site_firecrawl)
    monkeypatch.setattr(celery_app.settings, "FIRECRAWL_API_KEY", "chave-de-teste")

    resultado = celery_app.process_lead_pipeline(
        cnpj=CNPJ,
        razao_social="CLINICA TESTE LTDA",
        site_url="https://clinicateste.com.br",
        telefone="11987654321",
        db=_db_session_sqlite(),
    )

    # dependem estritamente de dados de enrich_receita_federal -> pulados
    assert "enrich_receita_federal" not in resultado["etapas_executadas"]
    assert "enrich_zona_franca" not in resultado["etapas_executadas"]
    assert "enrich_cnes" not in resultado["etapas_executadas"]
    assert "enrich_zona_franca" in resultado["etapas_puladas"]
    assert "enrich_cnes" in resultado["etapas_puladas"]

    # independentes de enrich_receita_federal -> continuam rodando.
    # enrich_email é o caso interessante: precisa do domínio (que veio de
    # site_url, não de receita_federal) -- decisor_nome é opcional, então
    # a etapa roda mesmo com receita_federal tendo falhado (só com busca
    # menos precisa no Hunter.io, ver _fake_email/celery_app.py)
    assert "enrich_pgfn_divida_ativa" in resultado["etapas_executadas"]
    assert "validate_whatsapp" in resultado["etapas_executadas"]
    assert "enrich_site_firecrawl" in resultado["etapas_executadas"]  # só depende de site_url
    assert "enrich_email" in resultado["etapas_executadas"]


def test_etapas_puladas_por_falta_de_site_url_e_telefone(monkeypatch):
    """Sem site_url nem telefone (gap real do pipeline hoje, ver
    docstring de process_lead_pipeline) -- pula firecrawl, whatsapp e
    email, sem quebrar."""
    monkeypatch.setattr(celery_app, "enrich_receita_federal", _fake_receita_federal)
    monkeypatch.setattr(celery_app, "enrich_pgfn_divida_ativa", _fake_pgfn)
    monkeypatch.setattr(celery_app, "enrich_zona_franca", _fake_zona_franca)
    monkeypatch.setattr(celery_app, "enrich_cnes", _fake_cnes)

    resultado = celery_app.process_lead_pipeline(cnpj=CNPJ, db=_db_session_sqlite())

    assert "enrich_site_firecrawl" in resultado["etapas_puladas"]
    assert "validate_whatsapp" in resultado["etapas_puladas"]
    assert "enrich_email" in resultado["etapas_puladas"]
    assert resultado["etapas_puladas"]["enrich_site_firecrawl"]  # motivo não vazio


def test_etapa_pulada_por_falta_de_firecrawl_api_key(monkeypatch):
    monkeypatch.setattr(celery_app, "enrich_receita_federal", _fake_receita_federal)
    monkeypatch.setattr(celery_app, "enrich_pgfn_divida_ativa", _fake_pgfn)
    monkeypatch.setattr(celery_app, "enrich_zona_franca", _fake_zona_franca)
    monkeypatch.setattr(celery_app, "enrich_cnes", _fake_cnes)
    monkeypatch.setattr(celery_app, "enrich_email", _fake_email)
    monkeypatch.setattr(celery_app.settings, "FIRECRAWL_API_KEY", "")  # não configurada

    resultado = celery_app.process_lead_pipeline(
        cnpj=CNPJ, site_url="https://clinicateste.com.br", db=_db_session_sqlite()
    )

    assert "enrich_site_firecrawl" in resultado["etapas_puladas"]
    assert "FIRECRAWL_API_KEY" in resultado["etapas_puladas"]["enrich_site_firecrawl"]


def test_google_places_sempre_pulada():
    """search_google_places ainda não está implementada -- sempre
    pulada, independente dos parâmetros."""
    resultado = celery_app.process_lead_pipeline(cnpj=CNPJ, db=_db_session_sqlite())
    assert "search_google_places" in resultado["etapas_puladas"]


# --- persistência no banco ---


def test_persiste_lead_com_dados_de_todas_as_etapas(monkeypatch):
    _patch_todas_as_etapas(monkeypatch)
    db = _db_session_sqlite()

    resultado = celery_app.process_lead_pipeline(
        cnpj=CNPJ,
        razao_social="CLINICA TESTE LTDA",
        site_url="https://clinicateste.com.br",
        telefone="11987654321",
        db=db,
    )

    lead = db.execute(select(Lead).where(Lead.cnpj == CNPJ)).scalar_one()

    assert lead.id == resultado["lead_id"]
    assert lead.razao_social == "CLINICA TESTE LTDA"
    assert lead.municipio == "SAO PAULO"
    assert lead.uf == "SP"
    assert lead.situacao_cadastral == "ATIVA"
    assert lead.decisor_nome == "CICLANA ADMINISTRADORA"
    assert lead.divida_ativa_qtd_inscricoes == 3
    assert lead.zona_franca is False
    assert lead.cnes_codigo == "9629866"
    assert lead.rqe_confirmado is True
    assert lead.rqe_fonte == "inferencia_ia"
    assert lead.site == "https://clinicateste.com.br"
    assert lead.instagram == "clinicateste"
    assert lead.servicos == "Cardiologia"
    assert lead.whatsapp_ativo is True
    assert lead.email == "contato@clinicateste.com.br"
    assert lead.email_validado is True
    assert lead.score_total == resultado["score_total"]
    assert lead.score_detalhes == {"breakdown": resultado["breakdown"]}


def test_persistencia_faz_upsert_nao_duplica_lead(monkeypatch):
    _patch_todas_as_etapas(monkeypatch)
    db = _db_session_sqlite()

    celery_app.process_lead_pipeline(cnpj=CNPJ, razao_social="CLINICA TESTE LTDA", db=db)
    celery_app.process_lead_pipeline(cnpj=CNPJ, razao_social="CLINICA TESTE LTDA", db=db)

    leads = db.execute(select(Lead).where(Lead.cnpj == CNPJ)).scalars().all()
    assert len(leads) == 1


def test_persiste_lead_mesmo_com_receita_federal_falhando(monkeypatch):
    """Sem receita_federal, razao_social cai pro parâmetro explícito (ou
    o CNPJ) -- não deve quebrar a constraint NOT NULL do modelo."""
    def receita_federal_com_falha(cnpj):
        raise TimeoutError("fora do ar")

    monkeypatch.setattr(celery_app, "enrich_receita_federal", receita_federal_com_falha)
    monkeypatch.setattr(celery_app, "enrich_pgfn_divida_ativa", _fake_pgfn)
    db = _db_session_sqlite()

    resultado = celery_app.process_lead_pipeline(cnpj=CNPJ, razao_social="NOME PROVISORIO", db=db)

    lead = db.execute(select(Lead).where(Lead.cnpj == CNPJ)).scalar_one()
    assert lead.razao_social == "NOME PROVISORIO"
    assert lead.divida_ativa_qtd_inscricoes == 3
    assert resultado["lead_id"] == lead.id


# --- _get_db_session ---


def test_get_db_session_usa_sessao_injetada():
    db = _db_session_sqlite()
    resultado, owns_session = celery_app._get_db_session(db)
    assert resultado is db
    assert owns_session is False


def test_get_db_session_cria_sessao_quando_nao_injetada():
    resultado, owns_session = celery_app._get_db_session(None)
    assert resultado is not None
    assert owns_session is True
    resultado.close()
