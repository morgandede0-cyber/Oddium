# Oddium V16.1 — Authoritative Live Visibility

- Design Discord inchangé.
- Le panneau DIRECT n'affiche plus les anciens états live issus de fournisseurs retirés.
- Un match actif doit être confirmé par SofaScore ou ESPN.
- `DÉMARRAGE` n'est visible que pour une fixture bookmaker réellement connue (`odds_available=1`).
- Le filtrage SofaScore est désormais strict sur l'identifiant du tournoi, sans fallback fuzzy nom/pays.
- Migration 16.1 non destructive : paris, Gold, tickets, cotes et résultats terminés sont conservés.
