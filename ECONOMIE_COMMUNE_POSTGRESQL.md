# Oddium — économie commune PostgreSQL

Cette version n'utilise plus le bridge HTTP Altherya pour le Gold.

Définir uniquement `ECONOMY_DATABASE_URL` avec exactement la même **Postgres URL (internal)** que dans Altherya.

Oddium lit/débite/crédite directement `economy_wallets` avec des transactions idempotentes dans `economy_transactions`. Les événements destinés aux logs/résultats Altherya sont déposés dans `economy_events` et consommés par Altherya.

`ALTHERYA_BRIDGE_URL` et `ALTHERYA_BRIDGE_TOKEN` ne sont plus nécessaires pour cette version.
