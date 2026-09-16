# Code layout and audit — V28

The active runtime is organized by responsibility. Historical Python tests for removed engines have been deleted instead of being shipped as executable-looking dead code. Historical release notes remain isolated under `docs/history/`.

The V28 line-by-line/static audit also removed unused public methods, unused imports, the obsolete `api_football_fixture_id` runtime path, stale provider rankings, stale environment variables, stale installer instructions, and version-labelled runtime comments that no longer described the current architecture.

A packaging regression introduced by the V27 folder move was found and fixed: Discord UI modules were resolving `legacy_bet/assets` while the actual artwork lives at the project-root `assets/` directory. Static tests now verify every carousel asset exists at the expected location.

The Champions League asset was also found to contain WebP bytes despite its `.png` filename. It has been converted to a real PNG and a signature test now prevents this mismatch from returning.

Static hygiene tests verify Python parsing, duplicate top-level definitions, retired-provider identifiers, carousel asset presence and PNG signatures.
