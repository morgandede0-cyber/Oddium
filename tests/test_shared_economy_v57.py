from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_shared_database_replaces_http_bridge():
    cfg=(ROOT/'config.py').read_text(encoding='utf-8')
    main=(ROOT/'main.py').read_text(encoding='utf-8')
    econ=(ROOT/'legacy_bet/betting/economy.py').read_text(encoding='utf-8')
    client=(ROOT/'legacy_bet/integrations/shared_economy.py').read_text(encoding='utf-8')
    assert 'economy_database_url' in cfg
    assert 'SharedEconomyClient' in main
    assert 'SharedEconomyClient' in econ
    assert 'economy_wallets' in client and 'economy_transactions' in client and 'economy_events' in client
    assert 'AltheryaBridgeClient(SETTINGS' not in main
