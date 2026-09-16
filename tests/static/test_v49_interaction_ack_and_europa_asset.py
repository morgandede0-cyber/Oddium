from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = (ROOT / 'legacy_bet/discord_ui/ui.py').read_text(encoding='utf-8')


def test_bet_league_open_acknowledges_before_provider_call():
    block = UI.split('class BetLeagueOpenButton', 1)[1].split('class BetLeagueCarouselView', 1)[0]
    assert block.index('interaction.response.defer') < block.index('matches_for_window')
    assert 'interaction.edit_original_response' in block


def test_components_session_can_render_after_defer():
    block = UI.split('class BetCarouselSession', 1)[1].split('class BetDayNav', 1)[0]
    assert 'interaction.response.is_done()' in block
    assert 'interaction.edit_original_response' in block


def test_europa_carousel_asset_is_mapped_and_present():
    assert '"soccer_uefa_europa_league": "carousel_league-europa.png"' in UI
    assert (ROOT / 'assets/carousel_league-europa.png').is_file()
