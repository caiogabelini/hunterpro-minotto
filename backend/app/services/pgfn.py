"""
Parser dos Dados Abertos da PGFN (Dívida Ativa da União).
=============================================================

Fonte: dadosabertos.pgfn.gov.br — CSV trimestral, gratuito, sem login.
NÃO confundir com regularize.pgfn.gov.br (portal do contribuinte,
exige certificado digital — não serve pra prospecção).

O QUE ESTE MÓDULO FAZ:
  - Lê o(s) arquivo(s) CSV baixado(s) manualmente/agendado (SIDA geral,
    FGTS, Previdenciário — a PGFN publica em arquivos separados)
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

⚠️ SOBRE OS NOMES DE COLUNA:
Os nomes abaixo (`PGFN_COLUMNS`) são baseados na documentação pública
mais confiável disponível (metodologia do projeto CruzaGrafos/Abraji,
que usa a mesma base). Ao baixar o arquivo real, confira contra o
"dicionário de campos" oficial da PGFN e ajuste `PGFN_COLUMNS` se os
nomes vierem diferentes — é só trocar aqui, o resto do módulo não
precisa mudar.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

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

# Situações que significam "não é mais uma dívida ativa de verdade" —
# excluídas da contagem. Lista conservadora; revisar contra os valores
# reais do arquivo (o campo costuma ter variações de grafia).
SITUACOES_QUITADAS = {
    "EXTINTA",
    "EXTINTA POR PAGAMENTO",
    "EXTINTA POR OUTROS MOTIVOS",
    "CANCELADA",
}


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
    """Converte valor no formato brasileiro ('1.234,56') para float.
    Retorna 0.0 se vazio ou não-numérico."""
    if not raw:
        return 0.0
    cleaned = _clean(raw).replace(".", "").replace(",", ".")
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
    """

    cnpj: str
    qtd_inscricoes_ativas: int
    valor_total: float
    situacoes: list[str] = field(default_factory=list)
    algum_ajuizado: bool = False


def iter_pgfn_records(csv_path: Path, encoding: str = DEFAULT_ENCODING, delimiter: str = DEFAULT_DELIMITER) -> Iterator[PgfnDebtRecord]:
    """
    Lê um arquivo CSV da PGFN e produz um PgfnDebtRecord por linha
    válida (pessoa jurídica, CNPJ parseável).

    Não carrega tudo em memória de uma vez — o arquivo trimestral é
    grande (mesma lição já registrada no projeto SR: "arquivo Simples
    tem milhões de linhas, não limitar a leitura").
    """
    cols = PGFN_COLUMNS

    with open(csv_path, encoding=encoding, newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)

        missing = [c for c in cols.values() if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(
                f"Colunas esperadas não encontradas em {csv_path.name}: {missing}. "
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


def build_pgfn_index(csv_paths: list[Path], encoding: str = DEFAULT_ENCODING, delimiter: str = DEFAULT_DELIMITER) -> dict[str, PgfnDebtSummary]:
    """
    Constrói o índice CNPJ → resumo de dívida, a partir de um ou mais
    arquivos CSV (a PGFN publica SIDA geral, FGTS e Previdenciário
    separados — passar todos os que forem relevantes).

    Só conta inscrições cuja situação NÃO está em SITUACOES_QUITADAS.
    """
    index: dict[str, PgfnDebtSummary] = {}

    for path in csv_paths:
        for record in iter_pgfn_records(path, encoding=encoding, delimiter=delimiter):
            if record.situacao in SITUACOES_QUITADAS:
                continue  # já foi paga/extinta — não conta como dívida ativa

            summary = index.get(record.cnpj)
            if summary is None:
                summary = PgfnDebtSummary(cnpj=record.cnpj, qtd_inscricoes_ativas=0, valor_total=0.0)
                index[record.cnpj] = summary

            summary.qtd_inscricoes_ativas += 1
            summary.valor_total += record.valor_consolidado
            summary.situacoes.append(record.situacao)
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


def find_pgfn_csv_files(directory: str | Path) -> list[Path]:
    """
    Localiza os CSVs da PGFN já baixados numa pasta (SIDA geral, FGTS,
    Previdenciário — a PGFN publica separados).

    O download em si (via navegador ou script agendado) fica fora do
    escopo deste módulo por enquanto — isso lê o que já estiver na
    pasta configurada em `settings.PGFN_DADOS_ABERTOS_DIR`.
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        return []
    return sorted(dir_path.glob("*.csv"))


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


def save_index_to_redis(index: dict[str, PgfnDebtSummary], redis_client) -> int:
    """
    Persiste o índice já construído no Redis, um registro por CNPJ.
    Retorna quantos CNPJs foram salvos.

    `redis_client` é injetado (não importa `redis` aqui direto) pra
    manter este módulo testável sem depender de um Redis real rodando.
    """
    import time

    pipe = redis_client.pipeline()
    for cnpj, summary in index.items():
        payload = json.dumps({
            "qtd_inscricoes_ativas": summary.qtd_inscricoes_ativas,
            "valor_total": summary.valor_total,
            "algum_ajuizado": summary.algum_ajuizado,
        })
        pipe.set(_redis_key(cnpj), payload)
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
