"""
V4.2 — Data Router multi-sources.

Sources :
- Binance       : crypto 24/7
- Finnhub       : actions / forex / indices selon disponibilité
- Yahoo Finance : couverture large
- Twelve Data   : complément + quota gratuit journalier

Principes :
- routage par type d'actif
- mapping fournisseur explicite
- fallback automatique
- circuit breakers différenciés
- limitation des appels Twelve Data
- timestamps normalisés en datetime64[ns, UTC]
- rejet des vraies données futures
- gestion robuste des volumes
- conversion automatique des intervalles selon le fournisseur
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pandas as pd
import requests


# ============================================================
# CONSTANTES
# ============================================================

PROVIDER_NAMES = (
    "binance",
    "finnhub",
    "yahoo",
    "twelve_data",
)

VOLUME_CONFIRMED = "confirmed"
VOLUME_PARTIAL = "partial"
VOLUME_UNAVAILABLE = "unavailable"
VOLUME_INVALID = "invalid"

TD_DAILY_LIMIT = int(
    os.getenv("TWELVE_DATA_DAILY_LIMIT", "800")
)

TD_MIN_REQUEST_INTERVAL = float(
    os.getenv("TWELVE_DATA_MIN_INTERVAL", "1.20")
)

HTTP_TIMEOUT = int(
    os.getenv("DATA_HTTP_TIMEOUT", "20")
)

# Circuit breakers.
COOLDOWN_403 = int(
    os.getenv("PROVIDER_COOLDOWN_403", "900")
)

COOLDOWN_429 = int(
    os.getenv("PROVIDER_COOLDOWN_429", "300")
)

COOLDOWN_OTHER = int(
    os.getenv("PROVIDER_COOLDOWN_OTHER", "120")
)

FUTURE_TOLERANCE_MINUTES = float(
    os.getenv(
        "FUTURE_TIMESTAMP_TOLERANCE_MINUTES",
        "2",
    )
)


class DataSourceError(RuntimeError):
    """Erreur de récupération ou de validation d'une source."""


# ============================================================
# UTILITAIRES TEMPORELS
# ============================================================

def _now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def _normalize_datetime(values: Any) -> pd.Series:
    """
    Normalise toutes les dates en :

        datetime64[ns, UTC]

    Cette normalisation est importante avec Pandas 3.x car
    certaines sources peuvent produire des résolutions différentes
    (ms / us / ns), ce qui peut provoquer des erreurs dans
    merge_asof().
    """

    s = pd.to_datetime(
        values,
        utc=True,
        errors="coerce",
    )

    s = pd.Series(s)

    try:
        s = s.astype("datetime64[ns, UTC]")
    except (TypeError, ValueError):
        try:
            s = s.dt.as_unit("ns")
        except Exception:
            pass

    return pd.Series(
        s,
        index=getattr(s, "index", None),
    )


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    )


# ============================================================
# NORMALISATION DES INTERVALLES
# ============================================================

def _normalize_internal_interval(interval: str) -> str:
    """
    Convertit les différentes écritures acceptées par V4.2
    vers une représentation interne stable.

    Exemples :
        5min  -> 5min
        5m    -> 5min
        15m   -> 15min
        1h    -> 1h
        1d    -> 1d
    """

    value = str(interval).strip().lower()

    aliases = {
        "1m": "1min",
        "1min": "1min",
        "5m": "5min",
        "5min": "5min",
        "15m": "15min",
        "15min": "15min",
        "30m": "30min",
        "30min": "30min",
        "1h": "1h",
        "60m": "1h",
        "60min": "1h",
        "4h": "4h",
        "240m": "4h",
        "240min": "4h",
        "1d": "1d",
        "1day": "1d",
        "day": "1d",
    }

    normalized = aliases.get(value)

    if normalized is None:
        raise DataSourceError(
            f"Intervalle non supporté : {interval}"
        )

    return normalized


def _provider_interval(
    provider: str,
    interval: str,
) -> str:
    """
    Convertit l'intervalle interne V4.2 vers le format attendu
    par chaque fournisseur.

    Interne V4.2 :
        1min / 5min / 15min / 30min / 1h / 4h / 1d

    Binance :
        1m / 5m / 15m / 30m / 1h / 4h / 1d

    Yahoo :
        1m / 5m / 15m / 30m / 1h / 1d

    Finnhub :
        1 / 5 / 15 / 30 / 60 / D

    Twelve Data :
        1min / 5min / 15min / 30min / 1h / 4h / 1day
    """

    internal = _normalize_internal_interval(interval)

    if provider == "binance":
        mapping = {
            "1min": "1m",
            "5min": "5m",
            "15min": "15m",
            "30min": "30m",
            "1h": "1h",
            "4h": "4h",
            "1d": "1d",
        }

    elif provider == "yahoo":
        mapping = {
            "1min": "1m",
            "5min": "5m",
            "15min": "15m",
            "30min": "30m",
            "1h": "1h",
            "1d": "1d",
        }

        # Yahoo Finance ne fournit pas directement 4h dans
        # l'endpoint chart.
        if internal == "4h":
            raise DataSourceError(
                "yahoo: intervalle 4h non supporté"
            )

    elif provider == "finnhub":
        mapping = {
            "1min": "1",
            "5min": "5",
            "15min": "15",
            "30min": "30",
            "1h": "60",
            "1d": "D",
        }

        if internal == "4h":
            raise DataSourceError(
                "finnhub: intervalle 4h non supporté directement"
            )

    elif provider == "twelve_data":
        mapping = {
            "1min": "1min",
            "5min": "5min",
            "15min": "15min",
            "30min": "30min",
            "1h": "1h",
            "4h": "4h",
            "1d": "1day",
        }

    else:
        raise DataSourceError(
            f"Provider inconnu : {provider}"
        )

    result = mapping.get(internal)

    if result is None:
        raise DataSourceError(
            f"{provider}: intervalle {internal} non supporté"
        )

    return result


# ============================================================
# ROUTER
# ============================================================

class DataRouter:

    def __init__(self) -> None:

        self.session = requests.Session()

        self.stats: Dict[str, Dict[str, int]] = {
            provider: {
                "calls": 0,
                "success": 0,
                "failures": 0,
                "skips": 0,
                "circuit_breaker": 0,
            }
            for provider in PROVIDER_NAMES
        }

        self._provider_cooldown_until: Dict[str, float] = {
            provider: 0.0
            for provider in PROVIDER_NAMES
        }

        self._provider_cooldown_reason: Dict[str, str] = {
            provider: ""
            for provider in PROVIDER_NAMES
        }

        self._last_request_at: Dict[str, float] = {
            provider: 0.0
            for provider in PROVIDER_NAMES
        }

        self._twelve_data_used = 0

        self.cache: Dict[
            str,
            tuple[float, pd.DataFrame],
        ] = {}

        self.cache_ttl = 20.0

    # --------------------------------------------------------
    # Circuit breaker
    # --------------------------------------------------------

    def _provider_available(
        self,
        provider: str,
    ) -> bool:

        until = self._provider_cooldown_until.get(
            provider,
            0.0,
        )

        if time.time() < until:
            self.stats[provider][
                "circuit_breaker"
            ] += 1

            return False

        return True

    def _open_circuit(
        self,
        provider: str,
        seconds: int,
        reason: str,
    ) -> None:

        self._provider_cooldown_until[provider] = (
            time.time() + seconds
        )

        self._provider_cooldown_reason[provider] = (
            reason
        )

        print(
            f"[Router] {provider} temporairement désactivé "
            f"pendant {seconds}s : {reason}"
        )

    # --------------------------------------------------------
    # Rate limiting Twelve Data
    # --------------------------------------------------------

    def _rate_limit(
        self,
        provider: str,
    ) -> None:

        if provider != "twelve_data":
            return

        elapsed = (
            time.time()
            - self._last_request_at[provider]
        )

        if elapsed < TD_MIN_REQUEST_INTERVAL:
            time.sleep(
                TD_MIN_REQUEST_INTERVAL - elapsed
            )

        self._last_request_at[provider] = time.time()

    # --------------------------------------------------------
    # HTTP
    # --------------------------------------------------------

    def _request_json(
        self,
        provider: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:

        if not self._provider_available(provider):

            self.stats[provider]["skips"] += 1

            raise DataSourceError(
                f"{provider} circuit breaker actif"
            )

        self._rate_limit(provider)

        self.stats[provider]["calls"] += 1

        try:

            response = self.session.get(
                url,
                params=params,
                timeout=HTTP_TIMEOUT,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 "
                        "(compatible; "
                        "V4.2-MultiAsset-Scanner/1.0)"
                    )
                },
            )

        except requests.RequestException as exc:

            self.stats[provider]["failures"] += 1

            self._open_circuit(
                provider,
                COOLDOWN_OTHER,
                f"network error: {exc}",
            )

            raise DataSourceError(
                f"{provider} network error: {exc}"
            ) from exc

        status = response.status_code

        # ----------------------------------------------------
        # 403
        # ----------------------------------------------------

        if status == 403:

            self.stats[provider]["failures"] += 1

            self._open_circuit(
                provider,
                COOLDOWN_403,
                "HTTP 403",
            )

            raise DataSourceError(
                f"{provider} HTTP 403"
            )

        # ----------------------------------------------------
        # 429
        # ----------------------------------------------------

        if status == 429:

            self.stats[provider]["failures"] += 1

            self._open_circuit(
                provider,
                COOLDOWN_429,
                "HTTP 429 rate limit",
            )

            raise DataSourceError(
                f"{provider} HTTP 429"
            )

        # ----------------------------------------------------
        # Autres erreurs HTTP
        # ----------------------------------------------------

        if status >= 400:

            self.stats[provider]["failures"] += 1

            detail = ""

            try:
                text = response.text.strip()

                if text:
                    detail = (
                        f" | réponse={text[:180]}"
                    )

            except Exception:
                pass

            raise DataSourceError(
                f"{provider} HTTP {status}"
                f"{detail}"
            )

        # ----------------------------------------------------
        # JSON
        # ----------------------------------------------------

        try:

            data = response.json()

        except ValueError as exc:

            self.stats[provider]["failures"] += 1

            raise DataSourceError(
                f"{provider} réponse JSON invalide"
            ) from exc

        self.stats[provider]["success"] += 1

        return data

    # ========================================================
    # CANONICALISATION
    # ========================================================

    def _canonicalize(
        self,
        data: pd.DataFrame,
        provider: str,
    ) -> pd.DataFrame:

        if data is None or data.empty:

            raise DataSourceError(
                f"{provider}: aucune donnée"
            )

        df = data.copy()

        rename: Dict[Any, str] = {}

        for col in df.columns:

            c = str(col).lower().strip()

            if c in {
                "datetime",
                "date",
                "timestamp",
                "time",
                "open_time",
            }:
                rename[col] = "open_time"

            elif c in {
                "open",
                "o",
            }:
                rename[col] = "open"

            elif c in {
                "high",
                "h",
            }:
                rename[col] = "high"

            elif c in {
                "low",
                "l",
            }:
                rename[col] = "low"

            elif c in {
                "close",
                "c",
                "price",
            }:
                rename[col] = "close"

            elif c in {
                "volume",
                "v",
            }:
                rename[col] = "volume"

        df = df.rename(
            columns=rename
        )

        required = [
            "open_time",
            "open",
            "high",
            "low",
            "close",
        ]

        missing = [
            column
            for column in required
            if column not in df.columns
        ]

        if missing:

            raise DataSourceError(
                f"{provider}: "
                f"colonnes manquantes {missing}"
            )

        if "volume" not in df.columns:
            df["volume"] = pd.NA

        df["open_time"] = _normalize_datetime(
            df["open_time"]
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

        df = df.sort_values(
            "open_time"
        )

        df = df.drop_duplicates(
            subset=["open_time"],
            keep="last",
        )

        df = df.reset_index(
            drop=True
        )

        if df.empty:

            raise DataSourceError(
                f"{provider}: dataframe vide "
                f"après nettoyage"
            )

        return df

    # ========================================================
    # VOLUME
    # ========================================================

    def _volume_status(
        self,
        series: pd.Series,
    ) -> str:

        if series is None:
            return VOLUME_UNAVAILABLE

        s = pd.to_numeric(
            series,
            errors="coerce",
        )

        if s.notna().sum() == 0:
            return VOLUME_UNAVAILABLE

        if (s.dropna() < 0).any():
            return VOLUME_INVALID

        coverage = float(
            s.notna().mean()
        )

        if coverage >= 0.80:
            return VOLUME_CONFIRMED

        if coverage > 0:
            return VOLUME_PARTIAL

        return VOLUME_UNAVAILABLE

    # ========================================================
    # FUTURE CANDLES
    # ========================================================

    def _validate_timestamps(
        self,
        df: pd.DataFrame,
        provider: str,
    ) -> pd.DataFrame:

        if df.empty:
            return df

        df = df.copy()

        df["open_time"] = _normalize_datetime(
            df["open_time"]
        )

        now = _now_utc()

        future_limit = (
            now
            + pd.Timedelta(
                minutes=FUTURE_TOLERANCE_MINUTES
            )
        )

        future_mask = (
            df["open_time"] > future_limit
        )

        future_count = int(
            future_mask.sum()
        )

        if future_count:

            print(
                f"[{provider}] "
                f"{future_count} bougie(s) futures "
                f"détectées et supprimées"
            )

            df = df.loc[
                ~future_mask
            ].copy()

        if df.empty:

            raise DataSourceError(
                f"{provider}: toutes les bougies "
                f"sont futures"
            )

        return df.reset_index(
            drop=True
        )

    # ========================================================
    # BOUGIE INCOMPLÈTE
    # ========================================================

    def _drop_incomplete_last_candle(
        self,
        df: pd.DataFrame,
        interval: str,
    ) -> pd.DataFrame:

        if df.empty:
            return df

        internal = _normalize_internal_interval(
            interval
        )

        minutes_map = {
            "1min": 1,
            "5min": 5,
            "15min": 15,
            "30min": 30,
            "1h": 60,
            "4h": 240,
            "1d": 1440,
        }

        minutes = minutes_map.get(
            internal,
            5,
        )

        now = _now_utc()

        last = df.iloc[-1]["open_time"]

        if pd.notna(last):

            candle_end = (
                last
                + pd.Timedelta(
                    minutes=minutes
                )
            )

            if now < candle_end:

                return df.iloc[:-1].copy()

        return df

    # ========================================================
    # METADATA
    # ========================================================

    def _attach_metadata(
        self,
        df: pd.DataFrame,
        provider: str,
        symbol: str,
        interval: str,
    ) -> pd.DataFrame:

        df = df.copy()

        df["open_time"] = _normalize_datetime(
            df["open_time"]
        )

        df.attrs["provider"] = provider
        df.attrs["symbol"] = symbol
        df.attrs["interval"] = (
            _normalize_internal_interval(interval)
        )

        volume_status = self._volume_status(
            df["volume"]
        )

        df.attrs["volume_status"] = (
            volume_status
        )

        if not df.empty:

            age = (
                _now_utc()
                - df["open_time"].iloc[-1]
            ).total_seconds() / 60.0

            df.attrs["age_minutes"] = float(
                age
            )

        else:

            df.attrs["age_minutes"] = None

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

        base = os.getenv(
            "BINANCE_DATA_URL",
            "https://data-api.binance.vision",
        ).rstrip("/")

        url = (
            f"{base}/api/v3/klines"
        )

        binance_interval = _provider_interval(
            "binance",
            interval,
        )

        data = self._request_json(
            "binance",
            url,
            {
                "symbol": symbol.upper(),
                "interval": binance_interval,
                "limit": min(
                    max(int(limit), 1),
                    1000,
                ),
            },
        )

        if not isinstance(data, list):

            raise DataSourceError(
                "binance: format inattendu"
            )

        rows = []

        for row in data:

            if not isinstance(row, (list, tuple)):
                continue

            if len(row) < 6:
                continue

            rows.append(
                {
                    "open_time": pd.to_datetime(
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

        if not rows:

            raise DataSourceError(
                "binance: aucune bougie exploitable"
            )

        df = pd.DataFrame(rows)

        return self._canonicalize(
            df,
            "binance",
        )

    # ========================================================
    # YAHOO
    # ========================================================

    def _fetch_yahoo(
        self,
        symbol: str,
        interval: str,
        limit: int,
    ) -> pd.DataFrame:

        yahoo_interval = _provider_interval(
            "yahoo",
            interval,
        )

        period_map = {
            "1min": "7d",
            "5min": "60d",
            "15min": "60d",
            "30min": "60d",
            "1h": "730d",
            "1d": "5y",
        }

        internal = _normalize_internal_interval(
            interval
        )

        period = period_map.get(
            internal,
            "60d",
        )

        url = (
            "https://query1.finance.yahoo.com/"
            f"v8/finance/chart/{symbol}"
        )

        params = {
            "interval": yahoo_interval,
            "range": period,
            "includePrePost": "false",
            "events": "div,splits",
        }

        payload = self._request_json(
            "yahoo",
            url,
            params,
        )

        chart = payload.get(
            "chart",
            {},
        )

        error = chart.get(
            "error"
        )

        if error:

            raise DataSourceError(
                "yahoo: "
                + str(error)
            )

        result = chart.get(
            "result"
        )

        if not result:

            raise DataSourceError(
                "yahoo: résultat vide"
            )

        result = result[0]

        timestamps = result.get(
            "timestamp",
            [],
        )

        indicators = result.get(
            "indicators",
            {},
        )

        quotes = indicators.get(
            "quote",
            [],
        )

        if not quotes:

            raise DataSourceError(
                "yahoo: données OHLC absentes"
            )

        quote = quotes[0]

        if not timestamps:

            raise DataSourceError(
                "yahoo: timestamps absents"
            )

        def _safe_list(
            value: Any,
            size: int,
        ) -> list[Any]:

            if isinstance(value, list):
                return value

            return [pd.NA] * size

        size = len(timestamps)

        df = pd.DataFrame(
            {
                "open_time": pd.to_datetime(
                    timestamps,
                    unit="s",
                    utc=True,
                    errors="coerce",
                ),
                "open": _safe_list(
                    quote.get("open"),
                    size,
                ),
                "high": _safe_list(
                    quote.get("high"),
                    size,
                ),
                "low": _safe_list(
                    quote.get("low"),
                    size,
                ),
                "close": _safe_list(
                    quote.get("close"),
                    size,
                ),
                "volume": _safe_list(
                    quote.get("volume"),
                    size,
                ),
            }
        )

        return self._canonicalize(
            df,
            "yahoo",
        )

    # ========================================================
    # FINNHUB
    # ========================================================

    def _fetch_finnhub(
        self,
        symbol: str,
        interval: str,
        limit: int,
    ) -> pd.DataFrame:

        key = os.getenv(
            "FINNHUB_API_KEY",
            "",
        ).strip()

        if not key:

            raise DataSourceError(
                "FINNHUB_API_KEY absente"
            )

        resolution = _provider_interval(
            "finnhub",
            interval,
        )

        now = int(
            datetime.now(
                timezone.utc
            ).timestamp()
        )

        internal = _normalize_internal_interval(
            interval
        )

        if internal == "1d":

            seconds = (
                86400
                * max(
                    int(limit) * 2,
                    365,
                )
            )

        elif internal == "1h":

            seconds = (
                3600
                * max(
                    int(limit) * 2,
                    30,
                )
            )

        else:

            # Pour les intraday, on garde une fenêtre
            # suffisamment large pour obtenir au moins
            # 120 bougies lorsque le marché est ouvert.
            seconds = (
                900
                * max(
                    int(limit) * 2,
                    10,
                )
            )

        start = now - seconds

        url = (
            "https://finnhub.io/api/v1/stock/candle"
        )

        params = {
            "symbol": symbol,
            "resolution": resolution,
            "from": start,
            "to": now,
            "token": key,
        }

        payload = self._request_json(
            "finnhub",
            url,
            params,
        )

        status = payload.get("s")

        if status != "ok":

            message = payload.get(
                "error"
            )

            if message:
                raise DataSourceError(
                    f"finnhub: {message}"
                )

            raise DataSourceError(
                f"finnhub: statut {status}"
            )

        timestamps = payload.get(
            "t",
            [],
        )

        if not timestamps:

            raise DataSourceError(
                "finnhub: aucune bougie"
            )

        size = len(timestamps)

        def _safe_list(
            value: Any,
        ) -> list[Any]:

            if isinstance(value, list):
                return value

            return [pd.NA] * size

        df = pd.DataFrame(
            {
                "open_time": pd.to_datetime(
                    timestamps,
                    unit="s",
                    utc=True,
                    errors="coerce",
                ),
                "open": _safe_list(
                    payload.get("o")
                ),
                "high": _safe_list(
                    payload.get("h")
                ),
                "low": _safe_list(
                    payload.get("l")
                ),
                "close": _safe_list(
                    payload.get("c")
                ),
                "volume": _safe_list(
                    payload.get("v")
                ),
            }
        )

        return self._canonicalize(
            df,
            "finnhub",
        )

    # ========================================================
    # TWELVE DATA
    # ========================================================

    def _fetch_twelvedata(
        self,
        symbol: str,
        interval: str,
        limit: int,
    ) -> pd.DataFrame:

        key = os.getenv(
            "TWELVE_DATA_API_KEY",
            "",
        ).strip()

        if not key:

            raise DataSourceError(
                "TWELVE_DATA_API_KEY absente"
            )

        if (
            self._twelve_data_used
            >= TD_DAILY_LIMIT
        ):

            raise DataSourceError(
                "Twelve Data quota journalier atteint"
            )

        td_interval = _provider_interval(
            "twelve_data",
            interval,
        )

        url = (
            "https://api.twelvedata.com/"
            "time_series"
        )

        params = {
            "symbol": symbol,
            "interval": td_interval,
            "outputsize": min(
                max(int(limit), 1),
                5000,
            ),
            "apikey": key,
            "timezone": "UTC",
            "format": "JSON",
        }

        # On comptabilise l'appel avant l'envoi.
        # Cela évite de dépasser artificiellement le quota
        # en cas de réponse HTTP 429 ou autre erreur.
        self._twelve_data_used += 1

        payload = self._request_json(
            "twelve_data",
            url,
            params,
        )

        if payload.get(
            "status"
        ) == "error":

            message = payload.get(
                "message",
                "API error",
            )

            raise DataSourceError(
                "twelve_data: "
                + str(message)
            )

        values = payload.get(
            "values"
        )

        if not values:

            raise DataSourceError(
                "twelve_data: aucune donnée"
            )

        rows = []

        for row in values:

            if not isinstance(row, dict):
                continue

            if "datetime" not in row:
                continue

            dt = row.get(
                "datetime"
            )

            parsed = pd.to_datetime(
                dt,
                utc=True,
                errors="coerce",
            )

            if pd.isna(parsed):
                continue

            rows.append(
                {
                    "open_time": parsed,
                    "open": row.get("open"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row.get("close"),
                    "volume": row.get("volume"),
                }
            )

        if not rows:

            raise DataSourceError(
                "twelve_data: parsing vide"
            )

        df = pd.DataFrame(rows)

        return self._canonicalize(
            df,
            "twelve_data",
        )

    # ========================================================
    # RESOLUTION DU SYMBOLE
    # ========================================================

    def _resolve_symbol(
        self,
        canonical: str,
        provider: str,
        symbol_map: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        """
        Résout le symbole selon la map fournie.

        Important :
        si une map explicite existe pour l'actif et que le
        fournisseur n'y figure pas, on NE transmet PAS le symbole
        canonique au hasard.

        Cela évite par exemple d'envoyer :
            EUR/USD
        directement à Yahoo ou Binance.
        """

        if not symbol_map:
            return canonical

        aliases = {
            "twelve_data": (
                "twelve_data",
                "twelvedata",
            ),
            "finnhub": (
                "finnhub",
            ),
            "yahoo": (
                "yahoo",
            ),
            "binance": (
                "binance",
            ),
        }

        keys = aliases.get(
            provider,
            (provider,),
        )

        for key in keys:

            if key not in symbol_map:
                continue

            value = symbol_map[key]

            if value is None:
                return None

            value = str(value).strip()

            if not value:
                return None

            return value

        return None

    # ========================================================
    # PROVIDERS PAR TYPE D'ACTIF
    # ========================================================

    def _providers_for(
        self,
        asset_type: str,
    ) -> list[str]:

        asset_type = str(
            asset_type
        ).lower()

        if asset_type == "crypto":

            return [
                "binance",
                "yahoo",
                "twelve_data",
            ]

        if asset_type == "stock":

            return [
                "finnhub",
                "yahoo",
                "twelve_data",
            ]

        if asset_type == "forex":

            return [
                "finnhub",
                "twelve_data",
                "yahoo",
            ]

        if asset_type == "index":

            return [
                "yahoo",
                "twelve_data",
                "finnhub",
            ]

        if asset_type == "commodity":

            return [
                "yahoo",
                "twelve_data",
                "finnhub",
            ]

        return [
            "yahoo",
            "twelve_data",
            "finnhub",
        ]

    # ========================================================
    # QUALITY SCORE
    # ========================================================

    def _quality_score(
        self,
        df: pd.DataFrame,
        provider: str,
        require_volume: bool,
    ) -> float:

        if df.empty:
            return 0.0

        score = 100.0

        volume_status = self._volume_status(
            df["volume"]
        )

        if require_volume:

            if volume_status == VOLUME_CONFIRMED:
                score += 10

            elif volume_status == VOLUME_PARTIAL:
                score -= 15

            else:
                score -= 30

        if len(df) < 120:
            score -= 30

        age = df.attrs.get(
            "age_minutes"
        )

        if age is not None:

            if age < 0:
                score -= 100

            elif age > 180:
                score -= 20

            elif age > 90:
                score -= 10

        return max(
            0.0,
            min(
                110.0,
                score,
            ),
        )

    # ========================================================
    # FETCH PRINCIPAL
    # ========================================================

    def fetch(
        self,
        canonical: str,
        interval: str = "15min",
        limit: int = 300,
        asset_type: str = "stock",
        symbol_map: Optional[Dict[str, Any]] = None,
        require_volume: bool = False,
    ) -> pd.DataFrame:

        internal_interval = (
            _normalize_internal_interval(
                interval
            )
        )

        providers = self._providers_for(
            asset_type
        )

        errors: list[str] = []

        candidates = []

        for provider in providers:

            symbol = self._resolve_symbol(
                canonical,
                provider,
                symbol_map,
            )

            if symbol is None:

                self.stats[provider]["skips"] += 1
                continue

            if not self._provider_available(
                provider
            ):

                self.stats[provider]["skips"] += 1
                continue

            candidates.append(
                (
                    provider,
                    symbol,
                )
            )

        if not candidates:

            raise DataSourceError(
                f"{canonical}: "
                f"aucun provider disponible"
            )

        # ----------------------------------------------------
        # Tentative fournisseur par fournisseur
        # ----------------------------------------------------

        for provider, symbol in candidates:

            cache_key = (
                f"{provider}|"
                f"{symbol}|"
                f"{internal_interval}|"
                f"{limit}"
            )

            cached = self.cache.get(
                cache_key
            )

            if cached:

                timestamp, cached_df = cached

                if (
                    time.time() - timestamp
                    < self.cache_ttl
                ):

                    result = cached_df.copy()

                    result.attrs = (
                        cached_df.attrs.copy()
                    )

                    return result

            try:

                # --------------------------------------------
                # FETCH
                # --------------------------------------------

                if provider == "binance":

                    df = self._fetch_binance(
                        symbol,
                        internal_interval,
                        limit,
                    )

                elif provider == "yahoo":

                    df = self._fetch_yahoo(
                        symbol,
                        internal_interval,
                        limit,
                    )

                elif provider == "finnhub":

                    df = self._fetch_finnhub(
                        symbol,
                        internal_interval,
                        limit,
                    )

                elif provider == "twelve_data":

                    df = self._fetch_twelvedata(
                        symbol,
                        internal_interval,
                        limit,
                    )

                else:

                    raise DataSourceError(
                        f"Provider inconnu: {provider}"
                    )

                # --------------------------------------------
                # VALIDATION TEMPORELLE
                # --------------------------------------------

                df = self._validate_timestamps(
                    df,
                    provider,
                )

                # --------------------------------------------
                # SUPPRESSION DERNIÈRE BOUGIE
                # --------------------------------------------

                df = self._drop_incomplete_last_candle(
                    df,
                    internal_interval,
                )

                if len(df) < 1:

                    raise DataSourceError(
                        f"{provider}: "
                        f"aucune bougie exploitable"
                    )

                # --------------------------------------------
                # MÉTADONNÉES
                # --------------------------------------------

                df = self._attach_metadata(
                    df,
                    provider,
                    symbol,
                    internal_interval,
                )

                # --------------------------------------------
                # QUALITY SCORE
                # --------------------------------------------

                quality = self._quality_score(
                    df,
                    provider,
                    require_volume,
                )

                df.attrs["quality_score"] = (
                    quality
                )

                # --------------------------------------------
                # CACHE
                # --------------------------------------------

                self.cache[cache_key] = (
                    time.time(),
                    df.copy(),
                )

                return df

            except Exception as exc:

                errors.append(
                    f"{provider} "
                    f"({symbol}) : {exc}"
                )

        raise DataSourceError(
            f"{canonical}: "
            + " | ".join(errors)
        )

    # ========================================================
    # STATISTIQUES
    # ========================================================

    def get_stats(
        self,
    ) -> Dict[str, Dict[str, int]]:

        return {
            provider: values.copy()
            for provider, values
            in self.stats.items()
        }

    def get_twelve_data_usage(
        self,
    ) -> Dict[str, int]:

        return {
            "used": self._twelve_data_used,
            "limit": TD_DAILY_LIMIT,
            "remaining": max(
                0,
                TD_DAILY_LIMIT
                - self._twelve_data_used,
            ),
        }

    def get_circuit_breakers(
        self,
    ) -> Dict[str, Dict[str, Any]]:

        now = time.time()

        result: Dict[
            str,
            Dict[str, Any],
        ] = {}

        for provider in PROVIDER_NAMES:

            remaining = max(
                0,
                int(
                    self._provider_cooldown_until[
                        provider
                    ]
                    - now
                ),
            )

            result[provider] = {
                "remaining_seconds": remaining,
                "reason": (
                    self._provider_cooldown_reason[
                        provider
                    ]
                    if remaining > 0
                    else ""
                ),
            }

        return result

    def router_summary(
        self,
    ) -> Dict[str, Any]:

        stats = self.get_stats()

        calls = sum(
            item["calls"]
            for item in stats.values()
        )

        success = sum(
            item["success"]
            for item in stats.values()
        )

        failures = sum(
            item["failures"]
            for item in stats.values()
        )

        skips = sum(
            item["skips"]
            for item in stats.values()
        )

        return {
            "requests": calls,
            "successes": success,
            "failures": failures,
            "skips": skips,
            "stats": stats,
            "twelve_data": (
                self.get_twelve_data_usage()
            ),
            "circuit_breakers": (
                self.get_circuit_breakers()
            ),
        }


# ============================================================
# WRAPPER LEGACY
# ============================================================

_default_router = DataRouter()


def fetch_data(
    canonical: str,
    interval: str = "15min",
    limit: int = 300,
    asset_type: str = "stock",
    symbol_map: Optional[Dict[str, Any]] = None,
    require_volume: bool = False,
) -> pd.DataFrame:

    return _default_router.fetch(
        canonical=canonical,
        interval=interval,
        limit=limit,
        asset_type=asset_type,
        symbol_map=symbol_map,
        require_volume=require_volume,
    )


__all__ = [
    "DataRouter",
    "DataSourceError",
    "fetch_data",
    "VOLUME_CONFIRMED",
    "VOLUME_PARTIAL",
    "VOLUME_UNAVAILABLE",
    "VOLUME_INVALID",
]
