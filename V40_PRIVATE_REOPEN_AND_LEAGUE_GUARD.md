# Oddium V40 — Private Reopen + League Guard

- Permanent panel entry buttons now replace the user's previous navigation ephemeral page before opening a fresh one.
- This fixes the Discord limitation where dismissing an ephemeral message is invisible to the bot: a later panel click always reopens Oddium.
- Internal navigation still edits the same ephemeral page.
- The combo slip remains the only additional ephemeral page.
- 5Dollar competition filtering is fail-closed: league id AND competition identity must agree.
- Upcoming/finished/live provider rows are filtered.
- A second service-level guard hides contaminated legacy DB rows (including Swiss Super League rows incorrectly stored under a supported sport key).
