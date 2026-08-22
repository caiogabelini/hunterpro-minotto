import csv
from pathlib import Path

import pytest

from app.services.pgfn import (
    PGFN_COLUMNS,
    _parse_valor,
    _situacao_conta_como_divida_ativa,
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
    """Gera um CSV sintético no schema real da PGFN.

    ⚠️ `VALOR_CONSOLIDADO` usa o formato AMERICANO ("1234.56"), que é o
    que o arquivo real publica -- confirmado em 23/08/2026 varrendo 2
    milhões de linhas. Antes desta data a fixture usava o formato
    brasileiro ("1.234,56"), que é o que `_parse_valor` *achava* que
    receberia: a fixture concordava com o bug em vez de com o arquivo.
    Os números esperados nas asserções não mudaram -- o que mudou foi a
    entrada passar a ser a real.

    ⚠️ `SITUACAO_INSCRICAO` idem: as grafias ativas agora são as reais
    ("ATIVA EM COBRANCA", "ATIVA AJUIZADA NEGOCIADA NO SISPAR"), medidas
    no arquivo de 202606. Já "EXTINTA POR PAGAMENTO"/"EXTINTA" **não
    existem** no arquivo real (a PGFN só publica inscrição ativa) -- ficam
    aqui de propósito, como caso defensivo: se um dia aparecer uma linha
    não-ATIVA, ela precisa ficar de fora da contagem."""
    path = tmp_path / "pgfn_sida_2026_t2.csv"
    fieldnames = list(PGFN_COLUMNS.values())

    rows = [
        # CNPJ_A: duas inscrições ativas distintas
        {
            "CPF_CNPJ": CNPJ_A, "TIPO_PESSOA": "PESSOA JURIDICA", "NOME_DEVEDOR": "CLINICA A LTDA",
            "NUMERO_INSCRICAO": "10.1.26.000001-01", "SITUACAO_INSCRICAO": "ATIVA EM COBRANCA",
            "RECEITA_PRINCIPAL": "IRPJ", "DATA_INSCRICAO": "2024-01-15",
            "INDICADOR_AJUIZADO": "NAO", "VALOR_CONSOLIDADO": "1234.56",
        },
        {
            "CPF_CNPJ": CNPJ_A, "TIPO_PESSOA": "PESSOA JURIDICA", "NOME_DEVEDOR": "CLINICA A LTDA",
            "NUMERO_INSCRICAO": "10.1.26.000001-02", "SITUACAO_INSCRICAO": "ATIVA AJUIZADA NEGOCIADA NO SISPAR",
            "RECEITA_PRINCIPAL": "CSLL", "DATA_INSCRICAO": "2024-06-10",
            "INDICADOR_AJUIZADO": "SIM", "VALOR_CONSOLIDADO": "500.00",
        },
        # CNPJ_B: uma inscrição, mas já extinta -> não deve contar
        {
            "CPF_CNPJ": CNPJ_B, "TIPO_PESSOA": "PESSOA JURIDICA", "NOME_DEVEDOR": "CLINICA B LTDA",
            "NUMERO_INSCRICAO": "10.1.25.000002-01", "SITUACAO_INSCRICAO": "EXTINTA POR PAGAMENTO",
            "RECEITA_PRINCIPAL": "IRPJ", "DATA_INSCRICAO": "2023-03-01",
            "INDICADOR_AJUIZADO": "NAO", "VALOR_CONSOLIDADO": "800.00",
        },
        # CNPJ_C: uma ativa + uma extinta -> conta só a ativa (1)
        {
            "CPF_CNPJ": CNPJ_C, "TIPO_PESSOA": "PESSOA JURIDICA", "NOME_DEVEDOR": "CLINICA C LTDA",
            "NUMERO_INSCRICAO": "10.1.26.000003-01", "SITUACAO_INSCRICAO": "ATIVA EM COBRANCA",
            "RECEITA_PRINCIPAL": "IRPJ", "DATA_INSCRICAO": "2024-02-20",
            "INDICADOR_AJUIZADO": "NAO", "VALOR_CONSOLIDADO": "2000.00",
        },
        {
            "CPF_CNPJ": CNPJ_C, "TIPO_PESSOA": "PESSOA JURIDICA", "NOME_DEVEDOR": "CLINICA C LTDA",
            "NUMERO_INSCRICAO": "10.1.20.000003-02", "SITUACAO_INSCRICAO": "EXTINTA",
            "RECEITA_PRINCIPAL": "COFINS", "DATA_INSCRICAO": "2019-05-01",
            "INDICADOR_AJUIZADO": "NAO", "VALOR_CONSOLIDADO": "300.00",
        },
        # Pessoa física -> deve ser ignorada inteiramente
        {
            "CPF_CNPJ": CPF_PESSOA_FISICA, "TIPO_PESSOA": "PESSOA FISICA", "NOME_DEVEDOR": "FULANO DE TAL",
            "NUMERO_INSCRICAO": "10.1.26.000004-01", "SITUACAO_INSCRICAO": "ATIVA EM COBRANCA",
            "RECEITA_PRINCIPAL": "IRPF", "DATA_INSCRICAO": "2024-01-01",
            "INDICADOR_AJUIZADO": "NAO", "VALOR_CONSOLIDADO": "100.00",
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


# --- _parse_valor ---
# Bug de 100x corrigido em 23/08/2026 -- ver a docstring da função.

def test_parse_valor_formato_real_americano():
    """Formato do arquivo real: ponto decimal, sem separador de milhar."""
    assert _parse_valor("3165.44") == pytest.approx(3165.44)
    assert _parse_valor("136981.90") == pytest.approx(136981.90)
    assert _parse_valor("1495743660.28") == pytest.approx(1495743660.28)


def test_parse_valor_nao_multiplica_por_cem():
    """Guarda de regressão direta do bug: '3165.44' virava 316544.0."""
    assert _parse_valor("3165.44") != pytest.approx(316544.0)
    assert _parse_valor("3165.44") < 4000


def test_parse_valor_inteiro_sem_decimal():
    assert _parse_valor("1200") == pytest.approx(1200.0)


def test_parse_valor_aceita_formato_brasileiro_se_a_pgfn_mudar():
    """A vírgula desambigua -- o formato real não usa nenhuma."""
    assert _parse_valor("1.234,56") == pytest.approx(1234.56)
    assert _parse_valor("500,00") == pytest.approx(500.0)


def test_parse_valor_vazio_ou_invalido():
    assert _parse_valor(None) == 0.0
    assert _parse_valor("") == 0.0
    assert _parse_valor("   ") == 0.0
    assert _parse_valor("abc") == 0.0


# --- iter_pgfn_records ---

def test_iter_records_skips_pessoa_fisica(pgfn_csv: Path):
    records = list(iter_pgfn_records(pgfn_csv))
    cnpjs = {r.cnpj for r in records}
    assert normalize_cnpj(CPF_PESSOA_FISICA) not in cnpjs  # é None mesmo, mas reforça a intenção
    assert all(len(r.cnpj) == 14 for r in records)


def test_iter_records_parses_valor_formato_real(pgfn_csv: Path):
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


# --- _situacao_conta_como_divida_ativa ---
# Regra reescrita em 23/08/2026 contra o arquivo real: a PGFN só publica
# inscrição ATIVA, e os 97 valores distintos medidos começam com "ATIVA".
# A blacklist anterior ("EXTINTA"/"CANCELADA"/...) casava com 0 das
# 45.553.971 linhas -- era um no-op silencioso.

# Amostra literal de SITUACAO_INSCRICAO observados no arquivo de 202606,
# cobrindo as grafias mais frequentes e as de cauda longa.
SITUACOES_REAIS_ATIVAS = [
    "ATIVA EM COBRANCA",
    "ATIVA AJUIZADA",
    "ATIVA A SER AJUIZADA",
    "ATIVA NAO AJUIZAVEL NEGOCIADA NO SISPAR",
    "ATIVA AJUIZADA NEGOCIADA NO SISPAR",
    "ATIVA A SER COBRADA",
    "ATIVA COM AJUIZAMENTO A SER PROSSEGUIDO",
    "ATIVA AJUIZADA - GARANTIA - SEGURO GARANTIA",
    "ATIVA NAO AJUIZAVEL COM EXIGIBILIDADE DO CREDITO SUSPENSA-DEC.JUDICIAL",
    "ATIVA AJUIZADA PARCELADA LEI 12996/14",
    "ATIVA CADASTRADA",
]


@pytest.mark.parametrize("situacao", SITUACOES_REAIS_ATIVAS)
def test_situacoes_reais_do_arquivo_contam_como_divida_ativa(situacao: str):
    assert _situacao_conta_como_divida_ativa(situacao) is True


@pytest.mark.parametrize(
    "situacao",
    ["EXTINTA", "EXTINTA POR PAGAMENTO", "CANCELADA", "QUITADA", ""],
)
def test_situacao_nao_ativa_nao_conta(situacao: str):
    """Hipotéticas -- não aparecem no arquivo real. O ponto é o sentido do
    erro: se surgirem, ficam de FORA da pré-seleção paga, nunca dentro."""
    assert _situacao_conta_como_divida_ativa(situacao) is False


def test_parcelamento_em_curso_ainda_conta_como_divida_ativa():
    """Decisão registrada: "Benefício Fiscal" (NEGOCIADA NO SISPAR e afins)
    é dívida sendo paga, não dívida quitada -- 20,5% das inscrições de PJ.
    Excluir isso é decisão do Douglas, não do parser."""
    assert _situacao_conta_como_divida_ativa("ATIVA NAO AJUIZAVEL NEGOCIADA NO SISPAR") is True


# --- build_pgfn_index / get_debt_summary ---

def test_index_counts_multiple_active_inscricoes(pgfn_csv: Path):
    index = build_pgfn_index([pgfn_csv])
    summary = get_debt_summary(index, CNPJ_A)
    assert summary is not None
    assert summary.qtd_inscricoes_ativas == 2
    assert summary.valor_total == pytest.approx(1734.56)
    assert summary.algum_ajuizado is True


def test_index_exclui_situacao_nao_ativa_por_completo(pgfn_csv: Path):
    index = build_pgfn_index([pgfn_csv])
    summary = get_debt_summary(index, CNPJ_B)
    assert summary is None  # única inscrição não era ATIVA -> CNPJ nem aparece no índice


def test_index_counts_only_active_when_mixed(pgfn_csv: Path):
    index = build_pgfn_index([pgfn_csv])
    summary = get_debt_summary(index, CNPJ_C)
    assert summary is not None
    assert summary.qtd_inscricoes_ativas == 1  # a não-ATIVA não conta
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
        "NUMERO_INSCRICAO": "FGTS.000001-01", "SITUACAO_INSCRICAO": "ATIVA A SER AJUIZADA",
        "RECEITA_PRINCIPAL": "FGTS", "DATA_INSCRICAO": "2024-08-01",
        "INDICADOR_AJUIZADO": "NAO", "VALOR_CONSOLIDADO": "999.00",
    }
    with open(fgts_path, "w", encoding="latin-1", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerow(extra_row)

    index = build_pgfn_index([pgfn_csv, fgts_path])
    summary = get_debt_summary(index, CNPJ_A)
    assert summary.qtd_inscricoes_ativas == 3  # 2 do SIDA + 1 do FGTS
