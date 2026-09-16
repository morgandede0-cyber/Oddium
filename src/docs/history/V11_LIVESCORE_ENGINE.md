# Oddium V11 — LiveScore Engine

Cette version adapte à Oddium les idées principales du projet open source **Scoring-Returns-Bot** : suivi multi-matchs, boucle live dédiée, événements détaillés et statistiques de match.

## Architecture

- **football-data.org** : calendrier, résultats, classements et identité canonique des rencontres.
- **API-Football** *(optionnel)* : source live enrichie pour buts, VAR, cartons et statistiques détaillées.
- **ESPN + SofaScore + FotMob + TheSportsDB** : fallbacks gratuits redondants conservés.
- **Oddium WebSocket local** : distribue les changements au panneau Discord et aux notifications.
- **SQLite** : état live et historique d'événements persistants.

## API-Football

Deux modes sont supportés, au choix :

```env
API_FOOTBALL_KEY=cle_api_sports_directe
```

ou

```env
RAPIDAPI_KEY=cle_rapidapi
```

Le polling API-Football est mutualisé entre les six compétitions et limité par défaut à **45 secondes** :

```env
API_FOOTBALL_POLL_SECONDS=45
```

Sans clé API-Football, Oddium continue de fonctionner avec ses sources gratuites existantes.

## Live Center

Le panneau permanent n'affiche plus la timeline technique. Il reste compact : compétition, état, minute et score. Le bouton **📊 Détails** ouvre une fiche privée contenant :

- événements récents ;
- buts ;
- VAR ;
- cartons jaunes/rouges ;
- remplacements ;
- possession ;
- tirs / tirs cadrés ;
- corners ;
- fautes ;
- hors-jeu.

Les statistiques avancées nécessitent API-Football. Les scores/états continuent de fonctionner sans cette clé.

## Anti-doublon

Au démarrage/installation, Oddium conserve un seul panneau `🔴 ODDIUM LIVE` et supprime les anciens panneaux Live du bot dans les messages récents du salon lorsqu'il a les permissions nécessaires.
