# Oddium V17 — 5Dollar-First Architecture

## Principe
5DollarFootballAPI Pro est la source prioritaire pour toutes les données qu'il fournit. Les autres fournisseurs ne sont plus fusionnés avec lui sur un match live : ils interviennent uniquement si 5Dollar ne fournit aucune donnée exploitable.

## 5Dollar prioritaire
- Calendrier / fixtures et identifiants de match
- Score live et résultat final
- Statut du match et minute via `status_code`
- Buts, corners et cartons (FT / HT)
- Timeline : buts, corners, jaunes, rouges, remplacements, penalties manqués, scores de période
- Statistiques live : attaques, attaques dangereuses, tirs cadrés/non cadrés, possession, splits 1re mi-temps
- Cotes Bet365 : 1X2, handicap asiatique, over/under buts, corners, Asian corners, cartes, marchés mi-temps, BTTS; opening/closing/in-play selon disponibilité/plan
- Classements et forme via fixtures d'équipe
- Ligues, équipes, pays et historique accessible au plan

## Fallbacks
- Live: SofaScore, puis ESPN, uniquement si 5Dollar ne renvoie aucun match exploitable pour la compétition.
- Résultat/règlement: 5Dollar direct par fixture id, puis résultats 5Dollar de la ligue; ensuite football-data.org, SofaScore, ESPN/FotMob/TheSportsDB.
- Calendrier: football-data.org si 5Dollar est indisponible.
- Classement: football-data.org si 5Dollar est indisponible.

## Non fourni par les endpoints publics 5Dollar actuels
Les 15 endpoints publics ne listent pas d'endpoint dédié aux compositions, blessures, profils/joueurs ou xG. Oddium ne fabrique donc pas ces données. Une source secondaire dédiée pourra être utilisée si une fonction du bot les demande.

## Anti-corruption live
Une fixture active a un seul propriétaire de données à la fois. Oddium ne mélange plus un score 5Dollar avec un chrono SofaScore ou une compétition ESPN. Cela évite les timers incohérents, les matchs rattachés à la mauvaise ligue et les doublons multi-provider.
