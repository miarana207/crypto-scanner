"""
V4.1.1 — Moteur technique et backtest.

Corrections V4.1.1 :
    - Normalisation systématique des timestamps en datetime64[ns, UTC]
    - Compatible Pandas 3.x / merge_asof
    - RSI Wilder
    - ATR(14)
    - Tendance 1H sans look-ahead
    - Score progressif
    - Gestion du volume indisponible
    - Breakout basé sur ATR
    - Entry / SL / TP1 / TP2
    - Risk/Reward
    - Backtest avec simulation des sorties
    - Slippage non compté deux fois
"""

import os
import math

import requests
import pandas as pd

from dotenv import load_dotenv


load_dotenv()


# ======================================================================
# CONFIGURATION
# ======================================================================

BASE = os.getenv(
    "BINANCE_DATA_URL",
    "https://data-api.binance.vision",
)


# ----------------------------------------------------------------------
# SCORE
# ----------------------------------------------------------------------

SCORE_WEIGHTS = {
    "trend": 25.0,
    "ema": 20.0,
    "rsi": 15.0,
    "volume": 15.0,
    "breakout": 25.0,
}

SCORE_MAX = 100.0


# ----------------------------------------------------------------------
# INDICATEURS
# ----------------------------------------------------------------------

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14
ATR_PERIOD = 14

VOLUME_LOOKBACK = 20
BREAKOUT_LOOKBACK = 20

BREAKOUT_ATR_MULT = 0.20
BREAKOUT_FULL_ATR = 0.50


# ----------------------------------------------------------------------
# RISK MANAGEMENT
# ----------------------------------------------------------------------

SL_ATR_MULT = 1.50
TP1_ATR_MULT = 2.25
TP2_ATR_MULT = 3.00

MIN_RR = 1.50


# ----------------------------------------------------------------------
# BACKTEST
# ----------------------------------------------------------------------

DEFAULT_MAX_HOLDING_BARS = 96

DEFAULT_FEE_RATE = 0.0005
DEFAULT_SLIPPAGE = 0.0002


# ======================================================================
# OUTILS
# ======================================================================

def normalize_timestamp_series(series):
    """
    Normalise une série temporelle en datetime64[ns, UTC].

    Pandas 3.x conserve parfois la résolution native du timestamp
    provenant de la source : s / ms / us / ns.

    merge_asof exige des types temporels compatibles.
    """

    result = pd.to_datetime(
        series,
        utc=True,
        errors="coerce",
    )

    try:
        result = result.dt.as_unit("ns")
    except AttributeError:
        # Compatibilité avec d'éventuelles versions Pandas plus anciennes.
        result = pd.to_datetime(
            result,
            utc=True,
            errors="coerce",
        )

    return result


def normalize_timestamp(value):
    """
    Normalise un timestamp scalaire en Timestamp UTC ns.
    """

    result = pd.to_datetime(
        value,
        utc=True,
        errors="coerce",
    )

    if pd.isna(result):
        return result

    try:
        return result.as_unit("ns")
    except AttributeError:
        return result


def safe_float(
    value,
    default=float("nan"),
):
    try:
        value = float(value)

        if math.isnan(value):
            return default

        return value

    except Exception:
        return default


def clamp(
    value,
    minimum,
    maximum,
):
    try:
        return max(
            minimum,
            min(
                maximum,
                float(value),
            ),
        )
    except Exception:
        return minimum


# ======================================================================
# BINANCE
# ======================================================================

def klines(
    symbol,
    interval="5m",
    limit=1000,
):
    """
    Récupère les bougies Binance.

    La bougie en formation est systématiquement supprimée.
    """

    url = f"{BASE}/api/v3/klines"

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
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    if not data:
        raise ValueError(
            f"Binance n'a renvoyé aucune donnée pour {symbol}"
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
        data,
        columns=columns,
    )

    df["open_time"] = normalize_timestamp_series(
        pd.to_datetime(
            df["open_time"],
            unit="ms",
            utc=True,
        )
    )

    df["close_time"] = normalize_timestamp_series(
        pd.to_datetime(
            df["close_time"],
            unit="ms",
            utc=True,
        )
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

    now = normalize_timestamp(
        pd.Timestamp.now(
            tz="UTC"
        )
    )

    df = df[
        df["close_time"] < now
    ].copy()

    df = (
        df.sort_values(
            "open_time"
        )
        .reset_index(
            drop=True
        )
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
        .tail(
            int(limit)
        )
        .reset_index(
            drop=True
        )
    )


# ======================================================================
# RSI WILDER
# ======================================================================

def calculate_rsi(
    series,
    period=14,
):
    """
    RSI utilisant le lissage exponentiel de Wilder.
    """

    delta = series.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

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

    rs = (
        avg_gain
        /
        avg_loss.replace(
            0,
            float("nan"),
        )
    )

    rsi = (
        100
        -
        (
            100
            /
            (1 + rs)
        )
    )

    rsi = rsi.mask(
        (avg_loss == 0)
        &
        (avg_gain > 0),
        100,
    )

    rsi = rsi.mask(
        (avg_gain == 0)
        &
        (avg_loss > 0),
        0,
    )

    rsi = rsi.mask(
        (avg_gain == 0)
        &
        (avg_loss == 0),
        50,
    )

    return rsi


# ======================================================================
# ATR
# ======================================================================

def calculate_atr(
    df,
    period=ATR_PERIOD,
):
    """
    ATR de Wilder.
    """

    previous_close = (
        df["close"]
        .shift(1)
    )

    tr1 = (
        df["high"]
        -
        df["low"]
    )

    tr2 = (
        df["high"]
        -
        previous_close
    ).abs()

    tr3 = (
        df["low"]
        -
        previous_close
    ).abs()

    true_range = pd.concat(
        [
            tr1,
            tr2,
            tr3,
        ],
        axis=1,
    ).max(
        axis=1
    )

    atr = true_range.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    return atr


# ======================================================================
# INDICATEURS
# ======================================================================

def indicators(df):
    """
    Calcule les indicateurs V4.1.1.

    La tendance 1H utilise uniquement la dernière heure
    complètement clôturée.

    Aucun look-ahead.
    """

    d = df.copy()

    # ------------------------------------------------------------------
    # NORMALISATION TEMPORELLE
    # ------------------------------------------------------------------

    d["open_time"] = (
        normalize_timestamp_series(
            d["open_time"]
        )
    )

    for col in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:
        if col not in d.columns:
            d[col] = float("nan")

        d[col] = pd.to_numeric(
            d[col],
            errors="coerce",
        )

    d = (
        d.dropna(
            subset=[
                "open_time",
                "open",
                "high",
                "low",
                "close",
            ]
        )
        .sort_values(
            "open_time"
        )
        .drop_duplicates(
            subset=[
                "open_time"
            ],
            keep="last",
        )
        .reset_index(
            drop=True
        )
    )

    # ------------------------------------------------------------------
    # EMA
    # ------------------------------------------------------------------

    d["ema20"] = (
        d["close"]
        .ewm(
            span=EMA_FAST,
            adjust=False,
        )
        .mean()
    )

    d["ema50"] = (
        d["close"]
        .ewm(
            span=EMA_SLOW,
            adjust=False,
        )
        .mean()
    )

    # ------------------------------------------------------------------
    # RSI
    # ------------------------------------------------------------------

    d["rsi"] = calculate_rsi(
        d["close"],
        RSI_PERIOD,
    )

    # ------------------------------------------------------------------
    # ATR
    # ------------------------------------------------------------------

    d["atr"] = calculate_atr(
        d,
        ATR_PERIOD,
    )

    # ------------------------------------------------------------------
    # VOLUME
    # ------------------------------------------------------------------

    previous_volume_mean = (
        d["volume"]
        .shift(1)
        .rolling(
            VOLUME_LOOKBACK,
            min_periods=VOLUME_LOOKBACK,
        )
        .mean()
    )

    d["relvol"] = (
        d["volume"]
        /
        previous_volume_mean.replace(
            0,
            float("nan"),
        )
    )

    d["volume_available"] = (
        d["volume"].notna()
    )

    # ------------------------------------------------------------------
    # TENDANCE 1H — SANS LOOK-AHEAD
    # ------------------------------------------------------------------

    hourly = (
        d.set_index(
            "open_time"
        )[
            ["close"]
        ]
        .resample(
            "1h"
        )
        .last()
        .dropna()
    )

    if not hourly.empty:

        hourly["ema20_1h"] = (
            hourly["close"]
            .ewm(
                span=20,
                adjust=False,
            )
            .mean()
        )

        hourly["ema50_1h"] = (
            hourly["close"]
            .ewm(
                span=50,
                adjust=False,
            )
            .mean()
        )

        hourly["trend1h"] = 0

        long_condition = (
            (
                hourly["close"]
                >
                hourly["ema20_1h"]
            )
            &
            (
                hourly["ema20_1h"]
                >
                hourly["ema50_1h"]
            )
        )

        short_condition = (
            (
                hourly["close"]
                <
                hourly["ema20_1h"]
            )
            &
            (
                hourly["ema20_1h"]
                <
                hourly["ema50_1h"]
            )
        )

        hourly.loc[
            long_condition,
            "trend1h",
        ] = 1

        hourly.loc[
            short_condition,
            "trend1h",
        ] = -1

        # --------------------------------------------------------------
        # Une heure H devient disponible au début de H+1.
        # --------------------------------------------------------------

        hourly["available_from"] = (
            hourly.index
            +
            pd.Timedelta(
                hours=1
            )
        )

        trend_map = (
            hourly[
                [
                    "available_from",
                    "trend1h",
                ]
            ]
            .rename(
                columns={
                    "available_from":
                        "open_time"
                }
            )
            .reset_index(
                drop=True
            )
        )

        # --------------------------------------------------------------
        # NORMALISATION CRITIQUE POUR PANDAS 3.x
        # --------------------------------------------------------------

        left = (
            d.sort_values(
                "open_time"
            )
            .copy()
        )

        right = (
            trend_map.sort_values(
                "open_time"
            )
            .copy()
        )

        left["open_time"] = (
            normalize_timestamp_series(
                left["open_time"]
            )
        )

        right["open_time"] = (
            normalize_timestamp_series(
                right["open_time"]
            )
        )

        # Vérification explicite.
        if (
            str(left["open_time"].dtype)
            !=
            str(right["open_time"].dtype)
        ):
            raise TypeError(
                "Incompatibilité temporelle avant "
                f"merge_asof : "
                f"{left['open_time'].dtype} "
                f"vs "
                f"{right['open_time'].dtype}"
            )

        d = pd.merge_asof(
            left,
            right,
            on="open_time",
            direction="backward",
        )

    else:

        d["trend1h"] = 0

    d["trend1h"] = (
        d["trend1h"]
        .fillna(0)
        .astype(int)
    )

    return d


# ======================================================================
# SCORE
# ======================================================================

def _ema_points(
    close,
    ema20,
    ema50,
    atr,
    direction,
):
    if any(
        pd.isna(x)
        for x in [
            close,
            ema20,
            ema50,
            atr,
        ]
    ):
        return 0.0

    if atr <= 0:
        return 0.0

    if direction == "LONG":

        if not (
            close
            >
            ema20
            >
            ema50
        ):
            return 0.0

        strength = (
            (
                close
                -
                ema20
            )
            +
            (
                ema20
                -
                ema50
            )
        ) / atr

    else:

        if not (
            close
            <
            ema20
            <
            ema50
        ):
            return 0.0

        strength = (
            (
                ema20
                -
                close
            )
            +
            (
                ema50
                -
                ema20
            )
        ) / atr

    normalized = clamp(
        strength / 0.75,
        0,
        1,
    )

    return (
        SCORE_WEIGHTS["ema"]
        *
        normalized
    )


def _rsi_points(
    rsi,
    direction,
):
    if pd.isna(rsi):
        return 0.0

    if direction == "LONG":

        if rsi <= 50:
            return 0.0

        normalized = clamp(
            (rsi - 50) / 10,
            0,
            1,
        )

    else:

        if rsi >= 50:
            return 0.0

        normalized = clamp(
            (50 - rsi) / 10,
            0,
            1,
        )

    return (
        SCORE_WEIGHTS["rsi"]
        *
        normalized
    )


def _volume_points(
    relvol,
    direction,
    trend1h,
    volume_available,
):
    if not volume_available:
        return 0.0

    if pd.isna(relvol):
        return 0.0

    if direction == "LONG":

        if trend1h < 0:
            return 0.0

    else:

        if trend1h > 0:
            return 0.0

    normalized = clamp(
        (
            float(relvol)
            -
            1.0
        )
        /
        1.5,
        0,
        1,
    )

    return (
        SCORE_WEIGHTS["volume"]
        *
        normalized
    )


def _breakout_points(
    row,
    direction,
):
    close = row.get(
        "close",
        float("nan"),
    )

    atr = row.get(
        "atr",
        float("nan"),
    )

    if (
        pd.isna(close)
        or
        pd.isna(atr)
    ):
        return 0.0

    if atr <= 0:
        return 0.0

    if direction == "LONG":

        level = row.get(
            "prev_high",
            float("nan"),
        )

        if pd.isna(level):
            return 0.0

        distance = (
            close
            -
            level
        )

    else:

        level = row.get(
            "prev_low",
            float("nan"),
        )

        if pd.isna(level):
            return 0.0

        distance = (
            level
            -
            close
        )

    # --------------------------------------------------------------
    # Avant 0.20 ATR : aucun point.
    # --------------------------------------------------------------

    minimum_distance = (
        atr
        *
        BREAKOUT_ATR_MULT
    )

    if distance < minimum_distance:
        return 0.0

    normalized = clamp(
        distance
        /
        (
            atr
            *
            BREAKOUT_FULL_ATR
        ),
        0,
        1,
    )

    return (
        SCORE_WEIGHTS["breakout"]
        *
        normalized
    )


def score(
    row,
    return_details=False,
):
    """
    Score LONG et SHORT.

    Si le volume est indisponible, les autres composantes
    sont renormalisées sur une base de 100.
    """

    close = row.get(
        "close",
        float("nan"),
    )

    ema20 = row.get(
        "ema20",
        float("nan"),
    )

    ema50 = row.get(
        "ema50",
        float("nan"),
    )

    rsi = row.get(
        "rsi",
        float("nan"),
    )

    atr = row.get(
        "atr",
        float("nan"),
    )

    relvol = row.get(
        "relvol",
        float("nan"),
    )

    trend1h = int(
        row.get(
            "trend1h",
            0,
        )
    )

    volume_available = bool(
        row.get(
            "volume_available",
            False,
        )
    )

    breakout_long = bool(
        row.get(
            "breakout_long",
            False,
        )
    )

    breakout_short = bool(
        row.get(
            "breakout_short",
            False,
        )
    )

    # ------------------------------------------------------------------
    # TREND
    # ------------------------------------------------------------------

    trend_long = (
        SCORE_WEIGHTS["trend"]
        if trend1h == 1
        else 0.0
    )

    trend_short = (
        SCORE_WEIGHTS["trend"]
        if trend1h == -1
        else 0.0
    )

    # ------------------------------------------------------------------
    # EMA
    # ------------------------------------------------------------------

    ema_long = _ema_points(
        close,
        ema20,
        ema50,
        atr,
        "LONG",
    )

    ema_short = _ema_points(
        close,
        ema20,
        ema50,
        atr,
        "SHORT",
    )

    # ------------------------------------------------------------------
    # RSI
    # ------------------------------------------------------------------

    rsi_long = _rsi_points(
        rsi,
        "LONG",
    )

    rsi_short = _rsi_points(
        rsi,
        "SHORT",
    )

    # ------------------------------------------------------------------
    # VOLUME
    # ------------------------------------------------------------------

    volume_long = _volume_points(
        relvol,
        "LONG",
        trend1h,
        volume_available,
    )

    volume_short = _volume_points(
        relvol,
        "SHORT",
        trend1h,
        volume_available,
    )

    # ------------------------------------------------------------------
    # BREAKOUT
    # ------------------------------------------------------------------

    breakout_long_pts = (
        _breakout_points(
            row,
            "LONG",
        )
    )

    breakout_short_pts = (
        _breakout_points(
            row,
            "SHORT",
        )
    )

    # ------------------------------------------------------------------
    # TOTAL
    # ------------------------------------------------------------------

    long_raw = (
        trend_long
        +
        ema_long
        +
        rsi_long
        +
        volume_long
        +
        breakout_long_pts
    )

    short_raw = (
        trend_short
        +
        ema_short
        +
        rsi_short
        +
        volume_short
        +
        breakout_short_pts
    )

    # ------------------------------------------------------------------
    # NORMALISATION SI VOLUME ABSENT
    # ------------------------------------------------------------------

    if volume_available:

        long_score = long_raw
        short_score = short_raw

    else:

        available_max = (
            SCORE_MAX
            -
            SCORE_WEIGHTS["volume"]
        )

        factor = (
            SCORE_MAX
            /
            available_max
        )

        long_score = (
            long_raw
            *
            factor
        )

        short_score = (
            short_raw
            *
            factor
        )

    details = {
        "long": {
            "trend": float(
                trend_long
            ),
            "ema": float(
                ema_long
            ),
            "rsi": float(
                rsi_long
            ),
            "volume": float(
                volume_long
            ),
            "breakout": float(
                breakout_long_pts
            ),
        },

        "short": {
            "trend": float(
                trend_short
            ),
            "ema": float(
                ema_short
            ),
            "rsi": float(
                rsi_short
            ),
            "volume": float(
                volume_short
            ),
            "breakout": float(
                breakout_short_pts
            ),
        },

        "volume_ok": (
            bool(
                pd.notna(relvol)
                and
                float(relvol) >= 1.5
            )
            if volume_available
            else False
        ),

        "volume_available":
            volume_available,

        "breakout_long":
            breakout_long,

        "breakout_short":
            breakout_short,

        "effective_max":
            SCORE_MAX,
    }

    if return_details:

        return (
            float(long_score),
            float(short_score),
            details,
        )

    return (
        float(long_score),
        float(short_score),
    )


# ======================================================================
# DIRECTION
# ======================================================================

def best_direction(
    score_long,
    score_short,
):
    if score_long > score_short:
        return "LONG"

    if score_short > score_long:
        return "SHORT"

    return "-"


# ======================================================================
# RISK MANAGEMENT
# ======================================================================

def calculate_trade_levels(
    close,
    atr,
    direction,
):
    """
    Calcule Entry / SL / TP1 / TP2.
    """

    if (
        pd.isna(close)
        or
        pd.isna(atr)
        or
        atr <= 0
    ):
        return {
            "entry": float("nan"),
            "stop_loss": float("nan"),
            "take_profit_1": float("nan"),
            "take_profit_2": float("nan"),
            "risk": float("nan"),
            "rr_tp1": float("nan"),
            "rr_tp2": float("nan"),
        }

    entry = float(close)

    risk = (
        float(atr)
        *
        SL_ATR_MULT
    )

    if direction == "LONG":

        stop_loss = (
            entry
            -
            risk
        )

        take_profit_1 = (
            entry
            +
            float(atr)
            *
            TP1_ATR_MULT
        )

        take_profit_2 = (
            entry
            +
            float(atr)
            *
            TP2_ATR_MULT
        )

    elif direction == "SHORT":

        stop_loss = (
            entry
            +
            risk
        )

        take_profit_1 = (
            entry
            -
            float(atr)
            *
            TP1_ATR_MULT
        )

        take_profit_2 = (
            entry
            -
            float(atr)
            *
            TP2_ATR_MULT
        )

    else:

        return {
            "entry": float("nan"),
            "stop_loss": float("nan"),
            "take_profit_1": float("nan"),
            "take_profit_2": float("nan"),
            "risk": float("nan"),
            "rr_tp1": float("nan"),
            "rr_tp2": float("nan"),
        }

    rr_tp1 = (
        abs(
            take_profit_1
            -
            entry
        )
        /
        risk
    )

    rr_tp2 = (
        abs(
            take_profit_2
            -
            entry
        )
        /
        risk
    )

    return {
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit_1":
            take_profit_1,
        "take_profit_2":
            take_profit_2,
        "risk": risk,
        "rr_tp1": rr_tp1,
        "rr_tp2": rr_tp2,
    }


# ======================================================================
# QUALITÉ
# ======================================================================

def signal_quality(
    score_value,
    breakout_ok,
    volume_ok,
    volume_available,
    rr_tp2,
):
    if (
        score_value >= 80
        and
        breakout_ok
        and
        (
            volume_ok
            or
            not volume_available
        )
        and
        pd.notna(rr_tp2)
        and
        rr_tp2 >= 2.0
    ):
        return "A"

    if (
        score_value >= 75
        and
        breakout_ok
        and
        (
            volume_ok
            or
            not volume_available
        )
        and
        pd.notna(rr_tp2)
        and
        rr_tp2 >= MIN_RR
    ):
        return "B"

    if score_value >= 65:
        return "C"

    return "D"


# ======================================================================
# BACKTEST V4.1.1
# ======================================================================

def run(
    df,
    threshold=75,
    max_holding_bars=DEFAULT_MAX_HOLDING_BARS,
    fee_rate=DEFAULT_FEE_RATE,
    slippage=DEFAULT_SLIPPAGE,
):
    """
    Backtest V4.1.1.

    Une entrée est déclenchée sur la bougie suivant le signal.

    Sorties :
        - Stop Loss
        - TP2
        - fin de durée maximale

    Si SL et TP sont touchés sur la même bougie,
    le scénario conservateur retient le SL en premier.
    """

    d = indicators(df)

    d["prev_high"] = (
        d["high"]
        .rolling(
            BREAKOUT_LOOKBACK,
            min_periods=BREAKOUT_LOOKBACK,
        )
        .max()
        .shift(1)
    )

    d["prev_low"] = (
        d["low"]
        .rolling(
            BREAKOUT_LOOKBACK,
            min_periods=BREAKOUT_LOOKBACK,
        )
        .min()
        .shift(1)
    )

    d["breakout_long"] = (
        d["close"]
        >
        (
            d["prev_high"]
            +
            d["atr"]
            *
            BREAKOUT_ATR_MULT
        )
    )

    d["breakout_short"] = (
        d["close"]
        <
        (
            d["prev_low"]
            -
            d["atr"]
            *
            BREAKOUT_ATR_MULT
        )
    )

    trades = []

    i = 0

    while i < len(d) - 1:

        signal_row = d.iloc[i]

        (
            long_score,
            short_score,
            details,
        ) = score(
            signal_row,
            return_details=True,
        )

        direction = best_direction(
            long_score,
            short_score,
        )

        if direction == "LONG":

            best_score = long_score

            breakout_ok = bool(
                details[
                    "breakout_long"
                ]
            )

        elif direction == "SHORT":

            best_score = short_score

            breakout_ok = bool(
                details[
                    "breakout_short"
                ]
            )

        else:

            i += 1
            continue

        volume_available = details[
            "volume_available"
        ]

        volume_ok = details[
            "volume_ok"
        ]

        trigger_volume_ok = (
            volume_ok
            or
            not volume_available
        )

        if not (
            best_score >= threshold
            and
            breakout_ok
            and
            trigger_volume_ok
        ):
            i += 1
            continue

        levels = calculate_trade_levels(
            signal_row["close"],
            signal_row["atr"],
            direction,
        )

        if pd.isna(
            levels["stop_loss"]
        ):
            i += 1
            continue

        if (
            levels["rr_tp2"]
            <
            MIN_RR
        ):
            i += 1
            continue

        entry_index = i + 1

        if entry_index >= len(d):
            break

        entry_row = d.iloc[
            entry_index
        ]

        entry = float(
            entry_row["open"]
        )

        # --------------------------------------------------------------
        # Slippage appliqué UNE SEULE FOIS sur l'entrée.
        # --------------------------------------------------------------

        if direction == "LONG":

            actual_entry = (
                entry
                *
                (
                    1
                    +
                    slippage
                )
            )

            stop_loss = (
                actual_entry
                -
                levels["risk"]
            )

            take_profit = (
                actual_entry
                +
                (
                    levels["risk"]
                    *
                    levels["rr_tp2"]
                )
            )

        else:

            actual_entry = (
                entry
                *
                (
                    1
                    -
                    slippage
                )
            )

            stop_loss = (
                actual_entry
                +
                levels["risk"]
            )

            take_profit = (
                actual_entry
                -
                (
                    levels["risk"]
                    *
                    levels["rr_tp2"]
                )
            )

        exit_price = None
        exit_time = None
        exit_reason = None
        bars_held = 0

        end_index = min(
            len(d),
            entry_index
            +
            max_holding_bars,
        )

        for j in range(
            entry_index,
            end_index,
        ):

            candle = d.iloc[j]

            high = float(
                candle["high"]
            )

            low = float(
                candle["low"]
            )

            bars_held += 1

            if direction == "LONG":

                stop_hit = (
                    low
                    <=
                    stop_loss
                )

                tp_hit = (
                    high
                    >=
                    take_profit
                )

                if stop_hit:

                    exit_price = (
                        stop_loss
                    )

                    exit_reason = "SL"

                elif tp_hit:

                    exit_price = (
                        take_profit
                    )

                    exit_reason = "TP2"

            else:

                stop_hit = (
                    high
                    >=
                    stop_loss
                )

                tp_hit = (
                    low
                    <=
                    take_profit
                )

                if stop_hit:

                    exit_price = (
                        stop_loss
                    )

                    exit_reason = "SL"

                elif tp_hit:

                    exit_price = (
                        take_profit
                    )

                    exit_reason = "TP2"

            if exit_price is not None:

                exit_time = (
                    candle[
                        "open_time"
                    ]
                )

                break

        if exit_price is None:

            last_index = (
                end_index - 1
            )

            if last_index < entry_index:

                i += 1
                continue

            last_candle = d.iloc[
                last_index
            ]

            exit_price = float(
                last_candle[
                    "close"
                ]
            )

            exit_time = (
                last_candle[
                    "open_time"
                ]
            )

            exit_reason = "TIME"

        # --------------------------------------------------------------
        # P&L
        # --------------------------------------------------------------

        if direction == "LONG":

            gross_return = (
                exit_price
                -
                actual_entry
            ) / actual_entry

        else:

            gross_return = (
                actual_entry
                -
                exit_price
            ) / actual_entry

        # --------------------------------------------------------------
        # Les frais sont appliqués ici.
        # Le slippage est déjà intégré à actual_entry.
        # --------------------------------------------------------------

        total_cost = (
            fee_rate
            *
            2
        )

        net_return = (
            gross_return
            -
            total_cost
        )

        risk_fraction = (
            levels["risk"]
            /
            actual_entry
        )

        if risk_fraction > 0:

            r_multiple = (
                net_return
                /
                risk_fraction
            )

        else:

            r_multiple = float(
                "nan"
            )

        trades.append(
            {
                "signal_time":
                    signal_row[
                        "open_time"
                    ],

                "entry_time":
                    entry_row[
                        "open_time"
                    ],

                "exit_time":
                    exit_time,

                "direction":
                    direction,

                "score":
                    float(
                        best_score
                    ),

                "entry":
                    float(
                        actual_entry
                    ),

                "stop_loss":
                    float(
                        stop_loss
                    ),

                "take_profit":
                    float(
                        take_profit
                    ),

                "exit_price":
                    float(
                        exit_price
                    ),

                "exit_reason":
                    exit_reason,

                "bars_held":
                    bars_held,

                "gross_return":
                    float(
                        gross_return
                    ),

                "net_return":
                    float(
                        net_return
                    ),

                "r_multiple":
                    float(
                        r_multiple
                    ),

                "relvol":
                    safe_float(
                        signal_row.get(
                            "relvol"
                        )
                    ),

                "atr":
                    safe_float(
                        signal_row.get(
                            "atr"
                        )
                    ),
            }
        )

        # Évite plusieurs positions simultanées.
        i = max(
            i + 1,
            entry_index
            +
            bars_held,
        )

    return pd.DataFrame(
        trades
    )


# ======================================================================
# STATISTIQUES
# ======================================================================

def backtest_statistics(
    trades,
):
    if (
        trades is None
        or
        trades.empty
    ):

        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "net_return": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
        }

    wins = trades[
        trades["net_return"] > 0
    ]

    losses = trades[
        trades["net_return"] <= 0
    ]

    win_rate = (
        len(wins)
        /
        len(trades)
        *
        100
    )

    gross_profit = wins[
        "net_return"
    ].sum()

    gross_loss = abs(
        losses[
            "net_return"
        ].sum()
    )

    if gross_loss > 0:

        profit_factor = (
            gross_profit
            /
            gross_loss
        )

    else:

        profit_factor = float(
            "inf"
        )

    return {
        "trades":
            int(len(trades)),

        "wins":
            int(len(wins)),

        "losses":
            int(len(losses)),

        "win_rate":
            float(win_rate),

        "net_return":
            float(
                trades[
                    "net_return"
                ].sum()
            ),

        "profit_factor":
            float(
                profit_factor
            ),

        "expectancy":
            float(
                trades[
                    "net_return"
                ].mean()
            ),
    }


# ======================================================================
# TEST
# ======================================================================

if __name__ == "__main__":

    print(
        "Module backtest.py V4.1.1 chargé correctement."
    )
