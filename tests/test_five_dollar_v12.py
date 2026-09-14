from legacy_bet.five_dollar import FiveDollarClient


def sample_fixture(status="scheduled", code=None):
    return {
        "id": 2688654353,
        "league": {"id": 4160026622, "name": "Premier League"},
        "teams": {
            "home": {"id": 1, "name": "Arsenal"},
            "away": {"id": 2, "name": "Chelsea"},
        },
        "kickoff_utc": "2026-09-20T15:30:00+00:00",
        "status": status,
        "status_reason": None,
        "status_code": code,
        "goals": {"home": 1 if status == "in_play" else None, "away": 0 if status == "in_play" else None},
        "corners": {"home": 4, "away": 2},
        "cards": {"home": {"yellow": 1, "red": 0}, "away": {"yellow": 2, "red": 0}},
        "odds": {
            "1x2": {
                "opening": {"home": 1.8, "draw": 3.6, "away": 4.2},
                "closing": {"home": 1.72, "draw": 3.8, "away": 4.6},
                "inplay": {"home": 1.35, "draw": 4.8, "away": 9.0},
            }
        },
        "events": [
            {"type": "goal", "minute": 12, "team": "home", "count": 1},
            {"type": "yellow_card", "minute": 28, "team": "away", "count": 1},
            {"type": "corner", "minute": 31, "team": "home", "count": 4},
        ],
        "statistics": {
            "shots_on_target": {"home": 3, "away": 1},
            "shots_off_target": {"home": 4, "away": 2},
            "possession": {"home": 58, "away": 42},
        },
    }


def test_scheduled_fixture_prefers_closing_bet365_prices():
    shell = FiveDollarClient._fixture_to_shell(sample_fixture())
    assert shell["id"] == "5d:2688654353"
    assert shell["status"] == "pending"
    assert shell["provider_odds"]["home_odd"] == 1.72
    assert "Bet365" in shell["provider_odds"]["bookmaker"]


def test_live_fixture_uses_inplay_prices_and_clock():
    shell = FiveDollarClient._fixture_to_shell(sample_fixture("in_play", "63"))
    assert shell["status"] == "second_half"
    assert shell["live_clock"] == "63'"
    assert shell["home_score"] == 1
    assert shell["provider_odds"]["home_odd"] == 1.35


def test_events_are_normalized():
    shell = FiveDollarClient._fixture_to_shell(sample_fixture("in_play", "31"))
    kinds = [e["type"] for e in shell["provider_events"]]
    assert kinds == ["goal", "yellow_card", "corner"]


def test_statistics_ui_reconstructs_total_shots_and_corners():
    shell = FiveDollarClient._fixture_to_shell(sample_fixture("in_play", "31"))
    stats = FiveDollarClient._statistics_for_ui(sample_fixture()["statistics"], shell)
    assert stats[0]["values"]["Total Shots"] == 7
    assert stats[1]["values"]["Total Shots"] == 3
    assert stats[0]["values"]["Corner Kicks"] == 4

def test_unknown_status_never_voids_bet():
    assert FiveDollarClient._status({"status": "unknown", "status_reason": "kickoff_unconfirmed"}) == "suspended"
    assert FiveDollarClient._status({"status": "unknown", "status_reason": "result_unconfirmed"}) == "suspended"
