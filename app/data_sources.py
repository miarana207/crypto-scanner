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
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

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

TD_DAILY_LIMIT = int(os.getenv("TWELVE_DATA_DAILY_LIMIT", "800"))
TD_MIN_REQUEST_INTERVAL = float(
    os.getenv("TWELVE_DATA_MIN_INTERVAL", "1.20")
)

HTTP_TIMEOUT = int(os.getenv("DATA_HTTP_TIMEOUT", "20"))

# Cooldowns différenciés.
COOLDOWN_403 = int(os.getenv("PROVIDER_COOLDOWN_403", "900"))
COOLDOWN_429 = int(os.getenv("PROVIDER_COOLDOWN_429", "300"))
COOLDOWN_OTHER = int(os.getenv("PROVIDER_COOLDOWN_OTHER", "120"))

FUTURE_TOLERANCE_MINUTES = float(
    os.getenv("FUTURE_TIMESTAMP_TOLERANCE_MINUTES", "2")
)


class DataSourceError(RuntimeError):
    """Erreur de récupération ou de validation d'une source."""


# ============================================================
# UTILITAIRES
# ============================================================

def _now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def _normalize_datetime(values: Any) -> pd.Series:
    """
    Normalise absolument toutes les dates en :
        datetime64[ns, UTC]
    """
    s = pd.to_datetime(values, utc=True, errors="coerce")

    # Pandas 3 peut conserver une résolution ms/us selon la source.
    # On force ns pour éviter les incompatibilités merge_asof().
    try:
        s = s.astype("datetime64[ns, UTC]")
    except (TypeError, ValueError):
        s = pd.Series(s).dt.as_unit("ns")

    return pd.Series(s)


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
# ROUTER
# ============================================================

class DataRouter:
    def __init__(self) -> None:
        self.session = requests.Session()

        self.stats: Dict[str, Dict[str, int]] = {
            p: {
                "calls": 0,
                "success": 0,
                "failures": 0,
                "skips": 0,
                "circuit_breaker": 0,
            }
            for p in PROVIDER_NAMES
        }

        self._provider_cooldown_until: Dict[str, float] = {
            p: 0.0 for p in PROVIDER_NAMES
        }

        self._provider_cooldown_reason: Dict[str, str] = {
            p: "" for p in PROVIDER_NAMES
        }

        self._last_request_at: Dict[str, float] = {
            p: 0.0 for p in PROVIDER_NAMES
        }

        self._twelve_data_used = 0

        self.cache: Dict[str, tuple[float, pd.DataFrame]] = {}
        self.cache_ttl = 20.0

    # --------------------------------------------------------
    # Circuit breaker
    # --------------------------------------------------------

    def _provider_available(self, provider: str) -> bool:
        until = self._provider_cooldown_until.get(provider, 0.0)

        if time.time() < until:
            self.stats[provider]["circuit_breaker"] += 1
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
        self._provider_cooldown_reason[provider] = reason

        print(
            f"[Router] {provider} temporairement désactivé "
            f"pendant {seconds}s : {reason}"
        )

    # --------------------------------------------------------
    # Rate limiting
    # --------------------------------------------------------

    def _rate_limit(self, provider: str) -> None:
        if provider != "twelve_data":
            return

        elapsed = time.time() - self._last_request_at[provider]

        if elapsed < TD_MIN_REQUEST_INTERVAL:
            time.sleep(TD_MIN_REQUEST_INTERVAL - elapsed)

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
                    "User-Agent": "V4.2-MultiAsset-Scanner/1.0"
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

        if status >= 400:
            self.stats[provider]["failures"] += 1

            raise DataSourceError(
                f"{provider} HTTP {status}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            self.stats[provider]["failures"] += 1
            raise DataSourceError(
                f"{provider} réponse JSON invalide"
            ) from exc

        self.stats[provider]["success"] += 1

        return data

    # --------------------------------------------------------
    # Canonicalisation
    # --------------------------------------------------------

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

        rename = {}

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

            elif c in {"open", "o"}:
                rename[col] = "open"

            elif c in {"high", "h"}:
                rename[col] = "high"

            elif c in {"low", "l"}:
                rename[col] = "low"

            elif c in {"close", "c", "price"}:
                rename[col] = "close"

            elif c in {"volume", "v"}:
                rename[col] = "volume"

        df = df.rename(columns=rename)

        required = [
            "open_time",
            "open",
            "high",
            "low",
            "close",
        ]

        missing = [
            c for c in required
            if c not in df.columns
        ]

        if missing:
            raise DataSourceError(
                f"{provider}: colonnes manquantes {missing}"
            )

        if "volume" not in df.columns:
            df["volume"] = pd.NA

        df["open_time"] = _normalize_datetime(
            df["open_time"]
        )

        for col in [
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]:
            df[col] = pd.to_numeric(
                df[col],
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

        df = df.reset_index(drop=True)

        if df.empty:
            raise DataSourceError(
                f"{provider}: dataframe vide après nettoyage"
            )

        return df

    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Future candles
    # --------------------------------------------------------

    def _validate_timestamps(
        self,
        df: pd.DataFrame,
        provider: str,
    ) -> pd.DataFrame:

        if df.empty:
            return df

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
                f"{future_count} bougie(s) futures détectées"
            )

            df = df.loc[
                ~future_mask
            ].copy()

        if df.empty:
            raise DataSourceError(
                f"{provider}: toutes les bougies sont futures"
            )

        return df.reset_index(drop=True)

    # --------------------------------------------------------
    # Incomplete candle
    # --------------------------------------------------------

    def _drop_incomplete_last_candle(
        self,
        df: pd.DataFrame,
        interval: str,
    ) -> pd.DataFrame:

        if df.empty:
            return df

        minutes = {
            "1m": 1,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "4h": 240,
            "1d": 1440,
        }.get(interval, 5)

        now = _now_utc()
        last = df.iloc[-1]["open_time"]

        if (
            pd.notna(last)
            and now
            < last + pd.Timedelta(
                minutes=minutes
            )
        ):
            return df.iloc[:-1].copy()

        return df

    # --------------------------------------------------------
    # Metadata
    # --------------------------------------------------------

    def _attach_metadata(
        self,
        df: pd.DataFrame,
        provider: str,
        symbol: str,
        interval: str,
    ) -> pd.DataFrame:

        df = df.copy()

        df.attrs["provider"] = provider
        df.attrs["symbol"] = symbol
        df.attrs["interval"] = interval
        df.attrs["volume_status"] = (
            self._volume_status(df["volume"])
        )

        if not df.empty:
            age = (
                _now_utc()
                - df["open_time"].iloc[-1]
            ).total_seconds() / 60.0

            df.attrs["age_minutes"] = age

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
        )

        url = f"{base}/api/v3/klines"

        data = self._request_json(
            "binance",
            url,
            {
                "symbol": symbol.upper(),
                "interval": interval,
                "limit": min(limit, 1000),
            },
        )

        if not isinstance(data, list):
            raise DataSourceError(
                "binance: format inattendu"
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

        period_map = {
            "1m": "7d",
            "5m": "60d",
            "15m": "60d",
            "30m": "60d",
            "1h": "730d",
            "4h": "730d",
            "1d": "5y",
        }

        yahoo_interval = interval

        period = period_map.get(
            interval,
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

        result = (
            payload
            .get("chart", {})
            .get("result")
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

        quote = (
            result
            .get("indicators", {})
            .get("quote", [{}])[0]
        )

        if not timestamps:
            raise DataSourceError(
                "yahoo: timestamps absents"
            )

        df = pd.DataFrame(
            {
                "open_time": pd.to_datetime(
                    timestamps,
                    unit="s",
                    utc=True,
                ),
                "open": quote.get("open", []),
                "high": quote.get("high", []),
                "low": quote.get("low", []),
                "close": quote.get("close", []),
                "volume": quote.get(
                    "volume",
                    [pd.NA] * len(timestamps),
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
            "FINNHUB_API_KEY"
        )

        if not key:
            raise DataSourceError(
                "FINNHUB_API_KEY absente"
            )

        resolution_map = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "4h": "60",
            "1d": "D",
        }

        resolution = resolution_map.get(
            interval,
            "15",
        )

        now = int(
            datetime.now(
                timezone.utc
            ).timestamp()
        )

        # Fenêtre large afin de récupérer assez de bougies.
        if interval == "1d":
            seconds = 86400 * max(limit * 2, 365)
        elif interval == "1h":
            seconds = 3600 * max(limit * 2, 30)
        else:
            seconds = 900 * max(limit * 2, 10)

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

        if payload.get("s") != "ok":
            raise DataSourceError(
                f"finnhub: statut {payload.get('s')}"
            )

        timestamps = payload.get("t", [])

        if not timestamps:
            raise DataSourceError(
                "finnhub: aucune bougie"
            )

        df = pd.DataFrame(
            {
                "open_time": pd.to_datetime(
                    timestamps,
                    unit="s",
                    utc=True,
                ),
                "open": payload.get("o", []),
                "high": payload.get("h", []),
                "low": payload.get("l", []),
                "close": payload.get("c", []),
                "volume": payload.get(
                    "v",
                    [pd.NA] * len(timestamps),
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
            "TWELVE_DATA_API_KEY"
        )

        if not key:
            raise DataSourceError(
                "TWELVE_DATA_API_KEY absente"
            )

        if self._twelve_data_used >= TD_DAILY_LIMIT:
            raise DataSourceError(
                "Twelve Data quota journalier atteint"
            )

        td_interval_map = {
            "1m": "1min",
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "1h": "1h",
            "4h": "4h",
            "1d": "1day",
        }

        td_interval = td_interval_map.get(
            interval,
            "15min",
        )

        url = (
            "https://api.twelvedata.com/time_series"
        )

        params = {
            "symbol": symbol,
            "interval": td_interval,
            "outputsize": min(limit, 5000),
            "apikey": key,
            "timezone": "UTC",
            "format": "JSON",
        }

        self._twelve_data_used += 1

        payload = self._request_json(
            "twelve_data",
            url,
            params,
        )

        if payload.get("status") == "error":
            raise DataSourceError(
                "twelve_data: "
                + str(payload.get("message", "API error"))
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
            if "datetime" not in row:
                continue

            dt = row["datetime"]

            # Twelve Data renvoie généralement :
            # YYYY-MM-DD HH:MM:SS
            #
            # Avec timezone=UTC, une date sans timezone
            # doit être considérée comme UTC, PAS comme heure
            # locale du runner.
            parsed = pd.to_datetime(
                dt,
                utc=True,
                errors="coerce",
            )

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

        df = pd.DataFrame(rows)

        if df.empty:
            raise DataSourceError(
                "twelve_data: parsing vide"
            )

        return self._canonicalize(
            df,
            "twelve_data",
        )

    # ========================================================
    # RESOLUTION SYMBOL
    # ========================================================

    def _resolve_symbol(
        self,
        canonical: str,
        provider: str,
        symbol_map: Optional[Dict[str, Any]],
    ) -> Optional[str]:

        if not symbol_map:
            return canonical

        # Alias acceptés.
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

        found = False

        for key in keys:
            if key in symbol_map:
                found = True
                value = symbol_map[key]

                if value is None:
                    return None

                value = str(value).strip()

                if not value:
                    return None

                return value

        # Si une map existe mais ne contient pas le provider,
        # on NE doit PAS envoyer aveuglément le symbole canonique.
        if found:
            return None

        return None

    # ========================================================
    # PROVIDERS
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
    # QUALITY
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

        volume_status = (
            self._volume_status(df["volume"])
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
            min(110.0, score),
        )

    # ========================================================
    # FETCH
    # ========================================================

    def fetch(
        self,
        canonical: str,
        interval: str = "15m",
        limit: int = 300,
        asset_type: str = "stock",
        symbol_map: Optional[Dict[str, Any]] = None,
        require_volume: bool = False,
    ) -> pd.DataFrame:

        providers = self._providers_for(
            asset_type
        )

        errors = []

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
                (provider, symbol)
            )

        if not candidates:
            raise DataSourceError(
                f"{canonical}: aucun provider disponible"
            )

        for provider, symbol in candidates:

            cache_key = (
                f"{provider}|{symbol}|"
                f"{interval}|{limit}"
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
                    result.attrs = cached_df.attrs.copy()
                    return result

            try:

                if provider == "binance":
                    df = self._fetch_binance(
                        symbol,
                        interval,
                        limit,
                    )

                elif provider == "yahoo":
                    df = self._fetch_yahoo(
                        symbol,
                        interval,
                        limit,
                    )

                elif provider == "finnhub":
                    df = self._fetch_finnhub(
                        symbol,
                        interval,
                        limit,
                    )

                elif provider == "twelve_data":
                    df = self._fetch_twelvedata(
                        symbol,
                        interval,
                        limit,
                    )

                else:
                    raise DataSourceError(
                        f"Provider inconnu: {provider}"
                    )

                df = self._validate_timestamps(
                    df,
                    provider,
                )

                df = self._drop_incomplete_last_candle(
                    df,
                    interval,
                )

                if len(df) < 1:
                    raise DataSourceError(
                        f"{provider}: aucune bougie exploitable"
                    )

                df = self._attach_metadata(
                    df,
                    provider,
                    symbol,
                    interval,
                )

                quality = self._quality_score(
                    df,
                    provider,
                    require_volume,
                )

                df.attrs["quality_score"] = quality

                self.cache[cache_key] = (
                    time.time(),
                    df.copy(),
                )

                return df

            except Exception as exc:
                errors.append(
                    f"{provider} ({symbol}) : {exc}"
                )

        raise DataSourceError(
            f"{canonical}: "
            + " | ".join(errors)
        )

    # ========================================================
    # STATISTIQUES
    # ========================================================

    def get_stats(self) -> Dict[str, Dict[str, int]]:
        return {
            provider: values.copy()
            for provider, values
            in self.stats.items()
        }

    def get_twelve_data_usage(self) -> Dict[str, int]:
        return {
            "used": self._twelve_data_used,
            "limit": TD_DAILY_LIMIT,
            "remaining": max(
                0,
                TD_DAILY_LIMIT
                - self._twelve_data_used,
            ),
        }

    def get_circuit_breakers(self) -> Dict[str, Dict[str, Any]]:
        now = time.time()

        result = {}

        for provider in PROVIDER_NAMES:
            remaining = max(
                0,
                int(
                    self._provider_cooldown_until[
                        provider
                    ] - now
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

    def router_summary(self) -> Dict[str, Any]:

        stats = self.get_stats()

        calls = sum(
            x["calls"]
            for x in stats.values()
        )

        success = sum(
            x["success"]
            for x in stats.values()
        )

        failures = sum(
            x["failures"]
            for x in stats.values()
        )

        skips = sum(
            x["skips"]
            for x in stats.values()
        )

        return {
            "requests": calls,
            "successes": success,
            "failures": failures,
            "skips": skips,
            "stats": stats,
            "twelve_data": self.get_twelve_data_usage(),
            "circuit_breakers": (
                self.get_circuit_breakers()
            ),
        }


# ============================================================
# WRAPPERS LEGACY
# ============================================================

_default_router = DataRouter()


def fetch_data(
    canonical: str,
    interval: str = "15m",
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
