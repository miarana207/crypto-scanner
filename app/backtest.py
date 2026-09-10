"""
V4.2 — Indicateurs, scoring et backtest.

Objectifs V4.2 :
- Indicateurs robustes sur OHLCV.
- Volume absent = NaN, jamais 0.
- Gestion explicite du volume :
    VOLUME_CONFIRMED
    VOLUME_UNAVAILABLE
    VOLUME_INVALID
- Aucun look-ahead dans le calcul du trend 1h.
- Breakout calculé uniquement à partir des bougies précédentes.
- Scoring compatible avec les marchés sans volume exploitable.
- Backtest sans utilisation de données futures.
- Gestion déterministe des collisions SL/TP dans une même bougie.
"""

import math
import os

import pandas as pd
from dotenv import load_dotenv

load_dotenv()


# ============================================================
# CONFIGURATION
# ============================================================

SCORE_WEIGHTS = {
    "trend": 25.0,
    "ema": 20.0,
    "rsi": 15.0,
    "volume": 15.0,
    "breakout": 25.0,
}

SCORE_MAX = 100.0

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

# Pour considérer un historique de volume comme exploitable,
# une proportion minimale des bougies doit contenir un volume valide.
VOLUME_MIN_COVERAGE = float(
    os.getenv("VOLUME_MIN_COVERAGE", "0.80")
)


# ============================================================
# CONSTANTES VOLUME
# ============================================================

VOLUME_CONFIRMED = "VOLUME_CONFIRMED"
VOLUME_UNAVAILABLE = "VOLUME_UNAVAILABLE"
VOLUME_INVALID = "VOLUME_INVALID"


# ============================================================
# UTILITAIRES
# ============================================================

def normalize_timestamp_series(series):
    """
    Convertit une série de timestamps en UTC avec une représentation
    stable compatible avec les versions récentes de pandas.
    """
    out = pd.to_datetime(series, utc=True, errors="coerce")

    try:
        return out.dt.as_unit("ns")
    except AttributeError:
        return out


def normalize_timestamp(value):
    """
    Normalise un timestamp individuel en UTC.
    """
    out = pd.to_datetime(value, utc=True, errors="coerce")

    try:
        return out.as_unit("ns")
    except AttributeError:
        return out


def safe_float(value, default=float("nan")):
    """
    Conversion sûre en float.
    """
    try:
        x = float(value)

        if math.isnan(x):
            return default

        return x

    except (TypeError, ValueError):
        return default


def clamp(value, low=0.0, high=1.0):
    """
    Limite une valeur à [low, high].
    """
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return low


# ============================================================
# VOLUME
# ============================================================

def get_volume_status(series):
    """
    Détermine la qualité d'une série de volumes.

    IMPORTANT :
    - NaN reste indisponible.
    - zéro réel reste zéro.
    - aucun volume valide => UNAVAILABLE.
    - volume négatif => INVALID.
    - couverture insuffisante => UNAVAILABLE.

    Aucun volume n'est inventé.
    """

    if series is None:
        return VOLUME_UNAVAILABLE

    try:
        volume = pd.to_numeric(series, errors="coerce")
    except Exception:
        return VOLUME_UNAVAILABLE

    if volume.empty:
        return VOLUME_UNAVAILABLE

    valid = volume.notna()

    if not valid.any():
        return VOLUME_UNAVAILABLE

    if (volume.loc[valid] < 0).any():
        return VOLUME_INVALID

    coverage = float(valid.mean())

    if coverage < VOLUME_MIN_COVERAGE:
        return VOLUME_UNAVAILABLE

    return VOLUME_CONFIRMED


def calculate_volume_features(df):
    """
    Calcule les métriques liées au volume.

    Le calcul du relvol n'utilise que les volumes réellement présents.

    Si le volume historique est incomplet :
    - relvol devient NaN lorsque le benchmark n'est pas calculable ;
    - aucune valeur artificielle n'est créée.
    """

    out = df.copy()

    if "volume" not in out.columns:
        out["volume"] = float("nan")

    out["volume"] = pd.to_numeric(
        out["volume"],
        errors="coerce"
    )

    out["volume_available"] = (
        out["volume"].notna()
        & (out["volume"] >= 0)
    )

    out["volume_invalid"] = (
        out["volume"].notna()
        & (out["volume"] < 0)
    )

    out["prev_volume_mean"] = (
        out["volume"]
        .shift(1)
        .rolling(
            VOLUME_LOOKBACK,
            min_periods=VOLUME_LOOKBACK
        )
        .mean()
    )

    out["relvol"] = (
        out["volume"]
        / out["prev_volume_mean"].replace(
            0,
            float("nan")
        )
    )

    # Un relvol négatif ou infini n'est jamais exploitable.
    out.loc[
        ~pd.to_numeric(
            out["relvol"],
            errors="coerce"
        ).between(0, float("inf")),
        "relvol"
    ] = float("nan")

    return out


# ============================================================
# RSI
# ============================================================

def calculate_rsi(series, period=RSI_PERIOD):
    """
    RSI de Wilder via moyenne exponentielle.

    Gestion explicite :
    - hausse uniquement => RSI 100
    - baisse uniquement => RSI 0
    - mouvement nul => RSI 50
    """

    series = pd.to_numeric(
        series,
        errors="coerce"
    )

    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        float("nan")
    )

    rsi = 100 - (
        100 / (1 + rs)
    )

    rsi = rsi.mask(
        (avg_gain == 0) & (avg_loss > 0),
        0
    )

    rsi = rsi.mask(
        (avg_loss == 0) & (avg_gain > 0),
        100
    )

    rsi = rsi.mask(
        (avg_gain == 0) & (avg_loss == 0),
        50
    )

    return pd.to_numeric(
        rsi,
        errors="coerce"
    )


# ============================================================
# ATR
# ============================================================

def calculate_atr(df, period=ATR_PERIOD):
    """
    ATR basé sur le True Range.
    """

    high = pd.to_numeric(
        df["high"],
        errors="coerce"
    )

    low = pd.to_numeric(
        df["low"],
        errors="coerce"
    )

    close = pd.to_numeric(
        df["close"],
        errors="coerce"
    )

    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()


# ============================================================
# TREND 1H
# ============================================================

def calculate_trend_1h(df):
    """
    Calcule le trend 1H sans look-ahead.

    Principe :
    la tendance de la bougie 1H n'est disponible qu'après
    la clôture de cette heure.

    On décale donc l'information d'une heure avant de la
    rattacher aux bougies de l'intervalle principal.
    """

    out = df.copy()

    hourly = (
        out.set_index("open_time")["close"]
        .resample("1h")
        .last()
        .dropna()
        .to_frame("close")
    )

    if hourly.empty:
        out["trend1h"] = 0
        return out

    hourly["ema20_1h"] = (
        hourly["close"]
        .ewm(
            span=EMA_FAST,
            adjust=False,
            min_periods=EMA_FAST
        )
        .mean()
    )

    hourly["ema50_1h"] = (
        hourly["close"]
        .ewm(
            span=EMA_SLOW,
            adjust=False,
            min_periods=EMA_SLOW
        )
        .mean()
    )

    hourly["trend1h"] = 0

    long_condition = (
        (hourly["close"] > hourly["ema20_1h"])
        & (
            hourly["ema20_1h"]
            > hourly["ema50_1h"]
        )
    )

    short_condition = (
        (hourly["close"] < hourly["ema20_1h"])
        & (
            hourly["ema20_1h"]
            < hourly["ema50_1h"]
        )
    )

    hourly.loc[
        long_condition,
        "trend1h"
    ] = 1

    hourly.loc[
        short_condition,
        "trend1h"
    ] = -1

    # La tendance d'une heure n'est utilisable qu'après
    # la clôture de cette heure.
    hourly["available_from"] = (
        hourly.index
        + pd.Timedelta(hours=1)
    )

    trend_map = (
        hourly[
            ["available_from", "trend1h"]
        ]
        .rename(
            columns={
                "available_from": "open_time"
            }
        )
        .reset_index(drop=True)
    )

    out["open_time"] = normalize_timestamp_series(
        out["open_time"]
    )

    trend_map["open_time"] = normalize_timestamp_series(
        trend_map["open_time"]
    )

    try:
        out = pd.merge_asof(
            out.sort_values("open_time"),
            trend_map.sort_values("open_time"),
            on="open_time",
            direction="backward",
        )

    except Exception:
        out["trend1h"] = 0

    if "trend1h" not in out.columns:
        out["trend1h"] = 0

    out["trend1h"] = (
        pd.to_numeric(
            out["trend1h"],
            errors="coerce"
        )
        .fillna(0)
        .astype(int)
    )

    return out


# ============================================================
# INDICATEURS PRINCIPAUX
# ============================================================

def indicators(df):
    """
    Prépare toutes les données nécessaires au scoring.

    Aucun remplissage artificiel du volume.
    """

    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()

    required = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
    ]

    for col in required:
        if col not in out.columns:
            return pd.DataFrame()

    if "volume" not in out.columns:
        out["volume"] = float("nan")

    out["open_time"] = normalize_timestamp_series(
        out["open_time"]
    )

    for col in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:
        out[col] = pd.to_numeric(
            out[col],
            errors="coerce"
        )

    out = out.dropna(
        subset=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
        ]
    )

    if out.empty:
        return out

    # Validation OHLC.
    valid_ohlc = (
        (out["high"] >= out["open"])
        & (out["high"] >= out["close"])
        & (out["low"] <= out["open"])
        & (out["low"] <= out["close"])
        & (out["high"] >= out["low"])
    )

    out = out.loc[valid_ohlc].copy()

    out = (
        out.sort_values("open_time")
        .drop_duplicates(
            "open_time",
            keep="last"
        )
        .reset_index(drop=True)
    )

    if out.empty:
        return out

    # -------------------------
    # EMA
    # -------------------------

    out["ema20"] = (
        out["close"]
        .ewm(
            span=EMA_FAST,
            adjust=False,
            min_periods=EMA_FAST
        )
        .mean()
    )

    out["ema50"] = (
        out["close"]
        .ewm(
            span=EMA_SLOW,
            adjust=False,
            min_periods=EMA_SLOW
        )
        .mean()
    )

    # -------------------------
    # RSI
    # -------------------------

    out["rsi"] = calculate_rsi(
        out["close"]
    )

    # -------------------------
    # ATR
    # -------------------------

    out["atr"] = calculate_atr(
        out
    )

    # -------------------------
    # Volume
    # -------------------------

    out = calculate_volume_features(
        out
    )

    # -------------------------
    # Trend 1H
    # -------------------------

    out = calculate_trend_1h(
        out
    )

    return out.reset_index(
        drop=True
    )


# ============================================================
# SCORING — EMA
# ============================================================

def _ema_points(
    close,
    ema20,
    ema50,
    atr,
    direction
):
    """
    Score EMA de 0 à 20.
    """

    if not all(
        pd.notna(x)
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
            close > ema20 > ema50
        ):
            return 0.0

        strength = (
            (close - ema20)
            + (ema20 - ema50)
        ) / atr

    elif direction == "SHORT":

        if not (
            close < ema20 < ema50
        ):
            return 0.0

        strength = (
            (ema20 - close)
            + (ema50 - ema20)
        ) / atr

    else:
        return 0.0

    return (
        SCORE_WEIGHTS["ema"]
        * clamp(
            strength / 0.75
        )
    )


# ============================================================
# SCORING — RSI
# ============================================================

def _rsi_points(
    rsi,
    direction
):
    """
    Score RSI de 0 à 15.

    Le RSI est utilisé comme confirmation de momentum,
    pas comme signal de surachat/survente autonome.
    """

    if pd.isna(rsi):
        return 0.0

    if direction == "LONG" and rsi > 50:
        return (
            SCORE_WEIGHTS["rsi"]
            * clamp(
                (rsi - 50) / 10
            )
        )

    if direction == "SHORT" and rsi < 50:
        return (
            SCORE_WEIGHTS["rsi"]
            * clamp(
                (50 - rsi) / 10
            )
        )

    return 0.0


# ============================================================
# SCORING — VOLUME
# ============================================================

def _volume_points(
    relvol,
    direction,
    trend1h,
    volume_available
):
    """
    Score volume de 0 à 15.

    Si le volume n'est pas disponible :
    => 0 point.

    Le score global sera ensuite normalisé afin que l'absence
    structurelle de volume ne pénalise pas artificiellement
    le signal.
    """

    if not volume_available:
        return 0.0

    if pd.isna(relvol):
        return 0.0

    if relvol < 0:
        return 0.0

    if (
        direction == "LONG"
        and trend1h < 0
    ):
        return 0.0

    if (
        direction == "SHORT"
        and trend1h > 0
    ):
        return 0.0

    return (
        SCORE_WEIGHTS["volume"]
        * clamp(
            (relvol - 1) / 1.5
        )
    )


# ============================================================
# SCORING — BREAKOUT
# ============================================================

def _breakout_points(
    row,
    direction
):
    """
    Score breakout de 0 à 25.
    """

    close = safe_float(
        row.get("close")
    )

    atr = safe_float(
        row.get("atr")
    )

    if direction == "LONG":
        level = safe_float(
            row.get("prev_high")
        )
    else:
        level = safe_float(
            row.get("prev_low")
        )

    if any(
        pd.isna(x)
        for x in [
            close,
            atr,
            level,
        ]
    ):
        return 0.0

    if atr <= 0:
        return 0.0

    if direction == "LONG":
        distance = close - level
    elif direction == "SHORT":
        distance = level - close
    else:
        return 0.0

    if distance < (
        atr * BREAKOUT_ATR_MULT
    ):
        return 0.0

    return (
        SCORE_WEIGHTS["breakout"]
        * clamp(
            distance
            / (
                atr
                * BREAKOUT_FULL_ATR
            )
        )
    )


# ============================================================
# SCORE GLOBAL
# ============================================================

def score(
    row,
    return_details=False
):
    """
    Calcule le score LONG et SHORT.

    Lorsque le volume est indisponible, les 15 points volume
    sont retirés du dénominateur puis le score est normalisé
    sur 100.

    Cela évite de considérer "volume absent" comme "volume faible".
    """

    close = safe_float(
        row.get("close")
    )

    trend1h = int(
        safe_float(
            row.get(
                "trend1h",
                0
            ),
            0
        )
    )

    atr = safe_float(
        row.get("atr")
    )

    relvol = safe_float(
        row.get("relvol")
    )

    volume_status = row.get(
        "volume_status"
    )

    if volume_status is None:
        if bool(
            row.get(
                "volume_invalid",
                False
            )
        ):
            volume_status = VOLUME_INVALID

        elif bool(
            row.get(
                "volume_available",
                False
            )
        ):
            volume_status = VOLUME_CONFIRMED

        else:
            volume_status = VOLUME_UNAVAILABLE

    volume_available = (
        volume_status
        == VOLUME_CONFIRMED
    )

    volume_invalid = (
        volume_status
        == VOLUME_INVALID
    )

    breakout_long = bool(
        row.get(
            "breakout_long",
            False
        )
    )

    breakout_short = bool(
        row.get(
            "breakout_short",
            False
        )
    )

    results = {}

    for direction in (
        "LONG",
        "SHORT"
    ):

        trend_pts = (
            SCORE_WEIGHTS["trend"]
            if (
                (
                    direction == "LONG"
                    and trend1h > 0
                )
                or
                (
                    direction == "SHORT"
                    and trend1h < 0
                )
            )
            else 0.0
        )

        ema_pts = _ema_points(
            close,
            safe_float(
                row.get("ema20")
            ),
            safe_float(
                row.get("ema50")
            ),
            atr,
            direction,
        )

        rsi_pts = _rsi_points(
            safe_float(
                row.get("rsi")
            ),
            direction,
        )

        volume_pts = _volume_points(
            relvol,
            direction,
            trend1h,
            volume_available,
        )

        breakout_pts = _breakout_points(
            row,
            direction,
        )

        raw = (
            trend_pts
            + ema_pts
            + rsi_pts
            + volume_pts
            + breakout_pts
        )

        if volume_available:
            effective_max = SCORE_MAX

        elif volume_status == VOLUME_UNAVAILABLE:
            effective_max = (
                SCORE_MAX
                - SCORE_WEIGHTS["volume"]
            )

        else:
            # Volume invalide = on ne doit pas le considérer
            # comme une absence normale.
            effective_max = (
                SCORE_MAX
                - SCORE_WEIGHTS["volume"]
            )

        if effective_max > 0:
            final = (
                raw
                * (
                    SCORE_MAX
                    / effective_max
                )
            )
        else:
            final = 0.0

        results[direction] = {
            "trend_pts": trend_pts,
            "ema_pts": ema_pts,
            "rsi_pts": rsi_pts,
            "volume_pts": volume_pts,
            "breakout_pts": breakout_pts,
            "raw_score": raw,
            "score": min(
                final,
                SCORE_MAX
            ),
        }

    best = max(
        results["LONG"]["score"],
        results["SHORT"]["score"]
    )

    volume_ok = bool(
        volume_available
        and pd.notna(relvol)
        and relvol >= 1.5
    )

    details = {
        "long": results["LONG"],
        "short": results["SHORT"],

        "volume_ok": volume_ok,

        "volume_available":
            volume_available,

        "volume_status":
            volume_status,

        "volume_invalid":
            volume_invalid,

        "breakout_long":
            breakout_long,

        "breakout_short":
            breakout_short,

        "effective_max":
            (
                SCORE_MAX
                if volume_available
                else (
                    SCORE_MAX
                    - SCORE_WEIGHTS["volume"]
                )
            ),

        "best_score":
            best,
    }

    if return_details:
        return details

    return best


# ============================================================
# DIRECTION
# ============================================================

def best_direction(
    score_long,
    score_short
):
    """
    Détermine la meilleure direction.

    En cas d'égalité, LONG est conservé pour compatibilité
    avec la logique historique.
    """

    if (
        pd.isna(score_long)
        and pd.isna(score_short)
    ):
        return "-"

    long_score = safe_float(
        score_long,
        -1
    )

    short_score = safe_float(
        score_short,
        -1
    )

    if long_score >= short_score:
        return "LONG"

    return "SHORT"


# ============================================================
# NIVEAUX DE TRADE
# ============================================================

def calculate_trade_levels(
    close,
    atr,
    direction
):
    """
    Calcule entrée théorique, SL, TP1, TP2 et RR.
    """

    close = safe_float(
        close
    )

    atr = safe_float(
        atr
    )

    if (
        pd.isna(close)
        or pd.isna(atr)
        or atr <= 0
        or direction not in (
            "LONG",
            "SHORT"
        )
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

    risk = (
        atr
        * SL_ATR_MULT
    )

    if direction == "LONG":

        sl = close - risk

        tp1 = (
            close
            + atr * TP1_ATR_MULT
        )

        tp2 = (
            close
            + atr * TP2_ATR_MULT
        )

        rr_tp1 = (
            tp1 - close
        ) / risk

        rr_tp2 = (
            tp2 - close
        ) / risk

    else:

        sl = close + risk

        tp1 = (
            close
            - atr * TP1_ATR_MULT
        )

        tp2 = (
            close
            - atr * TP2_ATR_MULT
        )

        rr_tp1 = (
            close - tp1
        ) / risk

        rr_tp2 = (
            close - tp2
        ) / risk

    return {
        "entry": close,
        "stop_loss": sl,
        "take_profit_1": tp1,
        "take_profit_2": tp2,
        "risk": risk,
        "rr_tp1": rr_tp1,
        "rr_tp2": rr_tp2,
    }


# ============================================================
# QUALITÉ DU SIGNAL
# ============================================================

def signal_quality(
    score_value,
    breakout_ok,
    volume_ok,
    volume_available,
    rr_tp2
):
    """
    Classe le signal A/B/C/D.

    Si le volume est structurellement indisponible,
    il ne bloque pas automatiquement le signal.

    En revanche, un volume réellement invalide ne doit pas
    être traité comme un volume simplement absent.
    """

    score_value = safe_float(
        score_value
    )

    rr_tp2 = safe_float(
        rr_tp2
    )

    if pd.isna(score_value):
        return "D"

    volume_condition = (
        volume_ok
        or not volume_available
    )

    if (
        score_value >= 80
        and breakout_ok
        and volume_condition
        and pd.notna(rr_tp2)
        and rr_tp2 >= 2.0
    ):
        return "A"

    if (
        score_value >= 75
        and breakout_ok
        and volume_condition
        and pd.notna(rr_tp2)
        and rr_tp2 >= MIN_RR
    ):
        return "B"

    if score_value >= 65:
        return "C"

    return "D"


# ============================================================
# BACKTEST
# ============================================================

def run(
    df,
    threshold=75,
    max_holding_bars=MAX_HOLDING_BARS,
    fee_rate=DEFAULT_FEE_RATE,
    slippage=DEFAULT_SLIPPAGE,
):
    """
    Exécute le backtest.

    Règles :

    1. Le signal est calculé sur la bougie i.
    2. L'entrée est faite sur l'ouverture de i+1.
    3. SL/TP sont calculés à partir de l'entrée réelle.
    4. Aucune donnée future n'est utilisée pour le signal.
    5. Si SL et TP sont touchés dans la même bougie,
       le SL est considéré prioritaire (hypothèse conservatrice).
    """

    data = indicators(df)

    if data is None or data.empty:
        return pd.DataFrame()

    # --------------------------------------------------------
    # Breakout
    # --------------------------------------------------------

    data["prev_high"] = (
        data["high"]
        .rolling(
            BREAKOUT_LOOKBACK,
            min_periods=BREAKOUT_LOOKBACK
        )
        .max()
        .shift(1)
    )

    data["prev_low"] = (
        data["low"]
        .rolling(
            BREAKOUT_LOOKBACK,
            min_periods=BREAKOUT_LOOKBACK
        )
        .min()
        .shift(1)
    )

    data["breakout_long"] = (
        data["close"]
        >
        (
            data["prev_high"]
            + data["atr"]
            * BREAKOUT_ATR_MULT
        )
    )

    data["breakout_short"] = (
        data["close"]
        <
        (
            data["prev_low"]
            - data["atr"]
            * BREAKOUT_ATR_MULT
        )
    )

    # --------------------------------------------------------
    # Volume global
    # --------------------------------------------------------

    volume_status = get_volume_status(
        data["volume"]
    )

    data["volume_status"] = (
        volume_status
    )

    # --------------------------------------------------------
    # Backtest
    # --------------------------------------------------------

    trades = []

    i = 0

    max_holding_bars = max(
        1,
        int(max_holding_bars)
    )

    fee_rate = max(
        0.0,
        float(fee_rate)
    )

    slippage = max(
        0.0,
        float(slippage)
    )

    threshold = float(
        threshold
    )

    while i < len(data) - 1:

        row = data.iloc[i]

        details = score(
            row,
            return_details=True
        )

        long_score = details[
            "long"
        ]["score"]

        short_score = details[
            "short"
        ]["score"]

        direction = best_direction(
            long_score,
            short_score
        )

        best_score = max(
            long_score,
            short_score
        )

        if direction == "-":
            i += 1
            continue

        breakout_ok = bool(
            details[
                "breakout_long"
                if direction == "LONG"
                else "breakout_short"
            ]
        )

        volume_ok = bool(
            details["volume_ok"]
        )

        volume_available = bool(
            details["volume_available"]
        )

        volume_invalid = bool(
            details["volume_invalid"]
        )

        # Volume invalide : on ne trade pas.
        if volume_invalid:
            i += 1
            continue

        # Conditions minimales du signal.
        if (
            best_score < threshold
            or not breakout_ok
            or not (
                volume_ok
                or not volume_available
            )
        ):
            i += 1
            continue

        # ----------------------------------------------------
        # Entrée sur la bougie suivante
        # ----------------------------------------------------

        entry_index = i + 1

        if entry_index >= len(data):
            break

        entry_row = data.iloc[
            entry_index
        ]

        entry_open = safe_float(
            entry_row["open"]
        )

        if pd.isna(entry_open) or entry_open <= 0:
            i += 1
            continue

        actual_entry = (
            entry_open
            * (
                1 + slippage
                if direction == "LONG"
                else 1 - slippage
            )
        )

        # ----------------------------------------------------
        # ATR / risque
        # ----------------------------------------------------

        signal_atr = safe_float(
            row["atr"]
        )

        if (
            pd.isna(signal_atr)
            or signal_atr <= 0
        ):
            i += 1
            continue

        risk = (
            signal_atr
            * SL_ATR_MULT
        )

        if risk <= 0:
            i += 1
            continue

        risk_fraction = (
            risk / actual_entry
        )

        if (
            pd.isna(risk_fraction)
            or risk_fraction <= 0
        ):
            i += 1
            continue

        if direction == "LONG":

            stop = (
                actual_entry
                - risk
            )

            tp1 = (
                actual_entry
                + signal_atr
                * TP1_ATR_MULT
            )

            tp2 = (
                actual_entry
                + signal_atr
                * TP2_ATR_MULT
            )

        else:

            stop = (
                actual_entry
                + risk
            )

            tp1 = (
                actual_entry
                - signal_atr
                * TP1_ATR_MULT
            )

            tp2 = (
                actual_entry
                - signal_atr
                * TP2_ATR_MULT
            )

        rr_tp1 = (
            TP1_ATR_MULT
            / SL_ATR_MULT
        )

        rr_tp2 = (
            TP2_ATR_MULT
            / SL_ATR_MULT
        )

        if rr_tp2 < MIN_RR:
            i += 1
            continue

        # ----------------------------------------------------
        # Recherche sortie
        # ----------------------------------------------------

        exit_price = None
        exit_index = None
        reason = "TIME"

        bars_held = 0

        last_index = min(
            entry_index
            + max_holding_bars
            - 1,
            len(data) - 1
        )

        for j in range(
            entry_index,
            last_index + 1
        ):

            bar = data.iloc[j]

            bars_held += 1

            high = safe_float(
                bar["high"]
            )

            low = safe_float(
                bar["low"]
            )

            close = safe_float(
                bar["close"]
            )

            if any(
                pd.isna(x)
                for x in [
                    high,
                    low,
                    close,
                ]
            ):
                continue

            if direction == "LONG":

                hit_sl = (
                    low <= stop
                )

                hit_tp = (
                    high >= tp2
                )

                # Hypothèse conservatrice :
                # si les deux niveaux sont touchés
                # dans la même bougie, SL d'abord.
                if hit_sl:

                    exit_price = stop
                    exit_index = j
                    reason = "SL"
                    break

                if hit_tp:

                    exit_price = tp2
                    exit_index = j
                    reason = "TP2"
                    break

            else:

                hit_sl = (
                    high >= stop
                )

                hit_tp = (
                    low <= tp2
                )

                if hit_sl:

                    exit_price = stop
                    exit_index = j
                    reason = "SL"
                    break

                if hit_tp:

                    exit_price = tp2
                    exit_index = j
                    reason = "TP2"
                    break

            # Tant que ni SL ni TP n'est atteint,
            # la position est évaluée au close.
            exit_price = close
            exit_index = j

        if exit_price is None:
            i += 1
            continue

        # ----------------------------------------------------
        # Rendement
        # ----------------------------------------------------

        if direction == "LONG":

            gross = (
                exit_price
                - actual_entry
            ) / actual_entry

        else:

            gross = (
                actual_entry
                - exit_price
            ) / actual_entry

        # Frais aller + retour.
        net = (
            gross
            - fee_rate * 2
        )

        r_multiple = (
            net / risk_fraction
            if risk_fraction
            and not pd.isna(
                risk_fraction
            )
            else float("nan")
        )

        exit_time = data.iloc[
            exit_index
        ]["open_time"]

        # ----------------------------------------------------
        # Trade
        # ----------------------------------------------------

        trades.append(
            {
                "signal_time":
                    row["open_time"],

                "entry_time":
                    entry_row["open_time"],

                "exit_time":
                    exit_time,

                "direction":
                    direction,

                "score":
                    best_score,

                "entry":
                    actual_entry,

                "stop_loss":
                    stop,

                "take_profit_1":
                    tp1,

                "take_profit_2":
                    tp2,

                "exit_price":
                    exit_price,

                "exit_reason":
                    reason,

                "bars_held":
                    bars_held,

                "gross_return":
                    gross,

                "net_return":
                    net,

                "r_multiple":
                    r_multiple,

                "relvol":
                    row.get(
                        "relvol",
                        float("nan")
                    ),

                "atr":
                    signal_atr,

                "volume_status":
                    details[
                        "volume_status"
                    ],

                "volume_available":
                    volume_available,

                "signal_quality":
                    signal_quality(
                        best_score,
                        breakout_ok,
                        volume_ok,
                        volume_available,
                        rr_tp2,
                    ),
            }
        )

        # ----------------------------------------------------
        # Évite le chevauchement des trades
        # ----------------------------------------------------

        i = max(
            exit_index + 1,
            i + 1
        )

    return pd.DataFrame(
        trades
    )


# ============================================================
# STATISTIQUES BACKTEST
# ============================================================

def backtest_statistics(
    trades
):
    """
    Statistiques principales du backtest.
    """

    if (
        trades is None
        or trades.empty
    ):
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

    if "net_return" not in trades.columns:
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

    returns = pd.to_numeric(
        trades["net_return"],
        errors="coerce"
    ).dropna()

    if returns.empty:
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

    wins = returns[
        returns > 0
    ]

    losses = returns[
        returns <= 0
    ]

    gross_profit = float(
        wins.sum()
    )

    gross_loss = abs(
        float(losses.sum())
    )

    if gross_loss == 0:

        profit_factor = (
            float("inf")
            if gross_profit > 0
            else 0.0
        )

    else:

        profit_factor = (
            gross_profit
            / gross_loss
        )

    if "r_multiple" in trades.columns:

        r_values = pd.to_numeric(
            trades["r_multiple"],
            errors="coerce"
        )

        avg_r = safe_float(
            r_values.mean(),
            0.0
        )

    else:

        avg_r = 0.0

    return {
        "trades":
            len(returns),

        "wins":
            len(wins),

        "losses":
            len(losses),

        "win_rate":
            (
                len(wins)
                / len(returns)
                * 100
            ),

        "profit_factor":
            profit_factor,

        "net_return":
            float(returns.sum()),

        "expectancy":
            float(returns.mean()),

        "avg_r":
            avg_r,
    }
