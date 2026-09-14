import asyncio
from config import SETTINGS
from legacy_bet.database import Database
from legacy_bet.odds_api import OddsAPI

SPORTS = [
    "soccer_france_ligue_one",
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_uefa_champs_league",
]


async def main():
    if not SETTINGS.five_dollar_api_key:
        raise SystemExit("FIVE_DOLLAR_FOOTBALL_API_KEY absente")
    db = Database()
    await db.init()
    api = OddsAPI(db)
    try:
        state = await api.five_dollar.status()
        print("5Dollar:", "OK" if state.get("ok") else "ERREUR", "quota", state.get("remaining"), "/", state.get("limit"))
        print("ID scheme:", api.five_dollar.id_scheme or "inconnu")
        for sport in SPORTS:
            fixtures = await api.fetch_five_dollar_fixtures(sport)
            with_odds = sum(1 for f in fixtures if f.get("provider_odds"))
            print(f"{sport}: {len(fixtures)} fixtures / {with_odds} avec 1N2 Bet365")
        # Le flux live global est mis en cache : les six filtres ne déclenchent pas six appels.
        counts = []
        for sport in SPORTS:
            counts.append((sport, len(await api.fetch_five_dollar_live(sport))))
        print("Live:", counts)
    finally:
        await api.close()


if __name__ == "__main__":
    asyncio.run(main())
