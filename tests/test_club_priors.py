from legacy_bet.market_model import HistoricalMarketModel, TeamState


def test_v3_has_no_hardcoded_club_strength_dependency():
    model = HistoricalMarketModel()
    # Before training/cache, an unknown club has no fabricated rating.
    assert model.seed_elo("Real Madrid CF") is None
    assert model.seed_elo("Rayo Vallecano") is None


def test_elo_seed_is_learned_from_model_cache_state():
    model = HistoricalMarketModel()
    model.team_elos = {"real madrid": 1942.0, "rayo vallecano": 1538.0}
    assert model.seed_elo("Real Madrid CF") > model.seed_elo("Rayo Vallecano de Madrid")
