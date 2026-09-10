"""
V4.2 — Univers d'actifs.

Les identifiants sont séparés par fournisseur lorsque nécessaire.
Le routeur V4.2 choisit ensuite la source la plus adaptée.
"""

CRYPTO = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
]

FOREX = [
    {
        "twelvedata": "EUR/USD",
        "finnhub": "OANDA:EUR_USD",
        "yahoo": "EURUSD=X",
        "display": "EUR/USD",
    },
    {
        "twelvedata": "GBP/USD",
        "finnhub": "OANDA:GBP_USD",
        "yahoo": "GBPUSD=X",
        "display": "GBP/USD",
    },
    {
        "twelvedata": "USD/JPY",
        "finnhub": "OANDA:USD_JPY",
        "yahoo": "JPY=X",
        "display": "USD/JPY",
    },
    {
        "twelvedata": "AUD/USD",
        "finnhub": "OANDA:AUD_USD",
        "yahoo": "AUDUSD=X",
        "display": "AUD/USD",
    },
    {
        "twelvedata": "USD/CHF",
        "finnhub": "OANDA:USD_CHF",
        "yahoo": "CHF=X",
        "display": "USD/CHF",
    },
]

ACTIONS = [
    {
        "yahoo": "AAPL",
        "twelvedata": "AAPL",
        "finnhub": "AAPL",
        "display": "Apple",
    },
    {
        "yahoo": "MSFT",
        "twelvedata": "MSFT",
        "finnhub": "MSFT",
        "display": "Microsoft",
    },
    {
        "yahoo": "GOOGL",
        "twelvedata": "GOOGL",
        "finnhub": "GOOGL",
        "display": "Alphabet",
    },
    {
        "yahoo": "AMZN",
        "twelvedata": "AMZN",
        "finnhub": "AMZN",
        "display": "Amazon",
    },
    {
        "yahoo": "NVDA",
        "twelvedata": "NVDA",
        "finnhub": "NVDA",
        "display": "Nvidia",
    },
    {
        "yahoo": "META",
        "twelvedata": "META",
        "finnhub": "META",
        "display": "Meta",
    },
    {
        "yahoo": "TSLA",
        "twelvedata": "TSLA",
        "finnhub": "TSLA",
        "display": "Tesla",
    },
    {
        "yahoo": "JPM",
        "twelvedata": "JPM",
        "finnhub": "JPM",
        "display": "JPMorgan",
    },
    {
        "yahoo": "V",
        "twelvedata": "V",
        "finnhub": "V",
        "display": "Visa",
    },
    {
        "yahoo": "UNH",
        "twelvedata": "UNH",
        "finnhub": "UNH",
        "display": "UnitedHealth",
    },
    {
        "yahoo": "XOM",
        "twelvedata": "XOM",
        "finnhub": "XOM",
        "display": "Exxon",
    },
    {
        "yahoo": "JNJ",
        "twelvedata": "JNJ",
        "finnhub": "JNJ",
        "display": "Johnson & Johnson",
    },
    {
        "yahoo": "WMT",
        "twelvedata": "WMT",
        "finnhub": "WMT",
        "display": "Walmart",
    },
    {
        "yahoo": "PG",
        "twelvedata": "PG",
        "finnhub": "PG",
        "display": "Procter & Gamble",
    },
    {
        "yahoo": "ORCL",
        "twelvedata": "ORCL",
        "finnhub": "ORCL",
        "display": "Oracle",
    },
    {
        "yahoo": "ADBE",
        "twelvedata": "ADBE",
        "finnhub": "ADBE",
        "display": "Adobe",
    },
]

INDICES = [
    {
        "yahoo": "^GSPC",
        "display": "S&P 500",
    },
    {
        "yahoo": "^IXIC",
        "display": "Nasdaq Composite",
    },
    {
        "yahoo": "^DJI",
        "display": "Dow Jones",
    },
    {
        "yahoo": "^FCHI",
        "display": "CAC 40",
    },
    {
        "yahoo": "^GDAXI",
        "display": "DAX",
    },
]

COMMODITIES = [
    {
        "yahoo": "GC=F",
        "twelvedata": "XAU/USD",
        "display": "Or",
    },
]

COMMODITIES_YAHOO_ONLY = [
    {
        "yahoo": "SI=F",
        "display": "Argent",
    },
    {
        "yahoo": "CL=F",
        "display": "Pétrole WTI",
    },
    {
        "yahoo": "BZ=F",
        "display": "Pétrole Brent",
    },
]
