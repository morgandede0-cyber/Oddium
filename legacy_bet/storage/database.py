from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from config import SETTINGS
from ..core.constants import DEFAULT_ACTIVE_COMPETITIONS


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str = SETTINGS.db_path):
        self.path = path

    async def init(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                PRAGMA busy_timeout=5000;

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS wallets (
                    user_id INTEGER PRIMARY KEY,
                    balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0)
                );

                CREATE TABLE IF NOT EXISTS wallet_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    reference TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS uq_wallet_idempotency
                ON wallet_transactions(user_id, reason, reference)
                WHERE reference IS NOT NULL;

                CREATE TABLE IF NOT EXISTS matches (
                    event_id TEXT PRIMARY KEY,
                    sport_key TEXT NOT NULL,
                    competition_name TEXT NOT NULL,
                    home_team TEXT NOT NULL,
                    away_team TEXT NOT NULL,
                    home_team_id INTEGER,
                    away_team_id INTEGER,
                    commence_time TEXT NOT NULL,
                    home_odd REAL,
                    draw_odd REAL,
                    away_odd REAL,
                    bookmaker TEXT,
                    last_odds_update TEXT,
                    odds_available INTEGER NOT NULL DEFAULT 1,
                    completed INTEGER NOT NULL DEFAULT 0,
                    cancelled INTEGER NOT NULL DEFAULT 0,
                    home_score INTEGER,
                    away_score INTEGER,
                    match_status TEXT NOT NULL DEFAULT 'pending',
                    live_phase TEXT NOT NULL DEFAULT 'pending',
                    live_clock TEXT,
                    live_detail TEXT,
                    live_source TEXT,
                    last_score_update TEXT,
                    first_seen_at TEXT,
                    last_seen_at TEXT
                );

                CREATE TABLE IF NOT EXISTS odds_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    home_odd REAL,
                    draw_odd REAL,
                    away_odd REAL,
                    bookmaker TEXT,
                    captured_at TEXT NOT NULL,
                    FOREIGN KEY(event_id) REFERENCES matches(event_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS bets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    selection TEXT NOT NULL CHECK(selection IN ('HOME','DRAW','AWAY')),
                    odd REAL NOT NULL,
                    stake INTEGER NOT NULL CHECK(stake > 0),
                    potential_payout INTEGER NOT NULL CHECK(potential_payout >= 0),
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    created_at TEXT NOT NULL,
                    settled_at TEXT,
                    payout INTEGER NOT NULL DEFAULT 0,
                    settlement_note TEXT,
                    FOREIGN KEY(event_id) REFERENCES matches(event_id)
                );

                CREATE TABLE IF NOT EXISTS combo_bets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    total_odd REAL NOT NULL,
                    stake INTEGER NOT NULL CHECK(stake > 0),
                    potential_payout INTEGER NOT NULL CHECK(potential_payout >= 0),
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    created_at TEXT NOT NULL,
                    settled_at TEXT,
                    payout INTEGER NOT NULL DEFAULT 0,
                    settlement_note TEXT
                );

                CREATE TABLE IF NOT EXISTS combo_legs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    combo_id INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    selection TEXT NOT NULL CHECK(selection IN ('HOME','DRAW','AWAY')),
                    odd REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    result TEXT,
                    FOREIGN KEY(combo_id) REFERENCES combo_bets(id) ON DELETE CASCADE,
                    FOREIGN KEY(event_id) REFERENCES matches(event_id),
                    UNIQUE(combo_id,event_id)
                );

                CREATE TABLE IF NOT EXISTS favorites (
                    user_id INTEGER NOT NULL,
                    team_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, team_name)
                );

                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id INTEGER PRIMARY KEY,
                    dm_notifications INTEGER NOT NULL DEFAULT 1,
                    notify_result INTEGER NOT NULL DEFAULT 1,
                    notify_before_match INTEGER NOT NULL DEFAULT 1,
                    notify_odds_change INTEGER NOT NULL DEFAULT 0
                );

                -- V69: preferences are opt-in and live alerts have their own switch.
                -- ALTER is executed below for databases created by older Oddium versions.

                CREATE TABLE IF NOT EXISTS live_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    phase TEXT,
                    clock TEXT,
                    home_score INTEGER,
                    away_score INTEGER,
                    detail TEXT,
                    source TEXT,
                    fingerprint TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(event_id) REFERENCES matches(event_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS match_follows (
                    user_id INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(user_id,event_id),
                    FOREIGN KEY(event_id) REFERENCES matches(event_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS notification_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    reference TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    UNIQUE(user_id, kind, reference)
                );

                CREATE TABLE IF NOT EXISTS admin_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER,
                    action TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS api_usage (
                    usage_date TEXT PRIMARY KEY,
                    requests_used INTEGER NOT NULL DEFAULT 0,
                    last_request_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_bets_user ON bets(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_bets_event ON bets(event_id, status);
                CREATE INDEX IF NOT EXISTS idx_combo_bets_user ON combo_bets(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_combo_legs_event ON combo_legs(event_id, status);
                CREATE INDEX IF NOT EXISTS idx_matches_time ON matches(commence_time);
                CREATE INDEX IF NOT EXISTS idx_matches_sport_time ON matches(sport_key, commence_time);
                CREATE INDEX IF NOT EXISTS idx_odds_history_event ON odds_history(event_id, captured_at DESC);
                CREATE INDEX IF NOT EXISTS idx_live_events_event ON live_events(event_id, id DESC);
                CREATE TABLE IF NOT EXISTS provider_fixture_aliases (
                    provider TEXT NOT NULL, provider_fixture_id TEXT NOT NULL, event_id TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(provider, provider_fixture_id),
                    FOREIGN KEY(event_id) REFERENCES matches(event_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_match_follows_event ON match_follows(event_id);
                """
            )

            # Lightweight compatibility migrations for databases created by older Oddium releases.
            await self._ensure_column(db, "matches", "odds_available", "INTEGER NOT NULL DEFAULT 1")
            await self._ensure_column(db, "matches", "cancelled", "INTEGER NOT NULL DEFAULT 0")
            await self._ensure_column(db, "matches", "first_seen_at", "TEXT")
            await self._ensure_column(db, "matches", "last_seen_at", "TEXT")
            await self._ensure_column(db, "matches", "home_team_id", "INTEGER")
            await self._ensure_column(db, "matches", "away_team_id", "INTEGER")
            await self._ensure_column(db, "matches", "match_status", "TEXT NOT NULL DEFAULT 'pending'")
            await self._ensure_column(db, "matches", "live_phase", "TEXT NOT NULL DEFAULT 'pending'")
            await self._ensure_column(db, "matches", "live_clock", "TEXT")
            await self._ensure_column(db, "matches", "live_detail", "TEXT")
            await self._ensure_column(db, "live_events", "fingerprint", "TEXT")
            await self._ensure_column(db, "matches", "live_source", "TEXT")
            await self._ensure_column(db, "matches", "five_dollar_fixture_id", "INTEGER")
            await self._ensure_column(db, "bets", "settlement_note", "TEXT")
            await self._ensure_column(db, "user_preferences", "notify_live", "INTEGER NOT NULL DEFAULT 0")

            # IMPORTANT: indexes that reference columns introduced by migrations
            # must be created only *after* those columns exist. Older Oddium
            # databases do not have live_events.fingerprint yet; creating this
            # index inside the initial executescript made startup fail with
            # "sqlite3.OperationalError: no such column: fingerprint" before
            # _ensure_column() had a chance to migrate the database.
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_live_event_fingerprint "
                "ON live_events(event_id, fingerprint) WHERE fingerprint IS NOT NULL"
            )
            await db.commit()

        defaults = {
            "active_competitions": DEFAULT_ACTIVE_COMPETITIONS,
            "panel_channel_id": None,
            "panel_message_id": None,
            "live_panel_channel_id": None,
            "live_panel_message_id": None,
            "betting_paused": False,
            "featured_event_id": None,
            "log_channel_id": None,
            "last_odds_refresh": None,
            "last_scores_refresh": None,
        }
        for key, value in defaults.items():
            if await self.get_setting(key) is None:
                await self.set_setting(key, value)

        # Compatibility migration: if the user was still on Oddium's old default
        # (Ligue 1 + EPL + Liga), enable Bundesliga + Serie A + Champions League too.
        old_default = ["soccer_france_ligue_one", "soccer_epl", "soccer_spain_la_liga"]
        current = await self.get_setting("active_competitions")
        if current == old_default:
            await self.set_setting("active_competitions", DEFAULT_ACTIVE_COMPETITIONS)
        else:
            # V47: existing installations on Oddium's previous six-league default
            # gain Europa League automatically, while custom admin selections remain untouched.
            previous_default = [
                "soccer_france_ligue_one", "soccer_epl", "soccer_spain_la_liga",
                "soccer_germany_bundesliga", "soccer_italy_serie_a",
                "soccer_uefa_champs_league",
            ]
            if current == previous_default:
                await self.set_setting("active_competitions", DEFAULT_ACTIVE_COMPETITIONS)
            else:
                # V67: installations using the former seven-competition default
                # automatically gain UEFA Nations League. Custom selections stay untouched.
                previous_seven = [
                    "soccer_france_ligue_one", "soccer_epl", "soccer_spain_la_liga",
                    "soccer_germany_bundesliga", "soccer_italy_serie_a",
                    "soccer_uefa_champs_league", "soccer_uefa_europa_league",
                ]
                if current == previous_seven:
                    await self.set_setting("active_competitions", DEFAULT_ACTIVE_COMPETITIONS)

    async def _ensure_column(self, db: aiosqlite.Connection, table: str, column: str, definition: str) -> None:
        cur = await db.execute(f"PRAGMA table_info({table})")
        cols = {row[1] for row in await cur.fetchall()}
        if column not in cols:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    async def connect(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        return db

    async def fetchone(self, query: str, params: tuple = ()):
        db = await self.connect()
        try:
            cur = await db.execute(query, params)
            return await cur.fetchone()
        finally:
            await db.close()

    async def fetchall(self, query: str, params: tuple = ()):
        db = await self.connect()
        try:
            cur = await db.execute(query, params)
            return await cur.fetchall()
        finally:
            await db.close()

    async def execute(self, query: str, params: tuple = ()) -> int:
        db = await self.connect()
        try:
            cur = await db.execute(query, params)
            await db.commit()
            return int(cur.lastrowid or 0)
        finally:
            await db.close()

    async def get_setting(self, key: str) -> Any:
        row = await self.fetchone("SELECT value FROM settings WHERE key=?", (key,))
        return None if row is None else json.loads(row["value"])

    async def set_setting(self, key: str, value: Any) -> None:
        await self.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    async def log_admin(self, admin_id: int | None, action: str, details: str = "") -> None:
        await self.execute(
            "INSERT INTO admin_logs(admin_id,action,details,created_at) VALUES(?,?,?,?)",
            (admin_id, action, details[:1500], utcnow_iso()),
        )

    async def backup(self) -> str:
        Path(SETTINGS.backup_dir).mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = Path(SETTINGS.backup_dir) / f"legacy_bet_{stamp}.db"
        # SQLite online backup API gives a consistent snapshot while the bot is running.
        src = await aiosqlite.connect(self.path)
        dst = await aiosqlite.connect(str(target))
        try:
            await src.backup(dst)
        finally:
            await dst.close()
            await src.close()
        await self._prune_backups()
        return str(target)

    async def _prune_backups(self) -> None:
        folder = Path(SETTINGS.backup_dir)
        files = sorted(folder.glob("legacy_bet_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[SETTINGS.backup_keep_count:]:
            try:
                old.unlink()
            except OSError:
                pass
