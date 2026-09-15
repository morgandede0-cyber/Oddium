# Oddium V19 — 5Dollar Turnkey Core

V19 treats 5DollarFootballAPI Pro as a ready-made football backend instead of reconstructing data from unrelated providers.

## 5Dollar-native capabilities used
- Global live board: `GET /v1/fixtures?status=live&include=odds,events,stats&per_page=500`
- Scheduled fixtures and historical results through league fixture lists
- Stable fixture, league and team IDs
- Native status/status_code for LIVE / minute / half / full
- Scores, half-time score, corners and cards from the fixture payload
- Event timeline: goals, corners, cards, missed penalties, substitutions and period scores
- Live statistics: attacks, dangerous attacks, shots on/off target, possession and first-half splits
- Bet365 markets and opening/closing/in-play stages; Oddium's existing betting UI currently consumes 1X2 while the native payload remains available for Match Center evolution
- League standings; the API also supports corner/card standings
- League catalogue/seasons, team fixtures/form, countries, bookmakers, odds history and account/quota endpoints are supported by 5Dollar and reserved as native data services rather than being re-created from other providers.

## Authority rules
For a capability 5Dollar supplies, 5Dollar is authoritative. Other providers must not overwrite its fixture identity, league, live phase, score or clock. External sources are fallbacks only for a capability/data gap that 5Dollar does not supply or when the 5Dollar service itself is unavailable.

## Live rendering
The Discord design is unchanged. The Live board is populated from 5Dollar's native live list. Oddium converts status_code into its existing visual states. It no longer promotes a scheduled kickoff into a fake `DÉMARRAGE`, and `kickoff_wait` is excluded from the permanent Live panel.
