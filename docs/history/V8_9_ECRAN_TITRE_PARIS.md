# Oddium V8.9 — Écran titre + modes de pari

- Nouveau panneau d'accueil utilisant l'image IV/Oddium.
- Deux boutons permanents seulement : **Voir les matchs** et **Parier**.
- Voir les matchs : carrousel des championnats, ouverture d'une ligue et consultation des rencontres sans bouton de mise.
- Parier : choix entre **Pari simple** et **Pari combiné**.
- Pari simple : parcours des championnats puis sélection 1/N/2 et mise.
- Pari combiné : 2 à 10 matchs différents, cote totale multiplicative, une seule mise et un gain potentiel commun.
- Vérification des cotes au moment de la validation du combiné ; si une cote a changé, le ticket doit être reconstruit avec les nouvelles cotes.
- Tables SQLite `combo_bets` et `combo_legs` ajoutées automatiquement.
- Règlement automatique des combinés lorsque les matchs concernés sont terminés.
