from .mtf import build_mtf
from .scoring import score_mtf
from .risk import build_plan

def analyze(symbol,df5m,portfolio=10000,risk_pct=.0075,min_score=75,min_gap=15):
    mtf=build_mtf(df5m); s=score_mtf(mtf); best=max(s.long,s.short)
    if s.direction=='FLAT' or best<min_score or s.gap<min_gap:return None
    p=build_plan(symbol,s.direction,mtf['5m'][0],portfolio,risk_pct)
    return {'symbol':symbol,'direction':s.direction,'score':best,'long_score':s.long,'short_score':s.short,'gap':s.gap,'grade':s.grade,'reasons':s.reasons,**p.to_dict()}

def rank(results,top_n=5):return sorted([x for x in results if x],key=lambda x:(x['score'],x['gap']),reverse=True)[:top_n]
