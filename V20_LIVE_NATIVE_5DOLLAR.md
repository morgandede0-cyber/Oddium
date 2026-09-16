# Oddium V20 — Native 5Dollar Live Board

The permanent Discord Live panel no longer reads SQLite to decide which fixtures are live.

Source of truth:
`GET /v1/fixtures?status=live&include=odds,events,stats&per_page=500&lang=fr`

The full native row is ingested, then only Oddium-supported competitions are retained.
`status`, `status_code`, goals, league/team IDs, events, stats and odds come from the same 5Dollar fixture.

SQLite remains persistence for bets/follows/history, not the authority for Live membership.
No kickoff-time inference and no kickoff_wait row can override the native live board.
