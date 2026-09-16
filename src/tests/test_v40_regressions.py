from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_panel_entry_always_replaces_private_page():
    ui = (ROOT / "legacy_bet/discord_ui/ui.py").read_text(encoding="utf-8")
    block = ui[ui.index("class MainPanelView"):ui.index("# --- Premium betting flow")]
    assert block.count("replace_existing=True") == 5


def test_private_page_can_be_reopened_after_discord_dismissal():
    src = (ROOT / "legacy_bet/discord_ui/private_pages.py").read_text(encoding="utf-8")
    assert "await page.message.delete()" in src
    assert "replace_existing: bool = False" in src
    assert "interaction.followup.send" in src


def test_swiss_super_league_cannot_pass_supported_feed():
    src = (ROOT / "legacy_bet/providers/five_dollar.py").read_text(encoding="utf-8")
    assert "_belongs_to_supported_league" in src
    assert "if lid not in ids" in src
    assert "return False" in src
    service = (ROOT / "legacy_bet/betting/service.py").read_text(encoding="utf-8")
    assert "_competition_name_allowed" in service
    assert '"super league"' not in service
