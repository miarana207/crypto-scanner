"""
Sources de données OHLCV par classe d'actifs.

- Binance       : crypto
- Twelve Data   : forex + actions + matières premières
- Yahoo Finance : indices + fallback / hors fenêtre Twelve Data

Twelve Data :
- timeout augmenté
- retries automatiques
- backoff progressif
- volume absent => 0.0
"""

import os
import time

import requests
import pandas as pd


# ============================================================
# YAHOO FINANCE
# ============================================================

_YF_CONFIG = {
    "1m":  ("1m", "7d"),
    "5m":  ("5m", "60d"),
    "15m": ("15m", "60d"),
    "30m": ("30m", "60d"),
    "1h":  ("60m", "730d"),
    "1d":  ("1d", "10y"),
}


def klines_yahoo(
    symbol,
    interval="15m",
    limit=500,
    **kwargs,
):
    yf_interval, yf_range = _YF_CONFIG.get(
        interval,
        ("15m", "60d"),
    )

    url = (
        "https://query1.finance.yahoo.com/"
        f"v8/finance/chart/{symbol}"
    )

    params = {
        "range": yf_range,
        "interval": yf_interval,
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(compatible; scanner-perso/1.0)"
        )
    }

    r = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=20,
    )

    r.raise_for_status()

    payload = r.json()

    result_list = (
        payload
        .get("chart", {})
        .get("result")
    )

    if not result_list:

        err = (
            payload
            .get("chart", {})
            .get("error")
        )

        raise ValueError(
            f"Yahoo Finance n'a rien renvoyé "
            f"pour {symbol}: {err}"
        )

    result = result_list[0]

    timestamps = result.get(
        "timestamp"
    )

    if not timestamps:

        raise ValueError(
            f"Aucune bougie disponible "
            f"pour {symbol}"
        )

    quote = result[
        "indicators"
    ]["quote"][0]

    df = pd.DataFrame(
        {
            "open_time": pd.to_datetime(
                timestamps,
                unit="s",
                utc=True,
            ),

            "open": quote["open"],
            "high": quote["high"],
            "low": quote["low"],
            "close": quote["close"],
            "volume": quote.get(
                "volume",
                [0.0] * len(timestamps),
            ),
        }
    )

    df["volume"] = pd.to_numeric(
        df["volume"],
        errors="coerce",
    ).fillna(0.0)

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    )

    return (
        df.tail(limit)
        .reset_index(drop=True)
    )


# ============================================================
# TWELVE DATA
# ============================================================

_TD_INTERVAL_MAP = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "1d": "1day",
}


def klines_twelvedata(
    symbol,
    interval="15m",
    limit=150,
    **kwargs,
):
    api_key = os.environ.get(
        "TWELVE_DATA_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY manquant "
            "(variable d'environnement)"
        )

    td_interval = _TD_INTERVAL_MAP.get(
        interval,
        "15min",
    )

    url = (
        "https://api.twelvedata.com/"
        "time_series"
    )

    # ========================================================
    # RETRIES
    # ========================================================

    max_attempts = 3
    timeout = 30

    last_error = None

    for attempt in range(
        1,
        max_attempts + 1,
    ):

        try:

            params = {
                "symbol": symbol,
                "interval": td_interval,

                # 150 suffit largement pour :
                # EMA50 / RSI14 / relvol20
                "outputsize": min(
                    int(limit),
                    5000,
                ),

                "apikey": api_key,
                "timezone": "UTC",
            }

            r = requests.get(
                url,
                params=params,
                timeout=timeout,
            )

            r.raise_for_status()

            data = r.json()

            if data.get("status") == "error":

                message = data.get(
                    "message",
                    "erreur inconnue",
                )

                # Les erreurs API explicites
                # ne sont généralement pas résolues
                # par un retry immédiat.
                raise ValueError(
                    "Twelve Data erreur pour "
                    f"{symbol}: {message}"
                )

            values = data.get(
                "values"
            )

            if not values:

                raise ValueError(
                    "Twelve Data n'a renvoyé "
                    f"aucune donnée pour {symbol}"
                )

            df = pd.DataFrame(values)

            df["open_time"] = (
                pd.to_datetime(
                    df["datetime"],
                    utc=True,
                )
            )

            for col in [
                "open",
                "high",
                "low",
                "close",
            ]:

                df[col] = pd.to_numeric(
                    df[col],
                    errors="coerce",
                )

            if "volume" in df.columns:

                df["volume"] = pd.to_numeric(
                    df["volume"],
                    errors="coerce",
                ).fillna(0.0)

            else:

                # Forex, or et certaines matières
                # peuvent ne pas fournir de volume.
                df["volume"] = 0.0

            df = df.dropna(
                subset=[
                    "open",
                    "high",
                    "low",
                    "close",
                ]
            )

            df = (
                df.sort_values(
                    "open_time"
                )
                .reset_index(drop=True)
            )

            return (
                df[
                    [
                        "open_time",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                    ]
                ]
                .tail(limit)
                .reset_index(drop=True)
            )

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
        ) as e:

            last_error = e

            if attempt < max_attempts:

                wait = 2 ** attempt

                print(
                    f"Twelve Data {symbol}: "
                    f"tentative {attempt}/"
                    f"{max_attempts} échouée "
                    f"({type(e).__name__}), "
                    f"nouvelle tentative dans "
                    f"{wait}s..."
                )

                time.sleep(wait)

            else:

                print(
                    f"Twelve Data {symbol}: "
                    f"{max_attempts} tentatives "
                    "échouées."
                )

        except Exception as e:

            # Les erreurs API / données ne sont
            # pas nécessairement récupérables.
            raise e

    if last_error:

        raise last_error

    raise RuntimeError(
        f"Échec Twelve Data pour {symbol}"
    )


# ============================================================
# FINNHUB FOREX
# ============================================================
#
# Conservé pour compatibilité avec d'autres scripts.
# Le scanner V4 actuel utilise Twelve Data pour le Forex.
# ============================================================

_FH_RESOLUTION_MAP = {
    "1m": ("1", 1),
    "5m": ("5", 5),
    "15m": ("15", 15),
    "30m": ("30", 30),
    "1h": ("60", 60),
    "1d": ("D", 1440),
}


def klines_finnhub_forex(
    symbol,
    interval="15m",
    limit=500,
    **kwargs,
):
    api_key = os.environ.get(
        "FINNHUB_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "FINNHUB_API_KEY manquant "
            "(variable d'environnement)"
        )

    resolution, minutes_per_bar = (
        _FH_RESOLUTION_MAP.get(
            interval,
            ("15", 15),
        )
    )

    now = int(time.time())

    span_seconds = (
        minutes_per_bar
        * 60
        * (limit + 20)
    )

    start = now - span_seconds

    url = (
        "https://finnhub.io/api/v1/"
        "forex/candle"
    )

    params = {
        "symbol": symbol,
        "resolution": resolution,
        "from": start,
        "to": now,
        "token": api_key,
    }

    r = requests.get(
        url,
        params=params,
        timeout=20,
    )

    r.raise_for_status()

    data = r.json()

    if data.get("s") != "ok":

        raise ValueError(
            f"Finnhub n'a pas de données "
            f"pour {symbol} "
            f"(statut: {data.get('s')})"
        )

    df = pd.DataFrame(
        {
            "open_time": pd.to_datetime(
                data["t"],
                unit="s",
                utc=True,
            ),

            "open": data["o"],
            "high": data["h"],
            "low": data["l"],
            "close": data["c"],
            "volume": data["v"],
        }
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

    return (
        df.tail(limit)
        .reset_index(drop=True)
    )
