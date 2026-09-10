"""
V4.2 — Routeur intelligent multi-sources.

Sources :
    Binance
        Crypto — source principale et unique.

    Finnhub
        Actions — priorité élevée.
        Forex — tentative lorsque disponible.
        Volume actions conservé lorsqu'il est fourni.

    Yahoo Finance
        Actions
        Indices
        Futures / matières premières
        Forex de secours.

    Twelve Data
        Source de secours intelligente.
        Budget quotidien partagé.

Principes V4.2 :
    - aucune dépendance à backtest.py ;
    - choix dynamique des fournisseurs ;
    - fallback automatique ;
    - cache des requêtes identiques ;
    - volume absent = NaN, jamais 0 ;
    - couverture volume >= 80 % = volume exploitable ;
    - agrégation correcte des bougies 1H -> 4H ;
    - exclusion des bougies incomplètes ;
    - pas de retry inutile sur Twelve Data ;
    - compteur Twelve Data partagé entre toutes les instances ;
    - diagnostic du fournisseur réellement utilisé.
"""

import os
import threading
from datetime import datetime, timezone

import pandas as pd
import requests
from dotenv import load_dotenv


load_dotenv()


# ======================================================================
# CONFIGURATION
# ======================================================================

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")

# Binance public market-data API
BINANCE_DATA_URL = os.getenv(
    "BINANCE_DATA_URL",
    "https://data-api.binance.vision",
).rstrip("/")

# Alias de compatibilité éventuelle
BINANCE_URL = BINANCE_DATA_URL

YAHOO_BASE = (
    "https://query1.finance.yahoo.com/v8/finance/chart/"
)

TWELVE_DATA_BASE = (
    "https://api.twelvedata.com/time_series"
)

FINNHUB_CANDLE_BASE = (
    "https://finnhub.io/api/v1/stock/candle"
)

FINNHUB_FOREX_CANDLE_BASE = (
    "https://finnhub.io/api/v1/forex/candle"
)

REQUEST_TIMEOUT = 30

VOLUME_MIN_COVERAGE = float(
    os.getenv("VOLUME_MIN_COVERAGE", "0.80")
)

TWELVE_DATA_DAILY_BUDGET = int(
    os.getenv(
        "TWELVE_DATA_DAILY_BUDGET",
        os.getenv(
            "TWELVE_DATA_DAILY_LIMIT",
            "800",
        ),
    )
)

# Alias de compatibilité
TWELVE_DATA_DAILY_LIMIT = TWELVE_DATA_DAILY_BUDGET


# ======================================================================
# SESSION HTTP
# ======================================================================

_SESSION = requests.Session()

_SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(compatible; crypto-scanner-v4.2/1.0)"
        )
    }
)


# ======================================================================
# STRUCTURE STANDARD
# ======================================================================

OHLCV_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
]


class DataSourceError(RuntimeError):
    """Erreur standard du routeur de données."""


# ======================================================================
# CACHE
# ======================================================================

_CACHE = {}
_CACHE_LOCK = threading.Lock()


def _cache_key(
    provider,
    symbol,
    interval,
    limit,
    asset_type,
):
    return (
        str(provider).lower(),
        str(symbol),
        str(interval).lower(),
        int(limit),
        str(asset_type).lower(),
    )


def _cache_get(key):
    with _CACHE_LOCK:
        value = _CACHE.get(key)

        if value is None:
            return None

        return value.copy(deep=True)


def _cache_set(key, df):
    with _CACHE_LOCK:
        _CACHE[key] = df.copy(deep=True)


def clear_cache():
    with _CACHE_LOCK:
        _CACHE.clear()


# ======================================================================
# STATISTIQUES
# ======================================================================

_ROUTER_STATS = {
    "requests": 0,
    "success": 0,
    "errors": 0,
    "cache_hits": 0,
    "binance": 0,
    "finnhub": 0,
    "yahoo": 0,
    "twelvedata": 0,
    "fallbacks": 0,
}

_STATS_LOCK = threading.Lock()


def _stats_increment(key, amount=1):
    with _STATS_LOCK:
        _ROUTER_STATS[key] = (
            _ROUTER_STATS.get(key, 0)
            + amount
        )


# ======================================================================
# BUDGET TWELVE DATA
# ======================================================================

class _TwelveDataBudget:
    """
    Compteur Twelve Data partagé au niveau du processus.

    Toutes les instances de DataRouter utilisent le même compteur.
    """

    used = 0
    day = None
    lock = threading.Lock()

    @classmethod
    def _reset_if_needed(cls):
        today = datetime.now(
            timezone.utc
        ).date()

        if cls.day != today:
            cls.day = today
            cls.used = 0

    @classmethod
    def reserve(cls):
        with cls.lock:
            cls._reset_if_needed()

            if cls.used >= TWELVE_DATA_DAILY_BUDGET:
                return False

            cls.used += 1
            return True

    @classmethod
    def used_today(cls):
        with cls.lock:
            cls._reset_if_needed()
            return cls.used

    @classmethod
    def remaining(cls):
        with cls.lock:
            cls._reset_if_needed()

            return max(
                0,
                TWELVE_DATA_DAILY_BUDGET - cls.used,
            )


# ======================================================================
# STATISTIQUES PUBLIQUES
# ======================================================================

def router_stats():
    with _STATS_LOCK:
        stats = dict(_ROUTER_STATS)

    stats.update(
        {
            "twelvedata_used":
                _TwelveDataBudget.used_today(),

            "twelvedata_remaining":
                _TwelveDataBudget.remaining(),

            "twelvedata_budget":
                TWELVE_DATA_DAILY_BUDGET,
        }
    )

    return stats


def get_router_stats():
    return router_stats()


# ======================================================================
# UTILITAIRES
# ======================================================================

def normalize_interval(interval):
    return str(
        interval
    ).strip().lower()


def interval_to_seconds(interval):
    value = normalize_interval(interval)

    if value.endswith("m"):
        return int(value[:-1]) * 60

    if value.endswith("h"):
        return int(value[:-1]) * 3600

    if value.endswith("d"):
        return int(value[:-1]) * 86400

    if value.endswith("w"):
        return int(value[:-1]) * 604800

    return 60


def empty_ohlcv():
    return pd.DataFrame(
        columns=OHLCV_COLUMNS
    )


def ensure_ohlcv(df):
    """
    Normalise systématiquement la structure OHLCV.
    """

    if df is None:
        return empty_ohlcv()

    d = df.copy()

    for column in OHLCV_COLUMNS:
        if column not in d.columns:
            d[column] = float("nan")

    d["open_time"] = pd.to_datetime(
        d["open_time"],
        utc=True,
        errors="coerce",
    )

    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:
        d[column] = pd.to_numeric(
            d[column],
            errors="coerce",
        )

    d = d.dropna(
        subset=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
        ]
    )

    d = (
        d.sort_values("open_time")
        .drop_duplicates(
            subset=["open_time"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    return d[OHLCV_COLUMNS]


def volume_coverage(df):
    """
    Pourcentage de bougies disposant d'un volume réel.

    Un volume manquant reste NaN.
    """

    if df is None or df.empty:
        return 0.0

    if "volume" not in df.columns:
        return 0.0

    volume = pd.to_numeric(
        df["volume"],
        errors="coerce",
    )

    return float(
        volume.notna().mean()
    )


def volume_status(df):
    coverage = volume_coverage(df)

    if coverage >= VOLUME_MIN_COVERAGE:
        return "VOLUME_CONFIRMED"

    if coverage > 0:
        return "VOLUME_PARTIAL"

    return "VOLUME_UNAVAILABLE"


def attach_metadata(
    df,
    provider,
    provider_symbol=None,
    asset_type=None,
):
    d = ensure_ohlcv(df)

    status = volume_status(d)

    d.attrs["provider"] = provider

    d.attrs["provider_symbol"] = (
        provider_symbol
        if provider_symbol is not None
        else ""
    )

    d.attrs["asset_type"] = (
        asset_type
        if asset_type is not None
        else ""
    )

    d.attrs["volume_coverage"] = (
        volume_coverage(d)
    )

    d.attrs["volume_status"] = status

    d.attrs["volume_available"] = (
        status == "VOLUME_CONFIRMED"
    )

    if not d.empty:
        last = pd.Timestamp(
            d["open_time"].iloc[-1]
        )

        now = pd.Timestamp.now(
            tz="UTC"
        )

        d.attrs["last_candle"] = last

        d.attrs["age_minutes"] = (
            now - last
        ).total_seconds() / 60.0

    else:
        d.attrs["last_candle"] = None
        d.attrs["age_minutes"] = None

    return d


def keep_completed_candles(
    df,
    interval,
):
    """
    Supprime la bougie actuellement en formation.
    """

    if df is None or df.empty:
        return empty_ohlcv()

    d = ensure_ohlcv(df)

    if d.empty:
        return d

    seconds = interval_to_seconds(
        interval
    )

    now = pd.Timestamp.now(
        tz="UTC"
    )

    completed = (
        (
            now - d["open_time"]
        ).dt.total_seconds()
        >= seconds
    )

    return (
        d.loc[completed]
        .copy()
        .reset_index(drop=True)
    )


def _completed_limit(
    limit,
    interval,
):
    limit = max(
        1,
        int(limit),
    )

    interval = normalize_interval(
        interval
    )

    if interval == "4h":
        return min(
            5000,
            limit * 4 + 24,
        )

    if interval == "2h":
        return min(
            5000,
            limit * 2 + 20,
        )

    return min(
        5000,
        limit + 20,
    )


# ======================================================================
# AGRÉGATION OHLCV
# ======================================================================

def aggregate_ohlcv(
    df,
    target_interval,
):
    """
    Agrégation OHLCV.

    Le volume agrégé est conservé uniquement si toutes les
    bougies composantes disposent d'un volume réel.
    """

    if df is None or df.empty:
        return empty_ohlcv()

    d = ensure_ohlcv(df)

    if d.empty:
        return d

    target = normalize_interval(
        target_interval
    )

    if target == "4h":
        rule = "4h"
        expected_children = 4

    elif target == "2h":
        rule = "2h"
        expected_children = 2

    elif target == "1h":
        return d

    else:
        raise ValueError(
            "Intervalle d'agrégation non supporté : "
            f"{target_interval}"
        )

    x = d.set_index(
        "open_time"
    )

    grouped = x[
        [
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    ].resample(
        rule,
        label="left",
        closed="left",
    )

    result = grouped.agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
        }
    )

    volume_sum = grouped[
        "volume"
    ].sum(
        min_count=1
    )

    volume_count = grouped[
        "volume"
    ].count()

    result["volume"] = volume_sum.where(
        volume_count == expected_children
    )

    result = (
        result.dropna(
            subset=[
                "open",
                "high",
                "low",
                "close",
            ]
        )
        .reset_index()
    )

    result = keep_completed_candles(
        result,
        target,
    )

    return ensure_ohlcv(
        result
    )


# ======================================================================
# BINANCE
# ======================================================================

_BINANCE_INTERVALS = {
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "6h",
    "8h",
    "12h",
    "1d",
    "3d",
    "1w",
}


def _normalize_binance_symbol(symbol):
    value = str(symbol).strip().upper()

    value = value.replace(
        "/",
        "",
    )

    value = value.replace(
        "-",
        "",
    )

    return value


def _binance_raw(
    symbol,
    interval,
    limit,
):
    interval = normalize_interval(
        interval
    )

    if interval not in _BINANCE_INTERVALS:
        raise DataSourceError(
            f"Intervalle Binance non supporté : "
            f"{interval}"
        )

    symbol = _normalize_binance_symbol(
        symbol
    )

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": min(
            max(1, int(limit)),
            1000,
        ),
    }

    url = (
        BINANCE_DATA_URL
        + "/api/v3/klines"
    )

    response = _SESSION.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code == 429:
        raise DataSourceError(
            f"Binance HTTP 429 pour {symbol}"
        )

    response.raise_for_status()

    payload = response.json()

    if not isinstance(
        payload,
        list,
    ):
        raise DataSourceError(
            f"Binance réponse invalide pour {symbol}: "
            f"{payload}"
        )

    if not payload:
        return empty_ohlcv()

    rows = []

    for row in payload:
        if not isinstance(
            row,
            (list, tuple),
        ):
            continue

        if len(row) < 6:
            continue

        rows.append(
            {
                "open_time":
                    pd.to_datetime(
                        row[0],
                        unit="ms",
                        utc=True,
                        errors="coerce",
                    ),
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5],
            }
        )

    return ensure_ohlcv(
        pd.DataFrame(rows)
    )


def klines_binance_v42(
    symbol,
    interval="15m",
    limit=1000,
    asset_type="crypto",
):
    key = _cache_key(
        "binance",
        symbol,
        interval,
        limit,
        asset_type,
    )

    cached = _cache_get(key)

    if cached is not None:
        _stats_increment(
            "cache_hits"
        )

        return attach_metadata(
            cached,
            "binance",
            symbol,
            asset_type,
        )

    raw_limit = _completed_limit(
        limit,
        interval,
    )

    df = _binance_raw(
        symbol,
        interval,
        raw_limit,
    )

    df = keep_completed_candles(
        df,
        interval,
    )

    df = (
        ensure_ohlcv(df)
        .tail(int(limit))
        .reset_index(drop=True)
    )

    if df.empty:
        raise DataSourceError(
            f"Binance : aucune donnée exploitable "
            f"pour {symbol}"
        )

    _cache_set(
        key,
        df,
    )

    return attach_metadata(
        df,
        "binance",
        symbol,
        asset_type,
    )


# ======================================================================
# YAHOO FINANCE
# ======================================================================

_YAHOO_CONFIG = {
    "1m": {
        "range": "7d",
        "interval": "1m",
    },
    "5m": {
        "range": "60d",
        "interval": "5m",
    },
    "15m": {
        "range": "60d",
        "interval": "15m",
    },
    "30m": {
        "range": "60d",
        "interval": "30m",
    },
    "1h": {
        "range": "730d",
        "interval": "1h",
    },
    "1d": {
        "range": "10y",
        "interval": "1d",
    },
}


def _yahoo_raw(
    symbol,
    interval,
):
    requested = normalize_interval(
        interval
    )

    yahoo_interval = (
        "1h"
        if requested in {
            "2h",
            "4h",
        }
        else requested
    )

    if yahoo_interval not in _YAHOO_CONFIG:
        raise DataSourceError(
            f"Intervalle Yahoo non supporté : "
            f"{requested}"
        )

    config = _YAHOO_CONFIG[
        yahoo_interval
    ]

    url = (
        YAHOO_BASE
        + requests.utils.quote(
            str(symbol),
            safe="",
        )
    )

    params = {
        "range": config["range"],
        "interval": config["interval"],
        "includePrePost": "false",
        "events": "div,splits",
    }

    response = _SESSION.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code == 429:
        raise DataSourceError(
            f"Yahoo HTTP 429 pour {symbol}"
        )

    response.raise_for_status()

    payload = response.json()

    chart = payload.get(
        "chart",
        {},
    )

    results = chart.get(
        "result"
    )

    if not results:
        raise DataSourceError(
            f"Yahoo : aucune donnée pour "
            f"{symbol}"
        )

    result = results[0]

    timestamps = result.get(
        "timestamp",
        [],
    )

    quote_list = (
        result
        .get(
            "indicators",
            {},
        )
        .get(
            "quote",
            [],
        )
    )

    if not timestamps or not quote_list:
        return empty_ohlcv()

    quote = quote_list[0]

    df = pd.DataFrame(
        {
            "open_time": pd.to_datetime(
                timestamps,
                unit="s",
                utc=True,
                errors="coerce",
            ),
            "open": quote.get(
                "open",
                [],
            ),
            "high": quote.get(
                "high",
                [],
            ),
            "low": quote.get(
                "low",
                [],
            ),
            "close": quote.get(
                "close",
                [],
            ),
            "volume": quote.get(
                "volume",
                [],
            ),
        }
    )

    return ensure_ohlcv(
        df
    )


def klines_yahoo(
    symbol,
    interval="15m",
    limit=1000,
    asset_type="stock",
):
    interval = normalize_interval(
        interval
    )

    key = _cache_key(
        "yahoo",
        symbol,
        interval,
        limit,
        asset_type,
    )

    cached = _cache_get(key)

    if cached is not None:
        _stats_increment(
            "cache_hits"
        )

        return attach_metadata(
            cached,
            "yahoo",
            symbol,
            asset_type,
        )

    df = _yahoo_raw(
        symbol,
        interval,
    )

    if interval == "4h":
        df = aggregate_ohlcv(
            df,
            "4h",
        )

    elif interval == "2h":
        df = aggregate_ohlcv(
            df,
            "2h",
        )

    else:
        df = keep_completed_candles(
            df,
            interval,
        )

    df = (
        ensure_ohlcv(df)
        .tail(int(limit))
        .reset_index(drop=True)
    )

    if df.empty:
        raise DataSourceError(
            f"Yahoo : aucune donnée exploitable "
            f"pour {symbol}"
        )

    _cache_set(
        key,
        df,
    )

    return attach_metadata(
        df,
        "yahoo",
        symbol,
        asset_type,
    )


# ======================================================================
# FINNHUB
# ======================================================================

_FINNHUB_RESOLUTION = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "1d": "D",
    "1w": "W",
}


def _finnhub_period(
    interval,
    limit,
):
    seconds = interval_to_seconds(
        interval
    )

    now = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    total = (
        max(100, int(limit))
        * seconds
        * 3
    )

    return (
        now - total,
        now,
    )


def _finnhub_raw(
    symbol,
    interval,
    limit,
    asset_type="stock",
):
    if not FINNHUB_API_KEY:
        raise DataSourceError(
            "FINNHUB_API_KEY absente."
        )

    requested = normalize_interval(
        interval
    )

    if requested in {
        "2h",
        "4h",
    }:
        resolution = "60"
        raw_interval = "1h"
    else:
        resolution = _FINNHUB_RESOLUTION.get(
            requested
        )

        if resolution is None:
            raise DataSourceError(
                f"Intervalle Finnhub non supporté : "
                f"{requested}"
            )

        raw_interval = requested

    start, end = _finnhub_period(
        raw_interval,
        _completed_limit(
            limit,
            requested,
        ),
    )

    asset = str(
        asset_type
    ).lower()

    if asset == "forex":
        endpoint = (
            FINNHUB_FOREX_CANDLE_BASE
        )
    else:
        endpoint = (
            FINNHUB_CANDLE_BASE
        )

    params = {
        "symbol": symbol,
        "resolution": resolution,
        "from": start,
        "to": end,
        "token": FINNHUB_API_KEY,
    }

    response = _SESSION.get(
        endpoint,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code == 429:
        raise DataSourceError(
            f"Finnhub HTTP 429 pour {symbol}"
        )

    response.raise_for_status()

    payload = response.json()

    status = str(
        payload.get(
            "s",
            "",
        )
    ).lower()

    if status != "ok":
        raise DataSourceError(
            f"Finnhub : aucune donnée pour "
            f"{symbol} : "
            f"{payload}"
        )

    timestamps = payload.get(
        "t",
        [],
    )

    opens = payload.get(
        "o",
        [],
    )

    highs = payload.get(
        "h",
        [],
    )

    lows = payload.get(
        "l",
        [],
    )

    closes = payload.get(
        "c",
        [],
    )

    volumes = payload.get(
        "v",
        [],
    )

    if not timestamps:
        return empty_ohlcv()

    df = pd.DataFrame(
        {
            "open_time": pd.to_datetime(
                timestamps,
                unit="s",
                utc=True,
                errors="coerce",
            ),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )

    if len(volumes) != len(
        timestamps
    ):
        df["volume"] = float("nan")

    return ensure_ohlcv(
        df
    )


def klines_finnhub(
    symbol,
    interval="15m",
    limit=1000,
    asset_type="stock",
):
    interval = normalize_interval(
        interval
    )

    key = _cache_key(
        "finnhub",
        symbol,
        interval,
        limit,
        asset_type,
    )

    cached = _cache_get(key)

    if cached is not None:
        _stats_increment(
            "cache_hits"
        )

        return attach_metadata(
            cached,
            "finnhub",
            symbol,
            asset_type,
        )

    raw_limit = _completed_limit(
        limit,
        interval,
    )

    df = _finnhub_raw(
        symbol,
        interval,
        raw_limit,
        asset_type,
    )

    if interval == "4h":
        df = aggregate_ohlcv(
            df,
            "4h",
        )

    elif interval == "2h":
        df = aggregate_ohlcv(
            df,
            "2h",
        )

    else:
        df = keep_completed_candles(
            df,
            interval,
        )

    df = (
        ensure_ohlcv(df)
        .tail(int(limit))
        .reset_index(drop=True)
    )

    if df.empty:
        raise DataSourceError(
            f"Finnhub : aucune donnée exploitable "
            f"pour {symbol}"
        )

    _cache_set(
        key,
        df,
    )

    return attach_metadata(
        df,
        "finnhub",
        symbol,
        asset_type,
    )


# ======================================================================
# TWELVE DATA
# ======================================================================

_TD_INTERVALS = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1day",
}


def _twelvedata_error_message(
    payload,
):
    message = payload.get(
        "message"
    )

    if message:
        return str(message)

    code = payload.get(
        "code"
    )

    if code:
        return f"code={code}"

    return "Erreur Twelve Data"


def klines_twelvedata(
    symbol,
    interval="15m",
    limit=1000,
    asset_type="stock",
):
    if not TWELVE_DATA_API_KEY:
        raise DataSourceError(
            "TWELVE_DATA_API_KEY absente."
        )

    interval = normalize_interval(
        interval
    )

    td_interval = _TD_INTERVALS.get(
        interval
    )

    # IMPORTANT :
    # vérifier l'intervalle AVANT de réserver un crédit.
    if td_interval is None:
        raise DataSourceError(
            f"Intervalle Twelve Data non supporté : "
            f"{interval}"
        )

    key = _cache_key(
        "twelvedata",
        symbol,
        interval,
        limit,
        asset_type,
    )

    cached = _cache_get(key)

    if cached is not None:
        _stats_increment(
            "cache_hits"
        )

        return attach_metadata(
            cached,
            "twelvedata",
            symbol,
            asset_type,
        )

    # Une réservation = une véritable requête API.
    if not _TwelveDataBudget.reserve():
        raise DataSourceError(
            "Budget Twelve Data quotidien atteint "
            f"({TWELVE_DATA_DAILY_BUDGET} appels)."
        )

    outputsize = min(
        max(1, int(limit)),
        5000,
    )

    params = {
        "symbol": symbol,
        "interval": td_interval,
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_API_KEY,
        "format": "JSON",
        "timezone": "UTC",
    }

    response = _SESSION.get(
        TWELVE_DATA_BASE,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code == 429:
        raise DataSourceError(
            f"Twelve Data HTTP 429 pour {symbol}"
        )

    response.raise_for_status()

    payload = response.json()

    if payload.get(
        "status"
    ) == "error":
        raise DataSourceError(
            "Twelve Data : "
            + _twelvedata_error_message(
                payload
            )
        )

    values = payload.get(
        "values"
    )

    if not values:
        raise DataSourceError(
            f"Twelve Data : aucune donnée pour "
            f"{symbol}"
        )

    df = pd.DataFrame(
        values
    )

    if "datetime" not in df.columns:
        raise DataSourceError(
            "Twelve Data : colonne datetime absente."
        )

    df["open_time"] = pd.to_datetime(
        df["datetime"],
        utc=True,
        errors="coerce",
    )

    for column in [
        "open",
        "high",
        "low",
        "close",
    ]:
        if column not in df.columns:
            raise DataSourceError(
                "Twelve Data : colonne "
                f"{column} absente."
            )

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(
            df["volume"],
            errors="coerce",
        )
    else:
        # JAMAIS 0.
        df["volume"] = float("nan")

    df = ensure_ohlcv(
        df
    )

    df = keep_completed_candles(
        df,
        interval,
    )

    df = (
        df.tail(int(limit))
        .reset_index(drop=True)
    )

    if df.empty:
        raise DataSourceError(
            f"Twelve Data : aucune bougie complète "
            f"pour {symbol}"
        )

    _cache_set(
        key,
        df,
    )

    return attach_metadata(
        df,
        "twelvedata",
        symbol,
        asset_type,
    )


# ======================================================================
# RÉSOLUTION DES SYMBOLES
# ======================================================================

def _resolve_provider_symbol(
    symbol,
    provider,
    symbol_map=None,
):
    """
    Formats supportés :

        {"yahoo": "AAPL", "finnhub": "AAPL"}

        {"AAPL": {
            "yahoo": "AAPL",
            "finnhub": "AAPL"
        }}

        {"AAPL": "AAPL"}

        [{"yahoo": "AAPL", ...}]
    """

    provider = str(
        provider
    ).lower()

    if symbol_map is None:
        return symbol

    if isinstance(
        symbol_map,
        dict,
    ):
        # Mapping direct fournisseur -> symbole
        direct = symbol_map.get(
            provider
        )

        if direct:
            return str(direct)

        # Alias possibles
        aliases = {
            "twelvedata": [
                "twelve_data",
                "td",
            ],
            "finnhub": [
                "fh",
            ],
            "yahoo": [
                "yf",
            ],
        }

        for alias in aliases.get(
            provider,
            [],
        ):
            value = symbol_map.get(
                alias
            )

            if value:
                return str(value)

        # Mapping symbole -> fournisseur
        nested = symbol_map.get(
            symbol
        )

        if isinstance(
            nested,
            dict,
        ):
            value = nested.get(
                provider
            )

            if value:
                return str(value)

            for alias in aliases.get(
                provider,
                [],
            ):
                value = nested.get(
                    alias
                )

                if value:
                    return str(value)

        elif isinstance(
            nested,
            str,
        ):
            return nested

    if isinstance(
        symbol_map,
        (list, tuple),
    ):
        for entry in symbol_map:
            if not isinstance(
                entry,
                dict,
            ):
                continue

            candidates = {
                entry.get("display"),
                entry.get("symbol"),
                entry.get("yahoo"),
                entry.get("finnhub"),
                entry.get("twelvedata"),
                entry.get("twelve_data"),
                entry.get("binance"),
            }

            if symbol not in candidates:
                continue

            value = entry.get(
                provider
            )

            if not value:
                value = entry.get(
                    {
                        "twelvedata":
                            "twelve_data"
                    }.get(
                        provider,
                        provider,
                    )
                )

            if value:
                return str(value)

    return symbol


# ======================================================================
# CAPACITÉS FOURNISSEURS
# ======================================================================

def _provider_order(
    asset_type,
    require_volume=False,
):
    asset = str(
        asset_type
    ).lower()

    if asset == "crypto":
        return [
            "binance",
        ]

    if asset == "stock":
        return [
            "finnhub",
            "yahoo",
            "twelvedata",
        ]

    if asset == "forex":
        return [
            "finnhub",
            "yahoo",
            "twelvedata",
        ]

    if asset == "index":
        return [
            "yahoo",
            "finnhub",
            "twelvedata",
        ]

    if asset == "commodity":
        return [
            "yahoo",
            "finnhub",
            "twelvedata",
        ]

    return [
        "finnhub",
        "yahoo",
        "twelvedata",
    ]


def _provider_supports_interval(
    provider,
    interval,
):
    provider = str(
        provider
    ).lower()

    interval = normalize_interval(
        interval
    )

    if provider == "binance":
        return interval in _BINANCE_INTERVALS

    if provider == "yahoo":
        return interval in {
            "1m",
            "5m",
            "15m",
            "30m",
            "1h",
            "2h",
            "4h",
            "1d",
        }

    if provider == "finnhub":
        return interval in {
            "1m",
            "5m",
            "15m",
            "30m",
            "1h",
            "2h",
            "4h",
            "1d",
        }

    if provider == "twelvedata":
        return interval in {
            "1m",
            "5m",
            "15m",
            "30m",
            "1h",
            "4h",
            "1d",
        }

    return False


def _provider_function(
    provider,
):
    provider = str(
        provider
    ).lower()

    if provider == "binance":
        return klines_binance_v42

    if provider == "finnhub":
        return klines_finnhub

    if provider == "yahoo":
        return klines_yahoo

    if provider == "twelvedata":
        return klines_twelvedata

    raise ValueError(
        f"Fournisseur inconnu : {provider}"
    )


# ======================================================================
# FRAÎCHEUR
# ======================================================================

def _freshness_score(
    df,
    interval,
):
    if df is None or df.empty:
        return 0.0

    last = pd.to_datetime(
        df["open_time"].iloc[-1],
        utc=True,
        errors="coerce",
    )

    if pd.isna(last):
        return 0.0

    now = pd.Timestamp.now(
        tz="UTC"
    )

    age_seconds = max(
        0.0,
        (
            now - last
        ).total_seconds(),
    )

    interval_seconds = max(
        60,
        interval_to_seconds(
            interval
        ),
    )

    ratio = (
        age_seconds
        / interval_seconds
    )

    if ratio <= 1.5:
        return 1.0

    if ratio <= 3:
        return 0.8

    if ratio <= 6:
        return 0.5

    if ratio <= 12:
        return 0.2

    return 0.0


# ======================================================================
# SCORE SOURCE
# ======================================================================

def _source_score(
    provider,
    df,
    asset_type,
    interval,
    require_volume,
):
    if df is None or df.empty:
        return -999.0

    coverage = volume_coverage(
        df
    )

    freshness = _freshness_score(
        df,
        interval,
    )

    score = 0.0

    # Fraîcheur
    score += (
        freshness * 50.0
    )

    # Volume
    if coverage >= VOLUME_MIN_COVERAGE:
        score += 30.0

    elif coverage > 0:
        score += (
            10.0 * coverage
        )

    if require_volume:
        if coverage < VOLUME_MIN_COVERAGE:
            score -= 40.0

    # Nombre de bougies
    count = len(df)

    if count >= 120:
        score += 15.0

    elif count >= 60:
        score += 8.0

    elif count >= 30:
        score += 3.0

    # Bonus catégorie
    provider = str(
        provider
    ).lower()

    asset = str(
        asset_type
    ).lower()

    if asset == "crypto" and provider == "binance":
        score += 100.0

    elif asset == "stock" and provider == "finnhub":
        score += 12.0

    elif asset == "index" and provider == "yahoo":
        score += 15.0

    elif asset == "commodity" and provider == "yahoo":
        score += 15.0

    # Twelve Data reste un secours.
    if provider == "twelvedata":
        score -= 8.0

    return score


# ======================================================================
# DATA ROUTER
# ======================================================================

class DataRouter:
    """
    Routeur intelligent V4.2.
    """

    # ------------------------------------------------------------------
    # Budget Twelve Data
    # ------------------------------------------------------------------

    @staticmethod
    def twelvedata_used():
        return _TwelveDataBudget.used_today()

    @staticmethod
    def twelvedata_remaining():
        return _TwelveDataBudget.remaining()

    @staticmethod
    def twelvedata_budget():
        return TWELVE_DATA_DAILY_BUDGET

    # Alias
    @staticmethod
    def td_used():
        return _TwelveDataBudget.used_today()

    @staticmethod
    def td_remaining():
        return _TwelveDataBudget.remaining()

    @property
    def twelve_data_used(self):
        return _TwelveDataBudget.used_today()

    @property
    def twelve_data_remaining(self):
        return _TwelveDataBudget.remaining()

    @property
    def twelvedata_used_count(self):
        return _TwelveDataBudget.used_today()

    # ------------------------------------------------------------------
    # Statistiques
    # ------------------------------------------------------------------

    @staticmethod
    def stats():
        return router_stats()

    @staticmethod
    def get_stats():
        return router_stats()

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    @staticmethod
    def clear_cache():
        clear_cache()

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    def fetch(
        self,
        symbol,
        interval="15m",
        limit=1000,
        asset_type="stock",
        preferred=None,
        require_volume=False,
        symbol_map=None,
    ):
        """
        Sélectionne automatiquement la meilleure source.

        preferred :
            fournisseur préféré optionnel.

        require_volume :
            True lorsque le volume est important.

        Le routeur essaie les fournisseurs gratuits avant
        Twelve Data lorsque cela est possible.
        """

        asset = str(
            asset_type
        ).lower()

        interval = normalize_interval(
            interval
        )

        symbol = str(
            symbol
        ).strip()

        _stats_increment(
            "requests"
        )

        if not symbol:
            _stats_increment(
                "errors"
            )

            raise DataSourceError(
                "Symbole vide."
            )

        # ==============================================================
        # CRYPTO → BINANCE
        # ==============================================================

        if asset == "crypto":
            provider = "binance"

            provider_symbol = (
                _resolve_provider_symbol(
                    symbol,
                    provider,
                    symbol_map,
                )
            )

            try:
                df = klines_binance_v42(
                    provider_symbol,
                    interval=interval,
                    limit=limit,
                    asset_type=asset,
                )

                if df.empty:
                    raise DataSourceError(
                        "Binance : données vides."
                    )

                _stats_increment(
                    "success"
                )

                _stats_increment(
                    "binance"
                )

                return df

            except Exception:
                _stats_increment(
                    "errors"
                )
                raise

        # ==============================================================
        # CANDIDATS
        # ==============================================================

        candidates = _provider_order(
            asset,
            require_volume=require_volume,
        )

        if preferred:
            preferred = str(
                preferred
            ).lower()

            if preferred in candidates:
                candidates.remove(
                    preferred
                )

                candidates.insert(
                    0,
                    preferred,
                )

        filtered = []

        for provider in candidates:
            if not _provider_supports_interval(
                provider,
                interval,
            ):
                continue

            if (
                provider == "twelvedata"
                and not TWELVE_DATA_API_KEY
            ):
                continue

            if (
                provider == "finnhub"
                and not FINNHUB_API_KEY
            ):
                continue

            filtered.append(
                provider
            )

        candidates = filtered

        if not candidates:
            _stats_increment(
                "errors"
            )

            raise DataSourceError(
                f"Aucun fournisseur disponible pour "
                f"{symbol} ({asset}, {interval})."
            )

        # ==============================================================
        # ESSAI DES SOURCES
        # ==============================================================

        successful = []
        errors = []

        for index, provider in enumerate(
            candidates
        ):
            provider_symbol = (
                _resolve_provider_symbol(
                    symbol,
                    provider,
                    symbol_map,
                )
            )

            if not provider_symbol:
                errors.append(
                    f"{provider}: symbole absent"
                )
                continue

            try:
                fetcher = _provider_function(
                    provider
                )

                df = fetcher(
                    provider_symbol,
                    interval=interval,
                    limit=limit,
                    asset_type=asset,
                )

                if df is None or df.empty:
                    raise DataSourceError(
                        "données vides"
                    )

                df = ensure_ohlcv(
                    df
                )

                if df.empty:
                    raise DataSourceError(
                        "données OHLCV vides"
                    )

                score = _source_score(
                    provider,
                    df,
                    asset,
                    interval,
                    require_volume,
                )

                successful.append(
                    {
                        "provider": provider,
                        "symbol": provider_symbol,
                        "df": df,
                        "score": score,
                    }
                )

                coverage = volume_coverage(
                    df
                )

                freshness = _freshness_score(
                    df,
                    interval,
                )

                volume_good = (
                    coverage
                    >= VOLUME_MIN_COVERAGE
                )

                # ------------------------------------------------------
                # Source suffisamment bonne.
                # ------------------------------------------------------

                if (
                    require_volume
                    and volume_good
                    and freshness >= 0.5
                ):
                    break

                if (
                    not require_volume
                    and freshness >= 0.5
                ):
                    break

                # ------------------------------------------------------
                # Volume requis mais non disponible :
                # essayer la source suivante.
                # ------------------------------------------------------

                if (
                    require_volume
                    and not volume_good
                    and index < len(candidates) - 1
                ):
                    _stats_increment(
                        "fallbacks"
                    )

                    continue

                break

            except Exception as exc:
                errors.append(
                    f"{provider}: {exc}"
                )

                _stats_increment(
                    "errors"
                )

                if index < len(candidates) - 1:
                    _stats_increment(
                        "fallbacks"
                    )

                continue

        # ==============================================================
        # AUCUNE SOURCE
        # ==============================================================

        if not successful:
            raise DataSourceError(
                f"Aucune source exploitable pour "
                f"{symbol}. "
                + " | ".join(errors)
            )

        # ==============================================================
        # MEILLEURE SOURCE
        # ==============================================================

        selected = max(
            successful,
            key=lambda item: item["score"],
        )

        provider = selected[
            "provider"
        ]

        provider_symbol = selected[
            "symbol"
        ]

        df = selected[
            "df"
        ]

        # ==============================================================
        # MÉTADONNÉES
        # ==============================================================

        df = attach_metadata(
            df,
            provider,
            provider_symbol,
            asset,
        )

        df.attrs[
            "router_candidates"
        ] = [
            item["provider"]
            for item in successful
        ]

        df.attrs[
            "router_scores"
        ] = {
            item["provider"]:
                round(
                    float(item["score"]),
                    2,
                )
            for item in successful
        }

        df.attrs[
            "router_errors"
        ] = errors

        df.attrs[
            "router_selected_score"
        ] = round(
            float(selected["score"]),
            2,
        )

        df.attrs[
            "router_fallback_count"
        ] = max(
            0,
            len(successful) - 1,
        )

        _stats_increment(
            "success"
        )

        _stats_increment(
            provider
        )

        return df


# ======================================================================
# INTERFACE SIMPLE
# ======================================================================

def fetch_data(
    symbol,
    interval="15m",
    limit=1000,
    asset_type="stock",
    preferred=None,
    require_volume=False,
    symbol_map=None,
):
    router = DataRouter()

    return router.fetch(
        symbol=symbol,
        interval=interval,
        limit=limit,
        asset_type=asset_type,
        preferred=preferred,
        require_volume=require_volume,
        symbol_map=symbol_map,
    )


def get_twelvedata_budget():
    return {
        "used":
            _TwelveDataBudget.used_today(),

        "remaining":
            _TwelveDataBudget.remaining(),

        "budget":
            TWELVE_DATA_DAILY_BUDGET,
    }


# ======================================================================
# TEST LOCAL
# ======================================================================

if __name__ == "__main__":
    print(
        "Module data_sources.py V4.2 chargé correctement."
    )

    print(
        "Twelve Data : "
        f"{_TwelveDataBudget.used_today()}/"
        f"{TWELVE_DATA_DAILY_BUDGET} "
        "appels utilisés."
    )

    print(
        "Twelve Data restant : "
        f"{_TwelveDataBudget.remaining()}"
    )

    print(
        "Statistiques routeur :"
    )

    print(
        router_stats()
    )
