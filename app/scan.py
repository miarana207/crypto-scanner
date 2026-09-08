"""
Scanner multi-actifs — v1.1
Réutilise les fonctions indicators() et score() de backtest.py
pour classer une liste d'actifs selon leur score long/short actuel.

Usage:
    python scan.py --symbols BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT --interval 5m --threshold 60
"""
import argparse
import time
import pandas as pd
from backtest import klines, indicators, score


def scan(symbols, fetcher=klines, interval="5m", limit=1000, threshold=60, pause=0.3):
    rows = []
    for sym in symbols:
        try:
            df = fetcher(sym, interval=interval, limit=limit)
            d = indicators(df)
            d["prev_high"] = d.high.rolling(20).max().shift(1)
            d["prev_low"] = d.low.rolling(20).min().shift(1)
            last = d.iloc[-1]

            required = ["ema20", "ema50", "rsi", "relvol", "prev_high", "prev_low"]
            if any(pd.isna(last[c]) for c in required):
                print(f"{sym}: pas assez de données pour un score fiable, ignoré.")
                continue

            L, S = score(last)
            if L >= threshold and L > S:
                direction = "LONG"
            elif S >= threshold and S > L:
                direction = "SHORT"
            else:
                direction = "-"

            rows.append({
                "symbol": sym,
                "close": round(float(last.close), 4),
                "score_long": L,
                "score_short": S,
                "direction": direction,
                "rsi": round(float(last.rsi), 1),
                "relvol": round(float(last.relvol), 2),
                "trend1h": int(last.trend1h),
                # Horodatage de la dernière bougie utilisée pour ce score.
                # Sert à détecter une donnée figée : si ce timestamp ne
                # bouge pas d'un run à l'autre alors que le marché est
                # censé être ouvert, la source renvoie des données stale
                # (souvent un signe de rate-limit atteint côté fournisseur).
                "last_candle": last.open_time,
            })
        except Exception as e:
            print(f"Erreur sur {sym}: {e}")
        time.sleep(pause)  # évite de saturer l'API du fournisseur

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows)
    out["max_score"] = out[["score_long", "score_short"]].max(axis=1)
    out = out.sort_values("max_score", ascending=False).drop(columns="max_score")
    return out.reset_index(drop=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+",
                     default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                              "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"])
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--threshold", type=float, default=60,
                     help="Score minimum (0-100) pour considérer un signal LONG ou SHORT")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    print(f"Scan de {len(args.symbols)} actifs en {args.interval}...\n")
    results = scan(args.symbols, interval=args.interval, limit=args.limit, threshold=args.threshold)

    if results.empty:
        print("Aucun résultat exploitable (données insuffisantes sur tous les actifs).")
    else:
        print(results.head(args.top).to_string(index=False))
        results.to_csv("scan_results.csv", index=False)
        print(f"\n{len(results)} actifs analysés — résultats complets dans scan_results.csv")
