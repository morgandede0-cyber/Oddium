# V49 — Full code audit

Audit performed from V48 source (42 Python files / ~6.5k Python lines).

Corrections:
- Discord interaction timeout: provider-backed league/match navigation now acknowledges the interaction before network work and edits the original response afterward.
- BetCarouselSession supports rendering after a deferred interaction.
- Europa League carousel asset is mapped and converted to a real PNG (the uploaded file had WebP/RIFF bytes despite a .png filename).
- Removed one proven unused import (`COMPETITIONS` in panel.py).
- Removed two byte-identical duplicate compose files; `docker-compose.yaml` remains canonical for Coolify.
- Removed generated `__pycache__`, `.pyc`, and `.pytest_cache` artifacts from the deliverable.
- Updated stale V38 static tests to the current V45/V46 logo size and mode-aware carousel architecture.
- Added V49 regression tests for interaction acknowledgement and Europa asset mapping.

Checks:
- `python -m compileall -q .`
- 42 selected static/regression tests pass locally.
- Full pytest collection cannot run in this audit container because runtime dependencies `discord.py` and `aiosqlite` are not installed here. They remain declared in requirements.txt for Docker/Coolify.

Deliberately not removed:
- defensive exception handlers used around network, Discord, persistence, cache and cleanup paths;
- compatibility/migration code in the betting service/database;
- `live/websocket.py`, which is active runtime code;
- historical docs, which are documentation rather than runtime dead code.
