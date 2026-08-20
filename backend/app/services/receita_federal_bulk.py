"""
Parser do arquivo de Estabelecimentos da Receita Federal (Dados Abertos do
CNPJ) — a "semente" de leads: filtra por CNAE (consultório médico) e
UF/região, devolvendo a lista de CNPJs encontrados.
================================================================================

Fonte: Dados Abertos do CNPJ da Receita Federal
(arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/) — mesmo
arquivo já usado no projeto HunterPro SR Contábil. Cada mês é publicado em
vários arquivos ESTABELECIMENTOS (nomeados tipo
K3241.K03200Y0.D60411.ESTABELE, K3241.K03200Y1.D60411.ESTABELE, ...) — o
número entre "K03200Y" e "D60411" é só um fatiamento alfabético/numérico
do arquivo completo (cada CNPJ cai numa fatia por hash), não muda o layout.

Layout confirmado contra o PDF oficial da Receita Federal
(gov.br/receitafederal/dados/cnpj-metadados.pdf, seção ESTABELECIMENTOS,
verificado em 19/08/2026) — 30 colunas, SEM cabeçalho, `;` como separador,
campos entre aspas duplas. Ver `ESTABELECIMENTOS_COLUNAS` abaixo pra ordem
exata.

⚠️ RAZÃO SOCIAL NÃO ESTÁ NO ARQUIVO ESTABELECIMENTOS: esse layout só tem
NOME_FANTASIA (pode vir vazio pra muitas empresas pequenas — é opcional).
A razão social mora no arquivo separado EMPRESAS
(K3241.K03200Y0.D10612.EMPRECSV e afins, confirmado contra o mesmo PDF
oficial, seção EMPRESAS) — 7 colunas, mesmo formato (sem cabeçalho, `;`,
aspas), chave é `cnpj_basico` (8 dígitos, os mesmos 8 primeiros dígitos
do CNPJ completo do ESTABELECIMENTOS). `enriquecer_com_razao_social`
junta os dois. Razão social é campo obrigatório no cadastro de CNPJ (ao
contrário de nome fantasia), por isso ela é o campo principal devolvido
por `gerar_semente_leads` (ver celery_app.py) — `nome_fantasia` fica como
complementar/opcional, e só é usado por `search_google_places` se a
razão social vier vazia (não deveria acontecer com o arquivo real; pode
acontecer em teste/dado parcial, ou se o CNPJ não aparecer no(s)
arquivo(s) EMPRESAS informado(s) — `enriquecer_com_razao_social` não
quebra nesse caso, só deixa `razao_social` vazia).

⚠️ MUNICÍPIO É UM CÓDIGO PRÓPRIO DA RECEITA, NÃO O IBGE: o campo
MUNICÍPIO deste arquivo é o "código do município de jurisdição" de uma
tabela de domínio própria da Receita Federal (publicada à parte, junto
com os outros arquivos de domínio: países, qualificações de sócio,
naturezas jurídicas, CNAEs) — não é o código IBGE usado pelo
CNES/app/services/ibge_municipios.py. Não cruzar um com o outro direto;
por enquanto, `app.services.receita_federal.fetch_empresa` (BrasilAPI)
continua sendo o jeito confirmado de obter o código IBGE por CNPJ.

O QUE ESTE MÓDULO FAZ:
  - Lê o(s) arquivo(s) ESTABELECIMENTOS em streaming, sem carregar tudo
    na memória (mesmo padrão do parser da PGFN — o arquivo completo tem
    dezenas de milhões de linhas)
  - Filtra: CNAE fiscal principal no grupo de consultório médico
    (prefixo "8630", exceto a subclasse odontológica — ver
    `CNAE_GRUPO_CONSULTORIO_MEDICO_PREFIXO` e `CNAES_EXCLUIDOS_DO_GRUPO`),
    situação cadastral ATIVA por padrão, e UF e/ou região
  - Devolve o CNPJ completo (14 dígitos) de cada estabelecimento que
    bateu no filtro, junto com o que mais o arquivo oferece pra essa
    etapa (nome_fantasia, UF, código CNAE, data de início de atividade)
  - `enriquecer_com_razao_social` junta a razão social (arquivo EMPRESAS)
    aos estabelecimentos já filtrados, pela raiz do CNPJ (`cnpj_basico`)
    — só busca no arquivo EMPRESAS os `cnpj_basico` que já sobraram do
    filtro de ESTABELECIMENTOS, não indexa o arquivo inteiro (~60 milhões
    de empresas), a mesma lógica de "filtra primeiro, depois cruza" já
    usada no resto do pipeline (PGFN, CNES)

O QUE ESTE MÓDULO **NÃO** FAZ (de propósito):
  - Não baixa nenhum dos dois arquivos — mesma divisão de
    responsabilidade da PGFN (`find_pgfn_csv_files`): download continua
    manual/agendado fora deste módulo, arquivos colocados num diretório
    configurável.
  - Não cruza com a tabela de domínio MUNICÍPIOS da Receita — ver
    ressalva acima sobre o campo `municipio`.
  - Não persiste nada — devolve os CNPJs filtrados (com razão social,
    depois do join) pra quem chamar decidir o que fazer (ex.:
    `search_google_places`, ver celery_app.py).
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# Layout confirmado contra o PDF oficial da Receita Federal (ver docstring
# do módulo) — 30 colunas, SEM cabeçalho, nesta ordem exata.
# ---------------------------------------------------------------------------
ESTABELECIMENTOS_COLUNAS = [
    "cnpj_basico",
    "cnpj_ordem",
    "cnpj_dv",
    "identificador_matriz_filial",
    "nome_fantasia",
    "situacao_cadastral",
    "data_situacao_cadastral",
    "motivo_situacao_cadastral",
    "nome_cidade_exterior",
    "pais",
    "data_inicio_atividade",
    "cnae_fiscal_principal",
    "cnae_fiscal_secundaria",
    "tipo_logradouro",
    "logradouro",
    "numero",
    "complemento",
    "bairro",
    "cep",
    "uf",
    "municipio",
    "ddd_1",
    "telefone_1",
    "ddd_2",
    "telefone_2",
    "ddd_fax",
    "fax",
    "correio_eletronico",
    "situacao_especial",
    "data_situacao_especial",
]

# Layout do arquivo EMPRESAS (K3241.K03200Y0.D10612.EMPRECSV e afins) —
# confirmado contra o mesmo PDF oficial da Receita Federal, seção
# EMPRESAS. 7 colunas, SEM cabeçalho, mesmo separador/aspas do
# ESTABELECIMENTOS. `cnpj_basico` é a chave de junção com
# ESTABELECIMENTOS_COLUNAS (mesmos 8 primeiros dígitos do CNPJ).
EMPRESAS_COLUNAS = [
    "cnpj_basico",
    "razao_social",
    "natureza_juridica",
    "qualificacao_responsavel",
    "capital_social",
    "porte_empresa",
    "ente_federativo_responsavel",
]

DEFAULT_ENCODING = "latin-1"
DEFAULT_DELIMITER = ";"

# "2 – ATIVA" no PDF oficial, mas arquivos reais desse tipo costumam
# zero-preencher pra 2 dígitos ("02") — comparação normaliza com zfill(2)
# em vez de comparar a string crua, pra não depender de qual dos dois
# formatos o arquivo real usa.
SITUACAO_CADASTRAL_ATIVA = "02"

# Grupo CNAE "Atividade médica ambulatorial" (classe 8630-5) — prefixo de
# 4 dígitos do CNAE fiscal principal (7 dígitos, sem pontuação, ex.:
# "8630501"). Subclasses confirmadas contra o CONCLA/IBGE em 19/08/2026:
# 8630-5/01 cirúrgicos, /02 exames complementares, /03 restrita a
# consultas, /04 odontológica, /06 vacinação, /07 reprodução assistida,
# /99 demais atividades ambulatoriais.
CNAE_GRUPO_CONSULTORIO_MEDICO_PREFIXO = "8630"

# 8630-5/04 é "Atividade odontológica" — fora do nicho (dentistas não têm
# RQE, que é registro específico de especialidade médica). Excluída por
# padrão; ajustar aqui se o cliente decidir incluir odontologia.
CNAES_EXCLUIDOS_DO_GRUPO = {"8630504"}

# Região -> UFs. Fixo (5 regiões, 27 UFs, geografia oficial estável) —
# diferente da tabela de municípios (~5.570 linhas, ver
# app/services/ibge_municipios.py sobre por que essa não é hardcoded.
UFS_POR_REGIAO: dict[str, set[str]] = {
    "norte": {"AC", "AP", "AM", "PA", "RO", "RR", "TO"},
    "nordeste": {"AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"},
    "centro-oeste": {"DF", "GO", "MT", "MS"},
    "sudeste": {"ES", "MG", "RJ", "SP"},
    "sul": {"PR", "RS", "SC"},
}


def _clean(value: str | None) -> str:
    """Mesmo padrão de app/services/pgfn.py."""
    if value is None:
        return ""
    return value.strip().strip('"').strip()


@dataclass
class EstabelecimentoRFB:
    """Um estabelecimento já filtrado e normalizado a partir de uma linha
    do arquivo ESTABELECIMENTOS.

    `razao_social` começa vazia — só é preenchida depois de passar por
    `enriquecer_com_razao_social` (o join com o arquivo EMPRESAS). Até lá
    (ou se o CNPJ básico não aparecer no(s) arquivo(s) EMPRESAS
    informado(s)), fica como string vazia, nunca None — mais fácil pra
    quem consome não ter que checar por None em todo lugar."""

    cnpj: str
    nome_fantasia: str
    situacao_cadastral: str
    cnae_fiscal_principal: str
    data_inicio_atividade: str
    municipio_codigo_rfb: str
    uf: str
    identificador_matriz_filial: str
    razao_social: str = ""


def _montar_cnpj(cnpj_basico: str, cnpj_ordem: str, cnpj_dv: str) -> str:
    return f"{cnpj_basico}{cnpj_ordem}{cnpj_dv}"


def _eh_cnae_do_grupo(cnae_fiscal_principal: str) -> bool:
    cnae = re.sub(r"\D", "", cnae_fiscal_principal or "")
    if not cnae.startswith(CNAE_GRUPO_CONSULTORIO_MEDICO_PREFIXO):
        return False
    return cnae not in CNAES_EXCLUIDOS_DO_GRUPO


def _eh_situacao_ativa(situacao_cadastral: str) -> bool:
    return situacao_cadastral.strip().zfill(2) == SITUACAO_CADASTRAL_ATIVA


def iter_estabelecimentos(
    csv_path: Path,
    encoding: str = DEFAULT_ENCODING,
    delimiter: str = DEFAULT_DELIMITER,
) -> Iterator[dict[str, str]]:
    """
    Lê o arquivo linha a linha (sem cabeçalho, sem carregar tudo na
    memória — mesmo padrão de `iter_pgfn_records`), devolvendo cada linha
    já como dict nomeado pelas colunas de `ESTABELECIMENTOS_COLUNAS`.

    Linhas com menos colunas que o layout esperado são puladas (arquivo
    corrompido/truncado) em vez de derrubar o parser inteiro.
    """
    with open(csv_path, encoding=encoding, newline="") as f:
        reader = csv.reader(f, delimiter=delimiter, quotechar='"')
        for row in reader:
            if len(row) < len(ESTABELECIMENTOS_COLUNAS):
                continue
            yield {coluna: _clean(valor) for coluna, valor in zip(ESTABELECIMENTOS_COLUNAS, row)}


def filtrar_semente_leads(
    csv_paths: list[Path],
    ufs: set[str] | None = None,
    regiao: str | None = None,
    apenas_ativos: bool = True,
    encoding: str = DEFAULT_ENCODING,
    delimiter: str = DEFAULT_DELIMITER,
) -> Iterator[EstabelecimentoRFB]:
    """
    Filtra um ou mais arquivos ESTABELECIMENTOS (a Receita particiona o
    arquivo completo em várias fatias) por CNAE de consultório médico +
    UF/região + situação cadastral, devolvendo um EstabelecimentoRFB por
    linha que bateu no filtro.

    `ufs` e `regiao` são combináveis (um estabelecimento passa se estiver
    numa UF de `ufs` OU na `regiao` informada); se nenhum dos dois for
    passado, não filtra por localização — só por CNAE (e situação, se
    `apenas_ativos`).
    """
    ufs_alvo: set[str] = {uf.upper() for uf in (ufs or set())}
    if regiao:
        ufs_alvo |= UFS_POR_REGIAO[regiao.lower()]

    for path in csv_paths:
        for linha in iter_estabelecimentos(path, encoding=encoding, delimiter=delimiter):
            if not _eh_cnae_do_grupo(linha["cnae_fiscal_principal"]):
                continue
            if apenas_ativos and not _eh_situacao_ativa(linha["situacao_cadastral"]):
                continue
            if ufs_alvo and linha["uf"].upper() not in ufs_alvo:
                continue

            yield EstabelecimentoRFB(
                cnpj=_montar_cnpj(linha["cnpj_basico"], linha["cnpj_ordem"], linha["cnpj_dv"]),
                nome_fantasia=linha["nome_fantasia"],
                situacao_cadastral=linha["situacao_cadastral"],
                cnae_fiscal_principal=linha["cnae_fiscal_principal"],
                data_inicio_atividade=linha["data_inicio_atividade"],
                municipio_codigo_rfb=linha["municipio"],
                uf=linha["uf"],
                identificador_matriz_filial=linha["identificador_matriz_filial"],
            )


def find_estabelecimentos_rfb_files(directory: str | Path) -> list[Path]:
    """
    Localiza os arquivos ESTABELECIMENTOS já baixados numa pasta — mesmo
    padrão de `find_pgfn_csv_files`. A Receita nomeia os arquivos tipo
    K3241.K03200Y0.D60411.ESTABELE (o número da fatia varia, "ESTABELE"
    não muda).
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        return []
    return sorted(dir_path.glob("*ESTABELE*"))


def iter_empresas(
    csv_path: Path,
    encoding: str = DEFAULT_ENCODING,
    delimiter: str = DEFAULT_DELIMITER,
) -> Iterator[dict[str, str]]:
    """
    Lê o arquivo EMPRESAS linha a linha (sem cabeçalho, sem carregar tudo
    na memória) — mesmo padrão de `iter_estabelecimentos`, devolvendo
    cada linha como dict nomeado por `EMPRESAS_COLUNAS`.
    """
    with open(csv_path, encoding=encoding, newline="") as f:
        reader = csv.reader(f, delimiter=delimiter, quotechar='"')
        for row in reader:
            if len(row) < len(EMPRESAS_COLUNAS):
                continue
            yield {coluna: _clean(valor) for coluna, valor in zip(EMPRESAS_COLUNAS, row)}


def build_razao_social_index(
    csv_paths: list[Path],
    cnpjs_basicos: set[str],
    encoding: str = DEFAULT_ENCODING,
    delimiter: str = DEFAULT_DELIMITER,
) -> dict[str, str]:
    """
    Constrói um índice `cnpj_basico -> razão social`, mas só pros
    `cnpjs_basicos` informados (normalmente os que já sobraram do filtro
    de `filtrar_semente_leads`) — o arquivo EMPRESAS completo tem ~60
    milhões de linhas, indexar tudo pra usar só um punhado seria
    desperdício (mesma lógica de "filtra primeiro" do resto do projeto).
    """
    alvo = set(cnpjs_basicos)
    indice: dict[str, str] = {}

    for path in csv_paths:
        for linha in iter_empresas(path, encoding=encoding, delimiter=delimiter):
            cnpj_basico = linha["cnpj_basico"]
            if cnpj_basico in alvo:
                indice[cnpj_basico] = linha["razao_social"]

    return indice


def enriquecer_com_razao_social(
    estabelecimentos: list[EstabelecimentoRFB],
    empresas_paths: list[Path],
    encoding: str = DEFAULT_ENCODING,
    delimiter: str = DEFAULT_DELIMITER,
) -> list[EstabelecimentoRFB]:
    """
    Junta a razão social (arquivo EMPRESAS) aos estabelecimentos já
    filtrados, pela raiz do CNPJ (`cnpj_basico`, 8 primeiros dígitos).

    CNPJs cujo `cnpj_basico` não aparecer em nenhum dos `empresas_paths`
    ficam com `razao_social` vazia — não quebra (pode acontecer com um
    arquivo EMPRESAS parcial, ex.: em teste; no arquivo real da Receita
    razão social é campo obrigatório, então isso não é esperado em
    produção com os arquivos completos do mesmo mês).
    """
    cnpjs_basicos = {e.cnpj[:8] for e in estabelecimentos}
    indice = build_razao_social_index(empresas_paths, cnpjs_basicos, encoding=encoding, delimiter=delimiter)

    return [
        replace(e, razao_social=indice.get(e.cnpj[:8], ""))
        for e in estabelecimentos
    ]


def find_empresas_rfb_files(directory: str | Path) -> list[Path]:
    """
    Localiza os arquivos EMPRESAS já baixados numa pasta — mesmo padrão
    de `find_estabelecimentos_rfb_files`. A Receita nomeia os arquivos
    tipo K3241.K03200Y0.D10612.EMPRECSV (o número da fatia varia,
    "EMPRECSV" não muda).
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        return []
    return sorted(dir_path.glob("*EMPRECSV*"))
