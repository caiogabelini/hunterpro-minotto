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


# ---------------------------------------------------------------------------
# gerar_semente_leads: deduplicação por CNPJ
#
# Necessária desde 23/08/2026, quando os finders passaram a reconhecer os
# .zip da Receita além dos CSVs extraídos: uma pasta com os dois formatos
# do mesmo lote devolve dois caminhos, e sem dedup o mesmo CNPJ entraria
# duas vezes na semente -- gastando o dobro de chamadas pagas no pipeline.
# ---------------------------------------------------------------------------
import zipfile as _zipfile

from app.services.receita_federal_bulk import DEFAULT_ENCODING as _ENCODING
from app.workers import celery_app


def _linha_estab(i: int) -> str:
    colunas = [""] * 30
    colunas[0], colunas[1], colunas[2] = f"{i:08d}", "0001", "55"
    colunas[3] = "1"
    colunas[4] = f"Clinica {i}"
    colunas[5] = "02"
    colunas[10] = "20100101"
    colunas[11] = "8630501"
    colunas[19] = "SP"
    colunas[20] = "7107"
    return ";".join(f'"{c}"' for c in colunas)


def test_semente_deduplica_cnpj_com_zip_e_csv_extraido_na_mesma_pasta(monkeypatch, tmp_path):
    csv_path = tmp_path / "K3241.K03200Y0.D50308.ESTABELE"
    csv_path.write_text(
        "\r\n".join(_linha_estab(i) for i in range(5)) + "\r\n", encoding=_ENCODING, newline=""
    )
    with _zipfile.ZipFile(tmp_path / "Estabelecimentos0.zip", "w") as z:
        z.write(csv_path, arcname=csv_path.name)

    monkeypatch.setattr(celery_app.settings, "RECEITA_FEDERAL_DADOS_ABERTOS_DIR", str(tmp_path))

    resultado = celery_app.gerar_semente_leads()

    cnpjs = [e["cnpj"] for e in resultado["estabelecimentos"]]
    assert len(cnpjs) == 5, "o mesmo lote foi contado duas vezes (zip + extraído)"
    assert len(set(cnpjs)) == 5
    assert resultado["total_encontrado"] == 5


def test_semente_le_direto_do_zip_quando_so_ele_esta_na_pasta(monkeypatch, tmp_path):
    csv_path = tmp_path / "K3241.K03200Y0.D50308.ESTABELE"
    csv_path.write_text(
        "\r\n".join(_linha_estab(i) for i in range(3)) + "\r\n", encoding=_ENCODING, newline=""
    )
    with _zipfile.ZipFile(tmp_path / "Estabelecimentos0.zip", "w") as z:
        z.write(csv_path, arcname=csv_path.name)
    csv_path.unlink()  # só o .zip fica

    monkeypatch.setattr(celery_app.settings, "RECEITA_FEDERAL_DADOS_ABERTOS_DIR", str(tmp_path))

    resultado = celery_app.gerar_semente_leads()

    assert resultado["status"] == "ok"
    assert resultado["total_encontrado"] == 3
