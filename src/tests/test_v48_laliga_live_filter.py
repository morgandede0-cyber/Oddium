from legacy_bet.providers.five_dollar import FiveDollarClient


def _client():
    c = FiveDollarClient(lambda: None)
    c._league_ids["soccer_spain_la_liga"] = (14, 1810150156)
    return c


def test_public_v1_spain_la_liga_is_accepted():
    c = _client()
    row = {"five_dollar_league_id": 14, "competition_name": "Spain La Liga"}
    assert c._belongs_to_supported_league(row, "soccer_spain_la_liga")


def test_sponsored_laliga_name_is_accepted():
    c = _client()
    row = {"five_dollar_league_id": 14, "competition_name": "LaLiga EA Sports"}
    assert c._belongs_to_supported_league(row, "soccer_spain_la_liga")


def test_wrong_spanish_competition_is_rejected():
    c = _client()
    row = {"five_dollar_league_id": 14, "competition_name": "Spain Segunda Division"}
    assert not c._belongs_to_supported_league(row, "soccer_spain_la_liga")


def test_wrong_id_is_rejected_even_with_laliga_name():
    c = _client()
    row = {"five_dollar_league_id": 999999, "competition_name": "Spain La Liga"}
    assert not c._belongs_to_supported_league(row, "soccer_spain_la_liga")
