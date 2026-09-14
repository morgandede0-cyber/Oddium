from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiohttp

from config import SETTINGS


@dataclass(frozen=True)
class ApiFootballLeague:
    id: int
    name: str


class ApiFootballClient:
    """Small API-Football adapter inspired by Scoring-Returns-Bot.

    Oddium uses it only as an optional premium live/event source. If no key is
    configured, every method returns an empty result and the existing ESPN /
    SofaScore / FotMob / TheSportsDB stack stays fully operational.
    """

    LEAGUES = {
        "soccer_epl": ApiFootballLeague(39, "Premier League"),
        "soccer_spain_la_liga": ApiFootballLeague(140, "La Liga"),
        "soccer_france_ligue_one": ApiFootballLeague(61, "Ligue 1"),
        "soccer_germany_bundesliga": ApiFootballLeague(78, "Bundesliga"),
        "soccer_italy_serie_a": ApiFootballLeague(135, "Serie A"),
        "soccer_uefa_champs_league": ApiFootballLeague(2, "UEFA Champions League"),
    }

    DIRECT_BASE = "https://v3.football.api-sports.io"
    RAPIDAPI_BASE = "https://api-football-v1.p.rapidapi.com/v3"

    def __init__(self, session_getter):
        self._session_getter = session_getter
        self._lock = asyncio.Lock()
        self._live_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self._details_cache: dict[int, tuple[float, dict[str, Any]]] = {}
        self.last_error: str | None = None
        self.last_status: int | None = None

    @property
    def enabled(self) -> bool:
        return bool(SETTINGS.api_football_key or SETTINGS.rapidapi_key)

    @property
    def mode(self) -> str:
        return "rapidapi" if SETTINGS.rapidapi_key else "direct"

    def _headers(self) -> dict[str, str]:
        if SETTINGS.rapidapi_key:
            return {
                "x-rapidapi-key": SETTINGS.rapidapi_key,
                "x-rapidapi-host": "api-football-v1.p.rapidapi.com",
            }
        return {"x-apisports-key": SETTINGS.api_football_key}

    def _base(self) -> str:
        return self.RAPIDAPI_BASE if SETTINGS.rapidapi_key else self.DIRECT_BASE

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        session: aiohttp.ClientSession = await self._session_getter()
        async with self._lock:
            try:
                async with session.get(
                    f"{self._base()}{path}", params=params or {}, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    self.last_status = resp.status
                    if resp.status != 200:
                        self.last_error = f"API-Football HTTP {resp.status}"
                        return None
                    payload = await resp.json(content_type=None)
                    errors = payload.get("errors") if isinstance(payload, dict) else None
                    if errors:
                        self.last_error = f"API-Football: {errors}"
                    else:
                        self.last_error = None
                    return payload if isinstance(payload, dict) else None
            except Exception as exc:
                self.last_error = f"API-Football: {exc}"
                return None

    @staticmethod
    def _status(short: str | None) -> str:
        code = str(short or "").upper()
        return {
            "TBD": "pending", "NS": "pending",
            "1H": "first_half", "HT": "halftime", "2H": "second_half",
            "ET": "extra_time", "BT": "extra_time", "P": "penalties",
            "SUSP": "suspended", "INT": "suspended", "LIVE": "live",
            "FT": "finished", "AET": "finished", "PEN": "finished",
            "PST": "postponed", "CANC": "cancelled", "ABD": "cancelled", "AWD": "finished", "WO": "finished",
        }.get(code, "live" if code else "pending")

    @staticmethod
    def _event_type(item: dict[str, Any]) -> str | None:
        etype = str(item.get("type") or "").lower()
        detail = str(item.get("detail") or "").lower()
        comments = str(item.get("comments") or "").lower()
        joined = f"{etype} {detail} {comments}"
        if "var" in joined:
            return "var"
        if etype == "goal" or "goal" in detail:
            if "missed penalty" in joined:
                return "penalty_missed"
            return "goal"
        if etype == "card":
            if "red" in detail:
                return "red_card"
            if "yellow" in detail:
                return "yellow_card"
            return "card"
        if "subst" in etype or "substitution" in joined:
            return "substitution"
        return None

    @classmethod
    def _normalize_event(cls, item: dict[str, Any]) -> dict[str, Any] | None:
        kind = cls._event_type(item)
        if not kind:
            return None
        t = item.get("time") or {}
        elapsed = t.get("elapsed")
        extra = t.get("extra")
        clock = ""
        if elapsed is not None:
            clock = f"{elapsed}'"
            if extra:
                clock = f"{elapsed}+{extra}'"
        team = (item.get("team") or {}).get("name")
        player = (item.get("player") or {}).get("name")
        assist = (item.get("assist") or {}).get("name")
        detail = str(item.get("detail") or "").strip()
        pieces = [p for p in (player, team, detail) if p]
        if assist:
            pieces.append(f"passe: {assist}")
        return {
            "type": kind,
            "clock": clock,
            "detail": " • ".join(pieces)[:300],
            "provider_id": f"{kind}:{elapsed}:{extra}:{team}:{player}:{detail}",
        }

    @classmethod
    def _fixture_to_shell(cls, item: dict[str, Any]) -> dict[str, Any] | None:
        fixture = item.get("fixture") or {}
        teams = item.get("teams") or {}
        goals = item.get("goals") or {}
        league = item.get("league") or {}
        fid = fixture.get("id")
        home = (teams.get("home") or {}).get("name")
        away = (teams.get("away") or {}).get("name")
        kickoff = fixture.get("date")
        if fid is None or not home or not away or not kickoff:
            return None
        status = fixture.get("status") or {}
        elapsed = status.get("elapsed")
        extra = status.get("extra")
        clock = ""
        if elapsed is not None:
            clock = f"{elapsed}'"
            if extra:
                clock = f"{elapsed}+{extra}'"
        provider_events = []
        for ev in item.get("events") or []:
            normalized = cls._normalize_event(ev)
            if normalized:
                provider_events.append(normalized)
        return {
            "id": f"af:{fid}",
            "api_football_fixture_id": int(fid),
            "home_team": home,
            "away_team": away,
            "home_team_id": (teams.get("home") or {}).get("id"),
            "away_team_id": (teams.get("away") or {}).get("id"),
            "commence_time": kickoff,
            "status": cls._status(status.get("short")),
            "home_score": goals.get("home"),
            "away_score": goals.get("away"),
            "live_clock": clock,
            "status_detail": status.get("long") or status.get("short"),
            "source": "API-Football",
            "competition_name": league.get("name"),
            "provider_events": provider_events,
        }

    async def live_shells(self, sport_key: str, *, force: bool = False) -> list[dict[str, Any]]:
        if not self.enabled or sport_key not in self.LEAGUES:
            return []
        now = time.monotonic()
        ttl = max(15, int(SETTINGS.api_football_poll_seconds))
        fetched_at, all_rows = self._live_cache
        # API-Football is deliberately rate-limited independently from Oddium's
        # faster free-source loop. Even when Oddium asks for a forced scan, one
        # global live request is shared by all six competitions for this TTL.
        if fetched_at <= 0.0 or now - fetched_at >= ttl:
            payload = await self._get("/fixtures", {"live": "all"})
            rows: list[dict[str, Any]] = []
            if payload:
                for item in payload.get("response") or []:
                    shell = self._fixture_to_shell(item)
                    if shell:
                        shell["api_football_league_id"] = (item.get("league") or {}).get("id")
                        rows.append(shell)
            if payload is not None:
                self._live_cache = (time.monotonic(), rows)
                all_rows = rows
        league_id = self.LEAGUES[sport_key].id
        return [r for r in all_rows if int(r.get("api_football_league_id") or -1) == league_id]

    async def fixture_details(self, fixture_id: int, *, force: bool = False) -> dict[str, Any]:
        if not self.enabled:
            return {}
        now = time.monotonic()
        cached = self._details_cache.get(int(fixture_id))
        if cached and not force and now - cached[0] < 30:
            return cached[1]

        fixture_task = self._get("/fixtures", {"id": int(fixture_id)})
        stats_task = self._get("/fixtures/statistics", {"fixture": int(fixture_id)})
        fixture_payload, stats_payload = await asyncio.gather(fixture_task, stats_task)
        result: dict[str, Any] = {"events": [], "statistics": [], "source": "API-Football"}

        if fixture_payload and fixture_payload.get("response"):
            raw = fixture_payload["response"][0]
            result["fixture"] = self._fixture_to_shell(raw) or {}
            result["events"] = [x for x in (self._normalize_event(ev) for ev in raw.get("events") or []) if x]

        if stats_payload:
            for team_block in stats_payload.get("response") or []:
                team = (team_block.get("team") or {}).get("name") or "Équipe"
                values = {}
                for stat in team_block.get("statistics") or []:
                    stype = str(stat.get("type") or "")
                    values[stype] = stat.get("value")
                result["statistics"].append({"team": team, "values": values})

        self._details_cache[int(fixture_id)] = (time.monotonic(), result)
        return result

    @staticmethod
    def fixture_id_from_event_id(event_id: str) -> int | None:
        if str(event_id).startswith("af:"):
            try:
                return int(str(event_id).split(":", 1)[1])
            except ValueError:
                return None
        return None
