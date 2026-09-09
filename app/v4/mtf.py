import pandas as pd
from .indicators import enrich

def resample_ohlcv(df,rule):
    x=df.copy().set_index('open_time')
    return x.resample(rule).agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna().reset_index()

def snapshot(df):
    d=enrich(df); x=d.iloc[-1]
    return {'bullish':bool(x.close>x.ema20>x.ema50 and x.ema50>x.ema200),
            'bearish':bool(x.close<x.ema20<x.ema50 and x.ema50<x.ema200),
            'rsi':float(x.rsi) if pd.notna(x.rsi) else None}

def build_mtf(df5):
    frames={'5m':df5,'15m':resample_ohlcv(df5,'15min'),'1h':resample_ohlcv(df5,'1h'),'4h':resample_ohlcv(df5,'4h')}
    return {k:(enrich(v),snapshot(v)) for k,v in frames.items()}
