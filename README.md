
## Panneau Matchs en direct (V8.1)

Utilise `/setup_live` dans le salon où tu veux le tableau des scores.
Le message est épinglé et se met à jour automatiquement lorsque PropLine renvoie un nouveau score ou un changement de statut.
Le polling est fait côté bot avec cache : les utilisateurs Discord ne consomment aucune requête API.

# ODDIUM V8 — PropLine

Bot Discord de paris football virtuels avec cotes bookmaker réelles.

## Compétitions
- Ligue 1
- Premier League
- La Liga
- Bundesliga
- Serie A
- Ligue des Champions

## Cotes
Les cotes viennent de PropLine (`h2h` / 1X2). Oddium ne génère plus de cote avec Elo, Poisson, Dixon-Coles ou ML.

Une seule grille bookmaker complète est utilisée pour chaque match selon l'ordre `PROPLINE_BOOKMAKERS`. Les prix américains du bookmaker sont convertis de façon déterministe en cotes décimales.

## Cache
Le bot utilise deux niveaux de cache : SQLite pour le fonctionnement Discord et `data/api_cache_propline` pour les réponses PropLine. Un clic utilisateur ne déclenche aucune requête API.

La récupération des cotes se fait en **bulk par championnat**, pas match par match. Voir `V8_PROPLINE_CACHE.md`.

## Installation
1. Lance `INSTALLER.bat`.
2. Copie `.env.example` en `.env` si besoin.
3. Renseigne `DISCORD_TOKEN=` et `PROPLINE_API_KEY=`.
4. Lance `DEMARRER.bat`.

Ne partage jamais tes clés.
