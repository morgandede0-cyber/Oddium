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
from .live_identity import event_fingerprint

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
        # Per-fixture throttle for direct 5Dollar reconciliation of stale kickoff rows.
        self._last_five_dollar_reconcile: dict[str, datetime] = {}
        # Separate throttle for old fixtures that still have open tickets.
        self._last_ticket_reconcile: dict[str, datetime] = {}
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
                            cand = await self._find_existing_fixture_for_provider_event(key, event)
                            if cand is not None:
                                target_id = str(cand["event_id"])
                                existing = cand
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
                    if five_id:
                        await self.bind_provider_fixture("5dollar", five_id, target_id)
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

    async def _expire_unconfirmed_kickoffs(self) -> int:
        """Stop showing a fake live match when no provider ever confirmed kickoff.

        ``kickoff_wait`` is only a short grace state.  A football match must not stay
        orange for hours after it has actually ended just because the schedule clock
        fired and the live feed did not match the fixture.  After 150 minutes, the
        row is returned to a neutral pending/result-wait state and disappears from
        the Live panel.  A later provider result can still update and settle it.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=150)).isoformat()
        rows = await self.db.fetchall(
            """SELECT event_id FROM matches
               WHERE completed=0 AND cancelled=0
                 AND live_phase='kickoff_wait'
                 AND commence_time < ?""",
            (cutoff,),
        )
        for row in rows:
            await self.db.execute(
                """UPDATE matches
                   SET live_phase='pending', match_status='pending', live_clock=NULL,
                       live_detail='Résultat en attente de confirmation',
                       live_source=NULL
                   WHERE event_id=? AND live_phase='kickoff_wait'""",
                (str(row["event_id"]),),
            )
        return len(rows)

    async def _five_dollar_reconcile_rows(self, sport_key: str) -> list[dict]:
        """Ask 5Dollar directly for stale scheduled rows that should already be live/finished.

        The global ``status=live`` endpoint stops returning a fixture once it is over.
        If Oddium missed the live transition, a row could otherwise remain in
        ``kickoff_wait`` forever.  Known 5Dollar fixture ids are therefore checked
        directly, at most once every 5 minutes per fixture.
        """
        if not SETTINGS.five_dollar_api_key:
            return []
        now = datetime.now(timezone.utc)
        lo = (now - timedelta(hours=4)).isoformat()
        hi = (now - timedelta(minutes=15)).isoformat()
        rows = await self.db.fetchall(
            """SELECT event_id,five_dollar_fixture_id FROM matches
               WHERE sport_key=? AND completed=0 AND cancelled=0
                 AND live_phase='kickoff_wait'
                 AND five_dollar_fixture_id IS NOT NULL
                 AND commence_time BETWEEN ? AND ?
               ORDER BY commence_time ASC LIMIT 4""",
            (sport_key, lo, hi),
        )
        out: list[dict] = []
        for row in rows:
            event_id = str(row["event_id"] or "")
            last = self._last_five_dollar_reconcile.get(event_id)
            if last and (now - last).total_seconds() < 300:
                continue
            self._last_five_dollar_reconcile[event_id] = now
            try:
                details = await self.odds_api.fetch_five_dollar_details(int(row["five_dollar_fixture_id"]), force=True)
                shell = (details or {}).get("fixture") or {}
                if shell:
                    out.append(shell)
            except Exception:
                continue
        return out

    async def _reconcile_open_tickets(self) -> list[dict]:
        """Resolve old pending tickets independently from the 5Dollar live engine.

        Result priority is deliberately separate from the odds provider:
        football-data.org -> Sofascore -> ESPN/FotMob/TheSportsDB -> 5Dollar last resort.
        This prevents a missing 5Dollar historical fixture from blocking settlement.
        """
        now = datetime.now(timezone.utc)
        oldest = (now - timedelta(days=7)).isoformat()
        newest = (now - timedelta(minutes=105)).isoformat()
        rows = await self.db.fetchall(
            """SELECT DISTINCT m.* FROM matches m
               WHERE m.cancelled=0 AND m.commence_time BETWEEN ? AND ?
                 AND (
                   EXISTS(SELECT 1 FROM bets b WHERE b.event_id=m.event_id AND b.status='PENDING')
                   OR EXISTS(SELECT 1 FROM combo_legs cl JOIN combo_bets cb ON cb.id=cl.combo_id
                             WHERE cl.event_id=m.event_id AND cl.status='PENDING' AND cb.status='PENDING')
                 )
               ORDER BY m.commence_time ASC LIMIT 50""",
            (oldest, newest),
        )
        settlements: list[dict] = []
        result_feeds: dict[str, list[dict]] = {}

        async def finish(row, parsed):
            event_id = str(row["event_id"])
            hs, aws = parsed.get("home_score"), parsed.get("away_score")
            if hs is None or aws is None:
                return False
            hs_i, aws_i = int(hs), int(aws)
            fid = parsed.get("five_dollar_fixture_id")
            await self.db.execute(
                """UPDATE matches SET home_score=?,away_score=?,completed=1,cancelled=0,
                   match_status='finished',live_phase='finished',live_clock=NULL,
                   live_source=COALESCE(?,live_source),five_dollar_fixture_id=COALESCE(?,five_dollar_fixture_id),
                   last_score_update=? WHERE event_id=?""",
                (hs_i, aws_i, parsed.get("source") or "Result fallback", fid, utcnow_iso(), event_id),
            )
            settlements.extend(await self.settle_event(event_id, hs_i, aws_i))
            return True

        def match_candidate(row, shell):
            # Always normalize provider shells before deciding whether a match is final.
            # ESPN/Sofascore/FotMob do not all use the literal word "finished".
            parsed = self.odds_api.parse_score_shell(shell, str(row["sport_key"])) or shell
            if str(parsed.get("status") or "").lower() != "finished":
                return None
            shell = parsed
            normal = (self._teams_equivalent(row["home_team"], shell.get("home_team")) and
                      self._teams_equivalent(row["away_team"], shell.get("away_team")))
            reversed_teams = (self._teams_equivalent(row["home_team"], shell.get("away_team")) and
                              self._teams_equivalent(row["away_team"], shell.get("home_team")))
            if not normal and not reversed_teams:
                return None
            try:
                a = datetime.fromisoformat(str(row["commence_time"]).replace("Z", "+00:00"))
                b = datetime.fromisoformat(str(shell.get("commence_time") or "").replace("Z", "+00:00"))
                if a.tzinfo is None: a = a.replace(tzinfo=timezone.utc)
                if b.tzinfo is None: b = b.replace(tzinfo=timezone.utc)
                delta = abs((a - b).total_seconds())
            except Exception:
                delta = 0
            if delta > 12 * 3600:
                return None
            if reversed_teams:
                shell = dict(shell)
                shell["home_score"], shell["away_score"] = shell.get("away_score"), shell.get("home_score")
            return delta, shell

        for row in rows:
            event_id = str(row["event_id"])
            if int(row["completed"] or 0) and row["home_score"] is not None and row["away_score"] is not None:
                settlements.extend(await self.settle_event(event_id, int(row["home_score"]), int(row["away_score"])))
                continue
            last = self._last_ticket_reconcile.get(event_id)
            if last and (now - last).total_seconds() < 120:
                continue
            self._last_ticket_reconcile[event_id] = now
            sport_key = str(row["sport_key"])

            # Build one independent result feed per competition. football-data.org is
            # first when configured; public score providers supplement it. 5Dollar is
            # intentionally LAST and is no longer required to settle a ticket.
            if sport_key not in result_feeds:
                feed: list[dict] = []
                try:
                    feed.extend(await self.odds_api.fetch_scores(sport_key, days_from=7, force=True) or [])
                except Exception:
                    pass
                # Historical settlement is intentionally independent from 5Dollar.
                # Query the actual calendar days of unresolved tickets, not only today's
                # scoreboard (the old bug that left yesterday's matches orange).
                for days_ago in range(0, 8):
                    target_day = (now - timedelta(days=days_ago)).date()
                    for method_name in ("fetch_sofascore_scores", "fetch_espn_scores"):
                        try:
                            method = getattr(self.odds_api, method_name, None)
                            if method:
                                feed.extend(await method(sport_key, force=True, date=target_day) or [])
                        except Exception:
                            pass
                # Extra current-day fallbacks remain useful, but settlement does not
                # require them and never requires 5Dollar.
                for method_name in ("fetch_fotmob_scores", "fetch_thesportsdb_scores"):
                    try:
                        method = getattr(self.odds_api, method_name, None)
                        if method:
                            feed.extend(await method(sport_key, force=True) or [])
                    except Exception:
                        pass
                result_feeds[sport_key] = feed

            candidates = []
            for shell in result_feeds[sport_key]:
                found = match_candidate(row, shell)
                if found:
                    candidates.append(found)
            if candidates:
                candidates.sort(key=lambda x: x[0])
                if await finish(row, candidates[0][1]):
                    continue

            # No 5Dollar fallback here by design: final-result settlement is
            # isolated from the odds/live provider. Unresolved tickets remain pending
            # until an independent result source confirms the final score.
        return settlements

    async def reconcile_open_tickets(self) -> list[dict]:
        """Public entry point used by the ticket screen for immediate self-healing."""
        return await self._reconcile_open_tickets()

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

    @staticmethod
    def _phase_transition_allowed(old_phase: str | None, new_phase: str | None) -> bool:
        """Monotonic football state machine inspired by robust live trackers.

        Providers occasionally answer out of order (for example 2H then 1H on the
        next poll). Oddium accepts legitimate pauses/resumes but rejects impossible
        backwards transitions. Terminal states stay terminal.
        """
        old = str(old_phase or "pending").lower()
        new = str(new_phase or "pending").lower()
        if old == new:
            return True
        terminal = {"finished", "cancelled", "postponed"}
        if old in terminal:
            return False
        if new in terminal:
            return True
        # A suspended game may resume in the same/later football period.
        if old == "suspended":
            return new in {"live", "first_half", "halftime", "second_half", "extra_time", "penalties", "suspended"}
        if new == "suspended":
            return old not in terminal
        rank = {
            "pending": 0, "kickoff_wait": 1, "live": 2, "first_half": 3,
            "halftime": 4, "second_half": 5, "extra_time": 6, "penalties": 7,
        }
        # Generic live is intentionally permissive: some providers only expose
        # IN_PLAY while others expose 1H/2H. It must never drag a known period back.
        if new == "live" and old in {"first_half", "halftime", "second_half", "extra_time", "penalties"}:
            return True
        if old == "live" and new in rank:
            return True
        return rank.get(new, -1) >= rank.get(old, -1)

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
        # Semantic keys seen during THIS refresh. DB checks alone are not enough
        # because several providers / repeated 5Dollar rows can enqueue the same
        # event before the batch is persisted at the end of the cycle.
        pending_live_event_keys: set[tuple[str, str, str, str]] = set()

        def _live_event_key(event_id, event_type, clock, detail):
            etype = str(event_type or "").strip().lower()
            # period_score is a snapshot and can be repeated at 57', 58', 59'...
            # Ignore its clock so one period/score combination appears once.
            eclock = "" if etype == "period_score" else str(clock or "").strip().lower()
            edetail = " ".join(str(detail or "").strip().lower().split())
            return (str(event_id or ""), etype, eclock, edetail)
        now = datetime.now(timezone.utc)
        try:
            kickoff_events = await self._promote_scheduled_kickoffs()
            live_events.extend(kickoff_events)
            score_updates += len(kickoff_events)
        except Exception:
            kickoff_events = []
        # Ticket settlement must NOT depend on the Live panel lookback. Reconcile
        # yesterday's/older open tickets before scanning currently visible matches.
        try:
            settlements.extend(await self._reconcile_open_tickets())
        except Exception as exc:
            errors.append(f"Réconciliation tickets: {exc}")
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
                # V13.2: reconcile scheduled rows that 5Dollar no longer returns in
                # ``status=live`` (for example when Oddium missed the transition and
                # the match is already finished).  The direct fixture endpoint is
                # throttled per match and feeds the exact same canonical update path.
                try:
                    raws.extend(await self._five_dollar_reconcile_rows(key))
                except Exception:
                    pass
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
                            cand = await self._find_existing_fixture_for_provider_event(key, event)
                            if cand is not None:
                                old = cand
                                target_event_id = str(cand["event_id"])
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
                    # V15: generic IN_PLAY must not erase a more precise known
                    # football period. After a confirmed half-time, IN_PLAY means the
                    # second half has resumed even if that provider does not expose 2H.
                    if new_phase == "live" and old_phase in {"first_half", "second_half", "extra_time", "penalties"}:
                        new_phase = old_phase
                        status = old_phase
                    elif new_phase == "live" and old_phase == "halftime":
                        new_phase = "second_half"
                        status = "second_half"
                    # Reject impossible backwards live transitions coming from an
                    # older/slower provider response. Score updates can still be
                    # accepted while the authoritative phase remains unchanged.
                    if not self._phase_transition_allowed(old_phase, new_phase):
                        new_phase = old_phase
                        status = old_phase
                    old_clock = str(old["live_clock"] or "").strip()
                    new_clock = str(event.get("live_clock") or "").strip()
                    # V15.6: clocks are merged across providers instead of being erased.
                    # 5Dollar sometimes confirms LIVE + score without returning a minute;
                    # in that case keep the ESPN/SofaScore/FotMob minute already stored.
                    stopped_phases = {"halftime", "finished", "postponed", "cancelled", "suspended", "penalties", "kickoff_wait"}
                    if new_phase in stopped_phases:
                        new_clock = ""
                    elif not new_clock and old_clock:
                        new_clock = old_clock
                    elif new_clock and old_clock and new_phase == old_phase:
                        # Do not let a slower provider move the clock backwards.
                        def _clock_minute(value):
                            try:
                                base = str(value).replace("'", "").split("+", 1)[0].strip()
                                return int(float(base))
                            except (TypeError, ValueError):
                                return None
                        old_min = _clock_minute(old_clock)
                        new_min = _clock_minute(new_clock)
                        if old_min is not None and new_min is not None and new_min < old_min:
                            new_clock = old_clock
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
                        semantic_key = _live_event_key(target_event_id, ptype, pclock, pdetail)
                        if semantic_key in pending_live_event_keys:
                            continue
                        if ptype == "period_score":
                            seen = await self.db.fetchone(
                                "SELECT 1 FROM live_events WHERE event_id=? AND event_type=? AND LOWER(TRIM(COALESCE(detail,'')))=LOWER(TRIM(?)) LIMIT 1",
                                (target_event_id, ptype, pdetail),
                            )
                        else:
                            seen = await self.db.fetchone(
                                "SELECT 1 FROM live_events WHERE event_id=? AND event_type=? AND COALESCE(clock,'')=? AND COALESCE(detail,'')=? LIMIT 1",
                                (target_event_id, ptype, pclock, pdetail),
                            )
                        if seen:
                            continue
                        pending_live_event_keys.add(semantic_key)
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

        # A kickoff placeholder is temporary only. If every provider missed the
        # match, remove the false orange LIVE state after a normal match duration.
        try:
            await self._expire_unconfirmed_kickoffs()
        except Exception:
            pass

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
        # UI-side semantic cleanup also hides duplicates already stored by older
        # Oddium versions, so users do not need to wipe SQLite after updating.
        cleaned = []
        seen_event_keys = set()
        for ev in reversed(recent):
            etype = str(ev["event_type"] or "").strip().lower()
            eclock = "" if etype == "period_score" else str(ev["clock"] or "").strip().lower()
            edetail = " ".join(str(ev["detail"] or "").strip().lower().split())
            key = (etype, eclock, edetail)
            if key in seen_event_keys:
                continue
            seen_event_keys.add(key)
            cleaned.append(ev)
        return {"match": match, "events": cleaned, "external": external}

    @staticmethod
    def _team_display_key(value: str | None) -> str:
        """Return a provider-agnostic club key.

        Live providers rarely agree on the exact display name: ``Inter Milan`` /
        ``FC Internazionale Milano``, ``Como`` / ``Como 1907`` or ``Real Betis`` /
        ``Real Betis Balompié``.  Oddium must treat those as one real club so one
        Discord line is edited in-place instead of adding another line.
        """
        import re
        import unicodedata

        text = unicodedata.normalize("NFKD", str(value or ""))
        text = "".join(c for c in text if not unicodedata.combining(c)).lower()
        words = re.findall(r"[a-z0-9]+", text)
        noise = {
            "fc", "cf", "ac", "afc", "ssc", "sc", "as", "us",
            "calcio", "club", "football", "futbol", "balompie",
            "1907", "1913",
        }
        words = [
            w for w in words
            if w not in noise and not re.fullmatch(r"(?:18|19|20)\d{2}", w)
        ]
        replacements = {"milano": "milan", "internazionale": "inter"}
        words = [replacements.get(w, w) for w in words]

        key = "".join(words)
        aliases = {
            "intermilan": "inter",
            "internazionalemilan": "inter",
            "internazionale": "inter",
            "internazionalemilano": "inter",
            "parmacalcio": "parma",
            "realbetisbalompie": "realbetis",
        }
        return aliases.get(key, key)

    @classmethod
    def _teams_equivalent(cls, left: str | None, right: str | None) -> bool:
        """Fuzzy-but-conservative equality for club names from different APIs."""
        from difflib import SequenceMatcher

        a = cls._team_display_key(left)
        b = cls._team_display_key(right)
        if not a or not b:
            return False
        if a == b:
            return True
        # Provider suffix/prefix variants: NewcastleUnited vs NewcastleUnitedFC,
        # Betis vs BetisBalompie, etc. Avoid tiny ambiguous strings.
        if min(len(a), len(b)) >= 5 and (a in b or b in a):
            return True
        return SequenceMatcher(None, a, b).ratio() >= 0.86

    @staticmethod
    def _row_kickoff(row):
        try:
            return parse_iso(str(row["commence_time"])).astimezone(timezone.utc)
        except Exception:
            return None

    @classmethod
    def _same_fixture_rows(cls, left, right, tolerance_hours: float = 8.0) -> bool:
        if str(left["sport_key"]) != str(right["sport_key"]):
            return False
        if not (cls._teams_equivalent(left["home_team"], right["home_team"]) and
                cls._teams_equivalent(left["away_team"], right["away_team"])):
            return False
        a = cls._row_kickoff(left)
        b = cls._row_kickoff(right)
        if a is None or b is None:
            return True
        return abs((a - b).total_seconds()) <= tolerance_hours * 3600

    @staticmethod
    def _live_row_rank(row):
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
        phase = str(row["live_phase"] or row["match_status"] or "pending").lower()
        source = str(row["live_source"] or "").lower()
        score_known = int(row["home_score"] is not None and row["away_score"] is not None)
        clock = str(row["live_clock"] or "")
        try:
            minute = int(''.join(ch for ch in clock.split('+', 1)[0] if ch.isdigit()) or 0)
        except Exception:
            minute = 0
        # 5Dollar wins ties; a genuinely advanced live state/clock wins over a
        # stale kickoff placeholder from any provider.
        return (phase_rank.get(phase, 0), score_known, minute, source_rank.get(source, 20))

    def _dedupe_match_rows(self, rows, limit: int = 25):
        """Return one row per real fixture, even when provider ids/names differ.

        This is deliberately pairwise instead of using an exact dictionary key.
        Provider kickoff times can differ by timezone and club names can differ by
        legal suffixes, so exact keys were the reason V13 could still show duplicates.
        """
        ordered = sorted(list(rows or []), key=self._live_row_rank, reverse=True)
        unique = []
        for row in ordered:
            if any(self._same_fixture_rows(row, kept) for kept in unique):
                continue
            unique.append(row)
        unique.sort(key=lambda r: (int(r["completed"] or 0), str(r["commence_time"])))
        return unique[:limit]

    async def _find_existing_fixture_for_provider_event(self, sport_key: str, event: dict):
        """Resolve a provider observation to the already-known Oddium fixture.

        The returned row is the canonical row that will be UPDATED.  This is the
        core of the 'one match = one line that updates itself' behaviour.
        """
        if not event.get("home_team") or not event.get("away_team") or not event.get("commence_time"):
            return None
        try:
            kickoff = parse_iso(str(event["commence_time"]))
            lo = (kickoff - timedelta(hours=8)).isoformat()
            hi = (kickoff + timedelta(hours=8)).isoformat()
        except Exception:
            return None
        candidates = await self.db.fetchall(
            """SELECT * FROM matches
               WHERE sport_key=? AND commence_time BETWEEN ? AND ?
               ORDER BY CASE WHEN five_dollar_fixture_id IS NOT NULL THEN 0 ELSE 1 END, first_seen_at ASC""",
            (sport_key, lo, hi),
        )
        for cand in candidates:
            if (self._teams_equivalent(event.get("home_team"), cand["home_team"]) and
                    self._teams_equivalent(event.get("away_team"), cand["away_team"])):
                return cand
        return None

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
        """Persist one semantic live event once, even across providers/restarts."""
        if not event.get("event_id") or event.get("type") in {None, "hello", "clock_update"}:
            return
        event_id = str(event["event_id"])
        event_type = str(event.get("type") or "live_update")
        clock = str(event.get("clock") or "")
        detail = str(event.get("detail") or "")
        fp = event_fingerprint({"type": event_type, "clock": clock, "detail": detail})
        exists = await self.db.fetchone("SELECT 1 FROM live_events WHERE event_id=? AND fingerprint=? LIMIT 1", (event_id, fp))
        if exists:
            return
        await self.db.execute(
            """INSERT OR IGNORE INTO live_events(event_id,event_type,phase,clock,home_score,away_score,detail,source,fingerprint,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (event_id, event_type, event.get("phase"), clock or None, event.get("home_score"), event.get("away_score"),
             detail or None, event.get("source"), fp, utcnow_iso()),
        )
        await self.db.execute(
            "DELETE FROM live_events WHERE event_id=? AND id NOT IN (SELECT id FROM live_events WHERE event_id=? ORDER BY id DESC LIMIT 150)",
            (event_id, event_id),
        )

    async def bind_provider_fixture(self, provider: str, provider_fixture_id: object, event_id: str) -> None:
        if provider_fixture_id in (None, ""):
            return
        await self.db.execute(
            """INSERT INTO provider_fixture_aliases(provider,provider_fixture_id,event_id,updated_at) VALUES(?,?,?,?)
               ON CONFLICT(provider,provider_fixture_id) DO UPDATE SET event_id=excluded.event_id,updated_at=excluded.updated_at""",
            (str(provider).lower(), str(provider_fixture_id), str(event_id), utcnow_iso()),
        )

    async def resolve_provider_fixture(self, provider: str, provider_fixture_id: object):
        if provider_fixture_id in (None, ""):
            return None
        row = await self.db.fetchone("SELECT event_id FROM provider_fixture_aliases WHERE provider=? AND provider_fixture_id=?", (str(provider).lower(), str(provider_fixture_id)))
        if not row:
            return None
        return await self.db.fetchone("SELECT * FROM matches WHERE event_id=?", (row["event_id"],))

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

    async def odds_market_snapshot(self, event_id: str) -> dict:
        """Return opening/current market information for the Match Center."""
        rows = await self.db.fetchall(
            "SELECT home_odd,draw_odd,away_odd,bookmaker,captured_at FROM odds_history WHERE event_id=? ORDER BY id ASC",
            (event_id,),
        )
        match = await self.db.fetchone(
            "SELECT home_odd,draw_odd,away_odd,bookmaker,last_odds_update FROM matches WHERE event_id=?",
            (event_id,),
        )
        if not match:
            return {}
        current = {
            "home": match["home_odd"], "draw": match["draw_odd"], "away": match["away_odd"],
            "bookmaker": match["bookmaker"], "captured_at": match["last_odds_update"],
        }
        if rows:
            opening_row = rows[0]
            opening = {"home": opening_row["home_odd"], "draw": opening_row["draw_odd"], "away": opening_row["away_odd"]}
        else:
            opening = {k: current[k] for k in ("home", "draw", "away")}

        def movement(key: str) -> float:
            a = opening.get(key); b = current.get(key)
            if a in (None, 0) or b is None:
                return 0.0
            return round((float(b) - float(a)) / float(a) * 100.0, 1)

        vals = [current.get("home"), current.get("draw"), current.get("away")]
        fair = [0.0, 0.0, 0.0]
        try:
            inv = [1.0 / float(v) for v in vals]
            total = sum(inv) or 1.0
            fair = [round(x / total * 100.0, 1) for x in inv]
        except Exception:
            pass
        return {
            "opening": opening, "current": current,
            "movement": {k: movement(k) for k in ("home", "draw", "away")},
            "fair_probability": {"home": fair[0], "draw": fair[1], "away": fair[2]},
            "samples": len(rows),
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
