# Oddium V9.1.0 — Live Engine autonome

- Scan Live immédiat au démarrage.
- Collecteur indépendant de `/setup`, `/setup_live`, des panneaux et des compétitions de paris.
- Les 6 compétitions passent toujours dans le moteur; les caches HTTP évitent le spam réseau.
- Watchdog: timeout global par cycle + reprise automatique.
- Un match découvert pour la première fois génère maintenant un événement WebSocket.
- Le panneau est aussi rafraîchi par signature d'affichage, même si aucun événement de timeline n'a été produit.
- Découverte ramenée à 15 s par défaut (10–30 s configurable).
- Conservation des sources gratuites existantes: SofaScore Live + ESPN + livescoreFootball (EPL/LaLiga), PropLine seulement pour les cotes/secours règlement.
