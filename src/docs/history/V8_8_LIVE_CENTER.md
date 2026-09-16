# Oddium V8.8 — Live Center

- Timeline live persistante par match (150 signaux max, affichage des 4 derniers événements significatifs).
- Coup d'envoi, score, mi-temps, reprise, prolongations, tirs au but, suspension, report, annulation et fin.
- Bouton permanent **Suivre un match** : abonnement individuel à un match live.
- Notifications DM instantanées via le WebSocket Oddium Live pour les événements importants (pas de spam sur chaque minute).
- Bouton **Mes paris live** : score actuel, mise, gain potentiel et état gagnant/non-gagnant.
- Bouton Actualiser relit l'état local ; le panneau reste automatiquement piloté par le WebSocket.
- PropLine reste réservé aux cotes. Les données live restent livescoreFootball + ESPN fallback.
- Les cartons, remplacements, VAR, buteur/passeur ne sont affichés que si une source live gratuite les fournit réellement ; Oddium n'invente jamais un événement.
