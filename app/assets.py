"""
Listes d'actifs à scanner, par catégorie.

Actions/Indices/Matières premières ont DEUX symboles (un par fournisseur
possible : Yahoo ou Twelve Data), car le format des tickers diffère d'un
fournisseur à l'autre. La Crypto reste inchangée (Binance, un seul format).

v1.2 — Forex basculé de Finnhub vers Twelve Data : le plan gratuit
Finnhub ne donne plus accès à /forex/candle (403 systématique). Twelve
Data supporte nativement les paires forex au format "EUR/USD", le même
format que celui déjà confirmé fonctionnel pour XAU/USD (or).

⚠️ Les symboles Twelve Data pour les indices et matières premières
restent à vérifier via /symbol_search — les tickers actuels (SPX, IXIC,
DJI, FCHI, XAG/USD, WTI/USD, BRENT/USD) renvoient une erreur 404.
"""

CRYPTO = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
]

# Format Twelve Data : "EUR/USD" (même format que XAU/USD, déjà confirmé
# fonctionnel). À tester une fois déployé, comme les autres symboles.
FOREX = [
    {"twelvedata": "EUR/USD", "display": "EUR/USD"},
    {"twelvedata": "GBP/USD", "display": "GBP/USD"},
    {"twelvedata": "USD/JPY", "display": "USD/JPY"},
    {"twelvedata": "AUD/USD", "display": "AUD/USD"},
    {"twelvedata": "USD/CHF", "display": "USD/CHF"},
]

# Les tickers actions sont identiques chez Yahoo et Twelve Data (cas simple)
ACTIONS = [
    {"yahoo": "AAPL", "twelvedata": "AAPL", "display": "Apple"},
    {"yahoo": "MSFT", "twelvedata": "MSFT", "display": "Microsoft"},
    {"yahoo": "GOOGL", "twelvedata": "GOOGL", "display": "Alphabet"},
    {"yahoo": "AMZN", "twelvedata": "AMZN", "display": "Amazon"},
    {"yahoo": "NVDA", "twelvedata": "NVDA", "display": "Nvidia"},
    {"yahoo": "META", "twelvedata": "META", "display": "Meta"},
    {"yahoo": "TSLA", "twelvedata": "TSLA", "display": "Tesla"},
    {"yahoo": "JPM", "twelvedata": "JPM", "display": "JPMorgan"},
    {"yahoo": "V", "twelvedata": "V", "display": "Visa"},
    {"yahoo": "UNH", "twelvedata": "UNH", "display": "UnitedHealth"},
    {"yahoo": "XOM", "twelvedata": "XOM", "display": "Exxon"},
    {"yahoo": "JNJ", "twelvedata": "JNJ", "display": "Johnson & Johnson"},
    {"yahoo": "WMT", "twelvedata": "WMT", "display": "Walmart"},
    {"yahoo": "PG", "twelvedata": "PG", "display": "Procter & Gamble"},
    {"yahoo": "ORCL", "twelvedata": "ORCL", "display": "Oracle"},
    {"yahoo": "ADBE", "twelvedata": "ADBE", "display": "Adobe"},
]

# Indices : accès réservé aux plans payants chez Twelve Data (à partir de
# 29$/mois) — non disponible sur le plan gratuit. On utilise donc
# uniquement Yahoo pour cette catégorie (voir run_and_notify.py).
INDICES = [
    {"yahoo": "^GSPC", "display": "S&P 500"},
    {"yahoo": "^IXIC", "display": "Nasdaq Composite"},
    {"yahoo": "^DJI", "display": "Dow Jones"},
    {"yahoo": "^FCHI", "display": "CAC 40"},
    {"yahoo": "^GDAXI", "display": "DAX"},
]

# Matières premières disponibles sur le plan gratuit Twelve Data : l'Or
# seulement (XAU/USD). WTI/USD et XBR/USD (Brent) existent bien dans leur
# catalogue officiel (/commodities), mais renvoient 404 en pratique — même
# schéma que les indices : accès énergie réservé aux plans payants.
COMMODITIES = [
    {"yahoo": "GC=F", "twelvedata": "XAU/USD", "display": "Or"},
]

# Argent + Pétrole WTI/Brent : pas d'accès gratuit chez Twelve Data pour
# ces trois-là (argent = pas de version USD dans leur catalogue, WTI/Brent
# = accès énergie payant) -> Yahoo uniquement, toujours, comme les indices.
COMMODITIES_YAHOO_ONLY = [
    {"yahoo": "SI=F", "display": "Argent"},
    {"yahoo": "CL=F", "display": "Pétrole WTI"},
    {"yahoo": "BZ=F", "display": "Pétrole Brent"},
]
