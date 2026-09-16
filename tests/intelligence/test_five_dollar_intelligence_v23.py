from legacy_bet.providers.intelligence import FiveDollarIntelligence

def fixture(minute=10, score=(0,0), events=None):
    return {"id":123,"status":"in_play","status_code":str(minute),"goals":{"home":score[0],"away":score[1]},"events":events or []}

def test_semantic_event_dedupe_and_score_diff():
    x=FiveDollarIntelligence()
    a=x.ingest(fixture(events=[{"type":"corner","minute":3,"team":"home","count":1}]))
    assert len(a["new_events"]) == 1
    b=x.ingest(fixture(11, (1,0), [{"type":"corner","minute":3,"team":"home","count":1},{"type":"goal","minute":11,"team":"home","count":1}]))
    assert len(b["new_events"]) == 1
    assert any(c["type"] == "score" for c in b["changes"])

def test_backwards_clock_not_accepted():
    x=FiveDollarIntelligence(); x.ingest(fixture(70)); b=x.ingest(fixture(65))
    assert not any(c["type"] == "clock" for c in b["changes"])
    assert x.fixtures[123].minute == 70

def test_team_history_derivation():
    x=FiveDollarIntelligence(); rows=[
      {"status":"finished","teams":{"home":{"id":1},"away":{"id":2}},"goals":{"home":2,"away":1}},
      {"status":"finished","teams":{"home":{"id":3},"away":{"id":1}},"goals":{"home":0,"away":0}},]
    r=x.analyse_team_history(rows,1)
    assert r["games"]==2 and r["wins"]==1 and r["draws"]==1 and r["points"]==4
