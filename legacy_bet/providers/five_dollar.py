from __future__ import annotations

import asyncio
import time
import json
import hashlib
from collections import deque
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from config import SETTINGS
from .intelligence import FiveDollarIntelligence
from .guardian import FiveDollarGuardian


@dataclass(frozen=True)
class FiveDollarLeague:
    ids: tuple[int, ...]
    name: str
    country: str
    aliases: tuple[str, ...] = ()


class FiveDollarClient:
    """5DollarFootballAPI Pro adapter used as Oddium's primary football feed.

    Native /v1 is used instead of emulating another provider. One global live
    request carries scores + Bet365 odds + timeline + statistics. Upcoming
    fixture lists are cached by competition so Oddium stays comfortably below
    the Pro 10 requests/min rate limit.
    """

    BASE = "https://api.5dollarfootballapi.com/v1"

    LEAGUES = {
        # Published ids are only bootstraps. Accounts created before September 2026
        # may use the legacy id family, so `_ensure_league_ids()` resolves the user's
        # own ids from /leagues and replaces these values in memory.
        "soccer_epl": FiveDollarLeague((3120672213,), "Premier League", "GB-ENG", ("EPL",)),
        "soccer_spain_la_liga": FiveDollarLeague((1810150156, 14), "La Liga", "ES", ("Primera Division", "Spain La Liga", "LaLiga", "LaLiga EA Sports")),
        "soccer_france_ligue_one": FiveDollarLeague((3614399544,), "Ligue 1", "FR", ("Ligue One",)),
        "soccer_germany_bundesliga": FiveDollarLeague((686337048,), "Bundesliga", "DE", ("1. Bundesliga",)),
        "soccer_italy_serie_a": FiveDollarLeague((3405541143,), "Serie A", "IT", ("Serie A Enilive",)),
        "soccer_uefa_champs_league": FiveDollarLeague((2187079931, 1318331555), "UEFA Champions League", "EUROPE", ("Champions League", "UEFA CL")),
        # No hard-coded bootstrap id: resolve the account-specific 5Dollar id from /leagues.
        "soccer_uefa_europa_league": FiveDollarLeague((), "UEFA Europa League", "EUROPE", ("Europa League", "UEFA EL", "UEL")),
        "soccer_uefa_nations_league": FiveDollarLeague((), "UEFA Nations League", "EUROPE", ("Nations League", "UEFA Nations League", "UNL")),
    }

    def __init__(self, session_getter):
        self._session_getter = session_getter
        self._request_lock = asyncio.Lock()
        self._live_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self._fixtures_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._details_cache: dict[int, tuple[float, dict[str, Any]]] = {}
        self._league_ids: dict[str, tuple[int, ...]] = {k: v.ids for k, v in self.LEAGUES.items()}
        self._league_refresh_at: float = 0.0
        self._league_lock = asyncio.Lock()
        self.id_scheme: str | None = None
        self.last_error: str | None = None
        self.last_status: int | None = None
        self.rate_limit_remaining: str | None = None
        self.rate_limit_limit: str | None = None
        self.last_request_at: float = 0.0
        # Account-wide Pro budget, response cache and RAW mirror.
        self._request_times: deque[float] = deque(maxlen=32)
        self._response_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._raw_dir = Path(SETTINGS.api_cache_dir) / "5dollar_raw"
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        self.request_count = 0
        self.cache_hits = 0
        self.rate_limit_reset: str | None = None
        self.intelligence = FiveDollarIntelligence(Path(SETTINGS.api_cache_dir) / "5dollar_brain_state.json")
        self.guardian = FiveDollarGuardian(Path(SETTINGS.api_cache_dir) / "5dollar_guardian")

    @property
    def enabled(self) -> bool:
        return bool(SETTINGS.five_dollar_api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {SETTINGS.five_dollar_api_key}",
            "Accept": "application/json",
        }

    @staticmethod
    def _request_key(path: str, params: dict[str, Any] | None) -> str:
        raw = path + "?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _ttl(path: str, params: dict[str, Any] | None) -> float:
        params = params or {}
        if path == "/fixtures" and params.get("status") == "live": return 5.5
        if "/events" in path or "/statistics" in path: return 6.0
        if "/odds/history" in path: return 12.0
        if path.endswith("/odds"): return 8.0
        if path == "/standings": return 120.0
        if path in {"/countries", "/bookmakers"}: return 86400.0
        if path == "/leagues" or (path.startswith("/leagues/") and not path.endswith("/fixtures")): return 21600.0
        if path.startswith("/teams/") and not path.endswith("/fixtures"): return 21600.0
        if path.endswith("/fixtures"): return 60.0
        if path == "/status": return 30.0
        return 5.0

    async def _budget_wait(self) -> None:
        """Keep normal traffic below Pro's 10/min account window with headroom."""
        while True:
            now = time.monotonic()
            while self._request_times and now - self._request_times[0] >= 60.0:
                self._request_times.popleft()
            # Use at most 9 calls/rolling minute; the 10th remains available for recovery/manual work.
            if len(self._request_times) < 9:
                return
            await asyncio.sleep(max(0.15, 60.05 - (now - self._request_times[0])))

    def _store_raw(self, key: str, path: str, params: dict[str, Any] | None, payload: dict[str, Any]) -> None:
        try:
            target = self._raw_dir / f"{key}.json"
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "path": path, "params": params or {}, "payload": payload}, ensure_ascii=False, default=str), encoding="utf-8")
            tmp.replace(target)
        except Exception:
            pass

    async def _get(self, path: str, params: dict[str, Any] | None = None, *, force: bool = False) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        params = dict(params or {})
        key = self._request_key(path, params)
        ttl = self._ttl(path, params)
        cached = self._response_cache.get(key)
        if not force and cached and time.monotonic() - cached[0] <= ttl:
            self.cache_hits += 1
            return cached[1]
        group = path.split("/")[1] if path.strip("/") else "root"
        if not self.guardian.allow(group):
            self.last_error = f"5Dollar guardian: circuit {group} temporairement ouvert"
            return cached[1] if cached else None
        session: aiohttp.ClientSession = await self._session_getter()
        url = f"{self.BASE}{path}"
        attempts = 0
        while attempts < 3:
            attempts += 1
            wait = 0.0
            try:
                async with self._request_lock:
                    # Re-check after waiting for another coroutine: it may have filled the cache.
                    cached = self._response_cache.get(key)
                    if not force and cached and time.monotonic() - cached[0] <= ttl:
                        self.cache_hits += 1
                        return cached[1]
                    await self._budget_wait()
                    async with session.get(url, params=params, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=12)) as resp:
                        self.last_status = resp.status
                        self.last_request_at = time.time()
                        self.request_count += 1
                        self._request_times.append(time.monotonic())
                        self.rate_limit_remaining = resp.headers.get("X-RateLimit-Remaining") or resp.headers.get("x-ratelimit-requests-remaining")
                        self.rate_limit_limit = resp.headers.get("X-RateLimit-Limit") or resp.headers.get("x-ratelimit-requests-limit")
                        self.rate_limit_reset = resp.headers.get("X-RateLimit-Reset") or resp.headers.get("x-ratelimit-reset")
                        self.id_scheme = resp.headers.get("X-ID-Scheme") or resp.headers.get("x-id-scheme") or self.id_scheme
                        if resp.status == 429:
                            try: wait = min(60.0, max(1.0, float(resp.headers.get("Retry-After", "6"))))
                            except ValueError: wait = 6.0
                            self.last_error = f"5DollarFootballAPI: quota temporaire, retry {wait:.0f}s"
                        elif resp.status != 200:
                            text = await resp.text(); self.last_error = f"5DollarFootballAPI HTTP {resp.status}: {text[:240]}"; self.guardian.failure(group,self.last_error); return cached[1] if cached else None
                        else:
                            payload = await resp.json(content_type=None)
                            if not isinstance(payload, dict) or not payload.get("success", 1):
                                self.last_error = f"5DollarFootballAPI: {(payload or {}).get('error') if isinstance(payload, dict) else 'JSON invalide'}"; self.guardian.failure(group,self.last_error); return cached[1] if cached else None
                            self.last_error = None
                            self._response_cache[key] = (time.monotonic(), payload)
                            self._store_raw(key, path, params, payload)
                            self.guardian.schema_watch(path, payload)
                            self.guardian.success(group)
                            return payload
                if attempts < 3: await asyncio.sleep(wait)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                self.last_error = f"5DollarFootballAPI: {exc}"
                self.guardian.failure(group,self.last_error)
                if attempts < 3: await asyncio.sleep(min(2 * attempts, 5))
        return cached[1] if cached else None


    @staticmethod
    def _name_key(value: Any) -> str:
        import unicodedata
        raw = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
        return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in raw).split())

    async def _ensure_league_ids(self, *, force: bool = False) -> None:
        """Resolve ids for the API key's own id scheme.

        5Dollar has a legacy and a public_v1 id family. The league catalogue is
        authoritative for the current account, so Oddium discovers all six ids in
        a few cached catalogue calls instead of assuming ids copied from the docs.
        """
        if not self.enabled:
            return
        now = time.monotonic()
        if not force and self._league_refresh_at and now - self._league_refresh_at < 21600:
            return
        async with self._league_lock:
            now = time.monotonic()
            if not force and self._league_refresh_at and now - self._league_refresh_at < 21600:
                return
            active_since = int((datetime.now(timezone.utc) - timedelta(days=400)).timestamp())
            catalogue: list[dict[str, Any]] = []
            page = 1
            while page <= 4:
                payload = await self._get("/leagues", {"active_since": active_since, "page": page, "per_page": 100, "lang": "en"})
                if payload is None:
                    break
                catalogue.extend(x for x in (payload.get("data") or []) if isinstance(x, dict))
                pagination = payload.get("pagination") or {}
                if not pagination.get("has_more"):
                    break
                page += 1
            if catalogue:
                resolved: dict[str, tuple[int, ...]] = {}
                for sport_key, wanted in self.LEAGUES.items():
                    names = {self._name_key(wanted.name), *(self._name_key(x) for x in wanted.aliases)}
                    ids: list[int] = []
                    for row in catalogue:
                        country = row.get("country") or {}
                        country_code = str(country.get("code") or "").upper()
                        # UEFA catalogue rows can be tagged EUROPE, EU or WORLD depending
                        # on the 5Dollar id family. Competition identity remains the strict gate.
                        is_uefa = sport_key in {"soccer_uefa_champs_league", "soccer_uefa_europa_league"}
                        if not is_uefa and country_code != wanted.country.upper():
                            continue
                        candidate = self._name_key(row.get("name"))
                        short = self._name_key(row.get("short_name"))
                        exact = candidate in names or short in names
                        champions = sport_key == "soccer_uefa_champs_league" and "champions league" in candidate
                        europa = sport_key == "soccer_uefa_europa_league" and "europa league" in candidate and "conference" not in candidate
                        # 5Dollar public-v1 currently exposes La Liga as "Spain La Liga"
                        # (league id 14), while older account catalogues can expose
                        # "La Liga" with a legacy id. Accept both identities, but only
                        # inside country ES so another competition cannot leak in.
                        laliga = sport_key == "soccer_spain_la_liga" and (
                            "la liga" in candidate or candidate.startswith("laliga") or "primera division" in candidate
                        )
                        if exact or champions or europa or laliga:
                            try:
                                ids.append(int(row["id"]))
                            except (KeyError, TypeError, ValueError):
                                pass
                    if ids:
                        resolved[sport_key] = tuple(dict.fromkeys(ids))
                self._league_ids.update(resolved)
                self._league_refresh_at = time.monotonic()

    async def standings(self, sport_key: str, *, season: int | str | None = None) -> dict[str, Any]:
        if not self.enabled or sport_key not in self.LEAGUES:
            return {}
        await self._ensure_league_ids()
        ids = self._league_ids.get(sport_key) or self.LEAGUES[sport_key].ids
        combined: list[dict[str, Any]] = []
        for league_id in ids:
            params: dict[str, Any] = {"league": league_id, "type": "total", "lang": "en"}
            if season is not None:
                params["season"] = season
            payload = await self._get("/standings", params)
            data = (payload or {}).get("data") or {}
            table = data.get("table") or [] if isinstance(data, dict) else []
            for row in table:
                if not isinstance(row, dict):
                    continue
                gf = row.get("goals_for", row.get("total_for", 0))
                ga = row.get("goals_against", row.get("total_against", 0))
                try:
                    gd = row.get("goal_difference")
                    gd = float(gd) if gd is not None else float(gf or 0) - float(ga or 0)
                except (TypeError, ValueError):
                    gd = 0
                combined.append({
                    "position": row.get("position"),
                    "team": row.get("team") or {},
                    "playedGames": row.get("played", 0),
                    "won": row.get("win", row.get("won", 0)),
                    "draw": row.get("draw", 0),
                    "lost": row.get("lose", row.get("lost", 0)),
                    "points": row.get("points", 0),
                    "goalsFor": gf or 0,
                    "goalsAgainst": ga or 0,
                    "goalDifference": gd,
                })
        if not combined:
            return {}
        return {"standings": [{"type": "TOTAL", "table": combined}], "source": "5DollarFootballAPI"}

    @staticmethod
    def _minute(item: dict[str, Any]) -> int | None:
        """Extract the live minute from the different payload shapes seen on 5Dollar."""
        candidates = [
            item.get("minute"), item.get("elapsed"), item.get("match_minute"),
            item.get("live_minute"), item.get("timer"), item.get("status_code"),
        ]
        for container_key in ("time", "clock", "status_info", "status_data"):
            obj = item.get(container_key)
            if isinstance(obj, dict):
                candidates.extend([obj.get("minute"), obj.get("elapsed"), obj.get("current"), obj.get("value")])
        status_obj = item.get("status")
        if isinstance(status_obj, dict):
            candidates.extend([status_obj.get("minute"), status_obj.get("elapsed"), status_obj.get("code")])
        for value in candidates:
            if value in (None, "") or isinstance(value, dict):
                continue
            text = str(value).strip().lower().replace("'", "")
            if text in {"half", "ht", "full", "ft", "live", "in_play"}:
                continue
            try:
                minute = int(float(text))
            except (TypeError, ValueError):
                continue
            if 0 <= minute <= 130:
                return minute
        return None

    @classmethod
    def _status(cls, item: dict[str, Any]) -> str:
        raw_obj = item.get("status")
        raw = str(raw_obj.get("name") or raw_obj.get("status") or raw_obj.get("code") or "scheduled" if isinstance(raw_obj, dict) else raw_obj or "scheduled").lower()
        code = str(item.get("status_code") or "").lower()
        if isinstance(raw_obj, dict):
            code = str(raw_obj.get("code") or code).lower()
        if raw in {"finished", "complete", "completed", "ft"} or code in {"full", "ft"}:
            return "finished"
        if raw in {"live", "in_play", "inplay", "playing"}:
            if code in {"half", "ht"}:
                return "halftime"
            minute = cls._minute(item) or 0
            if minute > 45:
                return "second_half"
            if minute > 0:
                return "first_half"
            return "live"
        if raw == "unknown":
            # Unknown is deliberately non-terminal: never settle/void from it.
            return "suspended"
        return "pending"

    @classmethod
    def _clock(cls, item: dict[str, Any]) -> str:
        raw_obj = item.get("status")
        code = str(item.get("status_code") or "").strip().lower()
        if isinstance(raw_obj, dict):
            code = str(raw_obj.get("code") or code).strip().lower()
        if code in {"half", "ht", "full", "ft"}:
            return ""
        minute = cls._minute(item)
        return f"{minute}'" if minute is not None else ""

    @staticmethod
    def _normalize_event(ev: dict[str, Any], home: str, away: str) -> dict[str, Any] | None:
        kind = str(ev.get("type") or "").lower().strip()
        mapping = {
            "goal": "goal",
            "yellow_card": "yellow_card",
            "red_card": "red_card",
            "substitution": "substitution",
            "missed_penalty": "penalty_missed",
            "corner": "corner",
            "period_score": "period_score",
        }
        kind = mapping.get(kind)
        if not kind:
            return None
        minute = ev.get("minute")
        # A period score is a snapshot (e.g. first-half 1-0), not an event
        # happening again at every polling minute. 5Dollar can repeat it in
        # every live payload, so it must not inherit the moving match clock.
        clock = "" if kind == "period_score" else (f"{minute}'" if minute not in (None, "") else "")
        side = str(ev.get("team") or "").lower()
        team_name = home if side == "home" else away if side == "away" else ""
        pieces: list[str] = []
        if team_name:
            pieces.append(team_name)
        if kind == "substitution":
            pin, pout = ev.get("player_in"), ev.get("player_out")
            if pin or pout:
                pieces.append(f"{pout or '?'} → {pin or '?'}")
        elif kind == "period_score":
            period = str(ev.get("period") or "période").replace("_", " ")
            score = ev.get("score") or {}
            pieces.append(f"{period}: {score.get('home', '–')}-{score.get('away', '–')}")
        elif ev.get("count") is not None:
            pieces.append(f"#{ev.get('count')}")
        return {
            "type": kind,
            "clock": clock,
            "detail": " • ".join(pieces)[:300],
            "provider_id": (
                f"5d:{kind}:{ev.get('period')}:{(ev.get('score') or {}).get('home')}:{(ev.get('score') or {}).get('away')}"
                if kind == "period_score" else
                f"5d:{kind}:{minute}:{side}:{ev.get('count')}:{ev.get('player_in')}:{ev.get('player_out')}:{ev.get('period')}"
            ),
        }

    @staticmethod
    def _extract_1x2(odds: Any, *, live: bool = False) -> dict[str, Any] | None:
        if not isinstance(odds, dict):
            return None
        board = odds.get("1x2") or odds.get("1X2")
        if not isinstance(board, dict):
            return None
        candidates = ["inplay", "current", "closing", "opening"] if live else ["current", "closing", "opening"]
        picked = None
        label = None
        for name in candidates:
            candidate = board.get(name)
            if isinstance(candidate, dict) and all(candidate.get(k) not in (None, "") for k in ("home", "draw", "away")):
                picked = candidate
                label = name
                break
        if not picked:
            return None
        try:
            h, d, a = float(picked["home"]), float(picked["draw"]), float(picked["away"])
        except (TypeError, ValueError):
            return None
        if min(h, d, a) <= 1.0:
            return None
        return {
            "home_odd": round(h, 4),
            "draw_odd": round(d, 4),
            "away_odd": round(a, 4),
            "bookmaker": f"Bet365 • 5Dollar ({label})",
            "last_odds_update": datetime.now(timezone.utc).isoformat(),
        }

    @classmethod
    def _fixture_to_shell(cls, item: dict[str, Any]) -> dict[str, Any] | None:
        fid = item.get("id")
        league = item.get("league") or {}
        teams = item.get("teams") or {}
        home_obj = teams.get("home") or {}
        away_obj = teams.get("away") or {}
        home, away = home_obj.get("name"), away_obj.get("name")
        kickoff = item.get("kickoff_utc")
        if fid is None or not home or not away or not kickoff:
            return None
        goals = item.get("goals") or {}
        provider_events = []
        for ev in item.get("events") or []:
            normalized = cls._normalize_event(ev, str(home), str(away))
            if normalized:
                provider_events.append(normalized)
        return {
            "id": f"5d:{fid}",
            "five_dollar_fixture_id": int(fid),
            "five_dollar_league_id": int(league.get("id")) if league.get("id") is not None else None,
            "home_team": str(home),
            "away_team": str(away),
            "home_team_id": int(home_obj.get("id")) if home_obj.get("id") is not None else None,
            "away_team_id": int(away_obj.get("id")) if away_obj.get("id") is not None else None,
            "commence_time": str(kickoff),
            "status": cls._status(item),
            "home_score": goals.get("home"),
            "away_score": goals.get("away"),
            "live_clock": cls._clock(item),
            "status_detail": item.get("status_reason") or item.get("status_code") or item.get("status"),
            "source": "5DollarFootballAPI",
            "competition_name": league.get("name"),
            "provider_events": provider_events,
            "provider_odds": cls._extract_1x2(item.get("odds"), live=cls._status(item) != "pending"),
            "provider_stats": item.get("statistics") or item.get("stats") or {},
            "corners": item.get("corners") or {},
            "cards": item.get("cards") or {},
            # Keep every useful native 5Dollar field available to the Match Center.
            # The main Live panel stays visually unchanged; richer fields are consumed
            # by Details/Stats/Market and diagnostics instead of being thrown away.
            "provider_round": item.get("round"),
            "league_season_id": item.get("league_season_id"),
            "status_code": item.get("status_code"),
            "status_reason": item.get("status_reason"),
            "half_score": {"home": goals.get("half_home"), "away": goals.get("half_away")},
            "provider_payload": item,
        }

    def _belongs_to_supported_league(self, row: dict[str, Any], sport_key: str) -> bool:
        """Fail closed: a fixture must match BOTH the resolved 5Dollar league id
        and the expected competition name. This prevents an incorrect/stale id from
        leaking Swiss Super League (or any other competition) into Oddium.
        """
        wanted = self.LEAGUES.get(sport_key)
        if wanted is None:
            return False
        try:
            lid = int(row.get("five_dollar_league_id"))
        except (TypeError, ValueError):
            return False
        ids = set(self._league_ids.get(sport_key) or wanted.ids)
        if lid not in ids:
            return False
        lname = self._name_key(row.get("competition_name"))
        if not lname:
            return False
        names = {self._name_key(wanted.name), *(self._name_key(x) for x in wanted.aliases)}
        if lname in names:
            return True
        # Sponsored display names are accepted only when they extend the exact
        # canonical league name (e.g. "Ligue 1 McDonald's", "LaLiga EA Sports").
        canonical = self._name_key(wanted.name)
        if canonical and (lname.startswith(canonical + " ") or lname.startswith(canonical + "-")):
            return True
        if sport_key == "soccer_spain_la_liga":
            # Native live rows may say "Spain La Liga" or sponsored "LaLiga EA Sports".
            return "la liga" in lname or lname.startswith("laliga") or "primera division" in lname
        if sport_key == "soccer_uefa_champs_league":
            return "champions league" in lname
        if sport_key == "soccer_uefa_europa_league":
            return "europa league" in lname and "conference" not in lname
        return False

    async def live_board_shells(self, *, force: bool = False) -> list[dict[str, Any]]:
        """Return the native 5Dollar global live board for Oddium competitions.

        This is intentionally the source of truth for the permanent Live panel.
        Oddium does not reconstruct live membership from SQLite/kickoff times.
        """
        if not self.enabled:
            return []
        now = time.monotonic()
        ttl = max(15, int(SETTINGS.five_dollar_poll_seconds))
        fetched_at, all_rows = self._live_cache
        if fetched_at <= 0 or force or now - fetched_at >= ttl:
            payload = await self._get(
                "/fixtures",
                {"status": "live", "include": "odds,events,stats", "per_page": 500, "lang": "fr"},
            )
            if payload is None:
                return list(all_rows) if fetched_at > 0 else []
            rows = []
            for item in payload.get("data") or []:
                item = self.intelligence.enrich(item)
                shell = self._fixture_to_shell(item)
                if shell:
                    rows.append(shell)
            self._live_cache = (time.monotonic(), rows)
            all_rows = rows

        await self._ensure_league_ids()
        out = []
        for row in all_rows:
            sport_key = None
            for key in self.LEAGUES:
                if self._belongs_to_supported_league(row, key):
                    sport_key = key
                    break
            if not sport_key:
                continue
            item = dict(row)
            item["sport_key"] = sport_key
            item["event_id"] = item.get("id")
            item["match_status"] = item.get("status")
            item["live_phase"] = item.get("status")
            out.append(item)
        return out

    async def live_shells(self, sport_key: str, *, force: bool = False) -> list[dict[str, Any]]:
        if not self.enabled or sport_key not in self.LEAGUES:
            return []
        now = time.monotonic()
        ttl = max(15, int(SETTINGS.five_dollar_poll_seconds))
        fetched_at, all_rows = self._live_cache
        if force and fetched_at > 0 and now - fetched_at < ttl:
            force = False
        if fetched_at <= 0 or force or now - fetched_at >= ttl:
            payload = await self._get(
                "/fixtures",
                {"status": "live", "include": "odds,events,stats", "per_page": 500, "lang": "fr"},
            )
            rows: list[dict[str, Any]] = []
            if payload is not None:
                for item in payload.get("data") or []:
                    item = self.intelligence.enrich(item)
                    shell = self._fixture_to_shell(item)
                    if shell:
                        rows.append(shell)
                self._live_cache = (time.monotonic(), rows)
                all_rows = rows
        await self._ensure_league_ids()
        return [r for r in all_rows if self._belongs_to_supported_league(r, sport_key)]

    async def fixture_shells(self, sport_key: str, *, force: bool = False, days: int = 30) -> list[dict[str, Any]]:
        if not self.enabled or sport_key not in self.LEAGUES:
            return []
        now_m = time.monotonic()
        cached = self._fixtures_cache.get(sport_key)
        ttl = max(120, int(SETTINGS.five_dollar_fixtures_cache_seconds))
        if cached and not force and now_m - cached[0] < ttl:
            return list(cached[1])

        start = int(datetime.now(timezone.utc).timestamp())
        end = int((datetime.now(timezone.utc) + timedelta(days=max(1, days))).timestamp())
        await self._ensure_league_ids()
        rows: list[dict[str, Any]] = []
        for league_id in self._league_ids.get(sport_key) or self.LEAGUES[sport_key].ids:
            page = 1
            while page <= 4:
                payload = await self._get(
                    f"/leagues/{league_id}/fixtures",
                    {
                        "status": "scheduled",
                        "start_time": start,
                        "end_time": end,
                        "include": "odds",
                        "lang": "fr",
                        "page": page,
                        "per_page": 50,
                    },
                )
                if payload is None:
                    break
                for item in payload.get("data") or []:
                    shell = self._fixture_to_shell(item)
                    if shell and self._belongs_to_supported_league(shell, sport_key):
                        rows.append(shell)
                pagination = payload.get("pagination") or {}
                if not pagination.get("has_more"):
                    break
                page += 1
        rows.sort(key=lambda r: str(r.get("commence_time") or ""))
        self._fixtures_cache[sport_key] = (time.monotonic(), rows)
        return list(rows)

    async def finished_shells(self, sport_key: str, *, days: int = 7) -> list[dict[str, Any]]:
        """Fetch recent completed fixtures for ticket recovery.

        Unlike fixture_shells(), this deliberately queries the historical window and
        status=finished. It is used only for unresolved bets, not the normal UI feed.
        """
        if not self.enabled or sport_key not in self.LEAGUES:
            return []
        now = datetime.now(timezone.utc)
        start = int((now - timedelta(days=max(1, days))).timestamp())
        end = int(now.timestamp())
        await self._ensure_league_ids()
        rows: list[dict[str, Any]] = []
        for league_id in self._league_ids.get(sport_key) or self.LEAGUES[sport_key].ids:
            page = 1
            while page <= 4:
                payload = await self._get(
                    f"/leagues/{league_id}/fixtures",
                    {"status": "finished", "start_time": start, "end_time": end,
                     "lang": "fr", "page": page, "per_page": 50},
                )
                if payload is None:
                    break
                for item in payload.get("data") or []:
                    shell = self._fixture_to_shell(item)
                    if shell and self._belongs_to_supported_league(shell, sport_key):
                        rows.append(shell)
                pagination = payload.get("pagination") or {}
                if not pagination.get("has_more"):
                    break
                page += 1
        return rows

    async def fixture_details(self, fixture_id: int, *, force: bool = False) -> dict[str, Any]:
        if not self.enabled:
            return {}
        now = time.monotonic()
        cached = self._details_cache.get(int(fixture_id))
        if cached and not force and now - cached[0] < 30:
            return cached[1]
        payload = await self._get(f"/fixtures/{int(fixture_id)}", {"include": "events,stats", "lang": "fr"})
        raw = (payload or {}).get("data") or {}
        if not isinstance(raw, dict) or not raw:
            return {}
        shell = self._fixture_to_shell(raw) or {}
        stats_raw = raw.get("statistics") or raw.get("stats") or {}
        result = {
            "fixture": shell,
            "events": shell.get("provider_events") or [],
            "statistics": self._statistics_for_ui(stats_raw, shell),
            "raw_statistics": stats_raw,
            "odds": self._extract_1x2(raw.get("odds"), live=self._status(raw) != "pending"),
            "source": "5DollarFootballAPI",
        }
        self._details_cache[int(fixture_id)] = (time.monotonic(), result)
        return result

    @staticmethod
    def _statistics_for_ui(stats: Any, shell: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(stats, dict):
            return []
        aliases = {
            "possession": "Ball Possession",
            "shots_on_target": "Shots on Goal",
            "shots_off_target": "Shots off Goal",
            "attacks": "Attacks",
            "dangerous_attacks": "Dangerous Attacks",
        }
        home_values: dict[str, Any] = {}
        away_values: dict[str, Any] = {}
        for key, display in aliases.items():
            block = stats.get(key)
            if isinstance(block, dict):
                home_values[display] = block.get("home")
                away_values[display] = block.get("away")
        corners = shell.get("corners") or {}
        if isinstance(corners, dict):
            home_values["Corner Kicks"] = corners.get("home")
            away_values["Corner Kicks"] = corners.get("away")
        # Reconstruct Total Shots from on/off target when the feed doesn't expose it.
        try:
            hs = int(home_values.get("Shots on Goal") or 0) + int(home_values.get("Shots off Goal") or 0)
            aws = int(away_values.get("Shots on Goal") or 0) + int(away_values.get("Shots off Goal") or 0)
            if hs or aws:
                home_values["Total Shots"] = hs
                away_values["Total Shots"] = aws
        except (TypeError, ValueError):
            pass
        return [
            {"team": shell.get("home_team") or "Domicile", "values": home_values},
            {"team": shell.get("away_team") or "Extérieur", "values": away_values},
        ]


    # ---- Complete native 5Dollar surface ---------------------------------------
    async def raw_fixtures(self, **params) -> dict[str, Any]:
        payload = await self._get("/fixtures", params or None)
        return payload or {}

    async def raw_fixture(self, fixture_id: int, *, include: str = "odds,events,stats", lang: str = "fr") -> dict[str, Any]:
        return (await self._get(f"/fixtures/{int(fixture_id)}", {"include": include, "lang": lang})) or {}

    async def fixture_odds(self, fixture_id: int, *, market: str | None = None, bookmakers: str = "bet365") -> dict[str, Any]:
        params: dict[str, Any] = {"bookmakers": bookmakers}
        if market: params["market"] = market
        return (await self._get(f"/fixtures/{int(fixture_id)}/odds", params)) or {}

    async def bookmakers(self) -> dict[str, Any]:
        return (await self._get("/bookmakers")) or {}

    async def odds_history(self, fixture_id: int, *, market: str = "1x2", bookmakers: str = "bet365") -> dict[str, Any]:
        return (await self._get(f"/fixtures/{int(fixture_id)}/odds/history", {"market": market, "bookmaker": bookmakers})) or {}

    async def fixture_events(self, fixture_id: int) -> dict[str, Any]:
        return (await self._get(f"/fixtures/{int(fixture_id)}/events")) or {}

    async def fixture_statistics(self, fixture_id: int) -> dict[str, Any]:
        return (await self._get(f"/fixtures/{int(fixture_id)}/statistics")) or {}

    async def countries(self) -> dict[str, Any]:
        return (await self._get("/countries")) or {}

    async def leagues(self, **params) -> dict[str, Any]:
        return (await self._get("/leagues", params or None)) or {}

    async def league(self, league_id: int, *, lang: str = "fr") -> dict[str, Any]:
        return (await self._get(f"/leagues/{int(league_id)}", {"lang": lang})) or {}

    async def league_fixtures(self, league_id: int, **params) -> dict[str, Any]:
        return (await self._get(f"/leagues/{int(league_id)}/fixtures", params or None)) or {}

    async def team(self, team_id: int, *, lang: str = "fr") -> dict[str, Any]:
        return (await self._get(f"/teams/{int(team_id)}", {"lang": lang})) or {}

    async def team_fixtures(self, team_id: int, **params) -> dict[str, Any]:
        return (await self._get(f"/teams/{int(team_id)}/fixtures", params or None)) or {}

    async def standings_native(self, league_id: int, *, season: str | int | None = None, table_type: str = "total", lang: str = "fr") -> dict[str, Any]:
        params: dict[str, Any] = {"league": int(league_id), "type": table_type, "lang": lang}
        if season is not None: params["season"] = season
        return (await self._get("/standings", params)) or {}

    async def account_status(self) -> dict[str, Any]:
        return (await self._get("/status")) or {}

    def diagnostics(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "authority": "5DollarFootballAPI", "mode": "MAX Intelligence / self-healing single-provider", "requests_process": self.request_count, "cache_hits": self.cache_hits, "rolling_minute_requests": len(self._request_times), "budget_target_per_minute": 9, "remaining": self.rate_limit_remaining, "limit": self.rate_limit_limit, "reset": self.rate_limit_reset, "last_status": self.last_status, "last_error": self.last_error, "id_scheme": self.id_scheme, "raw_store": str(self._raw_dir), "intelligence": self.intelligence.diagnostics(), "guardian": self.guardian.health()}


    async def team_intelligence(self, team_id: int, *, limit: int = 10) -> dict[str, Any]:
        payload = await self.team_fixtures(int(team_id), status="finished", include="odds,events,stats", per_page=min(50, max(10, limit)), lang="fr")
        rows = (payload or {}).get("data") or []
        return self.intelligence.analyse_team_history(rows, int(team_id), limit=limit)

    async def status(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        payload = await self._get("/status")
        return {
            "enabled": True,
            "ok": bool(payload),
            "data": (payload or {}).get("data") or {},
            "remaining": self.rate_limit_remaining,
            "limit": self.rate_limit_limit,
            "error": self.last_error,
        }
