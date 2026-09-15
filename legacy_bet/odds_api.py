from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

from config import SETTINGS
from .constants import COMPETITIONS
from .database import Database, utcnow_iso
from .api_football import ApiFootballClient
from .five_dollar import FiveDollarClient


class OddsAPIError(RuntimeError):
    pass


class OddsAPI:
    """Oddium football provider hub.

    V12 routes canonical fixtures, live data, events, statistics and Bet365 1/N/2
    through 5DollarFootballAPI Pro. The older providers below are deliberately kept
    as resilience/legacy adapters; Discord interactions always read SQLite rather
    than calling an external API directly.
    """

    BASE = "https://api.prop-line.com/v1"
    FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"
    FOOTBALL_DATA_CODES = {
        "soccer_epl": "PL",
        "soccer_spain_la_liga": "PD",
        "soccer_france_ligue_one": "FL1",
        "soccer_germany_bundesliga": "BL1",
        "soccer_italy_serie_a": "SA",
        "soccer_uefa_champs_league": "CL",
    }
    # PropLine accepts The Odds API soccer keys as aliases.  Keep the exact
    # canonical keys used by Oddium instead of translating them to guessed
    # PropLine names (those invalid translations caused HTTP 500 responses).
    SPORT_KEYS = {
        "soccer_epl": "soccer_epl",
        "soccer_spain_la_liga": "soccer_spain_la_liga",
        "soccer_france_ligue_one": "soccer_france_ligue_one",
        "soccer_germany_bundesliga": "soccer_germany_bundesliga",
        "soccer_italy_serie_a": "soccer_italy_serie_a",
        "soccer_uefa_champs_league": "soccer_uefa_champs_league",
    }

    # Public ESPN scoreboards are used ONLY for live scores/status. They require
    # no API key, so live-panel refreshes do not consume the PropLine quota.

    # livescoreFootball/worldcup26: only the competitions we have actually
    # verified on the public service. Unsupported keys can stall until timeout.
    OPEN_SOURCE_KEYS = {
        "soccer_epl": "eng.1",
        "soccer_spain_la_liga": "esp.1",
    }

    ESPN_KEYS = {
        "soccer_epl": "eng.1",
        "soccer_spain_la_liga": "esp.1",
        "soccer_france_ligue_one": "fra.1",
        "soccer_germany_bundesliga": "ger.1",
        "soccer_italy_serie_a": "ita.1",
        "soccer_uefa_champs_league": "uefa.champions",
    }

    FOTMOB_LEAGUE_NAMES = {
        "soccer_epl": {"premierleague"},
        "soccer_spain_la_liga": {"laliga", "laligaeasports"},
        "soccer_france_ligue_one": {"ligue1", "ligue1mcdonalds"},
        "soccer_germany_bundesliga": {"bundesliga"},
        "soccer_italy_serie_a": {"seriea"},
        "soccer_uefa_champs_league": {"championsleague", "uefachampionsleague"},
    }

    FOTMOB_COUNTRY_CODES = {
        "soccer_epl": {"ENG"},
        "soccer_spain_la_liga": {"ESP"},
        "soccer_france_ligue_one": {"FRA"},
        "soccer_germany_bundesliga": {"GER"},
        "soccer_italy_serie_a": {"ITA"},
        "soccer_uefa_champs_league": {"INT", "EUR"},
    }

    # Free v1 schedule fallback.  The public key 123 is explicitly documented by
    # TheSportsDB for free usage. We only use this for discovery, never odds.
    THESPORTSDB_LEAGUE_IDS = {
        "soccer_epl": "4328",
        "soccer_spain_la_liga": "4335",
        "soccer_france_ligue_one": "4334",
        "soccer_germany_bundesliga": "4331",
        "soccer_italy_serie_a": "4332",
        "soccer_uefa_champs_league": "4480",
    }

    # Sofascore public football feed is used as a second credential-free live
    # discovery source.  ESPN can occasionally omit a domestic competition
    # from its scoreboard while the match is actually in progress.  Filtering
    # the all-football daily feed by unique tournament keeps Ligue 1 / Serie A
    # (and the other Oddium competitions) visible without touching PropLine.
    SOFASCORE_TOURNAMENT_IDS = {
        "soccer_epl": 17,
        "soccer_spain_la_liga": 8,
        "soccer_france_ligue_one": 34,
        "soccer_germany_bundesliga": 35,
        "soccer_italy_serie_a": 23,
        "soccer_uefa_champs_league": 7,
    }

    SOFASCORE_TOURNAMENT_NAMES = {
        "soccer_epl": {"premierleague", "englishpremierleague"},
        "soccer_spain_la_liga": {"laliga", "laligaeasports", "spanishlaliga"},
        "soccer_france_ligue_one": {"ligue1", "ligue1mcdonalds", "frenchligue1"},
        "soccer_germany_bundesliga": {"bundesliga", "germanbundesliga"},
        "soccer_italy_serie_a": {"seriea", "italianseriea"},
        "soccer_uefa_champs_league": {"uefachampionsleague", "championsleague"},
    }

    SOFASCORE_COUNTRY_NAMES = {
        "soccer_epl": {"england"},
        "soccer_spain_la_liga": {"spain"},
        "soccer_france_ligue_one": {"france"},
        "soccer_germany_bundesliga": {"germany"},
        "soccer_italy_serie_a": {"italy"},
        "soccer_uefa_champs_league": set(),
    }

    def __init__(self, db: Database):
        self.db = db
        self.session: aiohttp.ClientSession | None = None
        self.last_status: int | None = None
        self.last_error: str | None = None
        self.remaining: str | None = None
        self.used: str | None = None
        self.daily_limit: str | None = None
        self._request_lock = asyncio.Lock()
        self._last_request_monotonic = 0.0
        self._cooldown_until_monotonic = 0.0
        self._cache_dir = Path(SETTINGS.api_cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._sofascore_last_fetch_monotonic = 0.0
        self._sofascore_day_rows: list[dict] = []
        self._sofascore_live_rows: list[dict] = []
        self._sofascore_live_last_fetch_monotonic = 0.0
        self._fotmob_day_rows: list[dict] = []
        self._fotmob_last_fetch_monotonic = 0.0
        self._sportsdb_rows: dict[str, list[dict]] = {}
        self._sportsdb_last_fetch: dict[str, float] = {}
        self.api_football = ApiFootballClient(self._shared_session)
        self.five_dollar = FiveDollarClient(self._shared_session)

    async def start(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=25),
                headers={"User-Agent": "Oddium/8.0"},
            )

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


    async def _shared_session(self):
        await self.start()
        assert self.session is not None
        return self.session

    async def fetch_api_football_live(self, sport_key: str, *, force: bool = False):
        """Optional Scoring-Returns-style live feed. Safe no-op without a key."""
        return await self.api_football.live_shells(sport_key, force=force)

    async def fetch_api_football_details(self, fixture_id: int, *, force: bool = False):
        return await self.api_football.fixture_details(fixture_id, force=force)

    async def fetch_five_dollar_live(self, sport_key: str, *, force: bool = False):
        return await self.five_dollar.live_shells(sport_key, force=force)

    async def fetch_five_dollar_fixtures(self, sport_key: str, *, force: bool = False):
        return await self.five_dollar.fixture_shells(sport_key, force=force)

    async def fetch_five_dollar_details(self, fixture_id: int, *, force: bool = False):
        return await self.five_dollar.fixture_details(fixture_id, force=force)

    async def fetch_five_dollar_finished(self, sport_key: str, *, days: int = 7):
        return await self.five_dollar.finished_shells(sport_key, days=days)

    @staticmethod
    def _cache_key(path: str, params: dict | None) -> str:
        import hashlib
        raw = path + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        return hashlib.sha1(raw.encode()).hexdigest()

    def _cache_path(self, path: str, params: dict | None) -> Path:
        return self._cache_dir / f"{self._cache_key(path, params)}.json"

    def _read_cache(self, path: str, params: dict | None, ttl: int, stale: bool = False):
        try:
            payload = json.loads(self._cache_path(path, params).read_text(encoding="utf-8"))
            age = time.time() - float(payload.get("saved_at", 0))
            if stale or age <= max(0, ttl):
                return payload.get("data")
        except Exception:
            return None
        return None

    def _write_cache(self, path: str, params: dict | None, data) -> None:
        try:
            p = self._cache_path(path, params)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"saved_at": time.time(), "data": data}, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(p)
        except Exception:
            pass

    async def _record_request(self):
        day = datetime.now(timezone.utc).date().isoformat()
        await self.db.execute(
            """INSERT INTO api_usage(usage_date,requests_used,last_request_at) VALUES(?,1,?)
               ON CONFLICT(usage_date) DO UPDATE SET requests_used=requests_used+1,last_request_at=excluded.last_request_at""",
            (day, utcnow_iso()),
        )

    async def quota_status(self) -> tuple[int, str]:
        day = datetime.now(timezone.utc).date().isoformat()
        row = await self.db.fetchone("SELECT requests_used FROM api_usage WHERE usage_date=?", (day,))
        used_local = int(row["requests_used"]) if row else 0
        # Prefer provider quota header because it also includes calls made outside Oddium.
        return used_local, self.remaining or "?"

    async def _respect_rate_limit(self):
        now = time.monotonic()
        if self._cooldown_until_monotonic > now:
            await asyncio.sleep(self._cooldown_until_monotonic - now)
        elapsed = time.monotonic() - self._last_request_monotonic
        spacing = max(0.1, float(SETTINGS.api_min_interval_seconds))
        if elapsed < spacing:
            await asyncio.sleep(spacing - elapsed)

    async def _get(self, path: str, params: dict | None = None, *, cache_ttl: int = 0, force: bool = False):
        if not SETTINGS.propline_api_key:
            raise OddsAPIError("PROPLINE_API_KEY manquante dans .env")

        params = dict(params or {})
        if cache_ttl > 0 and not force:
            cached = self._read_cache(path, params, cache_ttl)
            if cached is not None:
                return cached

        await self.start()
        assert self.session is not None
        headers = {"X-API-Key": SETTINGS.propline_api_key}

        async with self._request_lock:
            for attempt in range(max(1, SETTINGS.api_max_retries + 1)):
                await self._respect_rate_limit()
                try:
                    await self._record_request()
                    async with self.session.get(f"{self.BASE}{path}", params=params, headers=headers) as resp:
                        self._last_request_monotonic = time.monotonic()
                        self.last_status = resp.status
                        self.daily_limit = resp.headers.get("X-Daily-Limit", self.daily_limit)
                        self.used = resp.headers.get("X-Daily-Used", self.used)
                        self.remaining = resp.headers.get(
                            "X-Daily-Remaining",
                            resp.headers.get("X-RateLimit-Remaining", self.remaining),
                        )

                        if resp.status == 200:
                            data = await resp.json()
                            self.last_error = None
                            if cache_ttl > 0:
                                self._write_cache(path, params, data)
                            return data

                        text = await resp.text()
                        if resp.status == 429:
                            try:
                                wait = float(resp.headers.get("Retry-After", "60")) + 1
                            except ValueError:
                                wait = 61
                            self._cooldown_until_monotonic = time.monotonic() + wait
                            self.last_error = f"PropLine rate-limit: pause {wait:.0f}s"
                            if attempt < SETTINGS.api_max_retries:
                                await asyncio.sleep(wait)
                                continue

                        if resp.status >= 500 and attempt < SETTINGS.api_max_retries:
                            await asyncio.sleep((2 ** attempt) + random.random())
                            continue

                        stale = self._read_cache(path, params, cache_ttl, stale=True) if cache_ttl else None
                        if stale is not None:
                            self.last_error = f"PropLine HTTP {resp.status}; cache précédent utilisé"
                            return stale
                        raise OddsAPIError(f"PropLine HTTP {resp.status}: {text[:400]}")
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    self.last_error = str(exc)
                    if attempt < SETTINGS.api_max_retries:
                        await asyncio.sleep((2 ** attempt) + random.random())
                        continue
                    stale = self._read_cache(path, params, cache_ttl, stale=True) if cache_ttl else None
                    if stale is not None:
                        return stale
                    raise OddsAPIError(str(exc)) from exc
        raise OddsAPIError(self.last_error or "Erreur PropLine")

    async def _football_data_get(self, path: str, params: dict | None = None, *, cache_ttl: int = 0, force: bool = False):
        """GET football-data.org v4 avec cache disque Oddium."""
        if not SETTINGS.football_data_api_key:
            raise OddsAPIError("FOOTBALL_DATA_API_KEY manquante dans .env / Coolify")
        params = dict(params or {})
        cache_path = f"football-data:{path}"
        if cache_ttl > 0 and not force:
            cached = self._read_cache(cache_path, params, cache_ttl)
            if cached is not None:
                return cached
        await self.start()
        assert self.session is not None
        headers = {"X-Auth-Token": SETTINGS.football_data_api_key}
        async with self._request_lock:
            await self._respect_rate_limit()
            try:
                async with self.session.get(f"{self.FOOTBALL_DATA_BASE}{path}", params=params, headers=headers) as resp:
                    self._last_request_monotonic = time.monotonic()
                    self.last_status = resp.status
                    if resp.status == 200:
                        data = await resp.json()
                        self.last_error = None
                        if cache_ttl > 0:
                            self._write_cache(cache_path, params, data)
                        return data
                    text = await resp.text()
                    self.last_error = f"football-data.org HTTP {resp.status}: {text[:300]}"
                    stale = self._read_cache(cache_path, params, cache_ttl, stale=True) if cache_ttl else None
                    if stale is not None:
                        return stale
                    raise OddsAPIError(self.last_error)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                self.last_error = f"football-data.org: {exc}"
                stale = self._read_cache(cache_path, params, cache_ttl, stale=True) if cache_ttl else None
                if stale is not None:
                    return stale
                raise OddsAPIError(self.last_error) from exc

    @staticmethod
    def _football_data_match_to_shell(match: dict) -> dict:
        home = match.get("homeTeam") or {}
        away = match.get("awayTeam") or {}
        score = match.get("score") or {}
        full = score.get("fullTime") or {}
        status_map = {
            "SCHEDULED": "pending", "TIMED": "pending",
            "IN_PLAY": "live", "PAUSED": "halftime",
            "FINISHED": "finished", "POSTPONED": "postponed",
            "SUSPENDED": "suspended", "CANCELLED": "cancelled",
        }
        return {
            "id": f"fd:{match.get('id')}",
            "home_team": home.get("name") or home.get("shortName"),
            "away_team": away.get("name") or away.get("shortName"),
            "home_team_id": f"fd.team:{home.get('id')}" if home.get("id") is not None else None,
            "away_team_id": f"fd.team:{away.get('id')}" if away.get("id") is not None else None,
            "commence_time": match.get("utcDate"),
            "status": status_map.get(str(match.get("status") or "").upper(), "pending"),
            "home_score": full.get("home"),
            "away_score": full.get("away"),
            "status_detail": match.get("status"),
            "source": "football-data.org",
        }

    def provider_sport_key(self, sport_key: str) -> str:
        if sport_key not in self.SPORT_KEYS:
            raise OddsAPIError(
                f"Compétition non configurée sur PropLine: {COMPETITIONS.get(sport_key, {}).get('name', sport_key)}"
            )
        return self.SPORT_KEYS[sport_key]

    async def selected_bookmakers(self, *, force: bool = False) -> list[str]:
        if SETTINGS.five_dollar_api_key:
            return ["Bet365 • 5DollarFootballAPI Pro"]
        return ["Oddium Fusion • secours"]

    async def fetch_events(self, sport_key: str, *, statuses: str = "pending,live", priority: bool = False):
        # V12: 5Dollar Pro est la source canonique des calendriers et des cotes Bet365.
        if SETTINGS.five_dollar_api_key and sport_key in self.five_dollar.LEAGUES:
            return await self.fetch_five_dollar_fixtures(sport_key, force=priority)
        # Secours: football-data.org pour les calendriers si 5Dollar est absent/indisponible.
        code = self.FOOTBALL_DATA_CODES.get(sport_key)
        if code and SETTINGS.football_data_api_key:
            now = datetime.now(timezone.utc)
            params = {
                "dateFrom": now.date().isoformat(),
                "dateTo": (now + timedelta(days=30)).date().isoformat(),
            }
            payload = await self._football_data_get(
                f"/competitions/{code}/matches", params, cache_ttl=SETTINGS.fixtures_cache_seconds
            ) or {}
            return [self._football_data_match_to_shell(m) for m in (payload.get("matches") or [])]
        # Fallback PropLine si aucune cle football-data.org n'est configuree.
        provider_key = self.provider_sport_key(sport_key)
        return await self._get(f"/sports/{provider_key}/events", cache_ttl=SETTINGS.fixtures_cache_seconds, force=False) or []

    async def fetch_bulk_odds(self, sport_key: str, *, force: bool = False):
        """One API call returns h2h for every upcoming event in the competition."""
        provider_key = self.provider_sport_key(sport_key)
        ttl = await self._league_odds_ttl(sport_key)
        return await self._get(
            f"/sports/{provider_key}/odds",
            {"markets": "h2h"},
            cache_ttl=ttl,
            force=force,
        ) or []

    async def fetch_open_source_scores(self, sport_key: str, *, force: bool = False):
        """Read the public open-source livescoreFootball scoreboard when available.

        Verified coverage is currently England/Spain.  We keep ESPN as a parallel
        free fallback so unsupported competitions are never hidden.
        """
        league = self.OPEN_SOURCE_KEYS.get(sport_key)
        if not league:
            return []
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        cache_path = f"opensource_scores:{league}:{day}"
        params = {"dates": day}
        ttl = max(2, min(10, int(SETTINGS.live_poll_seconds)))
        if not force:
            cached = self._read_cache(cache_path, params, ttl)
            if cached is not None:
                return cached

        await self.start()
        assert self.session is not None
        url = f"https://worldcup26.ir/get/soccer/{league}/scoreboard"
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status != 200:
                    stale = self._read_cache(cache_path, params, ttl, stale=True)
                    return stale or []
                payload = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            stale = self._read_cache(cache_path, params, ttl, stale=True)
            return stale or []

        rows = []
        for ev in payload.get("events", []) if isinstance(payload, dict) else []:
            comps = ev.get("competitions") or []
            comp = (comps[0] if comps else {}) or {}
            sides = {}
            for c in comp.get("competitors") or []:
                ha = str(c.get("homeAway") or "").lower()
                if ha in {"home", "away"}:
                    sides[ha] = c
            if "home" not in sides or "away" not in sides:
                continue
            h, a = sides["home"], sides["away"]
            status_container = ev.get("status") or {}
            status_obj = (status_container.get("type") or {})
            status_name = str(status_obj.get("name") or status_obj.get("state") or "").upper()
            completed = bool(status_obj.get("completed"))
            detail = str(status_obj.get("detail") or status_obj.get("shortDetail") or "")
            marker = f"{status_name} {detail}".upper()
            if completed or "FINAL" in marker or "FULL_TIME" in marker:
                status = "finished"
            elif "HALF" in marker and ("TIME" in marker or "HT" in marker):
                status = "halftime"
            elif "SECOND_HALF" in marker or "2ND HALF" in marker or "2ND_HALF" in marker:
                status = "second_half"
            elif "FIRST_HALF" in marker or "1ST HALF" in marker or "1ST_HALF" in marker:
                status = "first_half"
            elif "EXTRA_TIME" in marker or "EXTRA TIME" in marker:
                status = "extra_time"
            elif "PENALT" in marker or "SHOOTOUT" in marker:
                status = "penalties"
            elif "SUSPEND" in marker or "DELAY" in marker:
                status = "suspended"
            elif "POSTPON" in marker:
                status = "postponed"
            elif "CANCEL" in marker:
                status = "cancelled"
            elif any(x in marker for x in ("IN_PROGRESS", "IN PROGRESS", "PLAYING", "LIVE")):
                status = "live"
            else:
                status = "scheduled"

            def score_of(side):
                raw = side.get("score")
                if isinstance(raw, dict):
                    raw = raw.get("value") or raw.get("displayValue")
                try:
                    return int(float(raw)) if raw not in (None, "") else None
                except (TypeError, ValueError):
                    return None

            hteam = h.get("team") or {}
            ateam = a.get("team") or {}
            rows.append({
                "id": str(ev.get("id") or comp.get("id") or ""),
                "sport_key": sport_key,
                "home_team": hteam.get("displayName") or hteam.get("name"),
                "away_team": ateam.get("displayName") or ateam.get("name"),
                "home_team_id": f"opensource.soccer:{hteam.get('id')}" if hteam.get("id") else None,
                "away_team_id": f"opensource.soccer:{ateam.get('id')}" if ateam.get("id") else None,
                "commence_time": ev.get("date") or comp.get("date"),
                "status": status,
                "home_score": score_of(h),
                "away_score": score_of(a),
                "status_detail": detail,
                "live_clock": status_container.get("displayClock") or status_container.get("clock"),
                "period": status_container.get("period"),
                "source": "livescoreFootball",
            })
        self._write_cache(cache_path, params, rows)
        return rows

    async def fetch_espn_scores(self, sport_key: str, *, force: bool = False, date=None):
        """Fetch today's scoreboard from ESPN's public JSON endpoint.

        This endpoint is credential-free and is used for the permanent live panel,
        so refreshing a score does not spend PropLine requests. The returned rows
        are normalized to the same shell consumed by ``parse_score_shell``.
        """
        league = self.ESPN_KEYS.get(sport_key)
        if not league:
            return []

        day = (date or datetime.now(timezone.utc).date())
        if hasattr(day, "strftime"):
            day = day.strftime("%Y%m%d")
        else:
            day = str(day).replace("-", "")
        cache_path = f"espn_scores:{league}:{day}"
        params = {"dates": day}
        ttl = min(45, max(15, int(SETTINGS.scores_cache_seconds)))
        if not force:
            cached = self._read_cache(cache_path, params, ttl)
            if cached is not None:
                return cached

        await self.start()
        assert self.session is not None
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard"
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status != 200:
                    stale = self._read_cache(cache_path, params, ttl, stale=True)
                    return stale or []
                payload = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            stale = self._read_cache(cache_path, params, ttl, stale=True)
            return stale or []

        rows = []
        for ev in payload.get("events", []) if isinstance(payload, dict) else []:
            comps = ev.get("competitions") or []
            if not comps:
                continue
            comp = comps[0] or {}
            sides = {}
            for c in comp.get("competitors") or []:
                ha = str(c.get("homeAway") or "").lower()
                if ha in {"home", "away"}:
                    sides[ha] = c
            if "home" not in sides or "away" not in sides:
                continue
            h, a = sides["home"], sides["away"]
            status_container = ev.get("status") or {}
            status_obj = (status_container.get("type") or {})
            status_name = str(status_obj.get("name") or "").upper()
            completed = bool(status_obj.get("completed"))
            detail = str(status_obj.get("detail") or status_obj.get("shortDetail") or "")
            marker = f"{status_name} {detail}".upper()
            if completed or "FINAL" in marker or "FULL_TIME" in marker:
                status = "finished"
            elif "HALF" in marker and ("TIME" in marker or "HT" in marker):
                status = "halftime"
            elif "SECOND_HALF" in marker or "2ND HALF" in marker or "2ND_HALF" in marker:
                status = "second_half"
            elif "FIRST_HALF" in marker or "1ST HALF" in marker or "1ST_HALF" in marker:
                status = "first_half"
            elif "EXTRA_TIME" in marker or "EXTRA TIME" in marker:
                status = "extra_time"
            elif "PENALT" in marker or "SHOOTOUT" in marker:
                status = "penalties"
            elif "SUSPEND" in marker or "DELAY" in marker:
                status = "suspended"
            elif "POSTPON" in marker:
                status = "postponed"
            elif "CANCEL" in marker:
                status = "cancelled"
            elif any(x in marker for x in ("IN_PROGRESS", "IN PROGRESS", "PLAYING", "LIVE")):
                status = "live"
            else:
                status = "scheduled"

            def score_of(side):
                raw = side.get("score")
                if isinstance(raw, dict):
                    raw = raw.get("value") or raw.get("displayValue")
                try:
                    return int(float(raw)) if raw not in (None, "") else None
                except (TypeError, ValueError):
                    return None

            hteam = h.get("team") or {}
            ateam = a.get("team") or {}
            rows.append({
                "id": str(ev.get("id") or comp.get("id") or ""),
                "sport_key": sport_key,
                "home_team": hteam.get("displayName") or hteam.get("name"),
                "away_team": ateam.get("displayName") or ateam.get("name"),
                "home_team_id": f"espn.soccer:{hteam.get('id')}" if hteam.get("id") else None,
                "away_team_id": f"espn.soccer:{ateam.get('id')}" if ateam.get("id") else None,
                "commence_time": ev.get("date") or comp.get("date"),
                "status": status,
                "home_score": score_of(h),
                "away_score": score_of(a),
                "status_detail": detail,
                "live_clock": status_container.get("displayClock") or status_container.get("clock"),
                "period": status_container.get("period"),
                "source": "ESPN",
            })

        self._write_cache(cache_path, params, rows)
        return rows

    async def fetch_fotmob_scores(self, sport_key: str, *, force: bool = False):
        """Credential-free daily/live fallback using FotMob."""
        wanted_names = self.FOTMOB_LEAGUE_NAMES.get(sport_key)
        if not wanted_names:
            return []

        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        cache_path = f"fotmob_matches:{day}"
        params = {"date": day, "timezone": "Europe/Paris", "ccode3": "FRA"}
        ttl = max(3, min(12, int(SETTINGS.live_poll_seconds)))
        now_mono = time.monotonic()

        if self._fotmob_last_fetch_monotonic > 0 and (now_mono - self._fotmob_last_fetch_monotonic) < 30.0:
            leagues = self._fotmob_day_rows
        else:
            leagues = None
            if not force:
                cached = self._read_cache(cache_path, params, ttl)
                if cached is not None:
                    leagues = cached
            if leagues is None:
                await self.start()
                assert self.session is not None
                url = "https://www.fotmob.com/api/matches"
                try:
                    async with self.session.get(
                        url,
                        params=params,
                        headers={
                            "Accept": "application/json, text/plain, */*",
                            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
                            "Referer": "https://www.fotmob.com/",
                            "Sec-Fetch-Dest": "empty",
                            "Sec-Fetch-Mode": "cors",
                            "Sec-Fetch-Site": "same-origin",
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
                        },
                    ) as resp:
                        if resp.status == 200:
                            payload = await resp.json(content_type=None)
                            leagues = payload.get("leagues", []) if isinstance(payload, dict) else []
                            self._write_cache(cache_path, params, leagues)
                        else:
                            import logging
                            logging.getLogger("oddium").warning("FotMob HTTP %s pour le flux live", resp.status)
                            leagues = self._read_cache(cache_path, params, ttl, stale=True) or []
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                    import logging
                    logging.getLogger("oddium").warning("FotMob indisponible: %s", exc)
                    leagues = self._read_cache(cache_path, params, ttl, stale=True) or []
            self._fotmob_day_rows = list(leagues or [])
            self._fotmob_last_fetch_monotonic = time.monotonic()

        wanted_ccodes = self.FOTMOB_COUNTRY_CODES.get(sport_key, set())
        rows: list[dict] = []
        for league in leagues or []:
            if not isinstance(league, dict):
                continue
            league_name = self._norm(league.get("name") or league.get("leagueName") or league.get("parentLeagueName"))
            ccode = str(league.get("ccode") or "").upper()
            name_match = league_name in wanted_names or any(alias in league_name for alias in wanted_names)
            if sport_key == "soccer_uefa_champs_league":
                if not name_match:
                    continue
            elif not name_match:
                continue
            elif wanted_ccodes and ccode and ccode not in wanted_ccodes:
                continue

            for match in league.get("matches") or []:
                if not isinstance(match, dict):
                    continue
                home = match.get("home") or {}
                away = match.get("away") or {}
                st = match.get("status") or {}
                started = bool(st.get("started"))
                finished = bool(st.get("finished"))
                cancelled = bool(st.get("cancelled"))
                reason = st.get("reason") or {}
                if isinstance(reason, dict):
                    reason_short = str(reason.get("short") or "")
                    reason_long = str(reason.get("long") or "")
                else:
                    reason_short, reason_long = str(reason or ""), ""
                lt = st.get("liveTime")
                if isinstance(lt, dict):
                    live_clock = lt.get("short") or lt.get("long")
                else:
                    live_clock = lt if isinstance(lt, str) else None
                detail = str(live_clock or reason_long or reason_short or st.get("scoreStr") or "")
                marker2 = f"{reason_short} {reason_long} {detail}".lower()
                if cancelled:
                    status = "cancelled"
                elif finished:
                    status = "finished"
                elif "postpon" in marker2:
                    status = "postponed"
                elif "half" in marker2 and ("time" in marker2 or "ht" in marker2):
                    status = "halftime"
                elif "penalt" in marker2:
                    status = "penalties"
                elif "extra" in marker2:
                    status = "extra_time"
                elif "suspend" in marker2 or "delay" in marker2 or "interrupt" in marker2:
                    status = "suspended"
                elif started:
                    status = "live"
                else:
                    status = "scheduled"

                def _score(side):
                    raw = side.get("score")
                    try:
                        return int(float(raw)) if raw not in (None, "") else None
                    except (TypeError, ValueError):
                        return None

                rows.append({
                    "id": f"fotmob:{match.get('id')}",
                    "sport_key": sport_key,
                    "home_team": home.get("name"),
                    "away_team": away.get("name"),
                    "home_team_id": f"fotmob.team:{home.get('id')}" if home.get("id") else None,
                    "away_team_id": f"fotmob.team:{away.get('id')}" if away.get("id") else None,
                    "commence_time": st.get("utcTime") or match.get("timeTS") or match.get("time"),
                    "status": status,
                    "home_score": _score(home),
                    "away_score": _score(away),
                    "status_detail": detail,
                    "live_clock": live_clock,
                    "period": st.get("period"),
                    "source": "FotMob",
                })
        return rows

    async def fetch_thesportsdb_scores(self, sport_key: str, *, force: bool = False):
        """Free discovery fallback via TheSportsDB v1 events-by-day.

        Free v1 does not promise premium-grade live latency, so this source is a
        safety net for missing competitions. Calls are throttled per league to
        preserve the documented free rate limit.
        """
        league_id = self.THESPORTSDB_LEAGUE_IDS.get(sport_key)
        if not league_id:
            return []
        now_mono = time.monotonic()
        last = self._sportsdb_last_fetch.get(sport_key, 0.0)
        # Discovery cadence only; do not burn 6 requests every 5 seconds.
        if last > 0 and (now_mono - last) < 60.0:
            return list(self._sportsdb_rows.get(sport_key, []))
        self._sportsdb_last_fetch[sport_key] = now_mono
        await self.start()
        assert self.session is not None
        day = datetime.now(timezone.utc).date().isoformat()
        url = "https://www.thesportsdb.com/api/v1/json/123/eventsday.php"
        params = {"d": day, "s": "Soccer", "l": league_id}
        try:
            async with self.session.get(url, params=params, headers={"Accept":"application/json"}) as resp:
                if resp.status != 200:
                    self._sportsdb_rows[sport_key] = []
                    return []
                payload = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return list(self._sportsdb_rows.get(sport_key, []))

        rows = []
        for ev in (payload.get("events") or []) if isinstance(payload, dict) else []:
            if not isinstance(ev, dict):
                continue
            status_raw = str(ev.get("strStatus") or "").lower()
            hs, aas = ev.get("intHomeScore"), ev.get("intAwayScore")
            try: hs = int(hs) if hs not in (None, "") else None
            except (TypeError, ValueError): hs = None
            try: aas = int(aas) if aas not in (None, "") else None
            except (TypeError, ValueError): aas = None
            if any(x in status_raw for x in ("match finished", "finished", "ft", "aet")):
                status = "finished"
            elif "half" in status_raw or status_raw == "ht":
                status = "halftime"
            elif "postpon" in status_raw:
                status = "postponed"
            elif "cancel" in status_raw:
                status = "cancelled"
            elif any(x in status_raw for x in ("1h", "2h", "live", "in progress", "playing")):
                status = "live"
            else:
                # The free feed may expose a minute in strProgress even when
                # strStatus is sparse.
                progress = str(ev.get("strProgress") or "")
                status = "live" if progress and progress not in {"0", "-"} else "scheduled"
            dt = ev.get("strTimestamp") or None
            if not dt and ev.get("dateEvent"):
                dt = f"{ev.get('dateEvent')}T{ev.get('strTime') or '00:00:00'}Z"
            rows.append({
                "id": f"sportsdb:{ev.get('idEvent')}",
                "sport_key": sport_key,
                "home_team": ev.get("strHomeTeam"),
                "away_team": ev.get("strAwayTeam"),
                "home_team_id": f"sportsdb.team:{ev.get('idHomeTeam')}" if ev.get("idHomeTeam") else None,
                "away_team_id": f"sportsdb.team:{ev.get('idAwayTeam')}" if ev.get("idAwayTeam") else None,
                "commence_time": dt,
                "status": status,
                "home_score": hs,
                "away_score": aas,
                "status_detail": ev.get("strStatus") or ev.get("strProgress") or "",
                "live_clock": ev.get("strProgress"),
                "period": None,
                "source": "TheSportsDB",
            })
        self._sportsdb_rows[sport_key] = rows
        return list(rows)

    async def fetch_sofascore_live_scores(self, sport_key: str, *, force: bool = False):
        """Primary credential-free live discovery from SofaScore's global live board.

        Unlike the daily scheduled-events feed this endpoint contains only matches
        that are actually in play, so Oddium does not depend on a date-board being
        complete. One HTTP response is shared by all six competitions in a loop.
        """
        tournament_id = self.SOFASCORE_TOURNAMENT_IDS.get(sport_key)
        if tournament_id is None:
            return []

        ttl = max(3, min(10, int(SETTINGS.live_poll_seconds)))
        cache_path = "sofascore_football_live"
        params: dict = {}
        now_mono = time.monotonic()

        if self._sofascore_live_last_fetch_monotonic > 0 and (now_mono - self._sofascore_live_last_fetch_monotonic) < 30.0:
            all_rows = self._sofascore_live_rows
        else:
            all_rows = None
            if not force:
                cached = self._read_cache(cache_path, params, ttl)
                if cached is not None:
                    all_rows = cached
            if all_rows is None:
                await self.start()
                assert self.session is not None
                url = "https://api.sofascore.com/api/v1/sport/football/events/live"
                try:
                    async with self.session.get(
                        url,
                        headers={
                            "Accept": "application/json",
                            "Origin": "https://www.sofascore.com",
                            "Referer": "https://www.sofascore.com/",
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
                        },
                    ) as resp:
                        if resp.status == 200:
                            payload = await resp.json()
                            all_rows = payload.get("events", []) if isinstance(payload, dict) else []
                            self._write_cache(cache_path, params, all_rows)
                        else:
                            all_rows = self._read_cache(cache_path, params, ttl, stale=True) or []
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                    all_rows = self._read_cache(cache_path, params, ttl, stale=True) or []
            self._sofascore_live_rows = list(all_rows or [])
            self._sofascore_live_last_fetch_monotonic = time.monotonic()

        return self._normalize_sofascore_rows(all_rows, sport_key)

    def _normalize_sofascore_rows(self, all_rows, sport_key: str) -> list[dict]:
        tournament_id = self.SOFASCORE_TOURNAMENT_IDS.get(sport_key)
        wanted_names = self.SOFASCORE_TOURNAMENT_NAMES.get(sport_key, set())
        rows: list[dict] = []
        for ev in all_rows or []:
            if not isinstance(ev, dict):
                continue
            tournament = ev.get("tournament") or {}
            unique = tournament.get("uniqueTournament") or {}
            try:
                uid = int(unique.get("id"))
            except (TypeError, ValueError):
                uid = None
            # Match by both id and normalized tournament name/slug. This protects
            # Oddium if SofaScore changes an internal id between seasons.
            name_norm = self._norm(unique.get("name") or unique.get("slug") or tournament.get("name") or tournament.get("slug"))
            category = tournament.get("category") or unique.get("category") or {}
            country_norm = self._norm(category.get("name") or category.get("slug"))
            wanted_countries = self.SOFASCORE_COUNTRY_NAMES.get(sport_key, set())
            # V16.1: strict tournament identity. The previous fuzzy name/country
            # fallback could classify unrelated competitions as La Liga/UCL when a
            # provider payload used ambiguous tournament metadata. SofaScore's unique
            # tournament id is stable for the six competitions Oddium supports.
            by_id = uid == tournament_id
            if not by_id:
                continue

            home = ev.get("homeTeam") or {}
            away = ev.get("awayTeam") or {}
            status_obj = ev.get("status") or {}
            status_type = str(status_obj.get("type") or "").lower()
            status_desc = str(status_obj.get("description") or "")
            marker = f"{status_type} {status_desc}".lower()
            if status_type in {"finished", "afterpenalties", "afterextra"} or "finished" in marker:
                status = "finished"
            elif "halftime" in marker or "half time" in marker:
                status = "halftime"
            elif "penalt" in marker:
                status = "penalties"
            elif "extra" in marker:
                status = "extra_time"
            elif "suspend" in marker or "interrupt" in marker or "delay" in marker:
                status = "suspended"
            elif "postpon" in marker:
                status = "postponed"
            elif "cancel" in marker:
                status = "cancelled"
            elif status_type in {"inprogress", "live"}:
                if any(x in marker for x in ("2nd", "second half")):
                    status = "second_half"
                elif any(x in marker for x in ("1st", "first half")):
                    status = "first_half"
                else:
                    status = "live"
            else:
                status = "scheduled"

            def ss_score(obj):
                if not isinstance(obj, dict):
                    return None
                raw = obj.get("current")
                if raw is None:
                    raw = obj.get("display") or obj.get("normaltime")
                try:
                    return int(float(raw)) if raw not in (None, "") else None
                except (TypeError, ValueError):
                    return None

            kickoff = None
            try:
                kickoff = datetime.fromtimestamp(int(ev.get("startTimestamp")), tz=timezone.utc).isoformat()
            except (TypeError, ValueError, OSError):
                pass

            live_clock = None
            if status in {"live", "first_half", "second_half", "extra_time"}:
                time_obj = ev.get("time") or {}
                try:
                    period_start = int(time_obj.get("currentPeriodStartTimestamp"))
                    # SofaScore stores `initial` as elapsed SECONDS at the start of
                    # the current period (2700 for the second half), not minutes.
                    # The old engine treated 2700 as 2700 minutes and rejected the
                    # clock, which is why many LIVE rows had no timer.
                    initial_seconds = int(time_obj.get("initial") or 0)
                    elapsed_seconds = max(0, int(time.time()) - period_start)
                    minute = (initial_seconds + elapsed_seconds) // 60
                    # Football displays the first running minute as 1', not 0'.
                    if status in {"live", "first_half", "second_half", "extra_time"} and minute == 0:
                        minute = 1
                    if 0 <= minute <= 130:
                        live_clock = f"{minute}'"
                except (TypeError, ValueError):
                    live_clock = None

            rows.append({
                "id": f"sofa:{ev.get('id')}",
                "sport_key": sport_key,
                "home_team": home.get("name") or home.get("shortName"),
                "away_team": away.get("name") or away.get("shortName"),
                "home_team_id": f"sofa.team:{home.get('id')}" if home.get("id") else None,
                "away_team_id": f"sofa.team:{away.get('id')}" if away.get("id") else None,
                "commence_time": kickoff,
                "status": status,
                "home_score": ss_score(ev.get("homeScore") or {}),
                "away_score": ss_score(ev.get("awayScore") or {}),
                "status_detail": status_desc or status_type,
                "live_clock": live_clock,
                "period": (ev.get("time") or {}).get("periodLength"),
                "source": "Sofascore Live",
            })
        return rows

    async def fetch_sofascore_scores(self, sport_key: str, *, force: bool = False, date=None):
        """Credential-free fallback for live discovery across all Oddium leagues.

        The endpoint returns every football event for the local calendar day.
        One shared in-memory fetch is reused across all six competitions so a
        discovery pass does not become six HTTP requests.  The method only
        returns the tournament requested by ``sport_key``.
        """
        tournament_id = self.SOFASCORE_TOURNAMENT_IDS.get(sport_key)
        if tournament_id is None:
            return []

        # Query UTC yesterday/today/tomorrow around midnight boundaries.  The
        # currently requested daily feed is normally enough, but using the local
        # UTC date is safe for all European competitions at match time.
        day = date or datetime.now(timezone.utc).date()
        day = day.isoformat() if hasattr(day, "isoformat") else str(day)
        cache_path = f"sofascore_football_day:{day}"
        params: dict = {}
        ttl = max(3, min(12, int(SETTINGS.live_poll_seconds)))
        now_mono = time.monotonic()

        # Even when several competitions request force=True in one score loop,
        # hit Sofascore at most once every ~3 seconds and share the payload.
        if date is None and self._sofascore_last_fetch_monotonic > 0 and (now_mono - self._sofascore_last_fetch_monotonic) < 30.0:
            all_rows = self._sofascore_day_rows
        else:
            all_rows = None
            if not force:
                cached = self._read_cache(cache_path, params, ttl)
                if cached is not None:
                    all_rows = cached
            if all_rows is None:
                await self.start()
                assert self.session is not None
                url = f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{day}"
                try:
                    async with self.session.get(
                        url,
                        headers={
                            "Accept": "application/json",
                            "Referer": "https://www.sofascore.com/football",
                            "User-Agent": "Mozilla/5.0 Oddium/9.0",
                        },
                    ) as resp:
                        if resp.status == 200:
                            payload = await resp.json()
                            all_rows = payload.get("events", []) if isinstance(payload, dict) else []
                            self._write_cache(cache_path, params, all_rows)
                        else:
                            all_rows = self._read_cache(cache_path, params, ttl, stale=True) or []
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                    all_rows = self._read_cache(cache_path, params, ttl, stale=True) or []

            self._sofascore_day_rows = list(all_rows or [])
            self._sofascore_last_fetch_monotonic = time.monotonic()

        wanted_names = self.SOFASCORE_TOURNAMENT_NAMES.get(sport_key, set())
        rows: list[dict] = []
        for ev in all_rows or []:
            if not isinstance(ev, dict):
                continue
            tournament = ev.get("tournament") or {}
            unique = tournament.get("uniqueTournament") or {}
            try:
                uid = int(unique.get("id"))
            except (TypeError, ValueError):
                uid = None
            name_norm = self._norm(unique.get("name") or tournament.get("name"))
            if uid != tournament_id and name_norm not in wanted_names:
                continue

            home = ev.get("homeTeam") or {}
            away = ev.get("awayTeam") or {}
            status_obj = ev.get("status") or {}
            status_type = str(status_obj.get("type") or "").lower()
            status_desc = str(status_obj.get("description") or "")
            marker = f"{status_type} {status_desc}".lower()

            if status_type in {"finished", "afterpenalties", "afterextra"} or "finished" in marker:
                status = "finished"
            elif "halftime" in marker or "half time" in marker:
                status = "halftime"
            elif "penalt" in marker:
                status = "penalties"
            elif "extra" in marker:
                status = "extra_time"
            elif "suspend" in marker or "interrupt" in marker or "delay" in marker:
                status = "suspended"
            elif "postpon" in marker:
                status = "postponed"
            elif "cancel" in marker:
                status = "cancelled"
            elif status_type in {"inprogress", "live"}:
                # Preserve half information where Sofascore exposes it.
                if any(x in marker for x in ("2nd", "second half")):
                    status = "second_half"
                elif any(x in marker for x in ("1st", "first half")):
                    status = "first_half"
                else:
                    status = "live"
            else:
                status = "scheduled"

            def ss_score(obj):
                if not isinstance(obj, dict):
                    return None
                raw = obj.get("current")
                if raw is None:
                    raw = obj.get("display") or obj.get("normaltime")
                try:
                    return int(float(raw)) if raw not in (None, "") else None
                except (TypeError, ValueError):
                    return None

            kickoff = None
            try:
                ts = int(ev.get("startTimestamp"))
                kickoff = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            except (TypeError, ValueError, OSError):
                pass

            live_clock = None
            if status in {"live", "first_half", "second_half", "extra_time"}:
                time_obj = ev.get("time") or {}
                try:
                    period_start = int(time_obj.get("currentPeriodStartTimestamp"))
                    # SofaScore stores `initial` as elapsed SECONDS at the start of
                    # the current period (2700 for the second half), not minutes.
                    # The old engine treated 2700 as 2700 minutes and rejected the
                    # clock, which is why many LIVE rows had no timer.
                    initial_seconds = int(time_obj.get("initial") or 0)
                    elapsed_seconds = max(0, int(time.time()) - period_start)
                    minute = (initial_seconds + elapsed_seconds) // 60
                    # Football displays the first running minute as 1', not 0'.
                    if status in {"live", "first_half", "second_half", "extra_time"} and minute == 0:
                        minute = 1
                    if 0 <= minute <= 130:
                        live_clock = f"{minute}'"
                except (TypeError, ValueError):
                    live_clock = None

            rows.append({
                "id": f"sofa:{ev.get('id')}",
                "sport_key": sport_key,
                "home_team": home.get("name") or home.get("shortName"),
                "away_team": away.get("name") or away.get("shortName"),
                "home_team_id": f"sofa.team:{home.get('id')}" if home.get("id") else None,
                "away_team_id": f"sofa.team:{away.get('id')}" if away.get("id") else None,
                "commence_time": kickoff,
                "status": status,
                "home_score": ss_score(ev.get("homeScore") or {}),
                "away_score": ss_score(ev.get("awayScore") or {}),
                "status_detail": status_desc or status_type,
                "live_clock": live_clock,
                "period": (ev.get("time") or {}).get("periodLength"),
                "source": "Sofascore",
            })
        return rows

    async def fetch_scores(self, sport_key: str, *, days_from: int = 3, force: bool = False):
        """Scores via football-data.org; fallback PropLine si necessaire."""
        code = self.FOOTBALL_DATA_CODES.get(sport_key)
        if code and SETTINGS.football_data_api_key:
            now = datetime.now(timezone.utc)
            params = {
                "dateFrom": (now - timedelta(days=max(1, min(int(days_from), 7)))).date().isoformat(),
                "dateTo": now.date().isoformat(),
            }
            payload = await self._football_data_get(
                f"/competitions/{code}/matches", params,
                cache_ttl=SETTINGS.scores_cache_seconds, force=force
            ) or {}
            return [self._football_data_match_to_shell(m) for m in (payload.get("matches") or [])]
        provider_key = self.provider_sport_key(sport_key)
        return await self._get(
            f"/sports/{provider_key}/scores",
            {"days_from": max(1, min(int(days_from), 7))},
            cache_ttl=SETTINGS.scores_cache_seconds, force=force,
        ) or []

    async def _league_odds_ttl(self, sport_key: str) -> int:
        now = datetime.now(timezone.utc)
        row = await self.db.fetchone(
            """SELECT commence_time FROM matches
               WHERE sport_key=? AND completed=0 AND cancelled=0 AND commence_time>?
               ORDER BY commence_time ASC LIMIT 1""",
            (sport_key, now.isoformat()),
        )
        if not row:
            return SETTINGS.odds_cache_far_seconds
        try:
            kickoff = datetime.fromisoformat(str(row["commence_time"]).replace("Z", "+00:00"))
        except Exception:
            return SETTINGS.odds_cache_far_seconds
        delta = (kickoff - now).total_seconds()
        if delta <= 3600:
            return SETTINGS.odds_cache_hot_seconds
        if delta <= 6 * 3600:
            return SETTINGS.odds_cache_near_seconds
        if delta <= 24 * 3600:
            return SETTINGS.odds_cache_day_seconds
        return SETTINGS.odds_cache_far_seconds

    @staticmethod
    def parse_event_shell(raw: dict, sport_key: str) -> dict | None:
        if not isinstance(raw, dict):
            return None
        fid = raw.get("id")
        home = raw.get("home_team")
        away = raw.get("away_team")
        kickoff = raw.get("commence_time")
        if fid is None or not home or not away or not kickoff:
            return None
        return {
            "event_id": str(fid),
            "sport_key": sport_key,
            "competition_name": COMPETITIONS.get(sport_key, {}).get("name", sport_key),
            "home_team": str(home),
            "away_team": str(away),
            "home_team_id": raw.get("home_team_id") or raw.get("home_team_key"),
            "away_team_id": raw.get("away_team_id") or raw.get("away_team_key"),
            "commence_time": str(kickoff),
            "status": "pending",
            "home_score": None,
            "away_score": None,
            "five_dollar_fixture_id": raw.get("five_dollar_fixture_id"),
            "provider_odds": raw.get("provider_odds"),
        }

    @staticmethod
    def parse_score_shell(raw: dict, sport_key: str) -> dict | None:
        if not isinstance(raw, dict):
            return None
        fid = raw.get("id")
        if fid is None:
            return None
        status_raw = str(raw.get("status") or "").strip().lower()
        status_norm = status_raw.replace("-", "_").replace(" ", "_")
        status_map = {
            "final": "finished", "finished": "finished", "complete": "finished", "completed": "finished",
            "in_progress": "live", "inprogress": "live", "playing": "live", "live": "live",
            "first_half": "first_half", "second_half": "second_half", "halftime": "halftime",
            "half_time": "halftime", "extra_time": "extra_time", "penalties": "penalties",
            "shootout": "penalties", "suspended": "suspended", "delayed": "suspended",
            "scheduled": "pending", "pending": "pending",
            "cancelled": "cancelled", "canceled": "cancelled", "postponed": "postponed",
        }

        # Current PropLine docs expose home_score/away_score at top level. Keep a
        # tolerant fallback for alternate score payloads so a harmless provider
        # shape change does not make the Discord panel display “– - –”.
        home_score = raw.get("home_score")
        away_score = raw.get("away_score")
        score_obj = raw.get("score") or raw.get("scores")
        if isinstance(score_obj, dict):
            if home_score is None:
                home_score = score_obj.get("home")
                if isinstance(home_score, dict):
                    home_score = home_score.get("score") or home_score.get("total")
            if away_score is None:
                away_score = score_obj.get("away")
                if isinstance(away_score, dict):
                    away_score = away_score.get("score") or away_score.get("total")

        merged = raw.get("merged_from_event_ids") or []
        if not isinstance(merged, list):
            merged = []
        return {
            "event_id": str(fid),
            "merged_from_event_ids": [str(x) for x in merged],
            "sport_key": sport_key,
            "home_team": raw.get("home_team"),
            "away_team": raw.get("away_team"),
            "home_team_id": raw.get("home_team_id") or raw.get("home_team_key"),
            "away_team_id": raw.get("away_team_id") or raw.get("away_team_key"),
            "commence_time": raw.get("commence_time"),
            "status": status_map.get(status_norm, status_norm or "pending"),
            "home_score": home_score,
            "away_score": away_score,
            "live_clock": raw.get("live_clock") or raw.get("display_clock") or raw.get("clock"),
            "period": raw.get("period"),
            "status_detail": raw.get("status_detail") or raw.get("detail"),
            "source": raw.get("source") or "PropLine",
            "provider_events": raw.get("provider_events") or [],
            "api_football_fixture_id": raw.get("api_football_fixture_id"),
            "five_dollar_fixture_id": raw.get("five_dollar_fixture_id"),
            "provider_odds": raw.get("provider_odds"),
            "provider_stats": raw.get("provider_stats") or {},
            "competition_name": raw.get("competition_name"),
            "status_code": raw.get("status_code"),
            "status_reason": raw.get("status_reason"),
            "corners": raw.get("corners") or {},
            "cards": raw.get("cards") or {},
            "provider_payload": raw.get("provider_payload") or {},
        }

    @staticmethod
    def _norm(value: str | None) -> str:
        import re
        import unicodedata
        text = unicodedata.normalize("NFKD", str(value or ""))
        text = "".join(c for c in text if not unicodedata.combining(c)).lower()
        return re.sub(r"[^a-z0-9]", "", text)

    @staticmethod
    def _american_to_decimal(price) -> float | None:
        try:
            p = float(price)
        except (TypeError, ValueError):
            return None
        if p == 0:
            return None
        # PropLine quotes American odds. Keep deterministic conversion only;
        # no statistical adjustment/no-vig manipulation is applied.
        if p > 0:
            return round(1.0 + p / 100.0, 4)
        return round(1.0 + 100.0 / abs(p), 4)

    def pick_1x2(self, payload: dict) -> dict | None:
        """Pick one real bookmaker's complete h2h board; never average prices."""
        if not isinstance(payload, dict):
            return None
        home = str(payload.get("home_team") or "")
        away = str(payload.get("away_team") or "")
        if not home or not away:
            return None
        home_n, away_n = self._norm(home), self._norm(away)
        prefs = [p.strip().lower() for p in SETTINGS.propline_bookmakers.split(",") if p.strip()]
        books = payload.get("bookmakers") or []
        if not isinstance(books, list):
            return None
        rank = {key: i for i, key in enumerate(prefs)}
        ordered = sorted(books, key=lambda b: rank.get(str(b.get("key") or "").lower(), 10_000))

        for book in ordered:
            markets = book.get("markets") or []
            for market in markets:
                if str(market.get("key") or "").lower() != "h2h":
                    continue
                found: dict[str, float] = {}
                for out in market.get("outcomes") or []:
                    name = str(out.get("name") or "")
                    n = self._norm(name)
                    dec = self._american_to_decimal(out.get("price"))
                    if dec is None or dec <= 1.0:
                        continue
                    if n in {"draw", "tie", "x"}:
                        found["draw"] = dec
                    elif n == home_n or home_n in n or n in home_n:
                        found["home"] = dec
                    elif n == away_n or away_n in n or n in away_n:
                        found["away"] = dec
                if {"home", "draw", "away"}.issubset(found):
                    title = str(book.get("title") or book.get("key") or "PropLine")
                    return {
                        "home_odd": found["home"],
                        "draw_odd": found["draw"],
                        "away_odd": found["away"],
                        "bookmaker": f"{title} • PropLine",
                        "last_odds_update": market.get("last_update") or book.get("last_update") or utcnow_iso(),
                        "source": "propline-h2h",
                    }
        return None

    async def calculate_odds(self, sport_key: str, home_team_id=None, away_team_id=None, *, event_id: str | None = None) -> dict | None:
        """Compatibility helper. Uses the cached bulk board, never one call per event."""
        if not event_id:
            return None
        board = await self.fetch_bulk_odds(sport_key)
        for event in board:
            if str(event.get("id")) == str(event_id):
                return self.pick_1x2(event)
        return None

    async def fetch_odds_multi(self, event_ids, *, priority: bool = False):
        return []

    async def fetch_matches(self, sport_key: str, *, season: int | None = None, force: bool = False):
        return await self.fetch_events(sport_key)

    async def fetch_standings(self, sport_key: str, *, season: int | None = None, force: bool = False):
        # V12: use the paid 5Dollar feed first so the odds fallback model remains
        # self-contained around the user's Pro subscription. football-data.org is
        # retained only as a resilience source.
        if SETTINGS.five_dollar_api_key:
            try:
                payload = await self.five_dollar.standings(sport_key, season=season)
                if payload:
                    return payload
            except Exception:
                pass
        code = self.FOOTBALL_DATA_CODES.get(sport_key)
        if not code or not SETTINGS.football_data_api_key:
            return {}
        payload = await self._football_data_get(
            f"/competitions/{code}/standings", cache_ttl=SETTINGS.standings_cache_seconds, force=force
        ) or {}
        return payload
