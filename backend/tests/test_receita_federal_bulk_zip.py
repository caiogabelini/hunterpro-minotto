"""
Testes da leitura dos arquivos da Receita direto de dentro do .zip
(app/services/receita_federal_bulk.py, adicionado em 23/08/2026).

Até então o parser só lia CSV já extraído, e os globs case-sensitive
ignoravam os .zip em silêncio -- quem esquecesse de descompactar via a
busca "concluir com sucesso" trazendo zero leads. Estes testes fixam os
dois comportamentos novos: ler do .zip dá exatamente o mesmo resultado
que ler do extraído, e os finders reconhecem os dois formatos.
"""
import zipfile

import pytest
from app.services.receita_federal_bulk import (
    DEFAULT_ENCODING,
    ArquivoZipInvalidoError,
    build_municipios_index,
    build_razao_social_index,
    filtrar_semente_leads,
    find_empresas_rfb_files,
    find_estabelecimentos_rfb_files,
    find_municipios_rfb_files,
    iter_empresas,
    iter_estabelecimentos,
    iter_municipios,
)


def _linha_estabelecimento(i: int, cnae: str = "8630501", uf: str = "SP") -> str:
    """30 colunas, sem cabeçalho, ';', tudo entre aspas -- layout real."""
    colunas = [""] * 30
    colunas[0] = f"{i:08d}"          # cnpj_basico
    colunas[1] = "0001"              # cnpj_ordem
    colunas[2] = "55"                # cnpj_dv
    colunas[3] = "1"                 # matriz/filial
    colunas[4] = f"Clínica Ação {i}"  # nome_fantasia -- acento exercita o latin-1
    colunas[5] = "02"                # situacao_cadastral (ativa)
    colunas[10] = "20100101"         # data_inicio_atividade
    colunas[11] = cnae
    colunas[19] = uf
    colunas[20] = "7107"             # municipio (código RFB)
    return ";".join(f'"{c}"' for c in colunas)


def _linha_empresa(i: int) -> str:
    colunas = [f"{i:08d}", f"CLINICA AÇÃO {i} LTDA", "2062", "49", "100000,00", "03", ""]
    return ";".join(f'"{c}"' for c in colunas)


def _escrever(path, linhas):
    path.write_text("\r\n".join(linhas) + "\r\n", encoding=DEFAULT_ENCODING, newline="")
    return path


def _zipar(csv_path, zip_path, arcname=None):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(csv_path, arcname=arcname or csv_path.name)
    return zip_path


# ---------------------------------------------------------------------------
# Paridade: ler do .zip == ler do extraído
# ---------------------------------------------------------------------------


def test_iter_estabelecimentos_do_zip_e_igual_ao_extraido(tmp_path):
    csv_path = _escrever(
        tmp_path / "K3241.K03200Y0.D50308.ESTABELE",
        [_linha_estabelecimento(i) for i in range(300)],
    )
    zip_path = _zipar(csv_path, tmp_path / "Estabelecimentos0.zip")

    do_extraido = list(iter_estabelecimentos(csv_path))
    do_zip = list(iter_estabelecimentos(zip_path))

    assert len(do_zip) == 300
    assert do_zip == do_extraido


def test_acentuacao_latin1_sobrevive_a_leitura_do_zip(tmp_path):
    csv_path = _escrever(tmp_path / "K3241.ESTABELE", [_linha_estabelecimento(1)])
    zip_path = _zipar(csv_path, tmp_path / "Estabelecimentos0.zip")

    linha = list(iter_estabelecimentos(zip_path))[0]

    assert linha["nome_fantasia"] == "Clínica Ação 1"


def test_iter_empresas_do_zip_e_igual_ao_extraido(tmp_path):
    csv_path = _escrever(
        tmp_path / "K3241.K03200Y0.D50308.EMPRECSV", [_linha_empresa(i) for i in range(50)]
    )
    zip_path = _zipar(csv_path, tmp_path / "Empresas0.zip")

    assert list(iter_empresas(zip_path)) == list(iter_empresas(csv_path))


def test_iter_municipios_do_zip_e_igual_ao_extraido(tmp_path):
    csv_path = _escrever(
        tmp_path / "F.K03200$Z.D50308.MUNICCSV",
        ['"7107";"SAO PAULO"', '"0027";"MACAPA"', '"0003";"BOA VISTA"'],
    )
    zip_path = _zipar(csv_path, tmp_path / "Municipios.zip")

    assert list(iter_municipios(zip_path)) == list(iter_municipios(csv_path))
    assert build_municipios_index([zip_path]) == build_municipios_index([csv_path])


def test_pipeline_de_filtro_funciona_com_zip_de_ponta_a_ponta(tmp_path):
    """
    Não é só o parser: o filtro de CNAE/UF e o join de razão social também
    precisam funcionar com os caminhos apontando pra .zip.
    """
    estab = _escrever(
        tmp_path / "K3241.ESTABELE",
        [_linha_estabelecimento(1), _linha_estabelecimento(2, cnae="8630504"), _linha_estabelecimento(3)],
    )
    emp = _escrever(tmp_path / "K3241.EMPRECSV", [_linha_empresa(1), _linha_empresa(3)])
    zip_estab = _zipar(estab, tmp_path / "Estabelecimentos0.zip")
    zip_emp = _zipar(emp, tmp_path / "Empresas0.zip")

    # odontologia (8630504) fica de fora nos dois formatos
    do_zip = list(filtrar_semente_leads([zip_estab]))
    do_extraido = list(filtrar_semente_leads([estab]))
    assert [e.cnpj for e in do_zip] == [e.cnpj for e in do_extraido]
    assert len(do_zip) == 2

    indice = build_razao_social_index([zip_emp], {e.cnpj[:8] for e in do_zip})
    assert indice["00000001"] == "CLINICA AÇÃO 1 LTDA"


def test_leitura_do_zip_nao_extrai_nada_no_disco(tmp_path):
    """A economia de disco é o ponto -- ler não pode deixar resíduo."""
    csv_path = _escrever(tmp_path / "K3241.ESTABELE", [_linha_estabelecimento(i) for i in range(100)])
    zip_path = _zipar(csv_path, tmp_path / "Estabelecimentos0.zip")
    csv_path.unlink()  # só o .zip fica na pasta

    antes = {p.name for p in tmp_path.iterdir()}
    assert len(list(iter_estabelecimentos(zip_path))) == 100
    assert {p.name for p in tmp_path.iterdir()} == antes


# ---------------------------------------------------------------------------
# Zip com número inesperado de membros
# ---------------------------------------------------------------------------


def test_zip_com_multiplos_membros_da_erro_claro(tmp_path):
    a = _escrever(tmp_path / "fatia_a.ESTABELE", [_linha_estabelecimento(1)])
    b = _escrever(tmp_path / "fatia_b.ESTABELE", [_linha_estabelecimento(2)])
    zip_path = tmp_path / "Estabelecimentos0.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.write(a, arcname=a.name)
        z.write(b, arcname=b.name)

    with pytest.raises(ArquivoZipInvalidoError) as exc:
        list(iter_estabelecimentos(zip_path))

    mensagem = str(exc.value)
    assert "2 arquivos dentro" in mensagem
    assert "fatia_a.ESTABELE" in mensagem and "fatia_b.ESTABELE" in mensagem
    assert "esperado exatamente 1" in mensagem


def test_zip_vazio_da_o_mesmo_erro_claro(tmp_path):
    zip_path = tmp_path / "Estabelecimentos0.zip"
    with zipfile.ZipFile(zip_path, "w"):
        pass

    with pytest.raises(ArquivoZipInvalidoError) as exc:
        list(iter_estabelecimentos(zip_path))

    assert "0 arquivos dentro" in str(exc.value)


def test_diretorio_dentro_do_zip_nao_conta_como_membro(tmp_path):
    csv_path = _escrever(tmp_path / "K3241.ESTABELE", [_linha_estabelecimento(1)])
    zip_path = tmp_path / "Estabelecimentos0.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("uma_pasta/", "")
        z.write(csv_path, arcname="uma_pasta/K3241.ESTABELE")

    assert len(list(iter_estabelecimentos(zip_path))) == 1


def test_zip_ruim_no_municipios_nao_derruba_o_indice(tmp_path):
    """
    `build_municipios_index` promete nunca levantar exceção -- um .zip
    inválido tem que ser pulado como qualquer outro arquivo ilegível.
    """
    ruim = tmp_path / "Municipios.zip"
    ruim.write_bytes(b"isso nao e um zip")
    bom = _escrever(tmp_path / "F.MUNICCSV", ['"7107";"SAO PAULO"'])

    indice = build_municipios_index([ruim, bom])

    assert indice == {"7107": "SAO PAULO"}


# ---------------------------------------------------------------------------
# Finders: os dois padrões de nome
# ---------------------------------------------------------------------------


def _tocar(tmp_path, *nomes):
    for nome in nomes:
        (tmp_path / nome).write_bytes(b"")


@pytest.mark.parametrize(
    "finder, nomes_zip, nome_csv",
    [
        (find_estabelecimentos_rfb_files, ["Estabelecimentos0.zip", "Estabelecimentos9.zip"], "K3241.K03200Y0.D50308.ESTABELE"),
        (find_empresas_rfb_files, ["Empresas0.zip", "Empresas9.zip"], "K3241.K03200Y0.D50308.EMPRECSV"),
        (find_municipios_rfb_files, ["Municipios.zip"], "F.K03200$Z.D50308.MUNICCSV"),
    ],
)
def test_finders_reconhecem_zip_e_csv_extraido(tmp_path, finder, nomes_zip, nome_csv):
    _tocar(tmp_path, *nomes_zip, nome_csv)

    encontrados = {p.name for p in finder(tmp_path)}

    assert encontrados == {*nomes_zip, nome_csv}


@pytest.mark.parametrize(
    "finder, nome_zip",
    [
        (find_estabelecimentos_rfb_files, "Estabelecimentos0.zip"),
        (find_empresas_rfb_files, "Empresas0.zip"),
        (find_municipios_rfb_files, "Municipios.zip"),
    ],
)
def test_finders_acham_o_zip_sozinho_na_pasta(tmp_path, finder, nome_zip):
    """
    A regressão que motivou a mudança: pasta só com os .zip devolvia lista
    vazia, e a busca concluía "com sucesso" sem nenhum lead.
    """
    _tocar(tmp_path, nome_zip)
    assert [p.name for p in finder(tmp_path)] == [nome_zip]


def test_finders_ignoram_arquivos_de_outro_tipo(tmp_path):
    _tocar(
        tmp_path,
        "Estabelecimentos0.zip",
        "Empresas0.zip",
        "Municipios.zip",
        "Socios0.zip",          # outro arquivo da Receita, fora do escopo
        "leiame.txt",
    )

    assert [p.name for p in find_estabelecimentos_rfb_files(tmp_path)] == ["Estabelecimentos0.zip"]
    assert [p.name for p in find_empresas_rfb_files(tmp_path)] == ["Empresas0.zip"]
    assert [p.name for p in find_municipios_rfb_files(tmp_path)] == ["Municipios.zip"]


def test_finder_em_pasta_inexistente_devolve_lista_vazia():
    assert find_estabelecimentos_rfb_files("/pasta/que/nao/existe") == []
