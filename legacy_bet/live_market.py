from __future__ import annotations

import csv
import io
import math
import re
import time
import unicodedata
from dataclasses import dataclass
from statistics import median

import aiohttp


FIXTURES_URL = "https://www.football-data.co.uk/matches/resources/fixtures.csv"

LEAGUE_CODES = {
    "soccer_epl": {"E0"},
    "soccer_spain_la_liga": {"SP1"},
    "soccer_france_ligue_one": {"F1"},
}


def norm_team(name: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\b(fc|cf|afc|rcd|rc|cd|ud|club de|club)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = " ".join(text.split())
    aliases = {
        "man utd": "manchester united", "man united": "manchester united",
        "man city": "manchester city", "spurs": "tottenham",
        "wolves": "wolverhampton", "nott m forest": "nottingham forest",
        "psg": "paris saint germain", "paris sg": "paris saint germain",
        "ath madrid": "atletico madrid", "ath bilbao": "athletic bilbao",
        "betis": "real betis", "sociedad": "real sociedad",
        "alaves": "deportivo alaves", "la coruna": "deportivo la coruna",
        "rayo vallecano": "rayo vallecano", "vallecano": "rayo vallecano",
    }
    return aliases.get(text, text)


def _safe_float(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 1.0:
        return None
    return v


def devig(odds: tuple[float, float, float]) -> tuple[float, float, float]:
    inv = [1.0 / x for x in odds]
    s = sum(inv)
    return inv[0] / s, inv[1] / s, inv[2] / s


@dataclass
class MarketQuote:
    home_probability: float
    draw_probability: float
    away_probability: float
    overround: float
    books: int
    source: str
    raw_odds: tuple[float, float, float] | None = None

    @property
    def quality_weight(self) -> float:
        # Current market should dominate the statistical model when several
        # independent prices are available. A single average/source remains useful
        # but is deliberately trusted less.
        if self.books >= 5:
            return 0.86
        if self.books >= 3:
            return 0.80
        if self.books == 2:
            return 0.72
        return 0.62


class LiveMarketConsensus:
    """Current 1X2 market snapshot without scraping bookmaker pages.

    Primary free source: football-data.co.uk's public fixtures CSV. The source
    publishes bookmaker odds for upcoming fixtures. We de-vig every available
    bookmaker triplet separately, then take the median probability for each
    outcome, which is robust to one bad/outlier book.
    """

    BOOK_TRIPLES = [
        ("B365H", "B365D", "B365A"),
        ("BWH", "BWD", "BWA"),
        ("IWH", "IWD", "IWA"),
        ("PSH", "PSD", "PSA"),
        ("WHH", "WHD", "WHA"),
        ("VCH", "VCD", "VCA"),
        ("LBH", "LBD", "LBA"),
        ("GBH", "GBD", "GBA"),
        ("BSH", "BSD", "BSA"),
    ]
    AVERAGE_TRIPLES = [
        ("AvgH", "AvgD", "AvgA"),
        ("AvgCH", "AvgCD", "AvgCA"),
    ]

    def __init__(self, *, ttl_seconds: int = 600):
        self.ttl_seconds = max(60, int(ttl_seconds))
        self._rows: list[dict[str, str]] = []
        self._loaded_at = 0.0
        self.last_error: str | None = None

    async def _refresh(self) -> None:
        now = time.monotonic()
        if self._rows and now - self._loaded_at < self.ttl_seconds:
            return
        timeout = aiohttp.ClientTimeout(total=15)
        headers = {"User-Agent": "Oddium/4.0 market-consensus"}
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(FIXTURES_URL) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"football-data.co.uk HTTP {resp.status}")
                    raw = await resp.read()
            text = raw.decode("utf-8-sig", errors="replace")
            rows = [dict(r) for r in csv.DictReader(io.StringIO(text))]
            if not rows:
                raise RuntimeError("fichier fixtures vide")
            self._rows = rows
            self._loaded_at = now
            self.last_error = None
        except Exception as exc:
            # Keep a previously downloaded snapshot if refresh fails.
            self.last_error = str(exc)
            if not self._rows:
                raise

    @staticmethod
    def _name_score(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        if a in b or b in a:
            return 0.92
        sa, sb = set(a.split()), set(b.split())
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / len(sa | sb)

    @classmethod
    def quote_from_row(cls, row: dict[str, str], *, source: str = "football-data.co.uk") -> MarketQuote | None:
        probs: list[tuple[float, float, float]] = []
        overrounds: list[float] = []
        raw_prices: list[tuple[float, float, float]] = []

        for cols in cls.BOOK_TRIPLES:
            vals = tuple(_safe_float(row.get(c)) for c in cols)
            if any(v is None for v in vals):
                continue
            odds = (float(vals[0]), float(vals[1]), float(vals[2]))
            probs.append(devig(odds))
            overrounds.append(sum(1.0 / x for x in odds))
            raw_prices.append(odds)

        # Some current fixture files expose only market averages. Treat an Avg
        # triplet as one consolidated source rather than pretending it is several books.
        if not probs:
            for cols in cls.AVERAGE_TRIPLES:
                vals = tuple(_safe_float(row.get(c)) for c in cols)
                if any(v is None for v in vals):
                    continue
                odds = (float(vals[0]), float(vals[1]), float(vals[2]))
                probs.append(devig(odds))
                overrounds.append(sum(1.0 / x for x in odds))
                raw_prices.append(odds)
                break

        if not probs:
            return None

        p = [median([x[i] for x in probs]) for i in range(3)]
        s = sum(p) or 1.0
        p = [x / s for x in p]
        overround = median(overrounds) if overrounds else 1.06
        overround = max(1.02, min(1.14, float(overround)))
        raw = tuple(median([x[i] for x in raw_prices]) for i in range(3)) if raw_prices else None
        return MarketQuote(p[0], p[1], p[2], overround, len(probs), source, raw)

    async def lookup_row(self, sport_key: str, home_team: str, away_team: str) -> dict[str, str] | None:
        try:
            await self._refresh()
        except Exception:
            return None
        hq, aq = norm_team(home_team), norm_team(away_team)
        allowed = LEAGUE_CODES.get(sport_key, set())
        best = None
        for row in self._rows:
            div = str(row.get("Div") or row.get("League") or "").strip()
            if allowed and div and div not in allowed:
                continue
            score = self._name_score(hq, norm_team(row.get("HomeTeam"))) + self._name_score(aq, norm_team(row.get("AwayTeam")))
            if score >= 1.65 and (best is None or score > best[0]):
                best = (score, row)
        return best[1] if best else None

    async def lookup(self, sport_key: str, home_team: str, away_team: str) -> MarketQuote | None:
        row = await self.lookup_row(sport_key, home_team, away_team)
        return self.quote_from_row(row) if row else None


def quote_from_football_data_odds(raw_odds: dict | None) -> MarketQuote | None:
    """Convert football-data.org's optional Odds Add-On payload when available."""
    if not isinstance(raw_odds, dict):
        return None
    h = _safe_float(raw_odds.get("homeWin"))
    d = _safe_float(raw_odds.get("draw"))
    a = _safe_float(raw_odds.get("awayWin"))
    if h is None or d is None or a is None:
        return None
    odds = (h, d, a)
    p = devig(odds)
    return MarketQuote(p[0], p[1], p[2], max(1.02, min(1.14, sum(1.0 / x for x in odds))), 1,
                       "football-data.org Odds", odds)
