"""
Lance le scan multi-actifs et envoie les résultats sur Slack + Email —
uniquement si au moins un signal dépasse le seuil.

Répartition des sources de données :
- Crypto            : Binance, toujours (24/7, gratuit, sans clé)
- Forex             : Finnhub, toujours (API officielle gratuite)
- Actions/Indices/
  Matières premières : Twelve Data de 3h à 19h UTC (6h-22h heure de
                        Madagascar), Yahoo Finance le reste du temps
"""
import argparse
import os
from datetime import datetime, timezone

from backtest import klines as klines_binance
from data_sources import klines_yahoo, klines_twelvedata, klines_finnhub_forex
from scan import scan
from notify import send_slack, send_email
from assets import CRYPTO, ACTIONS, FOREX, INDICES, COMMODITIES


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

    def resolve_symbols(entries, key):
        return [e[key] for e in entries]

    categories = {
        "🪙 Crypto": {
            "symbols": CRYPTO, "fetcher": klines_binance, "interval": "5m",
        },
        "💵 Forex": {
            "symbols": resolve_symbols(FOREX, "finnhub"),
            "fetcher": klines_finnhub_forex, "interval": "15m",
        },
        "📈 Actions": {
            "symbols": resolve_symbols(ACTIONS, stock_provider),
            "fetcher": stock_fetcher, "interval": "15m",
        },
        "📊 Indices": {
            "symbols": resolve_symbols(INDICES, stock_provider),
            "fetcher": stock_fetcher, "interval": "15m",
        },
        "🛢️ Matières premières": {
            "symbols": resolve_symbols(COMMODITIES, stock_provider),
            "fetcher": stock_fetcher, "interval": "15m",
        },
    }
    return categories, stock_provider


def scan_all(threshold=60, limit=500, pause=0.3, now=None):
    categories, stock_provider = build_categories(now)
    results = {}
    for name, cfg in categories.items():
        print(f"--- {name} ---")
        results[name] = scan(
            cfg["symbols"], fetcher=cfg["fetcher"], interval=cfg["interval"],
            limit=limit, threshold=threshold, pause=pause,
        )
    return results, stock_provider


def has_signal(all_results):
    for df in all_results.values():
        if not df.empty and df["direction"].isin(["LONG", "SHORT"]).any():
            return True
    return False


def format_message(all_results, stock_provider, top=5, threshold=60):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
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

    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=60)
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--pause", type=float, default=0.3)
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    results, stock_provider = scan_all(threshold=args.threshold, limit=args.limit, pause=args.pause)
    message = format_message(results, stock_provider, top=args.top, threshold=args.threshold)
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
            subject=f"Scan multi-actifs — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
            body=message,
        )
