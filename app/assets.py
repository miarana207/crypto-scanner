"""
V4.2.3 — Univers d'actifs dynamique + compatibilité V4.2.

Architecture :
- Binance : univers crypto dynamique 24/7
- Yahoo Finance : actions / ETF / indices / forex / commodities
- Twelve Data : complément / fallback
- Finnhub : conservé pour compatibilité
- Binance :
    1. exchangeInfo
    2. filtres Spot / TRADING
    3. volume 24h
    4. nombre de trades
    5. spread bid/ask
    6. âge du marché
    7. volatilité
    8. profondeur du carnet
    9. scoring qualité
    10. déduplication par actif sous-jacent
    11. univers final illimité si BINANCE_MAX_UNIVERSE <= 0

IMPORTANT :
- Les paramètres de stratégie ne sont PAS définis ici.
- run_and_notify.py peut continuer à utiliser les anciennes interfaces :
    ASSET_GROUPS
    get_symbol_map()
    build_binance_universe()
    get_binance_universe_metadata()
    COMMODITIES
    COMMODITIES_YAHOO_ONLY
"""

from __future__ import annotations

import os
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests


logger = logging.getLogger(__name__)


# ============================================================
# 1. UNIVERS TRADITIONNEL
# ============================================================

STOCKS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "TSLA",
    "AVGO",
    "ORCL",
    "AMD",
    "NFLX",
    "ADBE",
    "CRM",
    "INTC",
    "QCOM",
    "MU",
    "JPM",
    "BAC",
    "GS",
    "MS",
    "V",
    "MA",
    "WMT",
    "COST",
    "HD",
    "MCD",
    "KO",
    "PEP",
    "XOM",
    "CVX",
    "LLY",
    "JNJ",
    "UNH",
    "ABBV",
    "MRK",
    "PFE",
    "CAT",
    "GE",
    "BA",
    "DIS",
]


ETF = [
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "VTI",
    "VOO",
    "XLK",
    "XLF",
    "XLE",
    "XLV",
]


INDICES = [
    "^GSPC",
    "^DJI",
    "^IXIC",
    "^RUT",
    "^VIX",
]


FOREX = [
    "EURUSD=X",
    "GBPUSD=X",
    "USDJPY=X",
    "USDCHF=X",
    "AUDUSD=X",
    "USDCAD=X",
]


# ============================================================
# 2. COMMODITIES
# ============================================================
#
# Ces symboles sont Yahoo Finance.
#
# COMMODITIES :
# actifs pouvant être traités comme commodities génériques.
#
# COMMODITIES_YAHOO_ONLY :
# actifs explicitement réservés à Yahoo lorsqu'un provider
# alternatif ne possède pas forcément le même symbole.
#

COMMODITIES = [
    "GC=F",   # Gold
    "SI=F",   # Silver
    "CL=F",   # WTI Crude Oil
    "NG=F",   # Natural Gas
]


COMMODITIES_YAHOO_ONLY = [
    "HG=F",   # Copper
    "PL=F",   # Platinum
    "PA=F",   # Palladium
    "BZ=F",   # Brent Crude
]


# ============================================================
# 3. BINANCE — CONFIGURATION
# ============================================================

BINANCE_UNIVERSE_URL = os.getenv(
    "BINANCE_UNIVERSE_URL",
    os.getenv(
        "BINANCE_DATA_URL",
        "https://data-api.binance.vision",
    ),
).rstrip("/")


BINANCE_UNIVERSE_FALLBACK_URLS = [
    x.strip().rstrip("/")
    for x in os.getenv(
        "BINANCE_UNIVERSE_FALLBACK_URLS",
        (
            "https://api.binance.com,"
            "https://api-gcp.binance.com,"
            "https://api1.binance.com,"
            "https://api2.binance.com,"
            "https://api3.binance.com,"
            "https://api4.binance.com"
        ),
    ).split(",")
    if x.strip()
]


BINANCE_UNIVERSE_TIMEOUT = 20


# Volume minimum 24h en devise de cotation.
BINANCE_MIN_QUOTE_VOLUME_24H = 5_000_000


# Spread maximum en %.
BINANCE_MAX_SPREAD_PCT = 0.30


# Nombre minimum de trades 24h.
BINANCE_MIN_TRADES_24H = 500


# 0 = aucune limitation artificielle.
BINANCE_MAX_UNIVERSE = 0


# Quotes conservées.
BINANCE_ALLOWED_QUOTE_ASSETS = {
    "USDT",
    "USDC",
    "FDUSD",
}


# Priorité en cas de doublons.
BINANCE_QUOTE_PRIORITY = {
    "USDT": 0,
    "USDC": 1,
    "FDUSD": 2,
}


# ============================================================
# 4. ACTIFS À EXCLURE DE L'UNIVERS BINANCE
# ============================================================

BINANCE_EXCLUDED_BASE_ASSETS = {
    "USDT",
    "USDC",
    "FDUSD",
    "USD1",
    "RLUSD",
    "EUR",
    "GBP",
    "TRY",
    "BRL",
    "UAH",
    "PLN",
    "ZAR",
    "ARS",
    "MXN",
    "NGN",
    "RON",
    "JPY",
    "AUD",
}


# Produits spéciaux / tokenisés / dérivés que nous ne voulons
# pas mélanger avec les actifs spot classiques.
BINANCE_SPECIAL_BASE_ASSETS = {
    "CRCLB",
    "MSTRB",
    "NVDAB",
    "SNDKB",
    "TSLAB",
    "SPCXB",
    "AXTIB",
    "CRWVB",
    "INTWB",
    "KORUB",
    "MUUB",
    "MVLLB",
    "ORCLB",
    "QNTB",
    "SNXXB",
    "TQQQB",
    "MUB",
    "QQQB",
    "XAUT",
    "BFUSD",
}


BINANCE_LEVERAGED_SUFFIXES = (
    "UP",
    "DOWN",
    "BULL",
    "BEAR",
)


# ============================================================
# 5. FILTRES QUALITÉ
# ============================================================

BINANCE_MAX_UNIVERSE_VOLATILITY_PCT = 40.0

BINANCE_MIN_UNIVERSE_AGE_DAYS = 3.0


# ============================================================
# 6. PROFONDEUR DU CARNET
# ============================================================

BINANCE_DEPTH_LIMIT = 100

BINANCE_DEPTH_WORKERS = 8

# Rejet absolu si moins de $25k disponibles autour de ±0.25%.
BINANCE_MIN_DEPTH_025_NOTIONAL = 25_000

# Seuils utilisés pour le scoring.
BINANCE_DEPTH_TARGET_010 = 100_000
BINANCE_DEPTH_TARGET_025 = 250_000
BINANCE_DEPTH_TARGET_050 = 500_000


# ============================================================
# 7. UNIVERS DYNAMIQUE
# ============================================================

# IMPORTANT :
# cette liste est remplacée à chaque refresh.
CRYPTO: List[str] = []


_DYNAMIC_CRYPTO_SYMBOLS: set[str] = set()


_DYNAMIC_CRYPTO_METADATA: Dict[str, Dict[str, Any]] = {}


# ============================================================
# 8. SESSION HTTP
# ============================================================

def _make_session() -> requests.Session:
    """
    Crée une session HTTP réutilisable.
    """

    session = requests.Session()

    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; CryptoScanner/4.2.3)"
            )
        }
    )

    return session


# ============================================================
# 9. HELPERS
# ============================================================

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default

        result = float(value)

        if result != result:
            return default

        return result

    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default

        return int(float(value))

    except (TypeError, ValueError):
        return default


def _clamp(
    value: float,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> float:

    return max(
        minimum,
        min(maximum, value),
    )


# ============================================================
# 10. EXCLUSION PRODUITS BINANCE
# ============================================================

def _binance_is_excluded_product(
    base_asset: str,
    symbol: str,
) -> bool:

    base = (base_asset or "").upper()
    symbol = (symbol or "").upper()

    if base in BINANCE_EXCLUDED_BASE_ASSETS:
        return True

    if base in BINANCE_SPECIAL_BASE_ASSETS:
        return True

    for suffix in BINANCE_LEVERAGED_SUFFIXES:
        if base.endswith(suffix):
            return True

    # Certains produits spéciaux peuvent être identifiés
    # directement dans le symbole.
    for suffix in BINANCE_LEVERAGED_SUFFIXES:
        if symbol.endswith(suffix + "USDT"):
            return True

    return False


# ============================================================
# 11. GET BINANCE AVEC FALLBACK
# ============================================================

def _binance_get(
    path: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: Optional[int] = None,
) -> Tuple[Any, str]:

    if timeout is None:
        timeout = BINANCE_UNIVERSE_TIMEOUT

    bases: List[str] = []

    primary = BINANCE_UNIVERSE_URL.rstrip("/")

    if primary:
        bases.append(primary)

    for fallback in BINANCE_UNIVERSE_FALLBACK_URLS:
        fallback = fallback.rstrip("/")

        if fallback and fallback not in bases:
            bases.append(fallback)

    last_error: Optional[Exception] = None

    for base in bases:

        url = f"{base}{path}"

        try:

            response = requests.get(
                url,
                params=params,
                timeout=timeout,
            )

            response.raise_for_status()

            return response.json(), base

        except Exception as exc:

            last_error = exc

            logger.warning(
                "Binance GET failed: %s | %s",
                url,
                exc,
            )

    if last_error is not None:
        raise last_error

    raise RuntimeError(
        "Aucune URL Binance disponible."
    )


# ============================================================
# 12. ÂGE DU MARCHÉ
# ============================================================

def _symbol_age_days(
    onboard_date_ms: Any,
) -> float:

    if not onboard_date_ms:
        return 999999.0

    try:

        onboard_seconds = (
            float(onboard_date_ms) / 1000.0
        )

        age_seconds = (
            datetime.now(timezone.utc).timestamp()
            - onboard_seconds
        )

        return max(
            0.0,
            age_seconds / 86400.0,
        )

    except Exception:

        return 999999.0


# ============================================================
# 13. VOLATILITÉ
# ============================================================

def _calculate_volatility_pct(
    ticker: Dict[str, Any],
) -> float:

    high = _safe_float(
        ticker.get("highPrice")
    )

    low = _safe_float(
        ticker.get("lowPrice")
    )

    last = _safe_float(
        ticker.get("lastPrice")
    )

    if high <= 0 or low <= 0 or last <= 0:
        return 999999.0

    # Mesure simple du range 24h rapporté au dernier prix.
    volatility = (
        (high - low) / last
    ) * 100.0

    return volatility


# ============================================================
# 14. PROFONDEUR DU CARNET
# ============================================================

def _calculate_depth_notional(
    bids: List[Any],
    asks: List[Any],
    reference_price: float,
    pct: float,
) -> float:

    if reference_price <= 0:
        return 0.0

    lower_price = (
        reference_price * (1.0 - pct)
    )

    upper_price = (
        reference_price * (1.0 + pct)
    )

    total = 0.0

    for entry in bids or []:

        try:

            price = _safe_float(entry[0])
            quantity = _safe_float(entry[1])

            if (
                price > 0
                and quantity > 0
                and price >= lower_price
            ):
                total += price * quantity

        except Exception:
            continue

    for entry in asks or []:

        try:

            price = _safe_float(entry[0])
            quantity = _safe_float(entry[1])

            if (
                price > 0
                and quantity > 0
                and price <= upper_price
            ):
                total += price * quantity

        except Exception:
            continue

    return total


# ============================================================
# 15. SCORE DE PROFONDEUR
# ============================================================

def _depth_ratio_score(
    depth_010: float,
    depth_025: float,
    depth_050: float,
) -> float:

    score_010 = _clamp(
        depth_010 / BINANCE_DEPTH_TARGET_010
    )

    score_025 = _clamp(
        depth_025 / BINANCE_DEPTH_TARGET_025
    )

    score_050 = _clamp(
        depth_050 / BINANCE_DEPTH_TARGET_050
    )

    # Le carnet proche du prix est volontairement
    # davantage valorisé.
    score = (
        0.50 * score_010
        + 0.35 * score_025
        + 0.15 * score_050
    )

    return round(
        _clamp(score) * 100.0,
        2,
    )


# ============================================================
# 16. LIQUIDITÉ
# ============================================================

def _calculate_liquidity_grade(
    quote_volume_24h: float,
) -> float:

    if quote_volume_24h <= 0:
        return 0.0

    # 5M = score 0 ; 100M = score 1.
    score = (
        quote_volume_24h
        / 100_000_000.0
    )

    return _clamp(score)


# ============================================================
# 17. SCORE GLOBAL UNIVERS BINANCE
# ============================================================

def _calculate_universe_quality(
    depth_score: float,
    liquidity: float,
    spread_quality: float,
    trade_liquidity: float,
    stability: float,
) -> float:

    score = (
        0.35 * depth_score
        + 0.25 * liquidity
        + 0.20 * spread_quality
        + 0.10 * trade_liquidity
        + 0.10 * stability
    )

    return round(
        _clamp(score) * 100.0,
        2,
    )


# ============================================================
# 18. FETCH DEPTH
# ============================================================

def _binance_fetch_depth(
    symbol: str,
    last_price: float,
) -> Optional[Dict[str, Any]]:

    try:

        data, base_url = _binance_get(
            "/api/v3/depth",
            params={
                "symbol": symbol,
                "limit": BINANCE_DEPTH_LIMIT,
            },
            timeout=BINANCE_UNIVERSE_TIMEOUT,
        )

        bids = data.get(
            "bids",
            [],
        )

        asks = data.get(
            "asks",
            [],
        )

        depth_010 = _calculate_depth_notional(
            bids,
            asks,
            last_price,
            0.0010,
        )

        depth_025 = _calculate_depth_notional(
            bids,
            asks,
            last_price,
            0.0025,
        )

        depth_050 = _calculate_depth_notional(
            bids,
            asks,
            last_price,
            0.0050,
        )

        if depth_025 < BINANCE_MIN_DEPTH_025_NOTIONAL:
            return {
                "accepted": False,
                "symbol": symbol,
                "depth_010": depth_010,
                "depth_025": depth_025,
                "depth_050": depth_050,
                "depth_score": 0.0,
                "endpoint": base_url,
            }

        depth_score = _depth_ratio_score(
            depth_010,
            depth_025,
            depth_050,
        )

        return {
            "accepted": True,
            "symbol": symbol,
            "depth_010": depth_010,
            "depth_025": depth_025,
            "depth_050": depth_050,
            "depth_score": depth_score,
            "endpoint": base_url,
        }

    except Exception as exc:

        logger.warning(
            "Depth error %s: %s",
            symbol,
            exc,
        )

        return None


# ============================================================
# 19. REFRESH UNIVERS BINANCE
# ============================================================

def refresh_binance_universe() -> List[str]:

    global CRYPTO
    global _DYNAMIC_CRYPTO_SYMBOLS
    global _DYNAMIC_CRYPTO_METADATA

    started = time.time()

    logger.info(
        "=================================================="
    )

    logger.info(
        "BINANCE — refresh dynamique de l'univers"
    )

    logger.info(
        "=================================================="
    )

    # --------------------------------------------------------
    # 1. EXCHANGE INFO
    # --------------------------------------------------------

    exchange_info, exchange_endpoint = _binance_get(
        "/api/v3/exchangeInfo"
    )

    symbols_info = exchange_info.get(
        "symbols",
        [],
    )

    logger.info(
        "Binance exchangeInfo : %d marchés connus",
        len(symbols_info),
    )

    # --------------------------------------------------------
    # 2. FILTRAGE STRUCTUREL
    # --------------------------------------------------------

    candidates: List[Dict[str, Any]] = []

    count_trading = 0
    count_spot = 0
    count_quote = 0
    count_excluded = 0

    for info in symbols_info:

        symbol = str(
            info.get("symbol", "")
        ).upper()

        status = str(
            info.get("status", "")
        ).upper()

        base_asset = str(
            info.get("baseAsset", "")
        ).upper()

        quote_asset = str(
            info.get("quoteAsset", "")
        ).upper()

        if status != "TRADING":
            continue

        count_trading += 1

        is_spot = False

        if info.get(
            "isSpotTradingAllowed"
        ) is True:
            is_spot = True

        permissions = info.get(
            "permissions",
            [],
        )

        if (
            isinstance(permissions, list)
            and "SPOT" in permissions
        ):
            is_spot = True

        if not is_spot:
            continue

        count_spot += 1

        if quote_asset not in BINANCE_ALLOWED_QUOTE_ASSETS:
            continue

        count_quote += 1

        if _binance_is_excluded_product(
            base_asset,
            symbol,
        ):
            count_excluded += 1
            continue

        candidates.append(
            {
                "symbol": symbol,
                "baseAsset": base_asset,
                "quoteAsset": quote_asset,
                "onboardDate": info.get(
                    "onboardDate"
                ),
            }
        )

    logger.info(
        "Structure : %d TRADING | %d Spot | %d quotes autorisées | %d exclus",
        count_trading,
        count_spot,
        count_quote,
        count_excluded,
    )

    # --------------------------------------------------------
    # 3. TICKER 24H
    # --------------------------------------------------------

    ticker_data, ticker_endpoint = _binance_get(
        "/api/v3/ticker/24hr"
    )

    if not isinstance(
        ticker_data,
        list,
    ):
        raise RuntimeError(
            "Réponse ticker Binance invalide."
        )

    ticker_by_symbol: Dict[str, Dict[str, Any]] = {}

    for ticker in ticker_data:

        symbol = str(
            ticker.get("symbol", "")
        ).upper()

        if symbol:
            ticker_by_symbol[symbol] = ticker

    stats = {
        "ticker_ok": 0,
        "volume_ok": 0,
        "trades_ok": 0,
        "price_ok": 0,
        "spread_ok": 0,
        "age_ok": 0,
        "volatility_ok": 0,
        "ticker_missing": 0,
        "volume_rejected": 0,
        "trades_rejected": 0,
        "price_rejected": 0,
        "spread_rejected": 0,
        "age_rejected": 0,
        "volatility_rejected": 0,
    }

    prelim: List[Dict[str, Any]] = []

    for candidate in candidates:

        symbol = candidate["symbol"]

        ticker = ticker_by_symbol.get(
            symbol
        )

        if not ticker:

            stats["ticker_missing"] += 1
            continue

        stats["ticker_ok"] += 1

        quote_volume = _safe_float(
            ticker.get("quoteVolume")
        )

        if (
            quote_volume
            < BINANCE_MIN_QUOTE_VOLUME_24H
        ):

            stats["volume_rejected"] += 1
            continue

        stats["volume_ok"] += 1

        trades = _safe_int(
            ticker.get("count")
        )

        if trades < BINANCE_MIN_TRADES_24H:

            stats["trades_rejected"] += 1
            continue

        stats["trades_ok"] += 1

        last_price = _safe_float(
            ticker.get("lastPrice")
        )

        bid_price = _safe_float(
            ticker.get("bidPrice")
        )

        ask_price = _safe_float(
            ticker.get("askPrice")
        )

        if (
            last_price <= 0
            or bid_price <= 0
            or ask_price <= 0
        ):

            stats["price_rejected"] += 1
            continue

        stats["price_ok"] += 1

        mid_price = (
            bid_price + ask_price
        ) / 2.0

        if mid_price <= 0:

            stats["spread_rejected"] += 1
            continue

        spread_pct = (
            (ask_price - bid_price)
            / mid_price
        ) * 100.0

        if (
            spread_pct < 0
            or spread_pct
            > BINANCE_MAX_SPREAD_PCT
        ):

            stats["spread_rejected"] += 1
            continue

        stats["spread_ok"] += 1

        age_days = _symbol_age_days(
            candidate.get("onboardDate")
        )

        if (
            age_days
            < BINANCE_MIN_UNIVERSE_AGE_DAYS
        ):

            stats["age_rejected"] += 1
            continue

        stats["age_ok"] += 1

        volatility_pct = (
            _calculate_volatility_pct(
                ticker
            )
        )

        if (
            volatility_pct
            > BINANCE_MAX_UNIVERSE_VOLATILITY_PCT
        ):

            stats["volatility_rejected"] += 1
            continue

        stats["volatility_ok"] += 1

        prelim.append(
            {
                **candidate,
                "ticker": ticker,
                "quote_volume_24h": quote_volume,
                "trades_24h": trades,
                "last_price": last_price,
                "bid_price": bid_price,
                "ask_price": ask_price,
                "spread_pct": spread_pct,
                "age_days": age_days,
                "volatility_pct": volatility_pct,
            }
        )

    logger.info(
        (
            "Binance ticker filters : "
            "OK=%d | volume=%d | trades=%d | "
            "price=%d | spread=%d | age=%d | volatility=%d"
        ),
        stats["ticker_ok"],
        stats["volume_ok"],
        stats["trades_ok"],
        stats["price_ok"],
        stats["spread_ok"],
        stats["age_ok"],
        stats["volatility_ok"],
    )

    logger.info(
        (
            "Rejets : volume=%d | trades=%d | "
            "price=%d | spread=%d | age=%d | volatility=%d"
        ),
        stats["volume_rejected"],
        stats["trades_rejected"],
        stats["price_rejected"],
        stats["spread_rejected"],
        stats["age_rejected"],
        stats["volatility_rejected"],
    )

    logger.info(
        "Candidats avant profondeur : %d",
        len(prelim),
    )

    # --------------------------------------------------------
    # 4. DEPTH PARALLELISÉ
    # --------------------------------------------------------

    depth_results: Dict[
        str,
        Optional[Dict[str, Any]]
    ] = {}

    if prelim:

        workers = min(
            BINANCE_DEPTH_WORKERS,
            max(1, len(prelim)),
        )

        with ThreadPoolExecutor(
            max_workers=workers
        ) as executor:

            futures = {
                executor.submit(
                    _binance_fetch_depth,
                    item["symbol"],
                    item["last_price"],
                ): item["symbol"]
                for item in prelim
            }

            for future in as_completed(
                futures
            ):

                symbol = futures[future]

                try:

                    depth_results[
                        symbol
                    ] = future.result()

                except Exception as exc:

                    logger.warning(
                        "Depth future error %s: %s",
                        symbol,
                        exc,
                    )

                    depth_results[
                        symbol
                    ] = None

    depth_analyzed = 0
    depth_accepted = 0
    depth_rejected = 0
    depth_errors = 0

    accepted: List[Dict[str, Any]] = []

    for item in prelim:

        symbol = item["symbol"]

        depth = depth_results.get(
            symbol
        )

        if depth is None:

            depth_errors += 1
            continue

        depth_analyzed += 1

        if not depth.get(
            "accepted",
            False,
        ):

            depth_rejected += 1
            continue

        depth_accepted += 1

        item = {
            **item,
            **depth,
        }

        # ----------------------------------------------------
        # SCORE LIQUIDITÉ
        # ----------------------------------------------------

        liquidity_score = (
            _calculate_liquidity_grade(
                item["quote_volume_24h"]
            )
        )

        # ----------------------------------------------------
        # SCORE SPREAD
        # ----------------------------------------------------

        spread_quality = _clamp(
            1.0
            - (
                item["spread_pct"]
                / BINANCE_MAX_SPREAD_PCT
            )
        )

        # ----------------------------------------------------
        # SCORE TRADES
        # ----------------------------------------------------

        trade_liquidity = _clamp(
            item["trades_24h"]
            / 100_000.0
        )

        # ----------------------------------------------------
        # STABILITÉ
        # ----------------------------------------------------

        stability = _clamp(
            1.0
            - (
                item["volatility_pct"]
                / BINANCE_MAX_UNIVERSE_VOLATILITY_PCT
            )
        )

        quality_score = (
            _calculate_universe_quality(
                depth_score=(
                    item["depth_score"]
                    / 100.0
                ),
                liquidity=liquidity_score,
                spread_quality=spread_quality,
                trade_liquidity=trade_liquidity,
                stability=stability,
            )
        )

        item["liquidity_score"] = round(
            liquidity_score * 100.0,
            2,
        )

        item["spread_quality"] = round(
            spread_quality * 100.0,
            2,
        )

        item["trade_liquidity"] = round(
            trade_liquidity * 100.0,
            2,
        )

        item["stability_score"] = round(
            stability * 100.0,
            2,
        )

        item["quality_score"] = quality_score

        accepted.append(item)

    logger.info(
        (
            "Depth : %d analysés | %d acceptés | "
            "%d rejetés | %d erreurs"
        ),
        depth_analyzed,
        depth_accepted,
        depth_rejected,
        depth_errors,
    )

    # --------------------------------------------------------
    # 5. DÉDUPLICATION PAR BASE ASSET
    # --------------------------------------------------------

    before_dedup = len(
        accepted
    )

    accepted.sort(
        key=lambda item: (
            item.get("quality_score", 0.0),
            item.get("quote_volume_24h", 0.0),
        ),
        reverse=True,
    )

    deduped_by_base: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for item in accepted:

        base_asset = item[
            "baseAsset"
        ]

        if base_asset not in deduped_by_base:

            deduped_by_base[
                base_asset
            ] = item

            continue

        current = deduped_by_base[
            base_asset
        ]

        current_priority = BINANCE_QUOTE_PRIORITY.get(
            current["quoteAsset"],
            999,
        )

        new_priority = BINANCE_QUOTE_PRIORITY.get(
            item["quoteAsset"],
            999,
        )

        # Priorité USDT > USDC > FDUSD.
        # En cas d'égalité, meilleur score.
        if (
            new_priority
            < current_priority
        ):

            deduped_by_base[
                base_asset
            ] = item

        elif (
            new_priority
            == current_priority
            and item.get(
                "quality_score",
                0.0,
            )
            > current.get(
                "quality_score",
                0.0,
            )
        ):

            deduped_by_base[
                base_asset
            ] = item

    final_candidates = list(
        deduped_by_base.values()
    )

    logger.info(
        "Déduplication : %d -> %d actifs uniques",
        before_dedup,
        len(final_candidates),
    )

    # --------------------------------------------------------
    # 6. TRI FINAL
    # --------------------------------------------------------

    final_candidates.sort(
        key=lambda item: (
            item.get(
                "quality_score",
                0.0,
            ),
            item.get(
                "quote_volume_24h",
                0.0,
            ),
        ),
        reverse=True,
    )

    # --------------------------------------------------------
    # 7. CAP OPTIONNEL
    # --------------------------------------------------------

    if BINANCE_MAX_UNIVERSE > 0:

        final_candidates = (
            final_candidates[
                :BINANCE_MAX_UNIVERSE
            ]
        )

    # Sinon :
    # aucun plafond artificiel.

    # --------------------------------------------------------
    # 8. CONSTRUCTION UNIVERS FINAL
    # --------------------------------------------------------

    new_symbols = [
        item["symbol"]
        for item in final_candidates
    ]

    new_metadata: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for rank, item in enumerate(
        final_candidates,
        start=1,
    ):

        symbol = item[
            "symbol"
        ]

        new_metadata[
            symbol
        ] = {
            "symbol": symbol,
            "base_asset": item[
                "baseAsset"
            ],
            "quote_asset": item[
                "quoteAsset"
            ],
            "rank": rank,
            "quality_score": item.get(
                "quality_score",
                0.0,
            ),
            "quote_volume_24h": item.get(
                "quote_volume_24h",
                0.0,
            ),
            "trades_24h": item.get(
                "trades_24h",
                0,
            ),
            "last_price": item.get(
                "last_price",
                0.0,
            ),
            "bid_price": item.get(
                "bid_price",
                0.0,
            ),
            "ask_price": item.get(
                "ask_price",
                0.0,
            ),
            "spread_pct": item.get(
                "spread_pct",
                0.0,
            ),
            "age_days": item.get(
                "age_days",
                0.0,
            ),
            "volatility_pct": item.get(
                "volatility_pct",
                0.0,
            ),
            "depth_010": item.get(
                "depth_010",
                0.0,
            ),
            "depth_025": item.get(
                "depth_025",
                0.0,
            ),
            "depth_050": item.get(
                "depth_050",
                0.0,
            ),
            "depth_score": item.get(
                "depth_score",
                0.0,
            ),
            "liquidity_score": item.get(
                "liquidity_score",
                0.0,
            ),
            "spread_quality": item.get(
                "spread_quality",
                0.0,
            ),
            "trade_liquidity": item.get(
                "trade_liquidity",
                0.0,
            ),
            "stability_score": item.get(
                "stability_score",
                0.0,
            ),
            "provider": "binance",
            "endpoint": item.get(
                "endpoint",
                "",
            ),
        }

    # --------------------------------------------------------
    # 9. UPDATE GLOBAL
    # --------------------------------------------------------

    CRYPTO = new_symbols

    _DYNAMIC_CRYPTO_SYMBOLS = set(
        new_symbols
    )

    _DYNAMIC_CRYPTO_METADATA = (
        new_metadata
    )

    # Mise à jour de ASSET_GROUPS.
    ASSET_GROUPS[
        "crypto"
    ] = CRYPTO

    elapsed = (
        time.time() - started
    )

    logger.info(
        "=================================================="
    )

    logger.info(
        "BINANCE UNIVERS FINAL : %d actifs",
        len(CRYPTO),
    )

    logger.info(
        "Temps refresh Binance : %.1fs",
        elapsed,
    )

    logger.info(
        "=================================================="
    )

    return CRYPTO


# ============================================================
# 20. COMPATIBILITÉ ANCIENNE API
# ============================================================

def ensure_binance_universe(
    force_refresh: bool = False,
) -> List[str]:

    """
    Initialise l'univers Binance si nécessaire.

    Compatible avec l'ancien workflow.
    """

    if (
        force_refresh
        or not CRYPTO
    ):

        return refresh_binance_universe()

    return CRYPTO


def build_binance_universe(
    force_refresh: bool = False,
) -> List[str]:

    """
    Alias de compatibilité utilisé par
    run_and_notify.py.
    """

    return ensure_binance_universe(
        force_refresh=force_refresh
    )


def get_binance_metadata(
    symbol: Optional[str] = None,
) -> Any:

    """
    Retourne les métadonnées Binance dynamiques.

    Si symbol est fourni :
        retourne uniquement ses métadonnées.

    Sinon :
        retourne toutes les métadonnées.
    """

    if symbol is None:

        return dict(
            _DYNAMIC_CRYPTO_METADATA
        )

    return _DYNAMIC_CRYPTO_METADATA.get(
        symbol
    )


def get_binance_universe_metadata() -> Dict[
    str,
    Dict[str, Any]
]:

    """
    Alias de compatibilité utilisé par
    run_and_notify.py.
    """

    return dict(
        _DYNAMIC_CRYPTO_METADATA
    )


# ============================================================
# 21. SYMBOL MAP
# ============================================================

def _crypto_yahoo_symbol(
    symbol: str,
) -> str:

    """
    Conversion simple Binance -> Yahoo.

    BTCUSDT -> BTC-USD
    ETHUSDC -> ETH-USD
    """

    symbol = (
        symbol or ""
    ).upper()

    for quote in (
        "USDT",
        "USDC",
        "FDUSD",
    ):

        if symbol.endswith(
            quote
        ):

            base = symbol[
                :-len(quote)
            ]

            if base:
                return (
                    f"{base}-USD"
                )

    return symbol


def get_provider_symbol(
    symbol: str,
    provider: Optional[str] = None,
) -> str:

    """
    Retourne le symbole adapté au provider.

    Cette fonction conserve la compatibilité
    avec les appels existants.
    """

    symbol = (
        symbol or ""
    ).upper()

    provider_name = (
        (provider or "")
        .lower()
        .strip()
    )

    # Crypto dynamique.
    if symbol in _DYNAMIC_CRYPTO_SYMBOLS:

        if provider_name == "yahoo":
            return _crypto_yahoo_symbol(
                symbol
            )

        if provider_name in {
            "binance",
            "crypto",
        }:
            return symbol

        # Pour Twelve Data / Finnhub,
        # le symbole Binance reste disponible
        # mais le DataRouter décide du provider.
        return symbol

    # TradFi :
    # les symboles sont déjà dans le format
    # attendu par leur source principale.
    return symbol


def get_symbol_map(
    symbol: Optional[str] = None,
) -> Dict[str, Any]:

    """
    Compatibilité V4.2.

    Retourne les mappings de symbole pour les
    différents fournisseurs.

    Si aucun symbole n'est fourni :
        retourne le mapping de tout l'univers connu.
    """

    if symbol is None:

        all_symbols = get_all_assets()

        return {
            asset: get_symbol_map(asset)
            for asset in all_symbols
        }

    asset = (
        symbol or ""
    ).upper()

    asset_type = get_asset_type(
        asset
    )

    # Crypto Binance.
    if asset_type == "crypto":

        return {
            "binance": get_provider_symbol(
                asset,
                "binance",
            ),
            "yahoo": get_provider_symbol(
                asset,
                "yahoo",
            ),
            "twelve_data": get_provider_symbol(
                asset,
                "twelve_data",
            ),
            "finnhub": get_provider_symbol(
                asset,
                "finnhub",
            ),
        }

    # Traditional assets.
    #
    # Le symbole canonique est conservé.
    # Le DataRouter reste responsable du choix
    # effectif de la source.
    return {
        "binance": asset,
        "yahoo": asset,
        "twelve_data": asset,
        "finnhub": asset,
    }


# ============================================================
# 22. ASSET GROUPS
# ============================================================

ASSET_GROUPS = {
    "crypto": CRYPTO,
    "forex": FOREX,
    "stocks": STOCKS,
    "stock": STOCKS,
    "etf": ETF,
    "indices": INDICES,
    "index": INDICES,
    "commodities": COMMODITIES,
    "commodity": COMMODITIES,
    "commodities_yahoo_only": COMMODITIES_YAHOO_ONLY,
}


# ============================================================
# 23. TOUS LES ACTIFS
# ============================================================

def get_all_assets() -> List[str]:

    """
    Retourne l'ensemble de l'univers actuel.

    Binance est dynamique.
    """

    assets: List[str] = []

    for group in (
        CRYPTO,
        FOREX,
        STOCKS,
        ETF,
        INDICES,
        COMMODITIES,
        COMMODITIES_YAHOO_ONLY,
    ):

        for asset in group:

            if asset not in assets:
                assets.append(asset)

    return assets


# ============================================================
# 24. TYPE D'ACTIF
# ============================================================

def get_asset_type(
    symbol: str,
) -> str:

    symbol = (
        symbol or ""
    ).upper()

    if symbol in _DYNAMIC_CRYPTO_SYMBOLS:
        return "crypto"

    if symbol in FOREX:
        return "forex"

    if symbol in STOCKS:
        return "stock"

    if symbol in ETF:
        return "etf"

    if symbol in INDICES:
        return "index"

    if symbol in COMMODITIES:
        return "commodity"

    if symbol in COMMODITIES_YAHOO_ONLY:
        return "commodity"

    return "unknown"


# ============================================================
# 25. INITIALISATION LAZY
# ============================================================

def _initialize_compatibility_state() -> None:
    """
    Ne fait PAS d'appel réseau au moment de l'import.

    Le refresh Binance est déclenché explicitement par
    build_binance_universe() / ensure_binance_universe().
    """

    ASSET_GROUPS[
        "crypto"
    ] = CRYPTO


_initialize_compatibility_state()
