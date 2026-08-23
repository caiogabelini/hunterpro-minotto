"""
Correções derivadas da PRIMEIRA BUSCA REAL (6 leads, 23/08/2026).
================================================================================

Os 6 leads reais saíram com `telefone`, `whatsapp_ativo`, `email` e
`prioridade` todos nulos. O diagnóstico achou quatro causas distintas, e
este arquivo é a guarda de regressão de cada uma:

  1. `telefone_google` (que `search_google_places` sempre devolveu) não
     era lido por ninguém, e `Lead.telefone` só tinha uma fonte: a etapa
     de WhatsApp -- que era pulada justamente por falta de telefone.
  2. `enrich_email` não tinha guarda de chave: rodava com a chave vazia,
     tomava 401 e `_rodar_etapa` engolia -- 6 falhas silenciosas.
  3. A `api_key` ia na QUERY STRING, então entrava na mensagem da
     exceção e daí no log (ver app/core/segredos.py).
  4. `Lead.prioridade` era lida em 4 lugares e escrita em nenhum.
"""
import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.core.segredos import MARCADOR, redigir_segredos
from app.models.lead import Lead
from app.scoring.compute_lead_score import (
    PRIORIDADE_CORTE_A,
    PRIORIDADE_CORTE_B,
    derivar_prioridade,
)
from app.services.email_enrichment import ErroHttpSeguro, _dividir_nome
from app.workers import celery_app

CNPJ = "11222333000181"


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


# ---------------------------------------------------------------------------
# 1a/1b -- telefone do Google Places alimenta Lead.telefone e o WhatsApp
# ---------------------------------------------------------------------------

def test_telefone_do_google_places_e_persistido_no_lead(db_session):
    lead = celery_app._persistir_enriquecimento_lead(
        db=db_session, cnpj=CNPJ, razao_social="CLINICA TESTE",
        receita_federal=None, pgfn=None, zona_franca=None, cnes=None,
        google_places={"rating": 4.5, "avaliacoes": 10, "telefone_google": "(11) 98765-4321"},
        site_firecrawl=None, site_url=None, whatsapp=None, email=None,
    )
    assert lead.telefone == "(11) 98765-4321"


def test_whatsapp_validado_sobrescreve_o_telefone_cru_do_google(db_session):
    """Quando a Evolution rodou, o número normalizado (com código do país)
    é melhor que o formato cru do Google."""
    lead = celery_app._persistir_enriquecimento_lead(
        db=db_session, cnpj=CNPJ, razao_social="CLINICA TESTE",
        receita_federal=None, pgfn=None, zona_franca=None, cnes=None,
        google_places={"telefone_google": "(11) 98765-4321"},
        site_firecrawl=None, site_url=None,
        whatsapp={"numero_formatado": "5511987654321", "whatsapp_ativo": True}, email=None,
    )
    assert lead.telefone == "5511987654321"
    assert lead.whatsapp_ativo is True


def test_google_sem_telefone_nao_apaga_telefone_de_rodada_anterior(db_session):
    """Mesma regra de "etapa que não trouxe dado não apaga o que já havia"
    do resto de _persistir_enriquecimento_lead."""
    celery_app._persistir_enriquecimento_lead(
        db=db_session, cnpj=CNPJ, razao_social="CLINICA TESTE",
        receita_federal=None, pgfn=None, zona_franca=None, cnes=None,
        google_places={"telefone_google": "1133334444"},
        site_firecrawl=None, site_url=None, whatsapp=None, email=None,
    )
    db_session.commit()
    lead = celery_app._persistir_enriquecimento_lead(
        db=db_session, cnpj=CNPJ, razao_social="CLINICA TESTE",
        receita_federal=None, pgfn=None, zona_franca=None, cnes=None,
        google_places={"telefone_google": None, "rating": 4.0},
        site_firecrawl=None, site_url=None, whatsapp=None, email=None,
    )
    assert lead.telefone == "1133334444"


def test_telefone_do_google_alimenta_validate_whatsapp(monkeypatch, db_session):
    """A regressão central do item 1: antes, validate_whatsapp era SEMPRE
    pulada porque nenhuma etapa fornecia telefone."""
    recebidos = {}

    monkeypatch.setattr(celery_app, "enrich_receita_federal",
                        lambda cnpj: {"encontrado": True, "razao_social": "CLINICA TESTE", "uf": "SP"})
    monkeypatch.setattr(celery_app, "enrich_pgfn_divida_ativa", lambda cnpj: {"qtd_inscricoes_ativas": 0})
    monkeypatch.setattr(celery_app, "search_google_places",
                        lambda ests: [{**ests[0], "site": None, "telefone_google": "1133334444"}])

    def _fake_whatsapp(telefone):
        recebidos["telefone"] = telefone
        return {"telefone_original": telefone, "numero_formatado": "551133334444", "whatsapp_ativo": True}

    monkeypatch.setattr(celery_app, "validate_whatsapp", _fake_whatsapp)

    resultado = celery_app.process_lead_pipeline(CNPJ, razao_social="CLINICA TESTE", db=db_session)

    assert recebidos["telefone"] == "1133334444"
    assert "validate_whatsapp" in resultado["etapas_executadas"]
    assert "validate_whatsapp" not in resultado["etapas_puladas"]


def test_telefone_explicito_do_chamador_vence_o_do_google(monkeypatch, db_session):
    recebidos = {}
    monkeypatch.setattr(celery_app, "enrich_receita_federal", lambda cnpj: {"encontrado": True, "razao_social": "X"})
    monkeypatch.setattr(celery_app, "enrich_pgfn_divida_ativa", lambda cnpj: {"qtd_inscricoes_ativas": 0})
    monkeypatch.setattr(celery_app, "search_google_places",
                        lambda ests: [{**ests[0], "telefone_google": "1199999999"}])
    monkeypatch.setattr(celery_app, "validate_whatsapp",
                        lambda t: recebidos.setdefault("telefone", t) and None or {"whatsapp_ativo": True})

    celery_app.process_lead_pipeline(CNPJ, razao_social="X", telefone="1188888888", db=db_session)
    assert recebidos["telefone"] == "1188888888"


def test_sem_telefone_em_lugar_nenhum_pula_com_motivo_claro(monkeypatch, db_session):
    monkeypatch.setattr(celery_app, "enrich_receita_federal", lambda cnpj: {"encontrado": True, "razao_social": "X"})
    monkeypatch.setattr(celery_app, "enrich_pgfn_divida_ativa", lambda cnpj: {"qtd_inscricoes_ativas": 0})
    monkeypatch.setattr(celery_app, "search_google_places", lambda ests: [{**ests[0], "telefone_google": None}])

    resultado = celery_app.process_lead_pipeline(CNPJ, razao_social="X", db=db_session)
    # Mensagem ampliada em 23/08/2026 quando o site raspado virou uma
    # terceira fonte de telefone (ver test_whatsapp_site_e_fallback_email.py).
    motivo = resultado["etapas_puladas"]["validate_whatsapp"]
    assert "site raspado" in motivo and "Google Places" in motivo


# ---------------------------------------------------------------------------
# 2a -- guarda de chave ausente (enrich_email e validate_whatsapp)
# ---------------------------------------------------------------------------

def test_enrich_email_sem_chave_pula_com_motivo_e_nao_levanta(monkeypatch):
    """Sem a guarda, a chamada real saía e tomava 401 -- `_rodar_etapa`
    engolia e o painel não distinguia "não configurado" de "não achou"."""
    monkeypatch.setattr(celery_app.settings, "HUNTER_API_KEY", "")
    monkeypatch.setattr(celery_app.settings, "ZEROBOUNCE_API_KEY", "")

    def _nao_deveria_chamar(*a, **kw):
        raise AssertionError("enriquecer_email não deveria ser chamada sem chave configurada")

    monkeypatch.setattr("app.services.email_enrichment.enriquecer_email", _nao_deveria_chamar)

    resultado = celery_app.enrich_email("clinicateste.com.br", "MARIA SILVA")
    assert resultado["email"] is None
    assert resultado["email_aprovado"] is False
    assert "HUNTER_API_KEY" in resultado["etapa_pulada"]
    assert "ZEROBOUNCE_API_KEY" in resultado["etapa_pulada"]


def test_enrich_email_com_so_uma_chave_faltando_nomeia_so_ela(monkeypatch):
    monkeypatch.setattr(celery_app.settings, "HUNTER_API_KEY", "presente")
    monkeypatch.setattr(celery_app.settings, "ZEROBOUNCE_API_KEY", "")
    resultado = celery_app.enrich_email("clinicateste.com.br", None)
    assert resultado["etapa_pulada"] == "ZEROBOUNCE_API_KEY não configurada(s)"


def test_validate_whatsapp_sem_evolution_configurada_pula(monkeypatch):
    monkeypatch.setattr(celery_app.settings, "EVOLUTION_URL", "")
    monkeypatch.setattr(celery_app.settings, "EVOLUTION_KEY", "")
    monkeypatch.setattr(celery_app.settings, "EVOLUTION_INSTANCE", "")

    def _nao_deveria_chamar(*a, **kw):
        raise AssertionError("check_whatsapp não deveria ser chamada sem Evolution configurada")

    monkeypatch.setattr("app.services.whatsapp.check_whatsapp", _nao_deveria_chamar)

    resultado = celery_app.validate_whatsapp("11987654321")
    assert resultado["whatsapp_ativo"] is None
    assert "EVOLUTION_URL" in resultado["etapa_pulada"]


# ---------------------------------------------------------------------------
# 2b -- nenhuma credencial em texto puro (regressão de vazamento)
# ---------------------------------------------------------------------------

CHAVE_FICTICIA = "hunter-chave-secreta-ficticia-123456"


def test_hunter_nao_manda_a_chave_na_query_string(monkeypatch):
    """A correção estrutural: a chave sai da URL e vai pro header."""
    capturado = {}

    def handler(request: httpx.Request) -> httpx.Response:
        capturado["url"] = str(request.url)
        capturado["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"data": {"email": "a@b.com", "score": 90}})

    monkeypatch.setattr("app.services.email_enrichment.settings.HUNTER_API_KEY", CHAVE_FICTICIA)
    from app.services.email_enrichment import buscar_email_hunter

    buscar_email_hunter("clinicateste.com.br", "MARIA SILVA",
                        client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert CHAVE_FICTICIA not in capturado["url"]
    assert "api_key" not in capturado["url"]
    assert capturado["auth"] == f"Bearer {CHAVE_FICTICIA}"


@pytest.mark.parametrize("status", [401, 429, 500])
def test_erro_do_hunter_nunca_expoe_a_chave(monkeypatch, status: int):
    monkeypatch.setattr("app.services.email_enrichment.settings.HUNTER_API_KEY", CHAVE_FICTICIA)
    from app.services.email_enrichment import buscar_email_hunter

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status)))
    with pytest.raises(ErroHttpSeguro) as exc:
        buscar_email_hunter("clinicateste.com.br", client=client)
    assert CHAVE_FICTICIA not in str(exc.value)


def test_erro_do_zerobounce_redige_a_chave_da_query_string(monkeypatch):
    """O ZeroBounce mantém a chave na query (a API não documenta
    alternativa), então aqui a redação é a ÚNICA proteção."""
    monkeypatch.setattr("app.services.email_enrichment.settings.ZEROBOUNCE_API_KEY", CHAVE_FICTICIA)
    from app.services.email_enrichment import validar_zerobounce

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(ErroHttpSeguro) as exc:
        validar_zerobounce("a@b.com", client=client)
    assert CHAVE_FICTICIA not in str(exc.value)
    assert MARCADOR in str(exc.value)


def test_rodar_etapa_nao_loga_credencial(monkeypatch, caplog):
    """O ponto por onde TODA falha de etapa vira log."""
    monkeypatch.setattr("app.services.email_enrichment.settings.ZEROBOUNCE_API_KEY", CHAVE_FICTICIA)

    def _falha():
        raise httpx.HTTPStatusError(
            f"Client error '401' for url 'https://api.zerobounce.net/v2/validate?api_key={CHAVE_FICTICIA}'",
            request=httpx.Request("GET", "https://x"), response=httpx.Response(401),
        )

    with caplog.at_level("ERROR"):
        assert celery_app._rodar_etapa("etapa_teste", _falha) is None
    assert CHAVE_FICTICIA not in caplog.text
    assert MARCADOR in caplog.text


def test_redigir_segredos_pega_varios_nomes_de_parametro():
    for param in ["api_key", "apikey", "token", "access_token", "secret", "password"]:
        texto = f"https://x.com/a?{param}=VALOR_SECRETO&outro=ok"
        redigido = redigir_segredos(texto)
        assert "VALOR_SECRETO" not in redigido
        assert "outro=ok" in redigido, "só o parâmetro sensível deve ser redigido"


def test_redigir_segredos_nao_quebra_com_chave_vazia(monkeypatch):
    """Regressão específica: `texto.replace("", MARCADOR)` inseriria o
    marcador entre cada caractere. Chaves vazias são o caso normal em
    ambiente de desenvolvimento."""
    monkeypatch.setattr("app.core.config.settings.HUNTER_API_KEY", "")
    assert redigir_segredos("mensagem inofensiva") == "mensagem inofensiva"


# ---------------------------------------------------------------------------
# 2d -- _dividir_nome com nomes brasileiros
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nome,esperado", [
    # Decisores REAIS dos 6 leads da primeira busca.
    ("ALBA REGINA RIBEIRO DE SOUZA", ("ALBA", "SOUZA")),
    ("JOSE GUILHERME RIBEIRO NOGUEIRA FILHO", ("JOSE", "FILHO")),
    ("JACQUES LUCIO DE MOREIRA E NUNES", ("JACQUES", "NUNES")),
    ("ELIANE CUKIERMAN", ("ELIANE", "CUKIERMAN")),
    ("MARIO EDUARDO GUIMARAES VIANA", ("MARIO", "VIANA")),
    # Casos de borda
    ("Madonna", ("Madonna", None)),
    ("JOAO DE", ("JOAO", None)),          # só partícula sobrando
    ("  MARIA  SILVA  ", ("MARIA", "SILVA")),  # espaços extras
    ("", (None, None)),
    (None, (None, None)),
])
def test_dividir_nome(nome, esperado):
    assert _dividir_nome(nome) == esperado


def test_dividir_nome_nao_devolve_o_resto_inteiro_como_sobrenome():
    """Guarda direta do bug: `split(maxsplit=1)` devolvia
    "REGINA RIBEIRO DE SOUZA" como last_name."""
    _, sobrenome = _dividir_nome("ALBA REGINA RIBEIRO DE SOUZA")
    assert " " not in sobrenome


# ---------------------------------------------------------------------------
# 3 -- derivação de prioridade (cortes PROVISÓRIOS, não validados)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("score,esperado", [
    (100, "A"), (85.5, "A"), (70.1, "A"),
    (70, "A"),        # limite exato -- inclusivo na letra melhor
    (69.9, "B"), (60, "B"), (50.1, "B"),
    (50, "B"),        # limite exato
    (49.9, "C"), (45, "C"), (0, "C"),
])
def test_derivar_prioridade(score, esperado):
    assert derivar_prioridade(score) == esperado


def test_derivar_prioridade_score_none_nao_vira_C():
    """"Ainda não pontuado" != "prioridade C" -- gravar C faria o lead
    aparecer nos filtros como se tivesse sido avaliado."""
    assert derivar_prioridade(None) is None


def test_limites_batem_com_as_constantes_publicadas():
    """Se alguém mexer nos cortes, é aqui que a mudança aparece."""
    assert derivar_prioridade(PRIORIDADE_CORTE_A) == "A"
    assert derivar_prioridade(PRIORIDADE_CORTE_A - 0.1) == "B"
    assert derivar_prioridade(PRIORIDADE_CORTE_B) == "B"
    assert derivar_prioridade(PRIORIDADE_CORTE_B - 0.1) == "C"


def test_pipeline_persiste_prioridade_junto_do_score(monkeypatch, db_session):
    """O gap do item 3: `prioridade` era lida em 4 lugares e escrita em
    nenhum -- os 6 leads reais saíram todos com None."""
    monkeypatch.setattr(celery_app, "enrich_receita_federal",
                        lambda cnpj: {"encontrado": True, "razao_social": "CLINICA TESTE",
                                      "situacao_cadastral": "ATIVA"})
    monkeypatch.setattr(celery_app, "enrich_pgfn_divida_ativa",
                        lambda cnpj: {"qtd_inscricoes_ativas": 6, "pontos_score": 20})
    monkeypatch.setattr(celery_app, "search_google_places", lambda ests: [{**ests[0]}])

    resultado = celery_app.process_lead_pipeline(CNPJ, razao_social="CLINICA TESTE", db=db_session)

    lead = db_session.query(Lead).filter(Lead.cnpj == CNPJ).one()
    assert lead.prioridade is not None
    assert lead.prioridade == derivar_prioridade(lead.score_total)
    assert resultado["prioridade"] == lead.prioridade
