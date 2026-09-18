# Audit V59 — Oddium

Audit structurel du code Python, de l'économie, du démarrage Coolify et des anciens ponts Altherya.

## Corrections
- Connexion PostgreSQL commune testée obligatoirement pendant `setup_hook()`.
- Oddium refuse désormais de démarrer si `ECONOMY_DATABASE_URL` est absente, au lieu de laisser une situation ambiguë.
- Le démarrage affiche un diagnostic non secret : base, hôte, port, nombre de portefeuilles et empreinte SHA-256 tronquée de l'URL.
- L'ancien client HTTP `legacy_bet/integrations/altherya/client.py`, devenu inutilisé depuis PostgreSQL, a été retiré.
- Les anciennes variables Docker `ALTHERYA_BRIDGE_URL` et `ALTHERYA_BRIDGE_TOKEN` ont été retirées.
- Les caches Python/Pytest ont été retirés de l'archive.
- Le portefeuille SQLite local reste uniquement comme compatibilité de schéma/audit ; `EconomyAdapter.get_balance`, `debit` et `credit` utilisent PostgreSQL pour le solde dépensable.

## Vérifications
- Compilation de tous les fichiers Python : OK.
- Les tests complets Oddium n'ont pas pu être exécutés dans l'environnement d'audit faute de la dépendance locale `aiosqlite`; aucune erreur de syntaxe n'est présente.

## Diagnostic de déploiement
Comparer `empreinte=XXXXXXXXXXXX` dans les logs d'Altherya et d'Oddium. Les valeurs doivent être identiques. Le nombre `wallets=` permet aussi de confirmer que les deux applications voient la même table.
