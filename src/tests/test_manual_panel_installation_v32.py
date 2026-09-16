from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_on_ready_never_restores_or_creates_panels():
    text = (ROOT / "main.py").read_text(encoding="utf-8")
    block = text.split("async def on_ready():", 1)[1].split("@bot.event", 1)[0]
    assert "refresh_existing_panel" not in block
    assert "refresh_existing_live_panel" not in block
    assert "ensure_panel" not in block
    assert "ensure_live_panel" not in block


def test_automatic_refresh_does_not_call_creation_helpers():
    text = (ROOT / "legacy_bet" / "discord_ui" / "panel.py").read_text(encoding="utf-8")
    main_refresh = text.split("async def refresh_existing_panel", 1)[1].split("@staticmethod", 1)[0]
    live_refresh = text.split("async def refresh_existing_live_panel", 1)[1]
    assert "self.ensure_panel(" not in main_refresh
    assert "self.ensure_live_panel(" not in live_refresh
    assert "fetch_message" in main_refresh
    assert "fetch_message" in live_refresh


def test_only_setup_commands_install_panels():
    text = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'name="setup"' in text
    assert 'name="setup_live"' in text
    assert "msg = await panel.ensure_panel(interaction.channel)" in text
    assert "msg = await panel.ensure_live_panel(interaction.channel)" in text
