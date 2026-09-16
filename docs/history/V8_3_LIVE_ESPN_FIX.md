# Oddium V8.3 — Live scoreboard fix

- Les cotes restent fournies par PropLine.
- Le panneau `/setup_live` utilise désormais le scoreboard public ESPN pour les scores/status en direct.
- Aucune clé ESPN et aucune requête PropLine consommée pour l'actualisation normale du panneau live.
- Ligue 1, Premier League, Liga, Bundesliga, Serie A et Ligue des Champions sont mappées sur leurs scoreboards ESPN.
- Si ESPN ne retourne aucune donnée, PropLine `/scores` reste un fallback.
- Le panneau peut découvrir un match live même si le cache local des fixtures ne l'avait pas détecté.
