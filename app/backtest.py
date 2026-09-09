"""
Moteur V4 :
- Binance pour la crypto
- Indicateurs techniques
- Score LONG / SHORT sur 100
- Fonction klines conservée pour compatibilité avec run_and_notify.py

Score maximum = 100
    Tendance 1H : 25
    EMA          : 20
    RSI          : 15
    Volume       : 15
    Breakout     : 25
"""

import os
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

BASE = os.getenv(
    "BINANCE_DATA_URL",
    "https://data-api.binance.vision"
)

BREAKOUT_BUFFER = 0.001

SCORE_WEIGHTS = {
    "trend": 25,
    "ema": 20,
    "rsi": 15,
    "volume": 15,
    "breakout": 25,
}

SCORE_MAX = sum(SCORE_WEIGHTS.values())


# ============================================================
# BINANCE
# ============================================================

def klines(
    symbol,
    interval="5m",
    limit=1000
):
    """
    Récupère les bougies Binance et exclut systématiquement
    la dernière bougie si elle est encore en formation.
    """

    url = f"{BASE}/api/v3/klines"

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": min(int(limit), 1000),
    }

    response = requests.get(
        url,
        params=params,
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if not data:
        raise ValueError(
            f"Binance n'a renvoyé aucune donnée pour {symbol}"
        )

    columns = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "taker_buy_base",
        "taker_buy_quote",
        "ignore",
    ]

    df = pd.DataFrame(
        data,
        columns=columns
    )

    df["open_time"] = pd.to_datetime(
        df["open_time"],
        unit="ms",
        utc=True
    )

    df["close_time"] = pd.to_datetime(
        df["close_time"],
        unit="ms",
        utc=True
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

    # --------------------------------------------------------
    # Exclusion de la bougie en formation
    # --------------------------------------------------------

    now = pd.Timestamp.now(
        tz="UTC"
    )

    df = df[
        df["close_time"] < now
    ].copy()

    df = (
        df.sort_values("open_time")
        .reset_index(drop=True)
    )

    return df[
        [
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    ].tail(
        int(limit)
    ).reset_index(drop=True)


# ============================================================
# INDICATEURS
# ============================================================

def indicators(df):
    """
    Calcule tous les indicateurs utilisés par V4.

    Important :
    le relvol compare le volume de la dernière bougie
    à la moyenne des 20 bougies précédentes.
    """

    d = df.copy()

    d["close"] = pd.to_numeric(
        d["close"],
        errors="coerce"
    )

    d["volume"] = pd.to_numeric(
        d["volume"],
        errors="coerce"
    )

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    d["ema20"] = (
        d["close"]
        .ewm(
            span=20,
            adjust=False
        )
        .mean()
    )

    d["ema50"] = (
        d["close"]
        .ewm(
            span=50,
            adjust=False
        )
        .mean()
    )

    # --------------------------------------------------------
    # RSI 14
    # --------------------------------------------------------

    delta = d["close"].diff()

    gain = (
        delta.clip(lower=0)
        .rolling(14)
        .mean()
    )

    loss = (
        -delta.clip(upper=0)
        .rolling(14)
        .mean()
    )

    rs = gain / loss.replace(
        0,
        float("nan")
    )

    rsi = 100 - (
        100 / (1 + rs)
    )

    # Cas particuliers
    rsi = rsi.mask(
        (loss == 0) & (gain > 0),
        100
    )

    rsi = rsi.mask(
        (gain == 0) & (loss > 0),
        0
    )

    rsi = rsi.mask(
        (gain == 0) & (loss == 0),
        50
    )

    d["rsi"] = rsi

    # --------------------------------------------------------
    # RELATIVE VOLUME
    # --------------------------------------------------------
    #
    # IMPORTANT :
    # On exclut la bougie actuelle du calcul de la moyenne.
    #
    # Exemple :
    #
    # volume actuel = 150
    # moyenne des 20 précédentes = 100
    # relvol = 1.50
    #
    # --------------------------------------------------------

    previous_volume_mean = (
        d["volume"]
        .shift(1)
        .rolling(20)
        .mean()
    )

    d["relvol"] = (
        d["volume"]
        / previous_volume_mean.replace(
            0,
            float("nan")
        )
    )

    # --------------------------------------------------------
    # TENDANCE 1H
    # --------------------------------------------------------

    temp = (
        d.set_index("open_time")
        [["close"]]
        .resample("1h")
        .last()
        .dropna()
    )

    if not temp.empty:

        temp["ema20_1h"] = (
            temp["close"]
            .ewm(
                span=20,
                adjust=False
            )
            .mean()
        )

        temp["ema50_1h"] = (
            temp["close"]
            .ewm(
                span=50,
                adjust=False
            )
            .mean()
        )

        temp["trend1h"] = 0

        long_condition = (
            (temp["close"] > temp["ema20_1h"])
            &
            (temp["ema20_1h"] > temp["ema50_1h"])
        )

        short_condition = (
            (temp["close"] < temp["ema20_1h"])
            &
            (temp["ema20_1h"] < temp["ema50_1h"])
        )

        temp.loc[
            long_condition,
            "trend1h"
        ] = 1

        temp.loc[
            short_condition,
            "trend1h"
        ] = -1

        trend_map = temp[
            ["trend1h"]
        ]

        d = pd.merge_asof(
            d.sort_values("open_time"),
            trend_map.reset_index().sort_values(
                "open_time"
            ),
            on="open_time",
            direction="backward"
        )

    else:
        d["trend1h"] = 0

    d["trend1h"] = (
        d["trend1h"]
        .fillna(0)
        .astype(int)
    )

    return d


# ============================================================
# SCORE
# ============================================================

def score(
    row,
    return_details=False
):
    """
    Calcule séparément le score LONG et SHORT.
    """

    relvol = row.get(
        "relvol",
        float("nan")
    )

    rsi = row.get(
        "rsi",
        float("nan")
    )

    trend1h = row.get(
        "trend1h",
        0
    )

    close = row.get(
        "close",
        float("nan")
    )

    ema20 = row.get(
        "ema20",
        float("nan")
    )

    ema50 = row.get(
        "ema50",
        float("nan")
    )

    breakout_long = bool(
        row.get(
            "breakout_long",
            False
        )
    )

    breakout_short = bool(
        row.get(
            "breakout_short",
            False
        )
    )

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    trend_long = (
        SCORE_WEIGHTS["trend"]
        if trend1h == 1
        else 0
    )

    trend_short = (
        SCORE_WEIGHTS["trend"]
        if trend1h == -1
        else 0
    )

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    ema_long = (
        SCORE_WEIGHTS["ema"]
        if (
            pd.notna(close)
            and pd.notna(ema20)
            and pd.notna(ema50)
            and close > ema20 > ema50
        )
        else 0
    )

    ema_short = (
        SCORE_WEIGHTS["ema"]
        if (
            pd.notna(close)
            and pd.notna(ema20)
            and pd.notna(ema50)
            and close < ema20 < ema50
        )
        else 0
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi_long = (
        SCORE_WEIGHTS["rsi"]
        if (
            pd.notna(rsi)
            and rsi >= 55
        )
        else 0
    )

    rsi_short = (
        SCORE_WEIGHTS["rsi"]
        if (
            pd.notna(rsi)
            and rsi <= 45
        )
        else 0
    )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    volume_valid = (
        pd.notna(relvol)
        and float(relvol) >= 1.5
    )

    volume_long = (
        SCORE_WEIGHTS["volume"]
        if (
            volume_valid
            and trend1h >= 0
        )
        else 0
    )

    volume_short = (
        SCORE_WEIGHTS["volume"]
        if (
            volume_valid
            and trend1h <= 0
        )
        else 0
    )

    # --------------------------------------------------------
    # BREAKOUT
    # --------------------------------------------------------

    breakout_long_pts = (
        SCORE_WEIGHTS["breakout"]
        if breakout_long
        else 0
    )

    breakout_short_pts = (
        SCORE_WEIGHTS["breakout"]
        if breakout_short
        else 0
    )

    # --------------------------------------------------------
    # TOTALS
    # --------------------------------------------------------

    long_score = (
        trend_long
        + ema_long
        + rsi_long
        + volume_long
        + breakout_long_pts
    )

    short_score = (
        trend_short
        + ema_short
        + rsi_short
        + volume_short
        + breakout_short_pts
    )

    details = {
        "long": {
            "trend": trend_long,
            "ema": ema_long,
            "rsi": rsi_long,
            "volume": volume_long,
            "breakout": breakout_long_pts,
        },
        "short": {
            "trend": trend_short,
            "ema": ema_short,
            "rsi": rsi_short,
            "volume": volume_short,
            "breakout": breakout_short_pts,
        },
        "volume_ok": volume_valid,
        "breakout_long": breakout_long,
        "breakout_short": breakout_short,
    }

    if return_details:
        return (
            long_score,
            short_score,
            details
        )

    return (
        long_score,
        short_score
    )


# ============================================================
# BACKTEST SIMPLE
# ============================================================

def run(
    df,
    threshold=75
):
    """
    Backtest simple cohérent avec le signal V4.

    Une entrée n'est autorisée que si :
        score >= threshold
        + breakout
        + volume
    """

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

    d["breakout_long"] = (
        d["close"]
        >
        d["prev_high"] * (
            1 + BREAKOUT_BUFFER
        )
    )

    d["breakout_short"] = (
        d["close"]
        <
        d["prev_low"] * (
            1 - BREAKOUT_BUFFER
        )
    )

    trades = []

    for i in range(
        len(d)
    ):

        row = d.iloc[i]

        long_score, short_score, details = score(
            row,
            return_details=True
        )

        if long_score >= short_score:
            direction = "LONG"
            best_score = long_score
            breakout_ok = details[
                "breakout_long"
            ]
        else:
            direction = "SHORT"
            best_score = short_score
            breakout_ok = details[
                "breakout_short"
            ]

        volume_ok = details[
            "volume_ok"
        ]

        strong = (
            best_score >= threshold
            and breakout_ok
            and volume_ok
        )

        if strong:
            trades.append(
                {
                    "open_time": row[
                        "open_time"
                    ],
                    "close": row[
                        "close"
                    ],
                    "direction": direction,
                    "score": best_score,
                    "relvol": row[
                        "relvol"
                    ],
                }
            )

    return pd.DataFrame(
        trades
    )


if __name__ == "__main__":
    print(
        "Module backtest.py chargé correctement."
    )
