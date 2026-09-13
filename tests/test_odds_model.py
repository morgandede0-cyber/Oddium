import math

from legacy_bet.odds_api import OddsAPI


def _row(played, points, gf, ga, team_id=1, position=10):
    return {
        "team": {"id": team_id},
        "playedGames": played,
        "points": points,
        "goalsFor": gf,
        "goalsAgainst": ga,
        "position": position,
    }


def test_profile_prefers_established_strong_team_over_promoted_hot_start():
    league_rate = 1.40
    strong_prev = _row(38, 72, 70, 40)
    strong_now = _row(3, 4, 4, 4)
    promoted_hot = _row(3, 7, 6, 2)

    strong = OddsAPI._team_profile(strong_now, strong_prev, league_rate, promoted=False)
    promoted = OddsAPI._team_profile(promoted_hot, None, league_rate, promoted=True)

    assert strong["ppg"] > promoted["ppg"]
    assert strong["gf"] > promoted["gf"]
    assert strong["ga"] < promoted["ga"]


def test_poisson_orientation_is_home_draw_away():
    ph, pd, pa = OddsAPI._poisson_probs(2.6, 0.7)
    assert ph > pd > pa
    assert math.isclose(ph + pd + pa, 1.0, rel_tol=1e-9)


def test_market_model_makes_elite_home_team_heavy_favourite():
    # Synthetic Real-Madrid-vs-lower-table profile: target is roughly the
    # 1.10 / 9-10 / 15-20 family, not a weak 1.60 favourite.
    home = {"ppg": 2.35, "gd": 1.45, "rank": 1.0}
    away = {"ppg": 1.10, "gd": -0.35, "rank": 0.25}
    ph, pd, pa, gap = OddsAPI._market_probs(home, away, 1.45)
    assert gap > 400
    assert ph > 0.82
    assert 0.08 <= pd <= 0.14
    assert pa < 0.08


def test_market_model_keeps_balanced_match_reasonable():
    home = {"ppg": 1.50, "gd": 0.05, "rank": 0.55}
    away = {"ppg": 1.48, "gd": 0.03, "rank": 0.53}
    ph, pd, pa, gap = OddsAPI._market_probs(home, away, 1.40)
    assert 0.30 < ph < 0.55
    assert 0.20 < pd < 0.30
    assert 0.20 < pa < 0.45
