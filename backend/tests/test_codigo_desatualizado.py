"""
Detecção de worker rodando código desatualizado + falha de scrape por
exceção (23/08/2026).
================================================================================

⚠️ Este arquivo existe por causa de TRÊS investigações seguidas no mesmo
dia, todas com a mesma causa raiz: o worker rodando código anterior à
correção porque ninguém reiniciou o processo.

  1. `NoReferencedTableError` (FK buscas_leads -> users)
  2. e-mail vazio em 6 leads
  3. `site_scrape_sucesso` NULL em 6 de 6 leads -- a busca rodou 11
     minutos ANTES de a correção ser escrita

Nos três casos o worker parecia saudável e as tasks concluíam "com
sucesso", produzindo dados errados em silêncio.
"""
import logging
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.lead import Lead
from app.workers import celery_app
from app.workers.celery_app import (
    _avisar_se_codigo_desatualizado,
    arquivos_modificados_apos_inicio,
    resultado_firecrawl_falho,
)

CNPJ = "06186989000156"  # IBEN, o lead da investigação


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


# ---------------------------------------------------------------------------
# Item 2 -- exceção no scrape vira False, não NULL
# ---------------------------------------------------------------------------

def _pipeline_com_firecrawl(monkeypatch, fake_firecrawl):
    monkeypatch.setattr(celery_app, "enrich_receita_federal",
                        lambda c: {"encontrado": True, "razao_social": "IBEN"})
    monkeypatch.setattr(celery_app, "enrich_pgfn_divida_ativa", lambda c: {"qtd_inscricoes_ativas": 0})
    monkeypatch.setattr(celery_app, "search_google_places",
                        lambda e: [{**e[0], "site": "http://clinicaiben.com.br/", "telefone_google": None}])
    monkeypatch.setattr(celery_app, "enrich_email", lambda d, n: {"email": None, "email_aprovado": False})
    monkeypatch.setattr(celery_app, "enrich_site_firecrawl", fake_firecrawl)
    monkeypatch.setattr(celery_app.settings, "FIRECRAWL_API_KEY", "presente")


def test_excecao_no_scrape_persiste_false_e_nao_none(monkeypatch, db_session):
    """A regressão central: `_rodar_etapa` engole a exceção e devolve
    None; sem tratamento, a persistência pulava o bloco inteiro e
    `site_scrape_sucesso` ficava NULL -- que significa "nunca tentamos"."""
    def _explode(url):
        raise RuntimeError("Firecrawl 500 / timeout / rede caiu")

    _pipeline_com_firecrawl(monkeypatch, _explode)
    celery_app.process_lead_pipeline(CNPJ, razao_social="IBEN", db=db_session)

    lead = db_session.query(Lead).filter(Lead.cnpj == CNPJ).one()
    assert lead.site_scrape_sucesso is False
    assert lead.site_scrape_sucesso is not None  # explícito: NULL seria o bug


def test_excecao_no_scrape_aparece_no_retorno_do_pipeline(monkeypatch, db_session):
    def _explode(url):
        raise TimeoutError("timeout")

    _pipeline_com_firecrawl(monkeypatch, _explode)
    r = celery_app.process_lead_pipeline(CNPJ, razao_social="IBEN", db=db_session)
    assert r["site_scrape_sucesso"] is False
    # A etapa foi TENTADA, então não entra em etapas_puladas.
    assert "enrich_site_firecrawl" not in r["etapas_puladas"]


def test_lead_sem_site_continua_none(monkeypatch, db_session):
    """A distinção tem que continuar valendo: sem site, a etapa nem é
    tentada e NULL é a resposta certa."""
    monkeypatch.setattr(celery_app, "enrich_receita_federal",
                        lambda c: {"encontrado": True, "razao_social": "X"})
    monkeypatch.setattr(celery_app, "enrich_pgfn_divida_ativa", lambda c: {"qtd_inscricoes_ativas": 0})
    monkeypatch.setattr(celery_app, "search_google_places",
                        lambda e: [{**e[0], "site": None, "telefone_google": None}])

    r = celery_app.process_lead_pipeline(CNPJ, razao_social="X", db=db_session)
    lead = db_session.query(Lead).filter(Lead.cnpj == CNPJ).one()
    assert lead.site_scrape_sucesso is None
    assert "enrich_site_firecrawl" in r["etapas_puladas"]


def test_firecrawl_api_key_ausente_continua_none(monkeypatch, db_session):
    monkeypatch.setattr(celery_app, "enrich_receita_federal",
                        lambda c: {"encontrado": True, "razao_social": "X"})
    monkeypatch.setattr(celery_app, "enrich_pgfn_divida_ativa", lambda c: {"qtd_inscricoes_ativas": 0})
    monkeypatch.setattr(celery_app, "search_google_places",
                        lambda e: [{**e[0], "site": "http://x.com", "telefone_google": None}])
    monkeypatch.setattr(celery_app, "enrich_email", lambda d, n: {"email": None, "email_aprovado": False})
    monkeypatch.setattr(celery_app.settings, "FIRECRAWL_API_KEY", "")

    celery_app.process_lead_pipeline(CNPJ, razao_social="X", db=db_session)
    lead = db_session.query(Lead).filter(Lead.cnpj == CNPJ).one()
    assert lead.site_scrape_sucesso is None


def test_dict_de_falha_e_o_mesmo_nos_dois_caminhos():
    """Retorno gracioso e exceção têm que produzir a MESMA forma -- se
    divergirem, um dos caminhos grava um lead diferente do outro."""
    falha = resultado_firecrawl_falho("http://x.com")
    assert falha["scrape_sucesso"] is False
    assert set(falha) == {"site_url", "scrape_sucesso", "site_content_resumo", "instagram",
                          "whatsapp_site", "rqe_mencionado", "rqe_numero", "servicos"}


# ---------------------------------------------------------------------------
# Item 3 -- aviso de código desatualizado
# ---------------------------------------------------------------------------

def _criar_py(raiz, nome, mtime):
    caminho = raiz / nome
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text("# codigo", encoding="utf-8")
    import os
    os.utime(caminho, (mtime, mtime))
    return caminho


def test_detecta_arquivo_modificado_apos_o_inicio(tmp_path):
    inicio = time.time()
    _criar_py(tmp_path, "novo.py", inicio + 60)          # editado DEPOIS
    _criar_py(tmp_path, "antigo.py", inicio - 60)        # editado antes
    assert arquivos_modificados_apos_inicio(inicio=inicio, raiz=tmp_path) == ["novo.py"]


def test_nao_detecta_nada_quando_tudo_e_anterior(tmp_path):
    inicio = time.time()
    _criar_py(tmp_path, "a.py", inicio - 10)
    _criar_py(tmp_path, "sub/b.py", inicio - 500)
    assert arquivos_modificados_apos_inicio(inicio=inicio, raiz=tmp_path) == []


def test_varre_subdiretorios_e_ordena(tmp_path):
    inicio = time.time()
    _criar_py(tmp_path, "z.py", inicio + 5)
    _criar_py(tmp_path, "sub/a.py", inicio + 5)
    resultado = arquivos_modificados_apos_inicio(inicio=inicio, raiz=tmp_path)
    assert resultado == sorted(resultado)
    assert "z.py" in resultado and str(__import__("pathlib").Path("sub/a.py")) in resultado


def test_ignora_pycache(tmp_path):
    """O .pyc é regerado e não diz nada sobre o que está NA MEMÓRIA."""
    inicio = time.time()
    _criar_py(tmp_path, "__pycache__/modulo.cpython-312.py", inicio + 60)
    assert arquivos_modificados_apos_inicio(inicio=inicio, raiz=tmp_path) == []


def test_ignora_arquivos_que_nao_sao_py(tmp_path):
    inicio = time.time()
    import os
    outro = tmp_path / "dados.json"
    outro.write_text("{}", encoding="utf-8")
    os.utime(outro, (inicio + 60, inicio + 60))
    assert arquivos_modificados_apos_inicio(inicio=inicio, raiz=tmp_path) == []


def test_raiz_inexistente_nao_levanta(tmp_path):
    """Um detector de problema que vira problema é pior que não ter."""
    assert arquivos_modificados_apos_inicio(inicio=time.time(), raiz=tmp_path / "nao_existe") == []


def test_aviso_dispara_com_mensagem_acionavel(monkeypatch, tmp_path, caplog):
    inicio = time.time()
    _criar_py(tmp_path, "celery_app.py", inicio + 60)
    monkeypatch.setattr(celery_app, "_INICIO_DO_PROCESSO", inicio)
    monkeypatch.setattr(celery_app, "_RAIZ_APP", tmp_path)
    monkeypatch.setattr(celery_app, "_ultima_checagem_de_codigo", 0.0)

    with caplog.at_level(logging.WARNING):
        desatualizados = _avisar_se_codigo_desatualizado(forcar=True)

    assert desatualizados == ["celery_app.py"]
    assert "DESATUALIZADO" in caplog.text
    assert "REINICIE O WORKER" in caplog.text  # a ação, não só o diagnóstico
    assert "celery_app.py" in caplog.text


def test_aviso_nao_dispara_quando_o_codigo_esta_em_dia(monkeypatch, tmp_path, caplog):
    inicio = time.time()
    _criar_py(tmp_path, "celery_app.py", inicio - 60)
    monkeypatch.setattr(celery_app, "_INICIO_DO_PROCESSO", inicio)
    monkeypatch.setattr(celery_app, "_RAIZ_APP", tmp_path)
    monkeypatch.setattr(celery_app, "_ultima_checagem_de_codigo", 0.0)

    with caplog.at_level(logging.WARNING):
        assert _avisar_se_codigo_desatualizado(forcar=True) == []
    assert "DESATUALIZADO" not in caplog.text


def test_checagem_e_limitada_por_intervalo(monkeypatch, tmp_path):
    """Roda antes de cada task; numa busca de 60 leads são centenas de
    chamadas, e o código não muda no meio do lote."""
    inicio = time.time()
    _criar_py(tmp_path, "celery_app.py", inicio + 60)
    monkeypatch.setattr(celery_app, "_INICIO_DO_PROCESSO", inicio)
    monkeypatch.setattr(celery_app, "_RAIZ_APP", tmp_path)
    monkeypatch.setattr(celery_app, "_ultima_checagem_de_codigo", 0.0)

    assert _avisar_se_codigo_desatualizado() != []   # 1ª roda
    assert _avisar_se_codigo_desatualizado() == []   # 2ª é suprimida
    assert _avisar_se_codigo_desatualizado(forcar=True) != []  # forçar ignora o limite


def test_truncamento_da_lista_longa(monkeypatch, tmp_path, caplog):
    inicio = time.time()
    for i in range(12):
        _criar_py(tmp_path, f"modulo_{i:02d}.py", inicio + 60)
    monkeypatch.setattr(celery_app, "_INICIO_DO_PROCESSO", inicio)
    monkeypatch.setattr(celery_app, "_RAIZ_APP", tmp_path)
    monkeypatch.setattr(celery_app, "_ultima_checagem_de_codigo", 0.0)

    with caplog.at_level(logging.WARNING):
        assert len(_avisar_se_codigo_desatualizado(forcar=True)) == 12
    assert "12 arquivo(s)" in caplog.text
    assert "e mais 4" in caplog.text          # 12 - 8 listados
    assert "modulo_11.py" not in caplog.text  # além do corte


def test_cenario_real_da_investigacao(monkeypatch, tmp_path, caplog):
    """Reproduz o caso concreto: worker sobe 10:22:54, arquivos editados
    10:41:32 -- 19 minutos depois. O processo não tem como conter esse
    código, e nada no log dizia isso."""
    subida_do_worker = 1_000_000.0
    _criar_py(tmp_path, "workers/celery_app.py", subida_do_worker + 19 * 60)
    _criar_py(tmp_path, "models/lead.py", subida_do_worker + 18 * 60)
    monkeypatch.setattr(celery_app, "_INICIO_DO_PROCESSO", subida_do_worker)
    monkeypatch.setattr(celery_app, "_RAIZ_APP", tmp_path)
    monkeypatch.setattr(celery_app, "_ultima_checagem_de_codigo", 0.0)

    with caplog.at_level(logging.WARNING):
        desatualizados = _avisar_se_codigo_desatualizado(forcar=True)

    assert len(desatualizados) == 2
    assert "REINICIE O WORKER" in caplog.text
