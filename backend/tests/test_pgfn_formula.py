"""
Testes da fórmula de pontuação da dívida ativa PGFN.

Fórmula confirmada com o Douglas em 19/08/2026 (via WhatsApp: "sim,
era isso mesmo"): pontos = min(qtd_inscricoes_ativas, 6) × (peso ÷ 6).
"""
from app.scoring.rules import PGFN_TETO_INSCRICOES, get_criterion, pgfn_points


def test_zero_inscricoes_gives_zero_points():
    assert pgfn_points(0) == 0


def test_one_inscricao_gives_proportional_points():
    # peso atual = 20 -> 1 inscrição = 20/6 ≈ 3.33
    assert pgfn_points(1) == 20 / 6


def test_three_inscricoes_gives_half_the_weight():
    # peso atual = 20 -> 3 inscrições = metade do teto (6) = 10 pontos
    assert pgfn_points(3) == 10


def test_teto_de_seis_bate_peso_maximo():
    assert pgfn_points(6) == 20


def test_acima_do_teto_nao_ultrapassa_peso_maximo():
    """Mais de 6 inscrições não vale mais que o peso máximo do critério."""
    assert pgfn_points(6) == pgfn_points(10) == pgfn_points(100)


def test_valor_negativo_tratado_como_zero():
    assert pgfn_points(-3) == 0


def test_aceita_peso_customizado_sem_alterar_a_formula():
    """Se o peso mudar de novo no futuro (ex: voltar a 40), a fórmula
    continua igual — só muda o resultado."""
    assert pgfn_points(6, weight=40) == 40
    assert pgfn_points(3, weight=40) == 20


def test_usa_o_peso_atual_configurado_em_scoring_criteria():
    criterion = get_criterion("divida_ativa_pgfn")
    assert pgfn_points(6) == criterion.weight


def test_teto_e_seis_conforme_confirmado_com_cliente():
    assert PGFN_TETO_INSCRICOES == 6
