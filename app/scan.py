"""V4.2 — Scan et scoring des actifs."""

import math
import time
import pandas as pd

from backtest import indicators, score, best_direction, calculate_trade_levels, signal_quality, BREAKOUT_LOOKBACK, BREAKOUT_ATR_MULT, MIN_RR

DEFAULT_THRESHOLD = 75
MIN_CANDLES = 120


def safe_float(value, default=float("nan")):
    try:
        x = float(value)
        return default if math.isnan(x) else x
    except (TypeError, ValueError):
        return default


def format_relvol(value):
    return "n/d" if pd.isna(value) else f"{float(value):.2f}"


def empty_result_columns():
    return ["symbol","provider","close","score_long","score_short","direction","best_score","status","quality","missing","trend_pts","ema_pts","rsi_pts","volume_pts","breakout_pts","entry","stop_loss","take_profit_1","take_profit_2","risk","rr_tp1","rr_tp2","atr","rsi","relvol","trend1h","breakout_ok","volume_ok","volume_available","volume_status","last_candle","age_minutes"]


def scan(symbols, fetcher, interval="15m", limit=1000, threshold=DEFAULT_THRESHOLD, pause=0.0, fallback_fetcher=None, provider_name="unknown", fallback_provider_name=None, asset_type="stock"):
    results = []; requested = analyzed = error_count = insufficient = 0
    for symbol in symbols:
        requested += 1
        try:
            df = fetcher(symbol, interval=interval, limit=limit, asset_type=asset_type) if getattr(fetcher, "_router", False) else fetcher(symbol, interval=interval, limit=limit)
            used_provider = getattr(df, "attrs", {}).get("provider", provider_name)
        except Exception as primary_error:
            if fallback_fetcher is None:
                error_count += 1
                print(f"[{symbol}] erreur source {provider_name}: {primary_error}")
                continue
            try:
                df = fallback_fetcher(symbol, interval=interval, limit=limit)
                used_provider = getattr(df, "attrs", {}).get("provider", fallback_provider_name or "fallback")
            except Exception as fallback_error:
                error_count += 1
                print(f"[{symbol}] sources échouées: {primary_error} | {fallback_error}")
                continue
        if df is None or df.empty:
            error_count += 1; continue
        required = {"open_time","open","high","low","close","volume"}
        if not required.issubset(df.columns):
            error_count += 1; continue
        df = df.copy(); df["open_time"] = pd.to_datetime(df["open_time"], utc=True, errors="coerce")
        for col in ["open","high","low","close","volume"]: df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["open_time","open","high","low","close"]).sort_values("open_time").drop_duplicates("open_time", keep="last").reset_index(drop=True)
        if len(df) < MIN_CANDLES:
            insufficient += 1; continue
        d = indicators(df)
        if len(d) < MIN_CANDLES:
            insufficient += 1; continue
        d["prev_high"] = d["high"].rolling(BREAKOUT_LOOKBACK).max().shift(1)
        d["prev_low"] = d["low"].rolling(BREAKOUT_LOOKBACK).min().shift(1)
        d["breakout_long"] = d["close"] > d["prev_high"] + d["atr"] * BREAKOUT_ATR_MULT
        d["breakout_short"] = d["close"] < d["prev_low"] - d["atr"] * BREAKOUT_ATR_MULT
        row = d.iloc[-1]
        details = score(row, return_details=True)
        sl = details["long"]; ss = details["short"]
        direction = best_direction(sl["score"], ss["score"]); best_score = max(sl["score"], ss["score"])
        selected = sl if direction == "LONG" else ss
        breakout_ok = bool(details["breakout_long"] if direction == "LONG" else details["breakout_short"])
        volume_available = bool(details["volume_available"])
        volume_ok = bool(details["volume_ok"])
        volume_status = "VOLUME_CONFIRMED" if volume_available else "VOLUME_UNAVAILABLE"
        levels = calculate_trade_levels(row["close"], row["atr"], direction)
        quality = signal_quality(best_score, breakout_ok, volume_ok, volume_available, levels["rr_tp2"])
        strong = best_score >= threshold and breakout_ok and (volume_ok or not volume_available) and pd.notna(levels["rr_tp2"]) and levels["rr_tp2"] >= MIN_RR
        status = "SIGNAL FORT" if strong else ("ATTENTE" if best_score >= threshold else "SOUS SEUIL")
        missing = []
        if best_score < threshold: missing.append(f"SCORE < {threshold:.0f}")
        if not breakout_ok: missing.append("BREAKOUT")
        if volume_available and not volume_ok: missing.append("VOLUME")
        if pd.isna(levels["rr_tp2"]) or levels["rr_tp2"] < MIN_RR: missing.append("R:R")
        now = pd.Timestamp.now(tz="UTC"); last = pd.Timestamp(row["open_time"]); age = (now-last).total_seconds()/60
        results.append({"symbol":symbol,"provider":used_provider,"close":row["close"],"score_long":sl["score"],"score_short":ss["score"],"direction":direction,"best_score":best_score,"status":status,"quality":quality,"missing":" + ".join(missing) if missing else "aucune","trend_pts":selected["trend_pts"],"ema_pts":selected["ema_pts"],"rsi_pts":selected["rsi_pts"],"volume_pts":selected["volume_pts"],"breakout_pts":selected["breakout_pts"],**levels,"atr":row["atr"],"rsi":row["rsi"],"relvol":row["relvol"],"trend1h":row["trend1h"],"breakout_ok":breakout_ok,"volume_ok":volume_ok,"volume_available":volume_available,"volume_status":volume_status,"last_candle":last,"age_minutes":age})
        analyzed += 1
        if pause: time.sleep(pause)
    print(f"Couverture: {analyzed}/{requested} analysés — erreurs={error_count}, insuffisants={insufficient}")
    if not results: return pd.DataFrame(columns=empty_result_columns())
    return pd.DataFrame(results).sort_values(["status","best_score","quality"], ascending=[True,False,True]).reset_index(drop=True)
