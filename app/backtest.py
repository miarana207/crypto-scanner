import os
import argparse
import requests
from datetime import datetime, timezone

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

BASE = os.getenv("BINANCE_DATA_URL", "https://api.binance.com")

# ============================================================
# PARAMÈTRES STRATÉGIQUES V4
# ============================================================

BREAKOUT_BUFFER = 0.001  # 0,10 %

SCORE_WEIGHTS = {
    "trend": 25,
    "ema": 20,
    "rsi": 15,
    "volume": 15,
    "breakout": 25,
}

SCORE_MAX = sum(SCORE_WEIGHTS.values())


# ============================================================
# DONNÉES BINANCE
# ============================================================

def klines(symbol, interval="5m", limit=1000, start=None, end=None):
    params = {
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": limit,
    }

    if start:
        params["startTime"] = int(start.timestamp() * 1000)

    if end:
        params["endTime"] = int(end.timestamp() * 1000)

    r = requests.get(
        BASE + "/api/v3/klines",
        params=params,
        timeout=30,
    )
    r.raise_for_status()

    data = r.json()

    cols = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "qav",
        "trades",
        "tbv",
        "tqv",
        "ignore",
    ]

    df = pd.DataFrame(data, columns=cols)

    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)

    df["open_time"] = pd.to_datetime(
        df["open_time"],
        unit="ms",
        utc=True,
    )

    return df


# ============================================================
# INDICATEURS
# ============================================================

def indicators(df):
    d = df.copy()

    # EMA
    d["ema20"] = d["close"].ewm(
        span=20,
        adjust=False,
    ).mean()

    d["ema50"] = d["close"].ewm(
        span=50,
        adjust=False,
    ).mean()

    # RSI 14
    delta = d["close"].diff()

    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()

    # Evite division par zéro.
    rs = gain / loss.replace(0, float("nan"))

    d["rsi"] = 100 - (100 / (1 + rs))

    # Volume relatif
    volume_mean = d["volume"].rolling(20).mean()

    d["relvol"] = (
        d["volume"] / volume_mean.replace(0, float("nan"))
    )

    # ========================================================
    # TENDANCE 1H
    # ========================================================

    d["trend1h"] = 0

    h = (
        d.set_index("open_time")["close"]
        .resample("1h")
        .last()
        .dropna()
    )

    he20 = h.ewm(
        span=20,
        adjust=False,
    ).mean()

    he50 = h.ewm(
        span=50,
        adjust=False,
    ).mean()

    ht = pd.Series(
        0,
        index=h.index,
        dtype=int,
    )

    ht[
        (h > he20) &
        (he20 > he50)
    ] = 1

    ht[
        (h < he20) &
        (he20 < he50)
    ] = -1

    d["trend1h"] = (
        ht.reindex(
            d["open_time"],
            method="ffill",
        )
        .fillna(0)
        .astype(int)
        .values
    )

    return d


# ============================================================
# SCORE V4 /100
# ============================================================

def score(row, return_details=False):
    """
    Calcule le score LONG et SHORT sur 100.

    Pondération :
        Tendance  : 25
        EMA       : 20
        RSI       : 15
        Volume    : 15
        Breakout  : 25

    Le breakout utilise une marge de +0,10 % / -0,10 %.
    """

    L = 0
    S = 0

    details_long = {
        "trend": 0,
        "ema": 0,
        "rsi": 0,
        "volume": 0,
        "breakout": 0,
    }

    details_short = {
        "trend": 0,
        "ema": 0,
        "rsi": 0,
        "volume": 0,
        "breakout": 0,
    }

    # --------------------------------------------------------
    # 1. TENDANCE — 25 points
    # --------------------------------------------------------

    if row.trend1h == 1:
        L += 25
        details_long["trend"] = 25

    elif row.trend1h == -1:
        S += 25
        details_short["trend"] = 25

    # --------------------------------------------------------
    # 2. EMA — 20 points
    # --------------------------------------------------------

    if row.close > row.ema20 > row.ema50:
        L += 20
        details_long["ema"] = 20

    elif row.close < row.ema20 < row.ema50:
        S += 20
        details_short["ema"] = 20

    # --------------------------------------------------------
    # 3. RSI — 15 points
    # --------------------------------------------------------

    if 50 <= row.rsi <= 70:
        L += 15
        details_long["rsi"] = 15

    elif 30 <= row.rsi <= 50:
        S += 15
        details_short["rsi"] = 15

    # --------------------------------------------------------
    # 4. VOLUME — 15 points
    # --------------------------------------------------------

    relvol = row.relvol

    if pd.notna(relvol) and relvol >= 1.5:

        # Le volume confirme uniquement la direction
        # déjà suggérée par la tendance.

        if row.trend1h == 1:
            L += 15
            details_long["volume"] = 15

        elif row.trend1h == -1:
            S += 15
            details_short["volume"] = 15

    # --------------------------------------------------------
    # 5. BREAKOUT — 25 points
    # --------------------------------------------------------

    prev_high = row.prev_high
    prev_low = row.prev_low

    if pd.notna(prev_high):
        breakout_long_level = prev_high * (1 + BREAKOUT_BUFFER)

        if row.close > breakout_long_level:
            L += 25
            details_long["breakout"] = 25

    if pd.notna(prev_low):
        breakout_short_level = prev_low * (1 - BREAKOUT_BUFFER)

        if row.close < breakout_short_level:
            S += 25
            details_short["breakout"] = 25

    # --------------------------------------------------------
    # Sécurité : score maximum = 100
    # --------------------------------------------------------

    L = min(L, SCORE_MAX)
    S = min(S, SCORE_MAX)

    if not return_details:
        return L, S

    # --------------------------------------------------------
    # Déterminer le meilleur côté
    # --------------------------------------------------------

    if L >= S:
        best_side = "LONG"
        best_details = details_long
        best_score = L
    else:
        best_side = "SHORT"
        best_details = details_short
        best_score = S

    return L, S, {
        "trend": best_details["trend"],
        "ema": best_details["ema"],
        "rsi": best_details["rsi"],
        "volume": best_details["volume"],
        "breakout": best_details["breakout"],
        "best_score": best_score,
        "best_side": best_side,

        # Détails des deux directions
        "long_trend": details_long["trend"],
        "long_ema": details_long["ema"],
        "long_rsi": details_long["rsi"],
        "long_volume": details_long["volume"],
        "long_breakout": details_long["breakout"],

        "short_trend": details_short["trend"],
        "short_ema": details_short["ema"],
        "short_rsi": details_short["rsi"],
        "short_volume": details_short["volume"],
        "short_breakout": details_short["breakout"],
    }


# ============================================================
# BACKTEST
# ============================================================

def run(
    df,
    fee=0.001,
    slippage=0.0002,
    stop_pct=0.01,
    target_pct=0.02,
    threshold=75,
):
    d = indicators(df)

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

    position = None
    entry = 0
    stop = 0
    target = 0

    trades = []

    equity = 1.0
    peak = 1.0

    required = [
        "ema20",
        "ema50",
        "rsi",
        "prev_high",
        "prev_low",
    ]

    for _, row in d.iterrows():

        if any(pd.isna(row[x]) for x in required):
            continue

        L, S, details = score(
            row,
            return_details=True,
        )

        # ----------------------------------------------------
        # Direction selon score
        # ----------------------------------------------------

        action = None

        if L >= threshold and L > S:
            action = "LONG"

        elif S >= threshold and S > L:
            action = "SHORT"

        # ----------------------------------------------------
        # Gestion position existante
        # ----------------------------------------------------

        if position:

            if position == "LONG":

                hit_stop = row.low <= stop
                hit_target = row.high >= target

                # Si les deux sont touchés dans la même bougie,
                # hypothèse conservatrice : STOP en premier.

                if hit_stop or hit_target:

                    exitp = (
                        stop
                        if hit_stop
                        else target
                    )

                    ret = (
                        exitp / entry - 1
                    ) - 2 * fee - slippage

                    equity *= 1 + ret

                    trades.append(
                        (
                            row.open_time,
                            position,
                            entry,
                            exitp,
                            ret,
                        )
                    )

                    position = None

            else:

                hit_stop = row.high >= stop
                hit_target = row.low <= target

                if hit_stop or hit_target:

                    exitp = (
                        stop
                        if hit_stop
                        else target
                    )

                    ret = (
                        entry / exitp - 1
                    ) - 2 * fee - slippage

                    equity *= 1 + ret

                    trades.append(
                        (
                            row.open_time,
                            position,
                            entry,
                            exitp,
                            ret,
                        )
                    )

                    position = None

        # ----------------------------------------------------
        # Nouvelle position
        # ----------------------------------------------------

        if not position and action:

            if action == "LONG":

                entry = row.close * (
                    1 + slippage
                )

                stop = entry * (
                    1 - stop_pct
                )

                target = entry * (
                    1 + target_pct
                )

            else:

                entry = row.close * (
                    1 - slippage
                )

                stop = entry * (
                    1 + stop_pct
                )

                target = entry * (
                    1 - target_pct
                )

            position = action

        peak = max(
            peak,
            equity,
        )

    # --------------------------------------------------------
    # Fermer position finale
    # --------------------------------------------------------

    if position:

        row = d.iloc[-1]

        exitp = row.close

        if position == "LONG":

            ret = (
                exitp / entry - 1
            ) - 2 * fee

        else:

            ret = (
                entry / exitp - 1
            ) - 2 * fee

        equity *= 1 + ret

        trades.append(
            (
                row.open_time,
                position,
                entry,
                exitp,
                ret,
            )
        )

    # --------------------------------------------------------
    # Statistiques
    # --------------------------------------------------------

    t = pd.DataFrame(
        trades,
        columns=[
            "time",
            "side",
            "entry",
            "exit",
            "return",
        ],
    )

    wins = (
        t["return"] > 0
    ).sum() if len(t) else 0

    losses = (
        t["return"] <= 0
    ).sum() if len(t) else 0

    grosswin = (
        t.loc[
            t["return"] > 0,
            "return",
        ].sum()
        if len(t)
        else 0
    )

    grossloss = abs(
        t.loc[
            t["return"] <= 0,
            "return",
        ].sum()
    ) if len(t) else 0

    pf = (
        grosswin / grossloss
        if grossloss
        else (
            float("inf")
            if grosswin
            else 0
        )
    )

    stats = {
        "trades": len(t),
        "wins": int(wins),
        "losses": int(losses),
        "win_rate": (
            wins / len(t) * 100
            if len(t)
            else 0
        ),
        "profit_factor": pf,
        "return_pct": (
            equity - 1
        ) * 100,
        "final_equity": equity,
    }

    return stats, t


# ============================================================
# EXÉCUTION DIRECTE
# ============================================================

if __name__ == "__main__":

    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--symbol",
        default="BTCUSDT",
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
        "--fee",
        type=float,
        default=0.001,
    )

    ap.add_argument(
        "--slippage",
        type=float,
        default=0.0002,
    )

    ap.add_argument(
        "--stop",
        type=float,
        default=0.01,
    )

    ap.add_argument(
        "--target",
        type=float,
        default=0.02,
    )

    ap.add_argument(
        "--threshold",
        type=float,
        default=75,
    )

    args = ap.parse_args()

    df = klines(
        args.symbol,
        args.interval,
        args.limit,
    )

    stats, trades = run(
        df,
        args.fee,
        args.slippage,
        args.stop,
        args.target,
        args.threshold,
    )

    print(
        f"\nBACKTEST {args.symbol} "
        f"{args.interval}"
    )

    for k, v in stats.items():
        print(
            f"{k}: {v}"
        )

    if len(trades):

        trades.to_csv(
            f"trades_{args.symbol}_{args.interval}.csv",
            index=False,
        )
