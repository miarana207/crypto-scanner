import argparse
from .binance_data import klines
from .engine import analyze,rank

def main():
    p=argparse.ArgumentParser(); p.add_argument('--symbols',nargs='+',default=['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','LINKUSDT']); p.add_argument('--limit',type=int,default=1000); p.add_argument('--portfolio',type=float,default=10000); p.add_argument('--risk',type=float,default=.0075); p.add_argument('--min-score',type=float,default=75); p.add_argument('--min-gap',type=float,default=15); p.add_argument('--top',type=int,default=5); a=p.parse_args(); results=[]
    for s in a.symbols:
        try: results.append(analyze(s,klines(s,'5m',a.limit),a.portfolio,a.risk,a.min_score,a.min_gap))
        except Exception as e: print(f'{s}: ERROR {e}')
    print('\n=== CRYPTO SCANNER V4 ==='); ranked=rank(results,a.top)
    if not ranked: print('Aucun setup suffisamment robuste.'); return
    for i,x in enumerate(ranked,1):
        print(f"\n#{i} {x['symbol']} {x['direction']} {x['score']:.1f}/100 {x['grade']}")
        print(f"Entry {x['entry']:.8g} | SL {x['sl']:.8g} | TP1 {x['tp1']:.8g} | TP2 {x['tp2']:.8g} | TP3 {x['tp3']:.8g}")
        print(f"Position ${x['position_notional']:.2f} | Risk {x['risk_pct']*100:.2f}% | Setup {x['setup']}")
        print('Reasons:',', '.join(x['reasons']))
if __name__=='__main__':main()
