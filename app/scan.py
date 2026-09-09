"""
Scanner multi-actifs — V4

Score maximal : 100 points.

Pondération :
- Tendance 1H : 30
- EMA          : 20
- RSI          : 15
- Volume       : 15
- Breakout     : 20

Statut :
- SIGNAL FORT : score >= seuil + breakout + volume
- ATTENTE     : score >= seuil mais trigger incomplet
- "-"         : score sous le seuil

Breakout :
- LONG  : clôture > ancien plus haut de 20 bougies + 0,10 %
- SHORT : clôture < ancien plus bas de 20 bougies - 0,10 %
"""

import argparse
import time

import pandas as pd

from backtest import (
    klines,
    indicators,
    score
)


def scan(
    symbols,
    fetcher=klines,
    interval="5m",
    limit=1000,
    threshold=75,
    pause=0.3
):

    rows = []

    for sym in symbols:

        try:

            # ------------------------------------------------
            # DONNÉES
            # ------------------------------------------------

            df = fetcher(
                sym,
                interval=interval,
                limit=limit
            )

            if df is None or df.empty:

                print(
                    f"{sym}: aucune donnée reçue."
                )

                continue

            # ------------------------------------------------
            # INDICATEURS
            # ------------------------------------------------

            d = indicators(df)

            # ------------------------------------------------
            # ANCIEN PLUS HAUT / BAS
            # ------------------------------------------------

            d["prev_high"] = (
                d.high
                .rolling(20)
                .max()
                .shift(1)
            )

            d["prev_low"] = (
                d.low
                .rolling(20)
                .min()
                .shift(1)
            )

            if d.empty:

                continue

            last = d.iloc[-1]

            # ------------------------------------------------
            # DONNÉES MINIMALES NÉCESSAIRES
            # ------------------------------------------------

            required = [
                "ema20",
                "ema50",
                "rsi",
                "prev_high",
                "prev_low"
            ]

            if any(
                pd.isna(last[c])
                for c in required
            ):

                print(
                    f"{sym}: pas assez de données "
                    f"pour un score fiable, ignoré."
                )

                continue

            # ------------------------------------------------
            # SCORE
            # ------------------------------------------------

            L, S, details = score(
                last,
                return_details=True
            )

            # ------------------------------------------------
            # DIRECTION
            # ------------------------------------------------

            if L >= threshold and L > S:

                direction = "LONG"

            elif S >= threshold and S > L:

                direction = "SHORT"

            else:

                direction = "-"

            # ------------------------------------------------
            # TRIGGER BREAKOUT + VOLUME
            # ------------------------------------------------

            if direction == "LONG":

                breakout_ok = (
                    details["breakout"] == 20
                )

                volume_ok = (
                    details["volume"] == 15
                )

            elif direction == "SHORT":

                breakout_ok = (
                    details["breakout"] == -20
                )

                volume_ok = (
                    details["volume"] == -15
                )

            else:

                breakout_ok = False
                volume_ok = False

            # ------------------------------------------------
            # STATUT
            # ------------------------------------------------

            if direction in [
                "LONG",
                "SHORT"
            ]:

                if (
                    breakout_ok
                    and
                    volume_ok
                ):

                    status = "SIGNAL FORT"

                else:

                    status = "ATTENTE"

            else:

                status = "-"

            # ------------------------------------------------
            # RAISONS DU TRIGGER
            # ------------------------------------------------

            if direction == "LONG":

                if not breakout_ok and not volume_ok:

                    trigger_missing = (
                        "BREAKOUT + VOLUME"
                    )

                elif not breakout_ok:

                    trigger_missing = "BREAKOUT"

                elif not volume_ok:

                    trigger_missing = "VOLUME"

                else:

                    trigger_missing = ""

            elif direction == "SHORT":

                if not breakout_ok and not volume_ok:

                    trigger_missing = (
                        "BREAKOUT + VOLUME"
                    )

                elif not breakout_ok:

                    trigger_missing = "BREAKOUT"

                elif not volume_ok:

                    trigger_missing = "VOLUME"

                else:

                    trigger_missing = ""

            else:

                trigger_missing = "SEUIL"

            # ------------------------------------------------
            # RESULTAT
            # ------------------------------------------------

            rows.append({

                "symbol": sym,

                "close": round(
                    float(last.close),
                    4
                ),

                "score_long": L,

                "score_short": S,

                "direction": direction,

                "status": status,

                "trigger_missing": trigger_missing,

                "rsi": round(
                    float(last.rsi),
                    1
                ),

                "relvol": (
                    round(
                        float(last.relvol),
                        2
                    )
                    if not pd.isna(last.relvol)
                    else
                    float("nan")
                ),

                "trend1h": int(
                    last.trend1h
                ),

                "trend_pts": details["trend"],

                "ema_pts": details["ema"],

                "rsi_pts": details["rsi"],

                "volume_pts": details["volume"],

                "breakout_pts": details["breakout"],

                "last_candle": last.open_time,
            })

        except Exception as e:

            print(
                f"Erreur sur {sym}: {e}"
            )

        time.sleep(pause)

    # ========================================================
    # AUCUN RÉSULTAT
    # ========================================================

    if not rows:

        return pd.DataFrame()

    # ========================================================
    # DATAFRAME FINAL
    # ========================================================

    out = pd.DataFrame(rows)

    out["max_score"] = out[
        [
            "score_long",
            "score_short"
        ]
    ].max(axis=1)

    out = (
        out
        .sort_values(
            "max_score",
            ascending=False
        )
        .drop(
            columns="max_score"
        )
    )

    return out.reset_index(
        drop=True
    )


# ============================================================
# EXÉCUTION DIRECTE
# ============================================================

if __name__ == "__main__":

    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--symbols",
        nargs="+",
        default=[
            "BTCUSDT",
            "ETHUSDT",
            "SOLUSDT",
            "BNBUSDT",
            "XRPUSDT",
            "ADAUSDT",
            "DOGEUSDT",
            "AVAXUSDT",
            "LINKUSDT",
            "DOTUSDT"
        ]
    )

    ap.add_argument(
        "--interval",
        default="5m"
    )

    ap.add_argument(
        "--limit",
        type=int,
        default=1000
    )

    ap.add_argument(
        "--threshold",
        type=float,
        default=75,
        help=(
            "Score minimum sur 100 "
            "pour considérer un signal"
        )
    )

    ap.add_argument(
        "--top",
        type=int,
        default=10
    )

    args = ap.parse_args()

    print(
        f"Scan de {len(args.symbols)} actifs "
        f"en {args.interval}...\n"
    )

    results = scan(
        args.symbols,
        interval=args.interval,
        limit=args.limit,
        threshold=args.threshold
    )

    if results.empty:

        print(
            "Aucun résultat exploitable "
            "(données insuffisantes "
            "sur tous les actifs)."
        )

    else:

        print(
            results
            .head(args.top)
            .to_string(
                index=False
            )
        )

        results.to_csv(
            "scan_results.csv",
            index=False
        )

        print(
            f"\n{len(results)} actifs analysés — "
            "résultats complets dans scan_results.csv"
        )
