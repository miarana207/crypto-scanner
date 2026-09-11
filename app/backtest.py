"""
V4.2 — Moteur d'indicateurs, scoring et backtest.

Fonctions principales :
- normalisation OHLCV
- EMA 20 / EMA 50
- RSI 14
- ATR 14
- tendance 1H sans look-ahead
- volume relatif
- breakout
- scoring LONG / SHORT
- qualité du signal
- backtest avec SL / TP / frais / slippage

Point critique V4.2 :
tous les timestamps sont forcés en datetime64[ns, UTC].
Cela évite les erreurs pandas de type :
datetime64[ms, UTC] vs datetime64[us, UTC].
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


# ============================================================
# CONFIGURATION
# ============================================================

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

DEFAULT_FEE_RATE = float(
    os.getenv(
        "FEE_RATE",
        "0.0005",
    )
)

DEFAULT_SLIPPAGE = float(
    os.getenv(
        "SLIPPAGE",
        "0.0002",
    )
)

VOLUME_MIN_COVERAGE = float(
    os.getenv(
        "VOLUME_MIN_COVERAGE",
        "0.80",
    )
)


# ============================================================
# SCORE
# ============================================================

SCORE_WEIGHTS = {
    "trend": 25,
    "ema20": 20,
    "rsi": 15,
    "volume": 15,
    "breakout": 25,
}

MAX_SCORE = sum(
    SCORE_WEIGHTS.values()
)


# ============================================================
# VOLUME STATUS
# ============================================================

VOLUME_CONFIRMED = "confirmed"
VOLUME_PARTIAL = "partial"
VOLUME_UNAVAILABLE = "unavailable"
VOLUME_INVALID = "invalid"


def get_volume_status(
    series: Optional[pd.Series],
) -> str:
    """
    Détermine la qualité du volume.

    confirmed :
        >= 80 % de valeurs disponibles et aucune valeur négative.

    partial :
        volume présent mais couverture < 80 %.

    unavailable :
        aucune donnée de volume.

    invalid :
        au moins une valeur négative.
    """

    if series is None:
        return VOLUME_UNAVAILABLE

    s = pd.to_numeric(
        series,
        errors="coerce",
    )

    if s.notna().sum() == 0:
        return VOLUME_UNAVAILABLE

    valid_values = s.dropna()

    if (
        not valid_values.empty
        and (valid_values < 0).any()
    ):
        return VOLUME_INVALID

    coverage = float(
        s.notna().mean()
    )

    if coverage >= VOLUME_MIN_COVERAGE:
        return VOLUME_CONFIRMED

    if coverage > 0:
        return VOLUME_PARTIAL

    return VOLUME_UNAVAILABLE


# ============================================================
# TIMESTAMP
# ============================================================

def _normalize_utc_ns(
    values: Any,
) -> pd.Series:
    """
    Force les timestamps en datetime64[ns, UTC].

    Important avec Pandas 3.x :
    pd.to_datetime() peut conserver une résolution
    différente selon la source (ms/us/ns).
    """

    s = pd.to_datetime(
        values,
        utc=True,
        errors="coerce",
    )

    try:
        s = s.astype(
            "datetime64[ns, UTC]"
        )
    except (
        TypeError,
        ValueError,
    ):
        s = pd.Series(s).dt.as_unit(
            "ns"
        )

    return pd.Series(s)


def _normalize_datetime_index(
    index: pd.Index,
) -> pd.DatetimeIndex:
    """
    Normalise un DatetimeIndex en datetime64[ns, UTC].
    """

    idx = pd.DatetimeIndex(
        pd.to_datetime(
            index,
            utc=True,
            errors="coerce",
        )
    )

    try:
        idx = idx.astype(
            "datetime64[ns, UTC]"
        )
    except (
        TypeError,
        ValueError,
    ):
        idx = idx.as_unit("ns")

    return idx


# ============================================================
# UTILITAIRES NUMÉRIQUES
# ============================================================

def _safe_float(
    value: Any,
) -> Optional[float]:

    try:

        if value is None:
            return None

        value = float(value)

        if not np.isfinite(value):
            return None

        return value

    except (
        TypeError,
        ValueError,
    ):
        return None


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    close: pd.Series,
    period: int = RSI_PERIOD,
) -> pd.Series:
    """
    RSI de Wilder.
    """

    delta = close.diff()

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

    rs = avg_gain / avg_loss

    rsi = 100 - (
        100 / (1 + rs)
    )

    # Cas où les pertes sont nulles.
    rsi = rsi.where(
        avg_loss != 0,
        100.0,
    )

    # Cas où gains et pertes sont tous deux nuls.
    flat = (
        (avg_gain == 0)
        & (avg_loss == 0)
    )

    rsi = rsi.where(
        ~flat,
        50.0,
    )

    return rsi


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = ATR_PERIOD,
) -> pd.Series:
    """
    Average True Range de Wilder.
    """

    previous_close = close.shift(1)

    tr1 = (
        high - low
    )

    tr2 = (
        high
        - previous_close
    ).abs()

    tr3 = (
        low
        - previous_close
    ).abs()

    true_range = pd.concat(
        [
            tr1,
            tr2,
            tr3,
        ],
        axis=1,
    ).max(axis=1)

    atr = true_range.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    return atr


# ============================================================
# TENDANCE 1H
# ============================================================

def calculate_trend_1h(
    df: pd.DataFrame,
) -> pd.Series:
    """
    Calcule la tendance sur timeframe 1H.

    Important :
    la tendance d'une heure n'est disponible qu'après
    la clôture de cette heure.

    On utilise donc :

        available_from = hour + 1H

    puis merge_asof backward.

    Cela évite le look-ahead bias.

    Tous les timestamps sont explicitement normalisés
    en datetime64[ns, UTC] avant merge_asof().
    """

    if df.empty:
        return pd.Series(
            index=df.index,
            dtype="float64",
        )

    main = df[
        [
            "open_time",
            "close",
        ]
    ].copy()

    # --------------------------------------------------------
    # NORMALISATION CRITIQUE
    # --------------------------------------------------------

    main["open_time"] = (
        _normalize_utc_ns(
            main["open_time"]
        )
    )

    main = main.dropna(
        subset=["open_time"]
    )

    main = (
        main
        .sort_values("open_time")
        .reset_index(drop=True)
    )

    # Double sécurité : pandas peut reconstruire un type
    # différent après certaines opérations.
    main["open_time"] = (
        _normalize_utc_ns(
            main["open_time"]
        )
    )

    # --------------------------------------------------------
    # RESAMPLE HORAIRE
    # --------------------------------------------------------

    hourly = (
        main
        .set_index("open_time")["close"]
        .resample("1h")
        .last()
        .dropna()
        .to_frame("close")
    )

    if hourly.empty:
        return pd.Series(
            np.nan,
            index=df.index,
            dtype="float64",
        )

    # Normalisation explicite de l'index.
    hourly.index = (
        _normalize_datetime_index(
            hourly.index
        )
    )

    # --------------------------------------------------------
    # EMA 20 / EMA 50
    # --------------------------------------------------------

    hourly["ema20"] = (
        hourly["close"]
        .ewm(
            span=EMA_FAST,
            adjust=False,
            min_periods=EMA_FAST,
        )
        .mean()
    )

    hourly["ema50"] = (
        hourly["close"]
        .ewm(
            span=EMA_SLOW,
            adjust=False,
            min_periods=EMA_SLOW,
        )
        .mean()
    )

    hourly["trend"] = 0.0

    long_mask = (
        hourly["ema20"]
        > hourly["ema50"]
    )

    short_mask = (
        hourly["ema20"]
        < hourly["ema50"]
    )

    hourly.loc[
        long_mask,
        "trend",
    ] = 1.0

    hourly.loc[
        short_mask,
        "trend",
    ] = -1.0

    # --------------------------------------------------------
    # DISPONIBILITÉ
    # --------------------------------------------------------

    hourly["available_from"] = (
        hourly.index
        + pd.Timedelta(
            hours=1
        )
    )

    # CRITIQUE :
    # force aussi cette colonne en ns UTC.
    hourly["available_from"] = (
        _normalize_utc_ns(
            hourly["available_from"]
        )
    )

    # --------------------------------------------------------
    # DATAFRAME POUR MERGE_ASOF
    # --------------------------------------------------------

    trend_df = (
        hourly[
            [
                "available_from",
                "trend",
            ]
        ]
        .dropna(
            subset=["available_from"]
        )
        .sort_values(
            "available_from"
        )
        .reset_index(drop=True)
    )

    trend_df["available_from"] = (
        _normalize_utc_ns(
            trend_df["available_from"]
        )
    )

    # Le DataFrame principal du merge.
    main_merge = (
        main[
            [
                "open_time",
            ]
        ]
        .copy()
    )

    main_merge["open_time"] = (
        _normalize_utc_ns(
            main_merge["open_time"]
        )
    )

    main_merge = (
        main_merge
        .sort_values("open_time")
        .reset_index(drop=True)
    )

    # --------------------------------------------------------
    # ASSERTIONS DE TYPE
    # --------------------------------------------------------

    if str(
        main_merge["open_time"].dtype
    ) != "datetime64[ns, UTC]":
        main_merge["open_time"] = (
            main_merge["open_time"]
            .astype(
                "datetime64[ns, UTC]"
            )
        )

    if str(
        trend_df["available_from"].dtype
    ) != "datetime64[ns, UTC]":
        trend_df["available_from"] = (
            trend_df["available_from"]
            .astype(
                "datetime64[ns, UTC]"
            )
        )

    # --------------------------------------------------------
    # MERGE ASOF
    # --------------------------------------------------------

    merged = pd.merge_asof(
        main_merge,
        trend_df,
        left_on="open_time",
        right_on="available_from",
        direction="backward",
        allow_exact_matches=True,
    )

    # --------------------------------------------------------
    # RETOUR DANS L'ORDRE DU DATAFRAME ORIGINAL
    # --------------------------------------------------------

    # On recalcule la tendance par timestamp afin de ne pas
    # dépendre d'un index numérique éventuellement modifié.
    trend_by_time = (
        merged[
            [
                "open_time",
                "trend",
            ]
        ]
        .drop_duplicates(
            subset=["open_time"],
            keep="last",
        )
    )

    result = pd.DataFrame(
        {
            "open_time": _normalize_utc_ns(
                df["open_time"]
            ),
        }
    )

    result = result.merge(
        trend_by_time,
        on="open_time",
        how="left",
    )

    return pd.Series(
        result["trend"].to_numpy(),
        index=df.index,
        dtype="float64",
    )


# ============================================================
# VOLUME FEATURES
# ============================================================

def calculate_volume_features(
    volume: pd.Series,
    lookback: int = VOLUME_LOOKBACK,
) -> pd.DataFrame:

    v = pd.to_numeric(
        volume,
        errors="coerce",
    )

    previous_mean = (
        v.shift(1)
        .rolling(
            lookback,
            min_periods=lookback,
        )
        .mean()
    )

    relvol = (
        v / previous_mean
    )

    available = v.notna()

    invalid = (
        v < 0
    )

    return pd.DataFrame(
        {
            "volume_mean": previous_mean,
            "rel_volume": relvol,
            "volume_available": available,
            "volume_invalid": invalid,
        },
        index=volume.index,
    )


# ============================================================
# INDICATORS PRINCIPAL
# ============================================================

def indicators(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Calcule tous les indicateurs nécessaires au scanner.
    """

    if df is None:
        raise ValueError(
            "DataFrame absent"
        )

    if df.empty:
        raise ValueError(
            "DataFrame vide"
        )

    required = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
    ]

    missing = [
        col
        for col in required
        if col not in df.columns
    ]

    if missing:
        raise ValueError(
            f"Colonnes manquantes : {missing}"
        )

    data = df.copy()

    if "volume" not in data.columns:
        data["volume"] = np.nan

    # --------------------------------------------------------
    # TIMESTAMP
    # --------------------------------------------------------

    data["open_time"] = (
        _normalize_utc_ns(
            data["open_time"]
        )
    )

    data = data.dropna(
        subset=["open_time"]
    )

    # --------------------------------------------------------
    # NUMÉRIQUE
    # --------------------------------------------------------

    for col in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:
        data[col] = pd.to_numeric(
            data[col],
            errors="coerce",
        )

    data = data.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    )

    # --------------------------------------------------------
    # OHLC VALIDATION
    # --------------------------------------------------------

    invalid = (
        (data["high"] < data["low"])
        | (data["high"] < data["open"])
        | (data["high"] < data["close"])
        | (data["low"] > data["open"])
        | (data["low"] > data["close"])
        | (data["open"] <= 0)
        | (data["high"] <= 0)
        | (data["low"] <= 0)
        | (data["close"] <= 0)
    )

    data = data.loc[
        ~invalid
    ].copy()

    # --------------------------------------------------------
    # TRI / DEDUP
    # --------------------------------------------------------

    data = (
        data
        .sort_values("open_time")
        .drop_duplicates(
            subset=["open_time"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    data["open_time"] = (
        _normalize_utc_ns(
            data["open_time"]
        )
    )

    if len(data) < 2:
        raise ValueError(
            "Pas assez de données"
        )

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    data["ema20"] = (
        data["close"]
        .ewm(
            span=EMA_FAST,
            adjust=False,
            min_periods=EMA_FAST,
        )
        .mean()
    )

    data["ema50"] = (
        data["close"]
        .ewm(
            span=EMA_SLOW,
            adjust=False,
            min_periods=EMA_SLOW,
        )
        .mean()
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    data["rsi"] = calculate_rsi(
        data["close"],
        RSI_PERIOD,
    )

    # --------------------------------------------------------
    # ATR
    # --------------------------------------------------------

    data["atr"] = calculate_atr(
        data["high"],
        data["low"],
        data["close"],
        ATR_PERIOD,
    )

    # --------------------------------------------------------
    # TREND 1H
    # --------------------------------------------------------

    data["trend_1h"] = (
        calculate_trend_1h(
            data
        )
        .to_numpy()
    )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    volume_features = (
        calculate_volume_features(
            data["volume"]
        )
    )

    for col in volume_features.columns:
        data[col] = (
            volume_features[col]
            .to_numpy()
        )

    volume_status = get_volume_status(
        data["volume"]
    )

    # --------------------------------------------------------
    # BREAKOUT
    # --------------------------------------------------------

    data["previous_high"] = (
        data["high"]
        .rolling(
            BREAKOUT_LOOKBACK,
            min_periods=BREAKOUT_LOOKBACK,
        )
        .max()
        .shift(1)
    )

    data["previous_low"] = (
        data["low"]
        .rolling(
            BREAKOUT_LOOKBACK,
            min_periods=BREAKOUT_LOOKBACK,
        )
        .min()
        .shift(1)
    )

    data["breakout_long"] = (
        data["close"]
        > (
            data["previous_high"]
            + (
                data["atr"]
                * BREAKOUT_ATR_MULT
            )
        )
    )

    data["breakout_short"] = (
        data["close"]
        < (
            data["previous_low"]
            - (
                data["atr"]
                * BREAKOUT_ATR_MULT
            )
        )
    )

    # Breakout fort.
    data["breakout_long_full"] = (
        data["close"]
        > (
            data["previous_high"]
            + (
                data["atr"]
                * BREAKOUT_FULL_ATR
            )
        )
    )

    data["breakout_short_full"] = (
        data["close"]
        < (
            data["previous_low"]
            - (
                data["atr"]
                * BREAKOUT_FULL_ATR
            )
        )
    )

    # --------------------------------------------------------
    # METADATA
    # --------------------------------------------------------

    data.attrs["volume_status"] = (
        volume_status
    )

    data.attrs["volume_coverage"] = float(
        data["volume"].notna().mean()
    )

    data.attrs["volume_invalid"] = bool(
        (
            data["volume"]
            .dropna()
            < 0
        ).any()
    )

    return data


# ============================================================
# SCORE HELPERS
# ============================================================

def _trend_score(
    row: pd.Series,
    side: str,
) -> float:

    trend = _safe_float(
        row.get("trend_1h")
    )

    if trend is None:
        return 0.0

    if side == "LONG":
        return (
            SCORE_WEIGHTS["trend"]
            if trend > 0
            else 0.0
        )

    if side == "SHORT":
        return (
            SCORE_WEIGHTS["trend"]
            if trend < 0
            else 0.0
        )

    return 0.0


def _ema_score(
    row: pd.Series,
    side: str,
) -> float:

    close = _safe_float(
        row.get("close")
    )

    ema20 = _safe_float(
        row.get("ema20")
    )

    if (
        close is None
        or ema20 is None
    ):
        return 0.0

    if side == "LONG" and close > ema20:
        return SCORE_WEIGHTS["ema20"]

    if side == "SHORT" and close < ema20:
        return SCORE_WEIGHTS["ema20"]

    return 0.0


def _rsi_score(
    row: pd.Series,
    side: str,
) -> float:

    rsi = _safe_float(
        row.get("rsi")
    )

    if rsi is None:
        return 0.0

    # Évite d'acheter une situation extrêmement
    # surachetée ou de shorter une situation extrêmement
    # survendue.
    if side == "LONG":

        if 50 <= rsi <= 70:
            return SCORE_WEIGHTS["rsi"]

        if 45 <= rsi < 50:
            return SCORE_WEIGHTS["rsi"] * 0.50

        return 0.0

    if side == "SHORT":

        if 30 <= rsi <= 50:
            return SCORE_WEIGHTS["rsi"]

        if 50 < rsi <= 55:
            return SCORE_WEIGHTS["rsi"] * 0.50

        return 0.0

    return 0.0


def _volume_score(
    row: pd.Series,
    volume_status: str,
    side: str,
) -> float:

    if volume_status == VOLUME_INVALID:
        return 0.0

    if volume_status == VOLUME_UNAVAILABLE:
        return 0.0

    relvol = _safe_float(
        row.get("rel_volume")
    )

    if relvol is None:
        return 0.0

    if relvol >= 1.50:
        return SCORE_WEIGHTS["volume"]

    if relvol >= 1.20:
        return SCORE_WEIGHTS["volume"] * 0.75

    if relvol >= 1.00:
        return SCORE_WEIGHTS["volume"] * 0.50

    return 0.0


def _breakout_score(
    row: pd.Series,
    side: str,
) -> float:

    if side == "LONG":

        if bool(
            row.get(
                "breakout_long_full",
                False,
            )
        ):
            return SCORE_WEIGHTS[
                "breakout"
            ]

        if bool(
            row.get(
                "breakout_long",
                False,
            )
        ):
            return SCORE_WEIGHTS[
                "breakout"
            ] * 0.75

    if side == "SHORT":

        if bool(
            row.get(
                "breakout_short_full",
                False,
            )
        ):
            return SCORE_WEIGHTS[
                "breakout"
            ]

        if bool(
            row.get(
                "breakout_short",
                False,
            )
        ):
            return SCORE_WEIGHTS[
                "breakout"
            ] * 0.75

    return 0.0


# ============================================================
# SCORE PRINCIPAL
# ============================================================

def score(
    row: pd.Series,
    return_details: bool = False,
) -> Any:
    """
    Calcule le meilleur score LONG / SHORT.

    Le score est normalisé à 100 même lorsque le volume
    n'est pas disponible.

    Exemple :
    score brut maximal sans volume = 85.
    On le ramène à 100 afin de ne pas pénaliser
    structurellement les marchés sans volume exploitable.
    """

    if row is None:
        raise ValueError(
            "Row absente"
        )

    # --------------------------------------------------------
    # Volume status
    # --------------------------------------------------------

    volume_status = row.get(
        "volume_status"
    )

    if not volume_status:
        volume_status = (
            get_volume_status(
                pd.Series(
                    [row.get("volume")]
                )
            )
        )

    # --------------------------------------------------------
    # Score LONG
    # --------------------------------------------------------

    long_details = {
        "trend": _trend_score(
            row,
            "LONG",
        ),
        "ema20": _ema_score(
            row,
            "LONG",
        ),
        "rsi": _rsi_score(
            row,
            "LONG",
        ),
        "volume": _volume_score(
            row,
            volume_status,
            "LONG",
        ),
        "breakout": _breakout_score(
            row,
            "LONG",
        ),
    }

    # --------------------------------------------------------
    # Score SHORT
    # --------------------------------------------------------

    short_details = {
        "trend": _trend_score(
            row,
            "SHORT",
        ),
        "ema20": _ema_score(
            row,
            "SHORT",
        ),
        "rsi": _rsi_score(
            row,
            "SHORT",
        ),
        "volume": _volume_score(
            row,
            volume_status,
            "SHORT",
        ),
        "breakout": _breakout_score(
            row,
            "SHORT",
        ),
    }

    long_raw = sum(
        long_details.values()
    )

    short_raw = sum(
        short_details.values()
    )

    # --------------------------------------------------------
    # Score maximum effectif
    # --------------------------------------------------------

    volume_available = (
        volume_status
        in {
            VOLUME_CONFIRMED,
            VOLUME_PARTIAL,
        }
    )

    if volume_available:
        effective_max = float(
            MAX_SCORE
        )
    else:
        effective_max = float(
            MAX_SCORE
            - SCORE_WEIGHTS["volume"]
        )

    if effective_max <= 0:
        effective_max = float(
            MAX_SCORE
        )

    long_score = (
        long_raw
        / effective_max
        * 100.0
    )

    short_score = (
        short_raw
        / effective_max
        * 100.0
    )

    if long_score >= short_score:
        best_side = "LONG"
        best_score = long_score
        best_details = long_details
    else:
        best_side = "SHORT"
        best_score = short_score
        best_details = short_details

    # --------------------------------------------------------
    # RR
    # --------------------------------------------------------

    close = _safe_float(
        row.get("close")
    )

    atr = _safe_float(
        row.get("atr")
    )

    sl = None
    tp1 = None
    tp2 = None
    rr = None

    if (
        close is not None
        and atr is not None
        and atr > 0
    ):

        if best_side == "LONG":

            sl = (
                close
                - (
                    SL_ATR_MULT
                    * atr
                )
            )

            tp1 = (
                close
                + (
                    TP1_ATR_MULT
                    * atr
                )
            )

            tp2 = (
                close
                + (
                    TP2_ATR_MULT
                    * atr
                )
            )

        else:

            sl = (
                close
                + (
                    SL_ATR_MULT
                    * atr
                )
            )

            tp1 = (
                close
                - (
                    TP1_ATR_MULT
                    * atr
                )
            )

            tp2 = (
                close
                - (
                    TP2_ATR_MULT
                    * atr
                )
            )

        risk = abs(
            close - sl
        )

        reward = abs(
            tp2 - close
        )

        if risk > 0:
            rr = (
                reward / risk
            )

    # --------------------------------------------------------
    # RESULTAT DETAILLE
    # --------------------------------------------------------

    details = {
        "score": float(
            best_score
        ),
        "best_score": float(
            best_score
        ),
        "side": best_side,
        "direction": best_side,

        "long_score": float(
            long_score
        ),
        "short_score": float(
            short_score
        ),

        "long_raw": float(
            long_raw
        ),
        "short_raw": float(
            short_raw
        ),

        "long_details": long_details,
        "short_details": short_details,

        "volume_status": (
            volume_status
        ),

        "volume_coverage": row.get(
            "volume_coverage"
        ),

        "volume_invalid": row.get(
            "volume_invalid",
            False,
        ),

        "effective_max": (
            effective_max
        ),

        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr": rr,
        "risk_reward": rr,
        "rr_tp2": rr,

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
    }

    if return_details:
        return details

    return float(
        best_score
    )


# ============================================================
# SIGNAL QUALITY
# ============================================================

def signal_quality(
    score_value: float,
) -> str:

    score_value = _safe_float(
        score_value
    )

    if score_value is None:
        return "D"

    if score_value >= 90:
        return "A"

    if score_value >= 80:
        return "B"

    if score_value >= 75:
        return "C"

    return "D"


# ============================================================
# BACKTEST
# ============================================================

def run(
    df: pd.DataFrame,
    threshold: float = 75.0,
    fee_rate: float = DEFAULT_FEE_RATE,
    slippage: float = DEFAULT_SLIPPAGE,
    max_holding_bars: int = MAX_HOLDING_BARS,
) -> pd.DataFrame:
    """
    Backtest simple des signaux.

    Entrée :
        prochaine bougie après le signal.

    Sortie :
        SL, TP2 ou durée maximale.

    Les frais et le slippage sont intégrés.
    """

    if df is None or df.empty:
        return pd.DataFrame()

    data = indicators(
        df
    )

    trades = []

    for i in range(
        len(data) - 1
    ):

        row = data.iloc[i]

        # ----------------------------------------------------
        # SCORE
        # ----------------------------------------------------

        details = score(
            row,
            return_details=True,
        )

        score_value = float(
            details["score"]
        )

        if score_value < threshold:
            continue

        side = details[
            "side"
        ]

        if side == "LONG":

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

        if not breakout:
            continue

        rr = details.get(
            "rr"
        )

        if (
            rr is None
            or rr < MIN_RR
        ):
            continue

        # ----------------------------------------------------
        # ENTREE PROCHAINE BOUGIE
        # ----------------------------------------------------

        entry_row = data.iloc[
            i + 1
        ]

        raw_entry = _safe_float(
            entry_row["open"]
        )

        if raw_entry is None:
            continue

        # Slippage défavorable.
        if side == "LONG":

            entry_price = (
                raw_entry
                * (
                    1
                    + slippage
                )
            )

        else:

            entry_price = (
                raw_entry
                * (
                    1
                    - slippage
                )
            )

        atr = _safe_float(
            row.get("atr")
        )

        if atr is None or atr <= 0:
            continue

        # ----------------------------------------------------
        # NIVEAUX
        # ----------------------------------------------------

        if side == "LONG":

            sl = (
                entry_price
                - (
                    SL_ATR_MULT
                    * atr
                )
            )

            tp1 = (
                entry_price
                + (
                    TP1_ATR_MULT
                    * atr
                )
            )

            tp2 = (
                entry_price
                + (
                    TP2_ATR_MULT
                    * atr
                )
            )

        else:

            sl = (
                entry_price
                + (
                    SL_ATR_MULT
                    * atr
                )
            )

            tp1 = (
                entry_price
                - (
                    TP1_ATR_MULT
                    * atr
                )
            )

            tp2 = (
                entry_price
                - (
                    TP2_ATR_MULT
                    * atr
                )
            )

        # ----------------------------------------------------
        # RR
        # ----------------------------------------------------

        risk = abs(
            entry_price - sl
        )

        reward = abs(
            tp2 - entry_price
        )

        if risk <= 0:
            continue

        rr = (
            reward / risk
        )

        if rr < MIN_RR:
            continue

        # ----------------------------------------------------
        # SUIVI
        # ----------------------------------------------------

        exit_price = None
        exit_reason = None
        exit_index = None

        last_index = min(
            len(data) - 1,
            i
            + 1
            + max_holding_bars,
        )

        for j in range(
            i + 1,
            last_index + 1,
        ):

            candle = data.iloc[
                j
            ]

            high = _safe_float(
                candle["high"]
            )

            low = _safe_float(
                candle["low"]
            )

            if (
                high is None
                or low is None
            ):
                continue

            if side == "LONG":

                hit_sl = (
                    low <= sl
                )

                hit_tp2 = (
                    high >= tp2
                )

                # Si SL et TP sont touchés dans
                # la même bougie, on choisit
                # le scénario conservateur.
                if (
                    hit_sl
                    and hit_tp2
                ):
                    exit_price = sl
                    exit_reason = "SL"
                    exit_index = j
                    break

                if hit_sl:

                    exit_price = sl
                    exit_reason = "SL"
                    exit_index = j
                    break

                if hit_tp2:

                    exit_price = tp2
                    exit_reason = "TP2"
                    exit_index = j
                    break

            else:

                hit_sl = (
                    high >= sl
                )

                hit_tp2 = (
                    low <= tp2
                )

                if (
                    hit_sl
                    and hit_tp2
                ):
                    exit_price = sl
                    exit_reason = "SL"
                    exit_index = j
                    break

                if hit_sl:

                    exit_price = sl
                    exit_reason = "SL"
                    exit_index = j
                    break

                if hit_tp2:

                    exit_price = tp2
                    exit_reason = "TP2"
                    exit_index = j
                    break

        # ----------------------------------------------------
        # SORTIE MAX HOLDING
        # ----------------------------------------------------

        if exit_price is None:

            exit_index = last_index

            exit_price = _safe_float(
                data.iloc[
                    exit_index
                ]["close"]
            )

            exit_reason = (
                "TIMEOUT"
            )

        if exit_price is None:
            continue

        # ----------------------------------------------------
        # SLIPPAGE SORTIE
        # ----------------------------------------------------

        if side == "LONG":

            adjusted_exit = (
                exit_price
                * (
                    1
                    - slippage
                )
            )

        else:

            adjusted_exit = (
                exit_price
                * (
                    1
                    + slippage
                )
            )

        # ----------------------------------------------------
        # RENDEMENT BRUT
        # ----------------------------------------------------

        if side == "LONG":

            gross_return = (
                adjusted_exit
                / entry_price
                - 1
            )

        else:

            gross_return = (
                entry_price
                / adjusted_exit
                - 1
            )

        # Deux côtés de la transaction.
        total_cost = (
            2
            * fee_rate
        )

        net_return = (
            gross_return
            - total_cost
        )

        # ----------------------------------------------------
        # TRADE
        # ----------------------------------------------------

        entry_time = (
            data.iloc[
                i + 1
            ]["open_time"]
        )

        exit_time = (
            data.iloc[
                exit_index
            ]["open_time"]
        )

        trades.append(
            {
                "signal_time": row[
                    "open_time"
                ],

                "entry_time": entry_time,

                "exit_time": exit_time,

                "side": side,

                "score": score_value,

                "quality": signal_quality(
                    score_value
                ),

                "entry": entry_price,

                "sl": sl,

                "tp1": tp1,

                "tp2": tp2,

                "exit": adjusted_exit,

                "rr": rr,

                "exit_reason": exit_reason,

                "holding_bars": (
                    exit_index
                    - (
                        i + 1
                    )
                ),

                "gross_return": (
                    gross_return
                ),

                "net_return": (
                    net_return
                ),

                "volume_status": (
                    details.get(
                        "volume_status"
                    )
                ),

                "volume_coverage": (
                    details.get(
                        "volume_coverage"
                    )
                ),
            }
        )

    if not trades:
        return pd.DataFrame()

    result = pd.DataFrame(
        trades
    )

    # Timestamps à nouveau normalisés.
    for col in [
        "signal_time",
        "entry_time",
        "exit_time",
    ]:
        result[col] = (
            _normalize_utc_ns(
                result[col]
            )
        )

    return result


# ============================================================
# BACKTEST STATISTICS
# ============================================================

def backtest_statistics(
    trades: pd.DataFrame,
) -> Dict[str, Any]:

    if (
        trades is None
        or trades.empty
    ):
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "avg_return": 0.0,
            "total_return": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
        }

    data = trades.copy()

    returns = pd.to_numeric(
        data["net_return"],
        errors="coerce",
    ).fillna(0.0)

    wins = int(
        (
            returns > 0
        ).sum()
    )

    losses = int(
        (
            returns <= 0
        ).sum()
    )

    total_trades = len(
        returns
    )

    win_rate = (
        wins
        / total_trades
        * 100.0
    )

    avg_return = float(
        returns.mean()
    )

    # Rendement composé.
    total_return = float(
        (
            1
            + returns
        )
        .prod()
        - 1
    )

    gross_profit = float(
        returns[
            returns > 0
        ].sum()
    )

    gross_loss = abs(
        float(
            returns[
                returns < 0
            ].sum()
        )
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

    # --------------------------------------------------------
    # Drawdown
    # --------------------------------------------------------

    equity = (
        1.0
        + returns
    ).cumprod()

    running_max = (
        equity.cummax()
    )

    drawdown = (
        equity
        / running_max
        - 1.0
    )

    max_drawdown = float(
        drawdown.min()
    )

    # --------------------------------------------------------
    # TP / SL
    # --------------------------------------------------------

    tp2_count = int(
        (
            data["exit_reason"]
            == "TP2"
        ).sum()
    )

    sl_count = int(
        (
            data["exit_reason"]
            == "SL"
        ).sum()
    )

    timeout_count = int(
        (
            data["exit_reason"]
            == "TIMEOUT"
        ).sum()
    )

    return {
        "trades": total_trades,

        "wins": wins,

        "losses": losses,

        "win_rate": win_rate,

        "avg_return": avg_return,

        "total_return": total_return,

        "profit_factor": profit_factor,

        "max_drawdown": max_drawdown,

        "tp2": tp2_count,

        "sl": sl_count,

        "timeout": timeout_count,
    }


# ============================================================
# ALIAS COMPATIBILITÉ
# ============================================================

def calculate_indicators(
    df: pd.DataFrame,
) -> pd.DataFrame:

    return indicators(
        df
    )


def run_backtest(
    df: pd.DataFrame,
    threshold: float = 75.0,
    fee_rate: float = DEFAULT_FEE_RATE,
    slippage: float = DEFAULT_SLIPPAGE,
    max_holding_bars: int = MAX_HOLDING_BARS,
) -> pd.DataFrame:

    return run(
        df=df,
        threshold=threshold,
        fee_rate=fee_rate,
        slippage=slippage,
        max_holding_bars=max_holding_bars,
    )
