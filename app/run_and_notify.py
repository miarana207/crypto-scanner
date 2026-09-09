"""
Lance le scan multi-actifs V4 et envoie les résultats
sur Slack + Email.

ARCHITECTURE V4
---------------

Score maximum : 100

    Trend    = 25
    EMA      = 20
    RSI      = 15
    Volume   = 15
    Breakout = 25

Seuil par défaut : 75/100

STATUTS
-------

SIGNAL FORT
    score >= seuil
    + breakout confirmé
    + volume confirmé

ATTENTE
    score >= seuil
    mais breakout et/ou volume manquant

SOUS SEUIL
    score < seuil

NOTIFICATIONS
-------------

Email :
    envoyé à chaque scan.

Slack :
    envoyé uniquement lorsqu'au moins un
    SIGNAL FORT est détecté.

SOURCES
-------

Crypto :
    Binance

Forex :
    Twelve Data

Actions :
    Twelve Data de 03h à 19h UTC
    Yahoo Finance hors fenêtre

Indices :
    Yahoo Finance

Matières premières :
    Twelve Data de 03h à 19h UTC
    Yahoo Finance hors fenêtre

Argent :
    Yahoo Finance
"""

import argparse
import os
from datetime import datetime, timezone

import pandas as pd

from backtest import (
    klines as klines_binance,
)

from data_sources import (
    klines_yahoo,
    klines_twelvedata,
)

from scan import scan

from notify import (
    send_slack,
    send_email,
)

from assets import (
    CRYPTO,
    ACTIONS,
    FOREX,
    INDICES,
    COMMODITIES,
    COMMODITIES_YAHOO_ONLY,
)


# ============================================================
# PARAMÈTRES V4
# ============================================================

DEFAULT_THRESHOLD = 75
DEFAULT_LIMIT = 150
DEFAULT_TOP = 5

PAUSE_BY_PROVIDER = {
    "binance": 0.3,
    "twelvedata": 9.0,
    "yahoo": 0.5,
}


# ============================================================
# FENÊTRE TWELVE DATA
# ============================================================

def twelvedata_window_active(now=None):
    """
    Twelve Data actif de 03h00 à 19h00 UTC.
    Soit 06h00 à 22h00 à Madagascar (UTC+3).
    """

    now = (
        now
        or datetime.now(timezone.utc)
    )

    return 3 <= now.hour < 19


# ============================================================
# CATÉGORIES
# ============================================================

def build_categories(now=None):

    use_twelvedata = (
        twelvedata_window_active(now)
    )

    stock_provider = (
        "twelvedata"
        if use_twelvedata
        else "yahoo"
    )

    stock_fetcher = (
        klines_twelvedata
        if use_twelvedata
        else klines_yahoo
    )

    stock_pause = PAUSE_BY_PROVIDER[
        stock_provider
    ]

    def resolve_symbols(
        entries,
        key,
    ):
        return [
            e[key]
            for e in entries
        ]

    categories = {

        # ====================================================
        # CRYPTO
        # ====================================================

        "🪙 Crypto": [
            {
                "symbols": CRYPTO,
                "fetcher": klines_binance,
                "interval": "5m",
                "pause": PAUSE_BY_PROVIDER[
                    "binance"
                ],
                "provider": "binance",
            }
        ],

        # ====================================================
        # FOREX
        # ====================================================

        "💵 Forex": [
            {
                "symbols": resolve_symbols(
                    FOREX,
                    "twelvedata",
                ),
                "fetcher": klines_twelvedata,
                "interval": "15m",
                "pause": PAUSE_BY_PROVIDER[
                    "twelvedata"
                ],
                "provider": "twelvedata",
            }
        ],

        # ====================================================
        # ACTIONS
        # ====================================================

        "📈 Actions": [
            {
                "symbols": resolve_symbols(
                    ACTIONS,
                    stock_provider,
                ),
                "fetcher": stock_fetcher,
                "interval": "15m",
                "pause": stock_pause,
                "provider": stock_provider,
            }
        ],

        # ====================================================
        # INDICES
        # ====================================================

        "📊 Indices": [
            {
                "symbols": resolve_symbols(
                    INDICES,
                    "yahoo",
                ),
                "fetcher": klines_yahoo,
                "interval": "15m",
                "pause": PAUSE_BY_PROVIDER[
                    "yahoo"
                ],
                "provider": "yahoo",
            }
        ],

        # ====================================================
        # MATIÈRES PREMIÈRES
        # ====================================================

        "🛢️ Matières premières": [

            {
                "symbols": resolve_symbols(
                    COMMODITIES,
                    stock_provider,
                ),
                "fetcher": stock_fetcher,
                "interval": "15m",
                "pause": stock_pause,
                "provider": stock_provider,
            },

            {
                "symbols": resolve_symbols(
                    COMMODITIES_YAHOO_ONLY,
                    "yahoo",
                ),
                "fetcher": klines_yahoo,
                "interval": "15m",
                "pause": PAUSE_BY_PROVIDER[
                    "yahoo"
                ],
                "provider": "yahoo",
            },
        ],
    }

    return (
        categories,
        stock_provider,
    )


# ============================================================
# SCAN COMPLET
# ============================================================

def scan_all(
    threshold=DEFAULT_THRESHOLD,
    limit=DEFAULT_LIMIT,
    now=None,
):

    categories, stock_provider = (
        build_categories(now)
    )

    results = {}

    for name, groups in categories.items():

        dfs = []

        for cfg in groups:

            print(
                f"--- {name} "
                f"({cfg['fetcher'].__name__}, "
                f"source={cfg['provider']}, "
                f"pause={cfg['pause']}s) ---"
            )

            df = scan(
                cfg["symbols"],
                fetcher=cfg["fetcher"],
                interval=cfg["interval"],
                limit=limit,
                threshold=threshold,
                pause=cfg["pause"],
            )

            if not df.empty:

                # Source de chaque ligne
                df = df.copy()
                df["provider"] = (
                    cfg["provider"]
                )

                dfs.append(df)

        if dfs:

            merged = pd.concat(
                dfs,
                ignore_index=True,
            )

            merged = merged.sort_values(
                "best_score",
                ascending=False,
            ).reset_index(
                drop=True
            )

            results[name] = merged

        else:

            results[name] = (
                pd.DataFrame()
            )

    return (
        results,
        stock_provider,
    )


# ============================================================
# SIGNAL FORT
# ============================================================

def has_signal(all_results):

    for df in all_results.values():

        if df.empty:
            continue

        if "status" not in df.columns:
            continue

        if (
            df["status"]
            .eq("SIGNAL FORT")
            .any()
        ):
            return True

    return False


# ============================================================
# NOMBRE DE SIGNAUX FORTS
# ============================================================

def count_strong_signals(
    all_results,
):

    total = 0

    for df in all_results.values():

        if df.empty:
            continue

        if "status" not in df.columns:
            continue

        total += int(
            df["status"]
            .eq("SIGNAL FORT")
            .sum()
        )

    return total


# ============================================================
# FRAÎCHEUR
# ============================================================

def freshness_summary(
    all_results,
    now=None,
):

    now = (
        now
        or datetime.now(timezone.utc)
    )

    lines = []

    for name, df in all_results.items():

        if (
            df.empty
            or "last_candle"
            not in df.columns
        ):
            continue

        valid_times = (
            pd.to_datetime(
                df["last_candle"],
                utc=True,
                errors="coerce",
            )
            .dropna()
        )

        if valid_times.empty:
            continue

        most_recent = (
            valid_times.max()
        )

        age_min = (
            now - most_recent
        ).total_seconds() / 60

        if age_min > 90:
            flag = (
                " ⚠️ possible donnée figée"
            )

        elif age_min > 30:
            flag = (
                " ⚠️ donnée ancienne"
            )

        else:
            flag = ""

        lines.append(
            f"{name}: dernière bougie "
            f"{most_recent.strftime('%Y-%m-%d %H:%M UTC')} "
            f"(il y a {age_min:.0f} min)"
            f"{flag}"
        )

    return lines


# ============================================================
# FORMATAGE D'UNE LIGNE
# ============================================================

def format_row(
    row,
    threshold,
):

    status = row.get(
        "status",
        "SOUS SEUIL",
    )

    direction = row.get(
        "direction",
        "-",
    )

    best_score = float(
        row.get(
            "best_score",
            max(
                row.get(
                    "score_long",
                    0,
                ),
                row.get(
                    "score_short",
                    0,
                ),
            ),
        )
    )

    if status == "SIGNAL FORT":

        icon = "🔥"

    elif status == "ATTENTE":

        icon = "🟡"

    elif direction == "LONG":

        icon = "🟢"

    elif direction == "SHORT":

        icon = "🔴"

    else:

        icon = "⚪"

    relvol = row.get(
        "relvol",
        float("nan"),
    )

    if pd.isna(relvol):
        relvol_display = "n/d"
    else:
        relvol_display = (
            f"{float(relvol):.2f}"
        )

    trend_pts = float(
        row.get("trend_pts", 0)
    )

    ema_pts = float(
        row.get("ema_pts", 0)
    )

    rsi_pts = float(
        row.get("rsi_pts", 0)
    )

    volume_pts = float(
        row.get("volume_pts", 0)
    )

    breakout_pts = float(
        row.get("breakout_pts", 0)
    )

    missing = row.get(
        "missing",
        "",
    )

    if status == "SIGNAL FORT":

        diagnostic = (
            "🔥 PRISE DE POSITION IMMÉDIATE"
        )

    elif status == "ATTENTE":

        diagnostic = (
            f"manque : {missing}"
        )

    else:

        diagnostic = (
            "manque : SCORE"
        )

    return (
        f"{icon} {row['symbol']}: "
        f"{direction} | "
        f"score {best_score:.0f}/100 | "
        f"{status} | "
        f"{diagnostic} | "
        f"Trend={trend_pts:+.0f} "
        f"EMA={ema_pts:+.0f} "
        f"RSI={rsi_pts:+.0f} "
        f"Volume={volume_pts:+.0f} "
        f"Breakout={breakout_pts:+.0f} | "
        f"RSI={float(row['rsi']):.1f} | "
        f"relvol={relvol_display}"
    )


# ============================================================
# MESSAGE PRINCIPAL
# ============================================================

def format_message(
    all_results,
    stock_provider,
    top=DEFAULT_TOP,
    threshold=DEFAULT_THRESHOLD,
    now=None,
):

    now = (
        now
        or datetime.now(timezone.utc)
    )

    ts = now.strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    strong_count = (
        count_strong_signals(
            all_results
        )
    )

    if strong_count:

        signal_header = (
            f"🔥 {strong_count} SIGNAL(S) FORT(S) DÉTECTÉ(S)"
        )

    else:

        signal_header = (
            "ℹ️ Aucun SIGNAL FORT détecté"
        )

    lines = [

        f"📊 Scan multi-actifs V4 — {ts}",

        (
            f"Seuil : {threshold:.0f}/100 | "
            "Score maximum : 100"
        ),

        (
            "Pondération : "
            "Trend 25 | EMA 20 | RSI 15 | "
            "Volume 15 | Breakout 25"
        ),

        (
            "Breakout : ±0,10 % | "
            "Volume : relvol ≥ 1,50"
        ),

        signal_header,

        (
            "Source Actions/Matières premières "
            f"ce run : {stock_provider}"
        ),

        (
            "Indices : yahoo "
            "(fixe, indices non couverts "
            "par le plan gratuit Twelve Data)"
        ),

        "",
    ]

    # ========================================================
    # TOP 5 DE CHAQUE CATÉGORIE
    # ========================================================

    for name, df in all_results.items():

        if df.empty:
            continue

        lines.append(
            f"--- {name} | TOP {top} ---"
        )

        diagnostic = (
            df.sort_values(
                "best_score",
                ascending=False,
            )
            .head(top)
        )

        for _, row in diagnostic.iterrows():

            lines.append(
                format_row(
                    row,
                    threshold,
                )
            )

        lines.append("")

    # ========================================================
    # FRAÎCHEUR
    # ========================================================

    fresh_lines = (
        freshness_summary(
            all_results,
            now,
        )
    )

    if fresh_lines:

        lines.append(
            "--- Fraîcheur des données ---"
        )

        lines.extend(
            fresh_lines
        )

        lines.append("")

    # ========================================================
    # LÉGENDE
    # ========================================================

    lines.extend(
        [
            "--- Légende ---",
            (
                "🔥 SIGNAL FORT = score ≥ seuil "
                "+ BREAKOUT + VOLUME"
            ),
            (
                "🟡 ATTENTE = score ≥ seuil "
                "mais trigger incomplet"
            ),
            (
                "SOUS SEUIL = score < seuil"
            ),
        ]
    )

    return "\n".join(lines)


# ============================================================
# EXECUTION
# ============================================================

if __name__ == "__main__":

    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
    )

    ap.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=(
            "Nombre de bougies récupérées "
            "par appel. 150 suffit pour "
            "EMA50/RSI14/relvol20."
        ),
    )

    ap.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
    )

    args = ap.parse_args()

    # ========================================================
    # HEURE DE DÉBUT
    # ========================================================

    run_start = datetime.now(
        timezone.utc
    )

    print(
        "=================================================="
    )

    print(
        "SCAN MULTI-ACTIFS V4"
    )

    print(
        "=================================================="
    )

    print(
        f"Début : "
        f"{run_start.strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )

    print(
        f"Seuil : {args.threshold:.0f}/100"
    )

    print(
        f"Limit : {args.limit}"
    )

    print(
        f"Top : {args.top}"
    )

    print()

    # ========================================================
    # SCAN
    # ========================================================

    results, stock_provider = scan_all(
        threshold=args.threshold,
        limit=args.limit,
        now=run_start,
    )

    # ========================================================
    # HEURE APRÈS SCAN
    # ========================================================

    now_after_scan = datetime.now(
        timezone.utc
    )

    # ========================================================
    # MESSAGE
    # ========================================================

    message = format_message(
        results,
        stock_provider,
        top=args.top,
        threshold=args.threshold,
        now=now_after_scan,
    )

    print(
        "\n" + message
    )

    # ========================================================
    # NOTIFICATIONS
    # ========================================================

    strong_signal = has_signal(
        results
    )

    # --------------------------------------------------------
    # EMAIL : TOUJOURS
    # --------------------------------------------------------

    email_subject_prefix = (
        "🔥 SIGNAL FORT"
        if strong_signal
        else "📊 Scan"
    )

    email_subject = (
        f"{email_subject_prefix} "
        f"multi-actifs — "
        f"{run_start.strftime('%Y-%m-%d %H:%M')}"
    )

    try:

        send_email(
            smtp_host=os.getenv(
                "SMTP_HOST",
                "smtp.gmail.com",
            ),

            smtp_port=int(
                os.getenv(
                    "SMTP_PORT",
                    "465",
                )
            ),

            sender=os.getenv(
                "EMAIL_SENDER"
            ),

            password=os.getenv(
                "EMAIL_PASSWORD"
            ),

            recipient=os.getenv(
                "EMAIL_RECIPIENT"
            ),

            subject=email_subject,

            body=message,
        )

        print(
            "\n✅ Email envoyé."
        )

    except Exception as e:

        print(
            f"\n❌ Erreur envoi email : {e}"
        )

    # --------------------------------------------------------
    # SLACK : UNIQUEMENT SIGNAL FORT
    # --------------------------------------------------------

    if strong_signal:

        try:

            send_slack(
                os.getenv(
                    "SLACK_WEBHOOK_URL"
                ),
                message,
            )

            print(
                "✅ Slack envoyé "
                "(SIGNAL FORT détecté)."
            )

        except Exception as e:

            print(
                f"❌ Erreur envoi Slack : {e}"
            )

    else:

        print(
            "\nℹ️ Aucun SIGNAL FORT : "
            "Slack non envoyé."
        )
