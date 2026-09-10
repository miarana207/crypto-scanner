```python
"""
V4.2 — Sources de données + routeur intelligent multi-fournisseurs.

Fournisseurs :
- Binance
- Yahoo Finance
- Finnhub
- Twelve Data

Architecture V4.2 :
- OHLC valide obligatoire.
- Les bougies en formation sont exclues.
- Le volume manquant reste NaN/NA.
- Le volume n'est jamais transformé artificiellement en 0.
- Le routeur choisit dynamiquement la source.
- Le routeur tient compte de la fraîcheur, du volume, du nombre
  de bougies et du type d'actif.
- Twelve Data est protégé par un budget journalier.
- Les appels identiques sont mis en cache pendant le run.
- Les intervalles 4h sont correctement agrégés à partir du 1h
  lorsque le fournisseur ne fournit pas directement du 4h.
- Les métadonnées de routage sont conservées dans df.attrs.
"""

import os
import time
from datetime import datetime, timezone
from threading import Lock

import pandas as pd
import requests
from dotenv import load_dotenv


# ============================================================
# CONFIGURATION
# ============================================================

load_dotenv()

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")

YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
TWELVE_DATA_BASE = "https://api.twelvedata.com/time_series"
FINNHUB_BASE = "https://finnhub.io/api/v1/stock/candle"

BINANCE_BASE = os.getenv(
    "BINANCE_DATA_URL",
    "https://data-api.binance.vision",
)

REQUEST_TIMEOUT = int(
    os.getenv("DATA_REQUEST_TIMEOUT", "30")
)

CACHE_TTL = int(
    os.getenv("DATA_CACHE_TTL", "45")
)

# Budget quotidien configurable.
# La valeur par défaut correspond à une utilisation maximale
# volontairement prudente du quota Twelve Data configuré.
TWELVEDATA_DAILY_BUDGET = int(
    os.getenv("TWELVEDATA_DAILY_BUDGET", "800")
)


# ============================================================
# FORMAT STANDARD
# ============================================================

STANDARD_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
]


# ============================================================
# STATUT DU VOLUME
# ============================================================

VOLUME_CONFIRMED = "VOLUME_CONFIRMED"
VOLUME_UNAVAILABLE = "VOLUME_UNAVAILABLE"
VOLUME_INVALID = "VOLUME_INVALID"


# ============================================================
# INTERVALLES
# ============================================================

_INTERVAL_SECONDS = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
    "1w": 7 * 24 * 60 * 60,
}


# Yahoo Finance.
#
# Pour 4h, Yahoo est interrogé en 1h puis les bougies sont
# agrégées localement.
_YF_CONFIG = {
    "1m": ("7d", "1m"),
    "5m": ("60d", "5m"),
    "15m": ("60d", "15m"),
    "30m": ("60d", "30m"),
    "1h": ("730d", "1h"),
    "4h": ("730d", "1h"),
    "1d": ("10y", "1d"),
}


# Twelve Data.
_TD_INTERVALS = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1day",
}


# Finnhub.
#
# Finnhub ne fournit pas nécessairement une bougie 4h directement
# dans l'API candle. On récupère donc du 60 minutes puis on agrège.
_FH_RESOLUTION = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "4h": "60",
    "1d": "D",
}


# ============================================================
# CACHE MÉMOIRE
# ============================================================

_CACHE = {}
_CACHE_LOCK = Lock()


def _cache_get(key):
    """Retourne une copie du cache si elle est encore valide."""

    with _CACHE_LOCK:
        item = _CACHE.get(key)

        if item is None:
            return None

        timestamp, dataframe = item

        if time.time() - timestamp <= CACHE_TTL:
            return dataframe.copy()

        _CACHE.pop(key, None)

    return None


def _cache_put(key, dataframe):
    """Stocke une copie du DataFrame dans le cache."""

    with _CACHE_LOCK:
        _CACHE[key] = (
            time.time(),
            dataframe.copy(),
        )


# ============================================================
# UTILITAIRES INTERVALLES
# ============================================================

def interval_to_seconds(interval):
    """
    Convertit un intervalle en secondes.

    Exemple :
        15m -> 900
        1h  -> 3600
        4h  -> 14400
        1d  -> 86400
    """

    interval = str(interval).lower().strip()

    if interval in _INTERVAL_SECONDS:
        return _INTERVAL_SECONDS[interval]

    unit = interval[-1:] if interval else ""
    value_text = interval[:-1]

    try:
        value = int(value_text)
    except (TypeError, ValueError):
        return 60

    multiplier = {
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 604800,
    }.get(unit)

    if multiplier is None:
        return 60

    return value * multiplier


def _required_raw_limit(interval, limit):
    """
    Détermine combien de bougies brutes il faut demander.

    Pour une agrégation 4h depuis du 1h, il faut environ 4 fois
    plus de bougies.
    """

    limit = max(1, int(limit))

    if interval == "4h":
        return min(limit * 4 + 20, 5000)

    return limit


# ============================================================
# BOUGIES COMPLÉTÉES
# ============================================================

def keep_completed_candles(df, interval):
    """
    Supprime les bougies encore en formation.

    Une bougie est considérée comme terminée lorsque :

        maintenant >= open_time + durée_intervalle
    """

    if df is None or df.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    out = df.copy()

    if "open_time" not in out.columns:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    out["open_time"] = pd.to_datetime(
        out["open_time"],
        utc=True,
        errors="coerce",
    )

    out = out.dropna(subset=["open_time"])

    now = pd.Timestamp.now(tz="UTC")

    duration = interval_to_seconds(interval)

    completed = (
        now - out["open_time"]
    ).dt.total_seconds() >= duration

    out = out.loc[completed].copy()

    out = (
        out
        .sort_values("open_time")
        .drop_duplicates("open_time", keep="last")
        .reset_index(drop=True)
    )

    return out


# ============================================================
# NORMALISATION OHLCV
# ============================================================

def normalize_ohlcv(df, interval="15m", limit=1000):
    """
    Normalise toutes les sources dans le même format.

    Important :
    - OHLC est obligatoire.
    - volume peut rester NaN.
    - volume manquant n'est jamais remplacé par 0.
    """

    if df is None or df.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    out = df.copy()

    # Création des colonnes manquantes.
    for column in STANDARD_COLUMNS:

        if column not in out.columns:

            if column == "volume":
                out[column] = pd.NA
            else:
                out[column] = float("nan")

    # Conversion timestamp.
    out["open_time"] = pd.to_datetime(
        out["open_time"],
        utc=True,
        errors="coerce",
    )

    # Conversion numérique.
    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:
        out[column] = pd.to_numeric(
            out[column],
            errors="coerce",
        )

    # OHLC obligatoire.
    out = out.dropna(
        subset=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
        ]
    )

    # Vérification cohérence OHLC.
    valid_high = (
        out["high"]
        >= out[["open", "close"]].max(axis=1)
    )

    valid_low = (
        out["low"]
        <= out[["open", "close"]].min(axis=1)
    )

    out = out.loc[
        valid_high & valid_low
    ].copy()

    # Tri + suppression des doublons.
    out = (
        out
        .sort_values("open_time")
        .drop_duplicates(
            "open_time",
            keep="last",
        )
    )

    # Exclusion des bougies en formation.
    out = keep_completed_candles(
        out,
        interval,
    )

    return (
        out[STANDARD_COLUMNS]
        .tail(int(limit))
        .reset_index(drop=True)
    )


# ============================================================
# AGRÉGATION
# ============================================================

def aggregate_ohlcv(df, target_interval):
    """
    Agrège des bougies 1h en 4h.

    OHLC :
        open  = première valeur
        high  = maximum
        low   = minimum
        close = dernière valeur

    Volume :
        somme uniquement si le volume existe.

    Si aucun volume n'existe, le résultat conserve NaN.
    """

    if df is None or df.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    if target_interval != "4h":
        return normalize_ohlcv(
            df,
            target_interval,
            len(df),
        )

    out = normalize_ohlcv(
        df,
        "1h",
        len(df),
    )

    if out.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    out = out.set_index("open_time")

    aggregation = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }

    # Ne somme le volume que s'il existe réellement.
    volume_numeric = pd.to_numeric(
        out["volume"],
        errors="coerce",
    )

    if volume_numeric.notna().any():
        aggregation["volume"] = "sum"

    grouped = (
        out
        .resample("4h", origin="epoch")
        .agg(aggregation)
    )

    if "volume" not in grouped.columns:
        grouped["volume"] = pd.NA

    grouped = grouped.reset_index()

    grouped = normalize_ohlcv(
        grouped,
        "4h",
        len(grouped),
    )

    return grouped


# ============================================================
# VOLUME
# ============================================================

def volume_status(df):
    """
    Détermine l'état du volume.

    Important :
    - NaN = volume indisponible.
    - 0 n'est pas automatiquement converti en NaN.
    - un volume négatif est invalide.
    """

    if (
        df is None
        or df.empty
        or "volume" not in df.columns
    ):
        return VOLUME_UNAVAILABLE

    volume = pd.to_numeric(
        df["volume"],
        errors="coerce",
    )

    valid_values = volume.dropna()

    if valid_values.empty:
        return VOLUME_UNAVAILABLE

    if (valid_values < 0).any():
        return VOLUME_INVALID

    return VOLUME_CONFIRMED


def volume_coverage(df):
    """
    Pourcentage de bougies disposant d'un volume exploitable.
    """

    if (
        df is None
        or df.empty
        or "volume" not in df.columns
    ):
        return 0.0

    volume = pd.to_numeric(
        df["volume"],
        errors="coerce",
    )

    if len(volume) == 0:
        return 0.0

    return float(
        volume.notna().mean()
    )


# ============================================================
# QUALITÉ DES DONNÉES
# ============================================================

def data_quality(
    df,
    interval="15m",
    min_candles=120,
):
    """
    Évalue la qualité d'un dataset.

    Retourne notamment :
        valid
        candles
        fresh
        age_seconds
        volume_status
        volume_coverage
    """

    if df is None or df.empty:
        return {
            "valid": False,
            "candles": 0,
            "volume_status": VOLUME_UNAVAILABLE,
            "volume_coverage": 0.0,
            "fresh": False,
            "age_seconds": None,
        }

    out = normalize_ohlcv(
        df,
        interval,
        max(
            len(df),
            int(min_candles),
        ),
    )

    if out.empty:
        return {
            "valid": False,
            "candles": 0,
            "volume_status": VOLUME_UNAVAILABLE,
            "volume_coverage": 0.0,
            "fresh": False,
            "age_seconds": None,
        }

    now = pd.Timestamp.now(tz="UTC")

    latest = out["open_time"].iloc[-1]

    age = (
        now - latest
    ).total_seconds()

    # Tolérance de fraîcheur.
    #
    # Pour un scan périodique, on accepte plusieurs périodes,
    # mais pas une donnée manifestement ancienne.
    fresh_limit = max(
        interval_to_seconds(interval) * 4,
        15 * 60,
    )

    return {
        "valid": len(out) >= int(min_candles),
        "candles": len(out),
        "volume_status": volume_status(out),
        "volume_coverage": volume_coverage(out),
        "fresh": age <= fresh_limit,
        "age_seconds": max(0.0, age),
    }


# ============================================================
# HTTP
# ============================================================

def _request_json(
    url,
    params=None,
    headers=None,
):
    """
    Requête HTTP standardisée.
    """

    response = requests.get(
        url,
        params=params,
        headers=headers or {},
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return response.json(), response.headers


# ============================================================
# YAHOO FINANCE
# ============================================================

def klines_yahoo(
    symbol,
    interval="15m",
    limit=1000,
):
    """
    Récupère les données Yahoo Finance.

    Pour 4h :
        Yahoo -> 1h -> agrégation locale 4h.
    """

    cache_key = (
        "yahoo",
        symbol,
        interval,
        int(limit),
    )

    cached = _cache_get(cache_key)

    if cached is not None:
        return cached

    if interval not in _YF_CONFIG:
        raise ValueError(
            f"Intervalle Yahoo non supporté: {interval}"
        )

    range_value, yf_interval = _YF_CONFIG[interval]

    raw_limit = _required_raw_limit(
        interval,
        limit,
    )

    params = {
        "range": range_value,
        "interval": yf_interval,
        "includePrePost": "false",
        "events": "div,splits",
    }

    headers = {
        "User-Agent": "Mozilla/5.0 scanner-v4.2"
    }

    data, _ = _request_json(
        YAHOO_BASE + str(symbol),
        params=params,
        headers=headers,
    )

    result = (
        data
        .get("chart", {})
        .get("result")
        or []
    )

    if not result:
        raise RuntimeError(
            f"Yahoo: aucune donnée pour {symbol}"
        )

    result = result[0]

    timestamps = (
        result.get("timestamp")
        or []
    )

    indicators = result.get(
        "indicators",
        {},
    )

    quote_list = (
        indicators.get("quote")
        or [{}]
    )

    quote = quote_list[0]

    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume")

    n = min(
        len(timestamps),
        len(opens),
        len(highs),
        len(lows),
        len(closes),
    )

    rows = []

    for i in range(n):

        volume_value = None

        if volumes is not None and i < len(volumes):
            volume_value = volumes[i]

        rows.append(
            {
                "open_time": pd.to_datetime(
                    timestamps[i],
                    unit="s",
                    utc=True,
                ),
                "open": opens[i],
                "high": highs[i],
                "low": lows[i],
                "close": closes[i],
                "volume": volume_value,
            }
        )

    out = pd.DataFrame(rows)

    if interval == "4h":
        # Normalisation en 1h avant agrégation.
        out = normalize_ohlcv(
            out,
            "1h",
            raw_limit,
        )

        out = aggregate_ohlcv(
            out,
            "4h",
        )

        out = out.tail(
            int(limit)
        ).reset_index(drop=True)

    else:
        out = normalize_ohlcv(
            out,
            interval,
            limit,
        )

    _cache_put(
        cache_key,
        out,
    )

    return out


# ============================================================
# TWELVE DATA
# ============================================================

def klines_twelvedata(
    symbol,
    interval="15m",
    limit=1000,
):
    """
    Récupère les données Twelve Data.

    Twelve Data est volontairement appelé uniquement lorsque
    le routeur le décide.

    Cela permet de préserver le quota quotidien.
    """

    if not TWELVE_DATA_API_KEY:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY non configurée"
        )

    cache_key = (
        "twelvedata",
        symbol,
        interval,
        int(limit),
    )

    cached = _cache_get(cache_key)

    if cached is not None:
        return cached

    td_interval = _TD_INTERVALS.get(interval)

    if not td_interval:
        raise ValueError(
            f"Intervalle Twelve Data non supporté: {interval}"
        )

    params = {
        "symbol": symbol,
        "interval": td_interval,
        "outputsize": min(
            int(limit),
            5000,
        ),
        "apikey": TWELVE_DATA_API_KEY,
        "format": "JSON",
        "timezone": "UTC",
    }

    last_error = None

    # Maximum 2 tentatives.
    # Cela évite de multiplier inutilement les appels.
    for attempt in range(2):

        try:

            data, _ = _request_json(
                TWELVE_DATA_BASE,
                params=params,
            )

            if (
                data.get("status") == "error"
                or data.get("code")
            ):
                raise RuntimeError(
                    data.get("message")
                    or str(data)
                )

            values = (
                data.get("values")
                or []
            )

            if not values:
                raise RuntimeError(
                    f"Twelve Data: aucune donnée pour {symbol}"
                )

            out = (
                pd.DataFrame(values)
                .rename(
                    columns={
                        "datetime": "open_time"
                    }
                )
            )

            out = normalize_ohlcv(
                out,
                interval,
                limit,
            )

            _cache_put(
                cache_key,
                out,
            )

            return out

        except Exception as exc:

            last_error = exc

            if attempt == 0:
                time.sleep(1)

    raise RuntimeError(
        f"Twelve Data {symbol}: {last_error}"
    )


# ============================================================
# FINNHUB
# ============================================================

def klines_finnhub(
    symbol,
    interval="15m",
    limit=1000,
):
    """
    Récupère les bougies Finnhub.

    Le volume n'est volontairement PAS inventé.

    Pour 4h :
        Finnhub 60m -> agrégation locale 4h.
    """

    if not FINNHUB_API_KEY:
        raise RuntimeError(
            "FINNHUB_API_KEY non configurée"
        )

    cache_key = (
        "finnhub",
        symbol,
        interval,
        int(limit),
    )

    cached = _cache_get(cache_key)

    if cached is not None:
        return cached

    resolution = _FH_RESOLUTION.get(interval)

    if not resolution:
        raise ValueError(
            f"Intervalle Finnhub non supporté: {interval}"
        )

    now = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    raw_limit = _required_raw_limit(
        interval,
        limit,
    )

    # Pour le 4h, raw_limit est exprimé en bougies 1h.
    required_seconds = (
        interval_to_seconds("1h")
        if interval == "4h"
        else interval_to_seconds(interval)
    )

    lookback = max(
        required_seconds * (raw_limit + 30),
        7 * 86400,
    )

    start = now - lookback

    params = {
        "symbol": symbol,
        "resolution": resolution,
        "from": start,
        "to": now,
        "token": FINNHUB_API_KEY,
    }

    data, _ = _request_json(
        FINNHUB_BASE,
        params=params,
    )

    status = data.get("s")

    if status != "ok":

        raise RuntimeError(
            f"Finnhub: "
            f"{status or 'unknown'} "
            f"pour {symbol}"
        )

    timestamps = data.get("t") or []
    opens = data.get("o") or []
    highs = data.get("h") or []
    lows = data.get("l") or []
    closes = data.get("c") or []

    n = min(
        len(timestamps),
        len(opens),
        len(highs),
        len(lows),
        len(closes),
    )

    rows = []

    for i in range(n):

        rows.append(
            {
                "open_time": pd.to_datetime(
                    timestamps[i],
                    unit="s",
                    utc=True,
                ),
                "open": opens[i],
                "high": highs[i],
                "low": lows[i],
                "close": closes[i],

                # IMPORTANT :
                # Finnhub ne nous fournit pas ici un volume
                # exploitable de manière générique.
                "volume": None,
            }
        )

    out = pd.DataFrame(rows)

    if interval == "4h":

        out = normalize_ohlcv(
            out,
            "1h",
            raw_limit,
        )

        out = aggregate_ohlcv(
            out,
            "4h",
        )

        out = out.tail(
            int(limit)
        ).reset_index(drop=True)

    else:

        out = normalize_ohlcv(
            out,
            interval,
            limit,
        )

    _cache_put(
        cache_key,
        out,
    )

    return out


# ============================================================
# BINANCE
# ============================================================

def klines_binance(
    symbol,
    interval="5m",
    limit=1000,
):
    """
    Récupère les klines Binance.

    Binance reste la source prioritaire et quasi exclusive
    pour les cryptos.
    """

    cache_key = (
        "binance",
        symbol,
        interval,
        int(limit),
    )

    cached = _cache_get(cache_key)

    if cached is not None:
        return cached

    url = (
        f"{BINANCE_BASE}/api/v3/klines"
    )

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": min(
            int(limit),
            1000,
        ),
    }

    data, _ = _request_json(
        url,
        params=params,
    )

    if not isinstance(data, list):
        raise RuntimeError(
            f"Binance: réponse invalide pour {symbol}"
        )

    rows = []

    for row in data:

        if len(row) < 6:
            continue

        rows.append(
            {
                "open_time": pd.to_datetime(
                    row[0],
                    unit="ms",
                    utc=True,
                ),
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5],
            }
        )

    out = normalize_ohlcv(
        pd.DataFrame(rows),
        interval,
        limit,
    )

    _cache_put(
        cache_key,
        out,
    )

    return out


# ============================================================
# ROUTEUR
# ============================================================

class DataRouter:
    """
    Routeur intelligent V4.2.

    Le routeur tient compte de :

    - classe d'actif ;
    - disponibilité du fournisseur ;
    - fraîcheur ;
    - nombre de bougies ;
    - disponibilité du volume ;
    - préférence éventuelle ;
    - budget Twelve Data.

    Il peut essayer plusieurs fournisseurs puis choisir
    le meilleur dataset obtenu.
    """

    PROVIDERS = (
        "binance",
        "yahoo",
        "finnhub",
        "twelvedata",
    )

    FUNCTIONS = {
        "binance": klines_binance,
        "yahoo": klines_yahoo,
        "finnhub": klines_finnhub,
        "twelvedata": klines_twelvedata,
    }

    def __init__(
        self,
        twelvedata_daily_budget=None,
    ):

        self.td_budget = int(
            twelvedata_daily_budget
            or TWELVEDATA_DAILY_BUDGET
        )

        self.td_used = 0

        self.td_day = (
            datetime.now(
                timezone.utc
            ).date()
        )

        self.stats = {
            provider: {
                "attempts": 0,
                "success": 0,
                "fail": 0,
            }
            for provider in self.PROVIDERS
        }

        self.last_provider = None
        self.last_metadata = {}

    # ========================================================
    # TWELVE DATA BUDGET
    # ========================================================

    def _reset_td_day(self):

        today = (
            datetime.now(
                timezone.utc
            ).date()
        )

        if today != self.td_day:

            self.td_day = today
            self.td_used = 0

    def _td_allowed(self):

        self._reset_td_day()

        return (
            bool(TWELVE_DATA_API_KEY)
            and self.td_used
            < self.td_budget
        )

    # ========================================================
    # APPEL FOURNISSEUR
    # ========================================================

    def _call(
        self,
        provider,
        symbol,
        interval,
        limit,
    ):

        if provider not in self.FUNCTIONS:
            raise ValueError(
                f"Fournisseur inconnu: {provider}"
            )

        if (
            provider == "twelvedata"
            and not self._td_allowed()
        ):
            raise RuntimeError(
                "Twelve Data: budget journalier "
                "atteint ou clé absente"
            )

        self.stats[
            provider
        ]["attempts"] += 1

        try:

            dataframe = self.FUNCTIONS[
                provider
            ](
                symbol,
                interval=interval,
                limit=limit,
            )

            if provider == "twelvedata":
                self.td_used += 1

            self.stats[
                provider
            ]["success"] += 1

            return dataframe

        except Exception:

            if provider == "twelvedata":

                # Par prudence, on réserve un crédit en cas
                # d'échec. Cela évite de dépasser le quota si
                # l'API a malgré tout consommé la requête.
                self.td_used += 1

            self.stats[
                provider
            ]["fail"] += 1

            raise

    # ========================================================
    # ORDRE DES FOURNISSEURS
    # ========================================================

    def _provider_order(
        self,
        asset_type,
        preferred=None,
        require_volume=True,
    ):
        """
        Détermine l'ordre initial des fournisseurs.

        Ce n'est qu'un ordre de recherche.
        Le classement final est effectué après examen réel
        des données.
        """

        asset_type = (
            str(asset_type)
            .lower()
            .strip()
        )

        if asset_type == "crypto":

            base = [
                "binance",
            ]

        elif asset_type == "forex":

            # Finnhub prioritaire pour éviter de gaspiller
            # Twelve Data.
            base = [
                "finnhub",
                "yahoo",
                "twelvedata",
            ]

        elif asset_type == "index":

            # Yahoo est particulièrement adapté aux indices.
            base = [
                "yahoo",
                "twelvedata",
                "finnhub",
            ]

        elif asset_type == "commodity":

            base = [
                "yahoo",
                "twelvedata",
                "finnhub",
            ]

        else:

            # Actions.
            base = [
                "finnhub",
                "yahoo",
                "twelvedata",
            ]

        # Si une préférence est explicitement demandée,
        # elle passe devant les autres fournisseurs disponibles.
        if preferred in base:

            base.remove(preferred)
            base.insert(
                0,
                preferred,
            )

        return base

    # ========================================================
    # MERGE DU VOLUME
    # ========================================================

    @staticmethod
    def _merge_volume(
        price_df,
        volume_df,
    ):
        """
        Ajoute un volume externe uniquement lorsque les timestamps
        correspondent exactement.

        On ne crée jamais de volume artificiel.
        """

        if (
            price_df is None
            or volume_df is None
            or price_df.empty
            or volume_df.empty
        ):
            return price_df

        if (
            "open_time" not in price_df.columns
            or "open_time" not in volume_df.columns
        ):
            return price_df

        if "volume" not in volume_df.columns:
            return price_df

        price = price_df.copy()

        volume = volume_df[
            [
                "open_time",
                "volume",
            ]
        ].copy()

        volume["open_time"] = pd.to_datetime(
            volume["open_time"],
            utc=True,
            errors="coerce",
        )

        volume["volume"] = pd.to_numeric(
            volume["volume"],
            errors="coerce",
        )

        volume = (
            volume
            .dropna(
                subset=[
                    "open_time",
                    "volume",
                ]
            )
            .drop_duplicates(
                "open_time",
                keep="last",
            )
        )

        if volume.empty:
            return price

        price = price.copy()

        price["open_time"] = pd.to_datetime(
            price["open_time"],
            utc=True,
            errors="coerce",
        )

        merged = price.drop(
            columns=["volume"],
            errors="ignore",
        ).merge(
            volume,
            on="open_time",
            how="left",
        )

        for column in STANDARD_COLUMNS:

            if column not in merged.columns:

                if column == "volume":
                    merged[column] = pd.NA
                else:
                    merged[column] = float("nan")

        return merged[
            STANDARD_COLUMNS
        ]

    # ========================================================
    # SCORE
    # ========================================================

    @staticmethod
    def _score_candidate(
        provider,
        quality,
        preferred=None,
        require_volume=True,
    ):
        """
        Score qualitatif d'un candidat.

        Priorités :
        1. validité
        2. fraîcheur
        3. volume si nécessaire
        4. couverture volume
        5. quantité de données
        6. préférence explicite
        """

        score = 0.0

        if quality["valid"]:
            score += 1000

        if quality["fresh"]:
            score += 500

        if require_volume:

            if (
                quality["volume_status"]
                == VOLUME_CONFIRMED
            ):
                score += 300

            elif (
                quality["volume_status"]
                == VOLUME_UNAVAILABLE
            ):
                score -= 50

            elif (
                quality["volume_status"]
                == VOLUME_INVALID
            ):
                score -= 500

            score += (
                quality["volume_coverage"]
                * 100
            )

        score += min(
            quality["candles"],
            1000,
        )

        if provider == preferred:
            score += 50

        # L'ordre de coût/priorité est volontairement :
        #
        # Binance : gratuit / excellent pour crypto
        # Yahoo   : gratuit
        # Finnhub : quota gratuit
        # TD      : quota plus précieux
        #
        # Mais le coût n'écrase jamais une vraie différence
        # de qualité.
        provider_bonus = {
            "binance": 40,
            "yahoo": 30,
            "finnhub": 20,
            "twelvedata": 0,
        }

        score += provider_bonus.get(
            provider,
            0,
        )

        return score

    # ========================================================
    # FETCH
    # ========================================================

    def fetch(
        self,
        symbol,
        interval="15m",
        limit=1000,
        asset_type="stock",
        preferred=None,
        require_volume=True,
        symbol_map=None,
    ):
        """
        Récupère un actif via le meilleur fournisseur disponible.

        Parameters
        ----------
        symbol :
            symbole par défaut.

        interval :
            1m, 5m, 15m, 30m, 1h, 4h, 1d...

        limit :
            nombre de bougies souhaité.

        asset_type :
            crypto / forex / stock / index / commodity.

        preferred :
            fournisseur préféré éventuel.

        require_volume :
            si False, un OHLC frais sans volume peut être accepté.

        symbol_map :
            dictionnaire du type :

                {
                    "yahoo": "AAPL",
                    "finnhub": "AAPL",
                    "twelvedata": "AAPL"
                }

        Returns
        -------
        DataFrame normalisé avec df.attrs :
            provider
            provider_symbol
            volume_status
            volume_coverage
            data_fresh
            data_age_seconds
            router_candidates
            router_errors
        """

        symbol_map = symbol_map or {}

        order = self._provider_order(
            asset_type,
            preferred,
            require_volume,
        )

        candidates = []
        errors = []

        min_candles = min(
            120,
            int(limit),
        )

        # ====================================================
        # PHASE 1 — RECHERCHE
        # ====================================================

        for provider in order:

            provider_symbol = (
                symbol_map.get(provider)
                or (
                    symbol
                    if provider == "binance"
                    else None
                )
            )

            # Ne jamais inventer un symbole pour un fournisseur.
            if not provider_symbol:
                errors.append(
                    f"{provider}: symbole absent"
                )
                continue

            try:

                dataframe = self._call(
                    provider,
                    provider_symbol,
                    interval,
                    limit,
                )

                quality = data_quality(
                    dataframe,
                    interval,
                    min_candles=min_candles,
                )

                if not quality["valid"]:

                    errors.append(
                        f"{provider}: "
                        f"données insuffisantes "
                        f"({quality['candles']} bougies)"
                    )

                    continue

                candidates.append(
                    (
                        provider,
                        provider_symbol,
                        dataframe,
                        quality,
                    )
                )

                # =================================================
                # ARRÊT RAPIDE
                # =================================================

                # Cas idéal :
                # données fraîches + volume confirmé.
                if (
                    quality["fresh"]
                    and quality["volume_status"]
                    == VOLUME_CONFIRMED
                ):
                    break

                # Si le volume n'est pas requis, on peut arrêter
                # dès qu'un dataset OHLC frais est obtenu.
                if (
                    quality["fresh"]
                    and not require_volume
                ):
                    break

            except Exception as exc:

                errors.append(
                    f"{provider}: {exc}"
                )

        # ====================================================
        # AUCUNE SOURCE
        # ====================================================

        if not candidates:

            message = (
                "Aucune source exploitable"
            )

            if errors:
                message += ": " + " | ".join(
                    errors
                )

            raise RuntimeError(message)

        # ====================================================
        # PHASE 2 — CLASSEMENT
        # ====================================================

        candidates.sort(
            key=lambda item: self._score_candidate(
                item[0],
                item[3],
                preferred=preferred,
                require_volume=require_volume,
            ),
            reverse=True,
        )

        (
            provider,
            provider_symbol,
            dataframe,
            quality,
        ) = candidates[0]

        # ====================================================
        # PHASE 3 — ENRICHISSEMENT VOLUME
        # ====================================================

        if (
            require_volume
            and quality["volume_status"]
            != VOLUME_CONFIRMED
        ):

            for (
                volume_provider,
                volume_symbol,
                volume_df,
                volume_quality,
            ) in candidates:

                if (
                    volume_provider == provider
                ):
                    continue

                if (
                    volume_quality[
                        "volume_status"
                    ]
                    != VOLUME_CONFIRMED
                ):
                    continue

                enriched = self._merge_volume(
                    dataframe,
                    volume_df,
                )

                enriched_quality = data_quality(
                    enriched,
                    interval,
                    min_candles=min_candles,
                )

                if (
                    enriched_quality[
                        "volume_status"
                    ]
                    == VOLUME_CONFIRMED
                ):

                    dataframe = enriched
                    quality = enriched_quality

                    break

        # ====================================================
        # NORMALISATION FINALE
        # ====================================================

        dataframe = normalize_ohlcv(
            dataframe,
            interval,
            limit,
        )

        final_volume_status = volume_status(
            dataframe
        )

        final_volume_coverage = volume_coverage(
            dataframe
        )

        final_quality = data_quality(
            dataframe,
            interval,
            min_candles=min_candles,
        )

        # ====================================================
        # MÉTADONNÉES
        # ====================================================

        dataframe.attrs[
            "provider"
        ] = provider

        dataframe.attrs[
            "provider_symbol"
        ] = provider_symbol

        dataframe.attrs[
            "asset_type"
        ] = asset_type

        dataframe.attrs[
            "interval"
        ] = interval

        dataframe.attrs[
            "volume_status"
        ] = final_volume_status

        dataframe.attrs[
            "volume_coverage"
        ] = final_volume_coverage

        dataframe.attrs[
            "data_fresh"
        ] = final_quality["fresh"]

        dataframe.attrs[
            "data_age_seconds"
        ] = final_quality["age_seconds"]

        dataframe.attrs[
            "candles"
        ] = final_quality["candles"]

        dataframe.attrs[
            "router_candidates"
        ] = [
            item[0]
            for item in candidates
        ]

        dataframe.attrs[
            "router_errors"
        ] = errors

        dataframe.attrs[
            "twelvedata_used"
        ] = self.td_used

        self.last_provider = provider

        self.last_metadata = {
            "provider": provider,
            "provider_symbol": provider_symbol,
            "asset_type": asset_type,
            "interval": interval,
            "volume_status": final_volume_status,
            "volume_coverage": final_volume_coverage,
            "fresh": final_quality["fresh"],
            "age_seconds": final_quality["age_seconds"],
            "candles": final_quality["candles"],
            "candidates": [
                item[0]
                for item in candidates
            ],
            "errors": errors,
            "twelvedata_used": self.td_used,
            "twelvedata_budget": self.td_budget,
        }

        return dataframe

    # ========================================================
    # STATISTIQUES
    # ========================================================

    def get_stats(self):
        """
        Retourne les statistiques du routeur.
        """

        self._reset_td_day()

        return {
            "providers": {
                provider: values.copy()
                for provider, values
                in self.stats.items()
            },
            "twelvedata": {
                "used": self.td_used,
                "budget": self.td_budget,
                "remaining": max(
                    0,
                    self.td_budget
                    - self.td_used,
                ),
            },
            "last_provider": self.last_provider,
            "last_metadata": self.last_metadata.copy(),
        }
```
