from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from config import SETTINGS


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
        "soccer_spain_la_liga": FiveDollarLeague((1810150156,), "La Liga", "ES", ("Primera Division",)),
        "soccer_france_ligue_one": FiveDollarLeague((3614399544,), "Ligue 1", "FR", ("Ligue One",)),
        "soccer_germany_bundesliga": FiveDollarLeague((686337048,), "Bundesliga", "DE", ("1. Bundesliga",)),
        "soccer_italy_serie_a": FiveDollarLeague((3405541143,), "Serie A", "IT", ("Serie A Enilive",)),
        "soccer_uefa_champs_league": FiveDollarLeague((2187079931, 1318331555), "UEFA Champions League", "EUROPE", ("Champions League", "UEFA CL")),
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

    @property
    def enabled(self) -> bool:
        return bool(SETTINGS.five_dollar_api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {SETTINGS.five_dollar_api_key}",
            "Accept": "application/json",
        }

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        session: aiohttp.ClientSession = await self._session_getter()
        url = f"{self.BASE}{path}"
        attempts = 0
        while attempts < 3:
            attempts += 1
            try:
                async with self._request_lock:
                    async with session.get(
                        url,
                        params=params or {},
                        headers=self._headers(),
                        timeout=aiohttp.ClientTimeout(total=12),
                    ) as resp:
                        self.last_status = resp.status
                        self.last_request_at = time.time()
                        self.rate_limit_remaining = resp.headers.get("X-RateLimit-Remaining") or resp.headers.get("x-ratelimit-requests-remaining")
                        self.rate_limit_limit = resp.headers.get("X-RateLimit-Limit") or resp.headers.get("x-ratelimit-requests-limit")
                        self.id_scheme = resp.headers.get("X-ID-Scheme") or resp.headers.get("x-id-scheme") or self.id_scheme
                        if resp.status == 429:
                            retry = resp.headers.get("Retry-After", "6")
                            try:
                                wait = min(30.0, max(1.0, float(retry)))
                            except ValueError:
                                wait = 6.0
                            self.last_error = f"5DollarFootballAPI: quota temporaire, retry {wait:.0f}s"
                        elif resp.status != 200:
                            text = await resp.text()
                            self.last_error = f"5DollarFootballAPI HTTP {resp.status}: {text[:240]}"
                            return None
                        else:
                            payload = await resp.json(content_type=None)
                            if not isinstance(payload, dict):
                                self.last_error = "5DollarFootballAPI: réponse JSON invalide"
                                return None
                            if not payload.get("success", 1):
                                err = payload.get("error") or payload.get("errors") or payload
                                self.last_error = f"5DollarFootballAPI: {err}"
                                return None
                            self.last_error = None
                            return payload
                if attempts < 3:
                    await asyncio.sleep(wait)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                self.last_error = f"5DollarFootballAPI: {exc}"
                if attempts < 3:
                    await asyncio.sleep(min(2 * attempts, 5))
        return None


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
                        if str(country.get("code") or "").upper() != wanted.country.upper():
                            continue
                        candidate = self._name_key(row.get("name"))
                        short = self._name_key(row.get("short_name"))
                        exact = candidate in names or short in names
                        champions = sport_key == "soccer_uefa_champs_league" and "champions league" in candidate
                        if exact or champions:
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
        clock = f"{minute}'" if minute not in (None, "") else ""
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
            "provider_id": f"5d:{kind}:{minute}:{side}:{ev.get('count')}:{ev.get('player_in')}:{ev.get('player_out')}:{ev.get('period')}",
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
        }

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
                    shell = self._fixture_to_shell(item)
                    if shell:
                        rows.append(shell)
                self._live_cache = (time.monotonic(), rows)
                all_rows = rows
        await self._ensure_league_ids()
        ids = set(self._league_ids.get(sport_key) or self.LEAGUES[sport_key].ids)
        wanted = self.LEAGUES[sport_key]
        wanted_names = {self._name_key(wanted.name), *(self._name_key(x) for x in wanted.aliases)}
        return [
            r for r in all_rows
            if r.get("five_dollar_league_id") in ids
            or self._name_key(r.get("competition_name")) in wanted_names
            or (sport_key == "soccer_uefa_champs_league" and "champions league" in self._name_key(r.get("competition_name")))
        ]

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
                    if shell:
                        rows.append(shell)
                pagination = payload.get("pagination") or {}
                if not pagination.get("has_more"):
                    break
                page += 1
        rows.sort(key=lambda r: str(r.get("commence_time") or ""))
        self._fixtures_cache[sport_key] = (time.monotonic(), rows)
        return list(rows)

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
