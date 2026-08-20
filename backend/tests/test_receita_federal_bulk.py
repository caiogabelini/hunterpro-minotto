"""
Testes do parser do arquivo de Estabelecimentos da Receita Federal.

Gera um CSV sintético no layout oficial confirmado contra o PDF da
Receita Federal (ver docstring de receita_federal_bulk.py) — mesmo
espírito de test_pgfn.py.
"""
import csv
from pathlib import Path

import pytest

from app.services.receita_federal_bulk import (
    EMPRESAS_COLUNAS,
    ESTABELECIMENTOS_COLUNAS,
    build_razao_social_index,
    enriquecer_com_razao_social,
    filtrar_semente_leads,
    find_empresas_rfb_files,
    find_estabelecimentos_rfb_files,
    iter_empresas,
    iter_estabelecimentos,
)

# (cnpj_basico, cnpj_ordem, cnpj_dv, matriz_filial, nome_fantasia, situacao,
#  cnae_principal, uf) -- as demais colunas ficam com valores fixos no helper.
ESTABELECIMENTO_MEDICO_ATIVO_SP = ("11222333", "0001", "81", "1", "CLINICA A", "02", "8630501", "SP")
ESTABELECIMENTO_ODONTO_ATIVO_SP = ("22333444", "0001", "90", "1", "ODONTO B", "02", "8630504", "SP")
ESTABELECIMENTO_MEDICO_BAIXADO_SP = ("33444555", "0001", "00", "1", "CLINICA C FECHADA", "08", "8630501", "SP")
ESTABELECIMENTO_MEDICO_ATIVO_RJ = ("44555666", "0001", "11", "1", "CLINICA D", "02", "8630599", "RJ")
ESTABELECIMENTO_NAO_MEDICO_ATIVO_SP = ("55666777", "0001", "22", "1", "MERCADO E", "02", "4711301", "SP")
ESTABELECIMENTO_MEDICO_FILIAL_SP = ("11222333", "0002", "62", "2", "CLINICA A FILIAL", "02", "8630501", "SP")


def _linha(tupla: tuple) -> dict:
    cnpj_basico, cnpj_ordem, cnpj_dv, matriz_filial, nome_fantasia, situacao, cnae, uf = tupla
    valores = {
        "cnpj_basico": cnpj_basico,
        "cnpj_ordem": cnpj_ordem,
        "cnpj_dv": cnpj_dv,
        "identificador_matriz_filial": matriz_filial,
        "nome_fantasia": nome_fantasia,
        "situacao_cadastral": situacao,
        "data_situacao_cadastral": "20200101",
        "motivo_situacao_cadastral": "00",
        "nome_cidade_exterior": "",
        "pais": "",
        "data_inicio_atividade": "20150610",
        "cnae_fiscal_principal": cnae,
        "cnae_fiscal_secundaria": "",
        "tipo_logradouro": "RUA",
        "logradouro": "TESTE",
        "numero": "100",
        "complemento": "",
        "bairro": "CENTRO",
        "cep": "01000000",
        "uf": uf,
        "municipio": "7107",
        "ddd_1": "11",
        "telefone_1": "999999999",
        "ddd_2": "",
        "telefone_2": "",
        "ddd_fax": "",
        "fax": "",
        "correio_eletronico": "",
        "situacao_especial": "",
        "data_situacao_especial": "",
    }
    return valores


@pytest.fixture
def estabelecimentos_csv(tmp_path: Path) -> Path:
    path = tmp_path / "K3241.K03200Y0.D60411.ESTABELE"
    linhas = [
        _linha(ESTABELECIMENTO_MEDICO_ATIVO_SP),
        _linha(ESTABELECIMENTO_ODONTO_ATIVO_SP),
        _linha(ESTABELECIMENTO_MEDICO_BAIXADO_SP),
        _linha(ESTABELECIMENTO_MEDICO_ATIVO_RJ),
        _linha(ESTABELECIMENTO_NAO_MEDICO_ATIVO_SP),
        _linha(ESTABELECIMENTO_MEDICO_FILIAL_SP),
    ]

    with open(path, "w", encoding="latin-1", newline="") as f:
        writer = csv.writer(f, delimiter=";", quotechar='"', quoting=csv.QUOTE_ALL)
        for linha in linhas:
            writer.writerow([linha[coluna] for coluna in ESTABELECIMENTOS_COLUNAS])

    return path


def _linha_empresa(cnpj_basico: str, razao_social: str) -> dict:
    return {
        "cnpj_basico": cnpj_basico,
        "razao_social": razao_social,
        "natureza_juridica": "2062",
        "qualificacao_responsavel": "49",
        "capital_social": "10000,00",
        "porte_empresa": "01",
        "ente_federativo_responsavel": "",
    }


def _escrever_empresas_csv(path: Path, cnpjs_e_razoes: list[tuple[str, str]]) -> None:
    with open(path, "w", encoding="latin-1", newline="") as f:
        writer = csv.writer(f, delimiter=";", quotechar='"', quoting=csv.QUOTE_ALL)
        for cnpj_basico, razao_social in cnpjs_e_razoes:
            linha = _linha_empresa(cnpj_basico, razao_social)
            writer.writerow([linha[coluna] for coluna in EMPRESAS_COLUNAS])


@pytest.fixture
def empresas_csv(tmp_path: Path) -> Path:
    """Arquivo EMPRESAS com razão social só pros CNPJs básicos de
    CLINICA A (SP) e CLINICA D (RJ) -- de propósito não inclui o de
    CLINICA F (MG, usado em test_multiplos_arquivos_sao_mesclados) nem o
    de ODONTO B, pra exercitar o caso de CNPJ ausente no join."""
    path = tmp_path / "K3241.K03200Y0.D10612.EMPRECSV"
    _escrever_empresas_csv(path, [
        ("11222333", "CLINICA A LTDA"),
        ("44555666", "CLINICA D LTDA"),
    ])
    return path


# --- iter_estabelecimentos ---

def test_iter_estabelecimentos_parseia_todas_as_colunas(estabelecimentos_csv: Path):
    linhas = list(iter_estabelecimentos(estabelecimentos_csv))
    assert len(linhas) == 6
    assert linhas[0]["cnpj_basico"] == "11222333"
    assert linhas[0]["nome_fantasia"] == "CLINICA A"
    assert linhas[0]["cnae_fiscal_principal"] == "8630501"


def test_iter_estabelecimentos_pula_linha_incompleta(tmp_path: Path):
    path = tmp_path / "arquivo_corrompido.ESTABELE"
    with open(path, "w", encoding="latin-1", newline="") as f:
        f.write('"11222333";"0001"\n')  # linha truncada, faltam colunas

    assert list(iter_estabelecimentos(path)) == []


# --- filtrar_semente_leads: CNAE ---

def test_filtro_inclui_subclasses_medicas_do_grupo(estabelecimentos_csv: Path):
    resultado = list(filtrar_semente_leads([estabelecimentos_csv]))
    cnpjs = {e.cnpj for e in resultado}
    assert "11222333000181" in cnpjs  # 8630501, ativo, SP
    assert "44555666000111" in cnpjs  # 8630599, ativo, RJ


def test_filtro_exclui_odontologica(estabelecimentos_csv: Path):
    resultado = list(filtrar_semente_leads([estabelecimentos_csv]))
    cnpjs = {e.cnpj for e in resultado}
    assert "22333444000190" not in cnpjs  # 8630504, odontológica


def test_filtro_exclui_cnae_fora_do_grupo(estabelecimentos_csv: Path):
    resultado = list(filtrar_semente_leads([estabelecimentos_csv]))
    cnpjs = {e.cnpj for e in resultado}
    assert "55666777000122" not in cnpjs  # 4711301, mercado


# --- filtrar_semente_leads: situação cadastral ---

def test_filtro_situacao_ativa_por_padrao_exclui_baixado(estabelecimentos_csv: Path):
    resultado = list(filtrar_semente_leads([estabelecimentos_csv]))
    cnpjs = {e.cnpj for e in resultado}
    assert "33444555000100" not in cnpjs  # médico, mas baixado (08)


def test_filtro_situacao_desativado_inclui_baixado(estabelecimentos_csv: Path):
    resultado = list(filtrar_semente_leads([estabelecimentos_csv], apenas_ativos=False))
    cnpjs = {e.cnpj for e in resultado}
    assert "33444555000100" in cnpjs


# --- filtrar_semente_leads: UF / região ---

def test_filtro_por_uf(estabelecimentos_csv: Path):
    resultado = list(filtrar_semente_leads([estabelecimentos_csv], ufs={"RJ"}))
    ufs = {e.uf for e in resultado}
    assert ufs == {"RJ"}


def test_filtro_por_regiao(estabelecimentos_csv: Path):
    resultado = list(filtrar_semente_leads([estabelecimentos_csv], regiao="sudeste"))
    cnpjs = {e.cnpj for e in resultado}
    # SP e RJ são sudeste -- os dois médicos ativos devem aparecer
    assert "11222333000181" in cnpjs
    assert "44555666000111" in cnpjs


def test_filtro_uf_e_regiao_combinados_faz_uniao(estabelecimentos_csv: Path):
    resultado = list(filtrar_semente_leads([estabelecimentos_csv], ufs={"RJ"}, regiao="norte"))
    ufs = {e.uf for e in resultado}
    assert ufs == {"RJ"}  # não tem estabelecimento do Norte no fixture, só confirma que RJ ainda passa


def test_sem_filtro_de_localizacao_nao_filtra_por_uf(estabelecimentos_csv: Path):
    resultado = list(filtrar_semente_leads([estabelecimentos_csv]))
    ufs = {e.uf for e in resultado}
    assert ufs == {"SP", "RJ"}


# --- CNPJ / matriz-filial ---

def test_cnpj_montado_com_14_digitos(estabelecimentos_csv: Path):
    resultado = list(filtrar_semente_leads([estabelecimentos_csv]))
    for estabelecimento in resultado:
        assert len(estabelecimento.cnpj) == 14


def test_filial_nao_e_excluida(estabelecimentos_csv: Path):
    """Matriz e filial do mesmo CNPJ básico devem aparecer separadas -- cada
    uma é um estabelecimento físico potencialmente prospectável."""
    resultado = list(filtrar_semente_leads([estabelecimentos_csv]))
    cnpjs = {e.cnpj for e in resultado}
    assert "11222333000181" in cnpjs  # matriz
    assert "11222333000262" in cnpjs  # filial


# --- múltiplos arquivos ---

def test_multiplos_arquivos_sao_mesclados(estabelecimentos_csv: Path, tmp_path: Path):
    segundo_arquivo = tmp_path / "K3241.K03200Y1.D60411.ESTABELE"
    with open(segundo_arquivo, "w", encoding="latin-1", newline="") as f:
        writer = csv.writer(f, delimiter=";", quotechar='"', quoting=csv.QUOTE_ALL)
        outro = ("66777888", "0001", "33", "1", "CLINICA F", "02", "8630502", "MG")
        writer.writerow([_linha(outro)[coluna] for coluna in ESTABELECIMENTOS_COLUNAS])

    resultado = list(filtrar_semente_leads([estabelecimentos_csv, segundo_arquivo]))
    cnpjs = {e.cnpj for e in resultado}
    assert "66777888000133" in cnpjs
    assert "11222333000181" in cnpjs


# --- find_estabelecimentos_rfb_files ---

def test_find_estabelecimentos_rfb_files_encontra_por_padrao(tmp_path: Path):
    (tmp_path / "K3241.K03200Y0.D60411.ESTABELE").write_text("")
    (tmp_path / "K3241.K03200Y1.D60411.ESTABELE").write_text("")
    (tmp_path / "outro_arquivo.txt").write_text("")

    encontrados = find_estabelecimentos_rfb_files(tmp_path)
    assert len(encontrados) == 2


def test_find_estabelecimentos_rfb_files_diretorio_inexistente_retorna_vazio(tmp_path: Path):
    assert find_estabelecimentos_rfb_files(tmp_path / "nao_existe") == []


# --- filtrar_semente_leads: razao_social ainda não preenchida ---

def test_filtrar_semente_leads_sozinho_tem_razao_social_vazia(estabelecimentos_csv: Path):
    """Sem passar por enriquecer_com_razao_social, razao_social fica no
    valor padrão (string vazia, nunca None)."""
    resultado = list(filtrar_semente_leads([estabelecimentos_csv]))
    assert resultado
    assert all(e.razao_social == "" for e in resultado)


# --- iter_empresas ---

def test_iter_empresas_parseia_colunas(empresas_csv: Path):
    linhas = list(iter_empresas(empresas_csv))
    assert len(linhas) == 2
    assert linhas[0]["cnpj_basico"] == "11222333"
    assert linhas[0]["razao_social"] == "CLINICA A LTDA"


def test_iter_empresas_pula_linha_incompleta(tmp_path: Path):
    path = tmp_path / "arquivo_corrompido.EMPRECSV"
    with open(path, "w", encoding="latin-1", newline="") as f:
        f.write('"11222333"\n')  # linha truncada, faltam colunas

    assert list(iter_empresas(path)) == []


# --- build_razao_social_index ---

def test_build_razao_social_index_so_pros_cnpjs_alvo(empresas_csv: Path):
    indice = build_razao_social_index([empresas_csv], {"11222333"})
    assert indice == {"11222333": "CLINICA A LTDA"}


def test_build_razao_social_index_cnpj_nao_encontrado_fica_de_fora(empresas_csv: Path):
    indice = build_razao_social_index([empresas_csv], {"99999999"})
    assert indice == {}


def test_build_razao_social_index_sem_alvo_nenhum_nao_indexa_nada(empresas_csv: Path):
    indice = build_razao_social_index([empresas_csv], set())
    assert indice == {}


# --- enriquecer_com_razao_social: o join ---

def test_enriquecer_com_razao_social_junta_pela_raiz_do_cnpj(
    estabelecimentos_csv: Path, empresas_csv: Path
):
    estabelecimentos = list(filtrar_semente_leads([estabelecimentos_csv]))
    enriquecidos = enriquecer_com_razao_social(estabelecimentos, [empresas_csv])

    por_cnpj = {e.cnpj: e for e in enriquecidos}
    assert por_cnpj["11222333000181"].razao_social == "CLINICA A LTDA"
    assert por_cnpj["44555666000111"].razao_social == "CLINICA D LTDA"


def test_enriquecer_com_razao_social_matriz_e_filial_herdam_a_mesma_razao_social(
    estabelecimentos_csv: Path, empresas_csv: Path
):
    """Matriz e filial compartilham o mesmo cnpj_basico -- devem herdar a
    mesma razão social do join."""
    estabelecimentos = list(filtrar_semente_leads([estabelecimentos_csv]))
    enriquecidos = enriquecer_com_razao_social(estabelecimentos, [empresas_csv])

    por_cnpj = {e.cnpj: e for e in enriquecidos}
    assert por_cnpj["11222333000181"].razao_social == "CLINICA A LTDA"  # matriz
    assert por_cnpj["11222333000262"].razao_social == "CLINICA A LTDA"  # filial


def test_enriquecer_com_razao_social_cnpj_ausente_no_empresas_fica_vazio_sem_quebrar(
    estabelecimentos_csv: Path, empresas_csv: Path
):
    """CNPJ que existe no ESTABELECIMENTOS filtrado mas não aparece no
    arquivo EMPRESAS de teste -- caso central pedido pro teste: não pode
    quebrar, só deixa razao_social vazia (o fixture empresas_csv já
    exclui de propósito a razão social de CLINICA F, que só existe no
    segundo arquivo ESTABELECIMENTOS usado no teste de merge acima)."""
    segundo_arquivo = estabelecimentos_csv.parent / "K3241.K03200Y1.D60411.ESTABELE"
    with open(segundo_arquivo, "w", encoding="latin-1", newline="") as f:
        writer = csv.writer(f, delimiter=";", quotechar='"', quoting=csv.QUOTE_ALL)
        outro = ("66777888", "0001", "33", "1", "CLINICA F", "02", "8630502", "MG")
        writer.writerow([_linha(outro)[coluna] for coluna in ESTABELECIMENTOS_COLUNAS])

    estabelecimentos = list(filtrar_semente_leads([estabelecimentos_csv, segundo_arquivo]))
    enriquecidos = enriquecer_com_razao_social(estabelecimentos, [empresas_csv])

    por_cnpj = {e.cnpj: e for e in enriquecidos}
    assert por_cnpj["11222333000181"].razao_social == "CLINICA A LTDA"  # presente no EMPRESAS
    assert por_cnpj["66777888000133"].razao_social == ""  # ausente -- vazio, não quebra
    assert len(enriquecidos) == len(estabelecimentos)  # nenhum registro foi descartado


def test_enriquecer_com_razao_social_junta_multiplos_arquivos_empresas(
    estabelecimentos_csv: Path, tmp_path: Path
):
    arquivo1 = tmp_path / "K3241.K03200Y0.D10612.EMPRECSV"
    arquivo2 = tmp_path / "K3241.K03200Y1.D10612.EMPRECSV"
    _escrever_empresas_csv(arquivo1, [("11222333", "CLINICA A LTDA")])
    _escrever_empresas_csv(arquivo2, [("44555666", "CLINICA D LTDA")])

    estabelecimentos = list(filtrar_semente_leads([estabelecimentos_csv]))
    enriquecidos = enriquecer_com_razao_social(estabelecimentos, [arquivo1, arquivo2])

    por_cnpj = {e.cnpj: e for e in enriquecidos}
    assert por_cnpj["11222333000181"].razao_social == "CLINICA A LTDA"
    assert por_cnpj["44555666000111"].razao_social == "CLINICA D LTDA"


# --- find_empresas_rfb_files ---

def test_find_empresas_rfb_files_encontra_por_padrao(tmp_path: Path):
    (tmp_path / "K3241.K03200Y0.D10612.EMPRECSV").write_text("")
    (tmp_path / "K3241.K03200Y1.D10612.EMPRECSV").write_text("")
    (tmp_path / "K3241.K03200Y0.D60411.ESTABELE").write_text("")  # não deve bater no padrão de EMPRESAS

    encontrados = find_empresas_rfb_files(tmp_path)
    assert len(encontrados) == 2


def test_find_empresas_rfb_files_diretorio_inexistente_retorna_vazio(tmp_path: Path):
    assert find_empresas_rfb_files(tmp_path / "nao_existe") == []
