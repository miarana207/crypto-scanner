import os,requests,pandas as pd
BASE=os.getenv('BINANCE_DATA_URL','https://api.binance.com')
def klines(symbol,interval='5m',limit=1000):
    r=requests.get(BASE+'/api/v3/klines',params={'symbol':symbol.upper(),'interval':interval,'limit':limit},timeout=30); r.raise_for_status()
    cols=['open_time','open','high','low','close','volume','close_time','qav','trades','tbv','tqv','ignore']; d=pd.DataFrame(r.json(),columns=cols)
    for c in ['open','high','low','close','volume']:d[c]=d[c].astype(float)
    d['open_time']=pd.to_datetime(d.open_time,unit='ms',utc=True); return d
