"""
Testes de funções auxiliares puras do módulo Celery (não das tasks em si
-- a maioria ainda é NotImplementedError ou depende de serviços externos,
ver app/services/*).
"""
from app.workers.celery_app import nome_busca_google_places


def test_prefere_razao_social():
    estabelecimento = {"razao_social": "CLINICA A LTDA", "nome_fantasia": "Clinica A"}
    assert nome_busca_google_places(estabelecimento) == "CLINICA A LTDA"


def test_cai_pro_nome_fantasia_se_razao_social_vazia():
    estabelecimento = {"razao_social": "", "nome_fantasia": "Clinica A"}
    assert nome_busca_google_places(estabelecimento) == "Clinica A"


def test_cai_pro_nome_fantasia_se_razao_social_ausente():
    estabelecimento = {"nome_fantasia": "Clinica A"}
    assert nome_busca_google_places(estabelecimento) == "Clinica A"


def test_retorna_none_se_nenhum_nome_disponivel():
    assert nome_busca_google_places({"razao_social": "", "nome_fantasia": ""}) is None
    assert nome_busca_google_places({}) is None
