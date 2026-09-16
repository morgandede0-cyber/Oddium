# Oddium V13.4 — Timer State Fix

- Suppression des chronos estimés basés uniquement sur l'heure de coup d'envoi.
- Aucun chrono affiché à la mi-temps, pendant une suspension, aux tirs au but, après report/annulation ou fin de match.
- Validation du chrono selon la phase : 1H <= 45, 2H 46-90, prolongations 91-120.
- Le passage en mi-temps efface immédiatement un ancien `live_clock` en base.
- 5Dollar n'enregistre plus `MT`/`FT` comme chrono.
