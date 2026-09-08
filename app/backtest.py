import os, argparse, requests, math
from datetime import datetime, timezone
import pandas as pd
from dotenv import load_dotenv
load_dotenv()

BASE=os.getenv("BINANCE_DATA_URL","https://api.binance.com")

def klines(symbol, interval="5m", limit=1000, start=None, end=None):
    p={"symbol":symbol.upper(),"interval":interval,"limit":limit}
    if start: p["startTime"]=int(start.timestamp()*1000)
    if end: p["endTime"]=int(end.timestamp()*1000)
    r=requests.get(BASE+"/api/v3/klines",params=p,timeout=30)
    r.raise_for_status()
    x=r.json()
    cols=["open_time","open","high","low","close","volume","close_time","qav","trades","tbv","tqv","ignore"]
    df=pd.DataFrame(x,columns=cols)
    for c in ["open","high","low","close","volume"]: df[c]=df[c].astype(float)
    df["open_time"]=pd.to_datetime(df["open_time"],unit="ms",utc=True)
    return df

def indicators(df):
    d=df.copy()
    d["ema20"]=d.close.ewm(span=20,adjust=False).mean()
    d["ema50"]=d.close.ewm(span=50,adjust=False).mean()
    delta=d.close.diff()
    gain=delta.clip(lower=0).rolling(14).mean()
    loss=(-delta.clip(upper=0)).rolling(14).mean()
    rs=gain/loss.replace(0,float("nan"))
    d["rsi"]=100-(100/(1+rs))
    d["relvol"]=d.volume/d.volume.rolling(20).mean()
    d["trend1h"]=0
    # Approximate 1H trend from 5m data using 12-bar EMA aggregation.
    h=d.set_index("open_time").close.resample("1h").last().dropna()
    he20=h.ewm(span=20,adjust=False).mean()
    he50=h.ewm(span=50,adjust=False).mean()
    ht=pd.Series(0,index=h.index)
    ht[(h>he20)&(he20>he50)]=1
    ht[(h<he20)&(he20<he50)]=-1
    d["trend1h"]=ht.reindex(d.open_time,method="ffill").values
    return d

def score(row):
    L=S=0
    if row.trend1h==1:L+=25
    if row.trend1h==-1:S+=25
    if row.close>row.ema20>row.ema50:L+=15
    if row.close<row.ema20<row.ema50:S+=15
    if 50<=row.rsi<=70:L+=10
    if 30<=row.rsi<=50:S+=10
    # FIX: le volume relatif confirme la direction déjà suggérée par la
    # tendance (trend1h), au lieu de gonfler artificiellement les deux
    # scores à la fois dès qu'il y a du volume.
    if row.relvol>=1.5:
        if row.trend1h==1:L+=15
        elif row.trend1h==-1:S+=15
    prev_high=row.prev_high
    prev_low=row.prev_low
    if row.close>prev_high:L+=15
    if row.close<prev_low:S+=15
    return L,S

def run(df, fee=0.001, slippage=0.0002, stop_pct=0.01, target_pct=0.02, threshold=75):
    d=indicators(df)
    d["prev_high"]=d.high.rolling(20).max().shift(1)
    d["prev_low"]=d.low.rolling(20).min().shift(1)
    position=None; entry=stop=target=0; trades=[]
    equity=1.0; peak=1.0
    for i,row in d.iterrows():
        if any(pd.isna(row[x]) for x in ["ema20","ema50","rsi","relvol","prev_high","prev_low"]): continue
        L,S=score(row)
        action="LONG" if L>=threshold and L>S else "SHORT" if S>=threshold and S>L else None
        if position:
            if position=="LONG":
                hit_stop=row.low<=stop
                hit_target=row.high>=target
                # Conservative: if both occur in one candle, assume stop first.
                if hit_stop or hit_target:
                    exitp=stop if hit_stop else target
                    ret=(exitp/entry-1)-2*fee-slippage
                    equity*=1+ret
                    trades.append((row.open_time,position,entry,exitp,ret))
                    position=None
            else:
                hit_stop=row.high>=stop
                hit_target=row.low<=target
                if hit_stop or hit_target:
                    exitp=stop if hit_stop else target
                    ret=(entry/exitp-1)-2*fee-slippage
                    equity*=1+ret
                    trades.append((row.open_time,position,entry,exitp,ret))
                    position=None
        if not position and action:
            entry=row.close*(1+slippage if action=="LONG" else 1-slippage)
            if action=="LONG": stop=entry*(1-stop_pct); target=entry*(1+target_pct)
            else: stop=entry*(1+stop_pct); target=entry*(1-target_pct)
            position=action
        peak=max(peak,equity)
    if position:
        row=d.iloc[-1]
        exitp=row.close
        ret=(exitp/entry-1)-2*fee if position=="LONG" else (entry/exitp-1)-2*fee
        equity*=1+ret
        trades.append((row.open_time,position,entry,exitp,ret))
    t=pd.DataFrame(trades,columns=["time","side","entry","exit","return"])
    wins=(t["return"]>0).sum() if len(t) else 0
    losses=(t["return"]<=0).sum() if len(t) else 0
    grosswin=t.loc[t["return"]>0,"return"].sum() if len(t) else 0
    grossloss=abs(t.loc[t["return"]<=0,"return"].sum()) if len(t) else 0
    pf=grosswin/grossloss if grossloss else float("inf") if grosswin else 0
    return {"trades":len(t),"wins":int(wins),"losses":int(losses),
            "win_rate":wins/len(t)*100 if len(t) else 0,
            "profit_factor":pf,"return_pct":(equity-1)*100,
            "final_equity":equity},t

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--symbol",default="BTCUSDT")
    ap.add_argument("--interval",default="5m")
    ap.add_argument("--limit",type=int,default=1000)
    ap.add_argument("--fee",type=float,default=0.001)
    ap.add_argument("--slippage",type=float,default=0.0002)
    ap.add_argument("--stop",type=float,default=0.01)
    ap.add_argument("--target",type=float,default=0.02)
    args=ap.parse_args()
    df=klines(args.symbol,args.interval,args.limit)
    stats,trades=run(df,args.fee,args.slippage,args.stop,args.target)
    print("\nBACKTEST",args.symbol,args.interval)
    for k,v in stats.items(): print(f"{k}: {v}")
    if len(trades): trades.to_csv(f"trades_{args.symbol}_{args.interval}.csv",index=False)
