from dataclasses import dataclass,asdict
@dataclass
class Score:
    long:float; short:float; direction:str; gap:float; grade:str; reasons:list
    def to_dict(self): return asdict(self)

def grade(score,gap):
    if score>=85 and gap>=25:return 'A+'
    if score>=80 and gap>=20:return 'A'
    if score>=75 and gap>=15:return 'B'
    if score>=65:return 'C'
    return 'D'

def score_mtf(mtf):
    s5,s15,s1h,s4h=[mtf[k][1] for k in ('5m','15m','1h','4h')]; d=mtf['5m'][0].iloc[-1]
    L=S=0.; reasons=[]
    if s4h['bullish']: L+=15; reasons.append('4H bullish')
    elif s4h['bearish']: S+=15; reasons.append('4H bearish')
    for s in (s1h,s15):
        if s['bullish']: L+=10
        if s['bearish']: S+=10
    r=s5['rsi'] or 50
    if 52<=r<=68:L+=6
    if 32<=r<=48:S+=6
    if s5['bullish']:L+=4
    if s5['bearish']:S+=4
    if d.close>d.high20:L+=10; reasons.append('20-bar breakout')
    if d.close<d.low20:S+=10; reasons.append('20-bar breakdown')
    if d.close>d.ema20 and d.ema20_slope>0:L+=5
    if d.close<d.ema20 and d.ema20_slope<0:S+=5
    if d.relvol>=1.5:
        if d.close>d.open:L+=10; reasons.append('volume confirmation')
        else:S+=10; reasons.append('selling volume')
    bc=sum(x['bullish'] for x in (s5,s15,s1h,s4h)); rc=sum(x['bearish'] for x in (s5,s15,s1h,s4h))
    L+=min(10,bc*2.5); S+=min(10,rc*2.5)
    dist=abs(d.close-d.ema20)/d.close*100
    if dist<=.8:
        if d.close>=d.ema20:L+=10
        else:S+=10
    elif dist<=1.5:
        if d.close>=d.ema20:L+=5
        else:S+=5
    L=max(0,min(100,L)); S=max(0,min(100,S)); direction='LONG' if L>S else 'SHORT' if S>L else 'FLAT'; best=max(L,S); gap=abs(L-S)
    return Score(round(L,1),round(S,1),direction,round(gap,1),grade(best,gap),reasons)
