from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .live_market import LiveMarketConsensus, MarketQuote, norm_team


@dataclass
class OddiumQuote:
    home_odd: float
    draw_odd: float
    away_odd: float
    home_probability: float
    draw_probability: float
    away_probability: float
    source: str
    confidence: float
    last_odds_update: str


class OddiumOddsEngine:
    """Free-first 1/X/2 pricing engine.

    Data is intentionally layered:
      1. football-data.co.uk public fixture market when a matching row exists.
      2. 5Dollar standings-derived statistical model (football-data.org fallback).
      3. conservative neutral fallback when standings are temporarily unavailable.

    Oddium never presents these prices as a specific bookmaker quote.  The output is
    explicitly labelled as an Oddium model/consensus price.
    """

    VERSION = "10.0"

    def __init__(self, odds_api, *, margin: float = 0.055):
        self.odds_api = odds_api
        self.margin = max(0.02, min(0.12, float(margin)))
        self.market = LiveMarketConsensus(ttl_seconds=600)
        self._standings_cache: dict[str, tuple[float, dict]] = {}

    @staticmethod
    def _poisson(lam: float, k: int) -> float:
        return math.exp(-lam) * (lam ** k) / math.factorial(k)

    @classmethod
    def _score_probs(cls, lam_h: float, lam_a: float, max_goals: int = 9) -> tuple[float, float, float]:
        h = d = a = 0.0
        for i in range(max_goals + 1):
            ph = cls._poisson(lam_h, i)
            for j in range(max_goals + 1):
                p = ph * cls._poisson(lam_a, j)
                if i > j:
                    h += p
                elif i == j:
                    d += p
                else:
                    a += p
        s = h + d + a or 1.0
        return h / s, d / s, a / s

    @staticmethod
    def _safe_div(a, b, default: float) -> float:
        try:
            b = float(b)
            if b <= 0:
                return default
            return float(a) / b
        except (TypeError, ValueError, ZeroDivisionError):
            return default

    @staticmethod
    def _team_row(table: list[dict], name: str) -> dict | None:
        target = norm_team(name)
        if not target:
            return None
        exact = None
        fuzzy: list[tuple[float, dict]] = []
        for row in table:
            team = row.get("team") or {}
            candidate = norm_team(team.get("name") or team.get("shortName"))
            if not candidate:
                continue
            if candidate == target:
                exact = row
                break
            a, b = set(candidate.split()), set(target.split())
            overlap = len(a & b) / max(1, len(a | b))
            if candidate in target or target in candidate:
                overlap = max(overlap, 0.9)
            fuzzy.append((overlap, row))
        if exact is not None:
            return exact
        fuzzy.sort(key=lambda x: x[0], reverse=True)
        return fuzzy[0][1] if fuzzy and fuzzy[0][0] >= 0.55 else None

    async def _standings(self, sport_key: str) -> dict:
        # OddsAPI already applies its own persistent cache/rate limiting.
        try:
            return await self.odds_api.fetch_standings(sport_key) or {}
        except Exception:
            return {}

    def _model_from_standings(self, payload: dict, home_team: str, away_team: str) -> tuple[tuple[float, float, float], float] | None:
        standings = payload.get("standings") or [] if isinstance(payload, dict) else []
        total_blocks = [b for b in standings if str(b.get("type") or "").upper() == "TOTAL"]
        if not total_blocks and standings:
            total_blocks = [standings[0]]
        table: list[dict] = []
        seen_team_ids: set[str] = set()
        for block in total_blocks:
            for row in block.get("table") or []:
                team = row.get("team") or {}
                ident = str(team.get("id") or norm_team(team.get("name")))
                if ident and ident not in seen_team_ids:
                    seen_team_ids.add(ident)
                    table.append(row)
        if not table:
            return None

        h = self._team_row(table, home_team)
        a = self._team_row(table, away_team)
        if not h or not a:
            return None

        rows = [r for r in table if int(r.get("playedGames") or 0) > 0]
        if not rows:
            return None

        total_played = sum(int(r.get("playedGames") or 0) for r in rows)
        total_gf = sum(float(r.get("goalsFor") or 0) for r in rows)
        # Every league goal is counted once in goalsFor. team-match denominator gives
        # goals per team per match, i.e. one side's baseline lambda.
        league_side_rate = max(0.85, min(1.85, self._safe_div(total_gf, total_played, 1.35)))

        def stats(r: dict):
            played = max(1, int(r.get("playedGames") or 0))
            gf = float(r.get("goalsFor") or 0) / played
            ga = float(r.get("goalsAgainst") or 0) / played
            ppg = float(r.get("points") or 0) / played
            gdpg = float(r.get("goalDifference") or 0) / played
            return played, gf, ga, ppg, gdpg

        hp, hgf, hga, hppg, hgd = stats(h)
        ap, agf, aga, appg, agd = stats(a)

        # Bayesian shrinkage is strongest early in the season so a 4-0 opening-day
        # result cannot create absurd prices.
        h_rel = hp / (hp + 8.0)
        a_rel = ap / (ap + 8.0)
        rel = min(h_rel, a_rel)
        hgf = league_side_rate + rel * (hgf - league_side_rate)
        hga = league_side_rate + rel * (hga - league_side_rate)
        agf = league_side_rate + rel * (agf - league_side_rate)
        aga = league_side_rate + rel * (aga - league_side_rate)

        # Multiplicative attack/defence model with a modest home advantage.
        h_attack = max(0.55, min(1.75, hgf / league_side_rate))
        h_def_weak = max(0.55, min(1.75, hga / league_side_rate))
        a_attack = max(0.55, min(1.75, agf / league_side_rate))
        a_def_weak = max(0.55, min(1.75, aga / league_side_rate))
        lam_h = league_side_rate * h_attack * a_def_weak * 1.11
        lam_a = league_side_rate * a_attack * h_def_weak * 0.91

        # Table strength nudges the goal model without allowing table position to
        # overwhelm actual scoring/conceding performance.
        strength_gap = ((hppg - appg) / 3.0) + 0.18 * (hgd - agd)
        strength_gap = max(-0.65, min(0.65, strength_gap))
        lam_h *= math.exp(0.20 * strength_gap)
        lam_a *= math.exp(-0.20 * strength_gap)
        lam_h = max(0.35, min(3.60, lam_h))
        lam_a = max(0.25, min(3.20, lam_a))

        p = self._score_probs(lam_h, lam_a)
        # Confidence is based on sample size and matchup identifiability.
        confidence = max(0.42, min(0.82, 0.46 + 0.30 * rel))
        return p, confidence

    @staticmethod
    def _blend(model: tuple[float, float, float], market: MarketQuote, model_conf: float) -> tuple[tuple[float, float, float], float]:
        # Public market consensus dominates when several books are available; the
        # model provides resilience and keeps a coherent price if only Avg odds exist.
        mw = market.quality_weight
        mw = max(0.58, min(0.90, mw))
        p = tuple(mw * mkt + (1.0 - mw) * mod for mod, mkt in zip(model, (
            market.home_probability, market.draw_probability, market.away_probability
        )))
        s = sum(p) or 1.0
        p = tuple(x / s for x in p)
        confidence = max(model_conf, min(0.96, 0.70 + 0.05 * market.books))
        return p, confidence

    def _to_odds(self, p: tuple[float, float, float]) -> tuple[float, float, float]:
        # Apply an explicit Oddium margin symmetrically in probability space.
        mult = 1.0 + self.margin
        out = []
        for prob in p:
            priced_p = max(0.025, min(0.94, prob * mult))
            out.append(round(max(1.05, min(40.0, 1.0 / priced_p)), 2))
        return tuple(out)  # type: ignore[return-value]

    async def quote(self, sport_key: str, home_team: str, away_team: str) -> OddiumQuote:
        standings_payload = await self._standings(sport_key)
        modeled = self._model_from_standings(standings_payload, home_team, away_team)
        if modeled:
            model_p, model_conf = modeled
        else:
            # League-neutral safety fallback. It is deliberately conservative and
            # only used when a provider cannot supply usable standings.
            model_p, model_conf = (0.425, 0.285, 0.290), 0.34

        market = None
        try:
            market = await self.market.lookup(sport_key, home_team, away_team)
        except Exception:
            market = None

        if market is not None:
            p, confidence = self._blend(model_p, market, model_conf)
            source = f"Oddium Fusion • marché public + modèle ({market.books} source{'s' if market.books != 1 else ''})"
        else:
            p, confidence = model_p, model_conf
            source = "Oddium Model • classement 5Dollar/fallback"

        odds = self._to_odds(p)
        return OddiumQuote(
            home_odd=odds[0], draw_odd=odds[1], away_odd=odds[2],
            home_probability=p[0], draw_probability=p[1], away_probability=p[2],
            source=source, confidence=confidence,
            last_odds_update=datetime.now(timezone.utc).isoformat(),
        )
