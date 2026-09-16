# Oddium V15 — Best-of Engine

Cette version poursuit la consolidation d'Oddium autour des points forts observés dans plusieurs projets football/Discord, sans copier leur code.

## 1. Live State Machine robuste
Inspiré des trackers qui comparent l'état précédent au nouvel état au lieu de faire confiance aveuglément au dernier poll.

Oddium refuse maintenant les retours impossibles :
- 2e mi-temps → 1re mi-temps ;
- prolongation → 2e mi-temps ;
- match terminé → live ;
- pénalties → prolongation.

Un provider générique `IN_PLAY` ne remplace plus une phase plus précise déjà connue. Après une mi-temps confirmée, un retour générique `IN_PLAY` est interprété comme reprise de la 2e période.

## 2. Match Center interactif
La fiche live devient un mini centre de match avec quatre vues dans le même message Discord :
- **Résumé** : score, statut, chrono valide, dernier fait important ;
- **Stats** : possession, tirs, cadrés, corners, fautes, hors-jeu ;
- **Temps forts** : timeline dédupliquée et libellés plus lisibles ;
- **Marché** : cote d'ouverture → cote actuelle, variation %, probabilité normalisée.

Le bouton **Suivre** est intégré à la fiche. Les boutons éditent le même message au lieu d'empiler des réponses.

## 3. Alertes live idempotentes
Les notifications des matchs suivis utilisent désormais `notification_log` et une empreinte sémantique de l'événement.

Conséquence : un retry, une reconnexion WebSocket ou un redeploy ne doit plus envoyer deux fois le même but/carton/événement à la même personne.

## 4. Market movement
Ajout d'un snapshot marché :
- première cote connue ;
- cote actuelle ;
- mouvement en pourcentage ;
- probabilité implicite normalisée ;
- nombre de snapshots disponibles.

Cela reprend l'idée d'un bookmaker lisible sans transformer Oddium en terminal de trading.

## Compatibilité
- Base SQLite existante conservée.
- Aucun pari historique supprimé.
- 5DollarFootballAPI Pro reste la source principale.
- Les fallbacks existants restent actifs.
- L'interface V13/V14 reste compatible ; le Match Center s'ajoute au flux Live.
