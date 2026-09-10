# Scanner V4.2

Six fichiers principaux :
- assets.py
- backtest.py
- data_sources.py
- notify.py
- scan.py
- run_and_notify.py

Variables utiles :
- TWELVE_DATA_API_KEY
- FINNHUB_API_KEY
- BINANCE_DATA_URL (optionnel)
- TWELVEDATA_DAILY_BUDGET (défaut 800)
- DATA_CACHE_TTL (défaut 45 secondes)
- DATA_REQUEST_TIMEOUT (défaut 30 secondes)
- EMAIL_SENDER / EMAIL_PASSWORD / EMAIL_RECIPIENT
- SMTP_HOST / SMTP_PORT
- SLACK_WEBHOOK_URL

Important : Twelve Data Basic indique actuellement 8 API credits et 800 crédits/jour. Le routeur utilise un budget configurable et ne consomme pas Twelve Data lorsqu'une source suffisante est déjà disponible. Les données Twelve Data sont destinées à un usage personnel/interne sur le plan Basic.
