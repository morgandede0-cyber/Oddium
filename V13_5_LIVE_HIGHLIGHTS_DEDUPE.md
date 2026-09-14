# Oddium V13.5 — Live Highlights Deduplication

- Déduplication sémantique des temps forts dans le cycle courant.
- Déduplication persistante avant insertion SQLite.
- `period_score` devient un snapshot stable : son horloge mouvante est ignorée.
- Les anciennes bases contenant déjà des doublons sont nettoyées à l'affichage sans suppression de la BDD.
- Double garde-fou côté service + UI.
