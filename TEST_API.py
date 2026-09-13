import asyncio
from legacy_bet.database import Database
from legacy_bet.odds_api import OddsAPI


async def main():
    db = Database()
    await db.init()
    api = OddsAPI(db)
    try:
        sports = [
            "soccer_france_ligue_one",
            "soccer_epl",
            "soccer_spain_la_liga",
            "soccer_germany_bundesliga",
            "soccer_italy_serie_a",
            "soccer_uefa_champs_league",
        ]
        for sport in sports:
            events = await api.fetch_events(sport)
            odds = await api.fetch_bulk_odds(sport)
            parsed = sum(1 for e in odds if api.pick_1x2(e))
            print(f"{sport}: {len(events)} events / {parsed} matchs avec 1N2")
        used, remaining = await api.quota_status()
        print(f"Requêtes Oddium aujourd'hui: {used} | reste PropLine: {remaining}")
    finally:
        await api.close()


if __name__ == "__main__":
    asyncio.run(main())
