"""
V4.2 — Indicateurs, scoring et backtest.
"""

import os
import numpy as np
import pandas as pd

SCORE_WEIGHTS = {
    "trend": 25,
    "ema": 20,
    "rsi": 15,
    "volume": 15,
    "breakout": 25,
}

SCORE_MAX = 100

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14
ATR_PERIOD = 14
VOLUME_LOOKBACK = 20
BREAKOUT_LOOKBACK = 20

BREAKOUT_ATR_MULT = 0.20
BREAKOUT_FULL_ATR = 0.50

SL_ATR_MULT = 1.50
TP1_ATR_MULT = 2.25
TP2_ATR_MULT = 3.00

MIN_RR = 1.50

MAX_HOLDING_BARS = 96

DEFAULT_FEE_RATE = 0.0005
DEFAULT_SLIPPAGE = 0.0002

VOLUME_MIN_COVERAGE = float(
    os.getenv(
        "VOLUME_COVERAGE_THRESHOLD",
        "0.80",
    )
)

VOLUME_CONFIRMED = "confirmed"
VOLUME_PARTIAL = "partial"
VOLUME_UNAVAILABLE = "unavailable"
VOLUME_INVALID = "invalid"


def _normalize_datetime(series):
    return pd.to_datetime(
        series,
        utc=True,
        errors="coerce",
    )


def get_volume_status(series):

    if series is None:
        return VOLUME_UNAVAILABLE

    numeric = pd.to_numeric(
        series,
        errors="coerce",
    )

    if len(numeric) == 0:
        return VOLUME_UNAVAILABLE

    invalid = (
        numeric.notna()
        & (numeric < 0)
    )

    valid = (
        numeric.notna()
        & np.isfinite(numeric)
        & (numeric >= 0)
    )

    coverage = float(valid.mean())

    if invalid.any() and coverage == 0:
        return VOLUME_INVALID

    if coverage >= VOLUME_MIN_COVERAGE:
        return VOLUME_CONFIRMED

    if coverage > 0:
        return VOLUME_PARTIAL

    return VOLUME_UNAVAILABLE


def calculate_volume_features(df):

    data = df.copy()

    if "volume" not in data.columns:
        data["volume"] = np.nan

    data["volume"] = pd.to_numeric(
        data["volume"],
        errors="coerce",
    )

    data["volume_invalid"] = (
        data["volume"].notna()
        & (data["volume"] < 0)
    )

    data["volume_available"] = (
        data["volume"].notna()
        & np.isfinite(data["volume"])
        & (data["volume"] >= 0)
    )

    data["prev_volume_mean"] = (
        data["volume"]
        .where(~data["volume_invalid"])
        .shift(1)
        .rolling(
            VOLUME_LOOKBACK,
            min_periods=5,
        )
        .mean()
    )

    data["relvol"] = (
        data["volume"]
        / data["prev_volume_mean"]
    )

    data.loc[
        data["volume_invalid"],
        "relvol",
    ] = np.nan

    return data


def calculate_rsi(series, period=RSI_PERIOD):

    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    rs = avg_gain / avg_loss

    rsi = 100 - (
        100 / (1 + rs)
    )

    rsi = rsi.where(
        avg_loss != 0,
        100,
    )

    rsi = rsi.where(
        ~(
            (avg_gain == 0)
            & (avg_loss == 0)
        ),
        50,
    )

    return rsi


def calculate_atr(df, period=ATR_PERIOD):

    previous_close = df["close"].shift(1)

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()


def calculate_trend_1h(df):

    hourly = (
        df.set_index("open_time")["close"]
        .resample("1h")
        .last()
        .dropna()
        .to_frame("close")
    )

    if hourly.empty:
        return pd.Series(
            np.nan,
            index=df.index,
        )

    hourly["ema20"] = (
        hourly["close"]
        .ewm(
            span=EMA_FAST,
            adjust=False,
        )
        .mean()
    )

    hourly["ema50"] = (
        hourly["close"]
        .ewm(
            span=EMA_SLOW,
            adjust=False,
        )
        .mean()
    )

    hourly["trend1h"] = np.select(
        [
            (
                hourly["ema20"]
                > hourly["ema50"]
            ),
            (
                hourly["ema20"]
                < hourly["ema50"]
            ),
        ],
        [1, -1],
        default=0,
    )

    hourly["available_from"] = (
        hourly.index
        + pd.Timedelta(hours=1)
    )

    left = df[
        ["open_time"]
    ].sort_values("open_time")

    right = hourly[
        [
            "available_from",
            "trend1h",
        ]
    ].sort_values("available_from")

    merged = pd.merge_asof(
        left,
        right,
        left_on="open_time",
        right_on="available_from",
        direction="backward",
    )

    return (
        merged["trend1h"]
        .set_axis(df.index)
    )


def indicators(df):

    if df is None or df.empty:
        return None

    required = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
    ]

    if any(
        column not in df.columns
        for column in required
    ):
        return None

    data = df.copy()

    if "volume" not in data.columns:
        data["volume"] = np.nan

    data["open_time"] = _normalize_datetime(
        data["open_time"]
    )

    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:
        data[column] = pd.to_numeric(
            data[column],
            errors="coerce",
        )

    data = data.dropna(
        subset=required
    )

    data = (
        data.sort_values("open_time")
        .drop_duplicates(
            "open_time",
            keep="last",
        )
        .reset_index(drop=True)
    )

    data["ema20"] = (
        data["close"]
        .ewm(
            span=EMA_FAST,
            adjust=False,
        )
        .mean()
    )

    data["ema50"] = (
        data["close"]
        .ewm(
            span=EMA_SLOW,
            adjust=False,
        )
        .mean()
    )

    data["rsi"] = calculate_rsi(
        data["close"]
    )

    data["atr"] = calculate_atr(
        data
    )

    data = calculate_volume_features(
        data
    )

    data["trend1h"] = (
        calculate_trend_1h(data)
    )

    return data


def _ema_points(row, direction):

    close = row.get("close")
    ema20 = row.get("ema20")
    ema50 = row.get("ema50")
    atr = row.get("atr")

    if any(
        pd.isna(x)
        for x in [
            close,
            ema20,
            ema50,
        ]
    ):
        return 0

    if direction == "LONG":
        if close <= ema20:
            return 0
        if ema20 <= ema50:
            return 5
        return 20

    if close >= ema20:
        return 0
    if ema20 >= ema50:
        return 5
    return 20


def _rsi_points(row, direction):

    rsi = row.get("rsi")

    if pd.isna(rsi):
        return 0

    if direction == "LONG":
        if rsi >= 70:
            return 10
        if rsi > 50:
            return 15
        return 0

    if rsi <= 30:
        return 10
    if rsi < 50:
        return 15
    return 0


def _volume_points(row, direction):

    status = row.get(
        "volume_status",
        VOLUME_UNAVAILABLE,
    )

    if status == VOLUME_INVALID:
        return 0

    if status in {
        VOLUME_UNAVAILABLE,
    }:
        return 0

    relvol = row.get(
        "relvol",
        np.nan,
    )

    if pd.isna(relvol):
        return 0

    trend = row.get(
        "trend1h",
        0,
    )

    if direction == "LONG" and trend < 0:
        return 0

    if direction == "SHORT" and trend > 0:
        return 0

    points = (
        (relvol - 1)
        / 1.5
        * 15
    )

    return max(
        0,
        min(
            15,
            points,
        ),
    )


def _breakout_points(row, direction):

    atr = row.get("atr")

    if pd.isna(atr) or atr <= 0:
        return 0

    close = row.get("close")

    if direction == "LONG":
        level = row.get("prev_high")
        if pd.isna(level):
            return 0

        distance = close - level

    else:
        level = row.get("prev_low")
        if pd.isna(level):
            return 0

        distance = level - close

    threshold = atr * BREAKOUT_ATR_MULT
    full = atr * BREAKOUT_FULL_ATR

    if distance < threshold:
        return 0

    if distance >= full:
        return 25

    ratio = (
        distance - threshold
    ) / (
        full - threshold
    )

    return 10 + ratio * 15


def score(row, return_details=False):

    volume_status = row.get(
        "volume_status"
    )

    if not volume_status:
        if row.get(
            "volume_invalid",
            False,
        ):
            volume_status = VOLUME_INVALID
        elif row.get(
            "volume_available",
            False,
        ):
            volume_status = VOLUME_CONFIRMED
        else:
            volume_status = VOLUME_UNAVAILABLE

    scores = {}

    for direction in [
        "LONG",
        "SHORT",
    ]:

        trend = row.get(
            "trend1h",
            0,
        )

        trend_pts = 25 if (
            (
                direction == "LONG"
                and trend > 0
            )
            or (
                direction == "SHORT"
                and trend < 0
            )
        ) else 0

        ema_pts = _ema_points(
            row,
            direction,
        )

        rsi_pts = _rsi_points(
            row,
            direction,
        )

        volume_pts = _volume_points(
            row,
            direction,
        )

        breakout_pts = _breakout_points(
            row,
            direction,
        )

        total = (
            trend_pts
            + ema_pts
            + rsi_pts
            + volume_pts
            + breakout_pts
        )

        scores[direction] = {
            "total": total,
            "trend_pts": trend_pts,
            "ema_pts": ema_pts,
            "rsi_pts": rsi_pts,
            "volume_pts": volume_pts,
            "breakout_pts": breakout_pts,
        }

    confirmed = (
        volume_status == VOLUME_CONFIRMED
    )

    invalid = (
        volume_status == VOLUME_INVALID
    )

    effective_max = (
        100
        if confirmed
        else 85
    )

    if invalid:
        effective_max = 85

    result = {
        "score_long": min(
            100,
            scores["LONG"]["total"]
            / effective_max
            * 100,
        ),
        "score_short": min(
            100,
            scores["SHORT"]["total"]
            / effective_max
            * 100,
        ),
        "volume_status": volume_status,
        "volume_ok": (
            confirmed
            and not invalid
            and safe_relvol(row) >= 1.5
        ),
        "volume_pts": max(
            scores["LONG"]["volume_pts"],
            scores["SHORT"]["volume_pts"],
        ),
        "breakout_pts": max(
            scores["LONG"]["breakout_pts"],
            scores["SHORT"]["breakout_pts"],
        ),
        "breakout_long": bool(
            row.get(
                "breakout_long",
                False,
            )
        ),
        "breakout_short": bool(
            row.get(
                "breakout_short",
                False,
            )
        ),
        "effective_max": effective_max,
    }

    result["best_score"] = max(
        result["score_long"],
        result["score_short"],
    )

    if return_details:
        return result

    return result["best_score"]


def safe_relvol(row):

    value = row.get(
        "relvol",
        np.nan,
    )

    try:
        value = float(value)
        return value if np.isfinite(value) else 0.0
    except Exception:
        return 0.0


def best_direction(
    score_long,
    score_short,
):
    return (
        "LONG"
        if score_long >= score_short
        else "SHORT"
    )


def calculate_trade_levels(
    row,
    direction,
):

    entry = float(
        row["close"]
    )

    atr = float(
        row["atr"]
    )

    risk = (
        atr
        * SL_ATR_MULT
    )

    if direction == "LONG":

        stop_loss = (
            entry - risk
        )

        tp1 = (
            entry
            + atr * TP1_ATR_MULT
        )

        tp2 = (
            entry
            + atr * TP2_ATR_MULT
        )

    else:

        stop_loss = (
            entry + risk
        )

        tp1 = (
            entry
            - atr * TP1_ATR_MULT
        )

        tp2 = (
            entry
            - atr * TP2_ATR_MULT
        )

    return {
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "rr_tp1": TP1_ATR_MULT / SL_ATR_MULT,
        "rr_tp2": TP2_ATR_MULT / SL_ATR_MULT,
    }


def signal_quality(
    score_value,
    breakout,
    volume_status,
    rr_tp2,
):

    if volume_status == VOLUME_INVALID:
        return "D"

    volume_confirmed = (
        volume_status == VOLUME_CONFIRMED
    )

    if (
        score_value >= 80
        and breakout
        and volume_confirmed
        and rr_tp2 >= 2
    ):
        return "A"

    if (
        score_value >= 75
        and breakout
        and volume_confirmed
        and rr_tp2 >= 1.5
    ):
        return "B"

    if score_value >= 65:
        return "C"

    return "D"


def run(
    df,
    threshold=75,
    max_holding_bars=MAX_HOLDING_BARS,
    fee_rate=DEFAULT_FEE_RATE,
    slippage=DEFAULT_SLIPPAGE,
):

    data = indicators(df)

    if data is None or data.empty:
        return pd.DataFrame()

    data["prev_high"] = (
        data["high"]
        .rolling(BREAKOUT_LOOKBACK)
        .max()
        .shift(1)
    )

    data["prev_low"] = (
        data["low"]
        .rolling(BREAKOUT_LOOKBACK)
        .min()
        .shift(1)
    )

    data["breakout_long"] = (
        data["close"]
        >= data["prev_high"]
        + data["atr"]
        * BREAKOUT_ATR_MULT
    )

    data["breakout_short"] = (
        data["close"]
        <= data["prev_low"]
        - data["atr"]
        * BREAKOUT_ATR_MULT
    )

    global_volume_status = get_volume_status(
        data["volume"]
    )

    trades = []
    i = 0

    while i < len(data) - 1:

        row = data.iloc[i].copy()

        row["volume_status"] = (
            global_volume_status
        )

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

        breakout = (
            bool(row["breakout_long"])
            if direction == "LONG"
            else bool(row["breakout_short"])
        )

        if (
            best_score < threshold
            or not breakout
            or not details["volume_ok"]
        ):
            i += 1
            continue

        levels = calculate_trade_levels(
            row,
            direction,
        )

        entry_bar = i + 1

        if entry_bar >= len(data):
            break

        entry = float(
            data.iloc[entry_bar]["open"]
        )

        if direction == "LONG":
            entry *= (
                1 + slippage
            )
        else:
            entry *= (
                1 - slippage
            )

        atr = float(
            row["atr"]
        )

        if direction == "LONG":
            stop = entry - atr * SL_ATR_MULT
            tp1 = entry + atr * TP1_ATR_MULT
            tp2 = entry + atr * TP2_ATR_MULT
        else:
            stop = entry + atr * SL_ATR_MULT
            tp1 = entry - atr * TP1_ATR_MULT
            tp2 = entry - atr * TP2_ATR_MULT

        exit_price = None
        exit_reason = None
        exit_index = None

        end = min(
            len(data),
            entry_bar
            + max_holding_bars,
        )

        for j in range(
            entry_bar,
            end,
        ):

            bar = data.iloc[j]

            high = float(
                bar["high"]
            )

            low = float(
                bar["low"]
            )

            if direction == "LONG":

                if low <= stop:
                    exit_price = stop
                    exit_reason = "SL"
                    exit_index = j
                    break

                if high >= tp2:
                    exit_price = tp2
                    exit_reason = "TP2"
                    exit_index = j
                    break

            else:

                if high >= stop:
                    exit_price = stop
                    exit_reason = "SL"
                    exit_index = j
                    break

                if low <= tp2:
                    exit_price = tp2
                    exit_reason = "TP2"
                    exit_index = j
                    break

        if exit_price is None:

            exit_index = end - 1

            if exit_index <= entry_bar:
                break

            exit_price = float(
                data.iloc[exit_index]["close"]
            )

            exit_reason = "TIME"

        if direction == "LONG":
            gross = (
                exit_price - entry
            ) / entry
        else:
            gross = (
                entry - exit_price
            ) / entry

        net = (
            gross
            - fee_rate * 2
        )

        risk = (
            atr
            * SL_ATR_MULT
        )

        r_multiple = (
            (
                exit_price - entry
            )
            / risk
            if direction == "LONG"
            else
            (
                entry - exit_price
            )
            / risk
        )

        quality = signal_quality(
            best_score,
            breakout,
            global_volume_status,
            levels["rr_tp2"],
        )

        trades.append(
            {
                "entry_time": data.iloc[
                    entry_bar
                ]["open_time"],
                "exit_time": data.iloc[
                    exit_index
                ]["open_time"],
                "direction": direction,
                "score": best_score,
                "entry": entry,
                "exit": exit_price,
                "stop_loss": stop,
                "take_profit_2": tp2,
                "gross_return": gross,
                "net_return": net,
                "r_multiple": r_multiple,
                "exit_reason": exit_reason,
                "volume_status": global_volume_status,
                "signal_quality": quality,
            }
        )

        i = exit_index + 1

    return pd.DataFrame(trades)


def backtest_statistics(trades):

    if trades is None or trades.empty:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "net_return": 0.0,
            "expectancy": 0.0,
            "avg_r": 0.0,
        }

    wins = (
        trades["net_return"] > 0
    )

    losses = (
        trades["net_return"] < 0
    )

    gross_profit = trades.loc[
        wins,
        "net_return",
    ].sum()

    gross_loss = abs(
        trades.loc[
            losses,
            "net_return",
        ].sum()
    )

    if gross_loss > 0:
        profit_factor = (
            gross_profit
            / gross_loss
        )
    else:
        profit_factor = (
            float("inf")
            if gross_profit > 0
            else 0.0
        )

    return {
        "trades": int(len(trades)),
        "wins": int(wins.sum()),
        "losses": int(losses.sum()),
        "win_rate": float(
            wins.mean() * 100
        ),
        "profit_factor": float(
            profit_factor
        ),
        "net_return": float(
            trades["net_return"].sum()
        ),
        "expectancy": float(
            trades["net_return"].mean()
        ),
        "avg_r": float(
            trades["r_multiple"].mean()
        ),
    }
