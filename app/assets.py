"""
V4.2 — Univers d'actifs.

Les identifiants sont séparés par fournisseur lorsque nécessaire.
Le routeur V4.2 choisit ensuite automatiquement la source la plus adaptée.

Objectifs :
- univers large multi-actifs
- séparation claire par type
- compatibilité avec run_and_notify.py
- symboles spécifiques par fournisseur
"""

# ============================================================
# CRYPTO
# Binance Spot — 24/7
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

# Compatibilité avec run_and_notify.py
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
# Yahoo / Twelve Data / Finnhub lorsque disponible
# ============================================================

COMMODITIES = [
    "GC=F",       # Gold
    "SI=F",       # Silver
    "CL=F",       # WTI Crude Oil
    "BZ=F",       # Brent Crude Oil
    "NG=F",       # Natural Gas
    "HG=F",       # Copper
]


# ============================================================
# MATIÈRES PREMIÈRES YAHOO UNIQUEMENT
# ============================================================

YAHOO_COMMODITIES = [
    "ZC=F",       # Corn
    "ZS=F",       # Soybeans
    "ZW=F",       # Wheat
    "KC=F",       # Coffee
    "SB=F",       # Sugar
]


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
        + YAHOO_COMMODITIES
    )


# ============================================================
# NOMBRE TOTAL D'ACTIFS
# ============================================================

def asset_count():
    """
    Nombre total d'actifs disponibles.
    """
    return len(all_assets())


# ============================================================
# COMPTAGES PAR CATÉGORIE
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
    return len(COMMODITIES) + len(YAHOO_COMMODITIES)


# ============================================================
# TYPE D'ACTIF
# ============================================================

def get_asset_type(symbol):
    """
    Retourne le type d'actif correspondant au symbole.
    """

    if symbol in CRYPTO:
        return "crypto"

    if symbol in FOREX:
        return "forex"

    if symbol in STOCKS:
        return "stock"

    if symbol in INDICES:
        return "index"

    if symbol in COMMODITIES or symbol in YAHOO_COMMODITIES:
        return "commodity"

    return "unknown"


# ============================================================
# NOM D'AFFICHAGE
# ============================================================

DISPLAY_NAMES = {
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
# SYMBOL GENERIQUE
# ============================================================

def get_symbol(symbol):
    """
    Retourne le symbole canonique utilisé par le scanner.
    """
    return symbol


# ============================================================
# SYMBOL FOURNISSEUR
# ============================================================

def get_provider_symbol(symbol, provider):
    """
    Retourne le symbole adapté au fournisseur demandé.

    provider :
        - binance
        - finnhub
        - yahoo
        - twelvedata
    """

    provider = str(provider).lower().strip()

    # --------------------------------------------------------
    # CRYPTO
    # --------------------------------------------------------

    if symbol in CRYPTO:
        return symbol

    # --------------------------------------------------------
    # FOREX
    # --------------------------------------------------------

    if symbol in FOREX_SYMBOLS:
        mapping = FOREX_SYMBOLS[symbol]

        if provider in mapping:
            return mapping[provider]

        return symbol

    # --------------------------------------------------------
    # COMMODITIES
    # --------------------------------------------------------

    if symbol in COMMODITY_SYMBOLS:
        mapping = COMMODITY_SYMBOLS[symbol]

        if provider in mapping:
            return mapping[provider]

        return symbol

    # --------------------------------------------------------
    # ACTIONS / INDICES
    # --------------------------------------------------------

    return symbol


# ============================================================
# MAPPING POUR LE ROUTEUR
# ============================================================

def get_symbol_map(symbol):
    """
    Retourne l'ensemble des symboles disponibles pour un actif.
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
    "yahoo_commodities": YAHOO_COMMODITIES,
}


# ============================================================
# MÉTADONNÉES
# ============================================================

ASSET_COUNTS = {
    "crypto": len(CRYPTO),
    "forex": len(FOREX),
    "stocks": len(STOCKS),
    "actions": len(ACTIONS),
    "indices": len(INDICES),
    "commodities": len(COMMODITIES),
    "yahoo_commodities": len(YAHOO_COMMODITIES),
    "total": len(all_assets()),
}


# ============================================================
# VALIDATION
# ============================================================

def validate_assets():
    """
    Vérifie que l'univers d'actifs ne contient pas de doublons.
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


# Validation au chargement du module
validate_assets()
