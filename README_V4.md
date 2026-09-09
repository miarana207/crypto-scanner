# Crypto Scanner V4 patch

Adds a quantitative multi-timeframe trade-selection engine without touching the existing V3 files.

## Timeframes
4H regime · 1H trend · 15m setup · 5m entry

## Output
Direction, score, directional gap, grade, entry, ATR/structure SL, TP1/TP2/TP3, risk %, position notional, setup and reasons.

## Run
From repository root after copying `app/v4` and `config/v4.json`:

```bash
python -m app.v4.cli --symbols BTCUSDT ETHUSDT SOLUSDT BNBUSDT --portfolio 10000
```

Dependencies: pandas, numpy, requests.

**Paper-trading only initially.** The patch does not place live orders.
