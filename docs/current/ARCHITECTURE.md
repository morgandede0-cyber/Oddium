# Oddium architecture — current

- `legacy_bet/core/` : constants and shared utilities.
- `legacy_bet/storage/` : SQLite persistence and compatibility migrations.
- `legacy_bet/providers/` : 5Dollar API, Guardian, intelligence and provider gateway.
- `legacy_bet/live/` : live identity/fingerprints and local WebSocket transport.
- `legacy_bet/betting/` : economy and betting business service.
- `legacy_bet/discord_ui/` : Discord views, embeds, panels and league carousels.
- `assets/` : local league carousel artwork and welcome artwork.
- `tests/` : active tests grouped by subsystem.
- `docs/history/` : historical migration notes; never imported by runtime.
- `scripts/dev/` : manual developer utilities.

Football data authority: 5DollarFootballAPI only.
