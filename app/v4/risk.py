from dataclasses import dataclass
@dataclass
class TradePlan:
    symbol:str; direction:str; entry:float; sl:float; tp1:float; tp2:float; tp3:float; risk_pct:float; position_notional:float; rr:float; setup:str
    def to_dict(self):return self.__dict__.copy()

def build_plan(symbol,direction,d,portfolio=10000,risk_pct=.0075):
    x=d.iloc[-1]; entry=float(x.close); a=float(x.atr)
    if direction=='LONG':
        structural=float(d.low.tail(10).min()); sl=min(entry-1.5*a,structural); sl=sl if sl<entry else entry-1.5*a
        risk=entry-sl; tp1,tp2,tp3=entry+risk,entry+2*risk,entry+3*risk; setup='BREAKOUT' if entry>x.high20 else 'PULLBACK'
    else:
        structural=float(d.high.tail(10).max()); sl=max(entry+1.5*a,structural); sl=sl if sl>entry else entry+1.5*a
        risk=sl-entry; tp1,tp2,tp3=entry-risk,entry-2*risk,entry-3*risk; setup='BREAKDOWN' if entry<x.low20 else 'PULLBACK'
    notional=(portfolio*risk_pct/risk)*entry
    return TradePlan(symbol,direction,entry,sl,tp1,tp2,tp3,risk_pct,notional,2.0,setup)
