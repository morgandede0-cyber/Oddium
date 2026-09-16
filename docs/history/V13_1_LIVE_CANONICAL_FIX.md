# Oddium V13.1 — Live Canonical Fix

Cette révision corrige les doublons de matchs dans le panneau Live.

## Principe

Oddium ne considère plus l’identifiant fourni par chaque source comme l’identité du match affiché. Un match est rapproché à partir de la compétition, des deux clubs normalisés et d’une fenêtre de coup d’envoi tolérante.

Exemples désormais fusionnés :
- Torino FC / Torino
- AS Roma / Roma
- Como / Como 1907
- Parma / Parma Calcio 1913
- Inter Milan / FC Internazionale Milano
- Udinese / Udinese Calcio
- Villarreal / Villarreal CF
- Real Betis / Real Betis Balompié

## Une ligne qui s’actualise

Lorsqu’une observation 5Dollar, ESPN, SofaScore ou FotMob correspond à un match déjà connu, Oddium met à jour la ligne canonique existante au lieu d’insérer une seconde ligne.

Le panneau applique en plus une seconde déduplication de sécurité avant l’affichage. La meilleure observation est conservée : état live le plus avancé, score connu, chrono le plus récent, puis source prioritaire (5Dollar en tête).

Aucune suppression agressive des anciennes lignes de base n’est faite afin de préserver les paris historiques et les références existantes.
