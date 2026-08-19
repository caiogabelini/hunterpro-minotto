import csv
from pathlib import Path

import pytest

from app.services.pgfn import (
    PGFN_COLUMNS,
    build_pgfn_index,
    get_debt_summary,
    iter_pgfn_records,
    normalize_cnpj,
)

# CNPJs de teste (formato válido, não são reais)
CNPJ_A = "11.222.333/0001-81"   # 2 inscrições ativas
CNPJ_A_DIGITS = "11222333000181"
CNPJ_B = "44555666000199"       # 1 inscrição, já extinta (não deve contar)
CNPJ_C = "77888999000100"       # 1 inscrição ativa + 1 extinta -> conta só 1
CPF_PESSOA_FISICA = "123.456.789-01"


@pytest.fixture
def pgfn_csv(tmp_path: Path) -> Path:
    """Gera um CSV sintético no schema real documentado da PGFN."""
    path = tmp_path / "pgfn_sida_2026_t2.csv"
    fieldnames = list(PGFN_COLUMNS.values())

    rows = [
        # CNPJ_A: duas inscrições ativas distintas
        {
            "CPF_CNPJ": CNPJ_A, "TIPO_PESSOA": "PESSOA JURIDICA", "NOME_DEVEDOR": "CLINICA A LTDA",
            "NUMERO_INSCRICAO": "10.1.26.000001-01", "SITUACAO_INSCRICAO": "EM COBRANCA",
            "RECEITA_PRINCIPAL": "IRPJ", "DATA_INSCRICAO": "2024-01-15",
            "INDICADOR_AJUIZADO": "NAO", "VALOR_CONSOLIDADO": "1.234,56",
        },
        {
            "CPF_CNPJ": CNPJ_A, "TIPO_PESSOA": "PESSOA JURIDICA", "NOME_DEVEDOR": "CLINICA A LTDA",
            "NUMERO_INSCRICAO": "10.1.26.000001-02", "SITUACAO_INSCRICAO": "PARCELADA",
            "RECEITA_PRINCIPAL": "CSLL", "DATA_INSCRICAO": "2024-06-10",
            "INDICADOR_AJUIZADO": "SIM", "VALOR_CONSOLIDADO": "500,00",
        },
        # CNPJ_B: uma inscrição, mas já extinta -> não deve contar
        {
            "CPF_CNPJ": CNPJ_B, "TIPO_PESSOA": "PESSOA JURIDICA", "NOME_DEVEDOR": "CLINICA B LTDA",
            "NUMERO_INSCRICAO": "10.1.25.000002-01", "SITUACAO_INSCRICAO": "EXTINTA POR PAGAMENTO",
            "RECEITA_PRINCIPAL": "IRPJ", "DATA_INSCRICAO": "2023-03-01",
            "INDICADOR_AJUIZADO": "NAO", "VALOR_CONSOLIDADO": "800,00",
        },
        # CNPJ_C: uma ativa + uma extinta -> conta só a ativa (1)
        {
            "CPF_CNPJ": CNPJ_C, "TIPO_PESSOA": "PESSOA JURIDICA", "NOME_DEVEDOR": "CLINICA C LTDA",
            "NUMERO_INSCRICAO": "10.1.26.000003-01", "SITUACAO_INSCRICAO": "EM COBRANCA",
            "RECEITA_PRINCIPAL": "IRPJ", "DATA_INSCRICAO": "2024-02-20",
            "INDICADOR_AJUIZADO": "NAO", "VALOR_CONSOLIDADO": "2.000,00",
        },
        {
            "CPF_CNPJ": CNPJ_C, "TIPO_PESSOA": "PESSOA JURIDICA", "NOME_DEVEDOR": "CLINICA C LTDA",
            "NUMERO_INSCRICAO": "10.1.20.000003-02", "SITUACAO_INSCRICAO": "EXTINTA",
            "RECEITA_PRINCIPAL": "COFINS", "DATA_INSCRICAO": "2019-05-01",
            "INDICADOR_AJUIZADO": "NAO", "VALOR_CONSOLIDADO": "300,00",
        },
        # Pessoa física -> deve ser ignorada inteiramente
        {
            "CPF_CNPJ": CPF_PESSOA_FISICA, "TIPO_PESSOA": "PESSOA FISICA", "NOME_DEVEDOR": "FULANO DE TAL",
            "NUMERO_INSCRICAO": "10.1.26.000004-01", "SITUACAO_INSCRICAO": "EM COBRANCA",
            "RECEITA_PRINCIPAL": "IRPF", "DATA_INSCRICAO": "2024-01-01",
            "INDICADOR_AJUIZADO": "NAO", "VALOR_CONSOLIDADO": "100,00",
        },
    ]

    with open(path, "w", encoding="latin-1", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)

    return path


# --- normalize_cnpj ---

def test_normalize_cnpj_with_punctuation():
    assert normalize_cnpj("11.222.333/0001-81") == "11222333000181"


def test_normalize_cnpj_already_clean():
    assert normalize_cnpj("11222333000181") == "11222333000181"


def test_normalize_cnpj_cpf_returns_none():
    assert normalize_cnpj("123.456.789-01") is None


def test_normalize_cnpj_empty_returns_none():
    assert normalize_cnpj("") is None
    assert normalize_cnpj(None) is None


# --- iter_pgfn_records ---

def test_iter_records_skips_pessoa_fisica(pgfn_csv: Path):
    records = list(iter_pgfn_records(pgfn_csv))
    cnpjs = {r.cnpj for r in records}
    assert normalize_cnpj(CPF_PESSOA_FISICA) not in cnpjs  # é None mesmo, mas reforça a intenção
    assert all(len(r.cnpj) == 14 for r in records)


def test_iter_records_parses_valor_brl_format(pgfn_csv: Path):
    records = {r.numero_inscricao: r for r in iter_pgfn_records(pgfn_csv)}
    assert records["10.1.26.000001-01"].valor_consolidado == 1234.56
    assert records["10.1.26.000001-02"].valor_consolidado == 500.00


def test_iter_records_parses_ajuizado_flag(pgfn_csv: Path):
    records = {r.numero_inscricao: r for r in iter_pgfn_records(pgfn_csv)}
    assert records["10.1.26.000001-02"].ajuizado is True
    assert records["10.1.26.000001-01"].ajuizado is False


def test_iter_records_missing_column_raises(tmp_path: Path):
    bad_path = tmp_path / "bad.csv"
    with open(bad_path, "w", encoding="latin-1") as f:
        f.write("COLUNA_ERRADA;OUTRA\nvalor1;valor2\n")
    with pytest.raises(ValueError, match="Colunas esperadas"):
        list(iter_pgfn_records(bad_path))


# --- build_pgfn_index / get_debt_summary ---

def test_index_counts_multiple_active_inscricoes(pgfn_csv: Path):
    index = build_pgfn_index([pgfn_csv])
    summary = get_debt_summary(index, CNPJ_A)
    assert summary is not None
    assert summary.qtd_inscricoes_ativas == 2
    assert summary.valor_total == pytest.approx(1734.56)
    assert summary.algum_ajuizado is True


def test_index_excludes_quitada_entirely(pgfn_csv: Path):
    index = build_pgfn_index([pgfn_csv])
    summary = get_debt_summary(index, CNPJ_B)
    assert summary is None  # única inscrição estava extinta -> CNPJ nem aparece no índice


def test_index_counts_only_active_when_mixed(pgfn_csv: Path):
    index = build_pgfn_index([pgfn_csv])
    summary = get_debt_summary(index, CNPJ_C)
    assert summary is not None
    assert summary.qtd_inscricoes_ativas == 1  # a extinta não conta
    assert summary.valor_total == pytest.approx(2000.00)


def test_get_debt_summary_cnpj_without_debt_returns_none(pgfn_csv: Path):
    index = build_pgfn_index([pgfn_csv])
    summary = get_debt_summary(index, "99999999000199")  # nunca apareceu no arquivo
    assert summary is None


def test_get_debt_summary_accepts_formatted_cnpj(pgfn_csv: Path):
    index = build_pgfn_index([pgfn_csv])
    summary = get_debt_summary(index, "11.222.333/0001-81")  # com pontuação
    assert summary is not None
    assert summary.cnpj == CNPJ_A_DIGITS


def test_index_merges_multiple_files(pgfn_csv: Path, tmp_path: Path):
    """Simula PGFN publicando SIDA + FGTS separados -- devem se somar no mesmo índice."""
    fgts_path = tmp_path / "pgfn_fgts_2026_t2.csv"
    fieldnames = list(PGFN_COLUMNS.values())
    extra_row = {
        "CPF_CNPJ": CNPJ_A, "TIPO_PESSOA": "PESSOA JURIDICA", "NOME_DEVEDOR": "CLINICA A LTDA",
        "NUMERO_INSCRICAO": "FGTS.000001-01", "SITUACAO_INSCRICAO": "EM COBRANCA",
        "RECEITA_PRINCIPAL": "FGTS", "DATA_INSCRICAO": "2024-08-01",
        "INDICADOR_AJUIZADO": "NAO", "VALOR_CONSOLIDADO": "999,00",
    }
    with open(fgts_path, "w", encoding="latin-1", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerow(extra_row)

    index = build_pgfn_index([pgfn_csv, fgts_path])
    summary = get_debt_summary(index, CNPJ_A)
    assert summary.qtd_inscricoes_ativas == 3  # 2 do SIDA + 1 do FGTS
