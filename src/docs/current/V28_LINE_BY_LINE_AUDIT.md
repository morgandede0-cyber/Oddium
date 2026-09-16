# V28 — line-by-line audit

Main corrections made during the exhaustive pass:

- fixed the project-root asset path after the package reorganization;
- converted the mislabeled Champions League WebP asset to actual PNG;
- removed obsolete runtime `api_football_fixture_id` handling;
- removed retired provider priority tables and stale fallback labels;
- removed dead `OddsAPIError`, `build_main_carousel_embed`, `CompetitionSelect`, `PreferencesView`, `team_key`, unused service methods and obsolete odds-model tests;
- removed unused imports;
- moved ISO timestamp parsing to `core/time_utils.py` to decouple a pure helper from SQLite;
- made invalid `GUILD_ID` configuration fail soft instead of crashing import/startup;
- synchronized `.env.example` with the settings actually read by the application;
- removed stale Docker environment entries and corrected the Windows installer to request `FIVE_DOLLAR_FOOTBALL_API_KEY`;
- normalized runtime wording so it describes the current 5Dollar-only architecture.

Validation performed: compileall, AST parsing/hygiene, SQL literal placeholder-count scan, environment-variable consistency scan, YAML parse for both compose files, shell syntax check, asset signature/type inspection, provider method-reference scan, and all dependency-free active tests available in the audit environment.
