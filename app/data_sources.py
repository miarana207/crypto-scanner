"""
V4.2 — Routeur intelligent multi-sources de données.

Sources :
    - Binance
    - Yahoo Finance
    - Finnhub
    - Twelve Data

Fonctions principales :
    - routage intelligent selon le type d'actif ;
    - fallback automatique ;
    - priorité aux sources gratuites ;
    - utilisation de Twelve Data en dernier recours ;
    - gestion du quota journalier Twelve Data ;
    - cache des requêtes identiques ;
    - gestion robuste des volumes absents ;
    - suppression des bougies incomplètes ;
    - agrégation 2h / 4h ;
    - métadonnées de fraîcheur et de qualité ;
    - compatibilité avec les anciens noms de constantes V4.x.
"""

from __future__ import annotations

import copy
import math
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests


# ============================================================
# CONFIGURATION
# ============================================================

BINANCE_DATA_URL = os.getenv(
    "BINANCE_DATA_URL",
    "https://data-api.binance.vision",
).rstrip("/")

# Alias de compatibilité
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


# ============================================================
# CLÉS API
# ============================================================

TWELVE_DATA_API_KEY = (
    os.getenv("TWELVE_DATA_API_KEY")
    or os.getenv("TWELVEDATA_API_KEY")
    or ""
).strip()

FINNHUB_API_KEY = (
    os.getenv("FINNHUB_API_KEY")
    or os.getenv("FINNHUB_API_TOKEN")
    or ""
).strip()


# ============================================================
# STATUTS DE VOLUME
# ============================================================

VOLUME_CONFIRMED = "VOLUME_CONFIRMED"
VOLUME_PARTIAL = "VOLUME_PARTIAL"
VOLUME_UNAVAILABLE = "VOLUME_UNAVAILABLE"

# Compatibilité avec le code V4.x précédent.
# VOLUME_INVALID signifie qu'un volume fourni est inutilisable.
VOLUME_INVALID = "VOLUME_INVALID"

VOLUME_COVERAGE_THRESHOLD = float(
    os.getenv("VOLUME_COVERAGE_THRESHOLD", "0.80")
)


# ============================================================
# QUOTA TWELVE DATA
# ============================================================

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


# ============================================================
# EXCEPTIONS
# ============================================================

class DataSourceError(RuntimeError):
    """Aucune source exploitable."""


class ProviderError(RuntimeError):
    """Erreur d'un fournisseur."""


# ============================================================
# OUTILS
# ============================================================

def interval_to_minutes(interval: str) -> int:
    """Convertit un intervalle en minutes."""

    value = str(interval or "").strip().lower()

    mapping = {
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
    }

    if value in mapping:
        return mapping[value]

    if value.endswith("min"):
        return int(value[:-3])

    if value.endswith("m"):
        return int(value[:-1])

    if value.endswith("h"):
        return int(value[:-1]) * 60

    if value.endswith("d"):
        return int(value[:-1]) * 1440

    if value.endswith("w"):
        return int(value[:-1]) * 10080

    raise ValueError(
        f"Intervalle non supporté : {interval}"
    )


def _interval_to_yahoo(interval: str) -> str:
    value = str(interval).lower().strip()

    mapping = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "45m": "15m",
        "1h": "1h",
        "2h": "1h",
        "4h": "1h",
        "1d": "1d",
        "1w": "1wk",
        "1wk": "1wk",
    }

    return mapping.get(value, value)


def _interval_to_finnhub(interval: str) -> str:
    value = str(interval).lower().strip()

    mapping = {
        "1m": "1",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "45m": "15",
        "1h": "60",
        "2h": "60",
        "4h": "60",
        "1d": "D",
        "1w": "W",
        "1wk": "W",
    }

    return mapping.get(value, value)


def _interval_to_twelve_data(interval: str) -> str:
    value = str(interval).lower().strip()

    mapping = {
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

    return mapping.get(value, value)


def _utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def _copy_df(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy(deep=True)
    result.attrs = copy.deepcopy(df.attrs)
    return result


# ============================================================
# ROUTEUR
# ============================================================

class DataRouter:

    _td_lock = threading.Lock()
    _td_date: Optional[str] = None
    _td_used: int = 0

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout: int = 20,
    ):
        self.session = session or requests.Session()
        self.timeout = int(timeout)

        self.session.headers.update(
            {
                "User-Agent":
                    "CryptoScanner-V4.2/1.0"
            }
        )

        # Cache par exécution.
        self._cache: Dict[
            Tuple[Any, ...],
            pd.DataFrame
        ] = {}

        self._cache_hits = 0

        # Statistiques.
        self._calls = 0
        self._successes = 0
        self._failures = 0

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

        self._last_errors: Dict[
            str,
            List[str]
        ] = {}

    # ========================================================
    # TWELVE DATA
    # ========================================================

    @classmethod
    def _reset_td_counter_if_needed(cls):
        today = (
            datetime.now(timezone.utc)
            .date()
            .isoformat()
        )

        with cls._td_lock:
            if cls._td_date != today:
                cls._td_date = today
                cls._td_used = 0

    @classmethod
    def twelve_data_used_count(cls) -> int:
        cls._reset_td_counter_if_needed()

        with cls._td_lock:
            return cls._td_used

    @classmethod
    def twelve_data_remaining_count(cls) -> int:
        cls._reset_td_counter_if_needed()

        with cls._td_lock:
            return max(
                0,
                TD_DAILY_LIMIT - cls._td_used,
            )

    @classmethod
    def _reserve_twelve_data_credit(cls) -> bool:
        cls._reset_td_counter_if_needed()

        with cls._td_lock:
            if cls._td_used >= TD_DAILY_LIMIT:
                return False

            cls._td_used += 1
            return True

    @property
    def twelve_data_used(self) -> int:
        return self.twelve_data_used_count()

    @property
    def twelve_data_remaining(self) -> int:
        return self.twelve_data_remaining_count()

    @property
    def twelvedata_used(self) -> int:
        return self.twelve_data_used_count()

    @property
    def twelvedata_remaining(self) -> int:
        return self.twelve_data_remaining_count()

    @property
    def td_used(self) -> int:
        return self.twelve_data_used_count()

    @property
    def td_remaining(self) -> int:
        return self.twelve_data_remaining_count()

    def get_twelve_data_budget(self):
        return {
            "daily_limit": TD_DAILY_LIMIT,
            "used": self.twelve_data_used_count(),
            "remaining":
                self.twelve_data_remaining_count(),
        }

    # ========================================================
    # STATISTIQUES
    # ========================================================

    def router_stats(self):
        return {
            "calls": self._calls,
            "successes": self._successes,
            "failures": self._failures,
            "cache_hits": self._cache_hits,
            "provider_calls":
                dict(self._provider_calls),
            "provider_successes":
                dict(self._provider_successes),
            "provider_failures":
                dict(self._provider_failures),
            "volume_status":
                dict(self._volume_status_counts),
            "twelve_data":
                self.get_twelve_data_budget(),
            "last_errors":
                copy.deepcopy(self._last_errors),
        }

    def stats(self):
        return self.router_stats()

    def get_stats(self):
        return self.router_stats()

    # ========================================================
    # TYPE D'ACTIF
    # ========================================================

    @staticmethod
    def _normalise_asset_type(
        asset_type: str
    ) -> str:

        value = str(
            asset_type or "stock"
        ).lower().strip()

        aliases = {
            "stocks": "stock",
            "actions": "stock",
            "equity": "stock",
            "equities": "stock",

            "fx": "forex",
            "currency": "forex",
            "currencies": "forex",

            "indices": "index",
            "indice": "index",

            "cryptocurrency": "crypto",
            "cryptocurrencies": "crypto",

            "commodities": "commodity",
            "matiere": "commodity",
            "matières": "commodity",
        }

        return aliases.get(
            value,
            value,
        )

    # ========================================================
    # ORDRE DES FOURNISSEURS
    # ========================================================

    def _provider_candidates(
        self,
        asset_type: str,
        preferred: Optional[str],
    ) -> List[str]:

        asset_type = self._normalise_asset_type(
            asset_type
        )

        if asset_type == "crypto":
            providers = [
                "binance",
                "yahoo",
                "twelve_data",
            ]

        elif asset_type == "stock":
            providers = [
                "finnhub",
                "yahoo",
                "twelve_data",
            ]

        elif asset_type == "forex":
            providers = [
                "finnhub",
                "yahoo",
                "twelve_data",
            ]

        elif asset_type == "index":
            providers = [
                "yahoo",
                "finnhub",
                "twelve_data",
            ]

        elif asset_type == "commodity":
            providers = [
                "yahoo",
                "finnhub",
                "twelve_data",
            ]

        else:
            providers = [
                "yahoo",
                "finnhub",
                "twelve_data",
            ]

        if preferred:
            preferred = str(
                preferred
            ).lower().strip()

            if preferred in providers:
                providers.remove(
                    preferred
                )
                providers.insert(
                    0,
                    preferred,
                )

        return providers

    # ========================================================
    # HTTP JSON
    # ========================================================

    def _request_json(
        self,
        provider: str,
        url: str,
        params: Optional[
            Dict[str, Any]
        ] = None,
    ):

        provider = provider.lower()

        self._calls += 1
        self._provider_calls[
            provider
        ] += 1

        try:
            response = self.session.get(
                url,
                params=params,
                timeout=self.timeout,
            )

            if response.status_code == 429:
                raise ProviderError(
                    f"{provider}: HTTP 429"
                )

            if response.status_code >= 500:
                raise ProviderError(
                    f"{provider}: HTTP "
                    f"{response.status_code}"
                )

            response.raise_for_status()

            data = response.json()

            if not isinstance(
                data,
                dict,
            ):
                raise ProviderError(
                    f"{provider}: JSON inattendu"
                )

            self._provider_successes[
                provider
            ] += 1

            return data

        except Exception:
            self._provider_failures[
                provider
            ] += 1
            raise

    # ========================================================
    # DATAFRAME CANONIQUE
    # ========================================================

    @staticmethod
    def _canonicalize(
        rows: Iterable[
            Dict[str, Any]
        ],
    ) -> pd.DataFrame:

        columns = [
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]

        df = pd.DataFrame(
            list(rows)
        )

        if df.empty:
            return pd.DataFrame(
                columns=columns
            )

        for column in columns:
            if column not in df.columns:
                df[column] = np.nan

        df = df[columns].copy()

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
                "open_time",
                keep="last",
            )
            .reset_index(drop=True)
        )

        return df

    # ========================================================
    # VOLUME
    # ========================================================

    @staticmethod
    def _volume_status(
        df: pd.DataFrame,
    ) -> Tuple[str, float]:

        if (
            df is None
            or df.empty
            or "volume" not in df.columns
        ):
            return (
                VOLUME_UNAVAILABLE,
                0.0,
            )

        volume = pd.to_numeric(
            df["volume"],
            errors="coerce",
        )

        available = volume.notna()

        coverage = (
            float(available.mean())
            if len(available)
            else 0.0
        )

        if available.sum() == 0:
            return (
                VOLUME_UNAVAILABLE,
                coverage,
            )

        # IMPORTANT :
        # zéro réel reste zéro.
        # On ne convertit jamais NaN en zéro.

        if coverage >= (
            VOLUME_COVERAGE_THRESHOLD
        ):
            return (
                VOLUME_CONFIRMED,
                coverage,
            )

        return (
            VOLUME_PARTIAL,
            coverage,
        )

    # ========================================================
    # BOUGIE INCOMPLÈTE
    # ========================================================

    @staticmethod
    def _drop_incomplete_last_candle(
        df: pd.DataFrame,
        interval: str,
    ) -> pd.DataFrame:

        if df.empty:
            return df

        minutes = interval_to_minutes(
            interval
        )

        last_time = pd.Timestamp(
            df.iloc[-1]["open_time"]
        )

        if last_time.tzinfo is None:
            last_time = (
                last_time
                .tz_localize("UTC")
            )
        else:
            last_time = (
                last_time
                .tz_convert("UTC")
            )

        candle_end = (
            last_time
            + pd.Timedelta(
                minutes=minutes
            )
        )

        if candle_end > _utc_now():
            return (
                df.iloc[:-1]
                .reset_index(drop=True)
            )

        return df.reset_index(
            drop=True
        )

    # ========================================================
    # MÉTADONNÉES
    # ========================================================

    @staticmethod
    def _attach_metadata(
        df: pd.DataFrame,
        provider: str,
        provider_symbol: str,
        requested_interval: str,
    ) -> pd.DataFrame:

        df = df.copy()

        status, coverage = (
            DataRouter._volume_status(df)
        )

        if df.empty:
            last_candle = pd.NaT
            age_minutes = float("nan")

        else:
            last_candle = pd.Timestamp(
                df.iloc[-1]["open_time"]
            )

            if last_candle.tzinfo is None:
                last_candle = (
                    last_candle
                    .tz_localize("UTC")
                )
            else:
                last_candle = (
                    last_candle
                    .tz_convert("UTC")
                )

            age_minutes = (
                _utc_now()
                - last_candle
            ).total_seconds() / 60.0

        interval_minutes = (
            interval_to_minutes(
                requested_interval
            )
        )

        df.attrs[
            "provider"
        ] = provider

        df.attrs[
            "provider_symbol"
        ] = provider_symbol

        df.attrs[
            "requested_interval"
        ] = requested_interval

        df.attrs[
            "volume_status"
        ] = status

        df.attrs[
            "volume_coverage"
        ] = coverage

        df.attrs[
            "last_candle"
        ] = last_candle

        df.attrs[
            "age_minutes"
        ] = age_minutes

        df.attrs[
            "is_stale"
        ] = bool(
            pd.notna(age_minutes)
            and age_minutes
            > max(
                interval_minutes * 3,
                180,
            )
        )

        return df

    # ========================================================
    # BINANCE
    # ========================================================

    def _fetch_binance(
        self,
        symbol: str,
        interval: str,
        limit: int,
    ) -> pd.DataFrame:

        url = (
            f"{BINANCE_DATA_URL}"
            "/api/v3/klines"
        )

        params = {
            "symbol":
                symbol.upper(),
            "interval":
                interval,
            "limit":
                min(
                    int(limit),
                    1000,
                ),
        }

        self._calls += 1
        self._provider_calls[
            "binance"
        ] += 1

        try:
            response = self.session.get(
                url,
                params=params,
                timeout=self.timeout,
            )

            response.raise_for_status()

            raw = response.json()

            if not isinstance(
                raw,
                list,
            ):
                raise ProviderError(
                    "Binance: réponse "
                    "inattendue."
                )

            self._provider_successes[
                "binance"
            ] += 1

            rows = []

            for item in raw:

                if len(item) < 6:
                    continue

                rows.append(
                    {
                        "open_time":
                            pd.to_datetime(
                                item[0],
                                unit="ms",
                                utc=True,
                                errors="coerce",
                            ),
                        "open":
                            item[1],
                        "high":
                            item[2],
                        "low":
                            item[3],
                        "close":
                            item[4],
                        "volume":
                            item[5],
                    }
                )

            return self._canonicalize(
                rows
            )

        except Exception:
            self._provider_failures[
                "binance"
            ] += 1
            raise

    # ========================================================
    # YAHOO
    # ========================================================

    def _fetch_yahoo(
        self,
        symbol: str,
        interval: str,
        limit: int,
    ) -> pd.DataFrame:

        yahoo_interval = (
            _interval_to_yahoo(
                interval
            )
        )

        raw_minutes = (
            interval_to_minutes(
                yahoo_interval
            )
        )

        requested_minutes = (
            interval_to_minutes(
                interval
            )
        )

        candles_needed = max(
            int(limit),
            120,
        )

        total_minutes = (
            candles_needed
            * raw_minutes
            * 2
        )

        end = datetime.now(
            timezone.utc
        )

        start = (
            end
            - timedelta(
                minutes=total_minutes
            )
        )

        url = (
            f"{YAHOO_BASE_URL}"
            f"/v8/finance/chart/{symbol}"
        )

        params = {
            "period1":
                int(start.timestamp()),
            "period2":
                int(end.timestamp()),
            "interval":
                yahoo_interval,
            "events":
                "history",
            "includeAdjustedClose":
                "true",
        }

        data = self._request_json(
            "yahoo",
            url,
            params,
        )

        chart = data.get(
            "chart",
            {}
        )

        result = chart.get(
            "result"
        )

        if not result:
            raise ProviderError(
                f"Yahoo: aucune donnée "
                f"pour {symbol}"
            )

        result = result[0]

        timestamps = result.get(
            "timestamp",
            []
        )

        quote_list = (
            result
            .get("indicators", {})
            .get("quote", [])
        )

        if (
            not timestamps
            or not quote_list
        ):
            raise ProviderError(
                f"Yahoo: OHLC absent "
                f"pour {symbol}"
            )

        quote = quote_list[0]

        opens = quote.get(
            "open",
            []
        )

        highs = quote.get(
            "high",
            []
        )

        lows = quote.get(
            "low",
            []
        )

        closes = quote.get(
            "close",
            []
        )

        volumes = quote.get(
            "volume",
            []
        )

        rows = []

        for i, timestamp in enumerate(
            timestamps
        ):

            rows.append(
                {
                    "open_time":
                        pd.to_datetime(
                            timestamp,
                            unit="s",
                            utc=True,
                            errors="coerce",
                        ),
                    "open":
                        opens[i]
                        if i < len(opens)
                        else np.nan,
                    "high":
                        highs[i]
                        if i < len(highs)
                        else np.nan,
                    "low":
                        lows[i]
                        if i < len(lows)
                        else np.nan,
                    "close":
                        closes[i]
                        if i < len(closes)
                        else np.nan,
                    "volume":
                        volumes[i]
                        if i < len(volumes)
                        else np.nan,
                }
            )

        df = self._canonicalize(
            rows
        )

        if requested_minutes in {
            45,
            120,
            240,
        }:
            df = self._aggregate_intraday(
                df,
                requested_minutes,
            )

        return (
            df.tail(limit)
            .reset_index(drop=True)
        )

    # ========================================================
    # FINNHUB
    # ========================================================

    def _fetch_finnhub(
        self,
        symbol: str,
        interval: str,
        limit: int,
        asset_type: str,
    ) -> pd.DataFrame:

        if not FINNHUB_API_KEY:
            raise ProviderError(
                "Finnhub: API key absente."
            )

        resolution = (
            _interval_to_finnhub(
                interval
            )
        )

        raw_minutes = (
            int(resolution)
            if resolution.isdigit()
            else 1440
        )

        requested_minutes = (
            interval_to_minutes(
                interval
            )
        )

        candles_needed = max(
            int(limit),
            120,
        )

        total_minutes = (
            candles_needed
            * raw_minutes
            * 2
        )

        end = datetime.now(
            timezone.utc
        )

        start = (
            end
            - timedelta(
                minutes=total_minutes
            )
        )

        normalized_type = (
            self._normalise_asset_type(
                asset_type
            )
        )

        if normalized_type == "forex":
            endpoint = "forex/candle"
        else:
            endpoint = "stock/candle"

        url = (
            f"{FINNHUB_BASE_URL}"
            f"/{endpoint}"
        )

        params = {
            "symbol":
                symbol,
            "resolution":
                resolution,
            "from":
                int(start.timestamp()),
            "to":
                int(end.timestamp()),
            "token":
                FINNHUB_API_KEY,
        }

        data = self._request_json(
            "finnhub",
            url,
            params,
        )

        status = str(
            data.get("s", "")
        ).lower()

        if status != "ok":
            raise ProviderError(
                f"Finnhub: statut "
                f"'{status}' pour {symbol}"
            )

        timestamps = data.get(
            "t",
            []
        )

        opens = data.get(
            "o",
            []
        )

        highs = data.get(
            "h",
            []
        )

        lows = data.get(
            "l",
            []
        )

        closes = data.get(
            "c",
            []
        )

        volumes = data.get(
            "v",
            []
        )

        if not timestamps:
            raise ProviderError(
                f"Finnhub: aucune donnée "
                f"pour {symbol}"
            )

        rows = []

        for i, timestamp in enumerate(
            timestamps
        ):

            rows.append(
                {
                    "open_time":
                        pd.to_datetime(
                            timestamp,
                            unit="s",
                            utc=True,
                            errors="coerce",
                        ),
                    "open":
                        opens[i]
                        if i < len(opens)
                        else np.nan,
                    "high":
                        highs[i]
                        if i < len(highs)
                        else np.nan,
                    "low":
                        lows[i]
                        if i < len(lows)
                        else np.nan,
                    "close":
                        closes[i]
                        if i < len(closes)
                        else np.nan,
                    "volume":
                        volumes[i]
                        if i < len(volumes)
                        else np.nan,
                }
            )

        df = self._canonicalize(
            rows
        )

        if requested_minutes in {
            45,
            120,
            240,
        }:
            df = self._aggregate_intraday(
                df,
                requested_minutes,
            )

        return (
            df.tail(limit)
            .reset_index(drop=True)
        )

    # ========================================================
    # TWELVE DATA
    # ========================================================

    def _fetch_twelve_data(
        self,
        symbol: str,
        interval: str,
        limit: int,
    ) -> pd.DataFrame:

        if not TWELVE_DATA_API_KEY:
            raise ProviderError(
                "Twelve Data: API key absente."
            )

        if not self._reserve_twelve_data_credit():
            raise ProviderError(
                "Twelve Data: quota "
                "journalier épuisé."
            )

        url = (
            f"{TWELVE_DATA_BASE_URL}"
            "/time_series"
        )

        params = {
            "symbol":
                symbol,
            "interval":
                _interval_to_twelve_data(
                    interval
                ),
            "outputsize":
                min(
                    int(limit),
                    5000,
                ),
            "timezone":
                "UTC",
            "apikey":
                TWELVE_DATA_API_KEY,
        }

        data = self._request_json(
            "twelve_data",
            url,
            params,
        )

        if str(
            data.get(
                "status",
                ""
            )
        ).lower() == "error":

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
                f"Twelve Data: aucune "
                f"donnée pour {symbol}"
            )

        rows = []

        for item in values:

            rows.append(
                {
                    "open_time":
                        item.get(
                            "datetime"
                        ),
                    "open":
                        item.get("open"),
                    "high":
                        item.get("high"),
                    "low":
                        item.get("low"),
                    "close":
                        item.get("close"),

                    # JAMAIS transformer
                    # l'absence de volume
                    # en 0.
                    "volume":
                        item.get(
                            "volume",
                            np.nan,
                        ),
                }
            )

        df = self._canonicalize(
            rows
        )

        return (
            df.tail(limit)
            .reset_index(drop=True)
        )

    # ========================================================
    # AGRÉGATION
    # ========================================================

    @staticmethod
    def _aggregate_intraday(
        df: pd.DataFrame,
        target_minutes: int,
    ) -> pd.DataFrame:

        if df.empty:
            return df

        df = df.copy()

        df["open_time"] = pd.to_datetime(
            df["open_time"],
            utc=True,
            errors="coerce",
        )

        df = df.dropna(
            subset=["open_time"]
        )

        df = df.set_index(
            "open_time"
        )

        rule = f"{target_minutes}min"

        grouped = df.resample(
            rule,
            origin="epoch",
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

        if "volume" in df.columns:

            volume_count = (
                grouped["volume"]
                .count()
            )

            row_count = (
                grouped["volume"]
                .size()
            )

            volume_sum = (
                grouped["volume"]
                .sum(
                    min_count=1
                )
            )

            complete_volume = (
                volume_count
                == row_count
            )

            result["volume"] = (
                volume_sum.where(
                    complete_volume,
                    np.nan,
                )
            )

        else:
            result["volume"] = np.nan

        result = result.dropna(
            subset=[
                "open",
                "high",
                "low",
                "close",
            ]
        )

        result = (
            result
            .reset_index()
            .sort_values(
                "open_time"
            )
            .drop_duplicates(
                "open_time",
                keep="last",
            )
            .reset_index(
                drop=True
            )
        )

        return result

    # ========================================================
    # FETCH FOURNISSEUR
    # ========================================================

    def _fetch_provider(
        self,
        provider: str,
        symbol: str,
        interval: str,
        limit: int,
        asset_type: str,
    ) -> pd.DataFrame:

        provider = provider.lower()

        if provider == "binance":
            return self._fetch_binance(
                symbol,
                interval,
                limit,
            )

        if provider == "yahoo":
            return self._fetch_yahoo(
                symbol,
                interval,
                limit,
            )

        if provider == "finnhub":
            return self._fetch_finnhub(
                symbol,
                interval,
                limit,
                asset_type,
            )

        if provider == "twelve_data":
            return self._fetch_twelve_data(
                symbol,
                interval,
                limit,
            )

        raise ProviderError(
            f"Fournisseur inconnu : "
            f"{provider}"
        )

    # ========================================================
    # SYMBOL MAP
    # ========================================================

    def _resolve_symbol(
        self,
        symbol: str,
        provider: str,
        symbol_map: Optional[
            Dict[str, Any]
        ],
    ) -> str:

        if not symbol_map:
            return symbol

        provider_aliases = {
            "twelve_data": [
                "twelve_data",
                "twelvedata",
                "td",
            ],
            "finnhub": [
                "finnhub",
            ],
            "yahoo": [
                "yahoo",
            ],
            "binance": [
                "binance",
            ],
        }

        # Mapping direct :
        #
        # {
        #     "yahoo": "^GSPC",
        #     "finnhub": "SPX"
        # }

        if isinstance(
            symbol_map,
            dict,
        ):

            value = symbol_map.get(
                provider
            )

            if isinstance(
                value,
                str,
            ):
                return value

            for alias in (
                provider_aliases.get(
                    provider,
                    []
                )
            ):
                value = symbol_map.get(
                    alias
                )

                if isinstance(
                    value,
                    str,
                ):
                    return value

            # Mapping imbriqué :
            #
            # {
            #     "^GSPC": {
            #         "yahoo": "^GSPC"
            #     }
            # }

            nested = symbol_map.get(
                symbol
            )

            if isinstance(
                nested,
                str,
            ):
                return nested

            if isinstance(
                nested,
                dict,
            ):
                for key in [
                    provider,
                    *provider_aliases.get(
                        provider,
                        []
                    ),
                ]:

                    value = nested.get(
                        key
                    )

                    if isinstance(
                        value,
                        str,
                    ):
                        return value

        return symbol

    # ========================================================
    # SCORE QUALITÉ
    # ========================================================

    @staticmethod
    def _quality_score(
        df: pd.DataFrame,
        provider: str,
        require_volume: bool,
        preferred: Optional[str],
    ) -> float:

        if (
            df is None
            or df.empty
        ):
            return -1e9

        score = 0.0

        volume_status = df.attrs.get(
            "volume_status",
            VOLUME_UNAVAILABLE,
        )

        coverage = float(
            df.attrs.get(
                "volume_coverage",
                0.0,
            )
        )

        # Volume.
        if (
            volume_status
            == VOLUME_CONFIRMED
        ):
            score += 50

        elif (
            volume_status
            == VOLUME_PARTIAL
        ):
            score += 15

        elif (
            volume_status
            == VOLUME_INVALID
        ):
            score -= 40

        if (
            require_volume
            and volume_status
            == VOLUME_UNAVAILABLE
        ):
            score -= 80

        score += coverage * 20

        # Fraîcheur.
        age = df.attrs.get(
            "age_minutes",
            float("nan"),
        )

        if pd.notna(age):

            if age <= 30:
                score += 30

            elif age <= 60:
                score += 20

            elif age <= 180:
                score += 10

            elif age <= 720:
                score -= 5

            else:
                score -= 20

        # Quantité de données.
        score += min(
            len(df) / 20,
            25,
        )

        # Préférence explicite.
        if (
            preferred
            and provider
            == str(
                preferred
            ).lower()
        ):
            score += 100

        # Twelve Data reste un fallback
        # lorsque les autres sources
        # gratuites sont disponibles.
        if provider == "twelve_data":
            score -= 5

        return score

    # ========================================================
    # FETCH INTELLIGENT
    # ========================================================

    def fetch(
        self,
        symbol: str,
        interval: str = "15m",
        limit: int = 1000,
        asset_type: str = "stock",
        preferred: Optional[str] = None,
        require_volume: bool = False,
        symbol_map: Optional[
            Dict[str, Any]
        ] = None,
    ) -> pd.DataFrame:

        if not symbol:
            raise DataSourceError(
                "Symbole vide."
            )

        interval = (
            str(interval)
            .lower()
            .strip()
        )

        asset_type = (
            self._normalise_asset_type(
                asset_type
            )
        )

        limit = max(
            10,
            min(
                int(limit),
                5000,
            ),
        )

        providers = (
            self._provider_candidates(
                asset_type,
                preferred,
            )
        )

        # Cache.
        cache_key = (
            symbol,
            interval,
            limit,
            asset_type,
            preferred,
            bool(require_volume),
            repr(symbol_map),
        )

        if cache_key in self._cache:

            self._cache_hits += 1

            return _copy_df(
                self._cache[
                    cache_key
                ]
            )

        errors = []

        candidates = []

        # ====================================================
        # ESSAIS
        # ====================================================

        for provider in providers:

            if (
                provider
                == "twelve_data"
                and
                self.twelve_data_remaining_count()
                <= 0
            ):

                errors.append(
                    "twelve_data: "
                    "quota journalier épuisé"
                )

                continue

            provider_symbol = (
                self._resolve_symbol(
                    symbol,
                    provider,
                    symbol_map,
                )
            )

            try:

                df = self._fetch_provider(
                    provider,
                    provider_symbol,
                    interval,
                    limit,
                    asset_type,
                )

                if (
                    df is None
                    or df.empty
                ):
                    raise ProviderError(
                        "aucune bougie reçue"
                    )

                # Retire la bougie encore
                # en formation.
                df = (
                    self
                    ._drop_incomplete_last_candle(
                        df,
                        interval,
                    )
                )

                if len(df) < 10:
                    raise ProviderError(
                        "données insuffisantes : "
                        f"{len(df)} bougies"
                    )

                df = (
                    df.tail(limit)
                    .reset_index(
                        drop=True
                    )
                )

                df = (
                    self._attach_metadata(
                        df,
                        provider,
                        provider_symbol,
                        interval,
                    )
                )

                quality = (
                    self._quality_score(
                        df,
                        provider,
                        require_volume,
                        preferred,
                    )
                )

                candidates.append(
                    (
                        quality,
                        provider,
                        df,
                    )
                )

                # Si la source est suffisamment
                # bonne, inutile de solliciter
                # les fournisseurs suivants.
                if (
                    df.attrs.get(
                        "volume_status"
                    )
                    == VOLUME_CONFIRMED
                    and
                    len(df)
                    >= min(
                        limit,
                        120,
                    )
                ):
                    break

            except Exception as exc:

                message = (
                    f"{provider} "
                    f"({provider_symbol}) : "
                    f"{exc}"
                )

                errors.append(
                    message
                )

                self._last_errors.setdefault(
                    symbol,
                    [],
                ).append(
                    message
                )

        # ====================================================
        # ÉCHEC TOTAL
        # ====================================================

        if not candidates:

            self._failures += 1

            raise DataSourceError(
                f"Aucune source disponible "
                f"pour {symbol} "
                f"[{asset_type}/{interval}]. "
                + " | ".join(errors)
            )

        # ====================================================
        # MEILLEURE SOURCE
        # ====================================================

        candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        _, provider, result = (
            candidates[0]
        )

        self._successes += 1

        status = result.attrs.get(
            "volume_status",
            VOLUME_UNAVAILABLE,
        )

        self._volume_status_counts[
            status
        ] = (
            self
            ._volume_status_counts
            .get(status, 0)
            + 1
        )

        result.attrs[
            "router_errors"
        ] = errors

        result.attrs[
            "router_candidates"
        ] = [
            {
                "provider": p,
                "quality": q,
                "volume_status":
                    d.attrs.get(
                        "volume_status"
                    ),
                "volume_coverage":
                    d.attrs.get(
                        "volume_coverage"
                    ),
                "age_minutes":
                    d.attrs.get(
                        "age_minutes"
                    ),
            }
            for q, p, d
            in candidates
        ]

        self._cache[
            cache_key
        ] = _copy_df(
            result
        )

        return _copy_df(
            result
        )


# ============================================================
# FONCTIONS GLOBALES DE COMPATIBILITÉ
# ============================================================

def get_twelve_data_budget():
    return {
        "daily_limit":
            TD_DAILY_LIMIT,
        "used":
            DataRouter
            .twelve_data_used_count(),
        "remaining":
            DataRouter
            .twelve_data_remaining_count(),
    }


def twelve_data_budget():
    return get_twelve_data_budget()


# ============================================================
# TEST LOCAL
# ============================================================

if __name__ == "__main__":

    router = DataRouter()

    print("=" * 60)
    print("DATA ROUTER V4.2")
    print("=" * 60)

    print(
        "Twelve Data :",
        router.get_twelve_data_budget(),
    )

    print(
        "Stats :",
        router.router_stats(),
    )
