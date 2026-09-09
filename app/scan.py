import time
import math

import pandas as pd

from backtest import (
    indicators,
    score,
    BREAKOUT_BUFFER,
)


# ======================================================================
# CONFIGURATION
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
# SCAN D'UN ENSEMBLE D'ACTIFS
# ======================================================================

def scan(
    symbols,
    fetcher,
    interval="15m",
    limit=1000,
    threshold=75,
    pause=0.0,
):
    """
    Scan V4.

    Pour chaque actif :

    1. récupération des données
    2. suppression de la bougie en formation
    3. indicateurs
    4. précédent plus haut / plus bas
    5. score LONG / SHORT
    6. validation breakout + volume
    7. statut final
    """

    results = []

    for symbol in symbols:

        try:

            # ----------------------------------------------------------
            # Récupération
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

            # ----------------------------------------------------------
            # Nettoyage
            # ----------------------------------------------------------

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
                c
                for c in required
                if c not in df.columns
            ]

            if missing_columns:
                print(
                    f"{symbol}: colonnes absentes "
                    f"{missing_columns}"
                )

                if pause:
                    time.sleep(pause)

                continue

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

            df = df.sort_values(
                "open_time"
            ).reset_index(
                drop=True
            )

            if len(df) < 60:
                print(
                    f"{symbol}: données insuffisantes "
                    f"({len(df)} bougies)"
                )

                if pause:
                    time.sleep(pause)

                continue

            # ----------------------------------------------------------
            # INDICATEURS
            # ----------------------------------------------------------

            d = indicators(df)

            if d.empty:
                continue

            # ----------------------------------------------------------
            # BREAKOUT
            #
            # IMPORTANT :
            # shift(1) signifie que la bougie actuelle est comparée
            # au plus haut / plus bas des 20 bougies précédentes.
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

            # ----------------------------------------------------------
            # Dernière bougie CLÔTURÉE
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
                    details["breakout_long"]
                )

            elif direction == "SHORT":

                breakout_ok = bool(
                    details["breakout_short"]
                )

            else:

                breakout_ok = False

            # ----------------------------------------------------------
            # VOLUME DU CÔTÉ RETENU
            # ----------------------------------------------------------

            volume_ok = bool(
                details["volume_ok"]
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
                    " + ".join(missing)
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
            # FRAÎCHEUR
            # ----------------------------------------------------------

            last_candle = last[
                "open_time"
            ]

            # ----------------------------------------------------------
            # RELVOL
            # ----------------------------------------------------------

            relvol = safe_float(
                last.get(
                    "relvol",
                    float("nan")
                )
            )

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

                    "breakout_ok": breakout_ok,

                    "volume_ok": volume_ok,

                    "rsi": safe_float(
                        last.get(
                            "rsi",
                            float("nan")
                        )
                    ),

                    "relvol": relvol,

                    "trend1h": safe_float(
                        last.get(
                            "trend1h",
                            0
                        ),
                        0
                    ),

                    "last_candle": last_candle,
                }
            )

        except Exception as exc:

            print(
                f"{symbol}: ERREUR — {exc}"
            )

        finally:

            if pause:
                time.sleep(
                    pause
                )

    if not results:
        return pd.DataFrame()

    result = pd.DataFrame(
        results
    )

    result = result.sort_values(
        "best_score",
        ascending=False
    ).reset_index(
        drop=True
    )

    return result
