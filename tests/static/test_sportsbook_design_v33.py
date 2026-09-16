from pathlib import Path

UI = (Path(__file__).resolve().parents[2] / "legacy_bet" / "discord_ui" / "ui.py").read_text(encoding="utf-8")


def test_v33_sportsbook_signature_surfaces():
    for marker in ("ODDIUM • SPORTSBOOK", "LEAGUE LOBBY", "FEATURED MARKETS", "ODDIUM • MATCH BOARD", "ODDIUM • MARKET", "MARKET PULSE", "ODDIUM • BET SLIP"):
        assert marker in UI


def test_v33_keeps_local_carousel_assets():
    assert "CAROUSEL_ASSETS" in UI
    assert 'e.set_image(url=f"attachment://{filename}")' in UI


def test_v33_market_percentages_are_described_as_market_not_prediction():
    assert "pas un pronostic Oddium" in UI


def test_v33_manual_panels_still_preserved():
    main = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
    panel = (Path(__file__).resolve().parents[2] / "legacy_bet" / "discord_ui" / "panel.py").read_text(encoding="utf-8")
    assert "Panels are installed manually" in main
    assert "Only /setup is allowed to create/install the panel" in panel
    assert "await panel.ensure_panel" not in main[main.index("async def on_ready"):main.index("@bot.event\nasync def setup_hook")]
