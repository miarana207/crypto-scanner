import os, argparse, requests, math
from datetime import datetime, timezone
import pandas as pd
from dotenv import load_dotenv
load_dotenv()

# Import différé pour éviter tout souci d'import circulaire avec data_sources.py
# (qui, lui, n'importe jamais backtest.py). klines() ci-dessous reste inchangée
# et continue d'être utilisée telle quelle par scan.py pour la crypto.
def _get_fetcher(source):
    if source == "binance":
        return klines
    from data_sources import klines_yahoo, klines_twelvedata, klines_finnhub_forex
    return {
        "yahoo": klines_yahoo,
        "twelvedata": klines_twelvedata,
        "finnhub_forex": klines_finnhub_forex,
    }[source]

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

def _atr(d, period=14):
    """Average True Range — mesure de volatilité utilisée pour des stops/targets
    proportionnels au marché plutôt qu'un pourcentage fixe arbitraire."""
    prev_close = d.close.shift(1)
    tr = pd.concat([
        d.high - d.low,
        (d.high - prev_close).abs(),
        (d.low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

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
    d["atr"]=_atr(d,14)
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
    if row.relvol>=1.5:L+=15;S+=15
    prev_high=row.prev_high
    prev_low=row.prev_low
    if row.close>prev_high:L+=15
    if row.close<prev_low:S+=15
    return L,S

def run(df, fee=0.001, slippage=0.0002, stop_pct=0.01, target_pct=0.02, threshold=75,
        use_atr=False, atr_stop_mult=1.5, atr_target_mult=3.0, risk_pct=None):
    """
    Corrections apportées suite à la revue de code :

    - Biais de "look-ahead" corrigé : le signal est calculé sur la clôture de la
      bougie N, mais l'entrée en position se fait à l'OUVERTURE de la bougie N+1
      (pending_action), pas à la clôture de N — impossible de trader un prix
      qu'on ne connaît pas encore au moment de la décision.
    - use_atr=True : stop/target basés sur l'ATR (volatilité réelle du marché)
      plutôt qu'un pourcentage fixe. atr_stop_mult/atr_target_mult règlent la
      distance en multiples d'ATR.
    - risk_pct : si fourni (ex: 0.01 pour 1%), dimensionne chaque trade pour que
      la perte au stop corresponde à ce % du capital, plutôt que d'engager 100%
      du capital sur chaque trade (comportement par défaut si non renseigné,
      identique à avant pour rester rétro-compatible).
    """
    d=indicators(df)
    d["prev_high"]=d.high.rolling(20).max().shift(1)
    d["prev_low"]=d.low.rolling(20).min().shift(1)

    required=["ema20","ema50","rsi","relvol","prev_high","prev_low"]
    if use_atr: required.append("atr")

    position=None; entry=stop=target=0; size=1.0
    pending_action=None  # signal calculé à la bougie précédente, exécuté à l'ouverture de celle-ci
    trades=[]
    equity=1.0; peak=1.0

    for i,row in d.iterrows():
        # 1) Exécuter un signal en attente, à l'OUVERTURE de cette bougie
        if pending_action and not position:
            action=pending_action
            entry=row.open*(1+slippage if action=="LONG" else 1-slippage)
            if use_atr and not pd.isna(row.atr):
                if action=="LONG":
                    stop=entry-row.atr*atr_stop_mult; target=entry+row.atr*atr_target_mult
                else:
                    stop=entry+row.atr*atr_stop_mult; target=entry-row.atr*atr_target_mult
            else:
                if action=="LONG": stop=entry*(1-stop_pct); target=entry*(1+target_pct)
                else: stop=entry*(1+stop_pct); target=entry*(1-target_pct)
            if risk_pct:
                stop_dist_pct=abs(entry-stop)/entry
                size=min(risk_pct/stop_dist_pct,1.0) if stop_dist_pct>0 else 0.0
            else:
                size=1.0
            position=action
            pending_action=None

        # 2) Gérer une sortie (stop/target) sur la bougie courante si une position est ouverte
        if position:
            if position=="LONG":
                hit_stop=row.low<=stop
                hit_target=row.high>=target
                if hit_stop or hit_target:
                    exitp=stop if hit_stop else target
                    ret=((exitp/entry-1)-2*fee-slippage)*size
                    equity*=1+ret
                    trades.append((row.open_time,position,entry,exitp,ret))
                    position=None
            else:
                hit_stop=row.high>=stop
                hit_target=row.low<=target
                if hit_stop or hit_target:
                    exitp=stop if hit_stop else target
                    ret=((entry/exitp-1)-2*fee-slippage)*size
                    equity*=1+ret
                    trades.append((row.open_time,position,entry,exitp,ret))
                    position=None

        # 3) Calculer un signal sur la clôture de CETTE bougie, pour la PROCHAINE
        if any(pd.isna(row[x]) for x in required):
            peak=max(peak,equity)
            continue
        if not position and not pending_action:
            L,S=score(row)
            pending_action="LONG" if L>=threshold and L>S else "SHORT" if S>=threshold and S>L else None

        peak=max(peak,equity)

    if position:
        row=d.iloc[-1]
        exitp=row.close
        ret=((exitp/entry-1)-2*fee if position=="LONG" else (entry/exitp-1)-2*fee)*size
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
    ap.add_argument("--symbol",default="BTCUSDT",
                    help="Format selon --source : BTCUSDT (binance), AAPL/^GSPC/GC=F (yahoo), "
                         "AAPL/SPX/XAU-USD (twelvedata), OANDA:EUR_USD (finnhub_forex)")
    ap.add_argument("--source",default="binance",
                    choices=["binance","yahoo","twelvedata","finnhub_forex"],
                    help="Source de données à utiliser pour le backtest")
    ap.add_argument("--interval",default="5m")
    ap.add_argument("--limit",type=int,default=1000)
    ap.add_argument("--fee",type=float,default=0.001)
    ap.add_argument("--slippage",type=float,default=0.0002)
    ap.add_argument("--stop",type=float,default=0.01)
    ap.add_argument("--target",type=float,default=0.02)
    ap.add_argument("--use-atr",action="store_true",help="Stops/targets basés sur l'ATR plutôt qu'un % fixe")
    ap.add_argument("--atr-stop-mult",type=float,default=1.5)
    ap.add_argument("--atr-target-mult",type=float,default=3.0)
    ap.add_argument("--risk-pct",type=float,default=None,
                    help="Risque en %% du capital par trade, ex: 0.01 pour 1%%. Non renseigné = 100%% du capital par trade (comme avant).")
    args=ap.parse_args()
    fetch=_get_fetcher(args.source)
    df=fetch(args.symbol,interval=args.interval,limit=args.limit)
    stats,trades=run(df,args.fee,args.slippage,args.stop,args.target,
                      use_atr=args.use_atr,atr_stop_mult=args.atr_stop_mult,
                      atr_target_mult=args.atr_target_mult,risk_pct=args.risk_pct)
    print(f"\nBACKTEST {args.symbol} [{args.source}] {args.interval}")
    for k,v in stats.items(): print(f"{k}: {v}")
    if len(trades): trades.to_csv(f"trades_{args.symbol.replace('/','-').replace(':','-')}_{args.interval}.csv",index=False)
