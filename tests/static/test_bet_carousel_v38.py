from pathlib import Path

UI=Path("legacy_bet/discord_ui/ui.py").read_text(encoding="utf-8")
VIS=Path("legacy_bet/discord_ui/visuals.py").read_text(encoding="utf-8")


def test_simple_and_combo_share_match_carousel():
    assert "class BetCarouselSession" in UI
    # Current design uses one mode-aware session for both SIMPLE and COMBINÉ.
    assert "BetCarouselSession(view.service, view.active, matches, view.mode)" in UI
    assert 'await self._open_leagues(interaction, "simple")' in UI
    assert 'await self._open_leagues(interaction, "combo")' in UI


def test_match_card_has_two_real_team_logo_slots():
    assert "hl=_logo(home,logo_size)" in VIS
    assert "al=_logo(away,logo_size)" in VIS
    assert "logo_size=220" in VIS
    assert "im.paste(hl" in VIS and "im.paste(al" in VIS


def test_combo_slip_is_text_only_and_single_extra_page():
    assert "def combo_text_embed(session)" in UI
    assert "self.slip_message" in UI
    assert "await self.slip_message.edit" in UI
    assert "betslip_card" not in UI


def test_clear_and_validate_live_only_on_combo_slip():
    block=UI[UI.index("class ComboSlipView"):UI.index("class ComboStakeModal")]
    assert 'label="Vider"' in block
    assert 'label="Valider"' in block


def test_combo_carousel_does_not_own_clear_validate():
    block=UI[UI.index("class BetMatchCarouselLayout"):UI.index("def combo_text_embed")]
    assert 'label="Vider"' not in block
    assert 'label="Valider"' not in block
