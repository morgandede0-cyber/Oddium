import math
from legacy_bet.market_model import HistoricalMarketModel, TeamState, implied_probs, softmax


def test_devig_probabilities_sum_to_one():
    p = implied_probs((1.09, 9.0, 18.0))
    assert p is not None
    assert abs(sum(p) - 1.0) < 1e-9
    assert p[0] > p[1] > p[2]


def test_market_features_respect_elo_direction():
    strong = TeamState(elo=2000.0)
    weak = TeamState(elo=1450.0)
    x1 = HistoricalMarketModel._features(strong, weak)
    x2 = HistoricalMarketModel._features(weak, strong)
    assert x1[1] > 0
    assert x2[1] < 0


def test_softmax_is_probability_distribution():
    p = softmax([3.0, 1.0, -1.0])
    assert len(p) == 3
    assert all(0 < v < 1 for v in p)
    assert abs(sum(p) - 1.0) < 1e-12
    assert p[0] > p[1] > p[2]
