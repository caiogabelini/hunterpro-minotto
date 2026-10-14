"""
Pré-seleção de candidatos — HunterPro Minotto Contabilidade
===========================================================

Corta o universo da semente (`gerar_semente_leads`, filtro nacional de
CNAE 8630 — ordem de centenas de milhares de CNPJs) para as poucas
dezenas que valem o enriquecimento COMPLETO, usando só sinais que já
temos de graça neste ponto do fluxo.

Por que existe
--------------
Antes desta etapa, `executar_busca_mensal` rodava `process_lead_pipeline`
em TODO CNPJ da semente. Cada execução do pipeline gasta chamadas pagas
(Google Places, Firecrawl, Anthropic, Hunter.io, ZeroBounce), então o
custo era proporcional ao universo, não ao número de leads que o cliente
contratou. A pré-seleção inverte isso: ranqueia de graça, depois gasta só
nos melhores.

Os 3 sinais gratuitos
---------------------
São exatamente os que estão disponíveis ANTES de qualquer chamada externa
paga:

1. `divida_ativa_pgfn` (peso 20) — lookup no índice Redis já construído
   por `refresh_pgfn_index`. Local, sem custo por consulta.
2. `zona_franca_alc` (peso 20) — lista estática de 10 municípios da
   Suframa (app/scoring/zona_franca.py). Zero I/O externo.
3. `unidade_cnpj_ativo` (peso 5) — situação cadastral, que já vem no
   arquivo bulk da Receita. Zero I/O externo.

Máximo possível: 45 de 100.

Os outros 6 critérios (RQE, WhatsApp, e-mail, presença digital, decisor,
rating Google) dependem justamente das etapas pagas — por definição não
podem entrar aqui, senão a pré-seleção teria o mesmo custo do pipeline.

Duas fases, não um score somado
-------------------------------
A seleção NÃO ordena por um score único com os 3 sinais somados. Ela
tem prioridade estrita: primeiro quem tem dívida ativa (ordenado por
quantidade de inscrições), e só se sobrar cota é que entra quem não tem
dívida (aí sim ordenado por Zona Franca / CNPJ ativo). Ver
`selecionar_melhores_candidatos` para o racional completo.

Dívida ativa é o principal critério de venda do Douglas — é o que ele
quer priorizar de verdade na entrada do processamento. Zona Franca
continua sendo sinal de priorização, mas dentro do grupo certo: ela
ordena empatados, nunca toma a vaga de quem tem dívida.

⚠️ Histórico, pra quem for mexer: a primeira versão (22/08/2026) somava
os 3 sinais e ordenava por esse total. Como Zona Franca vale 20 e é
BINÁRIA, um candidato em ALC sem dívida nenhuma (25 pts) empatava com um
candidato no teto da fórmula da PGFN e superava qualquer dívida menor —
o lote podia acabar dominado por Zona Franca sem dívida, exatamente o
oposto da prioridade do cliente. Corrigido em 23/08/2026 pela separação
em fases. A separação é ESTRUTURAL de propósito: mudar peso em
`SCORING_CRITERIA` não consegue reintroduzir o problema.

`score_preliminar` continua na mesma escala do score final
----------------------------------------------------------
Os pontos vêm das MESMAS funções de `rules.py`/`compute_lead_score.py`
que o score definitivo usa, não de uma escala paralela. Um
`score_preliminar` de 40 significa literalmente "já garantiu 40 dos 100
pontos do score real".

⚠️ Mas ele é INFORMATIVO, não é mais a chave de ordenação. Um candidato
selecionado na fase 1 pode ter `score_preliminar` MENOR que um da fase 2
(1 inscrição fora de ALC = 8,3 contra 0 inscrição em ALC = 25). Isso é o
comportamento correto — a fase é que manda, não o total.

Este módulo é PURO
------------------
Nenhuma função aqui faz I/O. Os sinais chegam já resolvidos nos dicts de
entrada — quem busca PGFN no Redis e resolve o município é
`_coletar_sinais_gratuitos` em app/workers/celery_app.py. Mesma divisão
que já existe entre `montar_score_lead` (puro, aqui em app/scoring/) e
`process_lead_pipeline` (orquestra e faz I/O, em app/workers/).

Por que aqui e não em receita_federal_bulk.py
---------------------------------------------
`receita_federal_bulk.py` sabe ler arquivos da Receita e nada mais — não
conhece PGFN, Zona Franca nem pesos de score. Colocar a pontuação lá
obrigaria aquele módulo a importar `app.scoring.*`, invertendo a direção
da dependência (hoje o scoring é que é o topo da pilha). Como o que este
módulo faz é atribuir pontos usando os pesos fechados com o cliente, ele
é scoring, e mora junto de `rules.py`/`compute_lead_score.py`.
"""
import re
import unicodedata

from app.scoring.compute_lead_score import zona_franca_points
from app.scoring.rules import get_criterion, pgfn_points


def pontuar_candidato(candidato: dict) -> float:
    """
    Score preliminar (0-45) de um candidato da semente, a partir só dos
    sinais gratuitos já resolvidos no dict.

    Chaves lidas (todas opcionais — ausente significa "sem sinal", nunca
    levanta exceção, mesma tolerância de `montar_score_lead`):

      - "qtd_inscricoes_ativas": int   — inscrições ativas na PGFN
      - "zona_franca": bool            — município está numa ALC
      - "situacao_ativa": bool         — CNPJ com situação cadastral ativa

    Nota sobre `situacao_ativa`: na prática ele é CONSTANTE no fluxo
    atual, porque `filtrar_semente_leads` já roda com
    `apenas_ativos=True` e descarta os inativos antes de chegar aqui. Ou
    seja, todo candidato ganha os mesmos 5 pontos e o critério não
    desempata nada. Foi mantido mesmo assim para que `score_preliminar`
    fique na mesma escala do `score_total` final (ver docstring do
    módulo) e para não quebrar silenciosamente caso alguém passe a chamar
    a semente com `apenas_ativos=False`.
    """
    qtd_inscricoes = candidato.get("qtd_inscricoes_ativas") or 0
    try:
        qtd_inscricoes = int(qtd_inscricoes)
    except (TypeError, ValueError):
        qtd_inscricoes = 0

    # O peso de `unidade_cnpj_ativo` é lido direto de SCORING_CRITERIA em
    # vez de chamar `unidade_cnpj_ativo_points`: aquela função recebe a
    # string de situação cadastral da BrasilAPI ("ATIVA"), e aqui o sinal
    # vem do arquivo bulk da Receita, cujo vocabulário é um CÓDIGO ("02",
    # ver SITUACAO_CADASTRAL_ATIVA em receita_federal_bulk.py). São duas
    # representações da mesma coisa vindas de fontes diferentes; forjar
    # uma string "ATIVA" só pra satisfazer a assinatura esconderia essa
    # tradução. O peso continua vindo da mesma fonte única de verdade.
    criterio_ativo = get_criterion("unidade_cnpj_ativo")
    pontos_ativo = float(criterio_ativo.weight) if (criterio_ativo and candidato.get("situacao_ativa")) else 0.0

    return (
        pgfn_points(qtd_inscricoes)
        + zona_franca_points(bool(candidato.get("zona_franca")))
        + pontos_ativo
    )


def normalizar_cnpj(cnpj) -> str:
    """
    Só os dígitos do CNPJ (`"12.345.678/0001-90"` -> `"12345678000190"`).

    A semente da Receita sempre traz 14 dígitos crus, mas `Lead.cnpj` é
    `String(18)` -- cabe a versão com máscara, e linhas semeadas à mão ou
    vindas de outra origem podem estar assim. Comparar sem normalizar os
    DOIS lados deixaria passar justamente o CNPJ repetido que a exclusão
    existe pra barrar. `None`/vazio vira `""`.
    """
    return "".join(ch for ch in str(cnpj or "") if ch.isdigit())


def excluir_cnpjs_ja_na_base(candidatos: list[dict], cnpjs_na_base) -> tuple[list[dict], int]:
    """
    Remove dos candidatos todo CNPJ que já existe na tabela `leads`.
    Devolve `(candidatos_novos, quantidade_excluida)`, sem mutar a entrada.

    ⚠️ Tem que rodar ANTES de `selecionar_melhores_candidatos`, nunca
    depois. Se a exclusão viesse depois do corte, cada CNPJ repetido
    abriria um buraco no lote (60 selecionados - 8 já na base = 52
    processados); vindo antes, o corte enxerga só candidatos novos e a
    cota continua sendo preenchida inteira.

    Por que excluir TODO `kanban_status`, inclusive "ganho" e "perdido":
    reprocessar um lead existente não só gasta API paga pra entregar um
    lead que o cliente já tem -- o upsert por CNPJ de
    `_persistir_enriquecimento_lead` sobrescreve os dados enriquecidos, o
    score e a prioridade de um lead que alguém já está trabalhando no
    Kanban. Um lead "perdido" voltar como "novo" do mês é exatamente o
    que a busca mensal não pode entregar.

    A comparação normaliza os dois lados com `normalizar_cnpj` (só
    dígitos) -- quem chama pode passar o conjunto cru do banco.
    """
    base = {n for n in (normalizar_cnpj(c) for c in cnpjs_na_base) if n}
    novos = [c for c in candidatos if normalizar_cnpj(c.get("cnpj")) not in base]
    return novos, len(candidatos) - len(novos)


def normalizar_texto_para_termos(texto) -> str:
    """
    Maiúsculas, sem acento, e todo caractere que não é letra/dígito vira
    espaço (espaços repetidos colapsados). `"Saúde-Ocupacional Ltda."` ->
    `"SAUDE OCUPACIONAL LTDA"`; `"SEG. E MED."` -> `"SEG E MED"`.

    Pontuação vira separador de propósito: razão social da Receita usa
    hífen, ponto e barra como espaço ("MED - OCUPACIONAL", "SEG.E MED.").
    """
    sem_acento = unicodedata.normalize("NFKD", str(texto or ""))
    sem_acento = "".join(ch for ch in sem_acento if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^0-9A-Za-z]+", " ", sem_acento).upper().split())


def termo_que_casa(textos, termos) -> str | None:
    """
    Primeiro termo (já normalizado) que aparece como palavra/trecho
    INTEIRO em algum dos textos, ou `None`.

    "Inteiro" = delimitado por início/fim ou espaço, depois da
    normalização. Assim `OCUPACIONAL` casa em "MED - OCUPACIONAL" e em
    "SAUDE OCUPACIONAL LTDA", mas não em "PREOCUPACIONAL" nem
    "OCUPACIONAIS" -- substring solta casaria qualquer palavra que
    contivesse as letras, e o custo de um falso positivo aqui é perder um
    lead bom sem ninguém perceber.
    """
    termos_norm = [t for t in (normalizar_texto_para_termos(t) for t in termos) if t]
    if not termos_norm:
        return None
    for texto in textos:
        alvo = f" {normalizar_texto_para_termos(texto)} "
        if not alvo.strip():
            continue
        for termo in termos_norm:
            if f" {termo} " in alvo:
                return termo
    return None


def excluir_por_termos_de_nome(candidatos: list[dict], termos) -> tuple[list[dict], list[dict]]:
    """
    Tira os candidatos cuja `razao_social` ou `nome_fantasia` contém algum
    dos `termos` (ver `termo_que_casa`). Devolve `(mantidos, excluidos)`;
    cada excluído é uma cópia rasa com `termo_exclusao` dizendo qual termo
    casou -- é o que o dry-run mostra pra revisar falso positivo. Não muta
    a entrada.

    Hoje a lista é a de medicina ocupacional / segurança do trabalho
    (`settings.TERMOS_EXCLUSAO_NOME_CANDIDATO`), perfil que o cliente não
    quer prospectar e que não tem CNAE próprio pra filtrar na semente.

    ⚠️ Mesma regra de `excluir_cnpjs_ja_na_base`: tem que rodar ANTES de
    `selecionar_melhores_candidatos`, senão cada excluído abre um buraco
    no lote em vez de ceder a vaga ao próximo do ranking.

    `termos` vazio não exclui ninguém.
    """
    mantidos: list[dict] = []
    excluidos: list[dict] = []
    for candidato in candidatos:
        termo = termo_que_casa((candidato.get("razao_social"), candidato.get("nome_fantasia")), termos)
        if termo is None:
            mantidos.append(candidato)
        else:
            excluidos.append({**candidato, "termo_exclusao": termo})
    return mantidos, excluidos


FASE_DIVIDA_ATIVA = "divida_ativa"
FASE_COMPLETAR_COTA = "completar_cota"


def _qtd_inscricoes(candidato: dict) -> int:
    """Nº de inscrições ativas na PGFN, tolerante a valor ausente/inválido."""
    bruto = candidato.get("qtd_inscricoes_ativas") or 0
    try:
        return max(int(bruto), 0)
    except (TypeError, ValueError):
        return 0


def pontuar_sinais_complementares(candidato: dict) -> float:
    """
    Pontos dos sinais gratuitos que NÃO são dívida ativa: Zona Franca +
    CNPJ ativo (0-25).

    É a chave de ordenação da FASE 2 e o desempate dentro da FASE 1 (ver
    `selecionar_melhores_candidatos`). Existe separada de
    `pontuar_candidato` justamente para que Zona Franca nunca dispute a
    mesma vaga que dívida ativa -- ela só ordena candidatos que já estão
    empatados no critério principal.
    """
    criterio_ativo = get_criterion("unidade_cnpj_ativo")
    pontos_ativo = float(criterio_ativo.weight) if (criterio_ativo and candidato.get("situacao_ativa")) else 0.0
    return zona_franca_points(bool(candidato.get("zona_franca"))) + pontos_ativo


def selecionar_melhores_candidatos(candidatos: list[dict], top_n: int) -> list[dict]:
    """
    Escolhe os `top_n` candidatos que recebem o enriquecimento pago, em
    DUAS FASES com prioridade estrita -- não por um score único somado.

    FASE 1 -- quem tem dívida ativa na PGFN
        Todo candidato com `qtd_inscricoes_ativas > 0`, ordenado por
        número de inscrições DECRESCENTE (mais dívida entra primeiro).
        Preenche a cota inteira se houver candidatos suficientes.

    FASE 2 -- só se sobrar vaga
        Candidatos SEM dívida, ordenados por
        `pontuar_sinais_complementares` (Zona Franca + CNPJ ativo)
        decrescente. Se a fase 1 preencheu tudo, esta fase não seleciona
        ninguém.

    Por que duas fases, e não um score somado
    -----------------------------------------
    A primeira versão desta função (22/08/2026) somava os 3 sinais num
    `score_preliminar` único e ordenava por ele. Com Zona Franca valendo
    20 pontos e sendo BINÁRIA, um candidato em ALC sem dívida nenhuma
    (25 pts) empatava com um candidato no teto da fórmula da PGFN e
    superava qualquer dívida menor -- ou seja, Zona Franca competia com
    dívida ativa pela mesma vaga e frequentemente ganhava.

    Isso invertia a prioridade real do cliente: dívida ativa é o
    principal critério de venda do Douglas, é o que ele quer priorizar
    de fato na entrada do processamento. Zona Franca continua sendo um
    sinal de priorização relevante -- mas dentro do grupo certo, não
    tomando a vaga de quem tem dívida.

    A separação em fases torna essa prioridade ESTRUTURAL, não uma
    consequência de calibragem de peso: por construção, nenhum candidato
    sem dívida entra enquanto houver candidato com dívida fora do lote.
    Ajustar pesos em `SCORING_CRITERIA` não pode reintroduzir o problema.

    Desempates, determinísticos nas duas fases
    ------------------------------------------
      - Fase 1: `(-qtd_inscricoes, -sinais_complementares, cnpj)`. Entre
        dois candidatos com a MESMA quantidade de inscrições, Zona
        Franca desempata -- aqui ela não tira vaga de ninguém com mais
        dívida, só ordena empatados.
      - Fase 2: `(-sinais_complementares, cnpj)`.

    O CNPJ crescente fecha os dois: a maioria dos candidatos sem dívida
    empata em tudo, então sem esse critério a escolha ficaria à mercê da
    ordem física das linhas no arquivo da Receita, que muda a cada lote
    mensal -- duas execuções sobre o mesmo universo poderiam selecionar
    conjuntos diferentes sem nada no código explicando por quê. O viés
    (CNPJ menor tende a ser empresa mais antiga) é arbitrário mas
    inofensivo, e é propriedade conhecida em vez de acidente.

    Campos adicionados a cada selecionado
    -------------------------------------
      - `fase_selecao`: `"divida_ativa"` ou `"completar_cota"` -- qual
        fase colocou aquele candidato no lote.
      - `score_preliminar`: os 3 sinais somados (0-45), mantido por ser
        comparável com o `score_total` que o lead terá no fim.
        ⚠️ É INFORMATIVO -- não é mais a chave de ordenação. Um candidato
        de fase 1 pode ter `score_preliminar` menor que um de fase 2 (ex.:
        1 inscrição e fora de ALC = 8,3 contra 0 inscrição e em ALC = 25);
        isso é o comportamento correto, não um bug.

    Devolve dicts NOVOS (cópias rasas), sem mutar a entrada.

    `top_n <= 0` devolve lista vazia -- evita que uma configuração
    zerada/negativa vire acidentalmente "processe o universo inteiro",
    que é exatamente o custo que esta função existe pra impedir.
    """
    if top_n <= 0:
        return []

    com_divida: list[dict] = []
    sem_divida: list[dict] = []
    for candidato in candidatos:
        destino = com_divida if _qtd_inscricoes(candidato) > 0 else sem_divida
        destino.append(candidato)

    # FASE 1 -- mais dívida primeiro.
    com_divida.sort(
        key=lambda c: (
            -_qtd_inscricoes(c),
            -pontuar_sinais_complementares(c),
            c.get("cnpj") or "",
        )
    )
    selecionados = [
        {**c, "score_preliminar": pontuar_candidato(c), "fase_selecao": FASE_DIVIDA_ATIVA}
        for c in com_divida[:top_n]
    ]

    # FASE 2 -- só o que sobrou de cota, ordenado pelos demais sinais.
    vagas_restantes = top_n - len(selecionados)
    if vagas_restantes > 0:
        sem_divida.sort(
            key=lambda c: (
                -pontuar_sinais_complementares(c),
                c.get("cnpj") or "",
            )
        )
        selecionados.extend(
            {**c, "score_preliminar": pontuar_candidato(c), "fase_selecao": FASE_COMPLETAR_COTA}
            for c in sem_divida[:vagas_restantes]
        )

    return selecionados


def resumir_composicao(
    selecionados: list[dict],
    excluidos_ja_na_base: int = 0,
    excluidos_por_termo_no_nome: int = 0,
) -> dict:
    """
    Conta de onde veio cada candidato do lote -- quantos entraram pela
    FASE 1 (dívida ativa) e quantos pela FASE 2 (completar cota), quantos
    são de Zona Franca, e a faixa de score preliminar.

    `por_divida_ativa` e `para_completar_cota` são o número que responde
    "a cota foi preenchida por quem o cliente quer priorizar?" de um
    olhar só. `para_completar_cota == 0` significa que existiam
    candidatos com dívida suficientes pra encher o lote inteiro -- o
    cenário ideal.

    `executar_busca_mensal` loga este resumo e o devolve no resultado da
    task, pra que a composição do lote apareça já na primeira execução
    real em vez de só depois de alguém abrir os 60 leads um a um.

    `excluidos_ja_na_base` é só repassado (vem de
    `excluir_cnpjs_ja_na_base`, que roda antes do corte) -- fica aqui pra
    que a composição responda também "quantos o filtro de repetidos
    tirou do universo".

    `excluidos_por_termo_no_nome` idem, vindo de
    `excluir_por_termos_de_nome` (medicina ocupacional etc.). Contado
    SEPARADO de `excluidos_ja_na_base`: um CNPJ que está na base e também
    casa um termo conta só em `excluidos_ja_na_base` (essa exclusão roda
    primeiro).
    """
    if not selecionados:
        return {
            "total": 0,
            "excluidos_ja_na_base": excluidos_ja_na_base,
            "excluidos_por_termo_no_nome": excluidos_por_termo_no_nome,
            "por_divida_ativa": 0,
            "para_completar_cota": 0,
            "com_zona_franca": 0,
            "com_divida_ativa": 0,
            "inscricoes_pgfn_min": 0,
            "inscricoes_pgfn_max": 0,
            "score_preliminar_min": 0.0,
            "score_preliminar_max": 0.0,
        }

    scores = [c.get("score_preliminar", 0.0) for c in selecionados]
    por_divida = [c for c in selecionados if c.get("fase_selecao") == FASE_DIVIDA_ATIVA]
    inscricoes = [_qtd_inscricoes(c) for c in por_divida]

    return {
        "total": len(selecionados),
        "excluidos_ja_na_base": excluidos_ja_na_base,
        "excluidos_por_termo_no_nome": excluidos_por_termo_no_nome,
        "por_divida_ativa": len(por_divida),
        "para_completar_cota": sum(1 for c in selecionados if c.get("fase_selecao") == FASE_COMPLETAR_COTA),
        "com_zona_franca": sum(1 for c in selecionados if c.get("zona_franca")),
        "com_divida_ativa": sum(1 for c in selecionados if _qtd_inscricoes(c) > 0),
        # Faixa de inscrições DENTRO da fase 1 -- mostra se a cota foi
        # preenchida com dívidas altas ou raspando em quem tem 1 inscrição.
        "inscricoes_pgfn_min": min(inscricoes) if inscricoes else 0,
        "inscricoes_pgfn_max": max(inscricoes) if inscricoes else 0,
        "score_preliminar_min": min(scores),
        "score_preliminar_max": max(scores),
    }
