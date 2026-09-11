"""
V4.2.3 — Univers d'actifs dynamique.

Architecture :
- Binance : univers crypto dynamique
- Yahoo / Finnhub / Twelve Data : actifs traditionnels
- Filtrage Binance en cascade
- Analyse de profondeur du carnet après les filtres rapides
- Grading de liquidité A/B/C/D/E
- Déduplication par actif de base
- Aucun changement des paramètres de stratégie de trading

IMPORTANT :
Ce fichier ne modifie PAS :
- EMA
- RSI
- ATR
- breakout
- SL / TP
- RR
- holding period
- seuil de signal
- routage des actifs traditionnels
"""

from __future__ import annotations

import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# UNIVERS TRADITIONNEL
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
    "NFLX",
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
# CRYPTO DYNAMIQUE
# ============================================================

CRYPTO: List[str] = []

_DYNAMIC_CRYPTO_SYMBOLS: set[str] = set()

_DYNAMIC_CRYPTO_METADATA: Dict[
    str,
    Dict[str, Any],
] = {}

_UNIVERSE_LOCK = threading.Lock()


# ============================================================
# CONFIGURATION BINANCE
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


BINANCE_UNIVERSE_TIMEOUT = int(
    os.getenv(
        "BINANCE_UNIVERSE_TIMEOUT",
        "20",
    )
)

BINANCE_MIN_QUOTE_VOLUME_24H = float(
    os.getenv(
        "BINANCE_MIN_QUOTE_VOLUME_24H",
        "5000000",
    )
)

BINANCE_MAX_SPREAD_PCT = float(
    os.getenv(
        "BINANCE_MAX_SPREAD_PCT",
        "0.30",
    )
)

BINANCE_MIN_TRADES_24H = int(
    os.getenv(
        "BINANCE_MIN_TRADES_24H",
        "500",
    )
)

# 0 = illimité
BINANCE_MAX_UNIVERSE = int(
    os.getenv(
        "BINANCE_MAX_UNIVERSE",
        "0",
    )
)

BINANCE_ALLOWED_QUOTE_ASSETS = {
    "USDT",
    "USDC",
    "FDUSD",
}


# ============================================================
# EXCLUSIONS BINANCE
# ============================================================

BINANCE_EXCLUDED_BASE_ASSETS = {
    x.strip().upper()
    for x in os.getenv(
        "BINANCE_EXCLUDED_BASE_ASSETS",
        (
            "USDT,USDC,FDUSD,USD1,RLUSD,EUR,GBP,TRY,BRL,"
            "UAH,PLN,ZAR,ARS,MXN,NGN,RON,JPY,AUD"
        ),
    )
    .replace("\n", "")
    .split(",")
    if x.strip()
}


BINANCE_QUOTE_PRIORITY = [
    x.strip().upper()
    for x in os.getenv(
        "BINANCE_QUOTE_PRIORITY",
        "USDT,USDC,FDUSD",
    ).split(",")
    if x.strip()
]


BINANCE_SPECIAL_BASE_ASSETS = {
    x.strip().upper()
    for x in os.getenv(
        "BINANCE_SPECIAL_BASE_ASSETS",
        (
            "CRCLB,MSTRB,NVDAB,SNDKB,TSLAB,SPCXB,AXTIB,"
            "CRWVB,INTWB,KORUB,MUUB,MVLLB,ORCLB,QNTB,SNXXB,"
            "TQQQB,MUB,QQQB,XAUT,BFUSD"
        ),
    )
    .replace("\n", "")
    .split(",")
    if x.strip()
}


BINANCE_LEVERAGED_TOKEN_SUFFIXES = (
    "UP",
    "DOWN",
    "BULL",
    "BEAR",
)


# ============================================================
# VOLATILITÉ / ÂGE
# ============================================================

BINANCE_MAX_UNIVERSE_VOLATILITY_PCT = float(
    os.getenv(
        "BINANCE_MAX_UNIVERSE_VOLATILITY_PCT",
        "40.0",
    )
)

BINANCE_MIN_UNIVERSE_AGE_DAYS = float(
    os.getenv(
        "BINANCE_MIN_UNIVERSE_AGE_DAYS",
        "3.0",
    )
)


# ============================================================
# PROFONDEUR DU CARNET
# ============================================================

BINANCE_DEPTH_LIMIT = int(
    os.getenv(
        "BINANCE_DEPTH_LIMIT",
        "100",
    )
)

BINANCE_DEPTH_WORKERS = int(
    os.getenv(
        "BINANCE_DEPTH_WORKERS",
        "8",
    )
)

# Rejet dur :
# profondeur cumulée bid + ask à ±0,25 %
BINANCE_MIN_DEPTH_025_NOTIONAL = float(
    os.getenv(
        "BINANCE_MIN_DEPTH_025_NOTIONAL",
        "25000",
    )
)


# ============================================================
# CIBLES DE LIQUIDITÉ
# ============================================================

BINANCE_DEPTH_TARGET_010 = float(
    os.getenv(
        "BINANCE_DEPTH_TARGET_010",
        "100000",
    )
)

BINANCE_DEPTH_TARGET_025 = float(
    os.getenv(
        "BINANCE_DEPTH_TARGET_025",
        "250000",
    )
)

BINANCE_DEPTH_TARGET_050 = float(
    os.getenv(
        "BINANCE_DEPTH_TARGET_050",
        "500000",
    )
)


# ============================================================
# SESSION HTTP
# ============================================================

def _make_session() -> requests.Session:
    session = requests.Session()

    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; V4.2.3-BinanceScanner/1.0)"
            )
        }
    )

    return session


# ============================================================
# UTILITAIRES
# ============================================================

def _safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(
    value: Any,
    default: int = 0,
) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(
    value: float,
    low: float = 0.0,
    high: float = 1.0,
) -> float:
    return max(
        low,
        min(high, value),
    )


# ============================================================
# EXCLUSION PRODUIT
# ============================================================

def _binance_is_excluded_product(
    symbol: str,
    base_asset: str,
) -> tuple[bool, str]:

    base = str(
        base_asset or ""
    ).upper().strip()

    symbol = str(
        symbol or ""
    ).upper().strip()

    if base in BINANCE_SPECIAL_BASE_ASSETS:
        return True, "special/tokenized"

    if base.endswith(
        BINANCE_LEVERAGED_TOKEN_SUFFIXES
    ):
        return True, "leveraged-token"

    return False, ""


# ============================================================
# REQUÊTE BINANCE AVEC FALLBACK
# ============================================================

def _binance_get(
    path: str,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, str]:

    urls = [
        BINANCE_UNIVERSE_URL,
        *[
            x
            for x in BINANCE_UNIVERSE_FALLBACK_URLS
            if x != BINANCE_UNIVERSE_URL
        ],
    ]

    last_error: Optional[Exception] = None

    for base_url in urls:

        url = f"{base_url}{path}"

        try:
            session = _make_session()

            response = session.get(
                url,
                params=params,
                timeout=BINANCE_UNIVERSE_TIMEOUT,
            )

            response.raise_for_status()

            return (
                response.json(),
                base_url,
            )

        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        f"Binance request failed for "
        f"{path}: {last_error}"
    )


# ============================================================
# ÂGE DU MARCHÉ
# ============================================================

def _symbol_age_days(
    onboard_date: Any,
) -> float:

    timestamp = _safe_int(
        onboard_date,
        0,
    )

    if timestamp <= 0:
        return 99999.0

    try:

        listing_dt = datetime.fromtimestamp(
            timestamp / 1000,
            tz=timezone.utc,
        )

        now = datetime.now(
            timezone.utc
        )

        return (
            now - listing_dt
        ).total_seconds() / 86400.0

    except Exception:
        return 99999.0


# ============================================================
# VOLATILITÉ 24H
# ============================================================

def _calculate_volatility_pct(
    high: float,
    low: float,
) -> float:

    if high <= 0 or low <= 0:
        return 999.0

    midpoint = (
        high + low
    ) / 2.0

    if midpoint <= 0:
        return 999.0

    return (
        (high - low)
        / midpoint
        * 100.0
    )


# ============================================================
# PROFONDEUR CARNET
# ============================================================

def _calculate_depth_notional(
    bids: List[List[Any]],
    asks: List[List[Any]],
    mid_price: float,
    band_pct: float,
) -> float:

    if mid_price <= 0:
        return 0.0

    lower_price = (
        mid_price
        * (1.0 - band_pct)
    )

    upper_price = (
        mid_price
        * (1.0 + band_pct)
    )

    total = 0.0

    for level in bids:

        if len(level) < 2:
            continue

        price = _safe_float(
            level[0]
        )

        quantity = _safe_float(
            level[1]
        )

        if price >= lower_price:
            total += (
                price
                * quantity
            )

    for level in asks:

        if len(level) < 2:
            continue

        price = _safe_float(
            level[0]
        )

        quantity = _safe_float(
            level[1]
        )

        if price <= upper_price:
            total += (
                price
                * quantity
            )

    return total


# ============================================================
# SCORE DE PROFONDEUR
# ============================================================

def _depth_ratio_score(
    value: float,
    target: float,
) -> float:

    if value <= 0 or target <= 0:
        return 0.0

    ratio = (
        value / target
    )

    if ratio < 1.0:
        return 0.35 * ratio

    if ratio < 2.0:
        return (
            0.35
            + 0.30
            * (ratio - 1.0)
        )

    if ratio < 5.0:
        return (
            0.65
            + 0.20
            * (ratio - 2.0)
            / 3.0
        )

    if ratio < 10.0:
        return (
            0.85
            + 0.10
            * (ratio - 5.0)
            / 5.0
        )

    return (
        0.97
        + 0.03
        * min(
            1.0,
            math.log10(
                ratio / 10.0
                + 1.0
            ),
        )
    )


# ============================================================
# GRADING LIQUIDITÉ
# ============================================================

def _calculate_liquidity_grade(
    depth_010: float,
    depth_025: float,
    depth_050: float,
) -> Tuple[str, float]:

    score_010 = _depth_ratio_score(
        depth_010,
        BINANCE_DEPTH_TARGET_010,
    )

    score_025 = _depth_ratio_score(
        depth_025,
        BINANCE_DEPTH_TARGET_025,
    )

    score_050 = _depth_ratio_score(
        depth_050,
        BINANCE_DEPTH_TARGET_050,
    )

    depth_score = (
        0.30 * score_010
        + 0.45 * score_025
        + 0.25 * score_050
    )

    # Rejet dur
    if (
        depth_025
        < BINANCE_MIN_DEPTH_025_NOTIONAL
    ):
        return (
            "E",
            depth_score,
        )

    # Grade A
    if (
        depth_025 >= 1_000_000
        and depth_score >= 0.90
    ):
        return (
            "A",
            depth_score,
        )

    # Grade B
    if (
        depth_025 >= 500_000
        and depth_score >= 0.78
    ):
        return (
            "B",
            depth_score,
        )

    # Grade C
    if (
        depth_025 >= 150_000
        and depth_score >= 0.60
    ):
        return (
            "C",
            depth_score,
        )

    # Grade D
    if (
        depth_025
        >= BINANCE_MIN_DEPTH_025_NOTIONAL
        and depth_score >= 0.35
    ):
        return (
            "D",
            depth_score,
        )

    return (
        "E",
        depth_score,
    )


# ============================================================
# SCORE QUALITÉ GLOBAL
# ============================================================

def _calculate_universe_quality(
    depth_score: float,
    liquidity: float,
    spread_quality: float,
    trade_liquidity: float,
    stability: float,
) -> float:

    return (
        0.35 * depth_score
        + 0.25 * liquidity
        + 0.20 * spread_quality
        + 0.10 * trade_liquidity
        + 0.10 * stability
    )


# ============================================================
# FETCH DEPTH
# ============================================================

def _binance_fetch_depth(
    symbol: str,
) -> Dict[str, Any]:

    try:

        data, endpoint = _binance_get(
            "/api/v3/depth",
            {
                "symbol": symbol,
                "limit": BINANCE_DEPTH_LIMIT,
            },
        )

        bids = data.get(
            "bids",
            [],
        )

        asks = data.get(
            "asks",
            [],
        )

        if not bids or not asks:

            return {
                "symbol": symbol,
                "ok": False,
                "error": "empty-order-book",
                "endpoint": endpoint,
            }

        best_bid = _safe_float(
            bids[0][0]
        )

        best_ask = _safe_float(
            asks[0][0]
        )

        if (
            best_bid <= 0
            or best_ask <= 0
        ):

            return {
                "symbol": symbol,
                "ok": False,
                "error": (
                    "invalid-best-bid-ask"
                ),
                "endpoint": endpoint,
            }

        mid_price = (
            best_bid
            + best_ask
        ) / 2.0

        depth_010 = (
            _calculate_depth_notional(
                bids,
                asks,
                mid_price,
                0.0010,
            )
        )

        depth_025 = (
            _calculate_depth_notional(
                bids,
                asks,
                mid_price,
                0.0025,
            )
        )

        depth_050 = (
            _calculate_depth_notional(
                bids,
                asks,
                mid_price,
                0.0050,
            )
        )

        return {
            "symbol": symbol,
            "ok": True,
            "endpoint": endpoint,
            "depth_010": depth_010,
            "depth_025": depth_025,
            "depth_050": depth_050,
        }

    except Exception as exc:

        return {
            "symbol": symbol,
            "ok": False,
            "error": str(exc),
        }


# ============================================================
# REFRESH BINANCE UNIVERSE
# ============================================================

def refresh_binance_universe() -> List[str]:

    global CRYPTO
    global _DYNAMIC_CRYPTO_SYMBOLS
    global _DYNAMIC_CRYPTO_METADATA

    # ========================================================
    # EXCHANGE INFO
    # ========================================================

    print(
        "[Binance Universe] "
        "exchangeInfo..."
    )

    exchange_info, exchange_endpoint = (
        _binance_get(
            "/api/v3/exchangeInfo"
        )
    )

    symbols_info = exchange_info.get(
        "symbols",
        [],
    )

    known_count = len(
        symbols_info
    )

    trading_symbols = []

    for info in symbols_info:

        status = str(
            info.get(
                "status",
                "",
            )
        ).upper()

        if status == "TRADING":
            trading_symbols.append(
                info
            )

    print(
        "[Binance Universe] "
        f"endpoint={exchange_endpoint} | "
        f"connus={known_count} | "
        f"TRADING={len(trading_symbols)}"
    )

    # ========================================================
    # FILTRE SPOT
    # ========================================================

    candidates = []

    excluded_special = 0
    excluded_base = 0

    for info in trading_symbols:

        symbol = str(
            info.get(
                "symbol",
                "",
            )
        ).upper()

        base_asset = str(
            info.get(
                "baseAsset",
                "",
            )
        ).upper()

        quote_asset = str(
            info.get(
                "quoteAsset",
                "",
            )
        ).upper()

        permissions = {
            str(x).upper()
            for x in info.get(
                "permissions",
                [],
            )
        }

        spot_allowed = bool(
            info.get(
                "isSpotTradingAllowed",
                False,
            )
        )

        if (
            not spot_allowed
            and "SPOT"
            not in permissions
        ):
            continue

        if (
            quote_asset
            not in BINANCE_ALLOWED_QUOTE_ASSETS
        ):
            continue

        if (
            base_asset
            in BINANCE_EXCLUDED_BASE_ASSETS
        ):
            excluded_base += 1
            continue

        excluded, _reason = (
            _binance_is_excluded_product(
                symbol,
                base_asset,
            )
        )

        if excluded:
            excluded_special += 1
            continue

        candidates.append(
            info
        )

    print(
        "[Binance Universe] "
        "candidats Spot/liquides="
        f"{len(candidates)} | "
        f"exclus spécial={excluded_special} | "
        f"exclus base={excluded_base}"
    )

    # ========================================================
    # TICKER 24H
    # ========================================================

    print(
        "[Binance Universe] "
        "ticker 24h..."
    )

    ticker_data, ticker_endpoint = (
        _binance_get(
            "/api/v3/ticker/24hr"
        )
    )

    ticker_by_symbol = {}

    if isinstance(
        ticker_data,
        list,
    ):

        for ticker in ticker_data:

            symbol = str(
                ticker.get(
                    "symbol",
                    "",
                )
            ).upper()

            if symbol:
                ticker_by_symbol[
                    symbol
                ] = ticker

    ticker_missing = 0
    volume_rejected = 0
    trades_rejected = 0
    price_rejected = 0
    spread_rejected = 0
    age_rejected = 0
    volatility_rejected = 0

    preliminary = []

    for info in candidates:

        symbol = str(
            info.get(
                "symbol",
                "",
            )
        ).upper()

        ticker = (
            ticker_by_symbol.get(
                symbol
            )
        )

        if ticker is None:

            ticker_missing += 1
            continue

        quote_volume = _safe_float(
            ticker.get(
                "quoteVolume"
            )
        )

        trade_count = _safe_int(
            ticker.get(
                "count"
            )
        )

        last_price = _safe_float(
            ticker.get(
                "lastPrice"
            )
        )

        bid_price = _safe_float(
            ticker.get(
                "bidPrice"
            )
        )

        ask_price = _safe_float(
            ticker.get(
                "askPrice"
            )
        )

        high_price = _safe_float(
            ticker.get(
                "highPrice"
            )
        )

        low_price = _safe_float(
            ticker.get(
                "lowPrice"
            )
        )

        # Volume
        if (
            quote_volume
            < BINANCE_MIN_QUOTE_VOLUME_24H
        ):

            volume_rejected += 1
            continue

        # Nombre de trades
        if (
            trade_count
            < BINANCE_MIN_TRADES_24H
        ):

            trades_rejected += 1
            continue

        # Prix
        if (
            last_price <= 0
            or bid_price <= 0
            or ask_price <= 0
        ):

            price_rejected += 1
            continue

        midpoint = (
            bid_price
            + ask_price
        ) / 2.0

        if midpoint <= 0:

            price_rejected += 1
            continue

        # Spread
        spread_pct = (
            ask_price
            - bid_price
        ) / midpoint * 100.0

        if (
            spread_pct
            > BINANCE_MAX_SPREAD_PCT
        ):

            spread_rejected += 1
            continue

        # Âge
        age_days = _symbol_age_days(
            info.get(
                "onboardDate"
            )
        )

        if (
            age_days
            < BINANCE_MIN_UNIVERSE_AGE_DAYS
        ):

            age_rejected += 1
            continue

        # Volatilité
        volatility_pct = (
            _calculate_volatility_pct(
                high_price,
                low_price,
            )
        )

        if (
            volatility_pct
            > BINANCE_MAX_UNIVERSE_VOLATILITY_PCT
        ):

            volatility_rejected += 1
            continue

        preliminary.append(
            {
                "info": info,
                "ticker": ticker,
                "symbol": symbol,
                "base_asset": str(
                    info.get(
                        "baseAsset",
                        "",
                    )
                ).upper(),
                "quote_asset": str(
                    info.get(
                        "quoteAsset",
                        "",
                    )
                ).upper(),
                "quote_volume": quote_volume,
                "trade_count": trade_count,
                "last_price": last_price,
                "bid_price": bid_price,
                "ask_price": ask_price,
                "spread_pct": spread_pct,
                "age_days": age_days,
                "volatility_pct": volatility_pct,
            }
        )

    ticker_ok = (
        len(candidates)
        - ticker_missing
    )

    volume_ok = (
        ticker_ok
        - volume_rejected
    )

    trades_ok = (
        volume_ok
        - trades_rejected
    )

    price_ok = (
        trades_ok
        - price_rejected
    )

    spread_ok = (
        price_ok
        - spread_rejected
    )

    age_ok = (
        spread_ok
        - age_rejected
    )

    print(
        "[Binance Universe] "
        "filtres ticker | "
        f"candidats={len(candidates)} | "
        f"ticker OK={ticker_ok} | "
        f"volume OK={volume_ok} | "
        f"trades OK={trades_ok} | "
        f"prix OK={price_ok} | "
        f"spread OK={spread_ok} | "
        f"âge OK={age_ok} | "
        f"volatilité OK={len(preliminary)} | "
        "rejets: "
        f"volume={volume_rejected}, "
        f"trades={trades_rejected}, "
        f"prix={price_rejected}, "
        f"spread={spread_rejected}, "
        f"âge={age_rejected}, "
        f"volatilité={volatility_rejected}, "
        f"ticker_manquant={ticker_missing}"
    )

    # ========================================================
    # PROFONDEUR
    # ========================================================

    print(
        "[Binance Universe] "
        "analyse profondeur carnet: "
        f"{len(preliminary)} paires | "
        f"workers={BINANCE_DEPTH_WORKERS}"
    )

    depth_results: Dict[
        str,
        Dict[str, Any],
    ] = {}

    with ThreadPoolExecutor(
        max_workers=BINANCE_DEPTH_WORKERS
    ) as executor:

        future_map = {
            executor.submit(
                _binance_fetch_depth,
                item["symbol"],
            ): item["symbol"]
            for item in preliminary
        }

        for future in as_completed(
            future_map
        ):

            symbol = future_map[
                future
            ]

            try:

                result = (
                    future.result()
                )

            except Exception as exc:

                result = {
                    "symbol": symbol,
                    "ok": False,
                    "error": str(exc),
                }

            depth_results[
                symbol
            ] = result

    accepted = []
    depth_rejected = []
    depth_errors = []

    for item in preliminary:

        symbol = item[
            "symbol"
        ]

        depth = depth_results.get(
            symbol,
            {
                "ok": False,
                "error": (
                    "missing-depth-result"
                ),
            },
        )

        if not depth.get(
            "ok",
            False,
        ):

            depth_errors.append(
                {
                    **item,
                    "error": depth.get(
                        "error",
                        "unknown",
                    ),
                }
            )

            continue

        depth_010 = _safe_float(
            depth.get(
                "depth_010"
            )
        )

        depth_025 = _safe_float(
            depth.get(
                "depth_025"
            )
        )

        depth_050 = _safe_float(
            depth.get(
                "depth_050"
            )
        )

        liquidity_grade, depth_score = (
            _calculate_liquidity_grade(
                depth_010,
                depth_025,
                depth_050,
            )
        )

        # Rejet dur
        if (
            depth_025
            < BINANCE_MIN_DEPTH_025_NOTIONAL
        ):

            depth_rejected.append(
                {
                    **item,
                    "depth_010": depth_010,
                    "depth_025": depth_025,
                    "depth_050": depth_050,
                    "depth_score": depth_score,
                    "liquidity_grade": "E",
                    "threshold": (
                        BINANCE_MIN_DEPTH_025_NOTIONAL
                    ),
                }
            )

            continue

        # ----------------------------------------------------
        # SCORES
        # ----------------------------------------------------

        liquidity_score = (
            _depth_ratio_score(
                depth_025,
                BINANCE_DEPTH_TARGET_025,
            )
        )

        spread_quality = _clamp(
            1.0
            - (
                item["spread_pct"]
                / BINANCE_MAX_SPREAD_PCT
            )
        )

        trade_liquidity = _clamp(
            math.log1p(
                item["trade_count"]
            )
            / math.log1p(
                100000
            )
        )

        stability = _clamp(
            1.0
            - (
                item["volatility_pct"]
                / BINANCE_MAX_UNIVERSE_VOLATILITY_PCT
            )
        )

        universe_quality = (
            _calculate_universe_quality(
                depth_score,
                liquidity_score,
                spread_quality,
                trade_liquidity,
                stability,
            )
        )

        accepted.append(
            {
                **item,
                "depth_010": depth_010,
                "depth_025": depth_025,
                "depth_050": depth_050,
                "depth_score": depth_score,
                "liquidity_grade": liquidity_grade,
                "liquidity_score": liquidity_score,
                "spread_quality": spread_quality,
                "trade_liquidity": trade_liquidity,
                "stability": stability,
                "universe_quality": universe_quality,
            }
        )

    print(
        "[Binance Universe] "
        f"profondeur OK={len(accepted)} | "
        f"rejetée={len(depth_rejected)} | "
        f"erreurs={len(depth_errors)}"
    )

    # ========================================================
    # DIAGNOSTIC REJETS PROFONDEUR
    # ========================================================

    if depth_rejected:

        print(
            "[Binance Universe] "
            "détail rejets profondeur:"
        )

        for item in sorted(
            depth_rejected,
            key=lambda x: x[
                "depth_025"
            ],
        ):

            print(
                "  - "
                f"{item['symbol']}: "
                f"±0.10=${item['depth_010']:,.0f}, "
                f"±0.25=${item['depth_025']:,.0f}, "
                f"±0.50=${item['depth_050']:,.0f}, "
                f"vol24h=${item['quote_volume']:,.0f}, "
                f"spread={item['spread_pct']:.3f}%, "
                f"volatilité={item['volatility_pct']:.2f}%, "
                f"seuil=${item['threshold']:,.0f}"
            )

    # ========================================================
    # ERREURS PROFONDEUR
    # ========================================================

    if depth_errors:

        print(
            "[Binance Universe] "
            "erreurs profondeur:"
        )

        for item in depth_errors:

            print(
                "  - "
                f"{item['symbol']}: "
                f"{item.get('error', 'unknown')}"
            )

    # ========================================================
    # TRI QUALITÉ
    # ========================================================

    accepted.sort(
        key=lambda x: (
            x["universe_quality"],
            x["depth_score"],
            x["quote_volume"],
        ),
        reverse=True,
    )

    # ========================================================
    # DÉDUPLICATION PAR BASE ASSET
    # ========================================================

    before_dedup = len(
        accepted
    )

    quote_rank = {
        quote: index
        for index, quote in enumerate(
            BINANCE_QUOTE_PRIORITY
        )
    }

    best_by_base: Dict[
        str,
        Dict[str, Any],
    ] = {}

    for item in accepted:

        base = item[
            "base_asset"
        ]

        current = best_by_base.get(
            base
        )

        if current is None:

            best_by_base[
                base
            ] = item

            continue

        current_rank = quote_rank.get(
            current[
                "quote_asset"
            ],
            999,
        )

        new_rank = quote_rank.get(
            item[
                "quote_asset"
            ],
            999,
        )

        if new_rank < current_rank:

            best_by_base[
                base
            ] = item

        elif (
            new_rank == current_rank
            and item[
                "universe_quality"
            ]
            > current[
                "universe_quality"
            ]
        ):

            best_by_base[
                base
            ] = item

    accepted = list(
        best_by_base.values()
    )

    accepted.sort(
        key=lambda x: (
            x["universe_quality"],
            x["depth_score"],
            x["quote_volume"],
        ),
        reverse=True,
    )

    print(
        "[Binance Universe] "
        "déduplication base_asset | "
        f"avant={before_dedup} | "
        f"après={len(accepted)} | "
        f"paires supprimées="
        f"{before_dedup - len(accepted)}"
    )

    # ========================================================
    # CAP OPTIONNEL
    # ========================================================

    if BINANCE_MAX_UNIVERSE > 0:

        selected = accepted[
            :BINANCE_MAX_UNIVERSE
        ]

    else:

        selected = accepted

    # ========================================================
    # CONSTRUCTION UNIVERS
    # ========================================================

    new_symbols = {
        item[
            "symbol"
        ]
        for item in selected
    }

    new_metadata: Dict[
        str,
        Dict[str, Any],
    ] = {}

    for item in selected:

        symbol = item[
            "symbol"
        ]

        new_metadata[
            symbol
        ] = {
            "binance": symbol,
            "base_asset": item[
                "base_asset"
            ],
            "quote_asset": item[
                "quote_asset"
            ],
            "quote_volume_24h": item[
                "quote_volume"
            ],
            "trade_count_24h": item[
                "trade_count"
            ],
            "spread_pct": item[
                "spread_pct"
            ],
            "volatility_pct": item[
                "volatility_pct"
            ],
            "age_days": item[
                "age_days"
            ],
            "depth_010": item[
                "depth_010"
            ],
            "depth_025": item[
                "depth_025"
            ],
            "depth_050": item[
                "depth_050"
            ],
            "depth_score": item[
                "depth_score"
            ],
            "liquidity_grade": item[
                "liquidity_grade"
            ],
            "liquidity_score": item[
                "liquidity_score"
            ],
            "universe_quality": item[
                "universe_quality"
            ],
        }

    with _UNIVERSE_LOCK:

        _DYNAMIC_CRYPTO_SYMBOLS = (
            new_symbols
        )

        _DYNAMIC_CRYPTO_METADATA = (
            new_metadata
        )

        CRYPTO = sorted(
            _DYNAMIC_CRYPTO_SYMBOLS
        )

    print(
        "[Binance Universe] "
        f"ticker_endpoint={ticker_endpoint} | "
        f"sélection finale={len(CRYPTO)} "
        f"sur {len(accepted)} "
        f"actifs admissibles "
        f"({before_dedup} paires)"
    )

    # ========================================================
    # CLASSEMENT COMPLET
    # ========================================================

    print(
        "[Binance Universe] "
        f"classement liquidité complet "
        f"({len(selected)} actifs):"
    )

    for index, item in enumerate(
        selected,
        start=1,
    ):

        print(
            f"  {index:03d}. "
            f"{item['symbol']} | "
            f"grade={item['liquidity_grade']} | "
            f"quality={item['universe_quality']:.3f} | "
            f"depth_score={item['depth_score']:.3f} | "
            f"vol24h=${item['quote_volume']:,.0f} | "
            f"spread={item['spread_pct']:.3f}% | "
            f"vol={item['volatility_pct']:.2f}% | "
            f"±0.10=${item['depth_010']:,.0f} | "
            f"±0.25=${item['depth_025']:,.0f} | "
            f"±0.50=${item['depth_050']:,.0f}"
        )

    # ========================================================
    # DISTRIBUTION DES GRADES
    # ========================================================

    grade_counts = {
        "A": 0,
        "B": 0,
        "C": 0,
        "D": 0,
        "E": 0,
    }

    for item in selected:

        grade = item[
            "liquidity_grade"
        ]

        if grade in grade_counts:

            grade_counts[
                grade
            ] += 1

    print(
        "[Binance Universe] "
        "distribution liquidité | "
        f"A={grade_counts['A']} | "
        f"B={grade_counts['B']} | "
        f"C={grade_counts['C']} | "
        f"D={grade_counts['D']} | "
        f"E={grade_counts['E']}"
    )

    return CRYPTO


# ============================================================
# INITIALISATION AUTOMATIQUE
# ============================================================

def ensure_binance_universe() -> List[str]:

    with _UNIVERSE_LOCK:

        if _DYNAMIC_CRYPTO_SYMBOLS:

            return list(
                CRYPTO
            )

    return refresh_binance_universe()


# ============================================================
# MÉTADONNÉES BINANCE
# ============================================================

def get_binance_metadata(
    symbol: str,
) -> Dict[str, Any]:

    return _DYNAMIC_CRYPTO_METADATA.get(
        symbol,
        {},
    )


# ============================================================
# UNIVERS GLOBAL
# ============================================================

def get_all_assets() -> List[str]:

    crypto = (
        ensure_binance_universe()
    )

    return list(
        dict.fromkeys(
            STOCKS
            + ETF
            + INDICES
            + FOREX
            + crypto
        )
    )


# ============================================================
# TYPE D'ACTIF
# ============================================================

def get_asset_type(
    symbol: str,
) -> str:

    if symbol in CRYPTO:
        return "crypto"

    if symbol in STOCKS:
        return "stock"

    if symbol in ETF:
        return "etf"

    if symbol in INDICES:
        return "index"

    if symbol in FOREX:
        return "forex"

    return "unknown"


# ============================================================
# MAPPING FOURNISSEURS
# ============================================================

def get_provider_symbol(
    symbol: str,
    provider: str,
) -> str:

    asset_type = get_asset_type(
        symbol
    )

    provider = (
        provider
        .lower()
        .strip()
    )

    if asset_type == "crypto":

        metadata = (
            get_binance_metadata(
                symbol
            )
        )

        if provider == "binance":

            return metadata.get(
                "binance",
                symbol,
            )

        if provider == "yahoo":

            if symbol.endswith(
                "USDT"
            ):

                base = symbol[:-4]

                return (
                    f"{base}-USD"
                )

            if symbol.endswith(
                "USDC"
            ):

                base = symbol[:-4]

                return (
                    f"{base}-USD"
                )

            return symbol

        return symbol

    # Les symboles traditionnels
    # restent inchangés.
    return symbol
