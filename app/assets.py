"""
V4.2 — Univers multi-actifs.

Univers :
- 20 cryptos
- 10 Forex
- 35 actions
- 10 indices
- 6 commodities multi-sources
- 5 commodities Yahoo-only

Total : 86 actifs

Les mappings fournisseurs sont explicites.

Principe important V4.2 :
si un fournisseur n'a pas de mapping pour un actif,
le DataRouter doit ignorer ce fournisseur au lieu
d'envoyer aveuglément le symbole canonique.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import math
import os
import time

import requests


# ============================================================
# CRYPTO
# ============================================================

CRYPTO: List[str] = []

# Ensemble dynamique rempli par Binance Universe Builder.
_DYNAMIC_CRYPTO_SYMBOLS: set[str] = set()
_DYNAMIC_CRYPTO_METADATA: Dict[str, Dict[str, Any]] = {}


# ============================================================
# BINANCE UNIVERSE BUILDER
# ============================================================

# Binance recommande data-api.binance.vision pour les endpoints de
# données de marché publiques. Le workflow V4 utilise déjà cette URL via
# BINANCE_DATA_URL ; on la réutilise donc ici pour éviter de contourner
# l'endpoint public de market data.
BINANCE_UNIVERSE_URL = os.getenv(
    "BINANCE_UNIVERSE_URL",
    os.getenv(
        "BINANCE_DATA_URL",
        "https://data-api.binance.vision",
    ),
).rstrip("/")

# Fallbacks utilisés uniquement si l'endpoint principal échoue.
# Ils ne modifient aucun paramètre de stratégie.
BINANCE_UNIVERSE_FALLBACK_URLS = [
    x.strip().rstrip("/")
    for x in os.getenv(
        "BINANCE_UNIVERSE_FALLBACK_URLS",
        "https://api.binance.com,https://api-gcp.binance.com,https://api1.binance.com,https://api2.binance.com,https://api3.binance.com,https://api4.binance.com",
    ).split(",")
    if x.strip()
]

BINANCE_UNIVERSE_TIMEOUT = int(
    os.getenv("BINANCE_UNIVERSE_TIMEOUT", "20")
)

# Ces paramètres concernent UNIQUEMENT la construction dynamique
# de l'univers crypto. Ils ne modifient aucun paramètre du moteur
# technique ou des autres catégories d'actifs.
BINANCE_MIN_QUOTE_VOLUME_24H = float(
    os.getenv("BINANCE_MIN_QUOTE_VOLUME_24H", "5000000")
)
BINANCE_MAX_SPREAD_PCT = float(
    os.getenv("BINANCE_MAX_SPREAD_PCT", "0.30")
)
BINANCE_MIN_TRADES_24H = int(
    os.getenv("BINANCE_MIN_TRADES_24H", "500")
)
BINANCE_MAX_UNIVERSE = int(
    os.getenv("BINANCE_MAX_UNIVERSE", "100")
)
BINANCE_ALLOWED_QUOTE_ASSETS = {
    x.strip().upper()
    for x in os.getenv(
        "BINANCE_ALLOWED_QUOTE_ASSETS",
        "USDT,USDC,FDUSD",
    ).split(",")
    if x.strip()
}

# Actifs qui ne doivent pas être considérés comme des cryptomonnaies
# directionnelles. Ils peuvent être extrêmement liquides sur Binance,
# mais leur sélection fausserait le scanner : stablecoins, devises fiat
# et certains actifs synthétiques/tokenisés.
BINANCE_EXCLUDED_BASE_ASSETS = {
    x.strip().upper()
    for x in os.getenv(
        "BINANCE_EXCLUDED_BASE_ASSETS",
        "USDT,USDC,FDUSD,USD1,RLUSD,EUR,GBP,TRY,BRL,UAH,PLN,ZAR,ARS,MXN,NGN,RON,JPY,AUD",
    ).replace("\n", "").split(",")
    if x.strip()
}

# Préférence de cotation pour éviter de scanner plusieurs fois le même
# actif sous USDT/USDC/FDUSD. USDT est le marché canonique par défaut.
BINANCE_QUOTE_PRIORITY = [
    x.strip().upper()
    for x in os.getenv(
        "BINANCE_QUOTE_PRIORITY",
        "USDT,USDC,FDUSD",
    ).split(",")
    if x.strip()
]


def _binance_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if not math.isfinite(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def _binance_age_days(onboard_date: Any) -> float:
    if onboard_date in (None, "", 0):
        return 3650.0
    try:
        age = (time.time() * 1000.0 - float(onboard_date)) / 86400000.0
        return max(0.0, age)
    except (TypeError, ValueError):
        return 3650.0


def _binance_get_json(
    session: requests.Session,
    path: str,
) -> tuple[Any, str]:
    """
    Récupère un endpoint Binance public avec bascule automatique.

    L'ordre est :
        1. BINANCE_UNIVERSE_URL / BINANCE_DATA_URL
        2. endpoints publics Binance de secours

    On essaie chaque endpoint une seule fois par ressource afin de ne pas
    multiplier inutilement les appels API.
    """
    urls: List[str] = []
    for base in [BINANCE_UNIVERSE_URL, *BINANCE_UNIVERSE_FALLBACK_URLS]:
        base = str(base).strip().rstrip("/")
        if not base or base in urls:
            continue
        urls.append(base)

    errors: List[str] = []

    for base in urls:
        url = f"{base}{path}"
        try:
            response = session.get(
                url,
                timeout=BINANCE_UNIVERSE_TIMEOUT,
            )
            response.raise_for_status()
            return response.json(), base
        except requests.RequestException as exc:
            status = getattr(exc.response, "status_code", None)
            if status is not None:
                errors.append(f"{base} -> HTTP {status}")
            else:
                errors.append(f"{base} -> {exc}")
        except ValueError as exc:
            errors.append(f"{base} -> JSON invalide: {exc}")

    raise RuntimeError(
        f"Binance: aucun endpoint disponible pour {path}. "
        + " | ".join(errors)
    )


def build_binance_universe(
    max_symbols: Optional[int] = None,
) -> List[str]:
    """
    Construit l'univers Spot Binance à chaque exécution.

    Pipeline :
        1. /api/v3/exchangeInfo -> tous les symboles connus
        2. status == TRADING + Spot autorisé
        3. /api/v3/ticker/24hr -> volume/liquidité/spread/volatilité
        4. filtre de liquidité
        5. classement dynamique
        6. conservation des meilleurs marchés

    Les critères liés aux bougies/historique sont ensuite validés par
    le DataRouter/scanner lorsqu'il récupère réellement les candles.
    """
    global CRYPTO, _DYNAMIC_CRYPTO_SYMBOLS, _DYNAMIC_CRYPTO_METADATA

    limit = max(1, int(max_symbols or BINANCE_MAX_UNIVERSE))
    session = requests.Session()
    session.headers.update({
        "User-Agent": "V4.2-Binance-Universe-Builder/1.1",
        "Accept": "application/json",
    })

    print("[Binance Universe] exchangeInfo...")
    exchange, exchange_base = _binance_get_json(
        session,
        "/api/v3/exchangeInfo",
    )

    if not isinstance(exchange, dict):
        raise RuntimeError("Binance exchangeInfo: réponse JSON invalide")

    symbols = exchange.get("symbols", [])
    if not isinstance(symbols, list):
        raise RuntimeError("Binance exchangeInfo: format symbols invalide")

    # Découverte complète AVANT filtrage.
    candidates: Dict[str, Dict[str, Any]] = {}
    discovered = 0
    trading = 0

    for item in symbols:
        if not isinstance(item, dict):
            continue

        discovered += 1
        symbol = str(item.get("symbol", "")).upper().strip()
        if not symbol:
            continue

        if str(item.get("status", "")).upper() != "TRADING":
            continue

        trading += 1

        spot_allowed = item.get("isSpotTradingAllowed")
        permissions = item.get("permissions", [])
        permission_ok = isinstance(permissions, list) and (
            "SPOT" in {str(x).upper() for x in permissions}
        )

        # Si Binance fournit explicitement le champ, il est prioritaire.
        if spot_allowed is False and not permission_ok:
            continue

        quote_asset = str(item.get("quoteAsset", "")).upper()
        base_asset = str(item.get("baseAsset", "")).upper()

        if quote_asset not in BINANCE_ALLOWED_QUOTE_ASSETS:
            continue

        # Ne pas confondre les marchés de stablecoins/fiat avec des
        # cryptos directionnelles. Exemple : USDCUSDT, USD1USDT,
        # EURUSDT doivent rester hors de l'univers de trading crypto.
        if base_asset in BINANCE_EXCLUDED_BASE_ASSETS:
            continue

        candidates[symbol] = item

    print(
        f"[Binance Universe] endpoint={exchange_base} | "
        f"connus={discovered} | TRADING={trading} | "
        f"candidats Spot/liquides={len(candidates)}"
    )

    print("[Binance Universe] ticker 24h...")
    tickers, ticker_base = _binance_get_json(
        session,
        "/api/v3/ticker/24hr",
    )

    if not isinstance(tickers, list):
        raise RuntimeError("Binance ticker/24hr: format invalide")

    ticker_by_symbol = {
        str(item.get("symbol", "")).upper(): item
        for item in tickers
        if isinstance(item, dict)
    }

    ranked: List[Dict[str, Any]] = []

    for symbol, info in candidates.items():
        ticker = ticker_by_symbol.get(symbol)
        if not ticker:
            continue

        quote_volume = _binance_float(ticker.get("quoteVolume"))
        count = int(_binance_float(ticker.get("count")))
        last_price = _binance_float(ticker.get("lastPrice"))
        bid = _binance_float(ticker.get("bidPrice"))
        ask = _binance_float(ticker.get("askPrice"))

        if quote_volume < BINANCE_MIN_QUOTE_VOLUME_24H:
            continue
        if count < BINANCE_MIN_TRADES_24H:
            continue
        if last_price <= 0 or bid <= 0 or ask <= 0 or ask < bid:
            continue

        mid = (bid + ask) / 2.0
        spread_pct = ((ask - bid) / mid) * 100.0 if mid > 0 else 999.0
        if spread_pct > BINANCE_MAX_SPREAD_PCT:
            continue

        high = _binance_float(ticker.get("highPrice"), last_price)
        low = _binance_float(ticker.get("lowPrice"), last_price)
        volatility_pct = ((high - low) / last_price) * 100.0 if last_price > 0 else 0.0

        age_days = _binance_age_days(info.get("onboardDate"))
        stability = min(1.0, age_days / 365.0)
        liquidity = min(1.0, quote_volume / max(BINANCE_MIN_QUOTE_VOLUME_24H * 20.0, 1.0))
        trade_liquidity = min(1.0, count / 100000.0)
        spread_quality = max(0.0, 1.0 - spread_pct / max(BINANCE_MAX_SPREAD_PCT, 0.0001))

        ranked.append({
            "symbol": symbol,
            "quote_asset": info.get("quoteAsset"),
            "base_asset": info.get("baseAsset"),
            "quote_volume_24h": quote_volume,
            "trades_24h": count,
            "bid": bid,
            "ask": ask,
            "spread_pct": spread_pct,
            "volatility_pct": volatility_pct,
            "stability": stability,
            "onboard_date": info.get("onboardDate"),
            "liquidity": liquidity,
            "trade_liquidity": trade_liquidity,
            "spread_quality": spread_quality,
            "status": info.get("status"),
        })

    # Classement brut : le volume reste le facteur dominant ; les autres
    # critères départagent les marchés de volume comparable.
    ranked.sort(
        key=lambda x: (
            x["quote_volume_24h"],
            x["liquidity"],
            x["trade_liquidity"],
            x["spread_quality"],
            x["stability"],
        ),
        reverse=True,
    )

    # Une seule paire par crypto de base. Sinon BTCUSDT + BTCUSDC + BTCFDUSD
    # consommeraient trois positions de l'univers pour exactement le même
    # actif économique. La priorité de cotation choisit USDT par défaut,
    # puis USDC, puis FDUSD ; le volume reste le critère secondaire.
    quote_rank = {quote: i for i, quote in enumerate(BINANCE_QUOTE_PRIORITY)}
    best_by_base: Dict[str, Dict[str, Any]] = {}

    for item in ranked:
        base = str(item.get("base_asset", "")).upper()
        current = best_by_base.get(base)
        if current is None:
            best_by_base[base] = item
            continue

        current_quote_rank = quote_rank.get(
            str(current.get("quote_asset", "")).upper(),
            999,
        )
        new_quote_rank = quote_rank.get(
            str(item.get("quote_asset", "")).upper(),
            999,
        )

        if (new_quote_rank < current_quote_rank) or (
            new_quote_rank == current_quote_rank
            and item["quote_volume_24h"] > current["quote_volume_24h"]
        ):
            best_by_base[base] = item

    ranked_unique = list(best_by_base.values())
    ranked_unique.sort(
        key=lambda x: (
            x["quote_volume_24h"],
            x["liquidity"],
            x["trade_liquidity"],
            x["spread_quality"],
            x["stability"],
        ),
        reverse=True,
    )

    selected = ranked_unique[:limit]
    selected_symbols = [item["symbol"] for item in selected]

    _DYNAMIC_CRYPTO_SYMBOLS = set(selected_symbols)
    _DYNAMIC_CRYPTO_METADATA = {
        item["symbol"]: item for item in selected
    }
    CRYPTO = list(selected_symbols)

    # IMPORTANT : ne pas effacer les mappings historiques.
    # Les 20 paires connues conservent leurs éventuels fallbacks Yahoo/Twelve Data.
    # Une nouvelle paire Binance reçoit automatiquement son mapping Binance.
    for symbol in selected_symbols:
        existing = CRYPTO_SYMBOLS.get(symbol)
        if existing is None:
            CRYPTO_SYMBOLS[symbol] = {
                "binance": symbol,
            }
        else:
            existing["binance"] = symbol

    # Met à jour la référence du groupe crypto pour les éventuels appelants
    # qui utilisent ASSET_GROUPS après la construction dynamique.
    ASSET_GROUPS["crypto"] = CRYPTO

    print(
        f"[Binance Universe] ticker_endpoint={ticker_base} | "
        f"sélection finale={len(selected_symbols)} "
        f"sur {len(ranked_unique)} actifs admissibles "
        f"({len(ranked)} paires)"
    )

    for rank, item in enumerate(selected[:10], start=1):
        print(
            f"  #{rank:02d} {item['symbol']:<14} "
            f"vol24h={item['quote_volume_24h']:,.0f} "
            f"spread={item['spread_pct']:.3f}% "
            f"volatilité={item['volatility_pct']:.2f}%"
        )

    return selected_symbols


def get_binance_universe_metadata() -> Dict[str, Dict[str, Any]]:
    return dict(_DYNAMIC_CRYPTO_METADATA)



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
# COMMODITIES MULTI-SOURCES
# ============================================================

COMMODITIES = [
    "GC=F",
    "SI=F",
    "CL=F",
    "BZ=F",
    "NG=F",
    "HG=F",
]


# ============================================================
# COMMODITIES YAHOO ONLY
# ============================================================

COMMODITIES_YAHOO_ONLY = [
    "ZC=F",
    "ZS=F",
    "ZW=F",
    "KC=F",
    "SB=F",
]


# ============================================================
# ALIAS HISTORIQUE
# ============================================================

ACTIONS = STOCKS

YAHOO_COMMODITIES = (
    COMMODITIES_YAHOO_ONLY
)


# ============================================================
# MAPPINGS CRYPTO
# ============================================================

CRYPTO_SYMBOLS: Dict[str, Dict[str, Optional[str]]] = {

    "BTCUSDT": {
        "binance": "BTCUSDT",
        "yahoo": "BTC-USD",
        "twelve_data": "BTC/USD",
    },

    "ETHUSDT": {
        "binance": "ETHUSDT",
        "yahoo": "ETH-USD",
        "twelve_data": "ETH/USD",
    },

    "SOLUSDT": {
        "binance": "SOLUSDT",
        "yahoo": "SOL-USD",
        "twelve_data": "SOL/USD",
    },

    "BNBUSDT": {
        "binance": "BNBUSDT",
        "yahoo": "BNB-USD",
        "twelve_data": "BNB/USD",
    },

    "XRPUSDT": {
        "binance": "XRPUSDT",
        "yahoo": "XRP-USD",
        "twelve_data": "XRP/USD",
    },

    "ADAUSDT": {
        "binance": "ADAUSDT",
        "yahoo": "ADA-USD",
        "twelve_data": "ADA/USD",
    },

    "DOGEUSDT": {
        "binance": "DOGEUSDT",
        "yahoo": "DOGE-USD",
        "twelve_data": "DOGE/USD",
    },

    "AVAXUSDT": {
        "binance": "AVAXUSDT",
        "yahoo": "AVAX-USD",
        "twelve_data": "AVAX/USD",
    },

    "LINKUSDT": {
        "binance": "LINKUSDT",
        "yahoo": "LINK-USD",
        "twelve_data": "LINK/USD",
    },

    "DOTUSDT": {
        "binance": "DOTUSDT",
        "yahoo": "DOT-USD",
        "twelve_data": "DOT/USD",
    },

    "TRXUSDT": {
        "binance": "TRXUSDT",
        "yahoo": "TRX-USD",
        "twelve_data": "TRX/USD",
    },

    "LTCUSDT": {
        "binance": "LTCUSDT",
        "yahoo": "LTC-USD",
        "twelve_data": "LTC/USD",
    },

    "BCHUSDT": {
        "binance": "BCHUSDT",
        "yahoo": "BCH-USD",
        "twelve_data": "BCH/USD",
    },

    "ATOMUSDT": {
        "binance": "ATOMUSDT",
        "yahoo": "ATOM-USD",
        "twelve_data": "ATOM/USD",
    },

    "UNIUSDT": {
        "binance": "UNIUSDT",
        "yahoo": "UNI-USD",
        "twelve_data": "UNI/USD",
    },

    "ETCUSDT": {
        "binance": "ETCUSDT",
        "yahoo": "ETC-USD",
        "twelve_data": "ETC/USD",
    },

    "XLMUSDT": {
        "binance": "XLMUSDT",
        "yahoo": "XLM-USD",
        "twelve_data": "XLM/USD",
    },

    "NEARUSDT": {
        "binance": "NEARUSDT",
        "yahoo": "NEAR-USD",
        "twelve_data": "NEAR/USD",
    },

    "APTUSDT": {
        "binance": "APTUSDT",
        "yahoo": "APT-USD",
        "twelve_data": "APT/USD",
    },

    "FILUSDT": {
        "binance": "FILUSDT",
        "yahoo": "FIL-USD",
        "twelve_data": "FIL/USD",
    },
}


# ============================================================
# MAPPINGS FOREX
# ============================================================

FOREX_SYMBOLS: Dict[str, Dict[str, Optional[str]]] = {

    "EUR/USD": {
        "twelve_data": "EUR/USD",
        "finnhub": "OANDA:EUR_USD",
        "yahoo": "EURUSD=X",
    },

    "GBP/USD": {
        "twelve_data": "GBP/USD",
        "finnhub": "OANDA:GBP_USD",
        "yahoo": "GBPUSD=X",
    },

    "USD/JPY": {
        "twelve_data": "USD/JPY",
        "finnhub": "OANDA:USD_JPY",
        "yahoo": "JPY=X",
    },

    "AUD/USD": {
        "twelve_data": "AUD/USD",
        "finnhub": "OANDA:AUD_USD",
        "yahoo": "AUDUSD=X",
    },

    "USD/CHF": {
        "twelve_data": "USD/CHF",
        "finnhub": "OANDA:USD_CHF",
        "yahoo": "CHF=X",
    },

    "USD/CAD": {
        "twelve_data": "USD/CAD",
        "finnhub": "OANDA:USD_CAD",
        "yahoo": "CAD=X",
    },

    "NZD/USD": {
        "twelve_data": "NZD/USD",
        "finnhub": "OANDA:NZD_USD",
        "yahoo": "NZDUSD=X",
    },

    "EUR/GBP": {
        "twelve_data": "EUR/GBP",
        "finnhub": "OANDA:EUR_GBP",
        "yahoo": "EURGBP=X",
    },

    "EUR/JPY": {
        "twelve_data": "EUR/JPY",
        "finnhub": "OANDA:EUR_JPY",
        "yahoo": "EURJPY=X",
    },

    "GBP/JPY": {
        "twelve_data": "GBP/JPY",
        "finnhub": "OANDA:GBP_JPY",
        "yahoo": "GBPJPY=X",
    },
}


# ============================================================
# MAPPINGS ACTIONS
# ============================================================

STOCK_SYMBOLS: Dict[
    str,
    Dict[str, Optional[str]],
] = {}

for _symbol in STOCKS:
    STOCK_SYMBOLS[_symbol] = {
        "finnhub": _symbol,
        "yahoo": _symbol,
        "twelve_data": _symbol,
    }


# ============================================================
# MAPPINGS INDICES
# ============================================================

INDEX_SYMBOLS: Dict[
    str,
    Dict[str, Optional[str]],
] = {

    "^GSPC": {
        "yahoo": "^GSPC",
        "finnhub": "SPX",
        "twelve_data": "SPX",
    },

    "^IXIC": {
        "yahoo": "^IXIC",
        "finnhub": "IXIC",
        "twelve_data": "IXIC",
    },

    "^DJI": {
        "yahoo": "^DJI",
        "finnhub": "DJI",
        "twelve_data": "DJI",
    },

    "^RUT": {
        "yahoo": "^RUT",
        "finnhub": "RUT",
        "twelve_data": "RUT",
    },

    "^FCHI": {
        "yahoo": "^FCHI",
        "finnhub": "CAC40",
        "twelve_data": "CAC",
    },

    "^GDAXI": {
        "yahoo": "^GDAXI",
        "finnhub": "DAX",
        "twelve_data": "DAX",
    },

    "^FTSE": {
        "yahoo": "^FTSE",
        "finnhub": "FTSE",
        "twelve_data": "FTSE",
    },

    "^N225": {
        "yahoo": "^N225",
        "finnhub": "N225",
        "twelve_data": "N225",
    },

    "^HSI": {
        "yahoo": "^HSI",
        "finnhub": "HSI",
        "twelve_data": "HSI",
    },

    "^STOXX50E": {
        "yahoo": "^STOXX50E",
        "finnhub": "STOXX50E",
        "twelve_data": "STOXX",
    },
}


# ============================================================
# MAPPINGS COMMODITIES
# ============================================================

COMMODITY_SYMBOLS: Dict[
    str,
    Dict[str, Optional[str]],
] = {

    "GC=F": {
        "yahoo": "GC=F",
        "twelve_data": "XAU/USD",
    },

    "SI=F": {
        "yahoo": "SI=F",
        "twelve_data": "XAG/USD",
    },

    "CL=F": {
        "yahoo": "CL=F",
        "twelve_data": "WTI/USD",
    },

    "BZ=F": {
        "yahoo": "BZ=F",
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


# ============================================================
# UNIVERS COMPLET
# ============================================================

ASSET_GROUPS: Dict[str, List[str]] = {
    "crypto": CRYPTO,
    "forex": FOREX,
    "stock": STOCKS,
    "index": INDICES,
    "commodity": COMMODITIES,
    "commodity_yahoo": COMMODITIES_YAHOO_ONLY,
}


# ============================================================
# COMPATIBILITÉ ANCIENNES CLÉS
# ============================================================

ASSET_GROUPS["stocks"] = STOCKS
ASSET_GROUPS["indices"] = INDICES
ASSET_GROUPS["commodities"] = COMMODITIES
ASSET_GROUPS["commodities_yahoo"] = (
    COMMODITIES_YAHOO_ONLY
)


# ============================================================
# COMPTAGES
# ============================================================

ASSET_COUNTS = {
    "crypto": len(CRYPTO),
    "forex": len(FOREX),
    "stock": len(STOCKS),
    "index": len(INDICES),
    "commodity": len(COMMODITIES),
    "commodity_yahoo": len(
        COMMODITIES_YAHOO_ONLY
    ),
}


def all_assets() -> List[str]:
    """
    Retourne l'univers complet sans doublons.
    """

    result = []

    for group in [
        CRYPTO,
        FOREX,
        STOCKS,
        INDICES,
        COMMODITIES,
        COMMODITIES_YAHOO_ONLY,
    ]:
        for asset in group:
            if asset not in result:
                result.append(asset)

    return result


def asset_count() -> int:
    return len(
        all_assets()
    )


# ============================================================
# TYPE D'ACTIF
# ============================================================

def get_asset_type(
    symbol: str,
) -> str:

    if symbol in CRYPTO or symbol in _DYNAMIC_CRYPTO_SYMBOLS:
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

    raise ValueError(
        f"Actif inconnu : {symbol}"
    )


# ============================================================
# DISPLAY NAMES
# ============================================================

DISPLAY_NAMES: Dict[str, str] = {

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

    # Actions
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "GOOGL": "Alphabet",
    "AMZN": "Amazon",
    "NVDA": "NVIDIA",
    "META": "Meta Platforms",
    "TSLA": "Tesla",
    "AVGO": "Broadcom",
    "AMD": "AMD",
    "QCOM": "Qualcomm",
    "ORCL": "Oracle",
    "ADBE": "Adobe",
    "CRM": "Salesforce",
    "NFLX": "Netflix",
    "JPM": "JPMorgan Chase",
    "BAC": "Bank of America",
    "GS": "Goldman Sachs",
    "V": "Visa",
    "MA": "Mastercard",
    "UNH": "UnitedHealth",
    "JNJ": "Johnson & Johnson",
    "PFE": "Pfizer",
    "XOM": "Exxon Mobil",
    "CVX": "Chevron",
    "WMT": "Walmart",
    "COST": "Costco",
    "PG": "Procter & Gamble",
    "KO": "Coca-Cola",
    "PEP": "PepsiCo",
    "HD": "Home Depot",
    "DIS": "Disney",
    "NKE": "Nike",
    "IBM": "IBM",
    "INTC": "Intel",
    "CSCO": "Cisco",

    # Indices
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


def get_display_name(
    symbol: str,
) -> str:

    return DISPLAY_NAMES.get(
        symbol,
        symbol,
    )


# ============================================================
# SYMBOLE CANONIQUE
# ============================================================

def get_symbol(
    asset: str,
) -> str:

    return asset


# ============================================================
# SYMBOLE FOURNISSEUR
# ============================================================

def get_provider_symbol(
    asset: str,
    provider: str,
) -> Optional[str]:

    mapping = get_symbol_map(
        asset
    )

    return mapping.get(
        provider
    )


# ============================================================
# MAPPING FOURNISSEUR
# ============================================================

def get_symbol_map(
    asset: str,
) -> Dict[str, Optional[str]]:
    """
    Retourne le mapping fournisseur complet.

    Les clés absentes sont volontairement absentes :
    le DataRouter doit alors ignorer ce fournisseur.

    On ajoute aussi "symbol" pour compatibilité avec
    certaines parties du code V4/V4.1.
    """

    if asset in CRYPTO or asset in _DYNAMIC_CRYPTO_SYMBOLS:
        mapping = dict(
            CRYPTO_SYMBOLS.get(
                asset,
                {"binance": asset},
            )
        )

    elif asset in FOREX:
        mapping = dict(
            FOREX_SYMBOLS.get(
                asset,
                {},
            )
        )

    elif asset in STOCKS:
        mapping = dict(
            STOCK_SYMBOLS.get(
                asset,
                {},
            )
        )

    elif asset in INDICES:
        mapping = dict(
            INDEX_SYMBOLS.get(
                asset,
                {},
            )
        )

    elif (
        asset in COMMODITIES
        or asset in COMMODITIES_YAHOO_ONLY
    ):
        mapping = dict(
            COMMODITY_SYMBOLS.get(
                asset,
                {},
            )
        )

    else:
        raise ValueError(
            f"Actif inconnu : {asset}"
        )

    mapping["symbol"] = asset

    return mapping


# ============================================================
# VALIDATION
# ============================================================

def validate_assets() -> Dict[str, object]:
    """Vérifie uniquement les univers statiques au chargement.

    L'univers crypto est volontairement dynamique et sera validé par
    build_binance_universe() au début de chaque scan.
    """
    static_assets = []
    for group in [FOREX, STOCKS, INDICES, COMMODITIES, COMMODITIES_YAHOO_ONLY]:
        for asset in group:
            if asset not in static_assets:
                static_assets.append(asset)

    missing_maps = []
    for asset in static_assets:
        try:
            if not get_symbol_map(asset):
                missing_maps.append(asset)
        except Exception:
            missing_maps.append(asset)

    return {
        "static_total": len(static_assets),
        "missing_maps": missing_maps,
        "valid": not missing_maps,
    }


_ASSET_VALIDATION = validate_assets()

if not _ASSET_VALIDATION["valid"]:
    raise RuntimeError(
        "Univers statique invalide : "
        + str(_ASSET_VALIDATION)
    )

