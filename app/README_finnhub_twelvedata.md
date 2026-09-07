# Sources multiples : Finnhub (Forex) + Twelve Data / Yahoo (Actions, Indices, Matières premières)

## Répartition mise en place

| Catégorie | Source | Détail |
|---|---|---|
| 🪙 Crypto | Binance | Toujours, 24/7 (inchangé) |
| 💵 Forex | Finnhub | Toujours (API officielle gratuite) |
| 📈 Actions | Twelve Data **ou** Yahoo | Twelve Data de 3h à 19h UTC (6h-22h à Madagascar), Yahoo le reste du temps |
| 📊 Indices | Twelve Data **ou** Yahoo | Idem |
| 🛢️ Matières premières | Twelve Data **ou** Yahoo | Idem |

Le fichier `run_and_notify.py` bascule automatiquement de source à chaque
exécution selon l'heure UTC — rien à faire manuellement une fois configuré.

## ⚠️ Trois choses à tester toi-même avant d'automatiser

Rien de tout ça n'a pu être testé en conditions réelles depuis mon
environnement (pas d'accès réseau) — seul le format des réponses a été
validé avec des données simulées conformes à la documentation officielle.

**1. Les candles actions de Finnhub sont exclues volontairement.** Plusieurs
utilisateurs rapportent une erreur 403 sur `/stock/candle` en plan gratuit,
alors que d'autres sources affirment que ça fonctionne — restriction
probablement silencieuse et non documentée. C'est pour ça que j'ai gardé
Twelve Data + Yahoo pour les actions plutôt que Finnhub.

**2. Les symboles Twelve Data pour indices et matières premières sont
incertains** (marqués `# à vérifier` dans `assets.py`) : `FCHI`, `DAX`,
`WTI/USD`, `BRENT/USD`. Teste chacun avant de compter dessus :
```python
from data_sources import klines_twelvedata
print(klines_twelvedata("WTI/USD", interval="15m", limit=20))
```
Si un symbole échoue, cherche le bon ticker sur https://twelvedata.com/symbolsearch
et corrige-le dans `assets.py`.

**3. Twelve Data a un plafond strict : 800 requêtes/jour, 8/minute.** Avec
la répartition actuelle (~26 actifs non-crypto/forex scannés pendant la
fenêtre Twelve Data), calcule le nombre de requêtes selon la fréquence de
ton cron pour rester sous la limite — voir la section suivante.

## Obtenir les clés API

**Twelve Data** : https://twelvedata.com/pricing → "Get free API Key" →
crée un compte, la clé apparaît directement dans ton tableau de bord.

**Finnhub** : https://finnhub.io/register → confirme ton email → la clé
gratuite apparaît sur ton tableau de bord.

Aucune carte bancaire demandée dans les deux cas.

## Ajouter les nouveaux secrets GitHub

En plus des 4 secrets déjà configurés, ajoute dans **Settings → Secrets
and variables → Actions** :

| Nom du secret | Valeur |
|---|---|
| `TWELVEDATA_API_KEY` | Ta clé Twelve Data |
| `FINNHUB_API_KEY` | Ta clé Finnhub |

Puis ajoute ces deux variables dans le bloc `env:` de
`.github/workflows/daily_scan.yml` :
```yaml
env:
  SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
  SMTP_HOST: smtp.gmail.com
  SMTP_PORT: 465
  EMAIL_SENDER: ${{ secrets.EMAIL_SENDER }}
  EMAIL_PASSWORD: ${{ secrets.EMAIL_PASSWORD }}
  EMAIL_RECIPIENT: ${{ secrets.EMAIL_RECIPIENT }}
  TWELVEDATA_API_KEY: ${{ secrets.TWELVEDATA_API_KEY }}
  FINNHUB_API_KEY: ${{ secrets.FINNHUB_API_KEY }}
```

## Fichiers à remplacer/ajouter dans `app/`

- `data_sources.py` (remplacer) — ajoute `klines_twelvedata` et `klines_finnhub_forex`
- `assets.py` (remplacer) — nouveau format avec un symbole par fournisseur
- `run_and_notify.py` (remplacer) — bascule horaire automatique
- `scan.py` — inchangé depuis la dernière version, pas besoin de le retoucher

## Ce que confirme le message reçu sur Slack/Email

Chaque message indique maintenant explicitement quelle source a été
utilisée ce run-là pour Actions/Indices/Matières premières
(`Source Actions/Indices/Matières premières ce run : twelvedata` ou
`yahoo`) — utile pour repérer rapidement si la bascule fonctionne comme
prévu une fois déployée.
