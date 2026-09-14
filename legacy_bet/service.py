from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import SETTINGS
from .constants import COMPETITIONS
from .database import Database, utcnow_iso
from .economy import EconomyAdapter
from .odds_api import OddsAPI
from .oddium_odds import OddiumOddsEngine

PARIS_TZ = ZoneInfo("Europe/Paris")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class BettingService:
    def __init__(self, db: Database, economy: EconomyAdapter, odds_api: OddsAPI):
        self.db = db
        self.economy = economy
        self.odds_api = odds_api
        self.odds_engine = OddiumOddsEngine(odds_api, margin=SETTINGS.oddium_odds_margin_percent / 100.0)
        self.last_odds_refresh: datetime | None = None
        self.last_scores_refresh: datetime | None = None
        self.last_error: str | None = None
        self._last_engine_odds_attempt: datetime | None = None
        self._last_events_refresh: datetime | None = None
        self._last_live_score_poll: dict[str, datetime] = {}
        self._last_live_discovery: dict[str, datetime] = {}
        self.last_live_events: list[dict] = []

    async def active_competitions(self) -> list[str]:
        return await self.db.get_setting("active_competitions") or []

    async def set_active_competitions(self, keys: list[str], admin_id: int | None = None) -> None:
        valid = [k for k in keys if k in COMPETITIONS]
        await self.db.set_setting("active_competitions", valid)
        await self.db.log_admin(admin_id, "SET_COMPETITIONS", ", ".join(valid))

    async def refresh_events(self, *, priority: bool = False) -> tuple[int, list[dict], list[str]]:
        """Discover upcoming fixtures, with 5Dollar Pro as the canonical source.

        Provider ids are stored separately from Oddium event ids so an existing V10/V11
        database migrates without duplicating the same match. 5Dollar's batch fixture
        response also carries Bet365 1X2 prices, which are persisted immediately.
        """
        active = await self.active_competitions()
        discovered = 0
        settlements: list[dict] = []
        errors: list[str] = []
        now_iso = utcnow_iso()
        for key in active:
            try:
                raws = await self.odds_api.fetch_events(key, priority=priority)
                await self.db.set_setting(f"diag_events_{key}", len(raws or []))
                for raw in raws or []:
                    event = self.odds_api.parse_event_shell(raw, key)
                    if not event:
                        continue
                    discovered += 1
                    event_id = str(event["event_id"])
                    five_id = event.get("five_dollar_fixture_id")

                    # Migration/dedup: reuse an older fd:/af: row if teams and kickoff match.
                    target_id = event_id
                    existing = await self.db.fetchone("SELECT event_id FROM matches WHERE event_id=?", (event_id,))
                    if existing is None and event.get("home_team") and event.get("away_team"):
                        try:
                            kickoff = parse_iso(str(event["commence_time"]))
                            lo = (kickoff - timedelta(hours=3)).isoformat()
                            hi = (kickoff + timedelta(hours=3)).isoformat()
                            candidates = await self.db.fetchall(
                                "SELECT event_id,home_team,away_team FROM matches WHERE sport_key=? AND commence_time BETWEEN ? AND ?",
                                (key, lo, hi),
                            )
                            hn = self.odds_api._norm(event.get("home_team")); an = self.odds_api._norm(event.get("away_team"))
                            for cand in candidates:
                                if self.odds_api._norm(cand["home_team"]) == hn and self.odds_api._norm(cand["away_team"]) == an:
                                    target_id = str(cand["event_id"]); existing = cand; break
                        except Exception:
                            pass

                    provider_odds = event.get("provider_odds") or {}
                    hodd = provider_odds.get("home_odd")
                    dodd = provider_odds.get("draw_odd")
                    aodd = provider_odds.get("away_odd")
                    bookmaker = provider_odds.get("bookmaker")
                    odds_ts = provider_odds.get("last_odds_update") or now_iso
                    odds_available = 1 if all(x is not None for x in (hodd,dodd,aodd)) else 0

                    if existing is None:
                        await self.db.execute(
                            """INSERT INTO matches(event_id,sport_key,competition_name,home_team,away_team,home_team_id,away_team_id,commence_time,
                                  home_odd,draw_odd,away_odd,bookmaker,last_odds_update,odds_available,completed,cancelled,home_score,away_score,
                                  five_dollar_fixture_id,last_score_update,first_seen_at,last_seen_at)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,NULL,NULL,?,NULL,?,?)""",
                            (target_id, key, event["competition_name"], event["home_team"], event["away_team"],
                             event.get("home_team_id"), event.get("away_team_id"), event["commence_time"],
                             hodd,dodd,aodd,bookmaker,odds_ts,odds_available,five_id,now_iso,now_iso),
                        )
                    else:
                        old = await self.db.fetchone("SELECT home_odd,draw_odd,away_odd FROM matches WHERE event_id=?", (target_id,))
                        await self.db.execute(
                            """UPDATE matches SET sport_key=?,competition_name=?,home_team=?,away_team=?,
                               home_team_id=COALESCE(?,home_team_id),away_team_id=COALESCE(?,away_team_id),commence_time=?,
                               five_dollar_fixture_id=COALESCE(?,five_dollar_fixture_id),last_seen_at=?,
                               home_odd=COALESCE(?,home_odd),draw_odd=COALESCE(?,draw_odd),away_odd=COALESCE(?,away_odd),
                               bookmaker=COALESCE(?,bookmaker),last_odds_update=CASE WHEN ?=1 THEN ? ELSE last_odds_update END,
                               odds_available=CASE WHEN ?=1 THEN 1 ELSE odds_available END WHERE event_id=?""",
                            (key,event["competition_name"],event["home_team"],event["away_team"],event.get("home_team_id"),event.get("away_team_id"),
                             event["commence_time"],five_id,now_iso,hodd,dodd,aodd,bookmaker,odds_available,odds_ts,odds_available,target_id),
                        )
                        if odds_available and old and (old["home_odd"],old["draw_odd"],old["away_odd"]) != (hodd,dodd,aodd):
                            await self.db.execute(
                                "INSERT INTO odds_history(event_id,home_odd,draw_odd,away_odd,bookmaker,captured_at) VALUES(?,?,?,?,?,?)",
                                (target_id,hodd,dodd,aodd,bookmaker,now_iso),
                            )
            except Exception as exc:
                errors.append(f"{COMPETITIONS.get(key, {}).get('name', key)}: {exc}")
        self._last_events_refresh = datetime.now(timezone.utc)
        return discovered, settlements, errors

    async def refresh_odds(self, *, priority: bool = False, discover: bool = True) -> tuple[int, list[str]]:
        """Refresh real Bet365 prices from 5Dollar Pro, then fall back to Oddium Fusion.

        One batch fixture-list call per competition provides every upcoming 1/X/2
        board. No per-match odds loop is used, keeping the Pro quota predictable.
        """
        errors: list[str] = []
        if discover:
            _, _, discovery_errors = await self.refresh_events(priority=priority)
            errors.extend(discovery_errors)

        active = await self.active_competitions()
        updated = 0
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=SETTINGS.odds_horizon_hours)
        seen_now = utcnow_iso()

        for key in active:
            try:
                five_map: dict[int, dict] = {}
                if SETTINGS.five_dollar_api_key:
                    try:
                        raws = await self.odds_api.fetch_five_dollar_fixtures(key, force=priority)
                        for raw in raws or []:
                            fid = raw.get("five_dollar_fixture_id")
                            quote = raw.get("provider_odds") or {}
                            if fid is not None and quote:
                                five_map[int(fid)] = quote
                    except Exception as exc:
                        errors.append(f"5Dollar {COMPETITIONS.get(key, {}).get('name', key)}: {exc}")

                rows = await self.db.fetchall(
                    """SELECT * FROM matches WHERE sport_key=? AND completed=0 AND cancelled=0
                       AND commence_time>=? AND commence_time<=? ORDER BY commence_time ASC""",
                    (key, now.isoformat(), horizon.isoformat()),
                )
                parsed_count = 0
                provider_count = 0
                model_count = 0
                for match in rows:
                    quote = None
                    fid = match["five_dollar_fixture_id"] if "five_dollar_fixture_id" in match.keys() else None
                    if fid is not None:
                        quote = five_map.get(int(fid))
                    if quote:
                        home_odd=float(quote["home_odd"]); draw_odd=float(quote["draw_odd"]); away_odd=float(quote["away_odd"])
                        bookmaker=str(quote.get("bookmaker") or "Bet365 • 5DollarFootballAPI")
                        quote_ts=str(quote.get("last_odds_update") or seen_now)
                        provider_count += 1
                    else:
                        try:
                            model = await self.odds_engine.quote(key, str(match["home_team"]), str(match["away_team"]))
                            home_odd,draw_odd,away_odd=model.home_odd,model.draw_odd,model.away_odd
                            bookmaker=f"{model.source} • secours"
                            quote_ts=model.last_odds_update
                            model_count += 1
                        except Exception as exc:
                            errors.append(f"{match['home_team']} - {match['away_team']}: {exc}")
                            continue

                    old=(match["home_odd"],match["draw_odd"],match["away_odd"])
                    await self.db.execute(
                        """UPDATE matches SET home_odd=?,draw_odd=?,away_odd=?,bookmaker=?,last_odds_update=?,
                           odds_available=1,last_seen_at=? WHERE event_id=?""",
                        (home_odd,draw_odd,away_odd,bookmaker,quote_ts,seen_now,str(match["event_id"])),
                    )
                    changed = old[0] is None or any(
                        prev is None or abs(float(prev)-float(cur)) > 0.0001
                        for prev,cur in zip(old,(home_odd,draw_odd,away_odd))
                    )
                    if changed:
                        await self.db.execute(
                            "INSERT INTO odds_history(event_id,home_odd,draw_odd,away_odd,bookmaker,captured_at) VALUES(?,?,?,?,?,?)",
                            (str(match["event_id"]),home_odd,draw_odd,away_odd,bookmaker,seen_now),
                        )
                    parsed_count += 1; updated += 1

                await self.db.set_setting(f"diag_odds_{key}", len(rows))
                await self.db.set_setting(f"diag_parsed_{key}", parsed_count)
                await self.db.set_setting(f"diag_odds_source_{key}", f"5Dollar/Bet365={provider_count} • Fusion={model_count}")
            except Exception as exc:
                errors.append(f"{COMPETITIONS.get(key, {}).get('name', key)}: {exc}")

        self.last_odds_refresh = datetime.now(timezone.utc)
        await self.db.set_setting("last_odds_refresh", self.last_odds_refresh.isoformat())
        self.last_error = " | ".join(errors) if errors else None
        return updated, errors

    async def smart_refresh_due(self) -> bool:
        now = datetime.now(timezone.utc)
        if self._last_engine_odds_attempt is None:
            return True
        row = await self.db.fetchone(
            "SELECT commence_time FROM matches WHERE completed=0 AND cancelled=0 AND commence_time>? ORDER BY commence_time ASC LIMIT 1",
            (now.isoformat(),),
        )
        if not row:
            interval = SETTINGS.odds_refresh_far_seconds
        else:
            delta = parse_iso(row["commence_time"]) - now
            if delta <= timedelta(hours=1):
                interval = SETTINGS.odds_refresh_hot_seconds
            elif delta <= timedelta(hours=6):
                interval = SETTINGS.odds_refresh_near_seconds
            else:
                interval = SETTINGS.odds_refresh_far_seconds
        return (now - self._last_engine_odds_attempt).total_seconds() >= interval

    async def engine_refresh_odds_if_due(self) -> tuple[int, list[str]] | None:
        if not await self.smart_refresh_due():
            return None
        now = datetime.now(timezone.utc)
        discover = (
            self._last_events_refresh is None
            or (now - self._last_events_refresh).total_seconds() >= SETTINGS.events_refresh_seconds
        )
        self._last_engine_odds_attempt = now
        return await self.refresh_odds(priority=False, discover=discover)

    async def _promote_scheduled_kickoffs(self) -> list[dict]:
        """Make a fixture visible the instant its scheduled kickoff is reached.

        Public score feeds can confirm IN_PLAY a few minutes late.  Instead of hiding
        the fixture until the first score/status change, Oddium enters a temporary
        `kickoff_wait` phase.  A real provider replaces it as soon as it confirms live.
        """
        now = datetime.now(timezone.utc)
        lo = (now - timedelta(minutes=150)).isoformat()
        hi = now.isoformat()
        rows = await self.db.fetchall(
            """SELECT * FROM matches
               WHERE completed=0 AND cancelled=0
                 AND commence_time BETWEEN ? AND ?
                 AND COALESCE(live_phase,'pending')='pending'""",
            (lo, hi),
        )
        out: list[dict] = []
        for row in rows:
            event_id = str(row["event_id"])
            await self.db.execute(
                """UPDATE matches SET live_phase='kickoff_wait', match_status='kickoff_wait',
                   live_clock=COALESCE(live_clock,?),
                   live_detail='Coup d’envoi prévu • confirmation live en attente',
                   live_source='Horloge Oddium', last_score_update=? WHERE event_id=?""",
                ("0'", utcnow_iso(), event_id),
            )
            out.append({
                "type": "match_started", "event_id": event_id,
                "home_team": row["home_team"], "away_team": row["away_team"],
                "home_score": row["home_score"], "away_score": row["away_score"],
                "phase": "kickoff_wait", "previous_phase": "pending", "clock": "0'",
                "detail": "Coup d’envoi prévu • confirmation live en attente",
                "source": "Horloge Oddium",
            })
        return out

    def _sort_live_rows(self, raws: list[dict], sport_key: str) -> list[dict]:
        """Process weak/pending observations first and authoritative live states last.

        This prevents a slow schedule-only provider from reverting ESPN/Sofascore from
        LIVE back to PENDING during the same collector cycle.
        """
        state_rank = {
            "pending": 0, "kickoff_wait": 1, "suspended": 2,
            "live": 4, "first_half": 5, "halftime": 6, "second_half": 7,
            "extra_time": 8, "penalties": 9,
            "postponed": 10, "cancelled": 10, "finished": 11,
        }
        source_rank = {
            "thesportsdb": 1, "livescorefootball": 2, "fotmob": 3,
            "espn": 4, "sofascore": 5, "sofascore live": 6,
            "football-data.org": 3, "api-football": 8, "5dollarfootballapi": 12,
        }
        def key(raw: dict):
            parsed = self.odds_api.parse_score_shell(raw, sport_key) or {}
            st = str(parsed.get("status") or "pending").lower()
            src = str(parsed.get("source") or "").lower()
            return (state_rank.get(st, 1), source_rank.get(src, 0), 1 if parsed.get("live_clock") else 0)
        return sorted(list(raws or []), key=key)

    async def refresh_scores_and_settle(self, *, force_live_panel: bool = False) -> tuple[int, list[dict], list[str]]:
        """Refresh scores only where useful.

        - Pending bets near kickoff always trigger a competition score refresh.
        - If the live panel is installed, competitions with a match inside the live
          window are also refreshed, even when nobody has bet on them.
        - 5Dollar is globally cached, so six competition filters share one live
          request inside FIVE_DOLLAR_POLL_SECONDS.
        """
        active = await self.active_competitions()
        score_updates = 0
        settlements: list[dict] = []
        errors: list[str] = []
        live_events: list[dict] = []
        now = datetime.now(timezone.utc)
        try:
            kickoff_events = await self._promote_scheduled_kickoffs()
            live_events.extend(kickoff_events)
            score_updates += len(kickoff_events)
        except Exception:
            kickoff_events = []
        from_time = (now - timedelta(minutes=SETTINGS.live_panel_lookback_minutes)).isoformat()
        to_time = (now + timedelta(minutes=SETTINGS.live_panel_lookahead_minutes)).isoformat()

        # V9.0.2: le moteur Live doit fonctionner dès que le panneau principal
        # Oddium est installé. Avant, la découverte des matchs live ne tournait
        # que si /setup_live avait créé un panneau live séparé. Résultat : le
        # bouton 🔴 Live de l'accueil pouvait afficher une liste vide alors que
        # des matchs étaient réellement en cours.
        # V9.1: le moteur Live est un service autonome. Il tourne même sans panneau
        # Discord installé et ne dépend jamais des compétitions activées pour les paris.
        # Le panneau n'est qu'un consommateur de l'état Live stocké en SQLite.
        live_panel_enabled = True
        live_keys = list(COMPETITIONS.keys())

        for key in live_keys:
            pending_bets = await self.db.fetchone(
                """SELECT COUNT(*) c FROM bets b JOIN matches m ON m.event_id=b.event_id
                   WHERE b.status='PENDING' AND m.sport_key=? AND m.commence_time BETWEEN ? AND ?""",
                (key, from_time, to_time),
            )
            live_candidates = None
            if live_panel_enabled:
                live_candidates = await self.db.fetchone(
                    """SELECT COUNT(*) c FROM matches
                       WHERE sport_key=? AND cancelled=0 AND completed=0
                         AND commence_time BETWEEN ? AND ?""",
                    (key, from_time, to_time),
                )

            need_scores = int(pending_bets["c"] if pending_bets else 0) > 0
            if live_panel_enabled and live_candidates:
                need_scores = need_scores or int(live_candidates["c"]) > 0

            has_local_candidate = bool(live_candidates and int(live_candidates["c"]) > 0)
            has_pending_bet = bool(pending_bets and int(pending_bets["c"]) > 0)

            # V8.6: hot polling only for competitions that actually have a nearby/live
            # fixture.  If Oddium does not know a fixture yet, do one lightweight
            # discovery probe per LIVE_DISCOVERY_SECONDS instead of hammering six
            # competitions every few seconds.
            discovery_due = False
            if live_panel_enabled and not (has_local_candidate or has_pending_bet):
                last_discovery = self._last_live_discovery.get(key)
                discovery_due = last_discovery is None or (now - last_discovery).total_seconds() >= SETTINGS.live_discovery_seconds
                if discovery_due:
                    self._last_live_discovery[key] = now
                    need_scores = True
            # V9.1: on exécute toujours le passage logique pour les 6 ligues.
            # Les clients HTTP possèdent leur propre cache/TTL, donc cela ne signifie
            # pas 6 nouvelles requêtes réseau à chaque tick. Cela supprime le risque
            # qu'une ligue sans fixture locale connue ne soit jamais redécouverte.
            need_scores = True

            try:
                hot = has_local_candidate or has_pending_bet
                last_poll = self._last_live_score_poll.get(key)
                force_api = hot and (last_poll is None or (now - last_poll).total_seconds() >= SETTINGS.live_poll_seconds)

                # V9.0.8: never let one free source block the complete Live scan.
                # The three providers are queried in parallel with short independent
                # timeouts. livescoreFootball is only used for EPL/LaLiga where the
                # public project is verified; ESPN + SofaScore cover all 6 leagues.
                import asyncio

                async def _safe_live_call(label, coro, timeout=7.0):
                    try:
                        return await asyncio.wait_for(coro, timeout=timeout)
                    except Exception as exc:
                        try:
                            import logging
                            logging.getLogger("oddium").warning("Live source %s indisponible pour %s: %s", label, COMPETITIONS.get(key, {}).get("name", key), exc)
                        except Exception:
                            pass
                        return []

                try:
                    import logging
                    logging.getLogger("oddium").info("Live scan démarré: %s", COMPETITIONS.get(key, {}).get("name", key))
                except Exception:
                    pass
                five_dollar_task = _safe_live_call(
                    "5DollarFootballAPI",
                    self.odds_api.fetch_five_dollar_live(key, force=force_api or discovery_due),
                    10.0,
                )
                open_task = _safe_live_call(
                    "livescoreFootball",
                    self.odds_api.fetch_open_source_scores(key, force=force_api or discovery_due),
                    6.0,
                )
                espn_task = _safe_live_call(
                    "ESPN",
                    self.odds_api.fetch_espn_scores(key, force=force_api or discovery_due),
                    6.0,
                )
                sofa_task = _safe_live_call(
                    "SofaScore Live",
                    self.odds_api.fetch_sofascore_live_scores(key, force=force_api or discovery_due),
                    6.0,
                )
                fotmob_task = _safe_live_call(
                    "FotMob",
                    self.odds_api.fetch_fotmob_scores(key, force=force_api or discovery_due),
                    6.0,
                )
                sportsdb_task = _safe_live_call(
                    "TheSportsDB",
                    self.odds_api.fetch_thesportsdb_scores(key, force=force_api or discovery_due),
                    6.0,
                )
                five_dollar_rows, open_rows, espn_rows, sofa_live_rows, fotmob_rows, sportsdb_rows = await asyncio.gather(
                    five_dollar_task, open_task, espn_task, sofa_task, fotmob_task, sportsdb_task
                )

                sofa_rows = []
                if not sofa_live_rows:
                    sofa_rows = await _safe_live_call(
                        "SofaScore Day",
                        self.odds_api.fetch_sofascore_scores(key, force=force_api or discovery_due),
                        6.0,
                    )

                raws = list(open_rows or []) + list(espn_rows or []) + list(fotmob_rows or []) + list(sportsdb_rows or []) + list(sofa_rows or []) + list(sofa_live_rows or []) + list(five_dollar_rows or [])
                sources = []
                if five_dollar_rows:
                    sources.append("5Dollar")
                if open_rows:
                    sources.append("livescoreFootball")
                if espn_rows:
                    sources.append("ESPN")
                if fotmob_rows:
                    sources.append("FotMob")
                if sportsdb_rows:
                    sources.append("TheSportsDB")
                if sofa_live_rows:
                    sources.append("Sofascore Live")
                elif sofa_rows:
                    sources.append("Sofascore")
                score_source = " + ".join(sources) if sources else "aucune donnée live"
                try:
                    import logging
                    _log = logging.getLogger("oddium")
                    _log.info(
                        "Live scan %s: 5Dollar=%s | livescoreFootball=%s | ESPN=%s | FotMob=%s | TheSportsDB=%s | SofascoreLive=%s | SofascoreDay=%s | total=%s",
                        COMPETITIONS.get(key, {}).get("name", key),
                        len(five_dollar_rows or []), len(open_rows or []), len(espn_rows or []), len(fotmob_rows or []), len(sportsdb_rows or []),
                        len(sofa_live_rows or []), len(sofa_rows or []),
                        len(raws or []),
                    )
                except Exception:
                    pass

                # PropLine stays a last-resort settlement source only. It is not used
                # for the fast live loop, so the free odds quota is preserved.
                if not raws and has_pending_bet:
                    raws = await self.odds_api.fetch_scores(key, days_from=3, force=False)
                    score_source = "football-data.org secours" if raws else "aucune donnée live"
                if force_api:
                    self._last_live_score_poll[key] = datetime.now(timezone.utc)
                raws = self._sort_live_rows(list(raws or []), key)
                await self.db.set_setting(f"diag_scores_{key}", len(raws or []))
                await self.db.set_setting(f"diag_scores_source_{key}", score_source)

                for raw in raws or []:
                    event = self.odds_api.parse_score_shell(raw, key)
                    if not event:
                        continue
                    provider_event_id = event["event_id"]
                    hs, aws = event["home_score"], event["away_score"]
                    status = event["status"]

                    # PropLine documents that event ids can be merged/changed. First try
                    # the current id, then retired ids, then a stable team/time match.
                    target_event_id = provider_event_id
                    old = await self.db.fetchone(
                        "SELECT event_id,home_team,away_team,home_score,away_score,match_status,live_phase,live_clock,live_detail,live_source,completed,cancelled FROM matches WHERE event_id=?",
                        (provider_event_id,),
                    )
                    if old is None:
                        for retired in event.get("merged_from_event_ids", []):
                            old = await self.db.fetchone(
                                "SELECT event_id,home_team,away_team,home_score,away_score,match_status,live_phase,live_clock,live_detail,live_source,completed,cancelled FROM matches WHERE event_id=?",
                                (retired,),
                            )
                            if old is not None:
                                target_event_id = str(old["event_id"])
                                break

                    if old is None and event.get("home_team") and event.get("away_team") and event.get("commence_time"):
                        try:
                            kickoff = parse_iso(str(event["commence_time"]))
                            lo = (kickoff - timedelta(hours=6)).isoformat()
                            hi = (kickoff + timedelta(hours=6)).isoformat()
                            candidates = await self.db.fetchall(
                                """SELECT event_id,home_team,away_team,home_score,away_score,match_status,live_phase,live_clock,live_detail,live_source,completed,cancelled
                                   FROM matches WHERE sport_key=? AND commence_time BETWEEN ? AND ?""",
                                (key, lo, hi),
                            )
                            hn = self.odds_api._norm(event.get("home_team"))
                            an = self.odds_api._norm(event.get("away_team"))
                            for cand in candidates:
                                if self.odds_api._norm(cand["home_team"]) == hn and self.odds_api._norm(cand["away_team"]) == an:
                                    old = cand
                                    target_event_id = str(cand["event_id"])
                                    break
                        except Exception:
                            pass

                    newly_inserted = False
                    if old is None:
                        # Scores may surface a fixture before the events cache refreshes.
                        if not event.get("home_team") or not event.get("away_team") or not event.get("commence_time"):
                            continue
                        newly_inserted = True
                        await self.db.execute(
                            """INSERT INTO matches(event_id,sport_key,competition_name,home_team,away_team,home_team_id,away_team_id,commence_time,
                                   odds_available,completed,cancelled,home_score,away_score,match_status,live_phase,live_clock,live_detail,live_source,api_football_fixture_id,five_dollar_fixture_id,last_score_update,first_seen_at,last_seen_at)
                               VALUES(?,?,?,?,?,?,?,?,0,0,0,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (provider_event_id, key, COMPETITIONS.get(key, {}).get("name", key), event["home_team"], event["away_team"],
                             event.get("home_team_id"), event.get("away_team_id"), event["commence_time"],
                             int(hs) if hs is not None else None, int(aws) if aws is not None else None, status, status,
                             event.get("live_clock"), event.get("status_detail"), event.get("source"), event.get("api_football_fixture_id"), event.get("five_dollar_fixture_id"),
                             utcnow_iso(), utcnow_iso(), utcnow_iso()),
                        )
                        target_event_id = provider_event_id
                        old = await self.db.fetchone(
                            "SELECT event_id,home_team,away_team,home_score,away_score,match_status,live_phase,live_clock,live_detail,live_source,completed,cancelled FROM matches WHERE event_id=?",
                            (target_event_id,),
                        )

                    completed = 1 if status == "finished" else 0
                    cancelled = 1 if status in {"cancelled", "postponed"} else 0
                    hs_i = int(hs) if hs is not None else None
                    aws_i = int(aws) if aws is not None else None
                    old_phase = str(old["live_phase"] or old["match_status"] or "pending")
                    new_phase = status
                    if new_phase == "pending" and old_phase in {"kickoff_wait","live","first_half","halftime","second_half","extra_time","penalties","suspended"}:
                        new_phase = old_phase
                        status = old_phase
                    old_clock = str(old["live_clock"] or "")
                    new_clock = str(event.get("live_clock") or "")
                    old_detail = str(old["live_detail"] or "")
                    new_detail = str(event.get("status_detail") or "")
                    score_changed = (hs_i is not None and old["home_score"] != hs_i) or (aws_i is not None and old["away_score"] != aws_i)
                    phase_changed = old_phase != new_phase
                    clock_changed = bool(new_clock) and old_clock != new_clock
                    detail_changed = bool(new_detail) and old_detail != new_detail
                    changed = newly_inserted or score_changed or phase_changed or clock_changed or detail_changed or int(old["completed"] or 0) != completed or int(old["cancelled"] or 0) != cancelled

                    # IMPORTANT: last_score_update is the timestamp of the *last real live change*.
                    # Do not refresh it on every provider poll, otherwise finished matches remain
                    # permanently "recent" and never disappear from the Live panel.
                    signal_ts = utcnow_iso()
                    await self.db.execute(
                        """UPDATE matches SET home_score=COALESCE(?,home_score),away_score=COALESCE(?,away_score),
                           last_score_update=CASE WHEN ?=1 THEN ? ELSE last_score_update END,
                           match_status=?,live_phase=?,live_clock=?,live_detail=?,live_source=?,completed=?,cancelled=?,
                           home_team_id=COALESCE(?,home_team_id),away_team_id=COALESCE(?,away_team_id),
                           api_football_fixture_id=COALESCE(?,api_football_fixture_id),
                           five_dollar_fixture_id=COALESCE(?,five_dollar_fixture_id)
                           WHERE event_id=?""",
                        (hs_i, aws_i, 1 if changed else 0, signal_ts, status, new_phase, new_clock or None, new_detail or None,
                         event.get("source"), completed, cancelled, event.get("home_team_id"), event.get("away_team_id"),
                         event.get("api_football_fixture_id"), event.get("five_dollar_fixture_id"), target_event_id),
                    )
                    if changed:
                        score_updates += 1
                        event_type = "match_started" if newly_inserted and new_phase in {"live","first_half","second_half","halftime","extra_time","penalties","suspended"} else "live_update"
                        if score_changed and not newly_inserted:
                            event_type = "goal_or_score"
                        elif phase_changed:
                            event_type = {
                                "first_half": "match_started", "live": "match_started" if old_phase == "pending" else "phase_change",
                                "halftime": "halftime", "second_half": "second_half_started",
                                "extra_time": "extra_time_started", "penalties": "penalties_started",
                                "finished": "match_finished", "postponed": "match_postponed",
                                "cancelled": "match_cancelled", "suspended": "match_suspended",
                            }.get(new_phase, "phase_change")
                        elif clock_changed:
                            event_type = "clock_update"

                        # Si 5Dollar fournit le but précis, on évite le doublon
                        # "score modifié" + "but" dans les DM/timelines.
                        has_precise_goal = score_changed and any(
                            str(x.get("type") or "") == "goal" for x in (event.get("provider_events") or [])
                        )
                        if not has_precise_goal or event_type in {"match_finished", "halftime", "second_half_started"}:
                            live_events.append({
                                "type": event_type, "event_id": target_event_id,
                                "home_team": event.get("home_team") or old["home_team"], "away_team": event.get("away_team") or old["away_team"],
                                "home_score": hs_i if hs_i is not None else old["home_score"],
                                "away_score": aws_i if aws_i is not None else old["away_score"],
                                "phase": new_phase, "previous_phase": old_phase, "clock": new_clock or None,
                                "detail": new_detail or None, "source": event.get("source"),
                            })

                    # Scoring-Returns style event stream: goals, VAR and cards are
                    # persisted once and then pushed through Oddium's WebSocket/DM layer.
                    for pev in event.get("provider_events") or []:
                        ptype = str(pev.get("type") or "")
                        pclock = str(pev.get("clock") or "")
                        pdetail = str(pev.get("detail") or "")
                        if not ptype:
                            continue
                        seen = await self.db.fetchone(
                            "SELECT 1 FROM live_events WHERE event_id=? AND event_type=? AND COALESCE(clock,'')=? AND COALESCE(detail,'')=? LIMIT 1",
                            (target_event_id, ptype, pclock, pdetail),
                        )
                        if seen:
                            continue
                        live_events.append({
                            "type": ptype, "event_id": target_event_id,
                            "home_team": event.get("home_team") or old["home_team"],
                            "away_team": event.get("away_team") or old["away_team"],
                            "home_score": hs_i if hs_i is not None else old["home_score"],
                            "away_score": aws_i if aws_i is not None else old["away_score"],
                            "phase": new_phase, "clock": pclock or new_clock or None,
                            "detail": pdetail or None, "source": event.get("source") or "5DollarFootballAPI",
                        })

                    if status in {"cancelled", "postponed"}:
                        await self.void_event(target_event_id, note="Match annulé/reporté par la source football")
                    elif status == "finished" and hs_i is not None and aws_i is not None:
                        settlements.extend(await self.settle_event(target_event_id, hs_i, aws_i))
            except Exception as exc:
                errors.append(f"{COMPETITIONS.get(key, {}).get('name', key)}: {exc}")

        # Snapshot de santé du collecteur, utilisé par le watchdog et les diagnostics.
        try:
            visible_now = await self.live_matches(100)
            await self.db.set_setting("live_engine_last_scan", utcnow_iso())
            await self.db.set_setting("live_engine_visible_count", len(visible_now))
        except Exception:
            pass

        for _event in live_events:
            await self.record_live_event(_event)
        self.last_live_events = live_events
        self.last_scores_refresh = datetime.now(timezone.utc)
        await self.db.set_setting("last_scores_refresh", self.last_scores_refresh.isoformat())
        return score_updates, settlements, errors

    async def live_match_details(self, event_id: str) -> dict:
        match = await self.db.fetchone("SELECT * FROM matches WHERE event_id=?", (event_id,))
        if not match:
            return {}
        recent = await self.db.fetchall(
            "SELECT * FROM live_events WHERE event_id=? AND event_type!='clock_update' ORDER BY id DESC LIMIT 20",
            (event_id,),
        )
        external = {}
        five_id = match["five_dollar_fixture_id"] if "five_dollar_fixture_id" in match.keys() else None
        if five_id:
            try:
                external = await self.odds_api.fetch_five_dollar_details(int(five_id))
            except Exception:
                external = {}
        if not external:
            fixture_id = match["api_football_fixture_id"] if "api_football_fixture_id" in match.keys() else None
            if fixture_id and (SETTINGS.api_football_key or SETTINGS.rapidapi_key):
                try:
                    external = await self.odds_api.fetch_api_football_details(int(fixture_id))
                except Exception:
                    external = {}
        return {"match": match, "events": list(reversed(recent)), "external": external}

    @staticmethod
    def _team_display_key(value: str | None) -> str:
        """Normalize provider-specific club names for display de-duplication.

        Providers routinely alternate between forms such as Torino/Torino FC,
        Roma/AS Roma, Como/Como 1907 or Parma/Parma Calcio 1913. This key is
        deliberately display-only: database/provider ids remain untouched.
        """
        import re
        import unicodedata
        text = unicodedata.normalize("NFKD", str(value or ""))
        text = "".join(c for c in text if not unicodedata.combining(c)).lower()
        words = re.findall(r"[a-z0-9]+", text)
        noise = {"fc", "cf", "ac", "afc", "ssc", "calcio", "club", "football", "futbol"}
        words = [w for w in words if w not in noise and not re.fullmatch(r"(?:18|19|20)\d{2}", w)]
        # Short legal prefixes are useful only when they are part of a longer
        # distinctive name. Dropping them solves AS Roma / Roma, FC Barcelona / Barcelona.
        while len(words) > 1 and words[0] in {"as", "fc", "ac", "ssc", "cf"}:
            words.pop(0)
        return "".join(words)

    def _dedupe_match_rows(self, rows, limit: int = 25):
        """Collapse the same real fixture returned by multiple providers."""
        phase_rank = {
            "pending": 0, "kickoff_wait": 1, "suspended": 2,
            "live": 3, "first_half": 4, "halftime": 5, "second_half": 6,
            "extra_time": 7, "penalties": 8, "finished": 9,
            "postponed": 9, "cancelled": 9,
        }
        source_rank = {
            "5dollarfootballapi": 100, "5dollar": 100, "api-football": 90,
            "sofascore live": 80, "sofascore": 75, "espn": 70,
            "fotmob": 65, "thesportsdb": 50, "horloge oddium": 10,
        }
        best = {}
        for row in rows:
            try:
                kickoff = parse_iso(str(row["commence_time"])).astimezone(timezone.utc)
                # Provider kickoff timestamps can differ slightly; hour bucket plus teams
                # is more stable than the provider event id.
                bucket = kickoff.strftime("%Y-%m-%d-%H")
            except Exception:
                bucket = str(row["commence_time"] or "")[:13]
            key = (
                str(row["sport_key"]), bucket,
                self._team_display_key(row["home_team"]),
                self._team_display_key(row["away_team"]),
            )
            phase = str(row["live_phase"] or row["match_status"] or "pending").lower()
            source = str(row["live_source"] or "").lower()
            score_known = int(row["home_score"] is not None and row["away_score"] is not None)
            clock = str(row["live_clock"] or "")
            minute = 0
            try:
                minute = int(''.join(ch for ch in clock.split('+', 1)[0] if ch.isdigit()) or 0)
            except Exception:
                pass
            rank = (phase_rank.get(phase, 0), score_known, minute, source_rank.get(source, 20))
            current = best.get(key)
            if current is None or rank > current[0]:
                best[key] = (rank, row)
        unique = [pair[1] for pair in best.values()]
        unique.sort(key=lambda r: (int(r["completed"] or 0), str(r["commence_time"])))
        return unique[:limit]

    async def live_matches(self, limit: int = 25):
        """Rows displayed by the permanent live-score panel, de-duplicated across providers."""
        now = datetime.now(timezone.utc)
        start = (now - timedelta(minutes=SETTINGS.live_panel_lookback_minutes)).isoformat()
        end = (now + timedelta(minutes=SETTINGS.live_panel_lookahead_minutes)).isoformat()
        live_keys = list(COMPETITIONS.keys())
        placeholders = ",".join("?" for _ in live_keys)
        recent_terminal = (now - timedelta(minutes=SETTINGS.live_finished_display_minutes)).isoformat()
        rows = await self.db.fetchall(
            f"""SELECT * FROM matches
                WHERE sport_key IN ({placeholders})
                  AND commence_time BETWEEN ? AND ?
                  AND (
                       live_phase IN ('kickoff_wait','live','first_half','halftime','second_half','extra_time','penalties','suspended')
                       OR (live_phase IN ('finished','cancelled','postponed') AND last_score_update>=?)
                  )
                ORDER BY completed ASC, commence_time ASC LIMIT ?""",
            tuple(live_keys) + (start, end, recent_terminal, max(limit * 4, 100)),
        )
        return self._dedupe_match_rows(rows, limit)

    async def record_live_event(self, event: dict) -> None:
        """Persist the timeline used by the Live Center."""
        if not event.get("event_id") or event.get("type") in {None, "hello"}:
            return
        await self.db.execute(
            """INSERT INTO live_events(event_id,event_type,phase,clock,home_score,away_score,detail,source,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (str(event["event_id"]), str(event.get("type") or "live_update"), event.get("phase"), event.get("clock"),
             event.get("home_score"), event.get("away_score"), event.get("detail"), event.get("source"), utcnow_iso()),
        )
        # Keep storage bounded while preserving a rich recent timeline.
        await self.db.execute(
            "DELETE FROM live_events WHERE event_id=? AND id NOT IN (SELECT id FROM live_events WHERE event_id=? ORDER BY id DESC LIMIT 150)",
            (str(event["event_id"]), str(event["event_id"])),
        )

    async def recent_live_events(self, event_id: str, limit: int = 6):
        return await self.db.fetchall(
            "SELECT * FROM live_events WHERE event_id=? AND event_type!='clock_update' ORDER BY id DESC LIMIT ?",
            (event_id, limit),
        )

    async def toggle_match_follow(self, user_id: int, event_id: str) -> bool:
        row = await self.db.fetchone("SELECT 1 FROM match_follows WHERE user_id=? AND event_id=?", (user_id, event_id))
        if row:
            await self.db.execute("DELETE FROM match_follows WHERE user_id=? AND event_id=?", (user_id, event_id))
            return False
        await self.db.execute("INSERT OR IGNORE INTO match_follows(user_id,event_id,created_at) VALUES(?,?,?)", (user_id,event_id,utcnow_iso()))
        return True

    async def followed_event_ids(self, user_id: int) -> set[str]:
        rows = await self.db.fetchall("SELECT event_id FROM match_follows WHERE user_id=?", (user_id,))
        return {str(r["event_id"]) for r in rows}

    async def followers_for_event(self, event_id: str):
        return await self.db.fetchall("SELECT user_id FROM match_follows WHERE event_id=?", (event_id,))

    async def followed_matches(self, user_id: int, limit: int = 25):
        return await self.db.fetchall(
            """SELECT m.* FROM match_follows f JOIN matches m ON m.event_id=f.event_id
               WHERE f.user_id=? ORDER BY m.commence_time DESC LIMIT ?""",
            (user_id, limit),
        )

    async def user_live_bets(self, user_id: int, limit: int = 10):
        return await self.db.fetchall(
            """SELECT b.*,m.home_team,m.away_team,m.home_score,m.away_score,m.live_phase,m.live_clock
               FROM bets b JOIN matches m ON m.event_id=b.event_id
               WHERE b.user_id=? AND b.status='PENDING'
                 AND m.live_phase IN ('kickoff_wait','live','first_half','halftime','second_half','extra_time','penalties','suspended')
               ORDER BY b.created_at DESC LIMIT ?""", (user_id, limit))

    async def settle_event(self, event_id: str, home_score: int, away_score: int) -> list[dict]:
        result = "HOME" if home_score > away_score else "AWAY" if home_score < away_score else "DRAW"
        match = await self.db.fetchone("SELECT * FROM matches WHERE event_id=?", (event_id,))
        bets = await self.db.fetchall("SELECT * FROM bets WHERE event_id=? AND status='PENDING'", (event_id,))
        events: list[dict] = []
        for bet in bets:
            bet_id = int(bet["id"])
            user_id = int(bet["user_id"])
            if bet["selection"] == result:
                payout = int(bet["potential_payout"])
                # Idempotent credit: safe if a crash occurs before status update.
                await self.economy.credit(user_id, payout, "BET_WIN", f"BET-{bet_id}")
                status = "WON"
            else:
                payout = 0
                status = "LOST"
            await self.db.execute(
                "UPDATE bets SET status=?,settled_at=?,payout=?,settlement_note=? WHERE id=? AND status='PENDING'",
                (status, utcnow_iso(), payout, f"Résultat {home_score}-{away_score}", bet_id),
            )
            events.append({
                "user_id": user_id, "bet_id": bet_id, "status": status, "payout": payout,
                "stake": int(bet["stake"]), "odd": float(bet["odd"]), "selection": bet["selection"],
                "home_score": home_score, "away_score": away_score,
                "home_team": match["home_team"] if match else "Domicile",
                "away_team": match["away_team"] if match else "Extérieur",
            })
        combo_events = await self.settle_combo_for_event(event_id, home_score, away_score)
        for ev in combo_events:
            ev["home_score"] = home_score
            ev["away_score"] = away_score
            ev["home_team"] = match["home_team"] if match else "Domicile"
            ev["away_team"] = match["away_team"] if match else "Extérieur"
            ev["is_combo"] = True
        events.extend(combo_events)
        return events

    async def settle_combo_for_event(self, event_id: str, home_score: int, away_score: int) -> list[dict]:
        result = "HOME" if home_score > away_score else "AWAY" if home_score < away_score else "DRAW"
        legs = await self.db.fetchall(
            "SELECT * FROM combo_legs WHERE event_id=? AND status='PENDING'", (event_id,)
        )
        notifications: list[dict] = []
        touched: set[int] = set()
        for leg in legs:
            combo_id = int(leg["combo_id"])
            touched.add(combo_id)
            status = "WON" if str(leg["selection"]) == result else "LOST"
            await self.db.execute(
                "UPDATE combo_legs SET status=?,result=? WHERE id=? AND status='PENDING'",
                (status, result, int(leg["id"])),
            )

        for combo_id in touched:
            combo = await self.db.fetchone("SELECT * FROM combo_bets WHERE id=? AND status='PENDING'", (combo_id,))
            if not combo:
                continue
            combo_legs = await self.db.fetchall("SELECT * FROM combo_legs WHERE combo_id=? ORDER BY id", (combo_id,))
            statuses = [str(x["status"]) for x in combo_legs]
            final_status = None
            payout = 0
            note = None
            if "LOST" in statuses:
                final_status = "LOST"
                note = "Au moins une sélection est perdante"
            elif statuses and all(x in {"WON", "VOID"} for x in statuses):
                valid = [x for x in combo_legs if str(x["status"]) == "WON"]
                if not valid:
                    final_status = "VOID"
                    payout = int(combo["stake"])
                    note = "Toutes les sélections ont été annulées"
                else:
                    effective_odd = 1.0
                    for x in valid:
                        effective_odd *= float(x["odd"])
                    payout = math.floor(int(combo["stake"]) * effective_odd)
                    final_status = "WON"
                    note = f"Combiné validé • cote effective {effective_odd:.2f}"
            if final_status:
                if final_status in {"WON", "VOID"}:
                    await self.economy.credit(int(combo["user_id"]), payout, "COMBO_WIN" if final_status == "WON" else "COMBO_VOID", f"COMBO-{combo_id}")
                await self.db.execute(
                    "UPDATE combo_bets SET status=?,settled_at=?,payout=?,settlement_note=? WHERE id=? AND status='PENDING'",
                    (final_status, utcnow_iso(), payout, note, combo_id),
                )
                notifications.append({
                    "user_id": int(combo["user_id"]), "combo_id": combo_id, "status": final_status,
                    "payout": payout, "stake": int(combo["stake"]), "odd": float(combo["total_odd"]),
                })
        return notifications

    async def void_event(self, event_id: str, admin_id: int | None = None, note: str = "Match annulé / remboursé") -> int:
        bets = await self.db.fetchall("SELECT * FROM bets WHERE event_id=? AND status='PENDING'", (event_id,))
        count = 0
        for bet in bets:
            bet_id = int(bet["id"])
            await self.economy.credit(int(bet["user_id"]), int(bet["stake"]), "BET_VOID", f"BET-{bet_id}")
            await self.db.execute(
                "UPDATE bets SET status='VOID',settled_at=?,payout=?,settlement_note=? WHERE id=? AND status='PENDING'",
                (utcnow_iso(), int(bet["stake"]), note, bet_id),
            )
            count += 1
        await self.db.execute("UPDATE matches SET cancelled=1 WHERE event_id=?", (event_id,))
        await self.db.log_admin(admin_id, "VOID_EVENT", f"{event_id} • {count} paris • {note}")
        return count

    async def matches_for_window(self, sport_key: str, window: str, limit: int = 25):
        now_local = datetime.now(PARIS_TZ)
        if window == "today":
            start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
        elif window == "tomorrow":
            start = (now_local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
        else:
            start = now_local
            end = now_local + timedelta(days=7)
        rows = await self.db.fetchall(
            """SELECT * FROM matches WHERE sport_key=? AND commence_time>=? AND commence_time<?
               AND completed=0 AND cancelled=0 AND odds_available=1
               ORDER BY commence_time ASC LIMIT ?""",
            (sport_key, start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat(), max(limit * 3, 75)),
        )
        return self._dedupe_match_rows(rows, limit)

    async def current_and_future_matches(self, limit: int = 12):
        active = await self.active_competitions()
        if not active:
            return []
        placeholders = ",".join("?" for _ in active)
        start = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat()
        return await self.db.fetchall(
            f"""SELECT * FROM matches WHERE sport_key IN ({placeholders}) AND commence_time BETWEEN ? AND ?
                AND cancelled=0 ORDER BY commence_time ASC LIMIT ?""",
            tuple(active) + (start, end, limit),
        )

    async def odds_trend(self, event_id: str) -> dict[str, str]:
        rows = await self.db.fetchall(
            "SELECT home_odd,draw_odd,away_odd FROM odds_history WHERE event_id=? ORDER BY id DESC LIMIT 2", (event_id,)
        )
        if len(rows) < 2:
            return {"home": "→", "draw": "→", "away": "→"}
        latest, previous = rows[0], rows[1]
        def arrow(a, b):
            return "↑" if float(a) > float(b) else "↓" if float(a) < float(b) else "→"
        return {
            "home": arrow(latest["home_odd"], previous["home_odd"]),
            "draw": arrow(latest["draw_odd"], previous["draw_odd"]),
            "away": arrow(latest["away_odd"], previous["away_odd"]),
        }

    async def place_bet(self, user_id: int, event_id: str, selection: str, stake: int, displayed_odd: float):
        if bool(await self.db.get_setting("betting_paused")):
            return False, "Les paris sont temporairement suspendus.", None
        if selection not in {"HOME", "DRAW", "AWAY"}:
            return False, "Sélection invalide.", None
        balance = await self.economy.get_balance(user_id)
        dynamic_max = min(SETTINGS.max_stake, max(0, math.floor(balance * SETTINGS.max_stake_balance_percent / 100)))
        if stake < SETTINGS.min_stake or stake > dynamic_max:
            return False, f"Mise autorisée : {SETTINGS.min_stake} à {dynamic_max} {SETTINGS.currency_name}.", None
        match = await self.db.fetchone("SELECT * FROM matches WHERE event_id=?", (event_id,))
        if not match or match["cancelled"] or match["completed"]:
            return False, "Ce match n'est plus disponible.", None
        kickoff = parse_iso(match["commence_time"])
        if datetime.now(timezone.utc) >= kickoff - timedelta(seconds=SETTINGS.lock_seconds_before_kickoff):
            return False, "Les paris sont fermés pour ce match.", None
        odd_field = {"HOME": "home_odd", "DRAW": "draw_odd", "AWAY": "away_odd"}[selection]
        current_odd = float(match[odd_field])
        if abs(current_odd - float(displayed_odd)) > 0.0001:
            return False, "ODD_CHANGED", {"current_odd": current_odd, "match": match}
        if not SETTINGS.allow_multiple_bets_same_event:
            existing = await self.db.fetchone("SELECT id FROM bets WHERE user_id=? AND event_id=? AND status='PENDING'", (user_id, event_id))
            if existing:
                return False, "Tu as déjà un pari actif sur ce match.", None

        await self.ensure_preferences(user_id)
        payout = math.floor(stake * current_odd)
        # Create bet first so the ledger reference is a stable BET-id.
        bet_id = await self.db.execute(
            """INSERT INTO bets(user_id,event_id,selection,odd,stake,potential_payout,status,created_at)
               VALUES(?,?,?,?,?,?, 'CREATING', ?)""",
            (user_id, event_id, selection, current_odd, stake, payout, utcnow_iso()),
        )
        ok = await self.economy.debit(user_id, stake, "BET_STAKE", f"BET-{bet_id}")
        if not ok:
            await self.db.execute("DELETE FROM bets WHERE id=? AND status='CREATING'", (bet_id,))
            return False, "Solde insuffisant.", None
        await self.db.execute("UPDATE bets SET status='PENDING' WHERE id=? AND status='CREATING'", (bet_id,))
        return True, "OK", {"bet_id": bet_id, "odd": current_odd, "payout": payout, "match": match}

    async def place_combo_bet(self, user_id: int, legs: list[dict], stake: int):
        if bool(await self.db.get_setting("betting_paused")):
            return False, "Les paris sont temporairement suspendus.", None
        if len(legs) < 2:
            return False, "Un pari combiné doit contenir au moins 2 matchs.", None
        if len(legs) > 10:
            return False, "Maximum 10 sélections dans un combiné.", None
        balance = await self.economy.get_balance(user_id)
        dynamic_max = min(SETTINGS.max_stake, max(0, math.floor(balance * SETTINGS.max_stake_balance_percent / 100)))
        if stake < SETTINGS.min_stake or stake > dynamic_max:
            return False, f"Mise autorisée : {SETTINGS.min_stake} à {dynamic_max} {SETTINGS.currency_name}.", None

        checked = []
        seen = set()
        total_odd = 1.0
        now = datetime.now(timezone.utc)
        for leg in legs:
            event_id = str(leg.get("event_id") or "")
            selection = str(leg.get("selection") or "")
            displayed_odd = float(leg.get("odd") or 0)
            if not event_id or event_id in seen or selection not in {"HOME", "DRAW", "AWAY"}:
                return False, "Sélection combinée invalide ou match dupliqué.", None
            seen.add(event_id)
            match = await self.db.fetchone("SELECT * FROM matches WHERE event_id=?", (event_id,))
            if not match or match["cancelled"] or match["completed"]:
                return False, "Un des matchs du combiné n'est plus disponible.", None
            kickoff = parse_iso(match["commence_time"])
            if now >= kickoff - timedelta(seconds=SETTINGS.lock_seconds_before_kickoff):
                return False, f"Les paris sont fermés pour {match['home_team']} - {match['away_team']}.", None
            odd_field = {"HOME": "home_odd", "DRAW": "draw_odd", "AWAY": "away_odd"}[selection]
            current_odd = float(match[odd_field])
            if abs(current_odd - displayed_odd) > 0.0001:
                return False, "ODD_CHANGED", {"event_id": event_id, "current_odd": current_odd, "match": match}
            checked.append((event_id, selection, current_odd, match))
            total_odd *= current_odd

        payout = math.floor(stake * total_odd)
        combo_id = await self.db.execute(
            "INSERT INTO combo_bets(user_id,total_odd,stake,potential_payout,status,created_at) VALUES(?,?,?,?, 'CREATING', ?)",
            (user_id, total_odd, stake, payout, utcnow_iso()),
        )
        ok = await self.economy.debit(user_id, stake, "COMBO_STAKE", f"COMBO-{combo_id}")
        if not ok:
            await self.db.execute("DELETE FROM combo_bets WHERE id=? AND status='CREATING'", (combo_id,))
            return False, "Solde insuffisant.", None
        for event_id, selection, odd, _ in checked:
            await self.db.execute(
                "INSERT INTO combo_legs(combo_id,event_id,selection,odd,status) VALUES(?,?,?,?, 'PENDING')",
                (combo_id, event_id, selection, odd),
            )
        await self.db.execute("UPDATE combo_bets SET status='PENDING' WHERE id=? AND status='CREATING'", (combo_id,))
        return True, "OK", {"combo_id": combo_id, "total_odd": total_odd, "payout": payout, "legs": checked}

    async def user_combo_bets(self, user_id: int, limit: int = 10):
        combos = await self.db.fetchall("SELECT * FROM combo_bets WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit))
        out=[]
        for c in combos:
            legs = await self.db.fetchall(
                """SELECT l.*,m.home_team,m.away_team,m.commence_time FROM combo_legs l JOIN matches m ON m.event_id=l.event_id WHERE l.combo_id=? ORDER BY m.commence_time""",
                (int(c["id"]),),
            )
            out.append((c, legs))
        return out

    async def user_bets(self, user_id: int, status: str | None = None, limit: int = 20):
        where = "b.user_id=?"
        params: list = [user_id]
        if status:
            where += " AND b.status=?"
            params.append(status)
        params.append(limit)
        return await self.db.fetchall(
            f"""SELECT b.*,m.home_team,m.away_team,m.commence_time,m.home_score,m.away_score,m.competition_name
                FROM bets b JOIN matches m ON m.event_id=b.event_id
                WHERE {where} ORDER BY b.created_at DESC LIMIT ?""",
            tuple(params),
        )

    async def user_stats(self, user_id: int):
        return await self.db.fetchone(
            """SELECT COUNT(*) total,
               SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) wins,
               SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) losses,
               COALESCE(SUM(CASE WHEN status IN ('WON','LOST') THEN stake ELSE 0 END),0) wagered,
               COALESCE(SUM(CASE WHEN status='WON' THEN payout ELSE 0 END),0) returned,
               COALESCE(MAX(CASE WHEN status='WON' THEN payout ELSE 0 END),0) biggest_win,
               COALESCE(MAX(CASE WHEN status='WON' THEN odd ELSE 0 END),0) biggest_odd
               FROM bets WHERE user_id=?""",
            (user_id,),
        )

    async def leaderboard(self, limit: int = 10):
        return await self.db.fetchall(
            """SELECT user_id,
               SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) wins,
               SUM(CASE WHEN status IN ('WON','LOST') THEN 1 ELSE 0 END) settled,
               SUM(CASE WHEN status='WON' THEN payout-stake WHEN status='LOST' THEN -stake ELSE 0 END) net
               FROM bets GROUP BY user_id HAVING settled > 0 ORDER BY net DESC LIMIT ?""",
            (limit,),
        )

    async def toggle_favorite(self, user_id: int, team_name: str) -> bool:
        row = await self.db.fetchone("SELECT 1 FROM favorites WHERE user_id=? AND team_name=?", (user_id, team_name))
        if row:
            await self.db.execute("DELETE FROM favorites WHERE user_id=? AND team_name=?", (user_id, team_name))
            return False
        await self.db.execute("INSERT INTO favorites(user_id,team_name,created_at) VALUES(?,?,?)", (user_id, team_name, utcnow_iso()))
        return True

    async def favorites(self, user_id: int):
        return await self.db.fetchall("SELECT team_name FROM favorites WHERE user_id=? ORDER BY team_name", (user_id,))

    async def favorite_matches(self, user_id: int, limit: int = 20):
        favs = [r["team_name"] for r in await self.favorites(user_id)]
        if not favs:
            return []
        placeholders = ",".join("?" for _ in favs)
        now = datetime.now(timezone.utc).isoformat()
        params = tuple(favs + favs + [now, limit])
        return await self.db.fetchall(
            f"""SELECT * FROM matches WHERE (home_team IN ({placeholders}) OR away_team IN ({placeholders}))
               AND commence_time>? AND completed=0 AND cancelled=0 ORDER BY commence_time LIMIT ?""",
            params,
        )

    async def ensure_preferences(self, user_id: int):
        await self.db.execute(
            "INSERT OR IGNORE INTO user_preferences(user_id,dm_notifications,notify_result,notify_before_match,notify_odds_change) VALUES(?,?,?,?,?)",
            (user_id, 1 if SETTINGS.dm_notifications_default else 0, 1, 1, 0),
        )
        return await self.db.fetchone("SELECT * FROM user_preferences WHERE user_id=?", (user_id,))

    async def toggle_preference(self, user_id: int, field: str) -> bool:
        allowed = {"dm_notifications", "notify_result", "notify_before_match", "notify_odds_change"}
        if field not in allowed:
            raise ValueError("Préférence inconnue")
        await self.ensure_preferences(user_id)
        row = await self.db.fetchone(f"SELECT {field} FROM user_preferences WHERE user_id=?", (user_id,))
        new = 0 if int(row[field]) else 1
        await self.db.execute(f"UPDATE user_preferences SET {field}=? WHERE user_id=?", (new, user_id))
        return bool(new)

    async def mark_notification_sent(self, user_id: int, kind: str, reference: str) -> bool:
        try:
            await self.db.execute(
                "INSERT INTO notification_log(user_id,kind,reference,sent_at) VALUES(?,?,?,?)",
                (user_id, kind, reference, utcnow_iso()),
            )
            return True
        except Exception:
            return False

    async def pending_match_reminders(self):
        now = datetime.now(timezone.utc)
        target_end = now + timedelta(minutes=SETTINGS.notify_before_minutes)
        return await self.db.fetchall(
            """SELECT DISTINCT b.user_id,b.event_id,m.home_team,m.away_team,m.commence_time
               FROM bets b JOIN matches m ON m.event_id=b.event_id
               JOIN user_preferences p ON p.user_id=b.user_id
               LEFT JOIN notification_log n ON n.user_id=b.user_id AND n.kind='MATCH_REMINDER' AND n.reference=b.event_id
               WHERE b.status='PENDING' AND p.dm_notifications=1 AND p.notify_before_match=1
               AND m.commence_time>? AND m.commence_time<=? AND n.id IS NULL""",
            (now.isoformat(), target_end.isoformat()),
        )


    async def pending_odds_change_notifications(self):
        """Return users whose pending selection odd changed since their bet.

        notification_log makes each captured odds change one-shot per user/event.
        """
        rows = await self.db.fetchall(
            """SELECT b.user_id,b.id bet_id,b.event_id,b.selection,b.odd bet_odd,
                      m.home_team,m.away_team,m.home_odd,m.draw_odd,m.away_odd,
                      h.captured_at
               FROM bets b
               JOIN matches m ON m.event_id=b.event_id
               JOIN user_preferences p ON p.user_id=b.user_id
               JOIN odds_history h ON h.id=(SELECT MAX(h2.id) FROM odds_history h2 WHERE h2.event_id=b.event_id)
               LEFT JOIN notification_log n ON n.user_id=b.user_id AND n.kind='ODDS_CHANGE'
                 AND n.reference=(b.event_id || ':' || h.captured_at)
               WHERE b.status='PENDING' AND p.dm_notifications=1 AND p.notify_odds_change=1 AND n.id IS NULL"""
        )
        out = []
        for r in rows:
            current = float(r[{"HOME":"home_odd","DRAW":"draw_odd","AWAY":"away_odd"}[r["selection"]]])
            if abs(current - float(r["bet_odd"])) > 0.0001:
                out.append({**dict(r), "current_odd": current, "reference": f"{r['event_id']}:{r['captured_at']}"})
        return out

    async def api_status(self) -> dict:
        used_api, remaining_api = await self.odds_api.quota_status()
        pending = await self.db.fetchone("SELECT COUNT(*) c FROM bets WHERE status='PENDING'")
        matches = await self.db.fetchone(
            "SELECT COUNT(*) c FROM matches WHERE completed=0 AND cancelled=0 AND commence_time>?",
            (datetime.now(timezone.utc).isoformat(),),
        )
        diagnostics = []
        for key in await self.active_competitions():
            diagnostics.append({
                "key": key,
                "name": COMPETITIONS.get(key, {}).get("name", key),
                "events": int(await self.db.get_setting(f"diag_events_{key}") or 0),
                "odds": int(await self.db.get_setting(f"diag_odds_{key}") or 0),
                "parsed": int(await self.db.get_setting(f"diag_parsed_{key}") or 0),
                "live_rows": int(await self.db.get_setting(f"diag_scores_{key}") or 0),
                "live_source": str(await self.db.get_setting(f"diag_scores_source_{key}") or "—"),
                "odds_source": str(await self.db.get_setting(f"diag_odds_source_{key}") or "Oddium Fusion"),
            })
        try:
            selected_books = await self.odds_api.selected_bookmakers()
        except Exception as exc:
            selected_books = []
            if not self.odds_api.last_error:
                self.odds_api.last_error = str(exc)
        five = self.odds_api.five_dollar
        return {
            "remaining": five.rate_limit_remaining if SETTINGS.five_dollar_api_key else remaining_api,
            "used": used_api,
            "last_status": five.last_status if SETTINGS.five_dollar_api_key else self.odds_api.last_status,
            "last_error": five.last_error or self.odds_api.last_error or self.last_error,
            "five_dollar_enabled": bool(SETTINGS.five_dollar_api_key),
            "five_dollar_remaining": five.rate_limit_remaining,
            "five_dollar_limit": five.rate_limit_limit,
            "pending_bets": int(pending["c"] if pending else 0),
            "future_matches": int(matches["c"] if matches else 0),
            "diagnostics": diagnostics,
            "selected_bookmakers": selected_books,
        }
