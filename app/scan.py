"""
Scanner multi-actifs V4.

Logique :

1. Score qualité /100
2. Seuil stratégique = 75
3. Breakout = déclencheur
4. Volume = confirmation
5. SIGNAL FORT uniquement si :
       score >= 75
       + breakout
       + volume

Statuts :

- SIGNAL FORT
- ATTENTE
- SOUS SEUIL
"""

import argparse
import time

import pandas as pd

from backtest import (
    klines,
    indicators,
    score,
)


# ============================================================
# PARAMÈTRES
# ============================================================

DEFAULT_THRESHOLD = 75


# ============================================================
# SCAN
# ============================================================

def scan(
    symbols,
    fetcher=klines,
    interval="5m",
    limit=1000,
    threshold=DEFAULT_THRESHOLD,
    pause=0.3,
):
    rows = []

    for sym in symbols:

        try:

            df = fetcher(
                sym,
                interval=interval,
                limit=limit,
            )

            if df is None or df.empty:
                print(
                    f"{sym}: aucune donnée."
                )
                continue

            d = indicators(df)

            # ------------------------------------------------
            # Niveaux de breakout
            # ------------------------------------------------

            d["prev_high"] = (
                d["high"]
                .rolling(20)
                .max()
                .shift(1)
            )

            d["prev_low"] = (
                d["low"]
                .rolling(20)
                .min()
                .shift(1)
            )

            last = d.iloc[-1]

            required = [
                "ema20",
                "ema50",
                "rsi",
                "prev_high",
                "prev_low",
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
            # Score
            # ------------------------------------------------

            L, S, details = score(
                last,
                return_details=True,
            )

            # ------------------------------------------------
            # Direction
            # ------------------------------------------------

            if L > S:

                direction = "LONG"

                best_score = L

                trend_pts = details["long_trend"]
                ema_pts = details["long_ema"]
                rsi_pts = details["long_rsi"]
                volume_pts = details["long_volume"]
                breakout_pts = details["long_breakout"]

            elif S > L:

                direction = "SHORT"

                best_score = S

                trend_pts = details["short_trend"]
                ema_pts = details["short_ema"]
                rsi_pts = details["short_rsi"]
                volume_pts = details["short_volume"]
                breakout_pts = details["short_breakout"]

            else:

                direction = "LONG"
                best_score = L

                trend_pts = details["long_trend"]
                ema_pts = details["long_ema"]
                rsi_pts = details["long_rsi"]
                volume_pts = details["long_volume"]
                breakout_pts = details["long_breakout"]

            # ------------------------------------------------
            # Conditions du déclencheur
            # ------------------------------------------------

            breakout_ok = (
                breakout_pts > 0
            )

            volume_ok = (
                volume_pts > 0
            )

            score_ok = (
                best_score >= threshold
            )

            # ------------------------------------------------
            # Statut
            # ------------------------------------------------

            if score_ok and breakout_ok and volume_ok:

                status = "SIGNAL FORT"

                missing = ""

            elif not score_ok:

                status = "SOUS SEUIL"

                missing = "SCORE"

            else:

                status = "ATTENTE"

                missing_parts = []

                if not breakout_ok:
                    missing_parts.append(
                        "BREAKOUT"
                    )

                if not volume_ok:
                    missing_parts.append(
                        "VOLUME"
                    )

                missing = " + ".join(
                    missing_parts
                )

            # ------------------------------------------------
            # Relvol
            # ------------------------------------------------

            relvol = last.relvol

            if pd.isna(relvol):
                relvol_value = float("nan")
            else:
                relvol_value = float(
                    relvol
                )

            # ------------------------------------------------
            # Résultat
            # ------------------------------------------------

            rows.append(
                {
                    "symbol": sym,

                    "close": round(
                        float(last.close),
                        4,
                    ),

                    "score_long": float(L),
                    "score_short": float(S),

                    "score": float(
                        best_score
                    ),

                    "direction": direction,

                    # Composantes
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

                    # Conditions
                    "score_ok": bool(
                        score_ok
                    ),

                    "breakout_ok": bool(
                        breakout_ok
                    ),

                    "volume_ok": bool(
                        volume_ok
                    ),

                    "status": status,

                    "missing": missing,

                    # Indicateurs
                    "rsi": round(
                        float(last.rsi),
                        1,
                    ),

                    "relvol": relvol_value,

                    "trend1h": int(
                        last.trend1h
                    ),

                    # Horodatage
                    "last_candle": last.open_time,
                }
            )

        except Exception as e:

            print(
                f"Erreur sur {sym}: {e}"
            )

        time.sleep(pause)

    # ========================================================
    # DATAFRAME FINAL
    # ========================================================

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows)

    out = out.sort_values(
        "score",
        ascending=False,
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
            "DOTUSDT",
        ],
    )

    ap.add_argument(
        "--interval",
        default="5m",
    )

    ap.add_argument(
        "--limit",
        type=int,
        default=1000,
    )

    ap.add_argument(
        "--threshold",
        type=float,
        default=75,
    )

    ap.add_argument(
        "--top",
        type=int,
        default=10,
    )

    args = ap.parse_args()

    print(
        f"Scan de {len(args.symbols)} actifs "
        f"en {args.interval}..."
    )

    results = scan(
        args.symbols,
        interval=args.interval,
        limit=args.limit,
        threshold=args.threshold,
    )

    if results.empty:

        print(
            "Aucun résultat exploitable."
        )

    else:

        print(
            results.head(
                args.top
            ).to_string(
                index=False
            )
        )

        results.to_csv(
            "scan_results.csv",
            index=False,
        )

        print(
            f"\n{len(results)} actifs analysés."
        )

        print(
            "Résultats complets : "
            "scan_results.csv"
        )
