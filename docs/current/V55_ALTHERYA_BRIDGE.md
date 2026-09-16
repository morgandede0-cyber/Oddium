# Oddium V55 — Gold Altherya

- Suppression du portefeuille Oddium comme source de vérité : le solde dépensable vient de `Altherya.players.wallet_gold`.
- Débits de mises, gains et remboursements passent par le pont privé Altherya.
- Ledger Oddium conservé pour audit/idempotence.
- Publication des mises dans les Logs Altherya.
- Publication des résultats définitifs dans Succès & Résultats des jeux Altherya.
- Les combinés sont pris en charge.
- `bank_gold` n'est jamais accessible depuis Oddium.
- Code rangé sous `legacy_bet/integrations/altherya/`.
