# Pont Altherya ↔ Oddium

Altherya est l'autorité unique du Gold. Oddium conserve sa base de paris/tickets mais lit, débite et crédite `players.wallet_gold` via l'API privée d'Altherya. `bank_gold` n'est jamais exposé à Oddium.

## Variables Coolify

Même secret long et aléatoire dans les deux applications :

Altherya : `ALTHERYA_BRIDGE_TOKEN`, `ALTHERYA_BRIDGE_HOST=0.0.0.0`, `ALTHERYA_BRIDGE_PORT=8787`.

Oddium : `ALTHERYA_BRIDGE_TOKEN`, `ALTHERYA_BRIDGE_URL` pointant vers Altherya sur le réseau privé Coolify, par exemple `http://altherya:8787` si ce nom est résolu par le réseau commun.

Ne publiez pas le port 8787 sur Internet. Les requêtes sont authentifiées par Bearer token, mais le service doit rester sur le réseau privé.

## Flux

- Solde Oddium → `wallet_gold` Altherya.
- Mise simple/combinée → débit atomique Altherya + ledger idempotent des deux côtés.
- Gain/remboursement → crédit idempotent Altherya.
- Pari accepté → carte dans le salon configuré par `/logs` d'Altherya.
- Ticket réglé → carte gain/perte/remboursement dans le salon configuré par `/succes`.
- Les préférences DM Oddium n'affectent pas la publication Altherya.
