# Oddium V23 — 5Dollar MAX Intelligence

5Dollar remains the only football authority. V23 adds a deterministic intelligence layer that never fabricates football data.

- fixture brain keyed strictly by native 5Dollar fixture id
- snapshot revision tracking
- semantic event fingerprints and cross-poll deduplication
- score/status/clock diff engine
- backwards-clock rejection in the intelligence state
- change journal per live fixture
- RAW 5Dollar payload retained before normalization
- batch live remains `/fixtures?status=live&include=odds,events,stats&per_page=500`
- adaptive account quota/cache logic from V22 retained
- derived team form analytics are computed exclusively from 5Dollar finished fixtures
- diagnostics now expose intelligence counters

The Discord visual design is unchanged.
