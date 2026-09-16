from pathlib import Path

def test_visual_module_exists():
    p=Path('legacy_bet/discord_ui/visuals.py')
    assert p.exists()
    s=p.read_text(encoding='utf-8')
    assert 'def match_card' in s and 'def betslip_card' in s

def test_market_wires_dynamic_card():
    s=Path('legacy_bet/discord_ui/ui.py').read_text(encoding='utf-8')
    assert 'match_card(dict(match), "prematch")' in s
    assert 'betslip_card(self.session.legs' in s

def test_logo_assets_have_fallback_contract():
    s=Path('legacy_bet/discord_ui/visuals.py').read_text(encoding='utf-8')
    assert "assets' / 'team_logos" in s
    assert "initials=" in s
