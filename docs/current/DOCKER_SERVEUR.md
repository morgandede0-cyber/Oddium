# Oddium V12 — Installation Docker serveur

Oddium conserve SQLite, les sauvegardes, le cache football et les logs hors du conteneur. Une reconstruction Docker ne supprime donc pas les paris.

## 1. Configuration

Copie l'exemple puis renseigne au minimum :

```bash
cp .env.example .env
nano .env
```

```env
DISCORD_TOKEN=TON_TOKEN_DISCORD
FIVE_DOLLAR_FOOTBALL_API_KEY=TA_CLE_PRO_5DOLLAR
GUILD_ID=ID_DE_TON_SERVEUR_DISCORD
```

Variables utiles :

```env
FIVE_DOLLAR_POLL_SECONDS=45
FIVE_DOLLAR_FIXTURES_CACHE_SECONDS=900
# Secours facultatif :
FOOTBALL_DATA_API_KEY=
```

Ne publie jamais `.env` ni la clé 5Dollar.

## 2. Démarrage

```bash
docker compose up -d --build
docker compose logs -f oddium
```

Dans Discord, `/diagnostic_oddium` doit afficher **5DollarFootballAPI Pro : activée** après le premier appel réussi.

## 3. Données persistantes

- `./data/oddium.db` : base SQLite principale
- `./data/backups/` : sauvegardes automatiques
- `./data/api_cache/` : cache des sources football
- `./logs/` : logs Oddium

## 4. Mise à jour

Conserve `data/`, `logs/` et `.env`, remplace le code puis :

```bash
docker compose up -d --build
```

Le WebSocket Oddium reste local au conteneur (`127.0.0.1:8765`) : aucun port public n'est nécessaire.
