"""
Scanner multi-actifs V4.

Score maximum : 100
- Trend    : 25
- EMA      : 20
- RSI      : 15
- Volume   : 15
- Breakout : 25

Breakout confirmé :
- LONG  : close > prev_high * 1.001
- SHORT : close < prev_low  * 0.999

Statuts :
- SIGNAL FORT : score >= seuil + breakout + volume
- ATTENTE     : score >= seuil mais trigger incomplet
- SOUS SEUIL  : score < seuil
"""

import argparse
import time

import pandas as pd

from backtest import (
    klines,
    indicators,
    score,
    BREAKOUT_BUFFER,
    RELATIVE_VOLUME_THRESHOLD,
)


# ============================================================
# DIAGNOSTIC
# ============================================================

def determine_status(
    best_score,
    direction,
    threshold,
    breakout_ok,
    volume_ok,
):
    """
    Détermine le statut final du signal.
    """

    if (
        direction in ("LONG", "SHORT")
        and best_score >= threshold
    ):

        missing = []

        if not breakout_ok:
            missing.append("BREAKOUT")

        if not volume_ok:
            missing.append("VOLUME")

        if not missing:
            return (
                "SIGNAL FORT",
                "PRÉRÉQUIS COMPLETS",
            )

        return (
            "ATTENTE",
            " + ".join(missing),
        )

    return (
        "SOUS SEUIL",
        "SCORE",
    )


# ============================================================
# SCAN
# ============================================================

def scan(
    symbols,
    fetcher=klines,
    interval="5m",
    limit=1000,
    threshold=75,
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

            # Canal de breakout :
            # 20 dernières bougies précédentes,
            # en excluant la bougie actuelle.
            d["prev_high"] = (
                d.high
                .rolling(
                    20,
                    min_periods=20,
                )
                .max()
                .shift(1)
            )

            d["prev_low"] = (
                d.low
                .rolling(
                    20,
                    min_periods=20,
                )
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

            # =================================================
            # SCORE
            # =================================================

            L, S, details = score(
                last,
                return_details=True,
            )

            # =================================================
            # DIRECTION
            # =================================================

            if L >= threshold and L > S:
                direction = "LONG"
                best_score = L

                breakout_ok = details[
                    "long_breakout"
                ]

            elif S >= threshold and S > L:
                direction = "SHORT"
                best_score = S

                breakout_ok = details[
                    "short_breakout"
                ]

            else:
                # Pour le diagnostic, on garde la direction
                # correspondant au meilleur score.
                if L > S:
                    direction = "LONG"
                    best_score = L
                    breakout_ok = details[
                        "long_breakout"
                    ]

                elif S > L:
                    direction = "SHORT"
                    best_score = S
                    breakout_ok = details[
                        "short_breakout"
                    ]

                else:
                    direction = "-"
                    best_score = L
                    breakout_ok = False

            volume_ok = details[
                "volume_ok"
            ]

            status, missing = determine_status(
                best_score=best_score,
                direction=direction,
                threshold=threshold,
                breakout_ok=breakout_ok,
                volume_ok=volume_ok,
            )

            # =================================================
            # RELVOL
            # =================================================

            relvol = last.relvol

            if pd.isna(relvol):
                relvol_value = float("nan")
            else:
                relvol_value = float(relvol)

            # =================================================
            # RESULTAT
            # =================================================

            rows.append(
                {
                    "symbol": sym,

                    "close": round(
                        float(last.close),
                        8,
                    ),

                    "score_long": float(L),
                    "score_short": float(S),

                    "direction": direction,
                    "best_score": float(best_score),

                    "status": status,
                    "missing": missing,

                    # Détail du score
                    "trend_score": details[
                        "trend"
                    ],

                    "ema_score": details[
                        "ema"
                    ],

                    "rsi_score": details[
                        "rsi"
                    ],

                    "volume_score": details[
                        "volume"
                    ],

                    "breakout_score": details[
                        "breakout"
                    ],

                    # Alias utilisés dans le reporting
                    "trend_pts": details[
                        "trend_pts"
                    ],

                    "ema_pts": details[
                        "ema_pts"
                    ],

                    "rsi_pts": details[
                        "rsi_pts"
                    ],

                    "volume_pts": details[
                        "volume_pts"
                    ],

                    "breakout_pts": details[
                        "breakout_pts"
                    ],

                    "rsi": round(
                        float(last.rsi),
                        2,
                    ),

                    "relvol": relvol_value,

                    "trend1h": int(
                        last.trend1h
                    ),

                    "volume_ok": bool(
                        volume_ok
                    ),

                    "breakout_ok": bool(
                        breakout_ok
                    ),

                    "long_breakout": bool(
                        details[
                            "long_breakout"
                        ]
                    ),

                    "short_breakout": bool(
                        details[
                            "short_breakout"
                        ]
                    ),

                    # Horodatage dernière bougie
                    "last_candle": last.open_time,
                }
            )

        except Exception as e:

            print(
                f"Erreur sur {sym}: {e}"
            )

        time.sleep(pause)

    # ========================================================
    # SORTIE
    # ========================================================

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows)

    out = out.sort_values(
        "best_score",
        ascending=False,
    )

    return out.reset_index(drop=True)


# ============================================================
# EXECUTION DIRECTE
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
        help=(
            "Score minimum sur 100 "
            "pour considérer un setup."
        ),
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

    print(
        "Score V4 : 100 points maximum"
    )

    print(
        "Seuil : "
        f"{args.threshold:.0f}/100"
    )

    print(
        "Breakout : "
        f"{BREAKOUT_BUFFER * 100:.2f}%"
    )

    print(
        "Volume : relvol >= "
        f"{RELATIVE_VOLUME_THRESHOLD:.1f}"
    )

    print()

    results = scan(
        args.symbols,
        interval=args.interval,
        limit=args.limit,
        threshold=args.threshold,
    )

    if results.empty:

        print(
            "Aucun résultat exploitable "
            "(données insuffisantes)."
        )

    else:

        print(
            results.head(args.top)
            .to_string(index=False)
        )

        results.to_csv(
            "scan_results.csv",
            index=False,
        )

        print(
            f"\n{len(results)} actifs analysés "
            "— résultats complets dans "
            "scan_results.csv"
        )
