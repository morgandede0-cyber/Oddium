# Oddium V21 — 200% 5Dollar

Oddium's active football engine is now exclusively 5DollarFootballAPI. Other historical provider adapters may remain in the repository for migration compatibility, but the active fixture, live, result, standings and Match Center paths do not consult them.

## Native API surface exposed

All 15 documented endpoints are represented by `FiveDollarClient`: fixtures, fixture detail, fixture odds, bookmakers, odds history, events, statistics, standings, countries, leagues, league detail, league fixtures, team detail, team fixtures, and account status.

The admin slash command `/5dollar` exposes the full native surface for inspection. Large responses are returned as JSON attachments instead of being truncated by Discord.

## Live

The permanent Live panel is fed directly by `GET /v1/fixtures?status=live&include=odds,events,stats&per_page=500`. 5Dollar fixture id is canonical. Oddium formats the returned state but does not invent kickoff/live state or combine another provider's score/clock.

## Betting / settlement

Upcoming fixtures and Bet365 markets come from 5Dollar. Settlement uses exact 5Dollar fixture detail first, then the 5Dollar finished league feed. If 5Dollar has not confirmed a result, the ticket stays pending.
