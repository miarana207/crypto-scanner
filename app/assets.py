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

# ⚠️ Symboles Twelve Data non vérifiés en conditions réelles pour les indices
# (SPX, IXIC, DJI, FCHI renvoient une 404 actuellement — à corriger via
# /symbol_search avant de compter dessus)
INDICES = [
    {"yahoo": "^GSPC", "twelvedata": "SPX", "display": "S&P 500"},   # à vérifier
    {"yahoo": "^IXIC", "twelvedata": "IXIC", "display": "Nasdaq Composite"},  # à vérifier
    {"yahoo": "^DJI", "twelvedata": "DJI", "display": "Dow Jones"},   # à vérifier
    {"yahoo": "^FCHI", "twelvedata": "FCHI", "display": "CAC 40"},   # à vérifier
    {"yahoo": "^GDAXI", "twelvedata": "DAX", "display": "DAX"},      # à vérifier
]

# ⚠️ Symboles Twelve Data non vérifiés pour l'argent et le pétrole (XAU/USD
# est confirmé fonctionnel ; XAG/USD, WTI/USD, BRENT/USD renvoient une 404
# actuellement — à corriger via /symbol_search)
COMMODITIES = [
    {"yahoo": "GC=F", "twelvedata": "XAU/USD", "display": "Or"},
    {"yahoo": "SI=F", "twelvedata": "XAG/USD", "display": "Argent"},   # à vérifier
    {"yahoo": "CL=F", "twelvedata": "WTI/USD", "display": "Pétrole WTI"},   # à vérifier
    {"yahoo": "BZ=F", "twelvedata": "BRENT/USD", "display": "Pétrole Brent"},  # à vérifier
]
