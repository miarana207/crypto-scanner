"""
V4.2 — Indicateurs, scoring et backtest.

La logique de scoring V4.1.1 est conservée, avec une gestion explicite
VOLUME_CONFIRMED / VOLUME_UNAVAILABLE / VOLUME_INVALID.
"""

import math
import os

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

SCORE_WEIGHTS = {"trend": 25.0, "ema": 20.0, "rsi": 15.0, "volume": 15.0, "breakout": 25.0}
SCORE_MAX = 100.0

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14
ATR_PERIOD = 14
VOLUME_LOOKBACK = 20
BREAKOUT_LOOKBACK = 20
BREAKOUT_ATR_MULT = 0.20
BREAKOUT_FULL_ATR = 0.50

SL_ATR_MULT = 1.50
TP1_ATR_MULT = 2.25
TP2_ATR_MULT = 3.00
MIN_RR = 1.50

MAX_HOLDING_BARS = 96
DEFAULT_FEE_RATE = 0.0005
DEFAULT_SLIPPAGE = 0.0002


def normalize_timestamp_series(series):
    out = pd.to_datetime(series, utc=True, errors="coerce")
    try:
        return out.dt.as_unit("ns")
    except AttributeError:
        return out


def normalize_timestamp(value):
    out = pd.to_datetime(value, utc=True, errors="coerce")
    try:
        return out.as_unit("ns")
    except AttributeError:
        return out


def safe_float(value, default=float("nan")):
    try:
        x = float(value)
        return default if math.isnan(x) else x
    except (TypeError, ValueError):
        return default


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def calculate_rsi(series, period=RSI_PERIOD):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.mask((avg_gain == 0) & (avg_loss > 0), 0)
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100)
    rsi = rsi.mask((avg_gain == 0) & (avg_loss == 0), 50)
    return pd.to_numeric(rsi, errors="coerce")


def calculate_atr(df, period=ATR_PERIOD):
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def indicators(df):
    out = df.copy()
    if "volume" not in out.columns:
        out["volume"] = float("nan")
    out["open_time"] = normalize_timestamp_series(out["open_time"])
    for col in ["open", "high", "low", "close", "volume"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["open_time", "open", "high", "low", "close"])
    out = out.sort_values("open_time").drop_duplicates("open_time", keep="last").reset_index(drop=True)
    out["ema20"] = out["close"].ewm(span=EMA_FAST, adjust=False, min_periods=EMA_FAST).mean()
    out["ema50"] = out["close"].ewm(span=EMA_SLOW, adjust=False, min_periods=EMA_SLOW).mean()
    out["rsi"] = calculate_rsi(out["close"])
    out["atr"] = calculate_atr(out)
    out["prev_volume_mean"] = out["volume"].shift(1).rolling(VOLUME_LOOKBACK, min_periods=VOLUME_LOOKBACK).mean()
    out["relvol"] = out["volume"] / out["prev_volume_mean"].replace(0, float("nan"))
    out["volume_available"] = out["volume"].notna() & (out["volume"] >= 0)
    out["volume_invalid"] = out["volume"].notna() & (out["volume"] < 0)

    hourly = out.set_index("open_time")["close"].resample("1h").last().dropna().to_frame("close")
    hourly["ema20_1h"] = hourly["close"].ewm(span=EMA_FAST, adjust=False, min_periods=EMA_FAST).mean()
    hourly["ema50_1h"] = hourly["close"].ewm(span=EMA_SLOW, adjust=False, min_periods=EMA_SLOW).mean()
    hourly["trend1h"] = 0
    hourly.loc[(hourly["close"] > hourly["ema20_1h"]) & (hourly["ema20_1h"] > hourly["ema50_1h"]), "trend1h"] = 1
    hourly.loc[(hourly["close"] < hourly["ema20_1h"]) & (hourly["ema20_1h"] < hourly["ema50_1h"]), "trend1h"] = -1
    hourly["available_from"] = hourly.index + pd.Timedelta(hours=1)
    trend_map = hourly[["available_from", "trend1h"]].rename(columns={"available_from": "open_time"}).reset_index(drop=True)
    out["open_time"] = normalize_timestamp_series(out["open_time"])
    trend_map["open_time"] = normalize_timestamp_series(trend_map["open_time"])
    try:
        out = pd.merge_asof(out.sort_values("open_time"), trend_map.sort_values("open_time"), on="open_time", direction="backward")
    except Exception:
        out["trend1h"] = 0
    out["trend1h"] = pd.to_numeric(out.get("trend1h", 0), errors="coerce").fillna(0).astype(int)
    return out.reset_index(drop=True)


def _ema_points(close, ema20, ema50, atr, direction):
    if not all(pd.notna(x) for x in [close, ema20, ema50, atr]) or atr <= 0:
        return 0.0
    if direction == "LONG" and close > ema20 > ema50:
        strength = ((close - ema20) + (ema20 - ema50)) / atr
    elif direction == "SHORT" and close < ema20 < ema50:
        strength = ((ema20 - close) + (ema50 - ema20)) / atr
    else:
        return 0.0
    return 20.0 * clamp(strength / 0.75)


def _rsi_points(rsi, direction):
    if pd.isna(rsi):
        return 0.0
    if direction == "LONG" and rsi > 50:
        return 15.0 * clamp((rsi - 50) / 10)
    if direction == "SHORT" and rsi < 50:
        return 15.0 * clamp((50 - rsi) / 10)
    return 0.0


def _volume_points(relvol, direction, trend1h, volume_available):
    if not volume_available or pd.isna(relvol):
        return 0.0
    if direction == "LONG" and trend1h < 0:
        return 0.0
    if direction == "SHORT" and trend1h > 0:
        return 0.0
    return 15.0 * clamp((relvol - 1) / 1.5)


def _breakout_points(row, direction):
    close = safe_float(row.get("close")); atr = safe_float(row.get("atr"))
    level = safe_float(row.get("prev_high" if direction == "LONG" else "prev_low"))
    if any(pd.isna(x) for x in [close, atr, level]) or atr <= 0:
        return 0.0
    distance = close - level if direction == "LONG" else level - close
    if distance < atr * BREAKOUT_ATR_MULT:
        return 0.0
    return 25.0 * clamp(distance / (atr * BREAKOUT_FULL_ATR))


def score(row, return_details=False):
    close = safe_float(row.get("close")); trend1h = int(safe_float(row.get("trend1h"), 0))
    atr = safe_float(row.get("atr")); relvol = safe_float(row.get("relvol"))
    volume_available = bool(row.get("volume_available", False)) and not bool(row.get("volume_invalid", False))
    breakout_long = bool(row.get("breakout_long", False)); breakout_short = bool(row.get("breakout_short", False))
    results = {}
    for direction in ("LONG", "SHORT"):
        trend_pts = 25.0 if ((direction == "LONG" and trend1h > 0) or (direction == "SHORT" and trend1h < 0)) else 0.0
        ema_pts = _ema_points(close, safe_float(row.get("ema20")), safe_float(row.get("ema50")), atr, direction)
        rsi_pts = _rsi_points(safe_float(row.get("rsi")), direction)
        volume_pts = _volume_points(relvol, direction, trend1h, volume_available)
        breakout_pts = _breakout_points(row, direction)
        raw = trend_pts + ema_pts + rsi_pts + volume_pts + breakout_pts
        effective_max = 100.0 if volume_available else 85.0
        final = raw if volume_available else raw * (100.0 / effective_max)
        results[direction] = {"trend_pts": trend_pts, "ema_pts": ema_pts, "rsi_pts": rsi_pts, "volume_pts": volume_pts, "breakout_pts": breakout_pts, "score": min(final, 100.0)}
    best = max(results["LONG"]["score"], results["SHORT"]["score"])
    details = {
        "long": results["LONG"], "short": results["SHORT"],
        "volume_ok": bool(volume_available and pd.notna(relvol) and relvol >= 1.5),
        "volume_available": volume_available,
        "volume_status": "VOLUME_CONFIRMED" if volume_available else "VOLUME_UNAVAILABLE",
        "breakout_long": breakout_long, "breakout_short": breakout_short,
        "effective_max": 100.0,
    }
    if return_details:
        return details
    return best


def best_direction(score_long, score_short):
    if pd.isna(score_long) and pd.isna(score_short): return "-"
    if safe_float(score_long, -1) >= safe_float(score_short, -1): return "LONG"
    return "SHORT"


def calculate_trade_levels(close, atr, direction):
    close = safe_float(close); atr = safe_float(atr)
    if pd.isna(close) or pd.isna(atr) or atr <= 0 or direction not in ("LONG", "SHORT"):
        return {"entry": float("nan"), "stop_loss": float("nan"), "take_profit_1": float("nan"), "take_profit_2": float("nan"), "risk": float("nan"), "rr_tp1": float("nan"), "rr_tp2": float("nan")}
    risk = atr * SL_ATR_MULT
    if direction == "LONG":
        sl = close - risk; tp1 = close + atr * TP1_ATR_MULT; tp2 = close + atr * TP2_ATR_MULT
    else:
        sl = close + risk; tp1 = close - atr * TP1_ATR_MULT; tp2 = close - atr * TP2_ATR_MULT
    return {"entry": close, "stop_loss": sl, "take_profit_1": tp1, "take_profit_2": tp2, "risk": risk, "rr_tp1": (tp1-close if direction == "LONG" else close-tp1)/risk, "rr_tp2": (tp2-close if direction == "LONG" else close-tp2)/risk}


def signal_quality(score_value, breakout_ok, volume_ok, volume_available, rr_tp2):
    vol = volume_ok or not volume_available
    if score_value >= 80 and breakout_ok and vol and pd.notna(rr_tp2) and rr_tp2 >= 2.0: return "A"
    if score_value >= 75 and breakout_ok and vol and pd.notna(rr_tp2) and rr_tp2 >= 1.5: return "B"
    if score_value >= 65: return "C"
    return "D"


def run(df, threshold=75, max_holding_bars=MAX_HOLDING_BARS, fee_rate=DEFAULT_FEE_RATE, slippage=DEFAULT_SLIPPAGE):
    data = indicators(df)
    prev_high = data["high"].rolling(BREAKOUT_LOOKBACK).max().shift(1)
    prev_low = data["low"].rolling(BREAKOUT_LOOKBACK).min().shift(1)
    data["prev_high"] = prev_high; data["prev_low"] = prev_low
    data["breakout_long"] = data["close"] > data["prev_high"] + data["atr"] * BREAKOUT_ATR_MULT
    data["breakout_short"] = data["close"] < data["prev_low"] - data["atr"] * BREAKOUT_ATR_MULT
    trades = []; i = 0
    while i < len(data) - 1:
        row = data.iloc[i]
        details = score(row, return_details=True)
        sl_score = details["long"]["score"]; ss_score = details["short"]["score"]
        direction = best_direction(sl_score, ss_score); best_score = max(sl_score, ss_score)
        breakout_ok = bool(details["breakout_long"] if direction == "LONG" else details["breakout_short"])
        volume_ok = bool(details["volume_ok"]); volume_available = bool(details["volume_available"])
        levels = calculate_trade_levels(row["close"], row["atr"], direction)
        if best_score < threshold or not breakout_ok or not (volume_ok or not volume_available) or pd.isna(levels["rr_tp2"]):
            i += 1; continue
        entry_row = data.iloc[i + 1]
        actual_entry = float(entry_row["open"]) * (1 + slippage if direction == "LONG" else 1 - slippage)
        risk_fraction = levels["risk"] / actual_entry if actual_entry else float("nan")
        stop = actual_entry - levels["risk"] if direction == "LONG" else actual_entry + levels["risk"]
        tp2 = actual_entry + levels["risk"] * levels["rr_tp2"] if direction == "LONG" else actual_entry - levels["risk"] * levels["rr_tp2"]
        exit_price = None; reason = "TIME"; bars_held = 0
        for j in range(i + 1, min(i + 1 + max_holding_bars, len(data))):
            bar = data.iloc[j]; bars_held += 1
            if direction == "LONG":
                if bar["low"] <= stop: exit_price = stop; reason = "SL"; break
                if bar["high"] >= tp2: exit_price = tp2; reason = "TP2"; break
            else:
                if bar["high"] >= stop: exit_price = stop; reason = "SL"; break
                if bar["low"] <= tp2: exit_price = tp2; reason = "TP2"; break
            exit_price = float(bar["close"])
        if exit_price is None: i += 1; continue
        gross = (exit_price - actual_entry) / actual_entry if direction == "LONG" else (actual_entry - exit_price) / actual_entry
        net = gross - fee_rate * 2
        r_multiple = net / risk_fraction if risk_fraction and not pd.isna(risk_fraction) else float("nan")
        trades.append({"signal_time": row["open_time"], "entry_time": entry_row["open_time"], "exit_time": data.iloc[min(i + bars_held, len(data)-1)]["open_time"], "direction": direction, "score": best_score, "entry": actual_entry, "stop_loss": stop, "take_profit_2": tp2, "exit_price": exit_price, "exit_reason": reason, "bars_held": bars_held, "gross_return": gross, "net_return": net, "r_multiple": r_multiple, "relvol": row["relvol"], "atr": row["atr"]})
        i = i + max(bars_held, 1) + 1
    return pd.DataFrame(trades)


def backtest_statistics(trades):
    if trades is None or trades.empty:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "profit_factor": 0.0, "net_return": 0.0, "expectancy": 0.0}
    wins = trades[trades["net_return"] > 0]; losses = trades[trades["net_return"] <= 0]
    gross_profit = wins["net_return"].sum(); gross_loss = abs(losses["net_return"].sum())
    return {"trades": len(trades), "wins": len(wins), "losses": len(losses), "win_rate": len(wins)/len(trades)*100, "profit_factor": float("inf") if gross_loss == 0 else gross_profit/gross_loss, "net_return": trades["net_return"].sum(), "expectancy": trades["net_return"].mean()}
