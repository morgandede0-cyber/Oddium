# Oddium V15.1 — Database Migration Fix

Correctif de compatibilité pour les bases SQLite Oddium existantes.

## Problème corrigé

V15 créait l'index unique `uq_live_event_fingerprint` dans le script SQL initial avant que la migration n'ajoute la colonne `live_events.fingerprint` sur les anciennes bases. SQLite arrêtait donc le démarrage avec `no such column: fingerprint`.

## Correctif

- création de la table/index standard comme avant ;
- ajout de `live_events.fingerprint` via la migration `_ensure_column()` ;
- création de l'index `uq_live_event_fingerprint` uniquement après l'ajout de la colonne ;
- aucune suppression ou réinitialisation de la base ;
- paris, comptes, soldes et historique existants restent conservés.
