# Oddium V9.1.2 sur Coolify

Cette version est préparée pour Coolify. Le bot Discord n'a pas besoin d'un domaine public ni d'un port exposé : son WebSocket Live reste interne au même conteneur sur `127.0.0.1:8765`.

## Méthode recommandée : Dockerfile dans un dépôt Git

1. Mets ce dossier dans un dépôt GitHub/GitLab/Gitea accessible par Coolify.
2. Dans Coolify : **New Resource > Application > Public/Private Repository**.
3. Sélectionne le dépôt puis choisis **Dockerfile** comme Build Pack.
4. Dockerfile : `Dockerfile`.
5. Ne configure **aucun domaine** et **aucun port public**.
6. Dans **Environment Variables**, ajoute au minimum :

   - `DISCORD_TOKEN` = token Discord du bot
   - `FOOTBALL_DATA_API_KEY` = clé gratuite football-data.org
   - `PROPLINE_API_KEY` = optionnel (compatibilité seulement)
   - `GUILD_ID` = ID du serveur Discord si tu l'utilises déjà

7. Dans **Persistent Storage**, crée :

   - volume vers `/app/data`
   - volume vers `/app/logs`

   `/app/data` contient notamment SQLite, les sauvegardes et le cache API. Il doit être persistant.

8. Déploie l'application.

Le `Dockerfile` possède déjà un HEALTHCHECK qui contrôle `http://127.0.0.1:8765/health` depuis l'intérieur du conteneur.

## Alternative : Docker Compose dans Coolify

Si tu préfères créer une ressource **Docker Compose**, utilise `docker-compose.coolify.yml`.

Dans Coolify, définis les variables :

```env
DISCORD_TOKEN=...
FOOTBALL_DATA_API_KEY=...
PROPLINE_API_KEY=
GUILD_ID=...
```

Les volumes nommés `oddium-data` et `oddium-logs` sont déjà prévus et persistent entre les redéploiements.

## Variables Live recommandées

```env
LIVE_POLL_SECONDS=5
LIVE_DISCOVERY_SECONDS=60
LIVE_FINISHED_DISPLAY_MINUTES=5
```

Le reste peut garder les valeurs présentes dans `.env.example` ou être ajouté dans Coolify si tu veux les personnaliser.

## Important

- Ne mets jamais tes vraies clés dans Git.
- Ne commit pas `.env`.
- Le bot n'a pas besoin d'être exposé sur Internet.
- Ne crée pas de proxy/domain pour le port 8765 : il sert uniquement au WebSocket local d'Oddium.
- Vérifie les logs dans **Coolify > Logs** après le premier déploiement.

Tu dois voir notamment :

```text
WebSocket live Oddium actif sur ws://127.0.0.1:8765/live
Panneau Discord connecté au WebSocket Oddium Live
Oddium Live: ... match(s) visible(s)
```
