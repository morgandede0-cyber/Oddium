# Oddium V22 — 5Dollar Ultimate Engine

- 5DollarFootballAPI is the only active football authority.
- Global live snapshot uses `status=live&include=odds,events,stats` and native fixture IDs.
- Adaptive rolling request budget targets 9/10 Pro calls per minute, leaving one call of headroom.
- Endpoint-aware cache prevents duplicate requests and uses long TTLs for immutable/reference data.
- Every successful API response is mirrored verbatim into `API_CACHE_DIR/5dollar_raw` before normalization.
- Rate-limit headers (`Remaining`, `Limit`, `Reset`) and `Retry-After` are honored.
- Bet365 odds, all 11 documented markets, events, stats, standings, countries, leagues/seasons, teams, team/league history, bookmakers, odds tick history and account status remain available through `/5dollar`.
- `/5dollar action:Moteur 5Dollar Ultimate` exposes runtime diagnostics.
- No API-Football/SofaScore/ESPN/FotMob/football-data/PropLine client is instantiated by the football gateway.
