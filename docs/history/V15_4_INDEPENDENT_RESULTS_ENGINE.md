# Oddium V15.4 — Independent Results Engine

Le règlement des tickets ne dépend plus de 5DollarFootballAPI.

Priorité de récupération des résultats :
1. football-data.org (historique 7 jours, si clé configurée)
2. Sofascore
3. ESPN
4. FotMob
5. TheSportsDB
6. 5Dollar uniquement en dernier secours

Le moteur rapproche les équipes, accepte les variantes de noms déjà gérées par Oddium, vérifie l'heure à ±12 h et sait aussi corriger un flux dont domicile/extérieur serait inversé. L'ouverture de Mes tickets continue à déclencher une réconciliation immédiate.
