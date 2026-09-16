from legacy_bet.providers.guardian import FiveDollarGuardian
from legacy_bet.providers.intelligence import FiveDollarIntelligence

def test_circuit_breaker_and_recovery(tmp_path):
    g=FiveDollarGuardian(tmp_path)
    for _ in range(3): g.failure('fixtures','boom')
    assert not g.allow('fixtures')
    g.success('fixtures'); assert g.allow('fixtures')

def test_schema_discovery(tmp_path):
    g=FiveDollarGuardian(tmp_path)
    assert 'brand_new' in g.schema_watch('/fixtures',{'success':1,'brand_new':42})
    assert not g.schema_watch('/fixtures',{'success':1,'brand_new':99})

def test_quality_and_safe_rollback(tmp_path):
    i=FiveDollarIntelligence(tmp_path/'brain.json')
    i.ingest({'id':7,'status':'live','status_code':'51','goals':{'home':1,'away':0},'events':[]})
    q=i.fixture_quality(7); assert q['known'] and q['quality']>0
    r=i.rollback_snapshot(7); assert r['minute']==51 and r['goals']['home']==1
