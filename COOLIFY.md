# Déployer Oddium V12 sur Coolify

1. Déploie le dépôt/ZIP avec le `Dockerfile` fourni.
2. Monte `/app/data` et `/app/logs` en volumes persistants.
3. Ajoute les variables d'environnement suivantes :

```env
DISCORD_TOKEN=...
FIVE_DOLLAR_FOOTBALL_API_KEY=...
FIVE_DOLLAR_POLL_SECONDS=45
FOOTBALL_DATA_API_KEY=...   # optionnel / secours
GUILD_ID=...                # optionnel
```

Ne place jamais la clé 5Dollar dans le dépôt GitHub.

Le plan Pro autorise 10 requêtes/minute. Oddium utilise une requête live globale mise en cache puis répartie entre les six compétitions ; les listes de fixtures sont elles aussi mises en cache.

Après déploiement, lance `/diagnostic_oddium` : la ligne 5Dollar doit être verte et le quota restant doit apparaître après le premier appel réussi.
