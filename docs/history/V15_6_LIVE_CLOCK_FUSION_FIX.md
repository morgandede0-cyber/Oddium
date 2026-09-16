# Oddium V15.6 — Live Clock Fusion Fix

- Corrige les matchs affichés `DIRECT` sans minute quand 5Dollar ne fournit pas de chrono.
- Le chrono fourni par ESPN / SofaScore / FotMob est désormais conservé si un provider suivant renvoie un score/statut mais aucun chrono.
- Un provider plus lent ne peut plus faire reculer le chrono déjà enregistré pendant la même phase.
- Les phases arrêtées (mi-temps, terminé, suspendu, etc.) continuent d'effacer le chrono afin d'éviter le bug historique `MI-TEMPS · 81'`.
- Aucun calcul artificiel depuis l'heure de coup d'envoi : Oddium n'affiche qu'un chrono réellement fourni par une source live.
