import os
import time
import requests

import pandas as pd
from dotenv import load_dotenv

load_dotenv()


# ======================================================================
# CONFIGURATION
# ======================================================================

TWELVE_DATA_API_KEY = os.getenv(
    "TWELVE_DATA_API_KEY"
)

YAHOO_BASE = (
    "https://query1.finance.yahoo.com/v8/finance/chart/"
)

TWELVE_DATA_BASE = (
    "https://api.twelvedata.com/time_series"
)


# ======================================================================
# INTERVALLES
# ======================================================================

def interval_to_seconds(interval):
    interval = str(
        interval
    ).strip().lower()

    if interval.endswith("m"):
        return int(
            interval[:-1]
        ) * 60

    if interval.endswith("h"):
        return int(
            interval[:-1]
        ) * 3600

    if interval.endswith("d"):
        return int(
            interval[:-1]
        ) * 86400

    if interval.endswith("w"):
        return int(
            interval[:-1]
        ) * 604800

    return 60


def is_completed_candle(
    open_time,
    interval
):
    """
    Vérifie qu'une bougie est totalement clôturée.
    """

    if pd.isna(open_time):
        return False

    ts = pd.Timestamp(
        open_time
    )

    if ts.tzinfo is None:
        ts = ts.tz_localize(
            "UTC"
        )
    else:
        ts = ts.tz_convert(
            "UTC"
        )

    now = pd.Timestamp.now(
        tz="UTC"
    )

    age = (
        now - ts
    ).total_seconds()

    return (
        age >= interval_to_seconds(
            interval
        )
    )


def keep_completed_candles(
    df,
    interval
):
    if df.empty:
        return df

    d = df.copy()

    d["open_time"] = pd.to_datetime(
        d["open_time"],
        utc=True,
        errors="coerce"
    )

    d = d.dropna(
        subset=["open_time"]
    )

    if d.empty:
        return d

    interval_seconds = interval_to_seconds(
        interval
    )

    now = pd.Timestamp.now(
        tz="UTC"
    )

    completed = (
        (
            now - d["open_time"]
        ).dt.total_seconds()
        >= interval_seconds
    )

    return d.loc[
        completed
    ].copy()


# ======================================================================
# YAHOO FINANCE
# ======================================================================

_YF_CONFIG = {
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


def klines_yahoo(
    symbol,
    interval="15m",
    limit=1000
):
    """
    Yahoo Finance.

    Volume absent/invalide = NaN.
    Jamais 0 artificiellement.
    """

    config = _YF_CONFIG.get(
        interval,
        {
            "range": "60d",
            "interval": interval,
        }
    )

    url = (
        YAHOO_BASE
        + requests.utils.quote(
            str(symbol),
            safe=""
        )
    )

    params = {
        "range": config["range"],
        "interval": config["interval"],
        "includePrePost": "false",
        "events": "div,splits",
    }

    response = requests.get(
        url,
        params=params,
        timeout=30,
        headers={
            "User-Agent":
                "Mozilla/5.0"
        }
    )

    response.raise_for_status()

    payload = response.json()

    chart = payload.get(
        "chart",
        {}
    )

    results = chart.get(
        "result"
    )

    if not results:
        return pd.DataFrame()

    result = results[0]

    timestamps = result.get(
        "timestamp",
        []
    )

    indicators = result.get(
        "indicators",
        {}
    )

    quote_list = indicators.get(
        "quote",
        []
    )

    if not timestamps or not quote_list:
        return pd.DataFrame()

    quote = quote_list[0]

    df = pd.DataFrame(
        {
            "open_time": pd.to_datetime(
                timestamps,
                unit="s",
                utc=True
            ),
            "open": quote.get(
                "open",
                []
            ),
            "high": quote.get(
                "high",
                []
            ),
            "low": quote.get(
                "low",
                []
            ),
            "close": quote.get(
                "close",
                []
            ),
            "volume": quote.get(
                "volume",
                []
            ),
        }
    )

    for col in [
        "open",
        "high",
        "low",
        "close",
    ]:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df["volume"] = pd.to_numeric(
        df["volume"],
        errors="coerce"
    )

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    )

    df = df.sort_values(
        "open_time"
    ).reset_index(
        drop=True
    )

    df = keep_completed_candles(
        df,
        interval
    )

    return df[
        [
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    ].tail(
        int(limit)
    ).reset_index(
        drop=True
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


def klines_twelvedata(
    symbol,
    interval="15m",
    limit=1000
):
    """
    Twelve Data.

    Particularité importante :
    si le fournisseur ne retourne pas de volume,
    on utilise NaN, pas 0.
    """

    if not TWELVE_DATA_API_KEY:
        raise RuntimeError(
            "TWELVE_DATA_API_KEY absente."
        )

    td_interval = _TD_INTERVALS.get(
        interval,
        interval
    )

    outputsize = min(
        int(limit),
        5000
    )

    params = {
        "symbol": symbol,
        "interval": td_interval,
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_API_KEY,
        "format": "JSON",
        "timezone": "UTC",
    }

    last_error = None

    for attempt in range(3):

        try:

            response = requests.get(
                TWELVE_DATA_BASE,
                params=params,
                timeout=30
            )

            response.raise_for_status()

            payload = response.json()

            if "status" in payload:
                if payload.get("status") == "error":
                    raise RuntimeError(
                        payload.get(
                            "message",
                            "Erreur Twelve Data"
                        )
                    )

            values = payload.get(
                "values"
            )

            if not values:
                raise RuntimeError(
                    "Aucune donnée Twelve Data."
                )

            df = pd.DataFrame(
                values
            )

            if "datetime" not in df.columns:
                raise RuntimeError(
                    "Colonne datetime absente."
                )

            df["open_time"] = pd.to_datetime(
                df["datetime"],
                utc=True,
                errors="coerce"
            )

            for col in [
                "open",
                "high",
                "low",
                "close",
            ]:
                if col not in df.columns:
                    raise RuntimeError(
                        f"Colonne {col} absente."
                    )

                df[col] = pd.to_numeric(
                    df[col],
                    errors="coerce"
                )

            # ----------------------------------------------------------
            # Volume
            # ----------------------------------------------------------

            if "volume" in df.columns:

                df["volume"] = pd.to_numeric(
                    df["volume"],
                    errors="coerce"
                )

            else:
                # IMPORTANT :
                # absence de volume = NaN
                df["volume"] = float("nan")

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
            ).reset_index(
                drop=True
            )

            df = keep_completed_candles(
                df,
                interval
            )

            df = df[
                [
                    "open_time",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                ]
            ]

            return df.tail(
                int(limit)
            ).reset_index(
                drop=True
            )

        except Exception as exc:

            last_error = exc

            if attempt < 2:
                time.sleep(
                    2 ** (attempt + 1)
                )

    raise RuntimeError(
        f"Twelve Data échoué pour "
        f"{symbol}: {last_error}"
    )


# ======================================================================
# FINNHUB — COMPATIBILITÉ
# ======================================================================

def klines_finnhub(
    symbol,
    interval="15m",
    limit=1000
):
    """
    Fonction conservée pour compatibilité.

    Le scanner V4 actuel utilise Twelve Data
    pour le Forex.
    """

    api_key = os.getenv(
        "FINNHUB_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "FINNHUB_API_KEY absente."
        )

    # Finnhub n'est plus la source principale de V4.
    # On retourne un DataFrame vide plutôt que de produire
    # des données incompatibles avec le scanner.
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
