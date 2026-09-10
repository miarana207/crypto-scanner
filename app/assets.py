"""
V4.2 — Univers multi-actifs et mappings fournisseurs.
"""

CRYPTO = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
    "TRXUSDT", "LTCUSDT", "BCHUSDT", "ATOMUSDT", "UNIUSDT",
    "ETCUSDT", "XLMUSDT", "NEARUSDT", "APTUSDT", "FILUSDT",
]

FOREX = [
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CHF",
    "USD/CAD", "NZD/USD", "EUR/GBP", "EUR/JPY", "GBP/JPY",
]

STOCKS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    "AVGO", "AMD", "QCOM", "ORCL", "ADBE", "CRM", "NFLX",
    "JPM", "BAC", "GS", "V", "MA", "UNH", "JNJ", "PFE",
    "XOM", "CVX", "WMT", "COST", "PG", "KO", "PEP", "HD",
    "DIS", "NKE", "IBM", "INTC", "CSCO",
]

ACTIONS = STOCKS

INDICES = [
    "^GSPC",
    "^IXIC",
    "^DJI",
    "^RUT",
    "^FCHI",
    "^GDAXI",
    "^FTSE",
    "^N225",
    "^HSI",
    "^STOXX50E",
]

COMMODITIES = [
    "GC=F",
    "SI=F",
    "CL=F",
    "BZ=F",
    "NG=F",
    "HG=F",
]

COMMODITIES_YAHOO_ONLY = [
    "ZC=F",
    "ZS=F",
    "ZW=F",
    "KC=F",
    "SB=F",
]

YAHOO_COMMODITIES = COMMODITIES_YAHOO_ONLY


FOREX_SYMBOLS = {
    "EUR/USD": {
        "finnhub": "OANDA:EUR_USD",
        "yahoo": "EURUSD=X",
        "twelvedata": "EUR/USD",
        "twelve_data": "EUR/USD",
    },
    "GBP/USD": {
        "finnhub": "OANDA:GBP_USD",
        "yahoo": "GBPUSD=X",
        "twelvedata": "GBP/USD",
        "twelve_data": "GBP/USD",
    },
    "USD/JPY": {
        "finnhub": "OANDA:USD_JPY",
        "yahoo": "JPY=X",
        "twelvedata": "USD/JPY",
        "twelve_data": "USD/JPY",
    },
    "AUD/USD": {
        "finnhub": "OANDA:AUD_USD",
        "yahoo": "AUDUSD=X",
        "twelvedata": "AUD/USD",
        "twelve_data": "AUD/USD",
    },
    "USD/CHF": {
        "finnhub": "OANDA:USD_CHF",
        "yahoo": "CHF=X",
        "twelvedata": "USD/CHF",
        "twelve_data": "USD/CHF",
    },
    "USD/CAD": {
        "finnhub": "OANDA:USD_CAD",
        "yahoo": "CAD=X",
        "twelvedata": "USD/CAD",
        "twelve_data": "USD/CAD",
    },
    "NZD/USD": {
        "finnhub": "OANDA:NZD_USD",
        "yahoo": "NZDUSD=X",
        "twelvedata": "NZD/USD",
        "twelve_data": "NZD/USD",
    },
    "EUR/GBP": {
        "finnhub": "OANDA:EUR_GBP",
        "yahoo": "EURGBP=X",
        "twelvedata": "EUR/GBP",
        "twelve_data": "EUR/GBP",
    },
    "EUR/JPY": {
        "finnhub": "OANDA:EUR_JPY",
        "yahoo": "EURJPY=X",
        "twelvedata": "EUR/JPY",
        "twelve_data": "EUR/JPY",
    },
    "GBP/JPY": {
        "finnhub": "OANDA:GBP_JPY",
        "yahoo": "GBPJPY=X",
        "twelvedata": "GBP/JPY",
        "twelve_data": "GBP/JPY",
    },
}


COMMODITY_SYMBOLS = {
    "GC=F": {
        "yahoo": "GC=F",
        "twelvedata": "XAU/USD",
        "twelve_data": "XAU/USD",
    },
    "SI=F": {
        "yahoo": "SI=F",
        "twelvedata": "XAG/USD",
        "twelve_data": "XAG/USD",
    },
    "CL=F": {
        "yahoo": "CL=F",
        "twelvedata": "WTI/USD",
        "twelve_data": "WTI/USD",
    },
    "BZ=F": {
        "yahoo": "BZ=F",
        "twelvedata": "BRENT/USD",
        "twelve_data": "BRENT/USD",
    },
    "NG=F": {
        "yahoo": "NG=F",
    },
    "HG=F": {
        "yahoo": "HG=F",
    },
    "ZC=F": {
        "yahoo": "ZC=F",
    },
    "ZS=F": {
        "yahoo": "ZS=F",
    },
    "ZW=F": {
        "yahoo": "ZW=F",
    },
    "KC=F": {
        "yahoo": "KC=F",
    },
    "SB=F": {
        "yahoo": "SB=F",
    },
}


INDEX_SYMBOLS = {
    "^GSPC": {
        "yahoo": "^GSPC",
        "finnhub": "SP:SPX",
    },
    "^IXIC": {
        "yahoo": "^IXIC",
    },
    "^DJI": {
        "yahoo": "^DJI",
    },
    "^RUT": {
        "yahoo": "^RUT",
    },
    "^FCHI": {
        "yahoo": "^FCHI",
    },
    "^GDAXI": {
        "yahoo": "^GDAXI",
    },
    "^FTSE": {
        "yahoo": "^FTSE",
    },
    "^N225": {
        "yahoo": "^N225",
    },
    "^HSI": {
        "yahoo": "^HSI",
    },
    "^STOXX50E": {
        "yahoo": "^STOXX50E",
    },
}


def all_assets():
    return (
        CRYPTO
        + FOREX
        + STOCKS
        + INDICES
        + COMMODITIES
        + COMMODITIES_YAHOO_ONLY
    )


def asset_count():
    return len(all_assets())


ASSET_COUNTS = {
    "crypto": len(CRYPTO),
    "forex": len(FOREX),
    "stock": len(STOCKS),
    "index": len(INDICES),
    "commodity": (
        len(COMMODITIES)
        + len(COMMODITIES_YAHOO_ONLY)
    ),
}


def get_asset_type(symbol):

    if symbol in CRYPTO:
        return "crypto"

    if symbol in FOREX:
        return "forex"

    if symbol in STOCKS:
        return "stock"

    if symbol in INDICES:
        return "index"

    if (
        symbol in COMMODITIES
        or symbol in COMMODITIES_YAHOO_ONLY
    ):
        return "commodity"

    return "unknown"


DISPLAY_NAMES = {
    "^GSPC": "S&P 500",
    "^IXIC": "NASDAQ Composite",
    "^DJI": "Dow Jones",
    "^RUT": "Russell 2000",
    "^FCHI": "CAC 40",
    "^GDAXI": "DAX",
    "^FTSE": "FTSE 100",
    "^N225": "Nikkei 225",
    "^HSI": "Hang Seng",
    "^STOXX50E": "Euro Stoxx 50",
}


def get_display_name(symbol):
    return DISPLAY_NAMES.get(
        symbol,
        symbol,
    )


def get_symbol(symbol):
    return symbol


def get_provider_symbol(
    symbol,
    provider,
):
    mapping = get_symbol_map(symbol)
    return mapping.get(
        provider,
        symbol,
    )


def get_symbol_map(symbol):

    if symbol in FOREX_SYMBOLS:
        return dict(
            FOREX_SYMBOLS[symbol]
        )

    if symbol in COMMODITY_SYMBOLS:
        return dict(
            COMMODITY_SYMBOLS[symbol]
        )

    if symbol in INDEX_SYMBOLS:
        return dict(
            INDEX_SYMBOLS[symbol]
        )

    return {
        "binance": symbol,
        "finnhub": symbol,
        "yahoo": symbol,
        "twelvedata": symbol,
        "twelve_data": symbol,
    }


ASSET_GROUPS = {
    "crypto": CRYPTO,
    "forex": FOREX,
    "stock": STOCKS,
    "index": INDICES,
    "commodity": (
        COMMODITIES
        + COMMODITIES_YAHOO_ONLY
    ),
}


def validate_assets():

    assets = all_assets()

    duplicates = (
        len(assets)
        != len(set(assets))
    )

    return {
        "valid": not duplicates,
        "count": len(assets),
        "duplicates": duplicates,
    }
