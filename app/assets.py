"""
V4.1 — Listes d'actifs à scanner.

Crypto :
    Binance

Forex :
    Twelve Data

Actions :
    Yahoo + Twelve Data

Indices :
    Yahoo uniquement

Matières premières :
    Or : Yahoo + Twelve Data
    Argent / WTI / Brent : Yahoo uniquement
"""

# ======================================================================
# CRYPTO
# ======================================================================

CRYPTO = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
]


# ======================================================================
# FOREX
# ======================================================================

FOREX = [
    {
        "twelvedata": "EUR/USD",
        "display": "EUR/USD",
    },
    {
        "twelvedata": "GBP/USD",
        "display": "GBP/USD",
    },
    {
        "twelvedata": "USD/JPY",
        "display": "USD/JPY",
    },
    {
        "twelvedata": "AUD/USD",
        "display": "AUD/USD",
    },
    {
        "twelvedata": "USD/CHF",
        "display": "USD/CHF",
    },
]


# ======================================================================
# ACTIONS
# ======================================================================

ACTIONS = [
    {
        "yahoo": "AAPL",
        "twelvedata": "AAPL",
        "display": "Apple",
    },
    {
        "yahoo": "MSFT",
        "twelvedata": "MSFT",
        "display": "Microsoft",
    },
    {
        "yahoo": "GOOGL",
        "twelvedata": "GOOGL",
        "display": "Alphabet",
    },
    {
        "yahoo": "AMZN",
        "twelvedata": "AMZN",
        "display": "Amazon",
    },
    {
        "yahoo": "NVDA",
        "twelvedata": "NVDA",
        "display": "Nvidia",
    },
    {
        "yahoo": "META",
        "twelvedata": "META",
        "display": "Meta",
    },
    {
        "yahoo": "TSLA",
        "twelvedata": "TSLA",
        "display": "Tesla",
    },
    {
        "yahoo": "JPM",
        "twelvedata": "JPM",
        "display": "JPMorgan",
    },
    {
        "yahoo": "V",
        "twelvedata": "V",
        "display": "Visa",
    },
    {
        "yahoo": "UNH",
        "twelvedata": "UNH",
        "display": "UnitedHealth",
    },
    {
        "yahoo": "XOM",
        "twelvedata": "XOM",
        "display": "Exxon",
    },
    {
        "yahoo": "JNJ",
        "twelvedata": "JNJ",
        "display": "Johnson & Johnson",
    },
    {
        "yahoo": "WMT",
        "twelvedata": "WMT",
        "display": "Walmart",
    },
    {
        "yahoo": "PG",
        "twelvedata": "PG",
        "display": "Procter & Gamble",
    },
    {
        "yahoo": "ORCL",
        "twelvedata": "ORCL",
        "display": "Oracle",
    },
    {
        "yahoo": "ADBE",
        "twelvedata": "ADBE",
        "display": "Adobe",
    },
]


# ======================================================================
# INDICES
# ======================================================================

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


# ======================================================================
# COMMODITIES
# ======================================================================

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
