# Oddium V18 — 5Dollar Full Ingest

Le moteur ne choisit plus quelques champs de 5Dollar avant de jeter le reste.
Pour chaque fixture, Oddium ingère le payload natif complet puis normalise les données utiles.

## 5Dollar ingéré
- fixture id, league id/name, team ids/names, kickoff UTC, round, league season id
- status, status_reason et status_code (minute / half / full)
- score FT + score MT
- corners FT + MT
- cartons jaunes/rouges
- événements: buts, corners, cartons, penalty manqué, remplacements, scores de période
- statistiques: attaques, attaques dangereuses, tirs cadrés/non cadrés, possession + splits MT
- Bet365: 1X2, handicaps, lignes buts/corners/cartons, marchés MT, BTTS, opening/closing/in-play quand fournis

## Règle Live
`status` + `status_code` 5Dollar sont autoritaires. L'heure théorique du coup d'envoi ne crée plus de faux état DÉMARRAGE.
Les anciens `kickoff_wait` sont nettoyés automatiquement. Une fixture locale déjà commencée mais absente de la page live est relue par son ID 5Dollar exact.

## Sources secondaires
Elles ne remplacent 5Dollar que lorsqu'une fonction/donnée n'est pas fournie ou lorsque 5Dollar est indisponible. Aucun mélange de deux fournisseurs dans l'identité/chrono d'une fixture 5Dollar.
