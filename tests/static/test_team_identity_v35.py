from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def test_logo_sync_exists():
    s=(ROOT/'legacy_bet/discord_ui/team_logos.py').read_text(encoding='utf-8')
    assert 'MANIFEST_URL' in s and 'ALLOWED_GROUPS' in s and 'source_logo' in s
def test_visual_fallback_and_index():
    s=(ROOT/'legacy_bet/discord_ui/visuals.py').read_text(encoding='utf-8')
    assert "index.json" in s and "initials=" in s
def test_no_panel_creation_from_logo_sync():
    s=(ROOT/'legacy_bet/discord_ui/team_logos.py').read_text(encoding='utf-8').lower()
    assert 'ensure_panel' not in s and 'channel.send' not in s
