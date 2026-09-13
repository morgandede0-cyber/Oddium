# Oddium V8.6 — Oddium Live

## Objectif
Le panneau Discord n'est plus repeint à intervalle fixe. Un collecteur gratuit observe les sources de score et transforme uniquement les changements réels (but, statut, fin de match) en événements envoyés sur **notre propre WebSocket local**.

## Chaîne live

1. `livescoreFootball/worldcup26.ir` est interrogé lorsque la compétition est couverte (Angleterre/Espagne).
2. ESPN public sert de source parallèle et de secours pour les 6 compétitions Oddium.
3. PropLine reste réservé aux cotes et au secours de règlement d'un pari si les sources gratuites ne répondent pas.
4. Quand SQLite détecte une différence réelle de score/statut, le collecteur publie un événement sur `ws://127.0.0.1:8765/live`.
5. Le client WebSocket interne reçoit cet événement et modifie immédiatement le panneau Discord.

Le timer de collecte existe encore car les sources gratuites sont HTTP, mais **le panneau Discord lui-même n'est plus actualisé par timer**. Sans changement de score/statut, aucun edit Discord n'est effectué.

## Optimisation
- Match connu proche/en direct : collecte rapide (`LIVE_POLL_SECONDS=5`).
- Aucune rencontre locale connue : découverte lente (`LIVE_DISCOVERY_SECONDS=60`).
- Les 6 ligues ne sont donc pas martelées toutes les 5 secondes lorsqu'il ne se passe rien.

## Configuration
```env
LIVE_POLL_SECONDS=5
LIVE_DISCOVERY_SECONDS=60
LIVE_WS_HOST=127.0.0.1
LIVE_WS_PORT=8765
LIVE_WS_PATH=/live
```

Endpoint de santé local : `http://127.0.0.1:8765/health`

## Important
Créer notre WebSocket rend la diffusion Oddium instantanée **après réception de l'information par la source**. Il ne peut pas rendre une source HTTP plus rapide qu'elle ne publie elle-même le but.
