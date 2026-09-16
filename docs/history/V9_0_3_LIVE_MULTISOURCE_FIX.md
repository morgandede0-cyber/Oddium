# Oddium V9.0.3 — Live multi-source

- Ajout d’un fallback live Sofascore sans clé API pour les 6 compétitions.
- Corrige le cas où ESPN remonte La Liga mais omet Ligue 1 / Serie A.
- Le flux quotidien Sofascore est mutualisé entre les compétitions pour éviter 6 requêtes par cycle.
- Ordre de traitement live : livescoreFootball → ESPN → Sofascore.
- PropLine reste réservé aux cotes et au secours règlement, jamais au scan live rapide.
