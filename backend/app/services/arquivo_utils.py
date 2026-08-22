"""
Leitura de arquivos grandes de dados abertos: `.zip` ou CSV já extraído.
================================================================================

Extraído em 23/08/2026 de `app/services/receita_federal_bulk.py`, onde a
lógica nasceu (helper `_abrir_csv`), quando `app/services/pgfn.py` passou a
precisar da mesma coisa. Dois módulos precisando do mesmo comportamento é o
ponto em que duplicar sai mais caro do que compartilhar — principalmente
porque o que está em jogo aqui não é estilo, é a garantia de STREAMING: se
alguém reescrever "na mão" no segundo módulo e usar `ZipFile.read()` em vez
de `ZipFile.open()`, o arquivo inteiro entra na memória e ninguém percebe
até a produção morrer com um CSV de 2,7 GB.

POR QUE LER DE DENTRO DO .ZIP:
Descompactar antes exige ter o `.zip` E o CSV extraído ao mesmo tempo em
disco. No caso da PGFN isso é proibitivo: `Dados_abertos_Nao_Previdenciario.zip`
tem 1,3 GB comprimido e ~9,0 GB descomprimidos (medido em 23/08/2026 no
arquivo real de 202606) — extrair já estourou o disco disponível uma vez.
Lendo direto de dentro, o pico de disco é só o do `.zip`.

OS DOIS FORMATOS DE .ZIP QUE ESTE MÓDULO ATENDE (e por que são funções
separadas em vez de uma só com um parâmetro):

  - **Receita Federal** — exatamente UM membro por `.zip`
    (`Estabelecimentos0.zip` → `K3241.K03200Y0.D50308.ESTABELE`). Vários
    membros significa que alguém montou o zip à mão, e adivinhar qual usar
    processaria os dados errados em silêncio → `abrir_csv_unico`, que
    levanta `ArquivoZipInvalidoError` em vez de assumir o primeiro.
  - **PGFN** — VÁRIOS membros por `.zip`, todos partes do mesmo dado
    (`Dados_abertos_Nao_Previdenciario.zip` → `arquivo_lai_SIDA_1_202606.csv`
    .. `arquivo_lai_SIDA_6_202606.csv`, confirmado no arquivo real em
    23/08/2026). Aqui ler só o primeiro é que seria o erro silencioso —
    daria ~1/6 da dívida ativa do país sem nenhum sinal de que faltou
    coisa → `iter_streams_csv`, que percorre TODOS.

Ou seja: "mais de um membro" é erro num caso e é o normal no outro. Um
parâmetro booleano escondendo essa diferença tornaria fácil chamar a
função errada; duas funções com nomes que dizem o que esperam, não.
"""
from __future__ import annotations

import io
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator

SUFIXO_ZIP = ".zip"


class ArquivoZipInvalidoError(ValueError):
    """
    O `.zip` não tem os membros que o formato esperava (nenhum, ou mais de
    um onde só pode haver um).

    Herda de `ValueError` pra continuar sendo pego por quem captura exceções
    genéricas (ex.: `_rodar_etapa` no pipeline, o try/except de
    `executar_busca_mensal`), mas com tipo próprio pra quem quiser tratar
    especificamente.
    """


def eh_zip(path: Path) -> bool:
    """Decide pelo sufixo, não pelo conteúdo — os dois formatos aceitos
    (`.zip` e CSV extraído) sempre chegam com o nome original."""
    return Path(path).suffix.lower() == SUFIXO_ZIP


def _membros_de_dados(arquivo_zip: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Membros que são arquivo de verdade (descarta entradas de diretório)."""
    return [info for info in arquivo_zip.infolist() if not info.is_dir()]


@contextmanager
def abrir_csv_unico(path: Path, encoding: str) -> Iterator[IO[str]]:
    """
    Abre um arquivo de dados como stream de TEXTO, aceitando tanto o `.zip`
    de UM membro quanto o CSV já extraído. Formato da Receita Federal.

    Os dois caminhos são igualmente STREAMING — em nenhum dos dois o
    conteúdo inteiro entra na memória. `zipfile.ZipFile.open()` devolve um
    stream binário descomprimido sob demanda, e o `io.TextIOWrapper`
    decodifica em blocos; medido em 400 mil linhas de um CSV de 53 MB, o
    pico de memória ficou em 0,15 MB (não proporcional ao arquivo).

    ⚠️ Um `.zip` com qualquer número de membros diferente de 1 levanta
    `ArquivoZipInvalidoError` — ver a docstring do módulo sobre por que
    assumir o primeiro seria pior do que falhar.
    """
    path = Path(path)

    if not eh_zip(path):
        with open(path, encoding=encoding, newline="") as f:
            yield f
        return

    with zipfile.ZipFile(path) as arquivo_zip:
        membros = _membros_de_dados(arquivo_zip)
        if len(membros) != 1:
            nomes = [info.filename for info in membros]
            raise ArquivoZipInvalidoError(
                f"{path.name} tem {len(membros)} arquivos dentro (esperado exatamente 1): {nomes}. "
                "Os arquivos de dados abertos da Receita vêm com um único membro por .zip -- "
                "extraia o que você quer usar e coloque o CSV direto na pasta."
            )
        with arquivo_zip.open(membros[0]) as bruto:
            with io.TextIOWrapper(bruto, encoding=encoding, newline="") as f:
                yield f


def iter_streams_csv(
    path: Path,
    encoding: str,
    sufixo_membro: str = ".csv",
) -> Iterator[tuple[str, IO[str]]]:
    """
    Percorre TODOS os CSVs de um arquivo de dados, como streams de TEXTO,
    aceitando tanto o `.zip` de vários membros quanto o CSV já extraído.
    Formato da PGFN.

    Devolve pares `(rotulo, stream)`, onde `rotulo` é o nome de dentro do
    zip (ex.: `arquivo_lai_SIDA_3_202606.csv`) ou o nome do próprio arquivo
    quando ele já vem extraído. O rótulo existe pra que mensagens de erro
    apontem o membro exato, e não só o `.zip` de 1,3 GB.

    Cada stream é fechado assim que o consumidor pede o próximo — dois
    membros nunca ficam abertos ao mesmo tempo. Como é um gerador com
    `with` dentro, interromper a iteração no meio (`break`, exceção) fecha
    o que estiver aberto pelo `GeneratorExit`; não precisa de cuidado
    especial de quem chama.

    ⚠️ Um `.zip` sem NENHUM membro terminando em `sufixo_membro` levanta
    `ArquivoZipInvalidoError` listando o que tinha dentro, em vez de
    devolver zero linhas em silêncio — "processou tudo e não achou nada" e
    "não processou nada" são indistinguíveis pra quem chama, e essa é
    exatamente a classe de armadilha que já queimou este projeto antes
    (ver `_localizar_arquivos_rfb`, os globs case-sensitive de 23/08/2026).
    """
    path = Path(path)

    if not eh_zip(path):
        with open(path, encoding=encoding, newline="") as f:
            yield path.name, f
        return

    sufixo = sufixo_membro.lower()
    with zipfile.ZipFile(path) as arquivo_zip:
        membros = _membros_de_dados(arquivo_zip)
        csvs = [info for info in membros if info.filename.lower().endswith(sufixo)]
        if not csvs:
            nomes = [info.filename for info in membros]
            raise ArquivoZipInvalidoError(
                f"{path.name} não tem nenhum arquivo '{sufixo_membro}' dentro. Membros encontrados: {nomes}. "
                "Confira se o .zip é mesmo o de dados abertos esperado."
            )

        # Ordem estável por nome: o zip da PGFN traz as partes numeradas
        # (SIDA_1..SIDA_6) e a ordem do índice do zip não é garantida.
        for info in sorted(csvs, key=lambda i: i.filename):
            with arquivo_zip.open(info) as bruto:
                with io.TextIOWrapper(bruto, encoding=encoding, newline="") as f:
                    yield info.filename, f
