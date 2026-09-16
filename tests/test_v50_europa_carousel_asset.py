from pathlib import Path


def test_europa_carousel_asset_is_present_and_real_png():
    root = Path(__file__).resolve().parents[1]
    asset = root / "assets" / "carousel_europa.png"
    assert asset.is_file()
    assert asset.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_europa_mapping_uses_existing_asset():
    root = Path(__file__).resolve().parents[1]
    source = (root / "legacy_bet" / "discord_ui" / "ui.py").read_text(encoding="utf-8")
    assert '"soccer_uefa_europa_league": "carousel_europa.png"' in source
    assert 'carousel_league-europa.png' not in source
