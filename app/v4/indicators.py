import numpy as np
import pandas as pd

def ema(s,n): return s.ewm(span=n,adjust=False).mean()
def rsi(s,n=14):
    d=s.diff(); g=d.clip(lower=0).rolling(n).mean(); l=(-d.clip(upper=0)).rolling(n).mean()
    rs=g/l.replace(0,np.nan); return 100-100/(1+rs)
def atr(df,n=14):
    p=df.close.shift(1)
    tr=pd.concat([(df.high-df.low),(df.high-p).abs(),(df.low-p).abs()],axis=1).max(axis=1)
    return tr.rolling(n).mean()
def macd(s):
    m=ema(s,12)-ema(s,26); sig=ema(m,9); return m,sig,m-sig

def enrich(df):
    d=df.copy().sort_values('open_time').reset_index(drop=True)
    for c in ['open','high','low','close','volume']: d[c]=pd.to_numeric(d[c],errors='coerce')
    d['ema20']=ema(d.close,20); d['ema50']=ema(d.close,50); d['ema200']=ema(d.close,200)
    d['rsi']=rsi(d.close); d['atr']=atr(d); d['atr_pct']=d.atr/d.close*100
    d['relvol']=d.volume/d.volume.rolling(20).mean(); d['roc']=d.close.pct_change(10)*100
    d['macd'],d['macd_signal'],d['macd_hist']=macd(d.close)
    d['high20']=d.high.rolling(20).max().shift(1); d['low20']=d.low.rolling(20).min().shift(1)
    d['ema20_slope']=d.ema20.pct_change(5)*100; d['ema50_slope']=d.ema50.pct_change(5)*100
    return d
