# Oddium V13.3 — Live Timer Fix

- Corrige les matchs live dont 5Dollar renvoie le score/statut mais pas `status_code` minute par minute.
- Lecture robuste des champs `minute`, `elapsed`, `match_minute`, `live_minute`, `timer` et variantes imbriquées.
- Si 5Dollar confirme qu'un match est live mais n'envoie vraiment aucun chrono, le panneau affiche une estimation `~34'` calculée depuis l'heure de coup d'envoi.
- Dès qu'un vrai chrono fournisseur revient, il remplace automatiquement l'estimation.
- Aucun timer artificiel n'est affiché à la mi-temps, après le match, en cas de report ou d'annulation.
