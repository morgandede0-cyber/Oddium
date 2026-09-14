# Oddium V12 — 5Dollar Engine

Oddium utilise désormais **5DollarFootballAPI Pro comme source principale** pour les six compétitions du bot.

## Source principale

- calendriers et matchs futurs ;
- score live ;
- état/minute du match ;
- timeline : buts, corners, cartons, remplacements, penalties manqués et scores de période ;
- statistiques live : possession, attaques, attaques dangereuses, tirs cadrés/non cadrés ;
- corners/cartons sur les fixtures ;
- cotes **Bet365 1/N/2** pré-match ;
- cotes in-play disponibles dans le flux live pour de futures fonctions.

Le moteur appelle `GET /v1/fixtures?status=live&include=odds,events,stats` une fois pour tous les matchs en cours. Le résultat est ensuite réparti entre les compétitions Oddium. Le polling 5Dollar est indépendant du scan des fallbacks et vaut 45 secondes par défaut.

## Compétitions V12

- Premier League
- La Liga
- Ligue 1
- Bundesliga
- Serie A
- UEFA Champions League

## Fallbacks

En cas d'absence temporaire de 5Dollar, Oddium conserve ESPN, Sofascore, FotMob, TheSportsDB et livescoreFootball pour le live. `football-data.org` reste disponible comme secours calendrier/règlement. Le moteur Oddium Fusion reste le dernier secours de cotation si une rencontre n'a pas de 1/N/2 Bet365 exploitable.

## Coolify

Variable obligatoire :

```env
FIVE_DOLLAR_FOOTBALL_API_KEY=ta_cle
FIVE_DOLLAR_POLL_SECONDS=45
```

La clé n'est jamais intégrée dans le code ou dans le ZIP.

## Migration V10/V11

La colonne `five_dollar_fixture_id` est ajoutée automatiquement à SQLite. Oddium rapproche les anciens matchs `fd:` / `af:` par équipes + heure de coup d'envoi avant de créer une nouvelle ligne, afin d'éviter les doublons pendant la migration.

## IDs de ligues robustes

5Dollar peut servir des IDs `legacy` ou `public_v1` selon l'ancienneté du compte. V12 ne dépend donc pas d'IDs copiés en dur : elle charge périodiquement `/v1/leagues`, reconnaît les six compétitions par nom/pays, mémorise les IDs propres à la clé utilisée et expose le schéma `X-ID-Scheme` dans le test API.

## Statuts inconnus

Un statut `unknown` n'est **jamais** transformé automatiquement en match reporté/annulé. Oddium le garde en état non résolu afin d'éviter d'annuler un pari valide sur un simple retard de confirmation du fournisseur.
