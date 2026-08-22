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

Mesma escala do score final, de propósito
-----------------------------------------
Os pontos vêm das MESMAS funções de `rules.py`/`compute_lead_score.py`
que o score definitivo usa, não de uma escala paralela inventada. Assim
um `score_preliminar` de 40 significa literalmente "já garantiu 40 dos
100 pontos do score real" e é comparável com o `score_total` que o lead
vai ter no fim. Se um peso mudar em `SCORING_CRITERIA` (com aval do
cliente), a pré-seleção acompanha sozinha.

⚠️ Efeito emergente que vale conhecer antes da primeira execução
----------------------------------------------------------------
Os pesos foram fechados com o Douglas para RANQUEAR leads já
encontrados, não para DECIDIR quem é enriquecido. Reaproveitá-los como
porta de entrada tem uma consequência que ninguém aprovou explicitamente:

Como Zona Franca vale 20 pontos de um máximo de 45, e é binário, um
candidato em ALC praticamente sempre supera um candidato de fora dela —
mesmo um com 5 inscrições na PGFN (16,7 pontos). Se existirem 60+
clínicas de CNAE 8630 nos 10 municípios da lista, o top N tende a ser
composto quase só por elas, o que na prática restringe a busca àquelas
cidades — exatamente o que o Douglas disse que NÃO queria
("é prioridade, não filtro exclusivo", ver a nota em rules.py:66).

Isto NÃO foi "corrigido" aqui de propósito: mudar peso exige aval do
cliente (regra do próprio rules.py). O que foi feito foi tornar o efeito
visível — `executar_busca_mensal` registra a composição do lote
selecionado (quantos de ALC, quantos com dívida ativa) no log e no
retorno, então a primeira execução real mostra o problema em números em
vez de escondê-lo.

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


def selecionar_melhores_candidatos(candidatos: list[dict], top_n: int) -> list[dict]:
    """
    Pontua todos os candidatos, ordena por score preliminar decrescente e
    devolve os `top_n` primeiros.

    Devolve dicts NOVOS (cópias rasas) com a chave `score_preliminar`
    adicionada — não muta a entrada, para que quem chamou continue com a
    lista original intacta (útil para logar/auditar o universo completo
    depois de já ter selecionado).

    Desempate determinístico por CNPJ crescente
    -------------------------------------------
    A chave de ordenação é `(-score_preliminar, cnpj)`, não só o score.
    Isso importa mais do que parece: a esmagadora maioria dos candidatos
    empata (sem dívida ativa e fora de ALC = 5 pontos para todo mundo),
    então o critério de desempate é que decide, de fato, quem entra no
    lote. Ordenar só por score deixaria a escolha à mercê da ordem física
    das linhas no arquivo da Receita, que muda a cada lote mensal — duas
    execuções sobre o mesmo universo poderiam selecionar conjuntos
    diferentes, sem nada no código explicando por quê.

    Com o CNPJ no desempate o resultado é reproduzível: mesmo universo,
    mesmos 60 selecionados. O viés introduzido (CNPJ menor tende a ser
    empresa mais antiga) é arbitrário mas inofensivo, e é uma propriedade
    conhecida em vez de um acidente.

    `top_n <= 0` devolve lista vazia (nada a selecionar), em vez de
    levantar exceção ou devolver tudo — evita que uma configuração
    zerada/negativa vire acidentalmente "processe o universo inteiro",
    que é exatamente o custo que esta função existe pra impedir.
    """
    if top_n <= 0:
        return []

    pontuados = [
        {**candidato, "score_preliminar": pontuar_candidato(candidato)}
        for candidato in candidatos
    ]
    pontuados.sort(key=lambda c: (-c["score_preliminar"], c.get("cnpj") or ""))
    return pontuados[:top_n]


def resumir_composicao(selecionados: list[dict]) -> dict:
    """
    Conta de onde vem a força do lote selecionado — quantos entraram por
    Zona Franca, quantos por dívida ativa na PGFN, e a faixa de score.

    Existe por causa do efeito emergente descrito no topo do módulo: sem
    isto, o fato de o lote inteiro poder ser de ALC só apareceria depois
    de alguém abrir os 60 leads um a um no dossiê. `executar_busca_mensal`
    loga este resumo e o devolve no resultado da task.
    """
    if not selecionados:
        return {
            "total": 0,
            "com_zona_franca": 0,
            "com_divida_ativa": 0,
            "score_preliminar_min": 0.0,
            "score_preliminar_max": 0.0,
        }

    scores = [c.get("score_preliminar", 0.0) for c in selecionados]
    return {
        "total": len(selecionados),
        "com_zona_franca": sum(1 for c in selecionados if c.get("zona_franca")),
        "com_divida_ativa": sum(1 for c in selecionados if (c.get("qtd_inscricoes_ativas") or 0) > 0),
        "score_preliminar_min": min(scores),
        "score_preliminar_max": max(scores),
    }
