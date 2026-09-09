"""
Lance le scan multi-actifs V4 et envoie :

- EMAIL : à chaque scan
- SLACK  : uniquement si au moins un SIGNAL FORT

LOGIQUE V4 :

Score /100 :
    Trend    = 25
    EMA      = 20
    RSI      = 15
    Volume   = 15
    Breakout = 25

Seuil :
    75/100

SIGNAL FORT :
    score >= 75
    + BREAKOUT
    + VOLUME

ATTENTE :
    score >= 75
    mais BREAKOUT et/ou VOLUME manquant

SOUS SEUIL :
    score < 75

Top 5 de chaque catégorie :
    toujours affiché, indépendamment du seuil.
"""

import argparse
import os

from datetime import datetime, timezone

import pandas as pd

from backtest import klines as klines_binance

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
# CONFIGURATION
# ============================================================

DEFAULT_THRESHOLD = 75
DEFAULT_TOP = 5
DEFAULT_LIMIT = 150


PAUSE_BY_PROVIDER = {
    "binance": 0.3,
    "twelvedata": 9.0,
    "yahoo": 0.5,
}


# ============================================================
# FENÊTRE TWELVE DATA
# ============================================================

def twelvedata_window_active(now=None):

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

    stock_pause = (
        PAUSE_BY_PROVIDER[
            stock_provider
        ]
    )

    def resolve_symbols(
        entries,
        key,
    ):
        return [
            e[key]
            for e in entries
        ]

    categories = {

        # ----------------------------------------------------
        # CRYPTO
        # ----------------------------------------------------

        "🪙 Crypto": [
            {
                "symbols": CRYPTO,
                "fetcher": klines_binance,
                "interval": "5m",
                "pause":
                    PAUSE_BY_PROVIDER[
                        "binance"
                    ],
            }
        ],

        # ----------------------------------------------------
        # FOREX
        # ----------------------------------------------------

        "💵 Forex": [
            {
                "symbols":
                    resolve_symbols(
                        FOREX,
                        "twelvedata",
                    ),

                "fetcher":
                    klines_twelvedata,

                "interval": "15m",

                "pause":
                    PAUSE_BY_PROVIDER[
                        "twelvedata"
                    ],
            }
        ],

        # ----------------------------------------------------
        # ACTIONS
        # ----------------------------------------------------

        "📈 Actions": [
            {
                "symbols":
                    resolve_symbols(
                        ACTIONS,
                        stock_provider,
                    ),

                "fetcher":
                    stock_fetcher,

                "interval": "15m",

                "pause":
                    stock_pause,
            }
        ],

        # ----------------------------------------------------
        # INDICES
        # ----------------------------------------------------

        "📊 Indices": [
            {
                "symbols":
                    resolve_symbols(
                        INDICES,
                        "yahoo",
                    ),

                "fetcher":
                    klines_yahoo,

                "interval": "15m",

                "pause":
                    PAUSE_BY_PROVIDER[
                        "yahoo"
                    ],
            }
        ],

        # ----------------------------------------------------
        # MATIÈRES PREMIÈRES
        # ----------------------------------------------------

        "🛢️ Matières premières": [

            {
                "symbols":
                    resolve_symbols(
                        COMMODITIES,
                        stock_provider,
                    ),

                "fetcher":
                    stock_fetcher,

                "interval": "15m",

                "pause":
                    stock_pause,
            },

            {
                "symbols":
                    resolve_symbols(
                        COMMODITIES_YAHOO_ONLY,
                        "yahoo",
                    ),

                "fetcher":
                    klines_yahoo,

                "interval": "15m",

                "pause":
                    PAUSE_BY_PROVIDER[
                        "yahoo"
                    ],
            },
        ],
    }

    return (
        categories,
        stock_provider,
    )


# ============================================================
# SCAN GLOBAL
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

    for name, groups in (
        categories.items()
    ):

        dfs = []

        for cfg in groups:

            print(
                f"--- {name} "
                f"({cfg['fetcher'].__name__}, "
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
                dfs.append(df)

        if dfs:

            merged = pd.concat(
                dfs,
                ignore_index=True,
            )

            merged = (
                merged
                .sort_values(
                    "score",
                    ascending=False,
                )
                .reset_index(
                    drop=True
                )
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

    for name, df in (
        all_results.items()
    ):

        if df.empty:
            continue

        if "last_candle" not in df.columns:
            continue

        most_recent = (
            df["last_candle"]
            .max()
        )

        age_min = (
            now - most_recent
        ).total_seconds() / 60

        flag = ""

        if age_min > 90:

            flag = (
                " ⚠️ possible donnée figée"
            )

        lines.append(
            f"{name}: dernière bougie "
            f"{most_recent.strftime('%Y-%m-%d %H:%M UTC')} "
            f"(il y a {age_min:.0f} min)"
            f"{flag}"
        )

    return lines


# ============================================================
# FORMAT SCORE
# ============================================================

def format_score_line(row):

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

    score = float(
        row["score"]
    )

    direction = row[
        "direction"
    ]

    status = row[
        "status"
    ]

    missing = row.get(
        "missing",
        "",
    )

    if status == "SIGNAL FORT":

        status_text = (
            "🔥 SIGNAL FORT — "
            "PRISE DE POSITION IMMÉDIATE"
        )

    elif status == "ATTENTE":

        status_text = (
            "⏳ ATTENTE"
        )

        if missing:
            status_text += (
                f" — manque : {missing}"
            )

    else:

        status_text = (
            "SOUS SEUIL — "
            "manque : SCORE"
        )

    icon = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    return (
        f"{icon} {row['symbol']}: "
        f"{score:.0f}/100 "
        f"({direction}) — "
        f"{status_text} | "
        f"Tendance={row['trend_pts']:+.0f} | "
        f"EMA={row['ema_pts']:+.0f} | "
        f"RSI={row['rsi_pts']:+.0f} | "
        f"Volume={row['volume_pts']:+.0f} | "
        f"Breakout={row['breakout_pts']:+.0f} | "
        f"RSI actuel={row['rsi']:.1f} | "
        f"relvol={relvol_display}"
    )


# ============================================================
# MESSAGE
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

    lines = [

        f"📊 Scan multi-actifs — "
        f"{ts} "
        f"(seuil {threshold:.0f}/100)",

        (
            "Source Actions/Matières "
            f"premières ce run : "
            f"{stock_provider} | "
            "Indices : yahoo"
        ),

        "",

        "🔥 PRISE DE POSITION IMMÉDIATE",

        (
            "Conditions : "
            f"score ≥ {threshold:.0f}/100 "
            "+ BREAKOUT + VOLUME"
        ),

        "",
    ]

    strong_found = False

    # ========================================================
    # 1. SIGNALS FORTS
    # ========================================================

    for name, df in (
        all_results.items()
    ):

        if df.empty:
            continue

        strong = df[
            df["status"]
            == "SIGNAL FORT"
        ]

        if strong.empty:
            continue

        strong_found = True

        lines.append(
            f"--- {name} ---"
        )

        for _, row in (
            strong
            .head(top)
            .iterrows()
        ):

            lines.append(
                format_score_line(
                    row
                )
            )

        lines.append("")

    if not strong_found:

        lines.append(
            "Aucun actif ne remplit "
            "toutes les conditions."
        )

    else:

        lines.append(
            "⚠️ Un ou plusieurs "
            "SIGNAL FORT détectés."
        )

    lines.append("")

    # ========================================================
    # 2. TOP 5 DE CHAQUE CATÉGORIE
    # ========================================================

    lines.append(
        "👀 À SURVEILLER"
    )

    lines.append(
        "Top "
        f"{top} de chaque catégorie — "
        "indépendamment du seuil."
    )

    lines.append("")

    for name, df in (
        all_results.items()
    ):

        if df.empty:
            continue

        lines.append(name)

        diagnostic = (
            df
            .sort_values(
                "score",
                ascending=False,
            )
            .head(top)
        )

        for _, row in (
            diagnostic.iterrows()
        ):

            lines.append(
                "  "
                + format_score_line(
                    row
                )
            )

        lines.append("")

    # ========================================================
    # 3. FRAÎCHEUR
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

    return "\n".join(
        lines
    )


# ============================================================
# MAIN
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
            "par appel. "
            "150 suffit normalement pour "
            "EMA50/RSI14/relvol20."
        ),
    )

    ap.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
    )

    args = ap.parse_args()

    # --------------------------------------------------------
    # Heure de début
    # --------------------------------------------------------

    run_now = (
        datetime.now(timezone.utc)
    )

    # --------------------------------------------------------
    # Scan
    # --------------------------------------------------------

    results, stock_provider = (
        scan_all(
            threshold=args.threshold,
            limit=args.limit,
            now=run_now,
        )
    )

    # --------------------------------------------------------
    # Heure réelle de fin pour fraîcheur
    # --------------------------------------------------------

    freshness_now = (
        datetime.now(timezone.utc)
    )

    # --------------------------------------------------------
    # Message
    # --------------------------------------------------------

    message = format_message(
        results,
        stock_provider,
        top=args.top,
        threshold=args.threshold,
        now=freshness_now,
    )

    print(
        "\n" + message
    )

    # --------------------------------------------------------
    # SIGNAL FORT ?
    # --------------------------------------------------------

    strong_signal = (
        has_signal(results)
    )

    # --------------------------------------------------------
    # SLACK
    # --------------------------------------------------------

    if strong_signal:

        print(
            "\n🔥 SIGNAL FORT détecté "
            "— envoi Slack..."
        )

        send_slack(
            os.getenv(
                "SLACK_WEBHOOK_URL"
            ),
            message,
        )

    else:

        print(
            "\nAucun SIGNAL FORT "
            "— Slack non envoyé."
        )

    # --------------------------------------------------------
    # EMAIL — TOUJOURS ENVOYÉ
    # --------------------------------------------------------

    print(
        "\nEnvoi de l'email du scan..."
    )

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

        subject=(
            "Scan multi-actifs V4 — "
            f"{run_now.strftime('%Y-%m-%d %H:%M')}"
        ),

        body=message,
    )
