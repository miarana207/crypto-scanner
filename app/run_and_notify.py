"""
Lance le scan multi-actifs et envoie les résultats sur Slack + Email.
Toute la config (webhook, identifiants SMTP) vient de variables d'environnement,
jamais en dur dans le code — voir README_automation.md pour la configuration.
"""
import argparse
import os
from datetime import datetime, timezone

from scan import scan
from notify import send_slack, send_email


def has_signal(df):
    """True si au moins un actif a un score au-dessus du seuil (direction LONG ou SHORT)."""
    if df.empty:
        return False
    return df["direction"].isin(["LONG", "SHORT"]).any()


def format_message(df, top=5, interval="5m", threshold=60):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = f"📊 Scan {interval} — {ts} (seuil {threshold:.0f})"

    if df.empty:
        return header + "\nAucun résultat exploitable (données insuffisantes)."

    lines = [header, ""]
    for _, row in df.head(top).iterrows():
        best = max(row["score_long"], row["score_short"])
        icon = "🟢" if row["direction"] == "LONG" else "🔴" if row["direction"] == "SHORT" else "⚪"
        lines.append(
            f"{icon} {row['symbol']}: {row['direction']} (score {best:.0f}) | "
            f"close={row['close']} | RSI={row['rsi']} | relvol={row['relvol']} | trend1h={row['trend1h']}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+",
                     default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                              "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"])
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--threshold", type=float, default=60)
    ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()

    df = scan(args.symbols, args.interval, args.limit, args.threshold)
    message = format_message(df, args.top, args.interval, args.threshold)
    print(message)

    if not has_signal(df):
        print("\nAucun signal au-dessus du seuil — notifications non envoyées.")
    else:
        send_slack(os.getenv("SLACK_WEBHOOK_URL"), message)
        send_email(
            smtp_host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
            smtp_port=int(os.getenv("SMTP_PORT", "465")),
            sender=os.getenv("EMAIL_SENDER"),
            password=os.getenv("EMAIL_PASSWORD"),
            recipient=os.getenv("EMAIL_RECIPIENT"),
            subject=f"Scan crypto {args.interval} — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            body=message,
        )
