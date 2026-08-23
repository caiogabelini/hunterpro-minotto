"""
Parser dos Dados Abertos da PGFN (Dívida Ativa da União).
=============================================================

Fonte: dadosabertos.pgfn.gov.br — CSV trimestral, gratuito, sem login.
NÃO confundir com regularize.pgfn.gov.br (portal do contribuinte,
exige certificado digital — não serve pra prospecção).

O QUE ESTE MÓDULO FAZ:
  - Lê o(s) arquivo(s) baixado(s) manualmente/agendado (SIDA geral,
    FGTS, Previdenciário — a PGFN publica em arquivos separados),
    aceitando tanto o `.zip` direto do site quanto CSV já extraído
    (ver `find_pgfn_csv_files` / `iter_pgfn_records`)
  - Filtra: só pessoa jurídica (CNPJ), só inscrições ainda ATIVAS
    (remove "extinta por pagamento" e afins)
  - Indexa por CNPJ, contando quantas inscrições ativas cada empresa tem

O QUE ESTE MÓDULO **NÃO** FAZ (de propósito):
  - Não converte a contagem em pontos de score — isso depende da
    fórmula que ainda precisa ser confirmada com o Douglas (ver
    app/scoring/rules.py). Este módulo só entrega o dado bruto:
    "quantas inscrições ativas esse CNPJ tem". A conversão pra pontos
    é feita em outro lugar, assim que a fórmula for validada.
  - Não busca contato nenhum — a base da PGFN não tem e-mail/telefone,
    só identificação (CNPJ, nome) e dados da dívida.

⚠️ NÃO PRECISA DESCOMPACTAR (desde 23/08/2026):
Os `.zip` da PGFN (`Dados_abertos_Nao_Previdenciario.zip`,
`Dados_abertos_Previdenciario.zip`, `Dados_abertos_FGTS.zip`) são lidos
direto, em streaming, sem extrair nada em disco. Isso não é preferência de
estilo: o `Dados_abertos_Nao_Previdenciario.zip` de 202606 tem 1,3 GB
comprimido e ~9,0 GB descomprimidos (seis CSVs, o maior com 2,7 GB) —
extrair já estourou o disco disponível uma vez. Basta jogar o `.zip` em
`settings.PGFN_DADOS_ABERTOS_DIR`. Quem já tem os CSVs extraídos continua
funcionando igual; só não deixe os dois formatos do MESMO dado na pasta ao
mesmo tempo (ver a ressalva em `find_pgfn_csv_files`).

⚠️ SOBRE OS NOMES DE COLUNA:
Os nomes abaixo (`PGFN_COLUMNS`) eram, até 23/08/2026, um palpite baseado
na documentação pública mais confiável disponível (metodologia do projeto
CruzaGrafos/Abraji, que usa a mesma base). **Agora estão CONFIRMADOS**
contra o cabeçalho real de `arquivo_lai_SIDA_1_202606.csv`, dentro do
`Dados_abertos_Nao_Previdenciario.zip` baixado do site — os 9 nomes que
este módulo usa existem, escritos exatamente assim. (O arquivo real tem 13
colunas; as 4 que sobram — `TIPO_DEVEDOR`, `UF_DEVEDOR`,
`UNIDADE_RESPONSAVEL`, `TIPO_SITUACAO_INSCRICAO` — não são usadas aqui.)

⚠️ DUAS SUPOSIÇÕES SOBRE O **CONTEÚDO** ESTAVAM ERRADAS — as duas
corrigidas em 23/08/2026, depois de varrer o arquivo real inteiro
(45.553.971 linhas, os 6 membros do `.zip`, em streaming):

  1. `VALOR_CONSOLIDADO` vem no formato AMERICANO (`3165.44`), não no
     brasileiro (`1.234,56`) que `_parse_valor` assumia — todo valor saía
     100x maior. Ver a docstring de `_parse_valor`.
  2. `SITUACOES_QUITADAS` (a blacklist `"EXTINTA"` / `"CANCELADA"` / ...)
     não casava com **nenhuma** das 45,5 milhões de linhas: a PGFN só
     publica inscrição ATIVA nos dados abertos, e os 97 valores distintos
     de `SITUACAO_INSCRICAO` começam todos com `"ATIVA"`. Substituída por
     uma checagem positiva de prefixo — ver
     `_situacao_conta_como_divida_ativa`, que também documenta a decisão
     de negócio ainda em aberto sobre `TIPO_SITUACAO_INSCRICAO`.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterator

from app.services.arquivo_utils import iter_streams_csv

# ---------------------------------------------------------------------------
# Configuração — ajustar aqui se os nomes reais de coluna vierem diferentes
# ---------------------------------------------------------------------------
PGFN_COLUMNS = {
    "cnpj": "CPF_CNPJ",
    "tipo_pessoa": "TIPO_PESSOA",  # "PESSOA JURIDICA" | "PESSOA FISICA"
    "nome": "NOME_DEVEDOR",
    "numero_inscricao": "NUMERO_INSCRICAO",
    "situacao": "SITUACAO_INSCRICAO",
    "receita_principal": "RECEITA_PRINCIPAL",
    "data_inscricao": "DATA_INSCRICAO",
    "indicador_ajuizado": "INDICADOR_AJUIZADO",  # "SIM" | "NAO"
    "valor_consolidado": "VALOR_CONSOLIDADO",
}

# Encoding e delimitador mais comuns em arquivos .gov.br deste tipo.
# Ajustar se o arquivo real vier diferente (confirmar ao baixar).
DEFAULT_ENCODING = "latin-1"
DEFAULT_DELIMITER = ";"

# ---------------------------------------------------------------------------
# O que conta como "inscrição ainda ativa"
# ---------------------------------------------------------------------------
# ⚠️ REESCRITO EM 23/08/2026 A PARTIR DO ARQUIVO REAL. Até então isto era uma
# blacklist adivinhada -- `{"EXTINTA", "EXTINTA POR PAGAMENTO", "EXTINTA POR
# OUTROS MOTIVOS", "CANCELADA"}` -- e a varredura completa do
# `Dados_abertos_Nao_Previdenciario.zip` (202606, 45.553.971 linhas, os 6
# membros) mostrou que os quatro valores aparecem **ZERO vezes**. O filtro
# não excluía uma única linha: era um no-op que passava despercebido.
#
# O motivo é estrutural, e é a informação que faltava neste módulo: a PGFN
# **só publica inscrições ativas** nos dados abertos. Inscrição extinta,
# quitada ou cancelada simplesmente não entra no arquivo. Dos 97 valores
# distintos de `SITUACAO_INSCRICAO` medidos, **os 97 começam com "ATIVA"**
# ("ATIVA EM COBRANCA", "ATIVA AJUIZADA", "ATIVA A SER AJUIZADA", ...).
#
# Por isso o filtro virou uma checagem POSITIVA por prefixo em vez de uma
# blacklist de valores: com 97 grafias (e leis/programas novos aparecendo a
# cada trimestre), enumerar o que excluir é uma corrida perdida; enumerar o
# que aceitar é uma regra só. O sentido do erro também importa -- se um dia
# a PGFN passar a publicar linhas não-ATIVA, elas ficam de fora (menos
# CNPJs na pré-seleção paga), nunca dentro.
#
# ⚠️ Isto NÃO muda a contagem no arquivo de hoje: 100% das linhas passam nos
# dois filtros. A correção é de honestidade e de proteção futura, não um
# ajuste de volume -- ver o bullet de `TIPO_SITUACAO_INSCRICAO` abaixo pro
# número que mudaria de verdade.
SITUACAO_ATIVA_PREFIXO = "ATIVA"

# ⚠️ DECISÃO EM ABERTO, DE NEGÓCIO, NÃO TÉCNICA -- não implementar sem o
# Douglas. O arquivo tem uma segunda coluna, `TIPO_SITUACAO_INSCRICAO`, com
# só 5 valores, que classifica a situação (medido sobre as 28.312.053
# inscrições de pessoa jurídica):
#
#     Em cobrança                      22.390.784   79,09%
#     Benefício Fiscal                  5.808.595   20,52%
#     Garantia                             84.835    0,30%
#     Suspenso por decisão judicial        27.787    0,10%
#     Em negociação                            52    0,00%
#
# NENHUM desses cinco significa "quitada" -- todos são dívida ativa em
# algum estado. "Benefício Fiscal" é parcelamento/transação em curso (as
# situações "NEGOCIADA NO SISPAR", "PARCELADA LEI 12996/14"): a empresa
# deve e está pagando, não deixou de dever. Por isso ela continua contando.
#
# Mas é o único recorte que mexeria no volume de verdade: ignorar
# "Benefício Fiscal" cortaria 20,5% das inscrições de PJ. Se o Douglas
# entender que empresa já parcelada não é lead bom (ele não precisa do
# serviço que a Minotto venderia), é aqui que se implementa -- lendo essa
# coluna, que hoje o parser nem extrai. Enquanto ninguém decidir, contar
# tudo é o comportamento correto pro critério como está escrito
# ("inscrições ativas").
def _situacao_conta_como_divida_ativa(situacao: str) -> bool:
    """
    True se a situação da inscrição indica dívida ativa em aberto.

    `situacao` chega já normalizada por `_clean(...).upper()` em
    `_iter_records_de_stream`.
    """
    return situacao.startswith(SITUACAO_ATIVA_PREFIXO)


def _clean(value: str | None) -> str:
    """Remove aspas e espaços sobrando — mesmo padrão já usado nos
    outros parsers de arquivo público da Receita Federal."""
    if value is None:
        return ""
    return value.strip().strip('"').strip()


def normalize_cnpj(raw: str | None) -> str | None:
    """
    Extrai só os dígitos e valida que é um CNPJ (14 dígitos).
    Retorna None para CPF (11 dígitos) ou valores inválidos/vazios —
    a PGFN mistura pessoa física e jurídica no mesmo arquivo.
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 14:
        return None
    return digits


def _parse_valor(raw: str | None) -> float:
    """
    Converte VALOR_CONSOLIDADO para float. Retorna 0.0 se vazio ou
    não-numérico.

    ⚠️ O arquivo da PGFN usa formato AMERICANO: ponto como separador
    decimal e NENHUM separador de milhar ("3165.44"). Confirmado em
    23/08/2026 varrendo 2 milhões de linhas de
    `arquivo_lai_SIDA_1_202606.csv` — 100% dos valores no formato
    `<dígitos>.<dígitos>`, nenhuma vírgula, nenhum ponto de milhar.

    ⚠️ **Bug corrigido em 23/08/2026:** até então esta função assumia o
    formato brasileiro ('1.234,56') e removia TODOS os pontos antes de
    converter. Com o arquivo real, "3165.44" virava 316544.0 — todo valor
    100x maior. Afetava `PgfnDebtSummary.valor_total` →
    `Lead.divida_ativa_valor_total` (exibição no dossiê); a contagem de
    inscrições, que é o que alimenta o score e a pré-seleção, nunca
    dependeu deste parsing.

    A vírgula ainda é aceita como separador decimal (formato brasileiro),
    caso a PGFN mude o formato de publicação: presença de vírgula é
    desambiguação suficiente, já que o formato real não usa nenhuma. O
    que NÃO é suportado é o americano COM separador de milhar
    ("1,234.56") — esse seria lido como brasileiro. Não existe no arquivo
    real (nenhum separador de milhar aparece), e adivinhar entre os dois
    é justamente como o bug acima nasceu.
    """
    if not raw:
        return 0.0
    cleaned = _clean(raw)
    if not cleaned:
        return 0.0
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


@dataclass
class PgfnDebtRecord:
    """Uma linha do CSV, já normalizada."""

    cnpj: str
    nome_devedor: str
    numero_inscricao: str
    situacao: str
    receita_principal: str
    ajuizado: bool
    valor_consolidado: float


@dataclass
class PgfnDebtSummary:
    """Resumo consolidado por CNPJ — o que o resto do pipeline consome.

    `qtd_inscricoes_ativas` é o dado bruto que alimenta o critério de
    score (ver app/scoring/rules.py) — a conversão pra pontos NÃO
    acontece aqui.

    ⚠️ **Havia aqui um campo `situacoes: list[str]`, removido em
    23/08/2026.** Ele era preenchido a cada inscrição em
    `build_pgfn_index` e **nunca lido em lugar nenhum do projeto** --
    `save_index_to_redis` serializa só `qtd_inscricoes_ativas`,
    `valor_total` e `algum_ajuizado`. No arquivo real custava ~724 MB:
    6,4 milhões de objetos lista guardando 28,3 milhões de ponteiros.
    (O custo não eram as strings: só existem 97 valores distintos de
    situação no arquivo, então eram 28,3M referências repetidas a ~97
    objetos.) Se algum dia for preciso saber QUAIS situações um CNPJ
    tem, guardar um `set` dos 97 valores custa quase nada -- mas não
    recriar a lista completa por inscrição.
    """

    cnpj: str
    qtd_inscricoes_ativas: int
    valor_total: float
    algum_ajuizado: bool = False


def _iter_records_de_stream(
    stream: IO[str],
    rotulo: str,
    delimiter: str,
) -> Iterator[PgfnDebtRecord]:
    """
    Percorre UM CSV já aberto (arquivo extraído ou membro de dentro do
    .zip) e produz um PgfnDebtRecord por linha válida.

    `rotulo` só serve pra mensagem de erro: com o .zip da PGFN tendo seis
    membros, dizer "faltou coluna X" apontando o .zip de 1,3 GB não ajuda
    ninguém a achar qual parte veio quebrada.
    """
    cols = PGFN_COLUMNS
    reader = csv.DictReader(stream, delimiter=delimiter)

    missing = [c for c in cols.values() if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(
            f"Colunas esperadas não encontradas em {rotulo}: {missing}. "
            f"Colunas presentes: {reader.fieldnames}. "
            "Confira o dicionário de campos oficial da PGFN e ajuste PGFN_COLUMNS."
        )

    for row in reader:
        cnpj = normalize_cnpj(row.get(cols["cnpj"]))
        if cnpj is None:
            continue  # pessoa física ou CNPJ inválido — pula

        yield PgfnDebtRecord(
            cnpj=cnpj,
            nome_devedor=_clean(row.get(cols["nome"])),
            numero_inscricao=_clean(row.get(cols["numero_inscricao"])),
            situacao=_clean(row.get(cols["situacao"])).upper(),
            receita_principal=_clean(row.get(cols["receita_principal"])),
            ajuizado=_clean(row.get(cols["indicador_ajuizado"])).upper() == "SIM",
            valor_consolidado=_parse_valor(row.get(cols["valor_consolidado"])),
        )


def iter_pgfn_records(csv_path: Path, encoding: str = DEFAULT_ENCODING, delimiter: str = DEFAULT_DELIMITER) -> Iterator[PgfnDebtRecord]:
    """
    Lê um arquivo da PGFN e produz um PgfnDebtRecord por linha válida
    (pessoa jurídica, CNPJ parseável).

    Aceita os DOIS formatos, decidindo pelo sufixo do arquivo:

      - `.zip` baixado direto do site (`Dados_abertos_FGTS.zip` e afins) —
        percorre TODOS os CSVs de dentro, um depois do outro, sem extrair
        nada em disco;
      - CSV já extraído (`arquivo_lai_SIDA_1_202606.csv`) — comportamento
        de sempre, inalterado.

    ⚠️ **Todos** os membros, não o primeiro. O .zip da PGFN vem fatiado
    (`arquivo_lai_SIDA_1_202606.csv` .. `_6_`, confirmado no arquivo real
    em 23/08/2026) e cada fatia é uma parte diferente da mesma base — ler
    só a primeira devolveria ~1/6 da dívida ativa do país sem nenhum sinal
    de que faltou coisa. É o oposto do .zip da Receita Federal, que tem
    exatamente um membro e trata "vários" como erro (ver
    `app/services/arquivo_utils.py`).

    Cada membro tem o próprio cabeçalho, então a validação de colunas roda
    por membro — um .zip com uma fatia fora do layout falha apontando o
    nome da fatia, não o do .zip.

    Não carrega tudo em memória de uma vez — nem o CSV extraído nem o
    conteúdo do .zip. O arquivo trimestral é grande (o
    `Dados_abertos_Nao_Previdenciario.zip` de 202606: 1,3 GB comprimido,
    ~9,0 GB descomprimidos, ~2,7 GB só no maior membro).
    """
    for rotulo, stream in iter_streams_csv(Path(csv_path), encoding=encoding):
        yield from _iter_records_de_stream(stream, rotulo, delimiter)


def build_pgfn_index(csv_paths: list[Path], encoding: str = DEFAULT_ENCODING, delimiter: str = DEFAULT_DELIMITER) -> dict[str, PgfnDebtSummary]:
    """
    Constrói o índice CNPJ → resumo de dívida, a partir de um ou mais
    arquivos CSV (a PGFN publica SIDA geral, FGTS e Previdenciário
    separados — passar todos os que forem relevantes).

    Só conta inscrições que passam em `_situacao_conta_como_divida_ativa`
    (ver o bloco de comentários daquela função).
    """
    index: dict[str, PgfnDebtSummary] = {}

    for path in csv_paths:
        for record in iter_pgfn_records(path, encoding=encoding, delimiter=delimiter):
            if not _situacao_conta_como_divida_ativa(record.situacao):
                continue  # não é dívida ativa em aberto — não conta

            summary = index.get(record.cnpj)
            if summary is None:
                summary = PgfnDebtSummary(cnpj=record.cnpj, qtd_inscricoes_ativas=0, valor_total=0.0)
                index[record.cnpj] = summary

            summary.qtd_inscricoes_ativas += 1
            summary.valor_total += record.valor_consolidado
            if record.ajuizado:
                summary.algum_ajuizado = True

    return index


def get_debt_summary(index: dict[str, PgfnDebtSummary], cnpj: str) -> PgfnDebtSummary | None:
    """Consulta o índice já construído. CNPJ sem dívida ativa retorna None
    (equivalente a zero inscrições — tratar como sinal positivo no score)."""
    normalized = normalize_cnpj(cnpj)
    if normalized is None:
        return None
    return index.get(normalized)


# Prefixo dos .zip publicados pela PGFN em dadosabertos.pgfn.gov.br. Os três
# arquivos de hoje (confirmados em 23/08/2026):
#
#   Dados_abertos_Nao_Previdenciario.zip   (SIDA geral)
#   Dados_abertos_Previdenciario.zip
#   Dados_abertos_FGTS.zip
#
# A comparação é por PREFIXO + sufixo `.zip`, não pelos três nomes exatos, de
# propósito: a PGFN já mudou nome de arquivo antes, e é comum o navegador
# salvar como `Dados_abertos_FGTS (1).zip` ou o download vir com o trimestre
# no nome. Casar pelo prefixo aceita todas essas variantes; casar pelos três
# literais devolveria "sem arquivos" — que, nesse fluxo, é indistinguível de
# "a pasta está vazia".
PGFN_ZIP_PREFIXO = "dados_abertos"


def find_pgfn_csv_files(directory: str | Path) -> list[Path]:
    """
    Localiza os arquivos da PGFN já baixados numa pasta (SIDA geral, FGTS,
    Previdenciário — a PGFN publica separados), nos DOIS formatos:

      - `.zip` direto do site: `Dados_abertos_Nao_Previdenciario.zip`,
        `Dados_abertos_Previdenciario.zip`, `Dados_abertos_FGTS.zip`
        (ver `PGFN_ZIP_PREFIXO`) — **não precisa descompactar**;
        `iter_pgfn_records` lê os CSVs de dentro em streaming.
      - CSV já extraído: qualquer `*.csv` (ex.:
        `arquivo_lai_SIDA_1_202606.csv`), pra quem já tinha descompactado.

    Sem diferenciar maiúscula/minúscula, mesma decisão de
    `receita_federal_bulk._localizar_arquivos_rfb` e pelo mesmo motivo: um
    glob case-sensitive já ignorou os .zip da Receita em silêncio uma vez,
    e o sintoma foi uma busca "concluída com sucesso" trazendo zero leads.

    ⚠️ Se a pasta tiver os dois formatos do mesmo dado (o .zip E os CSVs
    extraídos de dentro dele), os dois entram na lista e as inscrições
    seriam contadas duas vezes. Diferente da semente da Receita, aqui NÃO
    dá pra deduplicar por CNPJ depois: o mesmo CNPJ legitimamente aparece
    em várias inscrições, então "linha repetida" e "dívida a mais" são
    indistinguíveis. Depois de ler direto do .zip, apague os CSVs extraídos
    (ou o .zip) — é o único jeito de garantir a contagem certa.

    O download em si (via navegador ou script agendado) fica fora do
    escopo deste módulo por enquanto — isso lê o que já estiver na
    pasta configurada em `settings.PGFN_DADOS_ABERTOS_DIR`.
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        return []

    encontrados: list[Path] = []
    for caminho in dir_path.iterdir():
        if not caminho.is_file():
            continue
        nome = caminho.name.lower()
        eh_csv_extraido = nome.endswith(".csv")
        eh_zip_da_pgfn = nome.startswith(PGFN_ZIP_PREFIXO) and nome.endswith(".zip")
        if eh_csv_extraido or eh_zip_da_pgfn:
            encontrados.append(caminho)
    return sorted(encontrados)


# ---------------------------------------------------------------------------
# Cache em Redis — separa a construção do índice (pesada, roda 1x por
# trimestre quando o arquivo da PGFN atualiza) da consulta por lead
# (barata, roda por CNPJ durante o enriquecimento).
#
# Sem essa separação, cada lead reprocessaria o CSV inteiro — inviável
# com um arquivo de milhões de linhas.
# ---------------------------------------------------------------------------
import json

REDIS_KEY_PREFIX = "pgfn:cnpj:"
REDIS_KEY_UPDATED_AT = "pgfn:index_updated_at"


def _redis_key(cnpj: str) -> str:
    return f"{REDIS_KEY_PREFIX}{cnpj}"


# Quantos SET por pipeline na ESCRITA do índice. Espelha o
# `LOTE_LOOKUP_PADRAO` da leitura, pelo mesmo motivo e com o mesmo valor.
#
# ⚠️ Medido em 23/08/2026 contra o arquivo real: empilhar os 6.432.808
# comandos de uma vez (o comportamento até então) custava **713 MB** de
# buffer -- em cima dos ~4 GB do índice, que continuam vivos. Era o que
# levava o processo de 4,03 GB ao pico de 4,74 GB e o fazia ser morto por
# OOM numa VPS com 4,2 GB livres.
#
# O `pipeline()` do redis-py acumula TODOS os comandos em
# `command_stack` até o `execute()`; nada é enviado antes. Executar a
# cada lote esvazia esse buffer e mantém o pico previsível,
# independentemente do tamanho do índice.
LOTE_ESCRITA_PADRAO = 5_000


def save_index_to_redis(
    index: dict[str, PgfnDebtSummary],
    redis_client,
    tamanho_lote: int = LOTE_ESCRITA_PADRAO,
) -> int:
    """
    Persiste o índice já construído no Redis, um registro por CNPJ.
    Retorna quantos CNPJs foram salvos.

    Escreve em LOTES de `tamanho_lote` comandos (ver
    `LOTE_ESCRITA_PADRAO`) -- o mesmo padrão que `lookup_debts_from_redis`
    já usava na leitura, por motivo de escala, não de estilo.

    ⚠️ **Não é atômico, e nunca foi.** Um pipeline do Redis sem `MULTI`
    já não dava atomicidade nem antes; o que muda é a janela: agora um
    erro no meio deixa parte do índice atualizado e parte não. Na prática
    isso não regride nada, porque `REDIS_KEY_UPDATED_AT` só é gravada
    DEPOIS de todos os lotes -- então uma execução interrompida deixa o
    carimbo antigo, e as chaves são sobrescritas por CNPJ na execução
    seguinte. `_indice_pgfn_populado` (a trava de custo em
    `executar_busca_mensal`) checa justamente esse carimbo.

    `redis_client` é injetado (não importa `redis` aqui direto) pra
    manter este módulo testável sem depender de um Redis real rodando.
    """
    import time

    pipe = redis_client.pipeline()
    pendentes = 0
    for cnpj, summary in index.items():
        payload = json.dumps({
            "qtd_inscricoes_ativas": summary.qtd_inscricoes_ativas,
            "valor_total": summary.valor_total,
            "algum_ajuizado": summary.algum_ajuizado,
        })
        pipe.set(_redis_key(cnpj), payload)
        pendentes += 1
        if pendentes >= tamanho_lote:
            pipe.execute()
            pendentes = 0

    # O carimbo vai no último lote, sempre executado -- mesmo que o
    # índice esteja vazio ou que o total seja múltiplo exato do lote
    # (caso em que `pendentes` acabou de zerar e o pipeline está limpo).
    pipe.set(REDIS_KEY_UPDATED_AT, str(int(time.time())))
    pipe.execute()
    return len(index)


# Quantas chaves por MGET. O Redis não impõe limite prático, mas um MGET
# de 300 mil chaves monta uma lista gigante de uma vez só na memória do
# cliente E do servidor -- fatiar mantém o pico de memória previsível sem
# perder quase nada de throughput.
LOTE_LOOKUP_PADRAO = 5_000


def lookup_debts_from_redis(
    cnpjs: list[str],
    redis_client,
    tamanho_lote: int = LOTE_LOOKUP_PADRAO,
) -> dict[str, PgfnDebtSummary]:
    """
    Versão em LOTE de `lookup_debt_from_redis` -- usada pela pré-seleção
    de candidatos (app/scoring/pre_selecao.py), que precisa consultar o
    universo inteiro da semente, não um lead por vez.

    Existe por motivo de escala, não de estilo: a semente nacional de
    CNAE 8630 tem ordem de centenas de milhares de CNPJs, e um `GET` por
    CNPJ seria um round-trip de rede por item. Com `MGET` em lotes, o
    mesmo trabalho vira algumas dezenas de round-trips.

    Devolve um dict {cnpj_normalizado: PgfnDebtSummary} contendo APENAS
    os CNPJs que estão de fato em dívida ativa. Ausência da chave = sem
    dívida (ou índice ainda não populado) -- mesma semântica do `None` da
    versão unitária, só que quem chama usa `.get(cnpj)` em vez de checar
    o retorno.

    CNPJs inválidos (que `normalize_cnpj` rejeita) são ignorados em
    silêncio, e uma linha de índice corrompida (JSON inválido ou sem as
    chaves esperadas) é pulada em vez de derrubar o lote inteiro -- a
    pré-seleção roda sobre o universo todo e não deve morrer por causa de
    uma entrada ruim.
    """
    normalizados: list[str] = []
    for cnpj in cnpjs:
        normalizado = normalize_cnpj(cnpj)
        if normalizado is not None:
            normalizados.append(normalizado)

    resultado: dict[str, PgfnDebtSummary] = {}
    for inicio in range(0, len(normalizados), tamanho_lote):
        lote = normalizados[inicio : inicio + tamanho_lote]
        if not lote:
            continue
        brutos = redis_client.mget([_redis_key(c) for c in lote])
        for cnpj_normalizado, bruto in zip(lote, brutos):
            if bruto is None:
                continue
            try:
                data = json.loads(bruto)
                resultado[cnpj_normalizado] = PgfnDebtSummary(
                    cnpj=cnpj_normalizado,
                    qtd_inscricoes_ativas=data["qtd_inscricoes_ativas"],
                    valor_total=data["valor_total"],
                    algum_ajuizado=data["algum_ajuizado"],
                )
            except (ValueError, TypeError, KeyError):
                continue

    return resultado


def lookup_debt_from_redis(cnpj: str, redis_client) -> PgfnDebtSummary | None:
    """
    Consulta rápida por CNPJ — é isso que a task `enrich_pgfn_divida_ativa`
    usa por lead, sem tocar no CSV.
    """
    normalized = normalize_cnpj(cnpj)
    if normalized is None:
        return None

    raw = redis_client.get(_redis_key(normalized))
    if raw is None:
        return None  # CNPJ não está em dívida ativa (ou índice ainda não populado)

    data = json.loads(raw)
    return PgfnDebtSummary(
        cnpj=normalized,
        qtd_inscricoes_ativas=data["qtd_inscricoes_ativas"],
        valor_total=data["valor_total"],
        algum_ajuizado=data["algum_ajuizado"],
    )
