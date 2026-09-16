# Oddium V8.1 — panneau Matchs en direct

Commande admin : `/setup_live`

Le bot installe un second panneau permanent et épinglé consacré aux scores live.
Le panneau reste séparé du carrousel de paris `/setup`.

## Fonctionnement

- Le panneau affiche les matchs en cours des compétitions actives.
- Les scores viennent de l'endpoint gratuit PropLine `/sports/{sport}/scores`.
- Le bot interroge les scores environ toutes les 120 secondes uniquement pour les compétitions qui ont un match dans la fenêtre live.
- Les clics Discord n'appellent jamais PropLine.
- Un cache de 110 secondes évite les appels doublons.
- Le message Discord n'est réédité que lorsqu'un score ou un statut de match change.
- Le système fonctionne même si personne n'a parié sur le match.
- Les matchs terminés disparaissent automatiquement du panneau et les paris sont réglés comme avant.

## Réglages .env

```env
SCORES_REFRESH_SECONDS=120
SCORES_CACHE_SECONDS=110
LIVE_PANEL_LOOKBACK_MINUTES=210
LIVE_PANEL_LOOKAHEAD_MINUTES=20
```

Ces valeurs privilégient un score suffisamment frais tout en protégeant le quota gratuit PropLine.
