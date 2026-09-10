"""
V4.2 — Routeur intelligent multi-sources.

Sources :
    - Binance
    - Yahoo Finance
    - Finnhub
    - Twelve Data

Principes :
    - Priorité aux sources gratuites hors Twelve Data.
    - Twelve Data utilisé en fallback et avec quota journalier partagé.
    - Cache des requêtes identiques pendant un même run.
    - Bougies incomplètes supprimées.
    - Volume manquant = NaN, jamais 0.
    - Sélection de la meilleure source selon qualité/fraîcheur/volume.
    - Pas de Twelve Data si une source gratuite satisfaisante suffit.
"""

import copy
import math
import os
import threading
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv


load_dotenv()


# ======================================================================
# CONFIGURATION
# ======================================================================

BINANCE_DATA_URL = os.getenv(
    "BINANCE_DATA_URL",
    "https://data-api.binance.vision",
).rstrip("/")

BINANCE_URL = BINANCE_DATA_URL

YAHOO_BASE_URL = os.getenv(
    "YAHOO_BASE_URL",
    "https://query1.finance.yahoo.com",
).rstrip("/")

FINNHUB_BASE_URL = os.getenv(
    "FINNHUB_BASE_URL",
    "https://finnhub.io/api/v1",
).rstrip("/")

TWELVE_DATA_BASE_URL = os.getenv(
    "TWELVE_DATA_BASE_URL",
    "https://api.twelvedata.com",
).rstrip("/")


TWELVE_DATA_API_KEY = (
    os.getenv("TWELVE_DATA_API_KEY")
    or os.getenv("TWELVEDATA_API_KEY")
)

FINNHUB_API_KEY = (
    os.getenv("FINNHUB_API_KEY")
    or os.getenv("FINNHUB_API_TOKEN")
)


# ======================================================================
# VOLUME
# ======================================================================

VOLUME_CONFIRMED = "confirmed"
VOLUME_PARTIAL = "partial"
VOLUME_UNAVAILABLE = "unavailable"
VOLUME_INVALID = "invalid"

VOLUME_COVERAGE_THRESHOLD = float(
    os.getenv(
        "VOLUME_COVERAGE_THRESHOLD",
        "0.80",
    )
)


# ======================================================================
# TWELVE DATA QUOTA
# ======================================================================

TD_DAILY_LIMIT = int(
    os.getenv(
        "TWELVE_DATA_DAILY_LIMIT",
        os.getenv(
            "TWELVE_DATA_DAILY_CREDITS",
            "800",
        ),
    )
)

TWELVE_DATA_DAILY_LIMIT = TD_DAILY_LIMIT


# ======================================================================
# EXCEPTIONS
# ======================================================================

class DataSourceError(Exception):
    """Erreur globale du routeur."""


class ProviderError(Exception):
    """Erreur d'un fournisseur."""


# ======================================================================
# INTERVALLES
# ======================================================================

YAHOO_INTERVALS = {
    "1m": "1m",
    "2m": "2m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "60m": "60m",
    "1h": "60m",
    "1d": "1d",
    "1wk": "1wk",
    "1w": "1wk",
}


FINNHUB_INTERVALS = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "60m": "60",
    "1h": "60",
    "1d": "D",
    "1w": "W",
}


TWELVE_DATA_INTERVALS = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "45m": "45min",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "1d": "1day",
    "1w": "1week",
    "1wk": "1week",
}


# ======================================================================
# OUTILS
# ======================================================================

def _utc_now():
    return pd.Timestamp.now(tz="UTC")


def _safe_float(value, default=np.nan):
    try:
        value = float(value)

        if math.isnan(value):
            return default

        return value

    except Exception:
        return default


def _normalize_datetime(value):
    try:
        result = pd.to_datetime(
            value,
            utc=True,
            errors="coerce",
        )

        if pd.isna(result):
            return pd.NaT

        return result

    except Exception:
        return pd.NaT


# ======================================================================
# ROUTEUR
# ======================================================================

class DataRouter:
    """
    Routeur intelligent V4.2.

    Le quota Twelve Data est partagé entre toutes les instances
    du routeur dans le même processus.
    """

    _td_lock = threading.Lock()
    _td_date = None
    _td_used = 0

    def __init__(
        self,
        timeout=30,
        cache_enabled=True,
    ):
        self.timeout = int(timeout)
        self.cache_enabled = bool(cache_enabled)

        self._cache = {}

        self._calls = 0
        self._successes = 0
        self._failures = 0
        self._cache_hits = 0

        self._provider_calls = {
            "binance": 0,
            "yahoo": 0,
            "finnhub": 0,
            "twelve_data": 0,
        }

        self._provider_successes = {
            "binance": 0,
            "yahoo": 0,
            "finnhub": 0,
            "twelve_data": 0,
        }

        self._provider_failures = {
            "binance": 0,
            "yahoo": 0,
            "finnhub": 0,
            "twelve_data": 0,
        }

        self._volume_status_counts = {
            VOLUME_CONFIRMED: 0,
            VOLUME_PARTIAL: 0,
            VOLUME_UNAVAILABLE: 0,
            VOLUME_INVALID: 0,
        }

        self._last_errors = {}

    # ==================================================================
    # TWELVE DATA BUDGET
    # ==================================================================

    @classmethod
    def _reset_td_day_if_needed(cls):
        today = datetime.now(timezone.utc).date()

        with cls._td_lock:
            if cls._td_date != today:
                cls._td_date = today
                cls._td_used = 0

    @property
    def twelve_data_used(self):
        self._reset_td_day_if_needed()

        with self._td_lock:
            return self._td_used

    @property
    def twelve_data_remaining(self):
        self._reset_td_day_if_needed()

        with self._td_lock:
            return max(
                0,
                TD_DAILY_LIMIT - self._td_used,
            )

    @property
    def twelvedata_used(self):
        return self.twelve_data_used

    @property
    def twelvedata_remaining(self):
        return self.twelve_data_remaining

    @property
    def td_used(self):
        return self.twelve_data_used

    @property
    def td_remaining(self):
        return self.twelve_data_remaining

    def get_twelve_data_budget(self):
        return {
            "daily_limit": TD_DAILY_LIMIT,
            "used": self.twelve_data_used,
            "remaining": self.twelve_data_remaining,
        }

    def _reserve_twelve_data_credit(self):
        self._reset_td_day_if_needed()

        with self._td_lock:

            if self._td_used >= TD_DAILY_LIMIT:
                raise ProviderError(
                    "Twelve Data : quota journalière atteinte."
                )

            self._td_used += 1

    # ==================================================================
    # STATS
    # ==================================================================

    def router_stats(self):

        return {
            "calls": self._calls,
            "successes": self._successes,
            "failures": self._failures,
            "cache_hits": self._cache_hits,
            "provider_calls": dict(
                self._provider_calls
            ),
            "provider_successes": dict(
                self._provider_successes
            ),
            "provider_failures": dict(
                self._provider_failures
            ),
            "volume_status": dict(
                self._volume_status_counts
            ),
            "twelve_data": self.get_twelve_data_budget(),
            "last_errors": copy.deepcopy(
                self._last_errors
            ),
        }

    def stats(self):
        return self.router_stats()

    def get_stats(self):
        return self.router_stats()

    # ==================================================================
    # REQUÊTES HTTP
    # ==================================================================

    def _request_json(
        self,
        provider,
        url,
        params=None,
        headers=None,
    ):

        provider_key = provider.lower()

        self._calls += 1

        self._provider_calls.setdefault(
            provider_key,
            0,
        )

        self._provider_calls[
            provider_key
        ] += 1

        try:

            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=self.timeout,
            )

            if response.status_code == 429:
                raise ProviderError(
                    f"{provider}: HTTP 429 — limite de requêtes."
                )

            if response.status_code >= 500:
                raise ProviderError(
                    f"{provider}: HTTP {response.status_code}"
                )

            response.raise_for_status()

            data = response.json()

            if not isinstance(data, dict) and provider_key != "binance":
                raise ProviderError(
                    f"{provider}: réponse JSON invalide."
                )

            self._successes += 1

            self._provider_successes.setdefault(
                provider_key,
                0,
            )

            self._provider_successes[
                provider_key
            ] += 1

            return data

        except Exception as exc:

            self._failures += 1

            self._provider_failures.setdefault(
                provider_key,
                0,
            )

            self._provider_failures[
                provider_key
            ] += 1

            raise

    # ==================================================================
    # NORMALISATION
    # ==================================================================

    def _canonicalize(
        self,
        df,
    ):

        if df is None:
            return None

        df = df.copy()

        rename_map = {}

        for column in df.columns:

            lower = str(column).lower()

            if lower in {
                "datetime",
                "date",
                "timestamp",
                "time",
            }:
                rename_map[column] = "open_time"

            elif lower == "open":
                rename_map[column] = "open"

            elif lower == "high":
                rename_map[column] = "high"

            elif lower == "low":
                rename_map[column] = "low"

            elif lower == "close":
                rename_map[column] = "close"

            elif lower in {
                "volume",
                "vol",
            }:
                rename_map[column] = "volume"

        df = df.rename(
            columns=rename_map
        )

        required = [
            "open_time",
            "open",
            "high",
            "low",
            "close",
        ]

        for column in required:

            if column not in df.columns:
                return None

        if "volume" not in df.columns:
            df["volume"] = np.nan

        df["open_time"] = pd.to_datetime(
            df["open_time"],
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

            df[column] = pd.to_numeric(
                df[column],
                errors="coerce",
            )

        df = df.dropna(
            subset=[
                "open_time",
                "open",
                "high",
                "low",
                "close",
            ]
        )

        df = (
            df.sort_values("open_time")
            .drop_duplicates(
                subset=["open_time"],
                keep="last",
            )
            .reset_index(drop=True)
        )

        return df

    # ==================================================================
    # VOLUME
    # ==================================================================

    def _volume_status(
        self,
        df,
    ):

        if df is None or df.empty:
            return (
                VOLUME_INVALID,
                0.0,
            )

        if "volume" not in df.columns:
            return (
                VOLUME_UNAVAILABLE,
                0.0,
            )

        numeric = pd.to_numeric(
            df["volume"],
            errors="coerce",
        )

        valid = numeric.notna() & np.isfinite(
            numeric
        )

        coverage = (
            float(valid.mean())
            if len(valid)
            else 0.0
        )

        if coverage >= VOLUME_COVERAGE_THRESHOLD:

            return (
                VOLUME_CONFIRMED,
                coverage,
            )

        if coverage > 0:

            return (
                VOLUME_PARTIAL,
                coverage,
            )

        return (
            VOLUME_UNAVAILABLE,
            0.0,
        )

    # ==================================================================
    # BOUGIE INCOMPLÈTE
    # ==================================================================

    def _drop_incomplete_last_candle(
        self,
        df,
        interval,
    ):

        if df is None or df.empty:
            return df

        df = df.copy()

        interval_minutes = {
            "1m": 1,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "45m": 45,
            "1h": 60,
            "2h": 120,
            "4h": 240,
            "1d": 1440,
            "1w": 10080,
            "1wk": 10080,
        }.get(
            interval,
            None,
        )

        if interval_minutes is None:
            return df

        last_open = df["open_time"].iloc[-1]

        if pd.isna(last_open):
            return df

        expected_close = (
            last_open
            + pd.Timedelta(
                minutes=interval_minutes
            )
        )

        now = _utc_now()

        if expected_close > now:
            df = df.iloc[:-1].copy()

        return df

    # ==================================================================
    # METADATA
    # ==================================================================

    def _attach_metadata(
        self,
        df,
        provider,
        provider_symbol,
        requested_interval,
        volume_status,
        volume_coverage,
    ):

        if df is None or df.empty:
            return df

        df = df.copy()

        last_candle = df[
            "open_time"
        ].max()

        age_minutes = np.nan

        if pd.notna(last_candle):

            age_minutes = (
                _utc_now()
                - last_candle
            ).total_seconds() / 60.0

        df["provider"] = provider

        df["provider_symbol"] = provider_symbol

        df["requested_interval"] = (
            requested_interval
        )

        df["volume_status"] = (
            volume_status
        )

        df["volume_coverage"] = (
            volume_coverage
        )

        df["last_candle"] = (
            last_candle
        )

        df["age_minutes"] = (
            age_minutes
        )

        df["is_stale"] = bool(
            pd.notna(age_minutes)
            and age_minutes > 180
        )

        df.attrs["provider"] = provider
        df.attrs["provider_symbol"] = (
            provider_symbol
        )
        df.attrs["volume_status"] = (
            volume_status
        )
        df.attrs["volume_coverage"] = (
            volume_coverage
        )
        df.attrs["last_candle"] = (
            last_candle
        )
        df.attrs["age_minutes"] = (
            age_minutes
        )

        return df

    # ==================================================================
    # BINANCE
    # ==================================================================

    def _fetch_binance(
        self,
        symbol,
        interval,
        limit,
    ):

        url = (
            f"{BINANCE_DATA_URL}"
            "/api/v3/klines"
        )

        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": min(
                int(limit),
                1000,
            ),
        }

        response = requests.get(
            url,
            params=params,
            timeout=self.timeout,
        )

        self._calls += 1
        self._provider_calls["binance"] += 1

        try:

            if response.status_code == 429:
                raise ProviderError(
                    "binance: HTTP 429"
                )

            response.raise_for_status()

            raw = response.json()

            if not raw:
                raise ProviderError(
                    f"Binance: aucune donnée pour {symbol}"
                )

            columns = [
                "open_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_volume",
                "trades",
                "taker_buy_base",
                "taker_buy_quote",
                "ignore",
            ]

            df = pd.DataFrame(
                raw,
                columns=columns,
            )

            df["open_time"] = pd.to_datetime(
                df["open_time"],
                unit="ms",
                utc=True,
            )

            df["close_time"] = pd.to_datetime(
                df["close_time"],
                unit="ms",
                utc=True,
            )

            for column in [
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]:

                df[column] = pd.to_numeric(
                    df[column],
                    errors="coerce",
                )

            df = df.dropna(
                subset=[
                    "open_time",
                    "open",
                    "high",
                    "low",
                    "close",
                ]
            )

            now = _utc_now()

            df = df[
                df["close_time"] < now
            ].copy()

            self._successes += 1
            self._provider_successes[
                "binance"
            ] += 1

            return df

        except Exception:

            self._failures += 1
            self._provider_failures[
                "binance"
            ] += 1

            raise

    # ==================================================================
    # YAHOO
    # ==================================================================

    def _fetch_yahoo(
        self,
        symbol,
        interval,
        limit,
    ):

        yahoo_interval = (
            YAHOO_INTERVALS.get(
                interval,
                interval,
            )
        )

        period2 = int(
            time.time()
        )

        period1 = (
            period2
            - max(
                int(limit) * 3600,
                86400,
            )
        )

        url = (
            f"{YAHOO_BASE_URL}"
            f"/v8/finance/chart/{symbol}"
        )

        params = {
            "period1": period1,
            "period2": period2,
            "interval": yahoo_interval,
            "events": "history",
            "includeAdjustedClose": "true",
        }

        data = self._request_json(
            "yahoo",
            url,
            params=params,
        )

        chart = (
            data.get("chart", {})
            .get("result")
        )

        if not chart:
            raise ProviderError(
                f"Yahoo: aucune donnée pour {symbol}"
            )

        result = chart[0]

        timestamps = result.get(
            "timestamp",
            [],
        )

        quote = (
            result.get(
                "indicators",
                {},
            )
            .get(
                "quote",
                [{}],
            )[0]
        )

        if not timestamps:
            raise ProviderError(
                f"Yahoo: timestamps absents pour {symbol}"
            )

        df = pd.DataFrame(
            {
                "open_time": pd.to_datetime(
                    timestamps,
                    unit="s",
                    utc=True,
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

        return df

    # ==================================================================
    # FINNHUB
    # ==================================================================

    def _fetch_finnhub(
        self,
        symbol,
        interval,
        limit,
        asset_type,
    ):

        if not FINNHUB_API_KEY:

            raise ProviderError(
                "Finnhub: API key absente."
            )

        resolution = FINNHUB_INTERVALS.get(
            interval,
            "15",
        )

        now = int(
            time.time()
        )

        minutes_map = {
            "1m": 1,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "60m": 60,
            "1h": 60,
        }

        minutes = minutes_map.get(
            interval,
            15,
        )

        start = (
            now
            - int(limit)
            * minutes
            * 60
        )

        if asset_type == "forex":

            finnhub_symbol = symbol

            url = (
                f"{FINNHUB_BASE_URL}"
                "/forex/candle"
            )

            params = {
                "symbol": finnhub_symbol,
                "resolution": resolution,
                "from": start,
                "to": now,
                "token": FINNHUB_API_KEY,
            }

        else:

            url = (
                f"{FINNHUB_BASE_URL}"
                "/stock/candle"
            )

            params = {
                "symbol": symbol,
                "resolution": resolution,
                "from": start,
                "to": now,
                "token": FINNHUB_API_KEY,
            }

        data = self._request_json(
            "finnhub",
            url,
            params=params,
        )

        if data.get("s") != "ok":
            raise ProviderError(
                f"Finnhub: statut {data.get('s')}"
            )

        timestamps = data.get(
            "t",
            [],
        )

        if not timestamps:
            raise ProviderError(
                f"Finnhub: aucune donnée pour {symbol}"
            )

        df = pd.DataFrame(
            {
                "open_time": pd.to_datetime(
                    timestamps,
                    unit="s",
                    utc=True,
                ),
                "open": data.get(
                    "o",
                    [],
                ),
                "high": data.get(
                    "h",
                    [],
                ),
                "low": data.get(
                    "l",
                    [],
                ),
                "close": data.get(
                    "c",
                    [],
                ),
                "volume": data.get(
                    "v",
                    [],
                ),
            }
        )

        return df

    # ==================================================================
    # TWELVE DATA
    # ==================================================================

    def _fetch_twelvedata(
        self,
        symbol,
        interval,
        limit,
    ):

        if not TWELVE_DATA_API_KEY:

            raise ProviderError(
                "Twelve Data: API key absente."
            )

        self._reserve_twelve_data_credit()

        td_interval = (
            TWELVE_DATA_INTERVALS.get(
                interval,
                interval,
            )
        )

        url = (
            f"{TWELVE_DATA_BASE_URL}"
            "/time_series"
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
        }

        data = self._request_json(
            "twelve_data",
            url,
            params=params,
        )

        if "status" in data:
            if data.get("status") == "error":
                raise ProviderError(
                    "Twelve Data: "
                    + str(
                        data.get(
                            "message",
                            "erreur inconnue",
                        )
                    )
                )

        values = data.get(
            "values"
        )

        if not values:
            raise ProviderError(
                f"Twelve Data: aucune donnée pour {symbol}"
            )

        rows = []

        for item in values:

            rows.append(
                {
                    "open_time": item.get(
                        "datetime"
                    ),
                    "open": item.get(
                        "open"
                    ),
                    "high": item.get(
                        "high"
                    ),
                    "low": item.get(
                        "low"
                    ),
                    "close": item.get(
                        "close"
                    ),
                    "volume": item.get(
                        "volume",
                        np.nan,
                    ),
                }
            )

        return pd.DataFrame(rows)

    # ==================================================================
    # AGRÉGATION INTRADAY
    # ==================================================================

    def _aggregate_intraday(
        self,
        df,
        target_minutes,
    ):

        if df is None or df.empty:
            return df

        df = df.copy()

        df = df.set_index(
            "open_time"
        )

        rule = f"{int(target_minutes)}min"

        result = pd.DataFrame()

        result["open"] = (
            df["open"]
            .resample(rule)
            .first()
        )

        result["high"] = (
            df["high"]
            .resample(rule)
            .max()
        )

        result["low"] = (
            df["low"]
            .resample(rule)
            .min()
        )

        result["close"] = (
            df["close"]
            .resample(rule)
            .last()
        )

        if "volume" in df.columns:

            volume = df["volume"]

            counts = (
                volume
                .resample(rule)
                .count()
            )

            expected = (
                volume
                .resample(rule)
                .size()
            )

            summed = (
                volume
                .resample(rule)
                .sum(
                    min_count=1
                )
            )

            result["volume"] = summed.where(
                counts >= expected
            )

        else:

            result["volume"] = np.nan

        result = (
            result
            .dropna(
                subset=[
                    "open",
                    "high",
                    "low",
                    "close",
                ]
            )
            .reset_index()
        )

        return result

    # ==================================================================
    # SYMBOL MAP
    # ==================================================================

    def _resolve_symbol(
        self,
        symbol,
        provider,
        symbol_map=None,
    ):

        if not symbol_map:
            return symbol

        provider_key = provider.lower()

        if symbol in symbol_map:

            mapping = symbol_map[symbol]

            if isinstance(
                mapping,
                str,
            ):
                return mapping

            if isinstance(
                mapping,
                dict,
            ):

                aliases = {
                    provider_key,
                    provider_key.replace(
                        "_",
                        "",
                    ),
                    "twelvedata"
                    if provider_key
                    == "twelve_data"
                    else provider_key,
                }

                for key in aliases:

                    value = mapping.get(
                        key
                    )

                    if value:
                        return value

        if isinstance(
            symbol_map,
            dict,
        ):

            value = symbol_map.get(
                provider_key
            )

            if isinstance(
                value,
                str,
            ):
                return value

        return symbol

    # ==================================================================
    # QUALITÉ
    # ==================================================================

    def _quality_score(
        self,
        df,
        provider,
        volume_status,
        require_volume,
    ):

        if df is None or df.empty:
            return -999.0

        score = 0.0

        if volume_status == VOLUME_CONFIRMED:
            score += 30

        elif volume_status == VOLUME_PARTIAL:
            score += 15

        elif volume_status == VOLUME_UNAVAILABLE:
            score -= (
                20
                if require_volume
                else 0
            )

        age = df.attrs.get(
            "age_minutes",
            np.nan,
        )

        if pd.notna(age):

            if age <= 15:
                score += 30

            elif age <= 60:
                score += 20

            elif age <= 180:
                score += 10

            else:
                score -= 20

        quantity = min(
            len(df),
            300,
        )

        score += (
            quantity / 300
        ) * 20

        preferred = {
            "binance": 12,
            "finnhub": 10,
            "yahoo": 8,
            "twelve_data": 5,
        }

        score += preferred.get(
            provider,
            0,
        )

        return score

    # ==================================================================
    # CANDIDATS
    # ==================================================================

    def _providers_for(
        self,
        asset_type,
    ):

        mapping = {

            "crypto": [
                "binance",
                "yahoo",
                "twelve_data",
            ],

            "stock": [
                "finnhub",
                "yahoo",
                "twelve_data",
            ],

            "forex": [
                "finnhub",
                "yahoo",
                "twelve_data",
            ],

            "index": [
                "yahoo",
                "finnhub",
                "twelve_data",
            ],

            "commodity": [
                "yahoo",
                "finnhub",
                "twelve_data",
            ],
        }

        return mapping.get(
            asset_type,
            [
                "yahoo",
                "finnhub",
                "twelve_data",
            ],
        )

    # ==================================================================
    # FETCH PRINCIPAL
    # ==================================================================

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

        cache_key = (
            symbol,
            interval,
            int(limit),
            asset_type,
            bool(require_volume),
            str(symbol_map),
        )

        if (
            self.cache_enabled
            and cache_key in self._cache
        ):

            self._cache_hits += 1

            return self._cache[
                cache_key
            ].copy()

        providers = self._providers_for(
            asset_type
        )

        if preferred:

            providers = [
                preferred
            ] + [
                provider
                for provider in providers
                if provider != preferred
            ]

        candidates = []

        errors = {}

        for provider in providers:

            try:

                provider_symbol = (
                    self._resolve_symbol(
                        symbol,
                        provider,
                        symbol_map,
                    )
                )

                if provider == "binance":

                    df = self._fetch_binance(
                        provider_symbol,
                        interval,
                        limit,
                    )

                elif provider == "yahoo":

                    df = self._fetch_yahoo(
                        provider_symbol,
                        interval,
                        limit,
                    )

                elif provider == "finnhub":

                    df = self._fetch_finnhub(
                        provider_symbol,
                        interval,
                        limit,
                        asset_type,
                    )

                elif provider == "twelve_data":

                    df = self._fetch_twelvedata(
                        provider_symbol,
                        interval,
                        limit,
                    )

                else:

                    raise ProviderError(
                        f"Provider inconnu : {provider}"
                    )

                df = self._canonicalize(
                    df
                )

                if df is None or df.empty:

                    raise ProviderError(
                        f"{provider}: "
                        "données vides après normalisation."
                    )

                df = (
                    self._drop_incomplete_last_candle(
                        df,
                        interval,
                    )
                )

                if (
                    df is None
                    or df.empty
                ):
                    raise ProviderError(
                        f"{provider}: "
                        "aucune bougie complète."
                    )

                if len(df) < 10:

                    raise ProviderError(
                        f"{provider}: "
                        f"seulement {len(df)} bougies."
                    )

                volume_status, volume_coverage = (
                    self._volume_status(df)
                )

                df = self._attach_metadata(
                    df,
                    provider,
                    provider_symbol,
                    interval,
                    volume_status,
                    volume_coverage,
                )

                self._volume_status_counts[
                    volume_status
                ] += 1

                quality = self._quality_score(
                    df,
                    provider,
                    volume_status,
                    require_volume,
                )

                df.attrs["quality_score"] = (
                    quality
                )

                candidates.append(
                    (
                        quality,
                        provider,
                        df,
                    )
                )

                # ------------------------------------------------------
                # ARRÊT INTELLIGENT
                #
                # Si le volume est requis :
                #   on veut idéalement un volume confirmé.
                #
                # Si le volume n'est pas requis :
                #   une source fraîche et suffisamment complète suffit.
                #
                # Cela évite de consommer inutilement Twelve Data
                # pour le Forex / indices.
                # ------------------------------------------------------

                if len(df) >= min(
                    int(limit),
                    120,
                ):

                    if (
                        volume_status
                        == VOLUME_CONFIRMED
                    ):

                        break

                    if not require_volume:

                        break

            except Exception as exc:

                errors[
                    provider
                ] = str(exc)

                self._last_errors[
                    symbol
                ] = dict(errors)

                continue

        if not candidates:

            error_text = " | ".join(
                f"{provider} ({symbol}) : {message}"
                for provider, message
                in errors.items()
            )

            raise DataSourceError(
                f"Aucune source disponible "
                f"pour {symbol} "
                f"[{asset_type}/{interval}]. "
                f"{error_text}"
            )

        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        best_quality, best_provider, best_df = (
            candidates[0]
        )

        best_df = best_df.copy()

        best_df.attrs[
            "quality_score"
        ] = best_quality

        best_df.attrs[
            "provider"
        ] = best_provider

        if self.cache_enabled:

            self._cache[
                cache_key
            ] = best_df.copy()

        return best_df


# ======================================================================
# COMPATIBILITÉ GLOBALE
# ======================================================================

def get_twelve_data_budget():

    DataRouter._reset_td_day_if_needed()

    with DataRouter._td_lock:

        return {
            "daily_limit": TD_DAILY_LIMIT,
            "used": DataRouter._td_used,
            "remaining": max(
                0,
                TD_DAILY_LIMIT
                - DataRouter._td_used,
            ),
        }


def twelve_data_budget():

    return get_twelve_data_budget()


# ======================================================================
# COMPATIBILITÉ — ANCIENNES FONCTIONS
# ======================================================================

def klines_yahoo(
    symbol,
    interval="15m",
    limit=1000,
):

    router = DataRouter()

    return router._fetch_yahoo(
        symbol,
        interval,
        limit,
    )


def klines_twelvedata(
    symbol,
    interval="15m",
    limit=1000,
):

    router = DataRouter()

    return router._fetch_twelvedata(
        symbol,
        interval,
        limit,
    )
