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
import logging
import re
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator

from app.services.arquivo_utils import ArquivoZipInvalidoError, abrir_csv_unico

logger = logging.getLogger(__name__)


# `ArquivoZipInvalidoError` e a leitura em streaming de .zip nasceram aqui e
# foram extraídas pra app/services/arquivo_utils.py em 23/08/2026, quando
# app/services/pgfn.py passou a precisar da mesma coisa (ver a docstring
# daquele módulo sobre por que os dois formatos de .zip têm funções
# separadas). O re-export mantém os imports existentes funcionando -- vários
# chamadores e testes importam `ArquivoZipInvalidoError` daqui (o import no
# topo deste arquivo é o que mantém esse nome disponível).


@contextmanager
def _abrir_csv(path: Path, encoding: str = None, delimiter: str = None):
    """
    Abre um arquivo de dados abertos da Receita como stream de TEXTO,
    aceitando tanto o `.zip` baixado direto do site quanto o CSV já
    extraído. Decide pelo sufixo do arquivo.

    Fininho por cima de `arquivo_utils.abrir_csv_unico` -- todo o
    comportamento (streaming nos dois caminhos, `ArquivoZipInvalidoError`
    quando o .zip não tem exatamente um membro) mora lá agora. Continua
    existindo aqui pra fixar o encoding padrão deste módulo e por causa do
    `delimiter`:

    `delimiter` é aceito e ignorado de propósito -- mantém a assinatura
    simétrica à das funções `iter_*` que chamam este helper, pra que
    ninguém precise lembrar quais parâmetros repassar.
    """
    with abrir_csv_unico(path, encoding=encoding or DEFAULT_ENCODING) as f:
        yield f


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
    with _abrir_csv(csv_path, encoding=encoding) as f:
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


def _localizar_arquivos_rfb(directory: str | Path, marcador_csv: str, prefixo_zip: str) -> list[Path]:
    """
    Localiza os arquivos de um tipo de dado da Receita numa pasta,
    aceitando os DOIS formatos:

      - CSV já extraído -- casa por marcador no nome
        (ex.: "ESTABELE" em `K3241.K03200Y0.D50308.ESTABELE`);
      - `.zip` baixado direto do site -- casa por prefixo + sufixo
        (ex.: `Estabelecimentos0.zip` ... `Estabelecimentos9.zip`).

    Comparação sem diferenciar maiúscula/minúscula. Isso importa: o
    marcador dos CSVs é MAIÚSCULO (`ESTABELE`) e o nome do .zip é
    capitalizado (`Estabelecimentos0.zip`), então um glob case-sensitive
    -- o comportamento até 23/08/2026 -- ignorava os .zip em silêncio, e
    quem esquecesse de descompactar via a busca "concluir com sucesso"
    trazendo zero leads.

    ⚠️ Se a pasta tiver os dois formatos do mesmo dado, os dois vêm na
    lista. A deduplicação acontece por CNPJ em `gerar_semente_leads`
    (app/workers/celery_app.py), não aqui: correlacionar
    `Estabelecimentos0.zip` com `K3241.K03200Y0.D50308.ESTABELE` pelo
    nome não é confiável, e escolher um formato "vencedor" descartaria
    silenciosamente fatias de quem extraiu só parte dos .zip.
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        return []

    marcador = marcador_csv.lower()
    prefixo = prefixo_zip.lower()
    encontrados: list[Path] = []
    for caminho in dir_path.iterdir():
        if not caminho.is_file():
            continue
        nome = caminho.name.lower()
        eh_csv_extraido = marcador in nome and not nome.endswith(".zip")
        eh_zip_da_receita = nome.startswith(prefixo) and nome.endswith(".zip")
        if eh_csv_extraido or eh_zip_da_receita:
            encontrados.append(caminho)
    return sorted(encontrados)


def find_estabelecimentos_rfb_files(directory: str | Path) -> list[Path]:
    """
    Localiza os arquivos ESTABELECIMENTOS já baixados numa pasta, nos
    dois formatos aceitos (ver `_localizar_arquivos_rfb`):

      - `.zip` direto do site: `Estabelecimentos0.zip` .. `Estabelecimentos9.zip`
      - CSV extraído: `K3241.K03200Y0.D60411.ESTABELE` (o número da fatia
        varia, "ESTABELE" não muda)
    """
    return _localizar_arquivos_rfb(directory, marcador_csv="ESTABELE", prefixo_zip="Estabelecimentos")


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
    with _abrir_csv(csv_path, encoding=encoding) as f:
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
    Localiza os arquivos EMPRESAS já baixados numa pasta, nos dois
    formatos aceitos (ver `_localizar_arquivos_rfb`):

      - `.zip` direto do site: `Empresas0.zip` .. `Empresas9.zip`
      - CSV extraído: `K3241.K03200Y0.D10612.EMPRECSV`
    """
    return _localizar_arquivos_rfb(directory, marcador_csv="EMPRECSV", prefixo_zip="Empresas")


# ---------------------------------------------------------------------------
# Tabela de domínio MUNICÍPIOS (código RFB -> nome)
# ---------------------------------------------------------------------------
#
# ⚠️ LAYOUT NÃO CONFIRMADO CONTRA O PDF OFICIAL DA RECEITA — diferente de
# ESTABELECIMENTOS_COLUNAS e EMPRESAS_COLUNAS, que foram conferidos linha
# a linha contra `gov.br/receitafederal/dados/cnpj-metadados.pdf` em
# 19/08/2026. Este ambiente não tem acesso de rede pra abrir o PDF, então
# as 2 colunas abaixo são a expectativa documentada publicamente pras
# tabelas de domínio da RFB (código; descrição), seguindo a MESMA
# convenção dos outros dois arquivos do lote (sem cabeçalho, `;`, aspas,
# latin-1). Mesmo tratamento que `PGFN_COLUMNS` em app/services/pgfn.py:
# se o arquivo real vier diferente, ajuste ESTAS CONSTANTES, não a lógica
# de parsing espalhada.
#
# O modo de falha é suave de propósito (ver `build_municipios_index`): se
# o arquivo não existir ou o layout estiver errado, o índice sai vazio, a
# Zona Franca não é resolvida pra ninguém e a pré-seleção passa a ranquear
# só por PGFN. Degrada, não quebra — mas `executar_busca_mensal` registra
# isso explicitamente no resultado da busca, pra não degradar em silêncio.
MUNICIPIOS_COLUNAS = [
    "codigo",
    "descricao",
]


def iter_municipios(
    csv_path: Path,
    encoding: str = DEFAULT_ENCODING,
    delimiter: str = DEFAULT_DELIMITER,
) -> Iterator[dict[str, str]]:
    """
    Lê a tabela de domínio de municípios linha a linha — mesmo padrão de
    `iter_estabelecimentos`/`iter_empresas`. Linhas com menos colunas que
    o layout esperado são puladas.
    """
    with _abrir_csv(csv_path, encoding=encoding) as f:
        reader = csv.reader(f, delimiter=delimiter, quotechar='"')
        for row in reader:
            if len(row) < len(MUNICIPIOS_COLUNAS):
                continue
            yield {coluna: _clean(valor) for coluna, valor in zip(MUNICIPIOS_COLUNAS, row)}


def build_municipios_index(
    csv_paths: list[Path],
    encoding: str = DEFAULT_ENCODING,
    delimiter: str = DEFAULT_DELIMITER,
) -> dict[str, str]:
    """
    Monta o índice {código RFB do município: nome do município}.

    Este é o elo que faltava pra Zona Franca entrar na PRÉ-seleção: o
    arquivo ESTABELECIMENTOS traz `municipio` como código interno da
    Receita (NÃO é código IBGE — ver o aviso no topo deste módulo), mas
    `app/scoring/zona_franca.py` compara por NOME do município. Sem esta
    tabela, o único jeito de saber o nome seria uma chamada por CNPJ à
    BrasilAPI (`enrich_receita_federal`), que é justamente a etapa cara
    que a pré-seleção existe pra evitar.

    Carrega tudo na memória de propósito, ao contrário dos outros dois
    parsers deste módulo: são ~5.570 municípios (algumas centenas de KB),
    não os ~60 milhões de linhas de ESTABELECIMENTOS/EMPRESAS.

    Nunca levanta exceção: um arquivo ilegível (ausente, encoding errado,
    layout diferente) é pulado e o índice sai sem ele. Ver o aviso de
    layout não confirmado acima.
    """
    indice: dict[str, str] = {}
    for path in csv_paths:
        try:
            for linha in iter_municipios(path, encoding=encoding, delimiter=delimiter):
                codigo = linha["codigo"].strip()
                descricao = linha["descricao"].strip()
                if codigo and descricao:
                    indice[codigo] = descricao
        except (OSError, UnicodeDecodeError, csv.Error, zipfile.BadZipFile, ArquivoZipInvalidoError):
            logger.exception("Falha lendo a tabela de municípios da RFB em %s -- ignorando esse arquivo.", path)
            continue
    return indice


def find_municipios_rfb_files(directory: str | Path) -> list[Path]:
    """
    Localiza a tabela de domínio de municípios já baixada, nos dois
    formatos aceitos (ver `_localizar_arquivos_rfb`):

      - `.zip` direto do site: `Municipios.zip`
      - CSV extraído: `F.K03200$Z.D<data>.MUNICCSV`
    """
    return _localizar_arquivos_rfb(directory, marcador_csv="MUNICCSV", prefixo_zip="Municipios")
