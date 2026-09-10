"""
V4.2 — Univers d'actifs.

Les identifiants sont séparés par fournisseur lorsque nécessaire.
Le DataRouter choisit ensuite automatiquement la source la plus adaptée.
"""

# ============================================================
# CRYPTO
# ============================================================

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
    "TRXUSDT",
    "LTCUSDT",
    "BCHUSDT",
    "ATOMUSDT",
    "UNIUSDT",
    "ETCUSDT",
    "XLMUSDT",
    "NEARUSDT",
    "APTUSDT",
    "FILUSDT",
]


# ============================================================
# FOREX
# ============================================================

FOREX = [
    {
        "name": "EUR/USD",
        "twelve_data": "EUR/USD",
        "finnhub": "OANDA:EUR_USD",
        "yahoo": "EURUSD=X",
    },
    {
        "name": "GBP/USD",
        "twelve_data": "GBP/USD",
        "finnhub": "OANDA:GBP_USD",
        "yahoo": "GBPUSD=X",
    },
    {
        "name": "USD/JPY",
        "twelve_data": "USD/JPY",
        "finnhub": "OANDA:USD_JPY",
        "yahoo": "JPY=X",
    },
    {
        "name": "AUD/USD",
        "twelve_data": "AUD/USD",
        "finnhub": "OANDA:AUD_USD",
        "yahoo": "AUDUSD=X",
    },
    {
        "name": "USD/CHF",
        "twelve_data": "USD/CHF",
        "finnhub": "OANDA:USD_CHF",
        "yahoo": "CHF=X",
    },
    {
        "name": "USD/CAD",
        "twelve_data": "USD/CAD",
        "finnhub": "OANDA:USD_CAD",
        "yahoo": "CAD=X",
    },
    {
        "name": "NZD/USD",
        "twelve_data": "NZD/USD",
        "finnhub": "OANDA:NZD_USD",
        "yahoo": "NZDUSD=X",
    },
    {
        "name": "EUR/GBP",
        "twelve_data": "EUR/GBP",
        "finnhub": "OANDA:EUR_GBP",
        "yahoo": "EURGBP=X",
    },
    {
        "name": "EUR/JPY",
        "twelve_data": "EUR/JPY",
        "finnhub": "OANDA:EUR_JPY",
        "yahoo": "EURJPY=X",
    },
    {
        "name": "GBP/JPY",
        "twelve_data": "GBP/JPY",
        "finnhub": "OANDA:GBP_JPY",
        "yahoo": "GBPJPY=X",
    },
]


# ============================================================
# ACTIONS US
# ============================================================

STOCKS = [
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "NVDA",
    "META",
    "TSLA",
    "AVGO",
    "AMD",
    "QCOM",
    "ORCL",
    "ADBE",
    "CRM",
    "NFLX",
    "JPM",
    "BAC",
    "GS",
    "V",
    "MA",
    "UNH",
    "JNJ",
    "PFE",
    "XOM",
    "CVX",
    "WMT",
    "COST",
    "PG",
    "KO",
    "PEP",
    "HD",
    "DIS",
    "NKE",
    "IBM",
    "INTC",
    "CSCO",
]


# ============================================================
# INDICES
# ============================================================

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


# ============================================================
# MATIÈRES PREMIÈRES — MULTI-SOURCES
# ============================================================

COMMODITIES = [
    {
        "name": "Gold",
        "yahoo": "GC=F",
        "twelve_data": "XAU/USD",
    },
    {
        "name": "Silver",
        "yahoo": "SI=F",
        "twelve_data": "XAG/USD",
    },
    {
        "name": "WTI Crude Oil",
        "yahoo": "CL=F",
        "twelve_data": "WTI/USD",
    },
    {
        "name": "Brent Crude Oil",
        "yahoo": "BZ=F",
        "twelve_data": "BRENT/USD",
    },
    {
        "name": "Natural Gas",
        "yahoo": "NG=F",
        "twelve_data": "NATGAS/USD",
    },
    {
        "name": "Copper",
        "yahoo": "HG=F",
        "twelve_data": "COPPER/USD",
    },
]


# ============================================================
# MATIÈRES PREMIÈRES — YAHOO UNIQUEMENT
# ============================================================

YAHOO_COMMODITIES = [
    "ZC=F",
    "ZS=F",
    "ZW=F",
    "KC=F",
    "SB=F",
]


# ============================================================
# NORMALISATION DES ACTIFS
# ============================================================

def _asset_name(asset):
    """
    Retourne le nom d'affichage d'un actif.
    """

    if isinstance(asset, dict):
        return asset.get("name", "")

    return str(asset)


def _asset_symbol(asset):
    """
    Retourne le symbole principal d'un actif.
    """

    if isinstance(asset, dict):
        return (
            asset.get("yahoo")
            or asset.get("twelve_data")
            or asset.get("finnhub")
            or asset.get("symbol")
            or asset.get("name")
        )

    return str(asset)


# ============================================================
# UNIVERS COMPLET
# ============================================================

def all_assets():
    """
    Retourne les 86 actifs de l'univers V4.2.

    Chaque élément contient :
        - name
        - symbol
        - asset_type
        - provider symbols lorsque disponibles
    """

    assets = []

    # Crypto
    for symbol in CRYPTO:
        assets.append(
            {
                "name": symbol,
                "symbol": symbol,
                "asset_type": "crypto",
                "binance": symbol,
            }
        )

    # Forex
    for item in FOREX:
        assets.append(
            {
                "name": item["name"],
                "symbol": item.get("twelve_data") or item["name"],
                "asset_type": "forex",
                "twelve_data": item.get("twelve_data"),
                "finnhub": item.get("finnhub"),
                "yahoo": item.get("yahoo"),
            }
        )

    # Actions
    for symbol in STOCKS:
        assets.append(
            {
                "name": symbol,
                "symbol": symbol,
                "asset_type": "stock",
                "finnhub": symbol,
                "yahoo": symbol,
                "twelve_data": symbol,
            }
        )

    # Indices
    for symbol in INDICES:
        assets.append(
            {
                "name": symbol,
                "symbol": symbol,
                "asset_type": "index",
                "yahoo": symbol,
                "finnhub": symbol,
                "twelve_data": symbol,
            }
        )

    # Commodities multi-sources
    for item in COMMODITIES:
        assets.append(
            {
                "name": item["name"],
                "symbol": item["yahoo"],
                "asset_type": "commodity",
                "yahoo": item.get("yahoo"),
                "twelve_data": item.get("twelve_data"),
            }
        )

    # Commodities Yahoo uniquement
    for symbol in YAHOO_COMMODITIES:
        assets.append(
            {
                "name": symbol,
                "symbol": symbol,
                "asset_type": "commodity",
                "yahoo": symbol,
            }
        )

    return assets


# ============================================================
# UTILITAIRES
# ============================================================

def asset_count():
    """
    Nombre total d'actifs.
    """

    return len(all_assets())


def get_display_name(asset):
    """
    Retourne le nom d'affichage d'un actif.
    """

    return _asset_name(asset)


def get_symbol(asset):
    """
    Retourne le symbole principal d'un actif.
    """

    return _asset_symbol(asset)


def get_asset_type(asset):
    """
    Détermine le type d'actif.
    """

    if isinstance(asset, dict):
        return asset.get("asset_type")

    symbol = str(asset)

    if symbol in CRYPTO:
        return "crypto"

    if symbol in STOCKS:
        return "stock"

    if symbol in INDICES:
        return "index"

    if symbol in YAHOO_COMMODITIES:
        return "commodity"

    for item in FOREX:
        if symbol in {
            item.get("name"),
            item.get("twelve_data"),
            item.get("finnhub"),
            item.get("yahoo"),
        }:
            return "forex"

    for item in COMMODITIES:
        if symbol in {
            item.get("name"),
            item.get("yahoo"),
            item.get("twelve_data"),
        }:
            return "commodity"

    return "stock"


# ============================================================
# COMPTAGES PAR CATÉGORIE
# ============================================================

def crypto_count():
    return len(CRYPTO)


def forex_count():
    return len(FOREX)


def stock_count():
    return len(STOCKS)


def index_count():
    return len(INDICES)


def commodity_count():
    return len(COMMODITIES) + len(YAHOO_COMMODITIES)


# ============================================================
# INFORMATIONS UNIVERSELLES
# ============================================================

ASSET_COUNTS = {
    "crypto": len(CRYPTO),
    "forex": len(FOREX),
    "stock": len(STOCKS),
    "index": len(INDICES),
    "commodity": len(COMMODITIES) + len(YAHOO_COMMODITIES),
}


TOTAL_ASSETS = sum(ASSET_COUNTS.values())


if TOTAL_ASSETS != 86:
    raise RuntimeError(
        "Erreur dans l'univers V4.2 : "
        f"{TOTAL_ASSETS} actifs détectés au lieu de 86."
    )
