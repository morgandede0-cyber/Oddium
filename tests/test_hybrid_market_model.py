from legacy_bet.odds_api import OddsAPI


def test_elite_home_team_market_shape():
    ph, pd, pa, gap = OddsAPI._market_probs_from_elo(2050, 1530, 0.0, 0.0)
    assert gap > 500
    assert ph > 0.82
    assert 0.07 < pd < 0.14
    assert pa < 0.08


def test_balanced_match_stays_balanced():
    ph, pd, pa, gap = OddsAPI._market_probs_from_elo(1600, 1600, 0.0, 0.0)
    assert 0.36 < ph < 0.48  # home edge, but not an absurd favourite
    assert 0.23 < pd < 0.30
    assert 0.25 < pa < 0.36
    assert 50 < gap < 110


def test_reverse_strength_favours_away():
    ph, pd, pa, _ = OddsAPI._market_probs_from_elo(1450, 1950, 0.0, 0.0)
    assert pa > 0.75
    assert ph < 0.12
