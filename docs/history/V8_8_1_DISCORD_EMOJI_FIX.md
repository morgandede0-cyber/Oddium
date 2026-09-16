# Oddium V8.8.1 — correctif panneau Live

Correction de l’erreur Discord `50035 Invalid Form Body` sur le panneau live.

Cause : le bouton **Actualiser** utilisait le caractère `↻` dans le champ `emoji`. Discord ne le considère pas comme un emoji valide pour un composant.

Correction : remplacement par l’emoji Unicode valide `🔄`.
