# Oddium V14 — Fusion Core

V14 consolide les meilleures idées des bots étudiés sans copier leur code : bookmaker réactif façon PronoBot, timeline live idempotente façon live-score, et architecture de suivi robuste.

## Changements structurants
- **Une rencontre = un objet canonique** : table `provider_fixture_aliases` pour rattacher durablement les IDs fournisseurs au match Oddium.
- **Un temps fort = un événement unique** : empreinte sémantique persistée en base et index UNIQUE. Un retry, un redémarrage ou deux providers ne doivent plus multiplier la même action.
- Les `period_score` sont traités comme des snapshots : la minute n'entre pas dans leur identité.
- Les simples `clock_update` ne polluent plus la timeline des temps forts.
- Compatible avec les anciennes bases : migrations légères, aucune suppression des paris historiques.

## Direction produit retenue
- PronoBot : ticket clair, cote verrouillée, économie/risque et interface bookmaker compacte.
- Scoring Returns : suivi live, stats et événements importants.
- World Cup 2026 Bot : séparation dashboard / diagnostics et logique de résilience.
- Oddium conserve 5Dollar Pro comme source principale et ses fallbacks existants.
