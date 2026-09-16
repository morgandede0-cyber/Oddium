# Oddium V13 — Premium Clean UI

Cette version conserve le moteur 5DollarFootballAPI Pro de la V12 et refond l'expérience Discord.

## Interface

- accueil plus court avec seulement les indicateurs utiles ;
- navigation championnat plus lisible ;
- calendrier groupé par journée ;
- fiche match et cotes 1/N/2 harmonisées ;
- ticket de pari et changement de cote redesignés ;
- page Mes paris, profil et classement simplifiées ;
- Live limité aux informations utiles : compétition, état, chrono, score ;
- fiche Live détaillée séparée pour statistiques et événements ;
- suppression des informations techniques de source dans l'interface publique.

## Anti-doublons

V13 ne se contente plus de masquer les doublons par ID fournisseur.
Les rencontres sont dédupliquées à l'affichage à partir de la compétition, de l'heure et de noms d'équipes normalisés. Les variantes comme `Torino FC / Torino`, `AS Roma / Roma`, `Como 1907 / Como` ou `Parma Calcio 1913 / Parma` sont fusionnées.

Le panneau Live permanent dispose également d'un verrou de création : deux boucles de rafraîchissement concurrentes ne peuvent plus créer deux messages. Après un redéploiement, Oddium recherche d'abord un panneau existant et le réutilise. Les anciens panneaux Live en double sont supprimés automatiquement si le bot possède la permission de gérer les messages.

## Direction artistique

La direction générale reprend les principes qui fonctionnent bien dans les bots de pronostics premium : contraste sombre, accent or Oddium, états rouge/vert, écrans courts et actions progressives. Le Live reste volontairement en embed afin de pouvoir être actualisé rapidement sans régénérer une image à chaque changement de score ou de cote.
