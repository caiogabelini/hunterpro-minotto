"""
Correções da 2ª investigação da busca real (23/08/2026).
================================================================================

Duas causas raiz distintas, nenhuma delas erro de parsing:

  1. **WhatsApp** -- o schema da Evolution batia e a resposta
     `exists: false` estava CERTA: os 6 telefones vinham do Google
     Places, que devolve a central telefônica. Todos eram FIXOS (8
     dígitos locais começando com 2 ou 3); celular brasileiro tem 9
     começando com 9. A correção é de FONTE DE DADO: extrair o WhatsApp
     divulgado no próprio site, do markdown que o Firecrawl já raspa.
  2. **E-mail** -- o `email-finder` só responde "qual o e-mail DESTA
     PESSOA". Falhou em 4 de 5 domínios reais, mas 3 desses 5 tinham
     e-mail catalogado no `domain-search`. A correção é de ENDPOINT,
     atrás de uma flag desligada por padrão (dobra o consumo de créditos).
"""
import httpx
import pytest

from app.services import email_enrichment
from app.services.email_enrichment import (
    buscar_emails_do_dominio,
    enriquecer_email,
)
from app.services.site_scraping import extrair_whatsapp
from app.workers import celery_app

DOMINIO = "clinicateste.com.br"


@pytest.fixture(autouse=True)
def _protege_contra_chamadas_reais(monkeypatch):
    """Rede de segurança contra chamada paga acidental -- mesma lição de
    `test_process_lead_pipeline.py::_protege_contra_chamada_real_ao_google_places`
    (um teste que esqueceu de mockar uma etapa fez uma chamada real de
    ~21s ao Google com a chave de produção).

    Aqui o risco é maior: o Hunter é plano Free, 50 buscas/mês, e um
    teste que esqueça de mockar `enrich_email` queima crédito real e
    ainda deixa a suíte dependente de rede. Acontecendo de verdade
    enquanto este arquivo era escrito, aliás.

    Testes que precisam do caminho real sobrescrevem depois -- o
    `monkeypatch` é o mesmo, vale a última escrita.
    """
    def _bloqueia(nome):
        def _falha(*a, **kw):
            raise AssertionError(
                f"{nome} não foi mockada -- este teste faria uma chamada externa REAL (e paga)."
            )
        return _falha

    for nome in ("search_google_places", "enrich_email", "enrich_site_firecrawl",
                 "validate_whatsapp", "enrich_receita_federal", "enrich_cnes"):
        monkeypatch.setattr(celery_app, nome, _bloqueia(nome))


@pytest.fixture
def db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _resolver_com_mx(dominio, tipo):
    return ["mx1.exemplo"]


# ---------------------------------------------------------------------------
# 1 -- extrair_whatsapp
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("texto,esperado", [
    # wa.me, os formatos que aparecem de verdade em site de clínica
    ("Fale conosco: https://wa.me/5511987654321", "5511987654321"),
    ("[WhatsApp](https://wa.me/11987654321)", "5511987654321"),   # sem DDI -> normaliza
    ("wa.me/+55 11 98765-4321", "5511987654321"),                 # com separadores
    ("<a href='http://wa.me/5511987654321'>Zap</a>", "5511987654321"),
    # api.whatsapp.com / web.whatsapp.com
    ('<a href="https://api.whatsapp.com/send?phone=5521999998888&text=Oi">',  "5521999998888"),
    ("https://api.whatsapp.com/send?text=Oi&phone=5521999998888", "5521999998888"),
    ("https://web.whatsapp.com/send?phone=5511912345678", "5511912345678"),
])
def test_extrai_whatsapp_dos_padroes_reais(texto, esperado):
    assert extrair_whatsapp(texto) == esperado


@pytest.mark.parametrize("texto", [
    "",
    None,
    "Site sem nenhum contato",
    "Siga em instagram.com/clinicateste",          # outro link, não é WhatsApp
    "Grupo: https://wa.me/AbCdEfGh123456",         # convite de grupo (alfanumérico)
    "https://wa.me/",                              # link truncado
    "whatsapp: 11 98765-4321",                     # texto solto, sem link -- fora do escopo
])
def test_sem_whatsapp_no_site_retorna_none_sem_quebrar(texto):
    assert extrair_whatsapp(texto) is None


def test_numero_implausivel_e_descartado_e_a_busca_continua():
    """Um link ruim não pode consumir a vez de um bom mais adiante."""
    texto = "https://wa.me/1 e também https://wa.me/5511912345678"
    assert extrair_whatsapp(texto) == "5511912345678"


def test_whatsapp_do_site_vem_normalizado_pro_formato_da_evolution():
    """Mesma normalização de app/services/whatsapp.py -- não uma cópia
    da heurística de código do país."""
    from app.services.whatsapp import formatar_numero_whatsapp
    numero = extrair_whatsapp("https://wa.me/11987654321")
    assert numero == formatar_numero_whatsapp("11987654321")


# ---------------------------------------------------------------------------
# 1b -- prioridade: site vence Google Places
# ---------------------------------------------------------------------------

def _monta_pipeline_basico(monkeypatch, whatsapp_site, telefone_google):
    monkeypatch.setattr(celery_app, "enrich_receita_federal",
                        lambda cnpj: {"encontrado": True, "razao_social": "CLINICA TESTE"})
    monkeypatch.setattr(celery_app, "enrich_pgfn_divida_ativa", lambda c: {"qtd_inscricoes_ativas": 0})
    monkeypatch.setattr(celery_app, "search_google_places",
                        lambda ests: [{**ests[0], "site": "https://clinicateste.com.br",
                                       "telefone_google": telefone_google}])
    monkeypatch.setattr(celery_app, "enrich_site_firecrawl",
                        lambda url: {"site_url": url, "scrape_sucesso": True,
                                     "site_content_resumo": "x", "instagram": None,
                                     "whatsapp_site": whatsapp_site,
                                     "rqe_mencionado": False, "rqe_numero": None, "servicos": []})
    monkeypatch.setattr(celery_app.settings, "FIRECRAWL_API_KEY", "presente")
    # `enrich_email` mockada de propósito: sem isso o teste faz chamada
    # REAL ao Hunter e queima crédito do plano Free (50/mês).
    monkeypatch.setattr(celery_app, "enrich_email",
                        lambda dominio, decisor: {"dominio": dominio, "email": None,
                                                  "email_aprovado": False})
    recebidos = {}

    def _fake_whatsapp(tel):
        recebidos["telefone"] = tel
        return {"telefone_original": tel, "numero_formatado": tel, "whatsapp_ativo": True}

    monkeypatch.setattr(celery_app, "validate_whatsapp", _fake_whatsapp)
    return recebidos


def test_whatsapp_do_site_tem_prioridade_sobre_o_telefone_do_google(monkeypatch, db_session):
    """A correção central do item 1: o do Google é a central (fixo)."""
    recebidos = _monta_pipeline_basico(monkeypatch, "5511987654321", "1133334444")
    r = celery_app.process_lead_pipeline("11222333000181", razao_social="X", db=db_session)
    assert recebidos["telefone"] == "5511987654321"
    assert r["telefone_origem"] == "site_firecrawl"


def test_sem_whatsapp_no_site_cai_pro_telefone_do_google(monkeypatch, db_session):
    recebidos = _monta_pipeline_basico(monkeypatch, None, "1133334444")
    r = celery_app.process_lead_pipeline("11222333000181", razao_social="X", db=db_session)
    assert recebidos["telefone"] == "1133334444"
    assert r["telefone_origem"] == "google_places"


def test_telefone_explicito_vence_ate_o_do_site(monkeypatch, db_session):
    recebidos = _monta_pipeline_basico(monkeypatch, "5511987654321", "1133334444")
    r = celery_app.process_lead_pipeline("11222333000181", razao_social="X",
                                         telefone="5511900000000", db=db_session)
    assert recebidos["telefone"] == "5511900000000"
    assert r["telefone_origem"] == "parametro_explicito"


def test_whatsapp_do_site_e_persistido_mesmo_sem_a_etapa_de_validacao(db_session):
    """Quando a Evolution não está configurada, `validate_whatsapp` é
    pulada -- o número do site ainda tem que sobrescrever o fixo."""
    lead = celery_app._persistir_enriquecimento_lead(
        db=db_session, cnpj="11222333000181", razao_social="X",
        receita_federal=None, pgfn=None, zona_franca=None, cnes=None,
        google_places={"telefone_google": "1133334444"},
        site_firecrawl={"instagram": None, "whatsapp_site": "5511987654321", "servicos": []},
        site_url=None, whatsapp=None, email=None,
    )
    assert lead.telefone == "5511987654321"


# ---------------------------------------------------------------------------
# 2 -- fallback domain-search (atrás da flag)
# ---------------------------------------------------------------------------

RESPOSTA_FINDER_VAZIA = {"data": {"email": None, "score": None}}


def _client_hunter(monkeypatch, emails_do_dominio, registro):
    """MockTransport que atende os dois endpoints do Hunter e registra
    quais foram chamados."""
    def handler(request: httpx.Request) -> httpx.Response:
        registro.append(request.url.path)
        if "domain-search" in request.url.path:
            return httpx.Response(200, json={"data": {"emails": emails_do_dominio}})
        return httpx.Response(200, json=RESPOSTA_FINDER_VAZIA)

    monkeypatch.setattr(email_enrichment.settings, "HUNTER_API_KEY", "chave-ficticia")
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fallback_nao_e_chamado_com_a_flag_desligada(monkeypatch):
    """Default de produção hoje -- o plano Free não comporta o dobro."""
    monkeypatch.setattr(email_enrichment.settings, "HUNTER_DOMAIN_SEARCH_FALLBACK", False)
    chamadas = []
    client = _client_hunter(monkeypatch, [{"value": "contato@x.com", "type": "generic", "confidence": 90}], chamadas)

    resultado = enriquecer_email(DOMINIO, "MARIA SILVA", client=client, resolver=_resolver_com_mx)

    assert resultado.email is None
    assert not any("domain-search" in c for c in chamadas)
    assert any("email-finder" in c for c in chamadas)


def test_fallback_e_chamado_com_a_flag_ligada_e_finder_vazio(monkeypatch):
    monkeypatch.setattr(email_enrichment.settings, "HUNTER_DOMAIN_SEARCH_FALLBACK", True)
    monkeypatch.setattr(email_enrichment.settings, "ZEROBOUNCE_API_KEY", "chave-ficticia")
    chamadas = []
    client = _client_hunter(monkeypatch, [{"value": "contato@x.com", "type": "generic", "confidence": 90}], chamadas)
    monkeypatch.setattr(email_enrichment, "validar_zerobounce",
                        lambda email, client=None: email_enrichment.ZeroBounceResult(status="valid", aprovado=True))

    resultado = enriquecer_email(DOMINIO, "MARIA SILVA", client=client, resolver=_resolver_com_mx)

    assert resultado.email == "contato@x.com"
    assert any("domain-search" in c for c in chamadas)


def test_fallback_nao_e_chamado_quando_o_finder_ja_achou(monkeypatch):
    """Economia de crédito: a chamada extra nunca acontece à toa."""
    monkeypatch.setattr(email_enrichment.settings, "HUNTER_DOMAIN_SEARCH_FALLBACK", True)
    monkeypatch.setattr(email_enrichment.settings, "HUNTER_API_KEY", "chave-ficticia")
    chamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request.url.path)
        return httpx.Response(200, json={"data": {"email": "maria@x.com", "score": 95}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(email_enrichment, "validar_zerobounce",
                        lambda email, client=None: email_enrichment.ZeroBounceResult(status="valid", aprovado=True))

    resultado = enriquecer_email(DOMINIO, "MARIA SILVA", client=client, resolver=_resolver_com_mx)

    assert resultado.email == "maria@x.com"
    assert not any("domain-search" in c for c in chamadas)


# --- ordenação: personal > generic, depois confidence desc ---

@pytest.mark.parametrize("emails,esperado", [
    # personal ganha de generic mesmo com confidence MENOR
    ([{"value": "generico@x.com", "type": "generic", "confidence": 99},
      {"value": "pessoa@x.com", "type": "personal", "confidence": 40}], "pessoa@x.com"),
    # dentro de personal, maior confidence
    ([{"value": "baixa@x.com", "type": "personal", "confidence": 50},
      {"value": "alta@x.com", "type": "personal", "confidence": 95}], "alta@x.com"),
    # só genéricos -> maior confidence entre eles (genérico ainda serve)
    ([{"value": "rh@x.com", "type": "generic", "confidence": 60},
      {"value": "contato@x.com", "type": "generic", "confidence": 88}], "contato@x.com"),
    # tipo desconhecido vai pro fim em vez de quebrar
    ([{"value": "estranho@x.com", "type": "novo_tipo", "confidence": 99},
      {"value": "contato@x.com", "type": "generic", "confidence": 10}], "contato@x.com"),
    # confidence ausente não quebra a ordenação
    ([{"value": "sem_conf@x.com", "type": "personal"},
      {"value": "com_conf@x.com", "type": "personal", "confidence": 70}], "com_conf@x.com"),
])
def test_ordenacao_do_domain_search(monkeypatch, emails, esperado):
    monkeypatch.setattr(email_enrichment.settings, "HUNTER_API_KEY", "chave-ficticia")
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"data": {"emails": emails}})))
    assert buscar_emails_do_dominio(DOMINIO, client=client).email == esperado


def test_domain_search_sem_emails_retorna_nao_encontrado(monkeypatch):
    monkeypatch.setattr(email_enrichment.settings, "HUNTER_API_KEY", "chave-ficticia")
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"data": {"emails": []}})))
    r = buscar_emails_do_dominio(DOMINIO, client=client)
    assert r.encontrado is False and r.email is None


def test_domain_search_ignora_entrada_sem_value(monkeypatch):
    monkeypatch.setattr(email_enrichment.settings, "HUNTER_API_KEY", "chave-ficticia")
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"data": {"emails": [
            {"type": "personal", "confidence": 99},
            {"value": "ok@x.com", "type": "generic", "confidence": 10}]}})))
    assert buscar_emails_do_dominio(DOMINIO, client=client).email == "ok@x.com"


def test_domain_search_nao_manda_chave_na_query_string(monkeypatch):
    """Mesma proteção do email-finder -- ver app/core/segredos.py."""
    CHAVE = "chave-ficticia-dominio-123"
    monkeypatch.setattr(email_enrichment.settings, "HUNTER_API_KEY", CHAVE)
    capturado = {}

    def handler(request: httpx.Request) -> httpx.Response:
        capturado["url"] = str(request.url)
        capturado["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"data": {"emails": []}})

    buscar_emails_do_dominio(DOMINIO, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert CHAVE not in capturado["url"]
    assert capturado["auth"] == f"Bearer {CHAVE}"
