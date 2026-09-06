# Scanner multi-actifs — scan.py

Complément à `backtest.py` : au lieu de tester un seul actif sur l'historique,
`scan.py` calcule le score long/short **actuel** (dernière bougie close) pour
une liste d'actifs, et les classe du signal le plus fort au plus faible.

## Installation

Place `scan.py` dans le même dossier `app/` que ton `backtest.py` existant
(le script en dépend directement).

## Utilisation

```bash
cd app
python scan.py --symbols BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT --interval 5m --threshold 60
```

Sur Colab :
```
!cd app && python scan.py --symbols BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT ADAUSDT --interval 5m --threshold 60
```

## Paramètres

- `--symbols` : liste des paires à scanner (n'importe quelle paire listée sur Binance)
- `--interval` : unité de temps (1m, 5m, 15m, 1h...)
- `--limit` : nombre de bougies récupérées par actif (1000 par défaut — nécessaire
  pour que le calcul de tendance 1H et les EMA50 soient fiables)
- `--threshold` : score minimum (0-100) pour qu'un actif soit considéré LONG ou SHORT
- `--top` : nombre de résultats affichés à l'écran (tous sont sauvegardés dans le CSV)

## Sortie

Le script affiche un tableau trié par score décroissant et sauvegarde
`scan_results.csv` avec, pour chaque actif : prix de clôture, scores long/short,
direction détectée, RSI, volume relatif, tendance 1H.

## Limites à connaître

- Le scan est fait sur la dernière bougie **déjà close** — pas en intra-bougie.
- Chaque appel = une requête API par actif. Avec beaucoup de symboles,
  prévoir quelques secondes d'exécution (une pause de 0,3s entre requêtes
  est intégrée pour éviter le rate-limit de l'API publique Binance).
- Un score élevé signale une configuration technique, pas une garantie de
  mouvement — à utiliser comme point de départ pour ta watchlist, pas comme
  signal d'exécution automatique.
