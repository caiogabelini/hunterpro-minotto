"""
`Lead.site_scrape_sucesso` -- distinguir "site lido, não publica" de
"site nunca lido" (23/08/2026).
================================================================================

Achado ao investigar por que 5 de 6 leads da busca real não tinham
WhatsApp. Três eram legítimos (o site realmente não publica link
`wa.me`), um não tinha site, e um (IBEN) simplesmente **nunca teve o site
lido** -- e isso só foi possível descobrir por inferência indireta no
`score_detalhes` (o critério `presenca_digital` zerado apesar de haver
site).

No banco e no dossiê os dois casos eram indistinguíveis: instagram,
serviços e whatsapp_site nulos podiam significar "não tem" ou "não
lemos". São conclusões comerciais opostas.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.workers import celery_app
from app.workers.celery_app import _aviso_scrapes_falhos

CNPJ = "11222333000181"


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _persistir(db, site_firecrawl):
    return celery_app._persistir_enriquecimento_lead(
        db=db, cnpj=CNPJ, razao_social="CLINICA TESTE",
        receita_federal=None, pgfn=None, zona_franca=None, cnes=None,
        google_places=None, site_firecrawl=site_firecrawl,
        site_url=None, whatsapp=None, email=None,
    )


# ---------------------------------------------------------------------------
# Os 3 estados
# ---------------------------------------------------------------------------

def test_none_quando_a_etapa_nunca_rodou(db_session):
    """Lead sem site conhecido (ou FIRECRAWL_API_KEY ausente): a etapa é
    pulada e `site_firecrawl` chega None. Não é falha -- é ausência."""
    lead = _persistir(db_session, None)
    assert lead.site_scrape_sucesso is None


def test_true_quando_o_firecrawl_leu_o_site(db_session):
    lead = _persistir(db_session, {
        "scrape_sucesso": True, "site_content_resumo": "conteúdo real",
        "instagram": "clinicateste", "whatsapp_site": None, "servicos": ["Ortopedia"],
    })
    assert lead.site_scrape_sucesso is True


def test_false_quando_o_scrape_falhou(db_session):
    """O caso do IBEN: tem site, mas o Firecrawl devolveu
    `success: false` -- dict cheio de vazios, sem exceção nenhuma."""
    lead = _persistir(db_session, {
        "scrape_sucesso": False, "site_content_resumo": "",
        "instagram": None, "whatsapp_site": None, "servicos": [],
    })
    assert lead.site_scrape_sucesso is False


def test_false_e_true_sao_distinguiveis_com_os_mesmos_campos_vazios(db_session):
    """O ponto da coluna: os dois cenários abaixo gravam exatamente os
    mesmos nulos em instagram/servicos, e antes disso eram idênticos."""
    lead_lido = _persistir(db_session, {
        "scrape_sucesso": True, "site_content_resumo": "site sem redes sociais",
        "instagram": None, "whatsapp_site": None, "servicos": [],
    })
    assert (lead_lido.instagram, lead_lido.servicos) == (None, None)
    assert lead_lido.site_scrape_sucesso is True

    db_session.rollback()
    lead_nao_lido = _persistir(db_session, {
        "scrape_sucesso": False, "site_content_resumo": "",
        "instagram": None, "whatsapp_site": None, "servicos": [],
    })
    assert (lead_nao_lido.instagram, lead_nao_lido.servicos) == (None, None)
    assert lead_nao_lido.site_scrape_sucesso is False


def test_campo_ausente_no_dict_conta_como_falha(db_session):
    """Defensivo: um retorno malformado da etapa é 'não lido', nunca
    'lido com sucesso' -- o sentido do erro tem que ser conservador."""
    lead = _persistir(db_session, {"instagram": None, "servicos": []})
    assert lead.site_scrape_sucesso is False


def test_pipeline_expoe_o_estado_no_retorno(monkeypatch, db_session):
    monkeypatch.setattr(celery_app, "enrich_receita_federal",
                        lambda c: {"encontrado": True, "razao_social": "X"})
    monkeypatch.setattr(celery_app, "enrich_pgfn_divida_ativa", lambda c: {"qtd_inscricoes_ativas": 0})
    monkeypatch.setattr(celery_app, "search_google_places",
                        lambda e: [{**e[0], "site": "https://x.com", "telefone_google": None}])
    monkeypatch.setattr(celery_app, "enrich_site_firecrawl",
                        lambda url: {"site_url": url, "scrape_sucesso": False,
                                     "site_content_resumo": "", "instagram": None,
                                     "whatsapp_site": None, "rqe_mencionado": False,
                                     "rqe_numero": None, "servicos": []})
    monkeypatch.setattr(celery_app, "enrich_email", lambda d, n: {"email": None, "email_aprovado": False})
    monkeypatch.setattr(celery_app.settings, "FIRECRAWL_API_KEY", "presente")

    r = celery_app.process_lead_pipeline(CNPJ, razao_social="X", db=db_session)
    assert r["site_scrape_sucesso"] is False


# ---------------------------------------------------------------------------
# Aviso agregado em busca.erros
# ---------------------------------------------------------------------------

def test_sem_scrape_falho_nao_gera_aviso():
    assert _aviso_scrapes_falhos([], 6) is None


def test_aviso_traz_contagem_e_cnpjs():
    aviso = _aviso_scrapes_falhos(["06186989000156", "08687827000127"], 6)
    assert "2 de 6" in aviso
    assert "06186989000156" in aviso and "08687827000127" in aviso


def test_aviso_explica_que_vazio_nao_significa_ausencia():
    """A frase é o ponto do aviso -- sem ela o admin lê os campos vazios
    como 'a empresa não tem', que é a conclusão errada."""
    aviso = _aviso_scrapes_falhos(["06186989000156"], 6)
    assert "NÃO porque a empresa não os tenha" in aviso


def test_aviso_trunca_lista_longa():
    aviso = _aviso_scrapes_falhos([f"{i:014d}" for i in range(9)], 20)
    assert "9 de 20" in aviso
    assert "e mais 4" in aviso           # 9 - 5 listados
    assert "00000000000005" not in aviso  # além do corte


# O teste de ponta a ponta do aviso em `busca.erros` mora em
# tests/test_executar_busca_mensal.py, junto das fixtures (redis_fake,
# sem_tabela_de_municipios) que a orquestração precisa -- duplicá-las
# aqui só pra um caso seria pior que a separação.
