# Oddium V10 — Free-First Fusion

## Changements majeurs

### 1. Cotes sans API payante obligatoire
Le nouveau `legacy_bet/oddium_odds.py` calcule les cotes 1/N/2 :
- modèle Poisson à partir des statistiques du classement football-data.org ;
- shrinkage en début de saison pour éviter les cotes extrêmes ;
- avantage domicile modéré ;
- consensus public football-data.co.uk lorsqu'il existe ;
- marge Oddium configurable avec `ODDIUM_ODDS_MARGIN_PERCENT`.

Les cotes sont enregistrées dans `odds_history` comme avant, et la cote validée par le joueur reste figée sur son ticket.

### 2. Un seul match canonique
Les fixtures créées par football-data.org restent la référence. Le moteur V10 ne crée plus une deuxième ligne PropLine pour la même rencontre lors du calcul des cotes.

### 3. Live visible dès le coup d'envoi
À l'heure officielle, un match encore `pending` passe temporairement en `kickoff_wait`. Le panneau affiche immédiatement le match avec `confirmation live en attente`, puis bascule sur le statut réel dès qu'ESPN/Sofascore/FotMob le confirme.

### 4. Protection contre les régressions de statut
Les observations live sont triées par état et qualité de source. Une observation `pending` tardive ne peut plus écraser un match déjà `live`, `mi-temps`, `2e mi-temps`, etc.

### 5. Sécurité Git
Ajout de `.gitignore` et `PUSH_ODDIUM_GITHUB.bat` avec contrôle de la racine Git et du remote avant tout `git add`.

## Variables minimales Coolify

```env
DISCORD_TOKEN=...
FOOTBALL_DATA_API_KEY=...
DB_PATH=/app/data/oddium.db
API_CACHE_DIR=/app/data/api_cache
```

`PROPLINE_API_KEY` n'est plus obligatoire.
