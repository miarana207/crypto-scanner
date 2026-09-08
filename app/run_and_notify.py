"""
Lance le scan multi-actifs et envoie les résultats sur Slack + Email —
uniquement si au moins un signal dépasse le seuil.

Répartition des sources de données :
- Crypto            : Binance, toujours (24/7, gratuit, sans clé)
- Forex             : Finnhub, toujours (API officielle gratuite)
- Actions/Indices/
  Matières premières : Twelve Data de 3h à 19h UTC (6h-22h heure de
                        Madagascar), Yahoo Finance le reste du temps

v1.1 — fix : pause spécifique par fournisseur (Twelve Data = 8 req/min sur
le plan gratuit, donc il faut au moins ~7.5s entre deux appels — l'ancienne
pause de 0.3s partagée par tous les fournisseurs faisait dépasser ce quota
en quelques secondes sur une liste de ~16 actifs, ce qui pouvait faire
retomber les appels suivants sur des données dégradées/non rafraîchies).
Ajout aussi d'un horodatage de fraîcheur (dernière bougie utilisée) dans
le message, pour repérer immédiatement si une source sert des données
figées.
"""
import argparse
import os
from datetime import datetime, timezone

from backtest import klines as klines_binance
from data_sources import klines_yahoo, klines_twelvedata, klines_finnhub_forex
from scan import scan
from notify import send_slack, send_email
from assets import CRYPTO, ACTIONS, FOREX, INDICES, COMMODITIES

# Pause (en secondes) entre deux appels API, par fournisseur.
# Twelve Data (plan gratuit) : 8 requêtes/minute max -> il faut au moins
# 60/8 = 7.5s entre deux appels. On prend une marge de sécurité à 8s.
PAUSE_BY_PROVIDER = {
    "binance": 0.3,
    "finnhub": 0.5,
    "twelvedata": 8.0,
    "yahoo": 0.5,
}


def twelvedata_window_active(now=None):
    """True si on est entre 3h et 19h UTC (6h-22h heure de Madagascar, UTC+3)."""
    now = now or datetime.now(timezone.utc)
    return 3 <= now.hour < 19


def build_categories(now=None):
    """Construit la config des catégories, avec la source active pour
    Actions/Indices/Matières premières selon l'heure actuelle."""
    use_twelvedata = twelvedata_window_active(now)
    stock_provider = "twelvedata" if use_twelvedata else "yahoo"
    stock_fetcher = klines_twelvedata if use_twelvedata else klines_yahoo
    stock_pause = PAUSE_BY_PROVIDER[stock_provider]

    def resolve_symbols(entries, key):
        return [e[key] for e in entries]

    categories = {
        "🪙 Crypto": {
            "symbols": CRYPTO, "fetcher": klines_binance, "interval": "5m",
            "pause": PAUSE_BY_PROVIDER["binance"],
        },
        "💵 Forex": {
            "symbols": resolve_symbols(FOREX, "finnhub"),
            "fetcher": klines_finnhub_forex, "interval": "15m",
            "pause": PAUSE_BY_PROVIDER["finnhub"],
        },
        "📈 Actions": {
            "symbols": resolve_symbols(ACTIONS, stock_provider),
            "fetcher": stock_fetcher, "interval": "15m",
            "pause": stock_pause,
        },
        "📊 Indices": {
            "symbols": resolve_symbols(INDICES, stock_provider),
            "fetcher": stock_fetcher, "interval": "15m",
            "pause": stock_pause,
        },
        "🛢️ Matières premières": {
            "symbols": resolve_symbols(COMMODITIES, stock_provider),
            "fetcher": stock_fetcher, "interval": "15m",
            "pause": stock_pause,
        },
    }
    return categories, stock_provider


def scan_all(threshold=60, limit=500, now=None):
    categories, stock_provider = build_categories(now)
    results = {}
    for name, cfg in categories.items():
        print(f"--- {name} (pause={cfg['pause']}s) ---")
        results[name] = scan(
            cfg["symbols"], fetcher=cfg["fetcher"], interval=cfg["interval"],
            limit=limit, threshold=threshold, pause=cfg["pause"],
        )
    return results, stock_provider


def has_signal(all_results):
    for df in all_results.values():
        if not df.empty and df["direction"].isin(["LONG", "SHORT"]).any():
            return True
    return False


def freshness_summary(all_results, now=None):
    """Pour chaque catégorie non vide, l'horodatage de la bougie la plus
    récente utilisée dans le scan — permet de repérer une source figée."""
    now = now or datetime.now(timezone.utc)
    lines = []
    for name, df in all_results.items():
        if df.empty or "last_candle" not in df.columns:
            continue
        most_recent = df["last_candle"].max()
        age_min = (now - most_recent).total_seconds() / 60
        flag = " ⚠️ possible donnée figée" if age_min > 90 else ""
        lines.append(
            f"{name}: dernière bougie {most_recent.strftime('%Y-%m-%d %H:%M UTC')} "
            f"(il y a {age_min:.0f} min){flag}"
        )
    return lines


def format_message(all_results, stock_provider, top=5, threshold=60, now=None):
    now = now or datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"📊 Scan multi-actifs — {ts} (seuil {threshold:.0f})",
        f"Source Actions/Indices/Matières premières ce run : {stock_provider}",
        "",
    ]
    any_signal = False

    for name, df in all_results.items():
        if df.empty:
            continue
        signals = df[df["direction"].isin(["LONG", "SHORT"])]
        if signals.empty:
            continue
        any_signal = True
        lines.append(f"--- {name} ---")
        for _, row in signals.head(top).iterrows():
            best = max(row["score_long"], row["score_short"])
            icon = "🟢" if row["direction"] == "LONG" else "🔴"
            lines.append(
                f"{icon} {row['symbol']}: {row['direction']} (score {best:.0f}) | "
                f"close={row['close']} | RSI={row['rsi']} | relvol={row['relvol']}"
            )
        lines.append("")

    if not any_signal:
        lines.append("Aucun signal au-dessus du seuil sur aucune catégorie.")

    fresh_lines = freshness_summary(all_results, now)
    if fresh_lines:
        lines.append("--- Fraîcheur des données ---")
        lines.extend(fresh_lines)

    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=60)
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    results, stock_provider = scan_all(threshold=args.threshold, limit=args.limit, now=now)
    message = format_message(results, stock_provider, top=args.top, threshold=args.threshold, now=now)
    print("\n" + message)

    if not has_signal(results):
        print("\nAucun signal détecté sur aucune catégorie — notifications non envoyées.")
    else:
        send_slack(os.getenv("SLACK_WEBHOOK_URL"), message)
        send_email(
            smtp_host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
            smtp_port=int(os.getenv("SMTP_PORT", "465")),
            sender=os.getenv("EMAIL_SENDER"),
            password=os.getenv("EMAIL_PASSWORD"),
            recipient=os.getenv("EMAIL_RECIPIENT"),
            subject=f"Scan multi-actifs — {now.strftime('%Y-%m-%d %H:%M')}",
            body=message,
        )
