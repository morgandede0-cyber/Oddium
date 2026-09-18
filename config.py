from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "oui", "on"}


@dataclass(frozen=True)
class Settings:
    # Discord / application
    discord_token: str = os.getenv("DISCORD_TOKEN", "")
    guild_id: int | None = _optional_int("GUILD_ID")

    # 5DollarFootballAPI — unique football authority
    five_dollar_api_key: str = os.getenv("FIVE_DOLLAR_FOOTBALL_API_KEY", "")
    five_dollar_poll_seconds: int = min(120, max(15, _int("FIVE_DOLLAR_POLL_SECONDS", 45)))
    five_dollar_fixtures_cache_seconds: int = max(120, _int("FIVE_DOLLAR_FIXTURES_CACHE_SECONDS", 900))
    api_cache_dir: str = os.getenv("API_CACHE_DIR", "data/api_cache")

    # Persistence / maintenance
    db_path: str = os.getenv("DB_PATH", "data/oddium.db")
    backup_dir: str = os.getenv("BACKUP_DIR", "data/backups")
    log_dir: str = os.getenv("LOG_DIR", "logs")
    backup_every_hours: int = _int("BACKUP_EVERY_HOURS", 6)
    backup_keep_count: int = _int("BACKUP_KEEP_COUNT", 20)

    # Shared Altherya/Oddium PostgreSQL economy
    economy_database_url: str = os.getenv("ECONOMY_DATABASE_URL", "").strip()

    # Economy / bets
    currency_name: str = os.getenv("CURRENCY_NAME", "Gold")
    starting_balance: int = _int("STARTING_BALANCE", 5000)
    min_stake: int = _int("MIN_STAKE", 10)
    max_stake: int = _int("MAX_STAKE", 5000)
    max_stake_balance_percent: int = _int("MAX_STAKE_BALANCE_PERCENT", 100)
    allow_multiple_bets_same_event: bool = _bool("ALLOW_MULTIPLE_BETS_SAME_EVENT", True)
    lock_seconds_before_kickoff: int = _int("LOCK_SECONDS_BEFORE_KICKOFF", 60)

    # Runtime / Discord refresh
    panel_refresh_seconds: int = _int("PANEL_REFRESH_SECONDS", 60)
    engine_tick_seconds: int = _int("ENGINE_TICK_SECONDS", 60)
    live_poll_seconds: int = min(15, max(3, _int("LIVE_POLL_SECONDS", 5)))
    live_discovery_seconds: int = min(30, max(10, _int("LIVE_DISCOVERY_SECONDS", 15)))
    live_ws_host: str = os.getenv("LIVE_WS_HOST", "127.0.0.1")
    live_ws_port: int = _int("LIVE_WS_PORT", 8765)
    live_ws_path: str = os.getenv("LIVE_WS_PATH", "/live")
    live_panel_lookback_minutes: int = _int("LIVE_PANEL_LOOKBACK_MINUTES", 210)
    live_panel_lookahead_minutes: int = _int("LIVE_PANEL_LOOKAHEAD_MINUTES", 20)

    # Scheduled refreshes
    events_refresh_seconds: int = _int("EVENTS_REFRESH_SECONDS", 21600)
    odds_refresh_far_seconds: int = _int("ODDS_REFRESH_FAR_SECONDS", 3600)
    odds_refresh_near_seconds: int = _int("ODDS_REFRESH_NEAR_SECONDS", 1800)
    odds_refresh_hot_seconds: int = _int("ODDS_REFRESH_HOT_SECONDS", 900)
    odds_horizon_hours: int = _int("ODDS_HORIZON_HOURS", 168)

    # Notifications
    dm_notifications_default: bool = _bool("DM_NOTIFICATIONS_DEFAULT", True)
    notify_before_minutes: int = _int("NOTIFY_BEFORE_MINUTES", 30)


SETTINGS = Settings()
