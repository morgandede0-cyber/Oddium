from legacy_bet.live_market import LiveMarketConsensus, devig


def test_devig_removes_bookmaker_margin():
    p = devig((1.09, 9.0, 18.0))
    assert abs(sum(p) - 1.0) < 1e-12
    assert p[0] > 0.84
    assert p[2] < 0.06


def test_consensus_uses_multiple_books_and_is_robust():
    row = {
        "HomeTeam": "Real Madrid", "AwayTeam": "Rayo Vallecano", "Div": "SP1",
        "B365H": "1.09", "B365D": "9.0", "B365A": "18.0",
        "PSH": "1.10", "PSD": "9.2", "PSA": "19.0",
        "BWH": "1.08", "BWD": "9.5", "BWA": "20.0",
    }
    q = LiveMarketConsensus.quote_from_row(row)
    assert q is not None
    assert q.books == 3
    assert q.home_probability > 0.84
    assert q.away_probability < 0.06
    assert 1.02 <= q.overround <= 1.14


def test_average_market_fallback_is_accepted():
    row = {"AvgH": "1.60", "AvgD": "4.20", "AvgA": "5.50"}
    q = LiveMarketConsensus.quote_from_row(row)
    assert q is not None
    assert q.books == 1
    assert q.home_probability > q.away_probability
