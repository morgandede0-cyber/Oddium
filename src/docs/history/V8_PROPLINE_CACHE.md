# Oddium V8 — PropLine + cache intelligent

## Source unique des cotes
Oddium utilise désormais PropLine pour les cotes 1/N/2 (`h2h`) et ne fabrique plus aucune cote.

Compétitions activées par défaut :
- Ligue 1
- Premier League
- La Liga
- Bundesliga
- Serie A
- Ligue des Champions

## Économie de requêtes
- Les boutons Discord ne contactent jamais PropLine.
- Tous les joueurs lisent les mêmes données SQLite locales.
- Les réponses API sont aussi gardées dans `data/api_cache_propline`, donc le cache survit aux redémarrages.
- Les cotes sont téléchargées via l'endpoint **bulk** : une requête récupère tous les matchs d'un championnat, au lieu d'une requête par match.
- Fixtures : cache 6 h.
- Cotes à plus de 24 h : cache 6 h.
- Cotes à 6–24 h : cache 2 h.
- Cotes à 1–6 h : cache 30 min.
- Cotes dans la dernière heure : cache 15 min.
- Scores : cache 5 min, mais l'endpoint n'est interrogé que si des paris sont réellement en attente autour du coup d'envoi.
- En cas de 429/erreur temporaire, Oddium peut réutiliser le dernier cache connu.

## Bookmaker
Oddium choisit une seule grille bookmaker complète, dans l'ordre de préférence défini par :

`PROPLINE_BOOKMAKERS=pinnacle,bovada,draftkings,fanduel,betmgm,unibet`

Il ne fait ni moyenne, ni consensus, ni modèle statistique. Les cotes américaines reçues sont uniquement converties mathématiquement au format décimal pour l'affichage et les gains.

## Clé API
Dans `.env` :

`PROPLINE_API_KEY=...`

Ne jamais partager cette clé.
