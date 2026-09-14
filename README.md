# ODDIUM V10 — FREE-FIRST

Bot Discord de paris football **virtuels** avec calendrier, scores live, suivi de matchs, économie, paris simples/combinés et cotes calculées automatiquement.

## Compétitions
- Ligue 1
- Premier League
- La Liga
- Bundesliga
- Serie A
- Ligue des Champions

## Architecture des données

- **football-data.org** : calendrier canonique, résultats et classements.
- **ESPN + Sofascore + FotMob + TheSportsDB** : live redondant et détection des changements de statut.
- **football-data.co.uk** : consensus de marché public quand disponible.
- **Oddium Fusion** : moteur 1/N/2 qui combine le consensus public avec un modèle Poisson basé sur les forces offensives/défensives et le classement.
- **SQLite** : source locale de vérité pour l'interface Discord et les paris.

Oddium ne dépend plus d'une API de cotes payante pour fonctionner. `PROPLINE_API_KEY` reste optionnelle uniquement pour compatibilité avec les anciennes versions.

## Live V10

Le match apparaît dans le panneau dès son heure officielle de coup d'envoi avec l'état **confirmation live en attente**. Dès qu'un fournisseur confirme le direct, son statut, son chrono et son score remplacent immédiatement l'état provisoire.

Les observations de plusieurs fournisseurs sont triées pour empêcher une source lente en `pending` de faire disparaître un match déjà confirmé `live` par ESPN/Sofascore.

## Installation locale
1. Lance `INSTALLER.bat`.
2. Copie `.env.example` en `.env`.
3. Renseigne au minimum `DISCORD_TOKEN=` et `FOOTBALL_DATA_API_KEY=`.
4. Lance `DEMARRER.bat`.
5. Sur Discord : `/setup` puis, si tu veux un panneau live séparé, `/setup_live`.

## GitHub

`PUSH_ODDIUM_GITHUB.bat` refuse de pousser si la racine Git n'est pas exactement le dossier Oddium ou si `origin` ne ressemble pas à un dépôt Oddium. Cela évite d'envoyer par erreur `AppData`, `Documents` ou le dépôt Legacy.

Ne partage jamais ton token Discord ni ta clé football-data.org.
