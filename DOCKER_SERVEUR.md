# Oddium V9.1.2 — Installation Docker serveur

Cette version conserve les données SQLite, les sauvegardes, le cache PropLine et les logs hors du conteneur.
Le redémarrage ou la reconstruction de l'image Docker ne supprime donc pas les paris.

## 1. Prérequis serveur

Installer Docker Engine + Docker Compose plugin.
Vérifier :

```bash
docker --version
docker compose version
```

## 2. Envoyer le dossier sur le serveur

Place tout le dossier Oddium sur le serveur, par exemple :

```bash
/opt/oddium
```

Puis :

```bash
cd /opt/oddium
```

## 3. Configurer .env

Le fichier `.env` n'est volontairement PAS inclus dans l'image Docker.
Sur le serveur, utilise ton `.env` actuel ou crée-le depuis `.env.example` :

```bash
cp .env.example .env
nano .env
```

Renseigne au minimum :

```env
DISCORD_TOKEN=TON_TOKEN_DISCORD
PROPLINE_API_KEY=TA_CLE_PROPLINE
GUILD_ID=ID_DE_TON_SERVEUR_DISCORD
```

Ne publie jamais ce fichier.

## 4. Premier démarrage

```bash
docker compose up -d --build
```

Voir les logs :

```bash
docker compose logs -f oddium
```

Tu dois notamment retrouver les lignes `Live scan ...`.

## 5. Commandes utiles

État :

```bash
docker compose ps
```

Redémarrer :

```bash
docker compose restart oddium
```

Arrêter :

```bash
docker compose down
```

Relancer après une mise à jour du code :

```bash
docker compose up -d --build
```

Logs récents :

```bash
docker compose logs --tail=200 oddium
```

## 6. Données persistantes

Les dossiers suivants restent sur le serveur :

- `./data/oddium.db` : base SQLite principale
- `./data/backups/` : sauvegardes automatiques
- `./data/api_cache_propline/` : cache API
- `./logs/` : logs Oddium

Ils sont montés dans le conteneur via `docker-compose.yml`.

## 7. Aucun port public nécessaire

Oddium est un bot Discord sortant. Le WebSocket Live `127.0.0.1:8765` reste interne au conteneur et ne doit pas être exposé à Internet.

## 8. Mise à jour sans perdre la base

Remplace les fichiers de code mais conserve les dossiers `data/`, `logs/` et ton `.env`, puis lance :

```bash
docker compose up -d --build
```

## 9. Vérifier la santé du conteneur

```bash
docker inspect --format='{{.State.Health.Status}}' oddium
```

Le healthcheck interroge le endpoint local Oddium Live `/health`.
