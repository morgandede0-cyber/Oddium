from pathlib import Path

UI = Path('legacy_bet/discord_ui/ui.py').read_text(encoding='utf-8')

def test_balance_button_exists_on_main_panel():
    assert 'label="Solde"' in UI
    assert 'custom_id="oddium:v56:balance"' in UI

def test_balance_uses_authoritative_economy_adapter():
    assert 'balance = await service.economy.get_balance(user.id)' in UI
    assert 'en temps réel depuis Altherya' in UI

def test_balance_refresh_reuses_private_page():
    assert 'class BalanceView' in UI
    assert 'label="Actualiser"' in UI
    assert 'interaction.edit_original_response(embed=await _balance_embed' in UI
