"""
Testes da pré-seleção de candidatos (app/scoring/pre_selecao.py).

Este módulo é 100% puro (nenhum I/O), então os testes montam os dicts de
candidato à mão, exatamente no formato que `_coletar_sinais_gratuitos`
(app/workers/celery_app.py) produz. A parte com I/O -- MGET no Redis,
resolução do município, e o fato de só os selecionados chegarem ao
pipeline pago -- é coberta em test_executar_busca_mensal.py.
"""
import pytest

from app.scoring.pre_selecao import (
    pontuar_candidato,
    resumir_composicao,
    selecionar_melhores_candidatos,
)
from app.scoring.rules import get_criterion


def _candidato(cnpj: str, inscricoes: int = 0, zona_franca: bool = False, ativo: bool = True) -> dict:
    return {
        "cnpj": cnpj,
        "razao_social": f"Clinica {cnpj[-4:]}",
        "uf": "SP",
        "qtd_inscricoes_ativas": inscricoes,
        "zona_franca": zona_franca,
        "situacao_ativa": ativo,
    }


# --- pontuar_candidato ---


def test_candidato_sem_sinal_nenhum_pontua_zero():
    assert pontuar_candidato({}) == 0.0


def test_dict_vazio_e_chaves_ausentes_nao_levantam():
    # Mesma tolerância de montar_score_lead: sinal ausente é "sem sinal",
    # nunca exceção -- a pré-seleção roda sobre o universo inteiro e não
    # pode morrer por causa de uma linha incompleta.
    assert pontuar_candidato({"cnpj": "1"}) == 0.0
    assert pontuar_candidato({"qtd_inscricoes_ativas": None}) == 0.0
    assert pontuar_candidato({"qtd_inscricoes_ativas": "nao-e-numero"}) == 0.0


def test_apenas_cnpj_ativo_vale_o_peso_do_criterio():
    peso = get_criterion("unidade_cnpj_ativo").weight
    assert pontuar_candidato({"situacao_ativa": True}) == float(peso)


def test_pgfn_escala_ate_o_teto_de_6_inscricoes():
    peso = get_criterion("divida_ativa_pgfn").weight
    # Fórmula confirmada com o cliente: min(qtd, 6) * (peso / 6)
    assert pontuar_candidato({"qtd_inscricoes_ativas": 3}) == pytest.approx(3 * (peso / 6))
    assert pontuar_candidato({"qtd_inscricoes_ativas": 6}) == pytest.approx(float(peso))
    # acima de 6 não passa do teto
    assert pontuar_candidato({"qtd_inscricoes_ativas": 50}) == pytest.approx(float(peso))


def test_zona_franca_vale_o_peso_cheio_do_criterio():
    peso = get_criterion("zona_franca_alc").weight
    assert pontuar_candidato({"zona_franca": True}) == float(peso)


def test_score_maximo_preliminar_e_a_soma_dos_tres_criterios_gratuitos():
    maximo = (
        get_criterion("divida_ativa_pgfn").weight
        + get_criterion("zona_franca_alc").weight
        + get_criterion("unidade_cnpj_ativo").weight
    )
    candidato = _candidato("11111111000100", inscricoes=6, zona_franca=True, ativo=True)
    assert pontuar_candidato(candidato) == pytest.approx(float(maximo))
    # E é um SUBCONJUNTO do score final de 100 -- não uma escala paralela.
    assert maximo < 100


# --- ordenação e corte ---


def test_corta_no_top_n_priorizando_divida_sobre_score_somado():
    candidatos = [
        _candidato("00000000000001", inscricoes=0),                    # score  5
        _candidato("00000000000002", inscricoes=6),                    # score 25
        _candidato("00000000000003", zona_franca=True),                # score 25, sem dívida
        _candidato("00000000000004", inscricoes=2),                    # score ~11.7
        _candidato("00000000000005", inscricoes=6, zona_franca=True),  # score 45
    ]

    selecionados = selecionar_melhores_candidatos(candidatos, top_n=3)

    assert len(selecionados) == 3
    # Os 3 com dívida entram, na ordem de inscrições; o de ALC sem dívida
    # fica de fora mesmo tendo score somado maior que o de 2 inscrições.
    assert [c["cnpj"] for c in selecionados] == [
        "00000000000005",  # 6 inscrições, + ZF no desempate
        "00000000000002",  # 6 inscrições
        "00000000000004",  # 2 inscrições
    ]
    assert "00000000000003" not in [c["cnpj"] for c in selecionados]


def test_divida_ativa_sobe_no_ranking():
    sem_divida = _candidato("00000000000001", inscricoes=0)
    com_divida = _candidato("00000000000002", inscricoes=4)

    # ordem de entrada propositalmente ao contrário do resultado esperado
    selecionados = selecionar_melhores_candidatos([sem_divida, com_divida], top_n=2)

    assert selecionados[0]["cnpj"] == "00000000000002"
    assert selecionados[0]["score_preliminar"] > selecionados[1]["score_preliminar"]


def test_zona_franca_sobe_no_ranking_entre_candidatos_sem_divida():
    fora = _candidato("00000000000001", zona_franca=False)
    dentro = _candidato("00000000000002", zona_franca=True)

    # nenhum dos dois tem dívida -> os dois caem na fase 2, e aí ZF ordena
    selecionados = selecionar_melhores_candidatos([fora, dentro], top_n=2)

    assert selecionados[0]["cnpj"] == "00000000000002"
    assert selecionados[0]["score_preliminar"] > selecionados[1]["score_preliminar"]


def test_divida_ativa_sempre_vence_zona_franca():
    """
    Substitui `test_zona_franca_supera_divida_ativa_no_teto`, que fixava
    o comportamento da PRIMEIRA versão da pré-seleção (score somado, em
    que Zona Franca com 20 pontos binários empatava com o teto da PGFN e
    superava qualquer dívida menor).

    Aquele comportamento era o oposto da prioridade do cliente e foi
    corrigido em 23/08/2026 pela separação em fases. Este teste fixa a
    regra correta: dívida ativa vence Zona Franca SEMPRE, inclusive na
    dívida mínima possível (1 inscrição) contra o melhor caso possível
    de um candidato sem dívida (ALC + CNPJ ativo).
    """
    zf_sem_divida = _candidato("00000000000001", zona_franca=True, inscricoes=0)
    divida_minima_sem_zf = _candidato("00000000000002", zona_franca=False, inscricoes=1)

    selecionados = selecionar_melhores_candidatos([zf_sem_divida, divida_minima_sem_zf], top_n=2)

    assert selecionados[0]["cnpj"] == "00000000000002"
    assert selecionados[0]["fase_selecao"] == "divida_ativa"
    assert selecionados[1]["fase_selecao"] == "completar_cota"
    # E o score somado NÃO explica a ordem -- o de fase 1 tem score MENOR.
    # É exatamente por isso que a seleção não pode ordenar por ele.
    assert selecionados[0]["score_preliminar"] < selecionados[1]["score_preliminar"]


def test_ordena_fase_1_por_quantidade_de_inscricoes_decrescente():
    candidatos = [
        _candidato("00000000000001", inscricoes=2),
        _candidato("00000000000002", inscricoes=9),
        _candidato("00000000000003", inscricoes=5),
    ]

    selecionados = selecionar_melhores_candidatos(candidatos, top_n=3)

    assert [c["qtd_inscricoes_ativas"] for c in selecionados] == [9, 5, 2]
    assert all(c["fase_selecao"] == "divida_ativa" for c in selecionados)


def test_acima_do_teto_da_formula_ainda_ordena_por_inscricoes_reais():
    """
    A fórmula da PGFN satura em 6 inscrições (`pgfn_points`), mas a FASE 1
    ordena pela quantidade REAL -- quem tem 30 inscrições entra antes de
    quem tem 7, mesmo os dois pontuando o peso cheio no score.
    """
    candidatos = [
        _candidato("00000000000001", inscricoes=7),
        _candidato("00000000000002", inscricoes=30),
    ]

    selecionados = selecionar_melhores_candidatos(candidatos, top_n=2)

    assert [c["cnpj"] for c in selecionados] == ["00000000000002", "00000000000001"]
    # ambos saturam a fórmula -- o score sozinho não distinguiria
    assert selecionados[0]["score_preliminar"] == selecionados[1]["score_preliminar"]


def test_zona_franca_desempata_dentro_da_fase_1():
    """
    Zona Franca continua valendo -- mas só entre candidatos empatados na
    mesma quantidade de inscrições, onde não tira vaga de ninguém com
    mais dívida.
    """
    sem_zf = _candidato("00000000000001", inscricoes=4, zona_franca=False)
    com_zf = _candidato("00000000000002", inscricoes=4, zona_franca=True)

    selecionados = selecionar_melhores_candidatos([sem_zf, com_zf], top_n=2)

    assert selecionados[0]["cnpj"] == "00000000000002"


# --- Preenchimento da cota: fase 1 versus fase 2 ---


def test_com_candidatos_com_divida_suficientes_nenhum_sem_divida_e_selecionado():
    """
    O caso que a correção existe pra garantir: havendo candidatos com
    dívida suficientes pra encher a cota, ZERO candidatos sem dívida
    entram -- por mais atraentes que sejam nos outros sinais.
    """
    com_divida = [_candidato(f"1{i:013d}", inscricoes=1 + (i % 6)) for i in range(60)]
    # 40 candidatos sem dívida, todos no melhor cenário possível
    # (Zona Franca + ativo) e com CNPJ que os colocaria primeiro num
    # desempate por CNPJ.
    sem_divida = [_candidato(f"0{i:013d}", inscricoes=0, zona_franca=True) for i in range(40)]

    selecionados = selecionar_melhores_candidatos(sem_divida + com_divida, top_n=60)

    assert len(selecionados) == 60
    assert all(c["fase_selecao"] == "divida_ativa" for c in selecionados)
    assert all(c["qtd_inscricoes_ativas"] > 0 for c in selecionados)

    resumo = resumir_composicao(selecionados)
    assert resumo["por_divida_ativa"] == 60
    assert resumo["para_completar_cota"] == 0


def test_sem_divida_completam_a_cota_ordenados_por_zona_franca_e_cnpj_ativo():
    """
    Quando não há candidatos com dívida suficientes, a fase 2 completa a
    cota -- e aí sim os sinais gratuitos restantes decidem a ordem.
    """
    com_divida = [
        _candidato("20000000000001", inscricoes=3),
        _candidato("20000000000002", inscricoes=1),
    ]
    sem_divida = [
        _candidato("10000000000001", inscricoes=0, zona_franca=False, ativo=False),
        _candidato("10000000000002", inscricoes=0, zona_franca=False, ativo=True),
        _candidato("10000000000003", inscricoes=0, zona_franca=True, ativo=True),
    ]

    selecionados = selecionar_melhores_candidatos(sem_divida + com_divida, top_n=5)

    # Fase 1 primeiro, na ordem de dívida; depois fase 2 pelos demais sinais.
    assert [c["cnpj"] for c in selecionados] == [
        "20000000000001",  # 3 inscrições
        "20000000000002",  # 1 inscrição
        "10000000000003",  # ZF + ativo  = 25
        "10000000000002",  # só ativo    = 5
        "10000000000001",  # nada        = 0
    ]
    assert [c["fase_selecao"] for c in selecionados] == [
        "divida_ativa",
        "divida_ativa",
        "completar_cota",
        "completar_cota",
        "completar_cota",
    ]


def test_cota_menor_que_os_com_divida_corta_dentro_da_fase_1():
    com_divida = [_candidato(f"0000000000000{i}", inscricoes=i) for i in range(1, 6)]
    sem_divida = [_candidato("00000000000009", inscricoes=0, zona_franca=True)]

    selecionados = selecionar_melhores_candidatos(sem_divida + com_divida, top_n=2)

    assert [c["qtd_inscricoes_ativas"] for c in selecionados] == [5, 4]
    assert resumir_composicao(selecionados)["para_completar_cota"] == 0


def test_universo_inteiro_sem_divida_usa_so_a_fase_2():
    candidatos = [
        _candidato("00000000000001", inscricoes=0, zona_franca=False),
        _candidato("00000000000002", inscricoes=0, zona_franca=True),
    ]

    selecionados = selecionar_melhores_candidatos(candidatos, top_n=2)

    assert all(c["fase_selecao"] == "completar_cota" for c in selecionados)
    assert selecionados[0]["cnpj"] == "00000000000002"  # ZF ordena aqui
    resumo = resumir_composicao(selecionados)
    assert resumo["por_divida_ativa"] == 0
    assert resumo["para_completar_cota"] == 2


def test_empate_desempata_por_cnpj_crescente_de_forma_deterministica():
    # todos com o mesmo score (só "ativo") -- é o caso MAIS COMUM no
    # universo real, então o desempate é que decide o lote de verdade.
    candidatos = [
        _candidato("00000000000009"),
        _candidato("00000000000002"),
        _candidato("00000000000005"),
    ]

    primeira = selecionar_melhores_candidatos(candidatos, top_n=2)
    # mesma entrada embaralhada -> mesmo resultado
    segunda = selecionar_melhores_candidatos(list(reversed(candidatos)), top_n=2)

    assert [c["cnpj"] for c in primeira] == ["00000000000002", "00000000000005"]
    assert [c["cnpj"] for c in primeira] == [c["cnpj"] for c in segunda]


def test_top_n_maior_que_o_universo_devolve_todos():
    candidatos = [_candidato("00000000000001"), _candidato("00000000000002")]
    assert len(selecionar_melhores_candidatos(candidatos, top_n=60)) == 2


def test_top_n_zero_ou_negativo_devolve_lista_vazia():
    # Guarda de custo: uma config zerada nunca pode virar "processe tudo".
    candidatos = [_candidato(f"0000000000000{i}") for i in range(5)]
    assert selecionar_melhores_candidatos(candidatos, top_n=0) == []
    assert selecionar_melhores_candidatos(candidatos, top_n=-10) == []


def test_universo_vazio_nao_quebra():
    assert selecionar_melhores_candidatos([], top_n=60) == []


def test_nao_muta_os_candidatos_de_entrada():
    candidatos = [_candidato("00000000000001", inscricoes=3)]
    selecionar_melhores_candidatos(candidatos, top_n=1)
    assert "score_preliminar" not in candidatos[0]


def test_preserva_os_campos_originais_do_candidato():
    candidatos = [_candidato("00000000000001", inscricoes=3)]
    selecionado = selecionar_melhores_candidatos(candidatos, top_n=1)[0]
    assert selecionado["razao_social"] == "Clinica 0001"
    assert selecionado["uf"] == "SP"
    assert selecionado["cnpj"] == "00000000000001"


def test_corta_exatamente_no_n_com_universo_grande():
    # 500 candidatos, top 60 -- o cenário real do corte.
    candidatos = [_candidato(f"{i:014d}", inscricoes=i % 7) for i in range(500)]
    selecionados = selecionar_melhores_candidatos(candidatos, top_n=60)
    assert len(selecionados) == 60
    # nenhum candidato de fora tem score maior que o pior selecionado
    piores_dentro = min(c["score_preliminar"] for c in selecionados)
    cnpjs_dentro = {c["cnpj"] for c in selecionados}
    for candidato in candidatos:
        if candidato["cnpj"] not in cnpjs_dentro:
            assert pontuar_candidato(candidato) <= piores_dentro


# --- resumir_composicao ---


def test_resumo_conta_zona_franca_divida_e_faixa_de_score():
    selecionados = selecionar_melhores_candidatos(
        [
            _candidato("00000000000001", zona_franca=True),
            _candidato("00000000000002", inscricoes=3),
            _candidato("00000000000003"),
        ],
        top_n=3,
    )

    resumo = resumir_composicao(selecionados)

    assert resumo["total"] == 3
    assert resumo["com_zona_franca"] == 1
    assert resumo["com_divida_ativa"] == 1
    assert resumo["score_preliminar_max"] > resumo["score_preliminar_min"]


def test_resumo_de_lote_vazio_nao_quebra():
    resumo = resumir_composicao([])
    assert resumo["total"] == 0
    assert resumo["com_zona_franca"] == 0
