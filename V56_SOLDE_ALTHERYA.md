# Oddium V56 — Solde Altherya

- Ajout du bouton `💰 Solde` au panneau principal Oddium.
- Le solde est lu en temps réel via `EconomyAdapter` et donc via le bridge Altherya lorsqu'il est configuré.
- Aucun fallback silencieux vers le portefeuille SQLite local si le bridge Altherya est indisponible.
- L'écran est privé/éphémère et réutilise la fenêtre temporaire Oddium.
- Bouton `🔄 Actualiser` pour relire le solde sans ouvrir une nouvelle page.
- Bouton `🏠 Retour à l'accueil` dans la même fenêtre.
