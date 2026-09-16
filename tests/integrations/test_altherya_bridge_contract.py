from pathlib import Path


def test_oddium_uses_altherya_wallet_and_emits_events():
    root = Path(__file__).resolve().parents[2]
    economy = (root / "legacy_bet/betting/economy.py").read_text(encoding="utf-8")
    service = (root / "legacy_bet/betting/service.py").read_text(encoding="utf-8")
    main = (root / "main.py").read_text(encoding="utf-8")
    assert "bridge.get_balance" in economy
    assert "bridge.mutate" in economy
    assert '"type": "bet_placed"' in service
    assert 'bridge_event["type"] = "ticket_settled"' in main
