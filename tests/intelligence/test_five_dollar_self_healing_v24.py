from legacy_bet.providers.intelligence import FiveDollarIntelligence

def f(minute=70, score=(2,0), status="in_play"):
    return {"id":999,"status":status,"status_code":str(minute) if minute is not None else "live","goals":{"home":score[0],"away":score[1]},"events":[]}

def test_clock_regression_is_repaired_then_provider_correction_can_win():
    x=FiveDollarIntelligence(); x.enrich(f(70)); a=x.enrich(f(65)); assert a["minute"]==70 and a["status_code"]=="70"
    b=x.enrich(f(65)); assert b["status_code"]=="65" and x.fixtures[999].minute==65

def test_score_regression_quarantined_then_confirmed():
    x=FiveDollarIntelligence(); x.enrich(f(70,(2,0))); a=x.enrich(f(71,(1,0)))
    assert a["goals"]["home"]==2 and a["oddium_intelligence"]["repairs"]
    b=x.enrich(f(71,(1,0))); assert b["goals"]["home"]==1

def test_missing_clock_reuses_only_confirmed_provider_clock():
    x=FiveDollarIntelligence(); x.enrich(f(44)); a=x.enrich(f(None)); assert a["minute"]==44

def test_terminal_regression_quarantined():
    x=FiveDollarIntelligence(); x.enrich(f(90,(2,0),"finished")); a=x.enrich(f(90,(2,0),"in_play")); assert a["status"]=="finished"
