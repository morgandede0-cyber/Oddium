# Oddium V25 — 5Dollar Maximum Autonomy

V25 keeps 5Dollar as the single football authority and adds an operational guardian around V24's self-healing match brain.

- Circuit breaker per API family after repeated failures, with automatic recovery.
- Stale-cache fallback only when a real previous 5Dollar response exists; no synthetic football facts.
- Persistent audit JSONL for automatic repairs/recoveries.
- Automatic schema discovery when 5Dollar adds top-level response fields.
- Per-fixture data-quality score and last-confirmed-state rollback primitive.
- Existing quarantine/confirmation policy remains for clock, score/VAR and terminal-status regressions.
- RAW 5Dollar responses remain immutable diagnostic truth.
- Rate-limit budget, retry/backoff and cache coalescing remain active.

Financial state (tickets/Gold/settlement) is intentionally not auto-rewritten by the guardian. Self-healing is limited to recoverable provider/transport/state inconsistencies.
