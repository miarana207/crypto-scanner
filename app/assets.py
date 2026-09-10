"""
V4.2 — Univers d'actifs.

Univers multi-actifs utilisé par le routeur intelligent V4.2.

Objectifs :
- large couverture multi-actifs
- séparation par catégorie
- symboles compatibles avec plusieurs fournisseurs
- compatibilité avec run_and_notify.py
- compatibilité avec DataRouter V4.2
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
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "USD/CHF",
    "USD/CAD",
    "NZD/USD",
    "EUR/GBP",
    "EUR/JPY",
    "GBP/JPY",
]


# ============================================================
# ACTIONS
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

# Alias utilisé par run_and_notify.py
ACTIONS = STOCKS


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
# MATIÈRES PREMIÈRES MULTI-SOURCES
#
# Yahoo + Twelve Data lorsque disponible
# ============================================================

COMMODITIES = [
    "GC=F",       # Gold
    "SI=F",       # Silver
    "CL=F",       # WTI
    "BZ=F",       # Brent
    "NG=F",       # Natural Gas
    "HG=F",       # Copper
]


# ============================================================
# MATIÈRES PREMIÈRES YAHOO UNIQUEMENT
# ============================================================

COMMODITIES_YAHOO_ONLY = [
    "ZC=F",       # Corn
    "ZS=F",       # Soybeans
    "ZW=F",       # Wheat
    "KC=F",       # Coffee
    "SB=F",       # Sugar
]

# Alias de compatibilité
YAHOO_COMMODITIES = COMMODITIES_YAHOO_ONLY


# ============================================================
# MAPPING FOREX PAR FOURNISSEUR
# ============================================================

FOREX_SYMBOLS = {
    "EUR/USD": {
        "twelvedata": "EUR/USD",
        "finnhub": "OANDA:EUR_USD",
        "yahoo": "EURUSD=X",
    },

    "GBP/USD": {
        "twelvedata": "GBP/USD",
        "finnhub": "OANDA:GBP_USD",
        "yahoo": "GBPUSD=X",
    },

    "USD/JPY": {
        "twelvedata": "USD/JPY",
        "finnhub": "OANDA:USD_JPY",
        "yahoo": "JPY=X",
    },

    "AUD/USD": {
        "twelvedata": "AUD/USD",
        "finnhub": "OANDA:AUD_USD",
        "yahoo": "AUDUSD=X",
    },

    "USD/CHF": {
        "twelvedata": "USD/CHF",
        "finnhub": "OANDA:USD_CHF",
        "yahoo": "CHF=X",
    },

    "USD/CAD": {
        "twelvedata": "USD/CAD",
        "finnhub": "OANDA:USD_CAD",
        "yahoo": "CAD=X",
    },

    "NZD/USD": {
        "twelvedata": "NZD/USD",
        "finnhub": "OANDA:NZD_USD",
        "yahoo": "NZDUSD=X",
    },

    "EUR/GBP": {
        "twelvedata": "EUR/GBP",
        "finnhub": "OANDA:EUR_GBP",
        "yahoo": "EURGBP=X",
    },

    "EUR/JPY": {
        "twelvedata": "EUR/JPY",
        "finnhub": "OANDA:EUR_JPY",
        "yahoo": "EURJPY=X",
    },

    "GBP/JPY": {
        "twelvedata": "GBP/JPY",
        "finnhub": "OANDA:GBP_JPY",
        "yahoo": "GBPJPY=X",
    },
}


# ============================================================
# MAPPING MATIÈRES PREMIÈRES
# ============================================================

COMMODITY_SYMBOLS = {
    "GC=F": {
        "yahoo": "GC=F",
        "twelvedata": "XAU/USD",
    },

    "SI=F": {
        "yahoo": "SI=F",
        "twelvedata": "XAG/USD",
    },

    "CL=F": {
        "yahoo": "CL=F",
        "twelvedata": "WTI/USD",
    },

    "BZ=F": {
        "yahoo": "BZ=F",
        "twelvedata": "BRENT/USD",
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


# ============================================================
# UNIVERS COMPLET
# ============================================================

def all_assets():
    """
    Retourne tous les actifs du scanner V4.2.
    """

    return (
        CRYPTO
        + FOREX
        + STOCKS
        + INDICES
        + COMMODITIES
        + COMMODITIES_YAHOO_ONLY
    )


# ============================================================
# NOMBRE TOTAL D'ACTIFS
# ============================================================

def asset_count():
    """
    Nombre total d'actifs.
    """

    return len(all_assets())


# ============================================================
# COMPTAGES
# ============================================================

def crypto_count():
    return len(CRYPTO)


def forex_count():
    return len(FOREX)


def stock_count():
    return len(STOCKS)


def action_count():
    return len(ACTIONS)


def index_count():
    return len(INDICES)


def commodity_count():
    return len(COMMODITIES) + len(COMMODITIES_YAHOO_ONLY)


# ============================================================
# TYPE D'ACTIF
# ============================================================

def get_asset_type(symbol):
    """
    Retourne le type d'actif.
    """

    if symbol in CRYPTO:
        return "crypto"

    if symbol in FOREX:
        return "forex"

    if symbol in STOCKS:
        return "stock"

    if symbol in INDICES:
        return "index"

    if symbol in COMMODITIES:
        return "commodity"

    if symbol in COMMODITIES_YAHOO_ONLY:
        return "commodity"

    return "unknown"


# ============================================================
# NOMS D'AFFICHAGE
# ============================================================

DISPLAY_NAMES = {
    # Crypto
    "BTCUSDT": "Bitcoin",
    "ETHUSDT": "Ethereum",
    "SOLUSDT": "Solana",
    "BNBUSDT": "BNB",
    "XRPUSDT": "XRP",
    "ADAUSDT": "Cardano",
    "DOGEUSDT": "Dogecoin",
    "AVAXUSDT": "Avalanche",
    "LINKUSDT": "Chainlink",
    "DOTUSDT": "Polkadot",
    "TRXUSDT": "TRON",
    "LTCUSDT": "Litecoin",
    "BCHUSDT": "Bitcoin Cash",
    "ATOMUSDT": "Cosmos",
    "UNIUSDT": "Uniswap",
    "ETCUSDT": "Ethereum Classic",
    "XLMUSDT": "Stellar",
    "NEARUSDT": "NEAR Protocol",
    "APTUSDT": "Aptos",
    "FILUSDT": "Filecoin",

    # Forex
    "EUR/USD": "EUR/USD",
    "GBP/USD": "GBP/USD",
    "USD/JPY": "USD/JPY",
    "AUD/USD": "AUD/USD",
    "USD/CHF": "USD/CHF",
    "USD/CAD": "USD/CAD",
    "NZD/USD": "NZD/USD",
    "EUR/GBP": "EUR/GBP",
    "EUR/JPY": "EUR/JPY",
    "GBP/JPY": "GBP/JPY",

    # Indices
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq Composite",
    "^DJI": "Dow Jones",
    "^RUT": "Russell 2000",
    "^FCHI": "CAC 40",
    "^GDAXI": "DAX",
    "^FTSE": "FTSE 100",
    "^N225": "Nikkei 225",
    "^HSI": "Hang Seng",
    "^STOXX50E": "Euro Stoxx 50",

    # Commodities
    "GC=F": "Gold",
    "SI=F": "Silver",
    "CL=F": "WTI Crude Oil",
    "BZ=F": "Brent Crude Oil",
    "NG=F": "Natural Gas",
    "HG=F": "Copper",
    "ZC=F": "Corn",
    "ZS=F": "Soybeans",
    "ZW=F": "Wheat",
    "KC=F": "Coffee",
    "SB=F": "Sugar",
}


def get_display_name(symbol):
    """
    Retourne le nom lisible de l'actif.
    """

    return DISPLAY_NAMES.get(symbol, symbol)


# ============================================================
# SYMBOLE CANONIQUE
# ============================================================

def get_symbol(symbol):
    """
    Retourne le symbole canonique.
    """

    return symbol


# ============================================================
# SYMBOLE POUR UN FOURNISSEUR
# ============================================================

def get_provider_symbol(symbol, provider):
    """
    Retourne le symbole adapté au fournisseur.
    """

    provider = str(provider).lower().strip()

    # Forex
    if symbol in FOREX_SYMBOLS:
        mapping = FOREX_SYMBOLS[symbol]

        if provider in mapping:
            return mapping[provider]

        return symbol

    # Matières premières
    if symbol in COMMODITY_SYMBOLS:
        mapping = COMMODITY_SYMBOLS[symbol]

        if provider in mapping:
            return mapping[provider]

        return symbol

    # Actions, indices, crypto
    return symbol


# ============================================================
# MAPPING COMPLET POUR LE ROUTEUR
# ============================================================

def get_symbol_map(symbol):
    """
    Retourne les symboles disponibles par fournisseur.
    """

    asset_type = get_asset_type(symbol)

    if asset_type == "forex":
        return FOREX_SYMBOLS.get(symbol, {})

    if asset_type == "commodity":
        return COMMODITY_SYMBOLS.get(symbol, {})

    return {
        "binance": symbol,
        "finnhub": symbol,
        "yahoo": symbol,
        "twelvedata": symbol,
    }


# ============================================================
# GROUPES D'ACTIFS
# ============================================================

ASSET_GROUPS = {
    "crypto": CRYPTO,
    "forex": FOREX,
    "stocks": STOCKS,
    "actions": ACTIONS,
    "indices": INDICES,
    "commodities": COMMODITIES,
    "commodities_yahoo_only": COMMODITIES_YAHOO_ONLY,
}


# ============================================================
# COMPTAGES STATIQUES
# ============================================================

ASSET_COUNTS = {
    "crypto": len(CRYPTO),
    "forex": len(FOREX),
    "stocks": len(STOCKS),
    "actions": len(ACTIONS),
    "indices": len(INDICES),
    "commodities": len(COMMODITIES),
    "commodities_yahoo_only": len(COMMODITIES_YAHOO_ONLY),
    "total": len(all_assets()),
}


# ============================================================
# VALIDATION
# ============================================================

def validate_assets():
    """
    Vérifie l'absence de doublons dans l'univers.
    """

    assets = all_assets()

    duplicates = {
        symbol
        for symbol in assets
        if assets.count(symbol) > 1
    }

    if duplicates:
        raise ValueError(
            "Doublons détectés dans l'univers d'actifs : "
            + ", ".join(sorted(duplicates))
        )

    return True


# ============================================================
# VALIDATION AU CHARGEMENT
# ============================================================

validate_assets()
