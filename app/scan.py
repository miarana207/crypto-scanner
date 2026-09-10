"""
V4.2 — Scanner et génération des signaux.
"""

import math
import time
from datetime import datetime, timezone

import pandas as pd

from backtest import (
    BREAKOUT_ATR_MULT,
    BREAKOUT_LOOKBACK,
    MIN_RR,
    best_direction,
    calculate_trade_levels,
    indicators,
    score,
    signal_quality,
)

DEFAULT_THRESHOLD = 75
MIN_CANDLES = 120


def safe_float(value, default=float("nan")):
    try:
        value = float(value)
        return default if math.isnan(value) else value
    except Exception:
        return default


def format_relvol(value):
    value = safe_float(value)
    if math.isnan(value):
        return "N/A"
    return f"{value:.2f}x"


def empty_result_columns():
    return [
        "symbol",
        "provider",
        "score_long",
        "score_short",
        "score",
        "direction",
        "status",
        "quality",
        "entry",
        "stop_loss",
        "take_profit_1",
        "take_profit_2",
        "rr_tp1",
        "rr_tp2",
        "atr",
        "rsi",
        "relvol",
        "trend1h",
        "breakout_long",
        "breakout_short",
        "breakout_pts",
        "volume_pts",
        "volume_status",
        "volume_coverage",
        "volume_available",
        "last_candle",
        "age_minutes",
    ]


def _clean_dataframe(df):

    if df is None or df.empty:
        return None

    df = df.copy()

    required = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
    ]

    if any(column not in df.columns for column in required):
        return None

    if "volume" not in df.columns:
        df["volume"] = float("nan")

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
        subset=required
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


def _drop_incomplete_last_candle(
    df,
    interval,
):
    if df is None or df.empty:
        return df

    minutes = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "45m": 45,
        "1h": 60,
        "2h": 120,
        "4h": 240,
        "1d": 1440,
        "1wk": 10080,
    }.get(interval)

    if minutes is None:
        return df

    last_open = df["open_time"].iloc[-1]

    if pd.isna(last_open):
        return df

    now = pd.Timestamp.now(tz="UTC")

    if (
        last_open
        + pd.Timedelta(minutes=minutes)
        > now
    ):
        return df.iloc[:-1].copy()

    return df


def scan(
    symbols,
    fetcher,
    interval="15m",
    limit=1000,
    threshold=75,
    pause=0.0,
    fallback_fetcher=None,
    provider_name="unknown",
    fallback_provider_name=None,
    asset_type="stock",
):

    results = []

    for symbol in symbols:

        try:

            if getattr(fetcher, "_router", False):
                df = fetcher(
                    symbol,
                    interval,
                    limit,
                    asset_type=asset_type,
                )
            else:
                try:
                    df = fetcher(
                        symbol,
                        interval,
                        limit,
                    )
                except TypeError:
                    df = fetcher(
                        symbol,
                        interval=interval,
                        limit=limit,
                    )

            used_provider = (
                df.attrs.get(
                    "provider",
                    provider_name,
                )
                if df is not None
                else provider_name
            )

        except Exception as first_error:

            if fallback_fetcher is None:
                print(
                    f"[{symbol}] fetch error : "
                    f"{first_error}"
                )
                continue

            try:
                df = fallback_fetcher(
                    symbol,
                    interval,
                    limit,
                )

                used_provider = (
                    fallback_provider_name
                    or "fallback"
                )

            except Exception as second_error:
                print(
                    f"[{symbol}] fallback error : "
                    f"{second_error}"
                )
                continue

        df = _clean_dataframe(df)

        if df is None or df.empty:
            continue

        df = _drop_incomplete_last_candle(
            df,
            interval,
        )

        if len(df) < MIN_CANDLES:
            print(
                f"[{symbol}] seulement "
                f"{len(df)} bougies."
            )
            continue

        last_candle = df["open_time"].iloc[-1]

        now = pd.Timestamp.now(tz="UTC")

        if last_candle > now + pd.Timedelta(minutes=2):
            print(
                f"[{symbol}] timestamp futur rejeté."
            )
            continue

        data = indicators(df)

        if data is None or data.empty:
            continue

        volume_status = df.attrs.get(
            "volume_status"
        )

        volume_coverage = df.attrs.get(
            "volume_coverage",
            float("nan"),
        )

        if volume_status is None:
            numeric_volume = pd.to_numeric(
                data["volume"],
                errors="coerce",
            )

            valid = (
                numeric_volume.notna()
                & (numeric_volume >= 0)
            )

            coverage = (
                float(valid.mean())
                if len(valid)
                else 0.0
            )

            if coverage >= 0.80:
                volume_status = "confirmed"
            elif coverage > 0:
                volume_status = "partial"
            else:
                volume_status = "unavailable"

            volume_coverage = coverage

        prev_high = (
            data["high"]
            .rolling(
                BREAKOUT_LOOKBACK
            )
            .max()
            .shift(1)
        )

        prev_low = (
            data["low"]
            .rolling(
                BREAKOUT_LOOKBACK
            )
            .min()
            .shift(1)
        )

        data["prev_high"] = prev_high
        data["prev_low"] = prev_low

        data["breakout_long"] = (
            data["close"]
            >= (
                data["prev_high"]
                + data["atr"]
                * BREAKOUT_ATR_MULT
            )
        )

        data["breakout_short"] = (
            data["close"]
            <= (
                data["prev_low"]
                - data["atr"]
                * BREAKOUT_ATR_MULT
            )
        )

        row = data.iloc[-1].copy()

        row["volume_status"] = volume_status
        row["volume_coverage"] = volume_coverage

        if volume_status == "confirmed":
            row["volume_available"] = True
        else:
            row["volume_available"] = False

        details = score(
            row,
            return_details=True,
        )

        direction = best_direction(
            details["score_long"],
            details["score_short"],
        )

        best_score = max(
            details["score_long"],
            details["score_short"],
        )

        if direction == "LONG":
            breakout = bool(
                row.get(
                    "breakout_long",
                    False,
                )
            )
        else:
            breakout = bool(
                row.get(
                    "breakout_short",
                    False,
                )
            )

        volume_ok = bool(
            details.get(
                "volume_ok",
                False,
            )
        )

        levels = calculate_trade_levels(
            row,
            direction,
        )

        rr_tp2 = safe_float(
            levels.get("rr_tp2")
        )

        quality = signal_quality(
            best_score,
            breakout,
            volume_status,
            rr_tp2,
        )

        strong = (
            best_score >= threshold
            and breakout
            and volume_ok
            and rr_tp2 >= MIN_RR
        )

        if strong:
            status = "SIGNAL FORT"
        elif best_score >= threshold:
            status = "ATTENTE"
        else:
            status = "SOUS SEUIL"

        age_minutes = (
            now - last_candle
        ).total_seconds() / 60.0

        result = {
            "symbol": symbol,
            "provider": used_provider,
            "score_long": details["score_long"],
            "score_short": details["score_short"],
            "score": best_score,
            "direction": direction,
            "status": status,
            "quality": quality,
            "entry": levels.get("entry"),
            "stop_loss": levels.get("stop_loss"),
            "take_profit_1": levels.get(
                "take_profit_1"
            ),
            "take_profit_2": levels.get(
                "take_profit_2"
            ),
            "rr_tp1": levels.get("rr_tp1"),
            "rr_tp2": levels.get("rr_tp2"),
            "atr": row.get("atr"),
            "rsi": row.get("rsi"),
            "relvol": row.get("relvol"),
            "trend1h": row.get("trend1h"),
            "breakout_long": row.get(
                "breakout_long"
            ),
            "breakout_short": row.get(
                "breakout_short"
            ),
            "breakout_pts": details.get(
                "breakout_pts",
                0,
            ),
            "volume_pts": details.get(
                "volume_pts",
                0,
            ),
            "volume_status": volume_status,
            "volume_coverage": volume_coverage,
            "volume_available": (
                volume_status == "confirmed"
            ),
            "last_candle": last_candle,
            "age_minutes": age_minutes,
        }

        results.append(result)

        if pause > 0:
            time.sleep(pause)

    if not results:
        return pd.DataFrame(
            columns=empty_result_columns()
        )

    result_df = pd.DataFrame(results)

    status_order = {
        "SIGNAL FORT": 0,
        "ATTENTE": 1,
        "SOUS SEUIL": 2,
    }

    result_df["_status_order"] = (
        result_df["status"]
        .map(status_order)
        .fillna(9)
    )

    result_df = (
        result_df
        .sort_values(
            [
                "_status_order",
                "score",
                "quality",
            ],
            ascending=[True, False, False],
        )
        .drop(columns=["_status_order"])
        .reset_index(drop=True)
    )

    return result_df
