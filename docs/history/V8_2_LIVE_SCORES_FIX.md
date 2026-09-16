# Oddium V8.2 — correctif panneau live

Correctifs du panneau `/setup_live` :

- polling des scores plafonné à 60 s, même si un ancien `.env` contient 300 s ;
- lecture forcée de PropLine pour une compétition qui a réellement un match dans la fenêtre live ;
- le cache disque ne peut plus masquer un nouveau but ;
- le panneau Discord est réédité à chaque cycle de vérification, pas uniquement quand le score change ;
- prise en charge de variantes de statut (`in_progress`, `in progress`, `live`, `playing`) ;
- prise en charge de plusieurs formes de payload score ;
- réconciliation des IDs d'événements PropLine fusionnés/changés ;
- suppression du faux statut `EN COURS` basé uniquement sur l'heure de coup d'envoi : un match n'est affiché live que si PropLine renvoie un statut/score live.

Le nombre de requêtes reste limité : le bot ne force `/scores` que pour les compétitions ayant un candidat autour de l'heure actuelle. Les clics Discord ne déclenchent toujours aucune requête API.
