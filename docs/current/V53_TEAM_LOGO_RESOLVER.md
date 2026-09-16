# V53 — Team Logo Resolver

- Correction PSG: `PSG`, `Paris SG`, `Paris Saint-Germain FC` et variantes pointent vers `paris-saint-germain`.
- Résolveur d'alias renforcé pour les clubs des compétitions supportées (France, Angleterre, Espagne, Allemagne, Italie + compétitions UEFA).
- Les alias ne sont inscrits dans l'index que si le logo canonique existe réellement dans le cache/manifeste.
- Compatibilité avec les anciens `index.json`: le renderer peut retrouver directement le fichier canonique déjà en cache.
- Protection contre les collisions évidentes: pas d'alias vague `Paris`, afin de ne jamais confondre Paris FC et PSG.
- Le fallback reste actif uniquement lorsqu'aucun logo sûr n'est disponible.
