"""
V4.1 — Scanner technique.

Sortie principale :

    symbol
    close
    score_long
    score_short
    direction
    best_score
    status

    entry
    stop_loss
    take_profit_1
    take_profit_2

    rr_tp1
    rr_tp2
    risk

    quality

    ATR
    RSI
    RelVol
    trend1h

    volume_available
    volume_ok
    breakout_ok
"""

import math
import time

import pandas as pd

from backtest import (
    indicators,
    score,
    best_direction,
    calculate_trade_levels,
    signal_quality,
    BREAKOUT_LOOKBACK,
    BREAKOUT_ATR_MULT,
    MIN_RR,
)


# ======================================================================
# CONFIGURATION
# ======================================================================

DEFAULT_THRESHOLD = 75

VOLUME_THRESHOLD = 1.5


# ======================================================================
# OUTILS
# ======================================================================

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


def format_relvol(
    value,
):
    if pd.isna(value):
        return "n/d"

    return f"{float(value):.2f}"


# ======================================================================
# SCAN
# ======================================================================

def scan(
    symbols,
    fetcher,
    interval="15m",
    limit=1000,
    threshold=DEFAULT_THRESHOLD,
    pause=0.0,
    fallback_fetcher=None,
    provider_name="unknown",
    fallback_provider_name=None,
):
    results = []

    for symbol in symbols:

        df = None
        used_provider = provider_name

        try:

            # ==========================================================
            # DONNÉES — SOURCE PRINCIPALE
            # ==========================================================

            try:

                df = fetcher(
                    symbol,
                    interval=interval,
                    limit=limit,
                )

            except Exception as primary_error:

                print(
                    f"{symbol}: "
                    f"échec source principale "
                    f"{provider_name}: "
                    f"{primary_error}"
                )

                # ------------------------------------------------------
                # FALLBACK
                # ------------------------------------------------------

                if fallback_fetcher is None:

                    raise

                print(
                    f"{symbol}: "
                    f"fallback → "
                    f"{fallback_provider_name}"
                )

                df = fallback_fetcher(
                    symbol,
                    interval=interval,
                    limit=limit,
                )

                used_provider = (
                    fallback_provider_name
                    or "fallback"
                )

            # ==========================================================
            # VALIDATION
            # ==========================================================

            if df is None or df.empty:

                print(
                    f"{symbol}: aucune donnée"
                )

                continue

            df = df.copy()

            required = [
                "open_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]

            missing_columns = [
                col
                for col in required
                if col not in df.columns
            ]

            if missing_columns:

                print(
                    f"{symbol}: colonnes "
                    f"absentes "
                    f"{missing_columns}"
                )

                continue

            # ==========================================================
            # TYPES
            # ==========================================================

            df["open_time"] = (
                pd.to_datetime(
                    df["open_time"],
                    utc=True,
                    errors="coerce",
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

            df = (
                df.dropna(
                    subset=[
                        "open_time",
                        "open",
                        "high",
                        "low",
                        "close",
                    ]
                )
                .sort_values("open_time")
                .drop_duplicates(
                    subset=["open_time"],
                    keep="last",
                )
                .reset_index(drop=True)
            )

            # ==========================================================
            # MINIMUM DE DONNÉES
            # ==========================================================

            if len(df) < 120:

                print(
                    f"{symbol}: données "
                    f"insuffisantes "
                    f"({len(df)} bougies)"
                )

                continue

            # ==========================================================
            # INDICATEURS
            # ==========================================================

            d = indicators(
                df
            )

            # ==========================================================
            # BREAKOUT ATR
            # ==========================================================

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
                    * BREAKOUT_ATR_MULT
                )
            )

            d["breakout_short"] = (
                d["close"]
                <
                (
                    d["prev_low"]
                    -
                    d["atr"]
                    * BREAKOUT_ATR_MULT
                )
            )

            # ==========================================================
            # DERNIÈRE BOUGIE CLÔTURÉE
            # ==========================================================

            last = d.iloc[-1]

            (
                score_long,
                score_short,
                details,
            ) = score(
                last,
                return_details=True,
            )

            # ==========================================================
            # DIRECTION
            # ==========================================================

            direction = best_direction(
                score_long,
                score_short,
            )

            best_score = max(
                score_long,
                score_short,
            )

            # ==========================================================
            # CONDITIONS DU CÔTÉ RETENU
            # ==========================================================

            if direction == "LONG":

                breakout_ok = bool(
                    details[
                        "breakout_long"
                    ]
                )

                selected = details[
                    "long"
                ]

            elif direction == "SHORT":

                breakout_ok = bool(
                    details[
                        "breakout_short"
                    ]
                )

                selected = details[
                    "short"
                ]

            else:

                breakout_ok = False

                selected = {
                    "trend": 0.0,
                    "ema": 0.0,
                    "rsi": 0.0,
                    "volume": 0.0,
                    "breakout": 0.0,
                }

            # ==========================================================
            # VOLUME
            # ==========================================================

            volume_available = bool(
                details[
                    "volume_available"
                ]
            )

            volume_ok = bool(
                details[
                    "volume_ok"
                ]
            )

            volume_trigger_ok = (
                volume_ok
                or not volume_available
            )

            # ==========================================================
            # NIVEAUX DE TRADE
            # ==========================================================

            levels = calculate_trade_levels(
                last["close"],
                last["atr"],
                direction,
            )

            # ==========================================================
            # QUALITÉ
            # ==========================================================

            quality = signal_quality(
                best_score,
                breakout_ok,
                volume_ok,
                volume_available,
                levels["rr_tp2"],
            )

            # ==========================================================
            # STATUT
            # ==========================================================

            if (
                best_score >= threshold
                and breakout_ok
                and volume_trigger_ok
                and pd.notna(
                    levels["rr_tp2"]
                )
                and levels["rr_tp2"] >= MIN_RR
            ):

                status = "SIGNAL FORT"

            elif best_score >= threshold:

                status = "ATTENTE"

            else:

                status = "SOUS SEUIL"

            # ==========================================================
            # CONDITIONS MANQUANTES
            # ==========================================================

            missing = []

            if best_score < threshold:

                missing.append(
                    f"SCORE < {threshold:.0f}"
                )

            if not breakout_ok:

                missing.append(
                    "BREAKOUT"
                )

            if (
                volume_available
                and not volume_ok
            ):

                missing.append(
                    "VOLUME"
                )

            if (
                pd.isna(
                    levels["rr_tp2"]
                )
                or levels["rr_tp2"] < MIN_RR
            ):

                missing.append(
                    "R:R"
                )

            if missing:

                missing_text = (
                    " + ".join(
                        missing
                    )
                )

            else:

                missing_text = "aucune"

            # ==========================================================
            # FRAÎCHEUR
            # ==========================================================

            last_candle = last[
                "open_time"
            ]

            now = pd.Timestamp.now(
                tz="UTC"
            )

            age_minutes = (
                (
                    now
                    - last_candle
                )
                .total_seconds()
                / 60
            )

            # ==========================================================
            # RESULTAT
            # ==========================================================

            results.append(
                {
                    "symbol": symbol,

                    "provider": used_provider,

                    "close": safe_float(
                        last["close"]
                    ),

                    "score_long": float(
                        score_long
                    ),

                    "score_short": float(
                        score_short
                    ),

                    "direction": direction,

                    "best_score": float(
                        best_score
                    ),

                    "status": status,

                    "quality": quality,

                    "missing": missing_text,

                    # --------------------------------------------------
                    # POINTS
                    # --------------------------------------------------

                    "trend_pts": float(
                        selected["trend"]
                    ),

                    "ema_pts": float(
                        selected["ema"]
                    ),

                    "rsi_pts": float(
                        selected["rsi"]
                    ),

                    "volume_pts": float(
                        selected["volume"]
                    ),

                    "breakout_pts": float(
                        selected["breakout"]
                    ),

                    # --------------------------------------------------
                    # TRADE
                    # --------------------------------------------------

                    "entry": safe_float(
                        levels["entry"]
                    ),

                    "stop_loss": safe_float(
                        levels["stop_loss"]
                    ),

                    "take_profit_1": safe_float(
                        levels["take_profit_1"]
                    ),

                    "take_profit_2": safe_float(
                        levels["take_profit_2"]
                    ),

                    "risk": safe_float(
                        levels["risk"]
                    ),

                    "rr_tp1": safe_float(
                        levels["rr_tp1"]
                    ),

                    "rr_tp2": safe_float(
                        levels["rr_tp2"]
                    ),

                    # --------------------------------------------------
                    # INDICATEURS
                    # --------------------------------------------------

                    "atr": safe_float(
                        last.get(
                            "atr",
                            float("nan"),
                        )
                    ),

                    "rsi": safe_float(
                        last.get(
                            "rsi",
                            float("nan"),
                        )
                    ),

                    "relvol": safe_float(
                        last.get(
                            "relvol",
                            float("nan"),
                        )
                    ),

                    "trend1h": int(
                        last.get(
                            "trend1h",
                            0,
                        )
                    ),

                    # --------------------------------------------------
                    # TRIGGERS
                    # --------------------------------------------------

                    "breakout_ok": bool(
                        breakout_ok
                    ),

                    "volume_ok": bool(
                        volume_ok
                    ),

                    "volume_available": bool(
                        volume_available
                    ),

                    # --------------------------------------------------
                    # DONNÉES
                    # --------------------------------------------------

                    "last_candle": last_candle,

                    "age_minutes": float(
                        age_minutes
                    ),
                }
            )

        except Exception as exc:

            print(
                f"{symbol}: ERREUR : {exc}"
            )

        if pause:

            time.sleep(
                pause
            )

    if not results:

        return pd.DataFrame(
            columns=[
                "symbol",
                "provider",
                "close",
                "score_long",
                "score_short",
                "direction",
                "best_score",
                "status",
                "quality",
                "missing",
                "trend_pts",
                "ema_pts",
                "rsi_pts",
                "volume_pts",
                "breakout_pts",
                "entry",
                "stop_loss",
                "take_profit_1",
                "take_profit_2",
                "risk",
                "rr_tp1",
                "rr_tp2",
                "atr",
                "rsi",
                "relvol",
                "trend1h",
                "breakout_ok",
                "volume_ok",
                "volume_available",
                "last_candle",
                "age_minutes",
            ]
        )

    return (
        pd.DataFrame(
            results
        )
        .sort_values(
            [
                "status",
                "best_score",
                "quality",
            ],
            ascending=[
                True,
                False,
                True,
            ],
        )
        .reset_index(drop=True)
    )
