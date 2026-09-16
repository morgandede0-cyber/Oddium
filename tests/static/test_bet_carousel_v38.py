from pathlib import Path

UI=Path("legacy_bet/discord_ui/ui.py").read_text(encoding="utf-8")
VIS=Path("legacy_bet/discord_ui/visuals.py").read_text(encoding="utf-8")

def test_simple_and_combo_share_match_carousel():
    assert "BetCarouselSession" in UI
    assert 'BetCarouselSession(self.service,self.active,matches,"simple")' in UI
    assert 'BetCarouselSession(self.service,self.active,matches,"combo")' in UI

def test_match_card_has_two_real_team_logo_slots():
    assert "hl=_logo(home,86)" in VIS
    assert "al=_logo(away,86)" in VIS
    assert "im.paste(hl,(90,120),hl)" in VIS
    assert "im.paste(al,(W-176,120),al)" in VIS

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
    block=UI[UI.index("class BetMatchCarouselView"):UI.index("def combo_text_embed")]
    assert 'label="Vider"' not in block
    assert 'label="Valider"' not in block
