from legacy_bet.api_football import ApiFootballClient


def test_status_mapping():
    assert ApiFootballClient._status("1H") == "first_half"
    assert ApiFootballClient._status("HT") == "halftime"
    assert ApiFootballClient._status("2H") == "second_half"
    assert ApiFootballClient._status("FT") == "finished"
    assert ApiFootballClient._status("PST") == "postponed"


def test_goal_event_normalization():
    event = {
        "time": {"elapsed": 57, "extra": None},
        "team": {"name": "Roma"},
        "player": {"name": "Dybala"},
        "assist": {"name": "Pellegrini"},
        "type": "Goal",
        "detail": "Normal Goal",
    }
    out = ApiFootballClient._normalize_event(event)
    assert out["type"] == "goal"
    assert out["clock"] == "57'"
    assert "Dybala" in out["detail"]


def test_var_and_cards():
    var = ApiFootballClient._normalize_event({
        "time": {"elapsed": 42}, "type": "Var", "detail": "Goal cancelled", "team": {"name": "Inter"}
    })
    red = ApiFootballClient._normalize_event({
        "time": {"elapsed": 81}, "type": "Card", "detail": "Red Card", "player": {"name": "Test"}
    })
    yellow = ApiFootballClient._normalize_event({
        "time": {"elapsed": 12}, "type": "Card", "detail": "Yellow Card", "player": {"name": "Test"}
    })
    assert var["type"] == "var"
    assert red["type"] == "red_card"
    assert yellow["type"] == "yellow_card"


def test_fixture_shell():
    raw = {
        "fixture": {"id": 123, "date": "2026-09-14T18:00:00+00:00", "status": {"short": "1H", "long": "First Half", "elapsed": 23}},
        "league": {"id": 135, "name": "Serie A"},
        "teams": {"home": {"id": 1, "name": "Torino"}, "away": {"id": 2, "name": "Roma"}},
        "goals": {"home": 0, "away": 1},
        "events": [],
    }
    out = ApiFootballClient._fixture_to_shell(raw)
    assert out["id"] == "af:123"
    assert out["status"] == "first_half"
    assert out["live_clock"] == "23'"
    assert out["home_score"] == 0 and out["away_score"] == 1
