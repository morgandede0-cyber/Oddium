from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import aiohttp

from config import SETTINGS


DATASET_CODES = {
    "soccer_epl": "E0",
    "soccer_spain_la_liga": "SP1",
    "soccer_france_ligue_one": "F1",
}


def norm_team(name: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\b(fc|cf|afc|rcd|rc|cd|ud|club de|club)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    aliases = {
        "man utd": "manchester united", "man united": "manchester united",
        "man city": "manchester city", "spurs": "tottenham",
        "wolves": "wolverhampton", "nott m forest": "nottingham forest",
        "psg": "paris saint germain", "paris sg": "paris saint germain",
        "ath madrid": "atletico madrid", "ath bilbao": "athletic bilbao",
        "betis": "real betis", "sociedad": "real sociedad",
        "alaves": "deportivo alaves", "valladolid": "real valladolid",
        "la coruna": "deportivo la coruna", "racing santander": "racing santander",
    }
    text = " ".join(text.split())
    return aliases.get(text, text)


def softmax(z: list[float]) -> list[float]:
    m = max(z)
    ex = [math.exp(v - m) for v in z]
    s = sum(ex) or 1.0
    return [v / s for v in ex]


def implied_probs(odds: tuple[float, float, float]) -> list[float] | None:
    try:
        inv = [1.0 / float(x) for x in odds]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if any(not math.isfinite(x) or x <= 0 for x in inv):
        return None
    s = sum(inv)
    return [x / s for x in inv]


def closing_odds(row: dict[str, str]) -> tuple[float, float, float] | None:
    candidates = [
        ("AvgCH", "AvgCD", "AvgCA"),
        ("B365CH", "B365CD", "B365CA"),
        ("PSCH", "PSCD", "PSCA"),
        ("AvgH", "AvgD", "AvgA"),
        ("B365H", "B365D", "B365A"),
    ]
    for cols in candidates:
        try:
            vals = tuple(float(row.get(c, "")) for c in cols)
            if all(v > 1.0 and math.isfinite(v) for v in vals):
                return vals  # type: ignore[return-value]
        except (TypeError, ValueError):
            continue
    return None


@dataclass
class TeamState:
    elo: float = 1500.0
    recent_points: deque = None  # type: ignore[assignment]
    recent_gf: deque = None  # type: ignore[assignment]
    recent_ga: deque = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.recent_points is None:
            self.recent_points = deque(maxlen=12)
        if self.recent_gf is None:
            self.recent_gf = deque(maxlen=12)
        if self.recent_ga is None:
            self.recent_ga = deque(maxlen=12)


class HistoricalMarketModel:
    """Learns a bookmaker-like 1/N/2 calibration from historical public CSVs.

    Source: football-data.co.uk historical league files. Training targets are
    *de-vigged* closing/average bookmaker probabilities, not match results.
    This lets Oddium learn the shape of real market pricing while still using
    its own live team state for current fixtures.
    """

    BASE = "https://www.football-data.co.uk/mmz4281"
    VERSION = 3

    def __init__(self):
        self.path = Path(os.getenv("MARKET_MODEL_PATH", "data/oddium_market_model.json"))
        self.weights: list[list[float]] | None = None
        self.team_elos: dict[str, float] = {}
        self.trained_rows = 0
        self.trained_at: str | None = None
        self.last_error: str | None = None
        self.ready = False
        self._lock = None

    @staticmethod
    def _season_tokens(years: int = 6) -> list[str]:
        now = datetime.now(timezone.utc)
        current_start = now.year if now.month >= 7 else now.year - 1
        starts = range(current_start - years, current_start)
        return [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in starts]

    async def ensure_ready(self, *, force: bool = False) -> bool:
        import asyncio
        if self.ready and not force:
            return True
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self.ready and not force:
                return True
            if not force and self._load_cache():
                self.ready = True
                return True
            try:
                rows = await self._download_training_rows()
                if len(rows) < 500:
                    raise RuntimeError(f"historique marché insuffisant ({len(rows)} lignes)")
                samples, states = self._make_samples(rows)
                if len(samples) < 500:
                    raise RuntimeError(f"échantillon entraînable insuffisant ({len(samples)})")
                self.weights = self._fit(samples)
                self.team_elos = {name: round(state.elo, 3) for name, state in states.items()}
                self.trained_rows = len(samples)
                self.trained_at = datetime.now(timezone.utc).isoformat()
                self.last_error = None
                self.ready = True
                self._save_cache()
                return True
            except Exception as exc:
                self.last_error = str(exc)
                # A stale cache is still preferable to reverting to hand-written club tiers.
                if self._load_cache():
                    self.ready = True
                    return True
                self.ready = False
                return False

    def _load_cache(self) -> bool:
        try:
            if not self.path.exists():
                return False
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if int(raw.get("version", 0)) != self.VERSION:
                return False
            w = raw.get("weights")
            if not isinstance(w, list) or len(w) != 3:
                return False
            self.weights = [[float(v) for v in row] for row in w]
            self.team_elos = {str(k): float(v) for k, v in (raw.get("team_elos") or {}).items()}
            self.trained_rows = int(raw.get("trained_rows") or 0)
            self.trained_at = raw.get("trained_at")
            return True
        except Exception:
            return False

    def _save_cache(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "trained_at": self.trained_at,
            "trained_rows": self.trained_rows,
            "weights": self.weights,
            "team_elos": self.team_elos,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    async def _download_training_rows(self) -> list[dict[str, str]]:
        timeout = aiohttp.ClientTimeout(total=25)
        headers = {"User-Agent": "Oddium/3.0 historical-market-calibration"}
        sem = __import__("asyncio").Semaphore(4)

        async def fetch_one(session: aiohttp.ClientSession, sport_key: str, season: str, code: str):
            url = f"{self.BASE}/{season}/{code}.csv"
            async with sem:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        return []
                    raw = await resp.read()
            text = raw.decode("utf-8-sig", errors="replace")
            out = []
            for row in csv.DictReader(io.StringIO(text)):
                if row.get("HomeTeam") and row.get("AwayTeam"):
                    row["_sport_key"] = sport_key
                    row["_season"] = season
                    out.append(row)
            return out

        import asyncio
        jobs = []
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for season in self._season_tokens(max(2, SETTINGS.market_model_history_years)):
                for sport_key, code in DATASET_CODES.items():
                    jobs.append(fetch_one(session, sport_key, season, code))
            groups = await asyncio.gather(*jobs, return_exceptions=True)
        rows: list[dict[str, str]] = []
        for group in groups:
            if isinstance(group, list):
                rows.extend(group)
        # Files are gathered by season then competition; order each competition chronologically
        # using original CSV order as the stable tiebreaker.
        return rows

    @staticmethod
    def _features(home: TeamState, away: TeamState, league_goal_rate: float = 1.35) -> list[float]:
        def avg(q: deque, default: float) -> float:
            return sum(q) / len(q) if q else default
        hp = avg(home.recent_points, 1.35) / 3.0
        ap = avg(away.recent_points, 1.35) / 3.0
        hgf = avg(home.recent_gf, league_goal_rate) / max(0.8, league_goal_rate)
        agf = avg(away.recent_gf, league_goal_rate) / max(0.8, league_goal_rate)
        hga = avg(home.recent_ga, league_goal_rate) / max(0.8, league_goal_rate)
        aga = avg(away.recent_ga, league_goal_rate) / max(0.8, league_goal_rate)
        gap = (home.elo + 72.0 - away.elo) / 400.0
        return [
            1.0,
            max(-3.0, min(3.0, gap)),
            max(-1.0, min(1.0, hp - ap)),
            max(-1.5, min(1.5, hgf - agf)),
            max(-1.5, min(1.5, hga - aga)),
            max(0.0, min(3.0, abs(gap))),
            max(-3.0, min(3.0, gap)) * max(0.0, min(3.0, abs(gap))),
        ]

    @staticmethod
    def _elo_expected(home_elo: float, away_elo: float) -> float:
        return 1.0 / (1.0 + 10.0 ** ((away_elo - (home_elo + 72.0)) / 400.0))

    @classmethod
    def _update_state(cls, h: TeamState, a: TeamState, hg: int, ag: int) -> None:
        exp = cls._elo_expected(h.elo, a.elo)
        actual = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
        gd = abs(hg - ag)
        mult = 1.0 if gd <= 1 else min(2.2, 1.0 + math.log(gd) * 0.55)
        delta = 22.0 * mult * (actual - exp)
        h.elo += delta
        a.elo -= delta
        hp = 3.0 if hg > ag else 1.0 if hg == ag else 0.0
        ap = 3.0 if ag > hg else 1.0 if hg == ag else 0.0
        h.recent_points.append(hp); a.recent_points.append(ap)
        h.recent_gf.append(float(hg)); h.recent_ga.append(float(ag))
        a.recent_gf.append(float(ag)); a.recent_ga.append(float(hg))

    def _make_samples(self, rows: list[dict[str, str]]):
        states: dict[str, TeamState] = defaultdict(TeamState)
        samples: list[tuple[list[float], list[float]]] = []

        # Group to preserve chronological progression within each league while keeping
        # team ratings continuous across seasons.
        by_league: dict[str, list[dict[str, str]]] = defaultdict(list)
        for r in rows:
            by_league[r.get("_sport_key", "")].append(r)

        for league_rows in by_league.values():
            for row in league_rows:
                hn, an = norm_team(row.get("HomeTeam")), norm_team(row.get("AwayTeam"))
                if not hn or not an:
                    continue
                try:
                    hg, ag = int(float(row.get("FTHG", ""))), int(float(row.get("FTAG", "")))
                except (TypeError, ValueError):
                    continue
                odds = closing_odds(row)
                target = implied_probs(odds) if odds else None
                h, a = states[hn], states[an]
                if target:
                    samples.append((self._features(h, a), target))
                self._update_state(h, a, hg, ag)
        return samples, states

    @staticmethod
    def _fit(samples: list[tuple[list[float], list[float]]]) -> list[list[float]]:
        # Multinomial softmax regression trained directly against de-vigged market
        # probabilities. Pure Python keeps the bot install light (no sklearn/xgboost).
        nfeat = len(samples[0][0])
        w = [[0.0] * nfeat for _ in range(3)]
        lr = 0.075
        reg = 0.0015
        # deterministic striding gives enough passes without expensive dependencies
        for epoch in range(90):
            grad = [[0.0] * nfeat for _ in range(3)]
            loss_n = 0
            for x, y in samples:
                p = softmax([sum(w[k][j] * x[j] for j in range(nfeat)) for k in range(3)])
                for k in range(3):
                    d = p[k] - y[k]
                    for j in range(nfeat):
                        grad[k][j] += d * x[j]
                loss_n += 1
            scale = 1.0 / max(1, loss_n)
            step = lr * (0.985 ** epoch)
            for k in range(3):
                for j in range(nfeat):
                    g = grad[k][j] * scale + reg * w[k][j]
                    w[k][j] -= step * g
        return w

    def seed_elo(self, team_name: str | None) -> float | None:
        key = norm_team(team_name)
        if not key:
            return None
        if key in self.team_elos:
            return self.team_elos[key]
        # cautious fuzzy containment for provider naming differences
        best = None
        for k, v in self.team_elos.items():
            if len(k) >= 5 and (k in key or key in k):
                if best is None or len(k) > len(best[0]):
                    best = (k, v)
        return None if best is None else float(best[1])

    def predict(self, *, elo_gap: float, home_form: float, away_form: float,
                home_gf: float, away_gf: float, home_ga: float, away_ga: float,
                league_rate: float) -> list[float] | None:
        if not self.weights:
            return None
        # Recreate the training feature vector from the live state.
        h = TeamState(elo=1500.0 + elo_gap / 2.0)
        a = TeamState(elo=1500.0 - elo_gap / 2.0)
        # Form residual is already opponent-adjusted; convert it to a modest points proxy.
        h.recent_points.append(1.35 + max(-0.9, min(0.9, home_form * 2.0)))
        a.recent_points.append(1.35 + max(-0.9, min(0.9, away_form * 2.0)))
        h.recent_gf.append(home_gf); a.recent_gf.append(away_gf)
        h.recent_ga.append(home_ga); a.recent_ga.append(away_ga)
        x = self._features(h, a, league_rate)
        z = [sum(self.weights[k][j] * x[j] for j in range(len(x))) for k in range(3)]
        return softmax(z)
