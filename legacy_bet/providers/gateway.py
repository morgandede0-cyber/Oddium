from __future__ import annotations

from datetime import datetime, timezone
import aiohttp

from config import SETTINGS
from ..core.constants import COMPETITIONS
from ..data.database import Database
from .five_dollar import FiveDollarClient




class OddsAPI:
    """Oddium football gateway — intentionally 5Dollar-only.

    5Dollar is the single football authority. This class is the stable façade
    used by the rest of Oddium so provider details stay isolated here.
    """

    def __init__(self, db: Database):
        self.db = db
        self.session: aiohttp.ClientSession | None = None
        self.last_status: int | None = None
        self.last_error: str | None = None
        self.remaining: str | None = None
        self.five_dollar = FiveDollarClient(self._shared_session)

    async def start(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25), headers={"User-Agent": "Oddium/23-5Dollar-Max-Intelligence"})

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def _shared_session(self):
        await self.start()
        assert self.session is not None
        return self.session

    def _sync_diag(self):
        self.last_status = self.five_dollar.last_status
        self.last_error = self.five_dollar.last_error
        self.remaining = self.five_dollar.rate_limit_remaining

    async def fetch_five_dollar_live(self, sport_key: str, *, force: bool = False):
        out = await self.five_dollar.live_shells(sport_key, force=force); self._sync_diag(); return out

    async def fetch_five_dollar_live_board(self, *, force: bool = False):
        out = await self.five_dollar.live_board_shells(force=force); self._sync_diag(); return out

    async def fetch_five_dollar_fixtures(self, sport_key: str, *, force: bool = False):
        out = await self.five_dollar.fixture_shells(sport_key, force=force); self._sync_diag(); return out

    async def fetch_five_dollar_details(self, fixture_id: int, *, force: bool = False):
        out = await self.five_dollar.fixture_details(fixture_id, force=force); self._sync_diag(); return out

    async def fetch_five_dollar_finished(self, sport_key: str, *, days: int = 7):
        out = await self.five_dollar.finished_shells(sport_key, days=days); self._sync_diag(); return out

    async def selected_bookmakers(self, *, force: bool = False) -> list[str]:
        return ["Bet365 • 5DollarFootballAPI Pro"] if SETTINGS.five_dollar_api_key else []

    async def fetch_events(self, sport_key: str, *, statuses: str = "pending,live", priority: bool = False):
        if not SETTINGS.five_dollar_api_key or sport_key not in self.five_dollar.LEAGUES:
            return []
        return await self.fetch_five_dollar_fixtures(sport_key, force=priority)

    async def quota_status(self) -> tuple[int, str]:
        day = datetime.now(timezone.utc).date().isoformat()
        row = await self.db.fetchone("SELECT requests_used FROM api_usage WHERE usage_date=?", (day,))
        used = int(row["requests_used"]) if row else 0
        return used, self.five_dollar.rate_limit_remaining or "?"

    async def five_dollar_call(self, action: str, *, object_id: int | None = None, league_id: int | None = None, season=None, market: str = "1x2", table_type: str = "total"):
        f = self.five_dollar
        if action == "live": return await f.raw_fixtures(status="live", include="odds,events,stats", per_page=500, lang="fr")
        if action == "fixtures": return await f.raw_fixtures(include="odds,events,stats", per_page=50, lang="fr")
        if action == "fixture": return await f.raw_fixture(int(object_id))
        if action == "odds": return await f.fixture_odds(int(object_id), market=market)
        if action == "bookmakers": return await f.bookmakers()
        if action == "odds_history": return await f.odds_history(int(object_id), market=market)
        if action == "events": return await f.fixture_events(int(object_id))
        if action == "statistics": return await f.fixture_statistics(int(object_id))
        if action == "standings": return await f.standings_native(int(league_id or object_id), season=season, table_type=table_type)
        if action == "countries": return await f.countries()
        if action == "leagues": return await f.leagues(include="seasons", per_page=100, lang="fr")
        if action == "league": return await f.league(int(league_id or object_id))
        if action == "league_fixtures": return await f.league_fixtures(int(league_id or object_id), include="odds,events,stats", per_page=50, lang="fr")
        if action == "team": return await f.team(int(object_id))
        if action == "team_fixtures": return await f.team_fixtures(int(object_id), include="odds,events,stats", per_page=50, lang="fr")
        if action == "team_intelligence": return await f.team_intelligence(int(object_id))
        if action == "status": return await f.account_status()
        if action == "engine": return f.diagnostics()
        raise ValueError(f"Action 5Dollar inconnue: {action}")

    @staticmethod
    def parse_event_shell(raw: dict, sport_key: str) -> dict | None:
        if not isinstance(raw, dict) or raw.get("id") is None or not raw.get("home_team") or not raw.get("away_team") or not raw.get("commence_time"):
            return None
        return {"event_id": str(raw["id"]), "sport_key": sport_key, "competition_name": raw.get("competition_name") or COMPETITIONS.get(sport_key, {}).get("name", sport_key), "home_team": str(raw["home_team"]), "away_team": str(raw["away_team"]), "home_team_id": raw.get("home_team_id"), "away_team_id": raw.get("away_team_id"), "commence_time": str(raw["commence_time"]), "status": raw.get("status") or "pending", "home_score": raw.get("home_score"), "away_score": raw.get("away_score"), "five_dollar_fixture_id": raw.get("five_dollar_fixture_id"), "provider_odds": raw.get("provider_odds")}

    @staticmethod
    def parse_score_shell(raw: dict, sport_key: str) -> dict | None:
        if not isinstance(raw, dict) or raw.get("id") is None: return None
        return {"event_id": str(raw["id"]), "merged_from_event_ids": [], "sport_key": sport_key, "home_team": raw.get("home_team"), "away_team": raw.get("away_team"), "home_team_id": raw.get("home_team_id"), "away_team_id": raw.get("away_team_id"), "commence_time": raw.get("commence_time"), "status": raw.get("status") or "pending", "home_score": raw.get("home_score"), "away_score": raw.get("away_score"), "live_clock": raw.get("live_clock"), "period": raw.get("period"), "status_detail": raw.get("status_detail"), "source": raw.get("source") or "5DollarFootballAPI", "provider_events": raw.get("provider_events") or [], "five_dollar_fixture_id": raw.get("five_dollar_fixture_id"), "provider_odds": raw.get("provider_odds"), "provider_stats": raw.get("provider_stats") or {}, "competition_name": raw.get("competition_name"), "status_code": raw.get("status_code"), "status_reason": raw.get("status_reason"), "corners": raw.get("corners") or {}, "cards": raw.get("cards") or {}, "provider_payload": raw.get("provider_payload") or {}}
