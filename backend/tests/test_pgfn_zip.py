"""
Testes da leitura dos arquivos da PGFN direto de dentro do .zip
(app/services/pgfn.py, adicionado em 23/08/2026).

Até então o parser só lia `*.csv` já extraído, e `find_pgfn_csv_files` não
reconhecia nenhum dos três `.zip` publicados pela PGFN. Descompactar o
`Dados_abertos_Nao_Previdenciario.zip` (1,3 GB comprimido, ~9,0 GB
descomprimidos) já estourou o disco disponível uma vez -- é esse problema
concreto que estes testes fixam.

Diferença importante em relação ao .zip da Receita Federal (ver
tests/test_receita_federal_bulk_zip.py): o da PGFN tem VÁRIOS CSVs dentro
(`arquivo_lai_SIDA_1_202606.csv` .. `_6_`, confirmado no arquivo real), e
todos precisam ser lidos. Ler só o primeiro devolveria uma fração da base
sem nenhum sinal de que faltou coisa.
"""
import csv
import zipfile
from pathlib import Path

import pytest

from app.services.arquivo_utils import ArquivoZipInvalidoError
from app.services.pgfn import (
    DEFAULT_ENCODING,
    PGFN_COLUMNS,
    build_pgfn_index,
    find_pgfn_csv_files,
    get_debt_summary,
    iter_pgfn_records,
)

# Os três nomes reais publicados em dadosabertos.pgfn.gov.br (23/08/2026).
NOMES_ZIP_REAIS = [
    "Dados_abertos_Nao_Previdenciario.zip",
    "Dados_abertos_Previdenciario.zip",
    "Dados_abertos_FGTS.zip",
]

CNPJ_A = "11222333000181"
CNPJ_B = "44555666000199"
CNPJ_C = "77888999000100"


def _linha(cnpj: str, inscricao: str, situacao: str = "ATIVA EM COBRANCA", valor: str = "100.00", ajuizado: str = "NAO") -> dict:
    return {
        "CPF_CNPJ": cnpj,
        "TIPO_PESSOA": "PESSOA JURIDICA",
        "NOME_DEVEDOR": "CLINICA AÇÃO LTDA",  # acento exercita o latin-1
        "NUMERO_INSCRICAO": inscricao,
        "SITUACAO_INSCRICAO": situacao,
        "RECEITA_PRINCIPAL": "IRPJ",
        "DATA_INSCRICAO": "2024-01-15",
        "INDICADOR_AJUIZADO": ajuizado,
        # Formato americano -- o real do arquivo da PGFN (ver _parse_valor).
        "VALOR_CONSOLIDADO": valor,
    }


def _escrever_csv(path: Path, linhas: list[dict]) -> Path:
    with open(path, "w", encoding=DEFAULT_ENCODING, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(PGFN_COLUMNS.values()), delimiter=";")
        writer.writeheader()
        writer.writerows(linhas)
    return path


# Três "fatias" com cabeçalho próprio cada uma, como no arquivo real.
FATIAS = {
    "arquivo_lai_SIDA_1_202606.csv": [
        _linha(CNPJ_A, "10.1.26.000001-01", valor="1000.00"),
        _linha(CNPJ_B, "10.1.25.000002-01", situacao="EXTINTA POR PAGAMENTO", valor="800.00"),
    ],
    "arquivo_lai_SIDA_2_202606.csv": [
        _linha(CNPJ_A, "10.1.26.000001-02", valor="500.00", ajuizado="SIM"),
        _linha(CNPJ_C, "10.1.26.000003-01", valor="2000.00"),
    ],
    "arquivo_lai_SIDA_3_202606.csv": [
        _linha(CNPJ_C, "10.1.20.000003-02", situacao="EXTINTA", valor="300.00"),
    ],
}


@pytest.fixture
def csvs_extraidos(tmp_path: Path) -> list[Path]:
    """As três fatias como arquivos soltos na pasta -- o formato de antes."""
    pasta = tmp_path / "extraidos"
    pasta.mkdir()
    return [_escrever_csv(pasta / nome, linhas) for nome, linhas in FATIAS.items()]


@pytest.fixture
def zip_pgfn(tmp_path: Path, csvs_extraidos: list[Path]) -> Path:
    """As MESMAS três fatias, dentro de um .zip -- o formato do site."""
    pasta = tmp_path / "zipado"
    pasta.mkdir()
    destino = pasta / "Dados_abertos_Nao_Previdenciario.zip"
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for caminho in csvs_extraidos:
            z.write(caminho, arcname=caminho.name)
    return destino


# ---------------------------------------------------------------------------
# Ler do .zip == ler dos CSVs extraídos
# ---------------------------------------------------------------------------

def test_zip_le_todos_os_membros_nao_so_o_primeiro(zip_pgfn: Path):
    """O ponto central: o .zip da PGFN tem várias fatias e TODAS contam."""
    inscricoes = {r.numero_inscricao for r in iter_pgfn_records(zip_pgfn)}
    # As 5 linhas das 3 fatias, cruas. `iter_pgfn_records` não filtra
    # situação quitada -- isso é `build_pgfn_index` (ver teste adiante).
    assert inscricoes == {
        "10.1.26.000001-01",  # fatia 1
        "10.1.25.000002-01",  # fatia 1
        "10.1.26.000001-02",  # fatia 2
        "10.1.26.000003-01",  # fatia 2
        "10.1.20.000003-02",  # fatia 3
    }


def test_zip_produz_os_mesmos_records_que_os_csvs_extraidos(zip_pgfn: Path, csvs_extraidos: list[Path]):
    def chave(r):
        return (r.cnpj, r.numero_inscricao, r.situacao, r.ajuizado, r.valor_consolidado, r.nome_devedor)

    do_zip = sorted(chave(r) for r in iter_pgfn_records(zip_pgfn))
    dos_extraidos = sorted(
        chave(r) for caminho in csvs_extraidos for r in iter_pgfn_records(caminho)
    )
    assert do_zip == dos_extraidos


def test_indice_do_zip_igual_ao_indice_dos_csvs_extraidos(zip_pgfn: Path, csvs_extraidos: list[Path]):
    """Resultado final (o que vai pro Redis) idêntico nos dois formatos."""
    indice_zip = build_pgfn_index([zip_pgfn])
    indice_csv = build_pgfn_index(csvs_extraidos)

    assert set(indice_zip) == set(indice_csv)
    for cnpj in indice_zip:
        do_zip, do_csv = indice_zip[cnpj], indice_csv[cnpj]
        assert do_zip.qtd_inscricoes_ativas == do_csv.qtd_inscricoes_ativas
        assert do_zip.valor_total == pytest.approx(do_csv.valor_total)
        assert do_zip.algum_ajuizado == do_csv.algum_ajuizado


def test_indice_do_zip_soma_inscricoes_entre_fatias_diferentes(zip_pgfn: Path):
    """CNPJ_A tem uma inscrição na fatia 1 e outra na fatia 2."""
    summary = get_debt_summary(build_pgfn_index([zip_pgfn]), CNPJ_A)
    assert summary is not None
    assert summary.qtd_inscricoes_ativas == 2
    assert summary.valor_total == pytest.approx(1500.00)
    assert summary.algum_ajuizado is True


def test_indice_do_zip_continua_excluindo_situacao_nao_ativa(zip_pgfn: Path):
    """CNPJ_B só tem inscrição não-ATIVA (fatia 1) -- não entra no índice.
    CNPJ_C tem uma ATIVA (fatia 2) e uma não-ATIVA (fatia 3) -> conta 1."""
    indice = build_pgfn_index([zip_pgfn])
    assert get_debt_summary(indice, CNPJ_B) is None
    assert get_debt_summary(indice, CNPJ_C).qtd_inscricoes_ativas == 1


def test_zip_preserva_encoding_latin1(zip_pgfn: Path):
    """O acento tem que sobreviver ao TextIOWrapper de dentro do zip."""
    assert all(r.nome_devedor == "CLINICA AÇÃO LTDA" for r in iter_pgfn_records(zip_pgfn))


def test_zip_com_fatia_fora_do_layout_aponta_o_membro(tmp_path: Path):
    """Erro de coluna precisa nomear a fatia, não o .zip de 1,3 GB."""
    boa = _escrever_csv(tmp_path / "arquivo_lai_SIDA_1_202606.csv", [_linha(CNPJ_A, "1")])
    ruim = tmp_path / "arquivo_lai_SIDA_2_202606.csv"
    ruim.write_text("COLUNA_ERRADA;OUTRA\nvalor1;valor2\n", encoding=DEFAULT_ENCODING)

    destino = tmp_path / "Dados_abertos_FGTS.zip"
    with zipfile.ZipFile(destino, "w") as z:
        z.write(boa, arcname=boa.name)
        z.write(ruim, arcname=ruim.name)

    with pytest.raises(ValueError, match="arquivo_lai_SIDA_2_202606.csv"):
        list(iter_pgfn_records(destino))


def test_zip_sem_nenhum_csv_dentro_levanta_erro(tmp_path: Path):
    """Melhor falhar alto do que devolver zero linhas em silêncio."""
    destino = tmp_path / "Dados_abertos_FGTS.zip"
    leia_me = tmp_path / "leia_me.txt"
    leia_me.write_text("sem dados aqui", encoding="utf-8")
    with zipfile.ZipFile(destino, "w") as z:
        z.write(leia_me, arcname=leia_me.name)

    with pytest.raises(ArquivoZipInvalidoError, match="leia_me.txt"):
        list(iter_pgfn_records(destino))


# ---------------------------------------------------------------------------
# find_pgfn_csv_files -- os três nomes reais + compatibilidade com extraído
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nome_zip", NOMES_ZIP_REAIS)
def test_find_reconhece_os_nomes_reais_da_pgfn(tmp_path: Path, nome_zip: str):
    (tmp_path / nome_zip).write_bytes(b"")
    assert [p.name for p in find_pgfn_csv_files(tmp_path)] == [nome_zip]


def test_find_reconhece_os_tres_zips_juntos(tmp_path: Path):
    for nome in NOMES_ZIP_REAIS:
        (tmp_path / nome).write_bytes(b"")
    assert {p.name for p in find_pgfn_csv_files(tmp_path)} == set(NOMES_ZIP_REAIS)


def test_find_ignora_maiuscula_minuscula(tmp_path: Path):
    """Foi um glob case-sensitive que já ignorou os .zip da Receita em
    silêncio -- não repetir o erro aqui."""
    (tmp_path / "DADOS_ABERTOS_FGTS.ZIP").write_bytes(b"")
    assert len(find_pgfn_csv_files(tmp_path)) == 1


def test_find_aceita_variacao_de_nome_do_download(tmp_path: Path):
    """Navegador salvando duplicata / trimestre no nome não pode sumir."""
    (tmp_path / "Dados_abertos_FGTS (1).zip").write_bytes(b"")
    (tmp_path / "Dados_abertos_Previdenciario_202606.zip").write_bytes(b"")
    assert len(find_pgfn_csv_files(tmp_path)) == 2


def test_find_mantem_compatibilidade_com_csv_extraido(tmp_path: Path):
    (tmp_path / "arquivo_lai_SIDA_1_202606.csv").write_text("", encoding="utf-8")
    (tmp_path / "arquivo_lai_SIDA_2_202606.csv").write_text("", encoding="utf-8")
    assert [p.name for p in find_pgfn_csv_files(tmp_path)] == [
        "arquivo_lai_SIDA_1_202606.csv",
        "arquivo_lai_SIDA_2_202606.csv",
    ]


def test_find_aceita_os_dois_formatos_na_mesma_pasta(tmp_path: Path):
    (tmp_path / "Dados_abertos_FGTS.zip").write_bytes(b"")
    (tmp_path / "arquivo_lai_SIDA_1_202606.csv").write_text("", encoding="utf-8")
    assert len(find_pgfn_csv_files(tmp_path)) == 2


def test_find_ignora_zip_que_nao_e_da_pgfn(tmp_path: Path):
    """A pasta pode ter outros .zip -- só os da PGFN entram."""
    (tmp_path / "Estabelecimentos0.zip").write_bytes(b"")
    (tmp_path / "backup.zip").write_bytes(b"")
    assert find_pgfn_csv_files(tmp_path) == []


def test_find_pasta_inexistente_devolve_lista_vazia(tmp_path: Path):
    assert find_pgfn_csv_files(tmp_path / "nao_existe") == []


def test_find_ignora_subdiretorios(tmp_path: Path):
    (tmp_path / "Dados_abertos_FGTS.zip").mkdir()
    assert find_pgfn_csv_files(tmp_path) == []
