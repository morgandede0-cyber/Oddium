from pathlib import Path

def test_ws_consumer_does_not_repaint_panel_per_event():
    text = Path('main.py').read_text(encoding='utf-8')
    block = text.split('async def on_live_ws_event',1)[1].split('async def notify_live_followers',1)[0]
    assert 'refresh_existing_live_panel' not in block
    assert 'notify_live_followers' in block

def test_collector_keeps_single_canonical_repaint():
    text = Path('main.py').read_text(encoding='utf-8')
    block = text.split('async def live_collector_supervisor',1)[1]
    assert 'await panel.refresh_existing_live_panel()' in block
