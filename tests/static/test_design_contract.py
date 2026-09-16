from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = (ROOT / "legacy_bet" / "discord_ui" / "ui.py").read_text(encoding="utf-8")
PANEL = (ROOT / "legacy_bet" / "discord_ui" / "panel.py").read_text(encoding="utf-8")


def test_oddium_visual_identity_is_present():
    for marker in ("ODDIUM • BET DESK", "ODDIUM • LIVE ARENA", "MATCH CENTER", "HALL OF FAME"):
        assert marker in UI


def test_live_entry_keeps_interactive_controls():
    assert "view=LivePanelView(self.service)" in UI


def test_live_design_has_single_canonical_builder():
    assert "return await _live_embed(self.service)" in PANEL
    assert "title=\"🔴  ODDIUM • LIVE CENTER\"" not in PANEL


def test_carousel_assets_are_still_used():
    assert "CAROUSEL_ASSETS" in UI
    assert 'e.set_image(url=f"attachment://{filename}")' in UI
