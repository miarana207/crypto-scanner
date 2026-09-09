import os
import argparse
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

BASE = os.getenv("BINANCE_DATA_URL", "https://api.binance.com")

# ============================================================
# PARAMÈTRES STRATÉGIE V4
# ============================================================

TREND_POINTS = 25
EMA_POINTS = 20
RSI_POINTS = 15
VOLUME_POINTS = 15
BREAKOUT_POINTS = 25

MAX_SCORE = (
    TREND_POINTS
    + EMA_POINTS
    + RSI_POINTS
    + VOLUME_POINTS
    + BREAKOUT_POINTS
)

BREAKOUT_BUFFER = 0.001      # +0,10 %
RELATIVE_VOLUME_THRESHOLD = 1.5


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
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["open_time"] = pd.to_datetime(
        df["open_time"],
        unit="ms",
        utc=True,
    )

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    ).reset_index(drop=True)

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

    gain = delta.clip(lower=0).rolling(
        14,
        min_periods=14,
    ).mean()

    loss = (-delta.clip(upper=0)).rolling(
        14,
        min_periods=14,
    ).mean()

    # Gestion correcte des séries sans pertes
    rs = gain / loss.replace(0, float("nan"))

    d["rsi"] = 100 - (
        100 / (1 + rs)
    )

    # Si aucune perte sur la période :
    # RSI = 100
    d.loc[
        (loss == 0) & gain.notna(),
        "rsi"
    ] = 100.0

    # Si aucun gain ni perte :
    # RSI neutre
    d.loc[
        (loss == 0) & (gain == 0),
        "rsi"
    ] = 50.0

    # Volume relatif
    volume_mean = d["volume"].rolling(
        20,
        min_periods=20,
    ).mean()

    d["relvol"] = (
        d["volume"] / volume_mean
    )

    # ========================================================
    # Tendance 1H
    # ========================================================

    d["trend1h"] = 0

    if "open_time" in d.columns:
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
        Trend    = 25
        EMA      = 20
        RSI      = 15
        Volume   = 15
        Breakout = 25

    Le breakout utilise une marge de 0,10 %.
    """

    L = 0
    S = 0

    trend_pts = 0
    ema_pts = 0
    rsi_pts = 0
    volume_pts = 0
    breakout_pts = 0

    # ========================================================
    # TREND — 25 points
    # ========================================================

    if row.trend1h == 1:
        L += TREND_POINTS
        trend_pts = TREND_POINTS

    elif row.trend1h == -1:
        S += TREND_POINTS
        trend_pts = -TREND_POINTS

    # ========================================================
    # EMA — 20 points
    # ========================================================

    if row.close > row.ema20 > row.ema50:
        L += EMA_POINTS
        ema_pts = EMA_POINTS

    elif row.close < row.ema20 < row.ema50:
        S += EMA_POINTS
        ema_pts = -EMA_POINTS

    # ========================================================
    # RSI — 15 points
    # ========================================================

    if 50 <= row.rsi <= 70:
        L += RSI_POINTS
        rsi_pts = RSI_POINTS

    elif 30 <= row.rsi < 50:
        S += RSI_POINTS
        rsi_pts = -RSI_POINTS

    # ========================================================
    # VOLUME — 15 points
    # ========================================================

    volume_ok = (
        pd.notna(row.relvol)
        and row.relvol >= RELATIVE_VOLUME_THRESHOLD
    )

    if volume_ok:
        if row.trend1h == 1:
            L += VOLUME_POINTS
            volume_pts = VOLUME_POINTS

        elif row.trend1h == -1:
            S += VOLUME_POINTS
            volume_pts = -VOLUME_POINTS

    # ========================================================
    # BREAKOUT — 25 points
    # ========================================================

    prev_high = getattr(row, "prev_high", float("nan"))
    prev_low = getattr(row, "prev_low", float("nan"))

    long_breakout = (
        pd.notna(prev_high)
        and row.close > prev_high * (1 + BREAKOUT_BUFFER)
    )

    short_breakout = (
        pd.notna(prev_low)
        and row.close < prev_low * (1 - BREAKOUT_BUFFER)
    )

    if long_breakout:
        L += BREAKOUT_POINTS
        breakout_pts = BREAKOUT_POINTS

    elif short_breakout:
        S += BREAKOUT_POINTS
        breakout_pts = -BREAKOUT_POINTS

    # ========================================================
    # LIMITATION DE SÉCURITÉ
    # ========================================================

    L = min(float(L), float(MAX_SCORE))
    S = min(float(S), float(MAX_SCORE))

    if not return_details:
        return L, S

    details = {
        "trend": trend_pts,
        "ema": ema_pts,
        "rsi": rsi_pts,
        "volume": volume_pts,
        "breakout": breakout_pts,

        "trend_pts": trend_pts,
        "ema_pts": ema_pts,
        "rsi_pts": rsi_pts,
        "volume_pts": volume_pts,
        "breakout_pts": breakout_pts,

        "volume_ok": bool(volume_ok),
        "long_breakout": bool(long_breakout),
        "short_breakout": bool(short_breakout),

        "max_score": MAX_SCORE,
    }

    return L, S, details


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
        d.high
        .rolling(20, min_periods=20)
        .max()
        .shift(1)
    )

    d["prev_low"] = (
        d.low
        .rolling(20, min_periods=20)
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

    for _, row in d.iterrows():

        required = [
            "ema20",
            "ema50",
            "rsi",
            "prev_high",
            "prev_low",
        ]

        if any(pd.isna(row[x]) for x in required):
            continue

        L, S, details = score(
            row,
            return_details=True,
        )

        # ====================================================
        # DIRECTION
        # ====================================================

        if L >= threshold and L > S:
            action = "LONG"

        elif S >= threshold and S > L:
            action = "SHORT"

        else:
            action = None

        # ====================================================
        # GESTION POSITION
        # ====================================================

        if position:

            if position == "LONG":

                hit_stop = row.low <= stop
                hit_target = row.high >= target

                # Si les deux sont touchés dans la même bougie,
                # hypothèse conservatrice : stop d'abord.
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

        # ====================================================
        # NOUVELLE POSITION
        # ====================================================

        if not position and action:

            if action == "LONG":

                entry = (
                    row.close *
                    (1 + slippage)
                )

                stop = (
                    entry *
                    (1 - stop_pct)
                )

                target = (
                    entry *
                    (1 + target_pct)
                )

            else:

                entry = (
                    row.close *
                    (1 - slippage)
                )

                stop = (
                    entry *
                    (1 + stop_pct)
                )

                target = (
                    entry *
                    (1 - target_pct)
                )

            position = action

        peak = max(
            peak,
            equity,
        )

    # ========================================================
    # FERMETURE POSITION FINALE
    # ========================================================

    if position and len(d):

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

    # ========================================================
    # STATISTIQUES
    # ========================================================

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

    if grossloss:
        pf = grosswin / grossloss
    elif grosswin:
        pf = float("inf")
    else:
        pf = 0

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
# EXECUTION DIRECTE
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
        "\nBACKTEST",
        args.symbol,
        args.interval,
    )

    print(
        f"Score maximum : {MAX_SCORE}/100"
    )

    print(
        f"Seuil : {args.threshold:.0f}/100"
    )

    for k, v in stats.items():
        print(f"{k}: {v}")

    if len(trades):
        trades.to_csv(
            f"trades_{args.symbol}_{args.interval}.csv",
            index=False,
        )
