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
        Budget quotidien partagé : 800 appels maximum.

Principes V4.2 :
    - aucune dépendance à un ordre de fournisseurs rigide ;
    - choix dynamique selon catégorie, disponibilité, fraîcheur et volume ;
    - fallback automatique ;
    - cache des requêtes identiques pendant le processus ;
    - volume absent = NaN, jamais 0 ;
    - couverture volume >= 80 % pour considérer le volume exploitable ;
    - agrégation correcte des bougies 1H -> 4H ;
    - exclusion systématique des bougies incomplètes ;
    - pas de retries inutiles sur Twelve Data ;
    - compteur Twelve Data partagé entre toutes les instances ;
    - diagnostic du fournisseur réellement utilisé.
"""

import os
import threading
from datetime import datetime, timezone

import pandas as pd
import requests
from dotenv import load_dotenv

from backtest import klines as klines_binance


load_dotenv()


# ======================================================================
# CONFIGURATION
# ======================================================================

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")

YAHOO_BASE = (
    "https://query1.finance.yahoo.com/v8/finance/chart/"
)

TWELVE_DATA_BASE = (
    "https://api.twelvedata.com/time_series"
)

FINNHUB_CANDLE_BASE = (
    "https://finnhub.io/api/v1/stock/candle"
)

REQUEST_TIMEOUT = 30

# Volume considéré comme réellement exploitable
VOLUME_MIN_COVERAGE = float(
    os.getenv("VOLUME_MIN_COVERAGE", "0.80")
)

# Budget Twelve Data demandé pour V4.2
TWELVE_DATA_DAILY_BUDGET = int(
    os.getenv("TWELVE_DATA_DAILY_BUDGET", "800")
)


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
# COMPTEURS ROUTEUR
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


def router_stats():
    with _STATS_LOCK:
        stats = dict(_ROUTER_STATS)

    stats.update(
        {
            "twelvedata_used":
                DataRouter.twelvedata_used(),
            "twelvedata_remaining":
                DataRouter.twelvedata_remaining(),
            "twelvedata_budget":
                TWELVE_DATA_DAILY_BUDGET,
        }
    )

    return stats


# ======================================================================
# BUDGET TWELVE DATA
# ======================================================================

class _TwelveDataBudget:
    """
    Compteur partagé au niveau du processus.

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
                TWELVE_DATA_DAILY_BUDGET - cls.used
            )


# ======================================================================
# UTILITAIRES
# ======================================================================

def interval_to_seconds(interval):
    """
    Convertit un intervalle en secondes.
    """

    value = str(
        interval
    ).strip().lower()

    if value.endswith("m"):
        return int(
            value[:-1]
        ) * 60

    if value.endswith("h"):
        return int(
            value[:-1]
        ) * 3600

    if value.endswith("d"):
        return int(
            value[:-1]
        ) * 86400

    if value.endswith("w"):
        return int(
            value[:-1]
        ) * 604800

    return 60


def normalize_interval(interval):
    return str(
        interval
    ).strip().lower()


def empty_ohlcv():
    df = pd.DataFrame(
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    )

    return df


def ensure_ohlcv(df):
    """
    Normalise systématiquement la structure de sortie.
    """

    if df is None:
        return empty_ohlcv()

    d = df.copy()

    required = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    for column in required:
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

    return d[
        required
    ]


def volume_coverage(df):
    """
    Pourcentage de bougies disposant d'un volume exploitable.
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
    """
    Statut explicite du volume.
    """

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
    """
    Ajoute les informations du routeur dans DataFrame.attrs.
    """

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

    return d


def keep_completed_candles(
    df,
    interval,
):
    """
    Exclut les bougies encore en formation.

    On utilise l'heure d'ouverture + durée de l'intervalle.
    """

    if df is None or df.empty:
        return df

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

    d = (
        d.loc[completed]
        .copy()
        .reset_index(drop=True)
    )

    return d


def _completed_limit(
    limit,
    interval,
):
    """
    Quantité brute à demander pour obtenir suffisamment
    de bougies après exclusion des bougies incomplètes.

    Pour 4H via agrégation 1H, on demande environ 4x.
    """

    limit = max(
        1,
        int(limit)
    )

    if normalize_interval(interval) == "4h":
        return min(
            5000,
            limit * 4 + 20,
        )

    return min(
        5000,
        limit + 10,
    )


# ======================================================================
# AGRÉGATION
# ======================================================================

def aggregate_ohlcv(
    df,
    target_interval,
):
    """
    Agrège des bougies inférieures vers l'intervalle cible.

    Exemple :
        1H -> 4H

    Règle volume :
        le volume agrégé n'est conservé que si TOUS les composants
        disposent d'un volume réel.

        Sinon :
            NaN

    On ne fabrique jamais un volume partiel.
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
    elif target == "2h":
        rule = "2h"
    elif target == "1h":
        return d
    else:
        raise ValueError(
            f"Intervalle d'agrégation non supporté : "
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

    expected_seconds = (
        interval_to_seconds("1h")
    )

    expected_children = (
        interval_to_seconds(target)
        // expected_seconds
    )

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
    limit,
):
    """
    Téléchargement brut Yahoo.
    """

    requested_interval = normalize_interval(
        interval
    )

    # Yahoo ne reçoit pas 4H directement :
    # on demande du 1H puis on agrège.
    yahoo_interval = (
        "1h"
        if requested_interval == "4h"
        else requested_interval
    )

    config = _YAHOO_CONFIG.get(
        yahoo_interval,
        {
            "range": "60d",
            "interval": yahoo_interval,
        },
    )

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
        raise RuntimeError(
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
        error = chart.get(
            "error"
        )

        raise RuntimeError(
            f"Yahoo : aucune donnée pour "
            f"{symbol} : {error}"
        )

    result = results[0]

    timestamps = result.get(
        "timestamp",
        [],
    )

    quote_list = (
        result
        .get("indicators", {})
        .get("quote", [])
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
    """
    Récupère des bougies Yahoo.

    4H :
        Yahoo 1H -> agrégation V4.2 4H.
    """

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

    cached = _cache_get(
        key
    )

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

    raw_limit = _completed_limit(
        limit,
        interval,
    )

    df = _yahoo_raw(
        symbol,
        interval,
        raw_limit,
    )

    if interval == "4h":
        df = aggregate_ohlcv(
            df,
            "4h",
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
    """
    Calcule une fenêtre Unix suffisamment large.
    """

    seconds = interval_to_seconds(
        interval
    )

    now = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    # Marge importante pour les marchés fermés
    # et pour les périodes sans cotation.
    total = (
        max(100, int(limit))
        * seconds
        * 2
    )

    return (
        now - total,
        now,
    )


def _finnhub_raw(
    symbol,
    interval,
    limit,
):
    """
    Récupère les candles Finnhub.

    Le volume 'v' est conservé lorsqu'il est fourni.
    """

    if not FINNHUB_API_KEY:
        raise RuntimeError(
            "FINNHUB_API_KEY absente."
        )

    requested_interval = normalize_interval(
        interval
    )

    if requested_interval == "4h":
        resolution = "60"
        raw_interval = "1h"
    else:
        resolution = _FINNHUB_RESOLUTION.get(
            requested_interval
        )

        if resolution is None:
            raise RuntimeError(
                f"Intervalle Finnhub non supporté : "
                f"{interval}"
            )

        raw_interval = requested_interval

    start, end = _finnhub_period(
        raw_interval,
        _completed_limit(
            limit,
            requested_interval,
        ),
    )

    params = {
        "symbol": symbol,
        "resolution": resolution,
        "from": start,
        "to": end,
        "token": FINNHUB_API_KEY,
    }

    response = _SESSION.get(
        FINNHUB_CANDLE_BASE,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code == 429:
        raise RuntimeError(
            f"Finnhub HTTP 429 pour {symbol}"
        )

    response.raise_for_status()

    payload = response.json()

    status = str(
        payload.get(
            "s",
            ""
        )
    ).lower()

    if status not in {
        "ok",
    }:
        raise RuntimeError(
            f"Finnhub : aucune donnée pour "
            f"{symbol} : {payload}"
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

    # Finnhub peut ne pas retourner le volume
    # pour certains instruments.
    if len(volumes) != len(timestamps):
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
    """
    Finnhub V4.2.

    Pour les actions :
        volume réel conservé.

    Pour les instruments sans volume :
        NaN.

    Pour 4H :
        1H -> 4H.
    """

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

    cached = _cache_get(
        key
    )

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
    )

    if interval == "4h":
        df = aggregate_ohlcv(
            df,
            "4h",
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
        return str(
            message
        )

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
    """
    Twelve Data.

    IMPORTANT :
        - une seule requête par appel logique ;
        - aucun retry automatique ;
        - budget quotidien partagé ;
        - volume absent = NaN ;
        - volume réel conservé.
    """

    if not TWELVE_DATA_API_KEY:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY absente."
        )

    interval = normalize_interval(
        interval
    )

    key = _cache_key(
        "twelvedata",
        symbol,
        interval,
        limit,
        asset_type,
    )

    cached = _cache_get(
        key
    )

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

    # Une réservation = une requête API.
    if not _TwelveDataBudget.reserve():
        raise RuntimeError(
            "Budget Twelve Data quotidien atteint "
            f"({TWELVE_DATA_DAILY_BUDGET} appels)."
        )

    td_interval = _TD_INTERVALS.get(
        interval
    )

    if td_interval is None:
        raise RuntimeError(
            f"Intervalle Twelve Data non supporté : "
            f"{interval}"
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
        raise RuntimeError(
            f"Twelve Data HTTP 429 pour {symbol}"
        )

    response.raise_for_status()

    payload = response.json()

    if payload.get(
        "status"
    ) == "error":
        raise RuntimeError(
            "Twelve Data : "
            + _twelvedata_error_message(
                payload
            )
        )

    values = payload.get(
        "values"
    )

    if not values:
        raise RuntimeError(
            f"Twelve Data : aucune donnée pour "
            f"{symbol}"
        )

    df = pd.DataFrame(
        values
    )

    if "datetime" not in df.columns:
        raise RuntimeError(
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
            raise RuntimeError(
                f"Twelve Data : colonne {column} absente."
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
        # JAMAIS 0 pour un volume absent.
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
# BINANCE WRAPPER
# ======================================================================

def klines_binance_v42(
    symbol,
    interval="5m",
    limit=1000,
    asset_type="crypto",
):
    """
    Wrapper Binance pour le routeur.

    Binance reste la source unique de la crypto.
    """

    key = _cache_key(
        "binance",
        symbol,
        interval,
        limit,
        asset_type,
    )

    cached = _cache_get(
        key
    )

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

    df = klines_binance(
        symbol,
        interval=interval,
        limit=min(
            int(limit),
            1000,
        ),
    )

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
# RÉSOLUTION DES SYMBOLES
# ======================================================================

def _resolve_provider_symbol(
    symbol,
    provider,
    symbol_map=None,
):
    """
    Résolution flexible compatible avec plusieurs formats
    de symbol_map.

    Formats acceptés :

    1.
        {"yahoo": "AAPL", "finnhub": "AAPL"}

    2.
        {"AAPL": {"yahoo": "AAPL", ...}}

    3.
        {"AAPL": "AAPL"}

    4.
        [{"yahoo": "AAPL", ...}, ...]
    """

    provider = str(
        provider
    ).lower()

    if symbol_map is None:
        return symbol

    # --------------------------------------------------------------
    # Mapping direct provider -> symbole
    # --------------------------------------------------------------

    if isinstance(
        symbol_map,
        dict,
    ):
        direct = symbol_map.get(
            provider
        )

        if direct:
            return str(
                direct
            )

        # ----------------------------------------------------------
        # Mapping symbole -> mapping fournisseurs
        # ----------------------------------------------------------

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
                return str(
                    value
                )

        elif isinstance(
            nested,
            str,
        ):
            return nested

    # --------------------------------------------------------------
    # Liste / tuple de dictionnaires
    # --------------------------------------------------------------

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

            display = entry.get(
                "display"
            )

            yahoo = entry.get(
                "yahoo"
            )

            td = entry.get(
                "twelvedata"
            )

            finnhub = entry.get(
                "finnhub"
            )

            binance = entry.get(
                "binance"
            )

            if symbol in {
                display,
                yahoo,
                td,
                finnhub,
                binance,
            }:
                value = entry.get(
                    provider
                )

                if value:
                    return str(
                        value
                    )

    return symbol


# ======================================================================
# CAPACITÉS FOURNISSEURS
# ======================================================================

def _provider_order(
    asset_type,
    require_volume=False,
):
    """
    Ordre initial intelligent.

    Le routeur peut ensuite modifier cet ordre en fonction
    des résultats et des capacités.
    """

    asset = str(
        asset_type
    ).lower()

    if asset == "crypto":
        return [
            "binance",
        ]

    if asset == "stock":
        # Finnhub et Yahoo avant Twelve Data.
        if require_volume:
            return [
                "finnhub",
                "yahoo",
                "twelvedata",
            ]

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
        return True

    if provider == "yahoo":
        return interval in {
            "1m",
            "5m",
            "15m",
            "30m",
            "1h",
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
# ÉVALUATION D'UNE SOURCE
# ======================================================================

def _freshness_score(
    df,
    interval,
):
    """
    Score de fraîcheur entre 0 et 1.

    Une donnée récente obtient 1.
    Une donnée plusieurs intervalles en retard voit son score diminuer.

    On ne rejette pas automatiquement les marchés fermés.
    """

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


def _source_score(
    provider,
    df,
    asset_type,
    interval,
    require_volume,
):
    """
    Score interne permettant de comparer les résultats
    réellement obtenus.
    """

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

    # --------------------------------------------------------------
    # Fraîcheur
    # --------------------------------------------------------------

    score += (
        freshness * 50.0
    )

    # --------------------------------------------------------------
    # Volume
    # --------------------------------------------------------------

    if coverage >= VOLUME_MIN_COVERAGE:
        score += 30.0
    elif coverage > 0:
        score += (
            10.0 * coverage
        )

    # Si le volume est obligatoire, un fournisseur sans volume
    # est fortement pénalisé.
    if require_volume:
        if coverage < VOLUME_MIN_COVERAGE:
            score -= 40.0

    # --------------------------------------------------------------
    # Qualité de données
    # --------------------------------------------------------------

    count = len(
        df
    )

    if count >= 120:
        score += 15.0
    elif count >= 60:
        score += 8.0
    elif count >= 30:
        score += 3.0

    # --------------------------------------------------------------
    # Bonus provider par catégorie
    # --------------------------------------------------------------

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

    # Twelve Data doit être conservé comme secours
    # afin d'économiser le quota.
    if provider == "twelvedata":
        score -= 8.0

    return score


# ======================================================================
# DATA ROUTER
# ======================================================================

class DataRouter:
    """
    Routeur V4.2.

    Exemple :

        router = DataRouter()

        df = router.fetch(
            "AAPL",
            interval="15m",
            limit=1000,
            asset_type="stock",
            symbol_map={
                "yahoo": "AAPL",
                "finnhub": "AAPL",
                "twelvedata": "AAPL",
            },
        )
    """

    # --------------------------------------------------------------
    # Budget partagé
    # --------------------------------------------------------------

    @staticmethod
    def twelvedata_used():
        return _TwelveDataBudget.used_today()

    @staticmethod
    def twelvedata_remaining():
        return _TwelveDataBudget.remaining()

    @staticmethod
    def twelvedata_budget():
        return TWELVE_DATA_DAILY_BUDGET

    # Alias utiles pour compatibilité.
    @staticmethod
    def td_used():
        return _TwelveDataBudget.used_today()

    @staticmethod
    def td_remaining():
        return _TwelveDataBudget.remaining()

    # --------------------------------------------------------------
    # Statistiques
    # --------------------------------------------------------------

    @staticmethod
    def stats():
        return router_stats()

    @staticmethod
    def get_stats():
        return router_stats()

    # --------------------------------------------------------------
    # Réinitialisation du cache
    # --------------------------------------------------------------

    @staticmethod
    def clear_cache():
        clear_cache()

    # --------------------------------------------------------------
    # Fetch
    # --------------------------------------------------------------

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
            True pour actions/crypto/matières nécessitant
            un volume exploitable.

        Le routeur n'utilise pas Twelve Data avant les
        autres sources lorsque celles-ci sont disponibles.
        """

        asset = str(
            asset_type
        ).lower()

        interval = normalize_interval(
            interval
        )

        symbol = str(
            symbol
        )

        _stats_increment(
            "requests"
        )

        # ----------------------------------------------------------
        # Crypto : Binance uniquement
        # ----------------------------------------------------------

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
                df = _provider_function(
                    provider
                )(
                    provider_symbol,
                    interval=interval,
                    limit=limit,
                    asset_type=asset,
                )

                if df is None or df.empty:
                    raise RuntimeError(
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

        # ----------------------------------------------------------
        # Construction des candidats
        # ----------------------------------------------------------

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

        # ----------------------------------------------------------
        # Suppression des fournisseurs non disponibles
        # ----------------------------------------------------------

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

            raise RuntimeError(
                f"Aucun fournisseur disponible pour "
                f"{symbol} ({asset}, {interval})."
            )

        # ----------------------------------------------------------
        # Essai des sources
        # ----------------------------------------------------------

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

            # Une absence explicite de symbole peut être ignorée.
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
                    raise RuntimeError(
                        "données vides"
                    )

                df = ensure_ohlcv(
                    df
                )

                if df.empty:
                    raise RuntimeError(
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

                # --------------------------------------------------
                # Cas normal :
                # une source très bonne suffit.
                # --------------------------------------------------

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

                # Crypto déjà traitée.
                if (
                    asset == "crypto"
                    or (
                        require_volume
                        and volume_good
                        and freshness >= 0.5
                    )
                    or (
                        not require_volume
                        and freshness >= 0.5
                    )
                ):
                    break

                # --------------------------------------------------
                # Si la source est exploitable mais volume absent,
                # on continue pour tenter une source avec volume.
                # --------------------------------------------------

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

        # ----------------------------------------------------------
        # Aucun succès
        # ----------------------------------------------------------

        if not successful:
            raise RuntimeError(
                f"Aucune source exploitable pour "
                f"{symbol}. "
                + " | ".join(errors)
            )

        # ----------------------------------------------------------
        # Choix final :
        # meilleure qualité réelle, pas simplement premier venu.
        # ----------------------------------------------------------

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

        # ----------------------------------------------------------
        # Métadonnées
        # ----------------------------------------------------------

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
# FONCTIONS DE COMPATIBILITÉ
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
    """
    Interface fonctionnelle simple vers DataRouter.
    """

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


def get_router_stats():
    return router_stats()


def get_twelvedata_budget():
    return {
        "used": DataRouter.twelvedata_used(),
        "remaining": DataRouter.twelvedata_remaining(),
        "budget": TWELVE_DATA_DAILY_BUDGET,
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
        f"{DataRouter.twelvedata_used()}/"
        f"{TWELVE_DATA_DAILY_BUDGET} "
        "appels utilisés."
    )

    print(
        "Twelve Data restant : "
        f"{DataRouter.twelvedata_remaining()}"
    )

    print(
        "Statistiques routeur :"
    )

    print(
        router_stats()
    )
