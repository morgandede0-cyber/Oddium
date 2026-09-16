# Oddium V9.0.6 — Global Live Feed Fix

- SofaScore utilise désormais son endpoint global `api.sofascore.com/api/v1/sport/football/events/live`.
- Correction du host SofaScore (`api.sofascore.com` au lieu de `www.sofascore.com/api`).
- Un seul payload live est partagé entre les 6 compétitions puis filtré par championnat.
- Filtrage tolérant par ID + nom/slug du tournoi pour résister aux changements d'ID de saison.
- Le daily scheduled-events reste un fallback pour les transitions terminales.
