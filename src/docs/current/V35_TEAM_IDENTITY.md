# Oddium V35 — Team Identity

Oddium met désormais en cache les écussons des clubs des compétitions supportées au démarrage, sans bloquer le bot et sans créer de panneau Discord. La source visuelle est le manifeste `frertommy/team-logos`, qui référence des PNG transparents issus du CDN ESPN et des masters SVG pour certains clubs. 5DollarFootballAPI reste l'unique autorité pour les données football, matchs, scores, cotes, événements et statistiques.

Les logos sont enregistrés dans `assets/team_logos/` et `index.json` maintient les alias de noms. Le moteur visuel normalise accents, ponctuation et préfixes usuels (FC/AFC/CF...) afin de rapprocher les noms 5Dollar des noms d'écussons. Si aucun logo ne correspond, le blason Oddium à initiales de V34 reste le fallback.

Les droits sur les marques et écussons restent ceux de leurs propriétaires respectifs. Utilisation prévue: identification visuelle des équipes.
