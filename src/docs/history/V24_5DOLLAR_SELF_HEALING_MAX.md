# Oddium V24 — 5Dollar MAX Intelligence + Self-Healing

Football authority remains 5Dollar only. The intelligence layer is conservative: it never estimates a score, clock or event.

## Automatic repair
- Missing live clock: reuse the last confirmed 5Dollar clock.
- Backward clock: quarantine the first regression; accept it only if 5Dollar repeats the correction on the next snapshot.
- Score regression / VAR correction: same two-snapshot confirmation policy.
- Terminal -> live regression: quarantine once, then accept only if repeated by the provider.
- Semantic event fingerprints survive polling/reordering and prevent duplicate notifications.
- Brain state persists in `api_cache_dir/5dollar_brain_state.json`, so a restart does not erase dedupe/confirmed state.
- Every repair/anomaly is counted in diagnostics. Raw 5Dollar payloads remain untouched in the RAW store for audit.

This is deliberately bounded self-healing: Oddium repairs transport/state glitches from previously confirmed provider facts, but never invents football facts and never silently overrides a repeated provider correction.
