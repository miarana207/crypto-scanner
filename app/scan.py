"""
Scanner multi-actifs — V4.1 Rapid Entry

Score maximal : 100 points.

Pondération :
- Tendance 1H : 30
- EMA          : 20
- RSI          : 15
- Volume       : 15
- Breakout     : 20

Logique Rapid Entry :
- Score >= seuil + breakout + volume = SIGNAL FORT
- Score >= seuil mais breakout absent = ATTENTE
- Score >= seuil mais volume absent = ATTENTE
- Score < seuil = diagnostic uniquement

Le breakout est confirmé uniquement si la clôture dépasse
le plus haut/bas des 20 bougies précédentes avec une marge
de confirmation de 0,10 %.

Important :
Une mèche qui dépasse le niveau mais dont la clôture revient
à l'intérieur du range ne déclenche PAS de signal.
"""

import argparse
import time
import pandas as pd

from backtest import klines, indicators, score


# ---------------------------------------------------------
# PARAMÈTRE RAPID ENTRY
# ---------------------------------------------------------

# Marge minimale de confirmation du breakout.
# 0.001 = 0,10 %
BREAKOUT_BUFFER = 0.001


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

            df = fetcher(
                sym,
                interval=interval,
                limit=limit
            )

            d = indicators(df)

            # -------------------------------------------------
            # NIVEAUX DE BREAKOUT
            # -------------------------------------------------

            # On regarde les 20 bougies précédentes,
            # sans inclure la bougie actuelle.
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

            last = d.iloc[-1]

            required = [
                "ema20",
                "ema50",
                "rsi",
                "relvol",
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

            # -------------------------------------------------
            # SCORE
            # -------------------------------------------------

            L, S, details = score(
                last,
                return_details=True,
                breakout_buffer=BREAKOUT_BUFFER
            )

            # -------------------------------------------------
            # DIRECTION
            # -------------------------------------------------

            if L >= threshold and L > S:

                direction = "LONG"

            elif S >= threshold and S > L:

                direction = "SHORT"

            else:

                direction = "-"

            # -------------------------------------------------
            # TRIGGER RAPID ENTRY
            # -------------------------------------------------

            # Le breakout doit être confirmé par la clôture.
            # Une simple mèche ne suffit pas.

            long_breakout = (
                last.close
                >
                last.prev_high * (1 + BREAKOUT_BUFFER)
            )

            short_breakout = (
                last.close
                <
                last.prev_low * (1 - BREAKOUT_BUFFER)
            )

            # Volume confirmé.
            volume_confirmed = (
                not pd.isna(last.relvol)
                and last.relvol >= 1.5
            )

            if direction == "LONG":

                breakout_ok = long_breakout
                volume_ok = (
                    volume_confirmed
                    and last.trend1h == 1
                )

            elif direction == "SHORT":

                breakout_ok = short_breakout
                volume_ok = (
                    volume_confirmed
                    and last.trend1h == -1
                )

            else:

                breakout_ok = False
                volume_ok = False

            # -------------------------------------------------
            # STATUT
            # -------------------------------------------------

            if direction in ["LONG", "SHORT"]:

                if breakout_ok and volume_ok:

                    status = "SIGNAL FORT"

                else:

                    status = "ATTENTE"

            else:

                status = "-"

            # -------------------------------------------------
            # RAISON DE L'ATTENTE
            # -------------------------------------------------

            if status == "ATTENTE":

                if not breakout_ok and not volume_ok:

                    trigger_reason = (
                        "BREAKOUT + VOLUME MANQUANTS"
                    )

                elif not breakout_ok:

                    trigger_reason = (
                        "BREAKOUT MANQUANT"
                    )

                elif not volume_ok:

                    trigger_reason = (
                        "VOLUME MANQUANT"
                    )

                else:

                    trigger_reason = ""

            elif status == "SIGNAL FORT":

                trigger_reason = (
                    "BREAKOUT + VOLUME CONFIRMÉS"
                )

            else:

                trigger_reason = ""

            # -------------------------------------------------
            # RESULTAT
            # -------------------------------------------------

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

                "trigger_reason": trigger_reason,

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
                    else float("nan")
                ),

                "trend1h": int(
                    last.trend1h
                ),

                "trend_pts": details["trend"],

                "ema_pts": details["ema"],

                "rsi_pts": details["rsi"],

                "volume_pts": details["volume"],

                "breakout_pts": details["breakout"],

                "prev_high": round(
                    float(last.prev_high),
                    4
                ),

                "prev_low": round(
                    float(last.prev_low),
                    4
                ),

                "last_candle": last.open_time,
            })

        except Exception as e:

            print(
                f"Erreur sur {sym}: {e}"
            )

        time.sleep(pause)

    # ---------------------------------------------------------
    # DATAFRAME FINAL
    # ---------------------------------------------------------

    if not rows:

        return pd.DataFrame()

    out = pd.DataFrame(rows)

    out["max_score"] = out[
        ["score_long", "score_short"]
    ].max(axis=1)

    # Les meilleurs scores en premier.
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

    return out.reset_index(drop=True)


# =========================================================
# MODE TERMINAL
# =========================================================

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
            "Score minimum sur 100 pour "
            "considérer une configuration"
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
            .to_string(index=False)
        )

        results.to_csv(
            "scan_results.csv",
            index=False
        )

        print(
            f"\n{len(results)} actifs analysés — "
            "résultats complets dans scan_results.csv"
        )
