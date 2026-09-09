import time
import math

import pandas as pd

from backtest import (
    indicators,
    score,
    BREAKOUT_BUFFER,
)


# ======================================================================
# CONFIGURATION V4
# ======================================================================

DEFAULT_THRESHOLD = 75

VOLUME_THRESHOLD = 1.5

BREAKOUT_LOOKBACK = 20


# ======================================================================
# OUTILS
# ======================================================================

def safe_float(
    value,
    default=float("nan")
):
    try:

        value = float(value)

        if math.isnan(value):
            return default

        return value

    except Exception:
        return default


def best_direction(
    score_long,
    score_short
):

    if score_long > score_short:
        return "LONG"

    if score_short > score_long:
        return "SHORT"

    return "-"


def format_relvol(
    value
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
    threshold=75,
    pause=0.0
):

    results = []

    for symbol in symbols:

        try:

            # ----------------------------------------------------------
            # DONNÉES
            # ----------------------------------------------------------

            df = fetcher(
                symbol,
                interval=interval,
                limit=limit
            )

            if df is None or df.empty:

                print(
                    f"{symbol}: aucune donnée"
                )

                if pause:
                    time.sleep(pause)

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
                    f"{symbol}: colonnes absentes "
                    f"{missing_columns}"
                )

                continue

            # ----------------------------------------------------------
            # TYPES
            # ----------------------------------------------------------

            df["open_time"] = pd.to_datetime(
                df["open_time"],
                utc=True,
                errors="coerce"
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
                    errors="coerce"
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

            df = (
                df.sort_values(
                    "open_time"
                )
                .reset_index(
                    drop=True
                )
            )

            if len(df) < 60:

                print(
                    f"{symbol}: données insuffisantes "
                    f"({len(df)} bougies)"
                )

                continue

            # ----------------------------------------------------------
            # INDICATEURS
            # ----------------------------------------------------------

            d = indicators(
                df
            )

            # ----------------------------------------------------------
            # BREAKOUT
            #
            # Le niveau de référence est constitué des
            # 20 bougies précédentes.
            # ----------------------------------------------------------

            d["prev_high"] = (
                d["high"]
                .rolling(
                    BREAKOUT_LOOKBACK
                )
                .max()
                .shift(1)
            )

            d["prev_low"] = (
                d["low"]
                .rolling(
                    BREAKOUT_LOOKBACK
                )
                .min()
                .shift(1)
            )

            d["breakout_long"] = (
                d["close"]
                >
                d["prev_high"]
                * (
                    1 + BREAKOUT_BUFFER
                )
            )

            d["breakout_short"] = (
                d["close"]
                <
                d["prev_low"]
                * (
                    1 - BREAKOUT_BUFFER
                )
            )

            # ----------------------------------------------------------
            # DERNIÈRE BOUGIE CLÔTURÉE
            # ----------------------------------------------------------

            last = d.iloc[-1]

            (
                score_long,
                score_short,
                details
            ) = score(
                last,
                return_details=True
            )

            # ----------------------------------------------------------
            # DIRECTION
            # ----------------------------------------------------------

            direction = best_direction(
                score_long,
                score_short
            )

            best_score = max(
                score_long,
                score_short
            )

            # ----------------------------------------------------------
            # BREAKOUT DU CÔTÉ RETENU
            # ----------------------------------------------------------

            if direction == "LONG":

                breakout_ok = bool(
                    details[
                        "breakout_long"
                    ]
                )

            elif direction == "SHORT":

                breakout_ok = bool(
                    details[
                        "breakout_short"
                    ]
                )

            else:

                breakout_ok = False

            # ----------------------------------------------------------
            # VOLUME
            # ----------------------------------------------------------

            volume_ok = bool(
                details[
                    "volume_ok"
                ]
            )

            # ----------------------------------------------------------
            # STATUT
            # ----------------------------------------------------------

            if (
                best_score >= threshold
                and breakout_ok
                and volume_ok
            ):

                status = "SIGNAL FORT"

            elif best_score >= threshold:

                status = "ATTENTE"

            else:

                status = "SOUS SEUIL"

            # ----------------------------------------------------------
            # CONDITIONS MANQUANTES
            # ----------------------------------------------------------

            missing = []

            if best_score < threshold:

                missing.append(
                    f"SCORE < {threshold:.0f}"
                )

            if not breakout_ok:
                missing.append(
                    "BREAKOUT"
                )

            if not volume_ok:
                missing.append(
                    "VOLUME"
                )

            if missing:

                missing_text = (
                    " + ".join(
                        missing
                    )
                )

            else:

                missing_text = "aucune"

            # ----------------------------------------------------------
            # POINTS DU CÔTÉ RETENU
            # ----------------------------------------------------------

            if direction == "LONG":

                trend_pts = details[
                    "long"
                ]["trend"]

                ema_pts = details[
                    "long"
                ]["ema"]

                rsi_pts = details[
                    "long"
                ]["rsi"]

                volume_pts = details[
                    "long"
                ]["volume"]

                breakout_pts = details[
                    "long"
                ]["breakout"]

            elif direction == "SHORT":

                trend_pts = details[
                    "short"
                ]["trend"]

                ema_pts = details[
                    "short"
                ]["ema"]

                rsi_pts = details[
                    "short"
                ]["rsi"]

                volume_pts = details[
                    "short"
                ]["volume"]

                breakout_pts = details[
                    "short"
                ]["breakout"]

            else:

                trend_pts = 0
                ema_pts = 0
                rsi_pts = 0
                volume_pts = 0
                breakout_pts = 0

            # ----------------------------------------------------------
            # RESULTAT
            # ----------------------------------------------------------

            results.append(
                {
                    "symbol": symbol,

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

                    "missing": missing_text,

                    "trend_pts": float(
                        trend_pts
                    ),

                    "ema_pts": float(
                        ema_pts
                    ),

                    "rsi_pts": float(
                        rsi_pts
                    ),

                    "volume_pts": float(
                        volume_pts
                    ),

                    "breakout_pts": float(
                        breakout_pts
                    ),

                    "breakout_ok": bool(
                        breakout_ok
                    ),

                    "volume_ok": bool(
                        volume_ok
                    ),

                    "rsi": safe_float(
                        last.get(
                            "rsi",
                            float("nan")
                        )
                    ),

                    "relvol": safe_float(
                        last.get(
                            "relvol",
                            float("nan")
                        )
                    ),

                    "trend1h": int(
                        last.get(
                            "trend1h",
                            0
                        )
                    ),

                    "last_candle": last[
                        "open_time"
                    ],
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
                "close",
                "score_long",
                "score_short",
                "direction",
                "best_score",
                "status",
                "missing",
                "trend_pts",
                "ema_pts",
                "rsi_pts",
                "volume_pts",
                "breakout_pts",
                "breakout_ok",
                "volume_ok",
                "rsi",
                "relvol",
                "trend1h",
                "last_candle",
            ]
        )

    return (
        pd.DataFrame(results)
        .sort_values(
            "best_score",
            ascending=False
        )
        .reset_index(
            drop=True
        )
    )
