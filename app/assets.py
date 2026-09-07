"""
Listes d'actifs à scanner, par catégorie.

Contrairement à avant, Actions/Indices/Matières premières ont maintenant
DEUX symboles (un par fournisseur possible : Yahoo ou Twelve Data), car le
format des tickers diffère d'un fournisseur à l'autre. Le Forex n'a qu'un
format Finnhub. La Crypto reste inchangée (Binance, un seul format).

⚠️ Les symboles Twelve Data pour les indices et matières premières sont
donnés à titre indicatif — je n'ai pas pu les vérifier en conditions
réelles (pas d'accès réseau dans mon environnement). Teste-les avant de
tout automatiser ; si un ticker ne fonctionne pas, corrige-le ici en te
référant à la documentation Twelve Data (recherche du symbole sur leur site).
"""

CRYPTO = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
]

# Format Finnhub : "OANDA:EUR_USD"
FOREX = [
    {"finnhub": "OANDA:EUR_USD", "display": "EUR/USD"},
    {"finnhub": "OANDA:GBP_USD", "display": "GBP/USD"},
    {"finnhub": "OANDA:USD_JPY", "display": "USD/JPY"},
    {"finnhub": "OANDA:AUD_USD", "display": "AUD/USD"},
    {"finnhub": "OANDA:USD_CHF", "display": "USD/CHF"},
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
INDICES = [
    {"yahoo": "^GSPC", "twelvedata": "SPX", "display": "S&P 500"},
    {"yahoo": "^IXIC", "twelvedata": "IXIC", "display": "Nasdaq Composite"},
    {"yahoo": "^DJI", "twelvedata": "DJI", "display": "Dow Jones"},
    {"yahoo": "^FCHI", "twelvedata": "FCHI", "display": "CAC 40"},   # à vérifier
    {"yahoo": "^GDAXI", "twelvedata": "DAX", "display": "DAX"},      # à vérifier
]

# ⚠️ Symboles Twelve Data non vérifiés en conditions réelles pour le pétrole
COMMODITIES = [
    {"yahoo": "GC=F", "twelvedata": "XAU/USD", "display": "Or"},
    {"yahoo": "SI=F", "twelvedata": "XAG/USD", "display": "Argent"},
    {"yahoo": "CL=F", "twelvedata": "WTI/USD", "display": "Pétrole WTI"},   # à vérifier
    {"yahoo": "BZ=F", "twelvedata": "BRENT/USD", "display": "Pétrole Brent"},  # à vérifier
]
