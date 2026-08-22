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


def test_ordena_por_score_decrescente_e_corta_no_top_n():
    candidatos = [
        _candidato("00000000000001", inscricoes=0),   # 5
        _candidato("00000000000002", inscricoes=6),   # 25
        _candidato("00000000000003", zona_franca=True),  # 25
        _candidato("00000000000004", inscricoes=2),   # ~11.7
        _candidato("00000000000005", inscricoes=6, zona_franca=True),  # 45
    ]

    selecionados = selecionar_melhores_candidatos(candidatos, top_n=3)

    assert len(selecionados) == 3
    scores = [c["score_preliminar"] for c in selecionados]
    assert scores == sorted(scores, reverse=True)
    assert selecionados[0]["cnpj"] == "00000000000005"  # o de score máximo
    # o de score mais baixo ficou de fora
    assert "00000000000001" not in [c["cnpj"] for c in selecionados]


def test_divida_ativa_sobe_no_ranking():
    sem_divida = _candidato("00000000000001", inscricoes=0)
    com_divida = _candidato("00000000000002", inscricoes=4)

    # ordem de entrada propositalmente ao contrário do resultado esperado
    selecionados = selecionar_melhores_candidatos([sem_divida, com_divida], top_n=2)

    assert selecionados[0]["cnpj"] == "00000000000002"
    assert selecionados[0]["score_preliminar"] > selecionados[1]["score_preliminar"]


def test_zona_franca_sobe_no_ranking():
    fora = _candidato("00000000000001", zona_franca=False)
    dentro = _candidato("00000000000002", zona_franca=True)

    selecionados = selecionar_melhores_candidatos([fora, dentro], top_n=2)

    assert selecionados[0]["cnpj"] == "00000000000002"
    assert selecionados[0]["score_preliminar"] > selecionados[1]["score_preliminar"]


def test_zona_franca_supera_divida_ativa_no_teto():
    """
    Documenta em teste o efeito emergente descrito no docstring do módulo:
    Zona Franca (20, binário) empata com a PGFN no TETO dela (6+
    inscrições) e supera qualquer dívida menor. Não é o comportamento
    "certo" ou "errado" -- é a consequência direta dos pesos fechados com
    o cliente, e está fixado aqui pra que qualquer mudança futura de peso
    apareça como quebra de teste em vez de mudança silenciosa de quem é
    selecionado.
    """
    zf_sem_divida = _candidato("00000000000001", zona_franca=True, inscricoes=0)
    divida_alta_sem_zf = _candidato("00000000000002", zona_franca=False, inscricoes=5)

    selecionados = selecionar_melhores_candidatos([divida_alta_sem_zf, zf_sem_divida], top_n=2)

    assert selecionados[0]["cnpj"] == "00000000000001"


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
