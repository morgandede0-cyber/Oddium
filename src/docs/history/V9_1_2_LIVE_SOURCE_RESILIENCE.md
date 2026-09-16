# Oddium V9.1.2 — Live Source Resilience

- Corrige le spam FotMob 404 : un échec vide est maintenant mis en cooldown et partagé entre les 6 ligues.
- FotMob utilise les paramètres de localisation/date attendus (`date`, `timezone`, `ccode3`) et des en-têtes navigateur cohérents.
- Ajoute TheSportsDB v1 comme filet de sécurité gratuit pour les 6 compétitions (clé publique gratuite 123, endpoint eventsday).
- TheSportsDB est limité à un appel par championnat / 60 s pour rester sous le quota gratuit.
- SofaScore vide est également mis en cooldown pour éviter 6 appels identiques par cycle.
- Le diagnostic Live affiche maintenant TheSportsDB séparément.
