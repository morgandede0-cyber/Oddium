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


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "oui", "on"}


@dataclass(frozen=True)
class Settings:
    discord_token: str = os.getenv("DISCORD_TOKEN", "")
    propline_api_key: str = os.getenv("PROPLINE_API_KEY", "")
    # Backward-compatible aliases used by old admin/UI checks.
    football_data_api_key: str = os.getenv("PROPLINE_API_KEY", "")
    odds_api_key: str = os.getenv("PROPLINE_API_KEY", "")
    guild_id: int | None = int(os.getenv("GUILD_ID")) if os.getenv("GUILD_ID") else None

    db_path: str = os.getenv("DB_PATH", "data/oddium.db")
    backup_dir: str = os.getenv("BACKUP_DIR", "data/backups")
    log_dir: str = os.getenv("LOG_DIR", "logs")

    propline_bookmakers: str = os.getenv("PROPLINE_BOOKMAKERS", "pinnacle,bovada,draftkings,fanduel,betmgm,unibet")
    odds_bookmakers: str = "PropLine • cote réelle bookmaker"
    currency_name: str = os.getenv("CURRENCY_NAME", "Gold")
    starting_balance: int = _int("STARTING_BALANCE", 5000)

    min_stake: int = _int("MIN_STAKE", 10)
    max_stake: int = _int("MAX_STAKE", 5000)
    max_stake_balance_percent: int = _int("MAX_STAKE_BALANCE_PERCENT", 100)
    allow_multiple_bets_same_event: bool = _bool("ALLOW_MULTIPLE_BETS_SAME_EVENT", True)

    lock_seconds_before_kickoff: int = _int("LOCK_SECONDS_BEFORE_KICKOFF", 60)
    panel_refresh_seconds: int = _int("PANEL_REFRESH_SECONDS", 60)
    engine_tick_seconds: int = _int("ENGINE_TICK_SECONDS", 60)
    # V8.6: collector near-live. The Discord panel itself is event-driven via our
    # local WebSocket and is NOT repainted on this timer.
    live_poll_seconds: int = min(15, max(3, _int("LIVE_POLL_SECONDS", 5)))
    live_discovery_seconds: int = min(30, max(10, _int("LIVE_DISCOVERY_SECONDS", 15)))
    scores_refresh_seconds: int = live_poll_seconds  # compatibility with older code
    live_ws_host: str = os.getenv("LIVE_WS_HOST", "127.0.0.1")
    live_ws_port: int = _int("LIVE_WS_PORT", 8765)
    live_ws_path: str = os.getenv("LIVE_WS_PATH", "/live")

    events_refresh_seconds: int = _int("EVENTS_REFRESH_SECONDS", 21600)  # 6h
    odds_refresh_far_seconds: int = _int("ODDS_REFRESH_FAR_SECONDS", 3600)
    odds_refresh_near_seconds: int = _int("ODDS_REFRESH_NEAR_SECONDS", 1800)
    odds_refresh_hot_seconds: int = _int("ODDS_REFRESH_HOT_SECONDS", 900)
    odds_horizon_hours: int = _int("ODDS_HORIZON_HOURS", 168)

    user_button_cooldown_seconds: int = _int("USER_BUTTON_COOLDOWN_SECONDS", 0)
    backup_every_hours: int = _int("BACKUP_EVERY_HOURS", 6)
    backup_keep_count: int = _int("BACKUP_KEEP_COUNT", 20)

    dm_notifications_default: bool = _bool("DM_NOTIFICATIONS_DEFAULT", True)
    notify_before_minutes: int = _int("NOTIFY_BEFORE_MINUTES", 30)

    market_model_enabled: bool = False
    market_model_history_years: int = 0

    # ODDIUM V8 — PropLine + cache intelligent.
    # Free tier: 1000 requests/day. Discord never calls the API directly.
    api_min_interval_seconds: int = _int("API_MIN_INTERVAL_SECONDS", 1)
    api_max_retries: int = _int("API_MAX_RETRIES", 2)
    api_cache_dir: str = os.getenv("API_CACHE_DIR", "data/api_cache_propline")
    fixtures_cache_seconds: int = _int("FIXTURES_CACHE_SECONDS", 21600)   # 6h
    scores_cache_seconds: int = min(45, max(10, _int("SCORES_CACHE_SECONDS", 45)))   # cache < polling live
    live_panel_lookback_minutes: int = _int("LIVE_PANEL_LOOKBACK_MINUTES", 210)
    live_panel_lookahead_minutes: int = _int("LIVE_PANEL_LOOKAHEAD_MINUTES", 20)
    live_finished_display_minutes: int = _int("LIVE_FINISHED_DISPLAY_MINUTES", 5)
    standings_cache_seconds: int = _int("STANDINGS_CACHE_SECONDS", 21600)
    odds_cache_far_seconds: int = _int("ODDS_CACHE_FAR_SECONDS", 21600)   # >24h: 6h
    odds_cache_day_seconds: int = _int("ODDS_CACHE_DAY_SECONDS", 7200)    # 6-24h: 2h
    odds_cache_near_seconds: int = _int("ODDS_CACHE_NEAR_SECONDS", 1800)  # 1-6h: 30m
    odds_cache_hot_seconds: int = _int("ODDS_CACHE_HOT_SECONDS", 900)     # <1h: 15m


SETTINGS = Settings()
