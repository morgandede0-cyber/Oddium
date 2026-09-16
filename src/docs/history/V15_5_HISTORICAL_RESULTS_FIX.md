# Oddium V15.5 — Historical Results Fix

- Ticket settlement no longer uses 5Dollar, even as a fallback.
- Historical final results are queried from independent providers for each of the last 8 calendar days.
- Sofascore and ESPN score methods now accept an explicit historical date.
- Fixes the V15.4 bug where independent providers were queried only for the current day, leaving yesterday's tickets pending.
- Provider statuses are normalized before checking `finished`.
- Existing database, bets, balances and history are preserved.
