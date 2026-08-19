from app.scoring.rules import SCORING_CRITERIA, get_criterion, total_weight
from app.scoring.zona_franca import is_zona_franca


def test_total_weight_is_100():
    assert total_weight() == 100


def test_all_criteria_have_positive_or_zero_weight():
    for c in SCORING_CRITERIA:
        assert c.weight >= 0


def test_get_criterion_found():
    c = get_criterion("divida_ativa_pgfn")
    assert c is not None
    assert c.weight == 20


def test_get_criterion_not_found():
    assert get_criterion("nao_existe") is None


def test_zona_franca_match():
    assert is_zona_franca("Boa Vista", "RR") is True
    assert is_zona_franca("boa vista", "rr") is True  # case-insensitive


def test_zona_franca_no_match():
    assert is_zona_franca("São Paulo", "SP") is False


def test_rating_google_is_zeroed_per_client_request():
    c = get_criterion("rating_google")
    assert c.weight == 0
