# Oddium V9.0.8 — Live scan non bloquant

- Correction principale : les sources live ne sont plus interrogées séquentiellement.
- ESPN, SofaScore Live et livescoreFootball sont lancés en parallèle.
- Timeout indépendant de 6 secondes par source.
- livescoreFootball est limité à EPL + LaLiga, les seules couvertures vérifiées, pour éviter les timeouts sur fra.1 / ita.1 / etc.
- Le terminal affiche `Live scan démarré: ...` avant chaque championnat puis le détail des résultats.
- Une source en panne n'empêche plus les autres championnats d'être scannés.
