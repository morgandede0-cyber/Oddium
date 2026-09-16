# Oddium V15.2 — Ticket Settlement Reconciliation

## Correctif
Les tickets pouvaient rester `PENDING` après la fin du match lorsque la rencontre sortait de la fenêtre temporelle du panneau Live.

V15.2 sépare désormais complètement le règlement des paris de la fenêtre d'affichage Live.

- recherche des paris simples encore PENDING sur les matchs des 7 derniers jours ;
- recherche également des jambes de combinés encore PENDING ;
- si le match est déjà marqué terminé en SQLite, règlement immédiat depuis le score final stocké ;
- sinon, si un identifiant 5Dollar est connu, interrogation directe de la fixture 5Dollar ;
- si la fixture est terminée, mise à jour du match puis règlement du pari/combiné ;
- interrogation directe limitée à une fois toutes les 5 minutes par fixture ;
- logique idempotente existante conservée pour empêcher un double crédit.

Aucune suppression de base n'est nécessaire.
