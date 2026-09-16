from legacy_bet.betting.service import BettingService


def row(**kw):
    base = {
        'sport_key': 'soccer_italy_serie_a',
        'commence_time': '2026-09-14T18:45:00+00:00',
        'home_team': 'Torino FC',
        'away_team': 'AS Roma',
        'live_phase': 'kickoff_wait',
        'match_status': 'pending',
        'live_source': 'Horloge Oddium',
        'home_score': None,
        'away_score': None,
        'live_clock': "0'",
        'completed': 0,
    }
    base.update(kw)
    return base


def test_team_key_removes_provider_suffixes_and_years():
    assert BettingService._team_display_key('Torino FC') == BettingService._team_display_key('Torino')
    assert BettingService._team_display_key('AS Roma') == BettingService._team_display_key('Roma')
    assert BettingService._team_display_key('Como 1907') == BettingService._team_display_key('Como')
    assert BettingService._team_display_key('Parma Calcio 1913') == BettingService._team_display_key('Parma')


def test_live_dedupe_prefers_real_live_over_kickoff_placeholder():
    svc = BettingService.__new__(BettingService)
    rows = [
        row(),
        row(home_team='Torino', away_team='Roma', live_phase='second_half', match_status='live',
            live_source='5DollarFootballAPI', home_score=0, away_score=1, live_clock="63'"),
    ]
    result = svc._dedupe_match_rows(rows, 25)
    assert len(result) == 1
    assert result[0]['live_source'] == '5DollarFootballAPI'
    assert result[0]['home_score'] == 0
    assert result[0]['away_score'] == 1
