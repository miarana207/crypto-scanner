import os
import argparse
import requests
import math

from datetime import datetime, timezone

import pandas as pd

from dotenv import load_dotenv

load_dotenv()


BASE = os.getenv(
    "BINANCE_DATA_URL",
    "https://api.binance.com"
)


# =========================================================
# PARAMÈTRE RAPID ENTRY
# =========================================================

# 0.001 = 0,10 %
BREAKOUT_BUFFER = 0.001


# =========================================================
# DONNÉES BINANCE
# =========================================================

def klines(
    symbol,
    interval="5m",
    limit=1000,
    start=None,
    end=None
):

    p = {
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": limit
    }

    if start:

        p["startTime"] = int(
            start.timestamp() * 1000
        )

    if end:

        p["endTime"] = int(
            end.timestamp() * 1000
        )

    r = requests.get(
        BASE + "/api/v3/klines",
        params=p,
        timeout=30
    )

    r.raise_for_status()

    x = r.json()

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
        "ignore"
    ]

    df = pd.DataFrame(
        x,
        columns=cols
    )

    for c in [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]:

        df[c] = df[c].astype(float)

    df["open_time"] = pd.to_datetime(
        df["open_time"],
        unit="ms",
        utc=True
    )

    return df


# =========================================================
# INDICATEURS
# =========================================================

def indicators(df):

    d = df.copy()

    # -----------------------------------------------------
    # EMA 20
    # -----------------------------------------------------

    d["ema20"] = (
        d.close
        .ewm(
            span=20,
            adjust=False
        )
        .mean()
    )

    # -----------------------------------------------------
    # EMA 50
    # -----------------------------------------------------

    d["ema50"] = (
        d.close
        .ewm(
            span=50,
            adjust=False
        )
        .mean()
    )

    # -----------------------------------------------------
    # RSI 14
    # -----------------------------------------------------

    delta = d.close.diff()

    gain = (
        delta
        .clip(lower=0)
        .rolling(14)
        .mean()
    )

    loss = (
        -delta
        .clip(upper=0)
        .rolling(14)
        .mean()
    )

    rs = (
        gain
        /
        loss.replace(
            0,
            float("nan")
        )
    )

    d["rsi"] = (
        100
        -
        (
            100
            /
            (1 + rs)
        )
    )

    # -----------------------------------------------------
    # RELATIVE VOLUME
    # -----------------------------------------------------

    d["relvol"] = (
        d.volume
        /
        d.volume
        .rolling(20)
        .mean()
    )

    # -----------------------------------------------------
    # TENDANCE 1H
    # -----------------------------------------------------

    d["trend1h"] = 0

    h = (
        d
        .set_index("open_time")
        .close
        .resample("1h")
        .last()
        .dropna()
    )

    he20 = (
        h
        .ewm(
            span=20,
            adjust=False
        )
        .mean()
    )

    he50 = (
        h
        .ewm(
            span=50,
            adjust=False
        )
        .mean()
    )

    ht = pd.Series(
        0,
        index=h.index
    )

    ht[
        (h > he20)
        &
        (he20 > he50)
    ] = 1

    ht[
        (h < he20)
        &
        (he20 < he50)
    ] = -1

    d["trend1h"] = (
        ht
        .reindex(
            d.open_time,
            method="ffill"
        )
        .values
    )

    return d


# =========================================================
# SCORE V4 — 100 POINTS
# =========================================================

def score(
    row,
    return_details=False,
    breakout_buffer=BREAKOUT_BUFFER
):

    """
    Score V4.1 normalisé sur 100.

    Pondération :

        Tendance 1H : 30 points
        EMA          : 20 points
        RSI          : 15 points
        Volume       : 15 points
        Breakout     : 20 points

    Maximum absolu : 100 points.

    Rapid Entry :

        Le breakout est validé uniquement si la clôture
        dépasse le niveau des 20 bougies précédentes
        avec une marge minimale de breakout_buffer.

        Exemple :
        breakout_buffer = 0.001
        = 0,10 %
    """

    L = 0
    S = 0

    details = {
        "trend": 0,
        "ema": 0,
        "rsi": 0,
        "volume": 0,
        "breakout": 0,
    }

    # ---------------------------------------------------------
    # 1. TENDANCE 1H — 30 POINTS
    # ---------------------------------------------------------

    if row.trend1h == 1:

        L += 30

        details["trend"] = 30

    elif row.trend1h == -1:

        S += 30

        details["trend"] = -30

    # ---------------------------------------------------------
    # 2. EMA — 20 POINTS
    # ---------------------------------------------------------

    if (
        row.close
        >
        row.ema20
        >
        row.ema50
    ):

        L += 20

        details["ema"] = 20

    elif (
        row.close
        <
        row.ema20
        <
        row.ema50
    ):

        S += 20

        details["ema"] = -20

    # ---------------------------------------------------------
    # 3. RSI — 15 POINTS
    # ---------------------------------------------------------

    if 50 <= row.rsi <= 70:

        L += 15

        details["rsi"] = 15

    elif 30 <= row.rsi <= 50:

        S += 15

        details["rsi"] = -15

    # ---------------------------------------------------------
    # 4. VOLUME — 15 POINTS
    # ---------------------------------------------------------

    if row.relvol >= 1.5:

        if row.trend1h == 1:

            L += 15

            details["volume"] = 15

        elif row.trend1h == -1:

            S += 15

            details["volume"] = -15

    # ---------------------------------------------------------
    # 5. BREAKOUT — 20 POINTS
    # ---------------------------------------------------------

    prev_high = row.prev_high
    prev_low = row.prev_low

    # LONG :
    # clôture > ancien plus haut + marge

    if (
        row.close
        >
        prev_high * (
            1 + breakout_buffer
        )
    ):

        L += 20

        details["breakout"] = 20

    # SHORT :
    # clôture < ancien plus bas - marge

    elif (
        row.close
        <
        prev_low * (
            1 - breakout_buffer
        )
    ):

        S += 20

        details["breakout"] = -20

    if return_details:

        return L, S, details

    return L, S


# =========================================================
# BACKTEST
# =========================================================

def run(
    df,
    fee=0.001,
    slippage=0.0002,
    stop_pct=0.01,
    target_pct=0.02,
    threshold=75
):

    d = indicators(df)

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

    position = None

    entry = 0
    stop = 0
    target = 0

    trades = []

    equity = 1.0
    peak = 1.0

    for i, row in d.iterrows():

        if any(
            pd.isna(row[x])
            for x in [
                "ema20",
                "ema50",
                "rsi",
                "relvol",
                "prev_high",
                "prev_low"
            ]
        ):

            continue

        # -----------------------------------------------------
        # SCORE
        # -----------------------------------------------------

        L, S, details = score(
            row,
            return_details=True,
            breakout_buffer=BREAKOUT_BUFFER
        )

        # -----------------------------------------------------
        # RAPID ENTRY
        # -----------------------------------------------------

        action = None

        # LONG :
        # score suffisant + breakout + volume

        if (
            L >= threshold
            and L > S
            and details["breakout"] == 20
            and details["volume"] == 15
        ):

            action = "LONG"

        # SHORT :
        # score suffisant + breakout + volume

        elif (
            S >= threshold
            and S > L
            and details["breakout"] == -20
            and details["volume"] == -15
        ):

            action = "SHORT"

        # -----------------------------------------------------
        # GESTION DE POSITION
        # -----------------------------------------------------

        if position:

            if position == "LONG":

                hit_stop = (
                    row.low <= stop
                )

                hit_target = (
                    row.high >= target
                )

                if hit_stop or hit_target:

                    exitp = (
                        stop
                        if hit_stop
                        else target
                    )

                    ret = (
                        (exitp / entry - 1)
                        - 2 * fee
                        - slippage
                    )

                    equity *= 1 + ret

                    trades.append(
                        (
                            row.open_time,
                            position,
                            entry,
                            exitp,
                            ret
                        )
                    )

                    position = None

            else:

                hit_stop = (
                    row.high >= stop
                )

                hit_target = (
                    row.low <= target
                )

                if hit_stop or hit_target:

                    exitp = (
                        stop
                        if hit_stop
                        else target
                    )

                    ret = (
                        (entry / exitp - 1)
                        - 2 * fee
                        - slippage
                    )

                    equity *= 1 + ret

                    trades.append(
                        (
                            row.open_time,
                            position,
                            entry,
                            exitp,
                            ret
                        )
                    )

                    position = None

        # -----------------------------------------------------
        # NOUVELLE ENTRÉE
        # -----------------------------------------------------

        if not position and action:

            entry = (
                row.close * (
                    1 + slippage
                )
                if action == "LONG"
                else
                row.close * (
                    1 - slippage
                )
            )

            if action == "LONG":

                stop = (
                    entry
                    * (1 - stop_pct)
                )

                target = (
                    entry
                    * (1 + target_pct)
                )

            else:

                stop = (
                    entry
                    * (1 + stop_pct)
                )

                target = (
                    entry
                    * (1 - target_pct)
                )

            position = action

        peak = max(
            peak,
            equity
        )

    # ---------------------------------------------------------
    # FERMETURE POSITION RESTANTE
    # ---------------------------------------------------------

    if position:

        row = d.iloc[-1]

        exitp = row.close

        ret = (
            (exitp / entry - 1)
            - 2 * fee
            if position == "LONG"
            else
            (entry / exitp - 1)
            - 2 * fee
        )

        equity *= 1 + ret

        trades.append(
            (
                row.open_time,
                position,
                entry,
                exitp,
                ret
            )
        )

    # ---------------------------------------------------------
    # STATISTIQUES
    # ---------------------------------------------------------

    t = pd.DataFrame(
        trades,
        columns=[
            "time",
            "side",
            "entry",
            "exit",
            "return"
        ]
    )

    wins = (
        (t["return"] > 0).sum()
        if len(t)
        else 0
    )

    losses = (
        (t["return"] <= 0).sum()
        if len(t)
        else 0
    )

    grosswin = (
        t.loc[
            t["return"] > 0,
            "return"
        ].sum()
        if len(t)
        else 0
    )

    grossloss = (
        abs(
            t.loc[
                t["return"] <= 0,
                "return"
            ].sum()
        )
        if len(t)
        else 0
    )

    pf = (
        grosswin / grossloss
        if grossloss
        else
        float("inf")
        if grosswin
        else
        0
    )

    return {
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
        "final_equity": equity
    }, t


# =========================================================
# MODE TERMINAL
# =========================================================

if __name__ == "__main__":

    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--symbol",
        default="BTCUSDT"
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
        "--fee",
        type=float,
        default=0.001
    )

    ap.add_argument(
        "--slippage",
        type=float,
        default=0.0002
    )

    ap.add_argument(
        "--stop",
        type=float,
        default=0.01
    )

    ap.add_argument(
        "--target",
        type=float,
        default=0.02
    )

    ap.add_argument(
        "--threshold",
        type=float,
        default=75
    )

    args = ap.parse_args()

    df = klines(
        args.symbol,
        args.interval,
        args.limit
    )

    stats, trades = run(
        df,
        args.fee,
        args.slippage,
        args.stop,
        args.target,
        args.threshold
    )

    print(
        "\nBACKTEST",
        args.symbol,
        args.interval
    )

    for k, v in stats.items():

        print(
            f"{k}: {v}"
        )

    if len(trades):

        trades.to_csv(
            f"trades_"
            f"{args.symbol}_"
            f"{args.interval}.csv",
            index=False
        )
