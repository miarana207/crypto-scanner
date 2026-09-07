"""
Sources de données OHLCV par classe d'actifs.

- klines_binance (dans backtest.py)  : crypto, API publique Binance
- klines_yahoo (ici)                 : fallback actions/indices/matières
  premières, endpoint non-officiel Yahoo Finance
- klines_twelvedata (ici)            : actions/indices/matières premières,
  API officielle gratuite (nécessite TWELVEDATA_API_KEY)
- klines_finnhub_forex (ici)         : forex, API officielle gratuite
  (nécessite FINNHUB_API_KEY)

⚠️ Aucune de ces fonctions n'a pu être testée en conditions réelles dans
l'environnement où ce fichier a été écrit (pas d'accès réseau) — seul le
parsing du format de réponse a été validé avec des réponses simulées
conformes à la documentation de chaque fournisseur. Teste chacune
isolément avant de tout automatiser (voir README_finnhub_twelvedata.md).
"""
import os
import time
import requests
import pandas as pd


# ---------------------------------------------------------------------------
# Yahoo Finance (non-officiel) — fallback pour actions/indices/matières 1ères
# ---------------------------------------------------------------------------
_YF_CONFIG = {
    "1m":  ("1m", "7d"),
    "5m":  ("5m", "60d"),
    "15m": ("15m", "60d"),
    "30m": ("30m", "60d"),
    "1h":  ("60m", "730d"),
    "1d":  ("1d", "10y"),
}


def klines_yahoo(symbol, interval="15m", limit=500, **kwargs):
    yf_interval, yf_range = _YF_CONFIG.get(interval, ("15m", "60d"))
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": yf_range, "interval": yf_interval}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; scanner-perso/1.0)"}

    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    payload = r.json()

    result_list = payload.get("chart", {}).get("result")
    if not result_list:
        err = payload.get("chart", {}).get("error")
        raise ValueError(f"Yahoo Finance n'a rien renvoyé pour {symbol}: {err}")
    result = result_list[0]

    timestamps = result.get("timestamp")
    if not timestamps:
        raise ValueError(f"Aucune bougie disponible pour {symbol} sur cette période")

    quote = result["indicators"]["quote"][0]
    df = pd.DataFrame({
        "open_time": pd.to_datetime(timestamps, unit="s", utc=True),
        "open": quote["open"], "high": quote["high"], "low": quote["low"],
        "close": quote["close"], "volume": quote["volume"],
    }).dropna().reset_index(drop=True)

    return df.tail(limit).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Twelve Data — actions/indices/matières premières (API officielle, clé requise)
# ---------------------------------------------------------------------------
_TD_INTERVAL_MAP = {
    "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "1d": "1day",
}


def klines_twelvedata(symbol, interval="15m", limit=500, **kwargs):
    api_key = os.environ.get("TWELVEDATA_API_KEY")
    if not api_key:
        raise RuntimeError("TWELVEDATA_API_KEY manquant (variable d'environnement)")

    td_interval = _TD_INTERVAL_MAP.get(interval, "15min")
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol, "interval": td_interval,
        "outputsize": min(limit, 5000), "apikey": api_key,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    if data.get("status") == "error":
        raise ValueError(f"Twelve Data erreur pour {symbol}: {data.get('message')}")
    values = data.get("values")
    if not values:
        raise ValueError(f"Twelve Data n'a renvoyé aucune donnée pour {symbol}")

    df = pd.DataFrame(values)
    df["open_time"] = pd.to_datetime(df["datetime"], utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    # Twelve Data renvoie généralement du plus récent au plus ancien -> on remet dans l'ordre chronologique
    df = df.sort_values("open_time").reset_index(drop=True)

    return df[["open_time", "open", "high", "low", "close", "volume"]].tail(limit).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Finnhub — forex (API officielle, clé requise)
# ---------------------------------------------------------------------------
_FH_RESOLUTION_MAP = {
    "1m": ("1", 1), "5m": ("5", 5), "15m": ("15", 15), "30m": ("30", 30),
    "1h": ("60", 60), "1d": ("D", 1440),
}


def klines_finnhub_forex(symbol, interval="15m", limit=500, **kwargs):
    """symbol au format Finnhub, ex: "OANDA:EUR_USD" """
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        raise RuntimeError("FINNHUB_API_KEY manquant (variable d'environnement)")

    resolution, minutes_per_bar = _FH_RESOLUTION_MAP.get(interval, ("15", 15))
    now = int(time.time())
    span_seconds = minutes_per_bar * 60 * (limit + 20)  # marge de sécurité
    start = now - span_seconds

    url = "https://finnhub.io/api/v1/forex/candle"
    params = {"symbol": symbol, "resolution": resolution, "from": start, "to": now, "token": api_key}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    if data.get("s") != "ok":
        raise ValueError(f"Finnhub n'a pas de données pour {symbol} (statut: {data.get('s')})")

    df = pd.DataFrame({
        "open_time": pd.to_datetime(data["t"], unit="s", utc=True),
        "open": data["o"], "high": data["h"], "low": data["l"],
        "close": data["c"], "volume": data["v"],
    })
    return df.tail(limit).reset_index(drop=True)
