from pathlib import Path

def test_visual_module_exists():
    p=Path("legacy_bet/discord_ui/visuals.py")
    assert p.exists()
    s=p.read_text(encoding="utf-8")
    assert "def match_card" in s
    assert "def betslip_card" not in s  # V38 combo slip is text-only

def test_market_wires_dynamic_match_card_only():
    s=Path("legacy_bet/discord_ui/ui.py").read_text(encoding="utf-8")
    assert 'match_card(dict(m),"prematch")' in s
    assert "betslip_card" not in s

def test_logo_assets_have_fallback_contract():
    s=Path("legacy_bet/discord_ui/visuals.py").read_text(encoding="utf-8")
    assert "assets' / 'team_logos" in s
    assert "initials=" in s
