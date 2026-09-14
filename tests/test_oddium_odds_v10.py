from legacy_bet.oddium_odds import OddiumOddsEngine


class DummyAPI:
    pass


def _payload():
    return {
        "standings": [{
            "type": "TOTAL",
            "table": [
                {"team": {"name": "Chelsea FC"}, "playedGames": 5, "goalsFor": 10, "goalsAgainst": 4, "points": 12, "goalDifference": 6},
                {"team": {"name": "Hull City AFC"}, "playedGames": 5, "goalsFor": 3, "goalsAgainst": 12, "points": 2, "goalDifference": -9},
                {"team": {"name": "Arsenal FC"}, "playedGames": 5, "goalsFor": 9, "goalsAgainst": 5, "points": 11, "goalDifference": 4},
                {"team": {"name": "Liverpool FC"}, "playedGames": 5, "goalsFor": 8, "goalsAgainst": 6, "points": 9, "goalDifference": 2},
            ],
        }]
    }


def test_strong_home_favorite_gets_short_price():
    engine = OddiumOddsEngine(DummyAPI(), margin=0.055)
    model = engine._model_from_standings(_payload(), "Chelsea", "Hull City")
    assert model is not None
    probs, confidence = model
    odds = engine._to_odds(probs)
    assert probs[0] > 0.70
    assert odds[0] < 1.40
    assert odds[2] > 7.0
    assert 0.4 <= confidence <= 0.9


def test_prices_are_valid_and_have_margin():
    engine = OddiumOddsEngine(DummyAPI(), margin=0.06)
    odds = engine._to_odds((0.45, 0.28, 0.27))
    assert all(x > 1.0 for x in odds)
    assert sum(1 / x for x in odds) > 1.0


def test_unknown_team_fails_cleanly():
    engine = OddiumOddsEngine(DummyAPI())
    assert engine._model_from_standings(_payload(), "Unknown FC", "Hull City") is None
