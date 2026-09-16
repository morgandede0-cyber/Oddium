# Oddium V16 — Live Core Rebuild

Le design Discord n'a pas été modifié.

## Architecture Live reconstruite

- **SofaScore** : source live autoritaire pour statut, score et minute.
- **ESPN** : seul fallback live indépendant.
- **5DollarFootballAPI** : retiré du moteur live ; conservé pour les cotes Bet365 / fixtures bookmaker.
- **FotMob, TheSportsDB et livescoreFootball** : retirés de la boucle live afin d'éviter les conflits de compétition, de statut et de chrono.
- Le moteur indépendant V15.5 reste responsable des résultats historiques et du règlement des tickets.

## Correction du timer SofaScore

Le champ `time.initial` de SofaScore est exprimé en **secondes**. L'ancien moteur l'interprétait comme un nombre de minutes. En seconde période, `2700` était donc traité comme `2700 minutes`, rejeté par la validation 0–130, et le panneau affichait `DIRECT` sans timer.

V16 calcule maintenant : `(initial_seconds + elapsed_seconds) // 60`.

## Migration

Au premier démarrage V16, seul l'ancien état live transitoire est nettoyé. Les matchs, cotes, utilisateurs, soldes, paris, tickets et résultats terminés sont conservés. Le nouveau moteur reconstruit ensuite le Live depuis SofaScore/ESPN.
