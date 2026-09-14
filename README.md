# Oddium V12 — 5Dollar Engine

Bot Discord de paris football virtuels avec cotes Bet365, live-score, événements, statistiques, favoris, combinés, classements et économie Gold.

## Architecture

**5DollarFootballAPI Pro** est la source principale : fixtures, live, timeline, statistiques et cotes Bet365. Oddium mutualise le flux live global et le met en cache pour respecter le quota Pro. ESPN, Sofascore, FotMob, TheSportsDB et livescoreFootball restent des secours live. football-data.org et le moteur Oddium Fusion restent des secours calendrier/cotation.

## Configuration minimale

```env
DISCORD_TOKEN=...
FIVE_DOLLAR_FOOTBALL_API_KEY=...
```

Recommandé :

```env
FIVE_DOLLAR_POLL_SECONDS=45
FOOTBALL_DATA_API_KEY=...
GUILD_ID=...
```

La clé 5Dollar doit être ajoutée dans Coolify ou `.env`, jamais dans Git.

## Démarrage

Docker/Coolify : utiliser `Dockerfile` ou `docker-compose.coolify.yml`.

Commandes principales : `/setup`, `/setup_live`, `/diagnostic_oddium`, `/admin_paris`.

Voir `V12_5DOLLAR_ENGINE.md` pour le détail de la V12.
