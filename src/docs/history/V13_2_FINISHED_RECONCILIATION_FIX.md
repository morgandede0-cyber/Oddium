# Oddium V13.2 — Finished reconciliation fix

Correction du faux état `DÉMARRAGE` qui pouvait rester affiché après la fin réelle d'un match.

## Changements

- Un `kickoff_wait` est désormais un état temporaire uniquement.
- Dès 15 minutes après le coup d'envoi théorique, si 5Dollar connaît l'identifiant du match mais que le flux global live ne l'a pas confirmé, Oddium interroge directement la fiche 5Dollar du match.
- Cette vérification directe est limitée à une fois toutes les 5 minutes par rencontre afin de préserver le quota Pro.
- Si 5Dollar renvoie `finished`, le score final, le statut et le règlement des paris passent par le pipeline canonique habituel.
- Si aucune source ne confirme jamais le match, le faux état orange `DÉMARRAGE` expire au bout de 150 minutes. Le match disparaît alors du panneau Live au lieu de rester affiché plusieurs heures.
- Le résultat peut toujours être reçu plus tard et mettre à jour/régler la rencontre.

Cela corrige notamment le cas où un match de Serie A terminé restait affiché `DÉMARRAGE • 0'` parce que le endpoint 5Dollar `status=live` ne renvoie plus les matchs une fois terminés.
