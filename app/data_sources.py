"""
V4.2 — Sources de données + routeur intelligent multi-fournisseurs.

Fournisseurs : Binance, Yahoo Finance, Finnhub, Twelve Data.

Principes :
- OHLC valide obligatoire.
- Les bougies en formation sont exclues.
- Le volume manquant n'est JAMAIS transformé en 0.
- Le routeur peut changer de fournisseur selon l'actif, l'intervalle,
  la fraîcheur et la disponibilité du volume.
- Un cache mémoire évite les appels identiques pendant un même run.
- Twelve Data est protégé par un budget journalier configurable.
"""

import os
import time
from datetime import datetime, timezone
from threading import Lock

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")

YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
TWELVE_DATA_BASE = "https://api.twelvedata.com/time_series"
FINNHUB_BASE = "https://finnhub.io/api/v1/stock/candle"
BINANCE_BASE = os.getenv("BINANCE_DATA_URL", "https://data-api.binance.vision")
REQUEST_TIMEOUT = int(os.getenv("DATA_REQUEST_TIMEOUT", "30"))

STANDARD_COLUMNS = ["open_time", "open", "high", "low", "close", "volume"]

VOLUME_CONFIRMED = "VOLUME_CONFIRMED"
VOLUME_UNAVAILABLE = "VOLUME_UNAVAILABLE"
VOLUME_INVALID = "VOLUME_INVALID"

_YF_CONFIG = {
    "1m": ("7d", "1m"),
    "5m": ("60d", "5m"),
    "15m": ("60d", "15m"),
    "30m": ("60d", "30m"),
    "1h": ("730d", "1h"),
    "4h": ("730d", "1h"),
    "1d": ("10y", "1d"),
}

_TD_INTERVALS = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1day",
}

_FH_RESOLUTION = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "4h": "60",
    "1d": "D",
}

_CACHE = {}
_CACHE_LOCK = Lock()


def interval_to_seconds(interval):
    interval = str(interval).lower().strip()
    unit = interval[-1:]
    try:
        value = int(interval[:-1])
    except ValueError:
        return 60
    return value * {"m": 60, "h": 3600, "d": 86400, "w": 604800}.get(unit, 60)


def keep_completed_candles(df, interval):
    if df is None or df.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)
    out = df.copy()
    out["open_time"] = pd.to_datetime(out["open_time"], utc=True, errors="coerce")
    out = out.dropna(subset=["open_time"])
    now = pd.Timestamp.now(tz="UTC")
    completed = (now - out["open_time"]).dt.total_seconds() >= interval_to_seconds(interval)
    out = out.loc[completed].copy()
    out = out.sort_values("open_time").drop_duplicates("open_time", keep="last").reset_index(drop=True)
    return out


def normalize_ohlcv(df, interval="15m", limit=1000):
    if df is None or df.empty:
        return pd.DataFrame(columns=STANDARD_COLUMNS)
    out = df.copy()
    for col in STANDARD_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA if col == "volume" else float("nan")
    out["open_time"] = pd.to_datetime(out["open_time"], utc=True, errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["open_time", "open", "high", "low", "close"])
    out = out[(out["high"] >= out[["open", "close"]].max(axis=1)) &
              (out["low"] <= out[["open", "close"]].min(axis=1))]
    out = out.sort_values("open_time").drop_duplicates("open_time", keep="last")
    out = keep_completed_candles(out, interval)
    return out[STANDARD_COLUMNS].tail(int(limit)).reset_index(drop=True)


def volume_status(df):
    if df is None or df.empty or "volume" not in df.columns:
        return VOLUME_UNAVAILABLE
    v = pd.to_numeric(df["volume"], errors="coerce")
    if v.notna().sum() == 0:
        return VOLUME_UNAVAILABLE
    if (v.dropna() < 0).any():
        return VOLUME_INVALID
    return VOLUME_CONFIRMED


def data_quality(df, interval="15m", min_candles=120):
    if df is None or df.empty:
        return {"valid": False, "candles": 0, "volume_status": VOLUME_UNAVAILABLE, "fresh": False}
    out = normalize_ohlcv(df, interval, max(len(df), min_candles))
    if out.empty:
        return {"valid": False, "candles": 0, "volume_status": VOLUME_UNAVAILABLE, "fresh": False}
    now = pd.Timestamp.now(tz="UTC")
    age = (now - out["open_time"].iloc[-1]).total_seconds()
    fresh_limit = max(interval_to_seconds(interval) * 4, 15 * 60)
    return {
        "valid": len(out) >= min_candles,
        "candles": len(out),
        "volume_status": volume_status(out),
        "fresh": age <= fresh_limit,
        "age_seconds": age,
    }


def _cache_get(key):
    ttl = int(os.getenv("DATA_CACHE_TTL", "45"))
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if item and time.time() - item[0] <= ttl:
            return item[1].copy()
        if item:
            _CACHE.pop(key, None)
    return None


def _cache_put(key, df):
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), df.copy())


def _request_json(url, params=None, headers=None):
    resp = requests.get(url, params=params, headers=headers or {}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json(), resp.headers


def klines_yahoo(symbol, interval="15m", limit=1000):
    cache_key = ("yahoo", symbol, interval, int(limit))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    range_value, yf_interval = _YF_CONFIG.get(interval, ("60d", interval))
    params = {"range": range_value, "interval": yf_interval, "includePrePost": "false", "events": "div,splits"}
    headers = {"User-Agent": "Mozilla/5.0 scanner-v4.2"}
    data, _ = _request_json(YAHOO_BASE + str(symbol), params=params, headers=headers)
    result = data.get("chart", {}).get("result") or []
    if not result:
        raise RuntimeError(f"Yahoo: aucune donnée pour {symbol}")
    result = result[0]
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    n = min(len(timestamps), len(quote.get("open", [])))
    rows = []
    for i in range(n):
        rows.append({
            "open_time": pd.to_datetime(timestamps[i], unit="s", utc=True),
            "open": quote.get("open", [None] * n)[i],
            "high": quote.get("high", [None] * n)[i],
            "low": quote.get("low", [None] * n)[i],
            "close": quote.get("close", [None] * n)[i],
            "volume": quote.get("volume", [None] * n)[i] if quote.get("volume") is not None else None,
        })
    out = normalize_ohlcv(pd.DataFrame(rows), interval, limit)
    _cache_put(cache_key, out)
    return out


def klines_twelvedata(symbol, interval="15m", limit=1000):
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError("TWELVE_DATA_API_KEY non configurée")
    cache_key = ("twelvedata", symbol, interval, int(limit))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    td_interval = _TD_INTERVALS.get(interval)
    if not td_interval:
        raise ValueError(f"Intervalle Twelve Data non supporté: {interval}")
    params = {
        "symbol": symbol,
        "interval": td_interval,
        "outputsize": min(int(limit), 5000),
        "apikey": TWELVE_DATA_API_KEY,
        "format": "JSON",
        "timezone": "UTC",
    }
    last_error = None
    for attempt in range(3):
        try:
            data, headers = _request_json(TWELVE_DATA_BASE, params=params)
            if data.get("status") == "error" or data.get("code"):
                raise RuntimeError(data.get("message") or str(data))
            values = data.get("values") or []
            if not values:
                raise RuntimeError(f"Twelve Data: aucune donnée pour {symbol}")
            out = pd.DataFrame(values).rename(columns={"datetime": "open_time"})
            out = normalize_ohlcv(out, interval, limit)
            _cache_put(cache_key, out)
            return out
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"Twelve Data {symbol}: {last_error}")


def klines_finnhub(symbol, interval="15m", limit=1000):
    if not FINNHUB_API_KEY:
        raise RuntimeError("FINNHUB_API_KEY non configurée")
    cache_key = ("finnhub", symbol, interval, int(limit))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    resolution = _FH_RESOLUTION.get(interval)
    if not resolution:
        raise ValueError(f"Intervalle Finnhub non supporté: {interval}")
    now = int(datetime.now(timezone.utc).timestamp())
    lookback = max(interval_to_seconds(interval) * max(int(limit) + 20, 150), 7 * 86400)
    start = now - lookback
    params = {"symbol": symbol, "resolution": resolution, "from": start, "to": now, "token": FINNHUB_API_KEY}
    data, _ = _request_json(FINNHUB_BASE, params=params)
    if data.get("s") != "ok":
        raise RuntimeError(f"Finnhub: {data.get('s', 'unknown')} pour {symbol}")
    ts = data.get("t") or []
    n = len(ts)
    rows = []
    for i in range(n):
        rows.append({
            "open_time": pd.to_datetime(ts[i], unit="s", utc=True),
            "open": (data.get("o") or [None] * n)[i],
            "high": (data.get("h") or [None] * n)[i],
            "low": (data.get("l") or [None] * n)[i],
            "close": (data.get("c") or [None] * n)[i],
            "volume": None,
        })
    out = normalize_ohlcv(pd.DataFrame(rows), interval, limit)
    _cache_put(cache_key, out)
    return out


def klines_binance(symbol, interval="5m", limit=1000):
    cache_key = ("binance", symbol, interval, int(limit))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    url = f"{BINANCE_BASE}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": min(int(limit), 1000)}
    data, _ = _request_json(url, params=params)
    rows = []
    for row in data:
        rows.append({
            "open_time": pd.to_datetime(row[0], unit="ms", utc=True),
            "open": row[1], "high": row[2], "low": row[3], "close": row[4], "volume": row[5],
        })
    out = normalize_ohlcv(pd.DataFrame(rows), interval, limit)
    _cache_put(cache_key, out)
    return out


class DataRouter:
    """Routeur multi-source avec score de qualité et enrichissement du volume."""

    def __init__(self, twelvedata_daily_budget=None):
        self.td_budget = int(twelvedata_daily_budget or os.getenv("TWELVEDATA_DAILY_BUDGET", "800"))
        self.td_used = 0
        self.td_day = datetime.now(timezone.utc).date()
        self.stats = {name: {"attempts": 0, "success": 0, "fail": 0} for name in ("binance", "yahoo", "finnhub", "twelvedata")}
        self.last_provider = None

    def _reset_td_day(self):
        today = datetime.now(timezone.utc).date()
        if today != self.td_day:
            self.td_day = today
            self.td_used = 0

    def _td_allowed(self):
        self._reset_td_day()
        return bool(TWELVE_DATA_API_KEY) and self.td_used < self.td_budget

    def _call(self, provider, symbol, interval, limit):
        funcs = {
            "binance": klines_binance,
            "yahoo": klines_yahoo,
            "finnhub": klines_finnhub,
            "twelvedata": klines_twelvedata,
        }
        if provider == "twelvedata" and not self._td_allowed():
            raise RuntimeError("Twelve Data: budget journalier atteint ou clé absente")
        self.stats[provider]["attempts"] += 1
        try:
            df = funcs[provider](symbol, interval=interval, limit=limit)
            if provider == "twelvedata":
                self.td_used += 1
            self.stats[provider]["success"] += 1
            return df
        except Exception:
            if provider == "twelvedata":
                # A failed request may still consume a credit on the provider;
                # reserve one budget unit conservatively.
                self.td_used += 1
            self.stats[provider]["fail"] += 1
            raise

    def _provider_order(self, asset_type, preferred=None, require_volume=True):
        if asset_type == "crypto":
            return ["binance"]
        if asset_type == "forex":
            base = ["finnhub", "twelvedata", "yahoo"]
        elif asset_type == "index":
            base = ["yahoo", "finnhub", "twelvedata"]
        elif asset_type == "commodity":
            base = ["yahoo", "twelvedata", "finnhub"]
        else:
            base = ["yahoo", "finnhub", "twelvedata"]
        if preferred in base:
            base.remove(preferred)
            base.insert(0, preferred)
        return base

    @staticmethod
    def _merge_volume(price_df, volume_df):
        if price_df.empty or volume_df.empty:
            return price_df
        p = price_df.copy()
        v = volume_df[["open_time", "volume"]].copy()
        v["volume"] = pd.to_numeric(v["volume"], errors="coerce")
        v = v.dropna(subset=["volume"])
        if v.empty:
            return p
        merged = p.drop(columns=["volume"]).merge(v, on="open_time", how="left")
        return merged[STANDARD_COLUMNS]

    def fetch(self, symbol, interval="15m", limit=1000, asset_type="stock", preferred=None, require_volume=True, symbol_map=None):
        """Fetch one asset using the cheapest/reliable source order.

        The router stops as soon as it has a valid fresh dataset with usable
        volume. If volume is missing, it tries another provider before accepting
        the best OHLC dataset. This prevents unnecessary Twelve Data calls.
        """
        symbol_map = symbol_map or {}
        order = self._provider_order(asset_type, preferred, require_volume)
        candidates = []
        errors = []

        for provider in order:
            provider_symbol = symbol_map.get(provider, symbol)
            try:
                df = self._call(provider, provider_symbol, interval, limit)
                q = data_quality(df, interval, min_candles=min(120, limit))
                if not q["valid"]:
                    errors.append(f"{provider}: données insuffisantes")
                    continue
                candidates.append((provider, provider_symbol, df, q))

                # A fresh dataset with confirmed volume is the ideal result.
                if q["fresh"] and q["volume_status"] == VOLUME_CONFIRMED:
                    break

                # For markets where volume is structurally unavailable, a fresh
                # OHLC dataset is already sufficient; don't waste quota.
                if q["fresh"] and not require_volume:
                    break
            except Exception as exc:
                errors.append(f"{provider}: {exc}")

        if not candidates:
            raise RuntimeError("Aucune source exploitable: " + " | ".join(errors))

        def rank(item):
            provider, _, _, q = item
            return (
                1 if q["fresh"] else 0,
                1 if q["volume_status"] == VOLUME_CONFIRMED else 0,
                q["candles"],
                1 if provider == preferred else 0,
            )

        candidates.sort(key=rank, reverse=True)
        provider, provider_symbol, df, q = candidates[0]

        # If OHLC is good but volume is missing/invalid, use a volume-confirmed
        # candidate already fetched. Otherwise the next provider would already
        # have been queried by the loop above when required.
        if require_volume and q["volume_status"] != VOLUME_CONFIRMED:
            for p2, _, df2, q2 in candidates:
                if q2["volume_status"] == VOLUME_CONFIRMED:
                    df = self._merge_volume(df, df2)
                    if volume_status(df) == VOLUME_CONFIRMED:
                        break

        df = normalize_ohlcv(df, interval, limit)
        df.attrs["provider"] = provider
        df.attrs["provider_symbol"] = provider_symbol
        df.attrs["volume_status"] = volume_status(df)
        df.attrs["router_candidates"] = [p for p, _, _, _ in candidates]
        df.attrs["router_errors"] = errors
        self.last_provider = provider
        return df
