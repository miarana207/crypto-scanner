```python
"""
Lance le scan multi-actifs et envoie les résultats
sur Slack + Email.

Score V4 :
- Tendance 1H : 30 points
- EMA          : 20 points
- RSI          : 15 points
- Volume       : 15 points
- Breakout     : 20 points
- TOTAL        : 100 points

Déclencheur :
- SIGNAL FORT = score >= seuil + Breakout + Volume
- ATTENTE     = score >= seuil mais trigger incomplet
- SOUS SEUIL  = score < seuil

Notification :
- EMAIL : envoyé à chaque scan
- SLACK : envoyé uniquement lorsqu'il existe
          au moins un SIGNAL FORT

Breakout :
- LONG  : clôture > ancien plus haut + 0,10 %
- SHORT : clôture < ancien plus bas - 0,10 %
"""

import argparse
import os

from datetime import datetime, timezone

import pandas as pd

from backtest import (
    klines as klines_binance
)

from data_sources import (
    klines_yahoo,
    klines_twelvedata
)

from scan import scan

from notify import (
    send_slack,
    send_email
)

from assets import (
    CRYPTO,
    ACTIONS,
    FOREX,
    INDICES,
    COMMODITIES,
    COMMODITIES_YAHOO_ONLY
)


# ============================================================
# PAUSES PAR FOURNISSEUR
# ============================================================

PAUSE_BY_PROVIDER = {

    "binance": 0.3,

    "twelvedata": 9.0,

    "yahoo": 0.5,
}


# ============================================================
# FENÊTRE TWELVE DATA
# ============================================================

def twelvedata_window_active(
    now=None
):

    now = (
        now
        or
        datetime.now(timezone.utc)
    )

    return (
        3 <= now.hour < 19
    )


# ============================================================
# CONSTRUCTION DES CATÉGORIES
# ============================================================

def build_categories(
    now=None
):

    use_twelvedata = (
        twelvedata_window_active(
            now
        )
    )

    stock_provider = (
        "twelvedata"
        if use_twelvedata
        else
        "yahoo"
    )

    stock_fetcher = (
        klines_twelvedata
        if use_twelvedata
        else
        klines_yahoo
    )

    stock_pause = (
        PAUSE_BY_PROVIDER[
            stock_provider
        ]
    )

    def resolve_symbols(
        entries,
        key
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

                "fetcher":
                klines_binance,

                "interval": "5m",

                "pause":
                PAUSE_BY_PROVIDER[
                    "binance"
                ],
            }
        ],

        # ====================================================
        # FOREX
        # ====================================================

        "💵 Forex": [

            {

                "symbols":
                resolve_symbols(
                    FOREX,
                    "twelvedata"
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

        # ====================================================
        # ACTIONS
        # ====================================================

        "📈 Actions": [

            {

                "symbols":
                resolve_symbols(
                    ACTIONS,
                    stock_provider
                ),

                "fetcher":
                stock_fetcher,

                "interval": "15m",

                "pause":
                stock_pause,
            }
        ],

        # ====================================================
        # INDICES
        # ====================================================

        "📊 Indices": [

            {

                "symbols":
                resolve_symbols(
                    INDICES,
                    "yahoo"
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

        # ====================================================
        # MATIÈRES PREMIÈRES
        # ====================================================

        "🛢️ Matières premières": [

            {

                "symbols":
                resolve_symbols(
                    COMMODITIES,
                    stock_provider
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
                    "yahoo"
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
    }

    return (
        categories,
        stock_provider
    )


# ============================================================
# SCAN COMPLET
# ============================================================

def scan_all(
    threshold=75,
    limit=1000,
    now=None
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

                fetcher=
                cfg["fetcher"],

                interval=
                cfg["interval"],

                limit=limit,

                threshold=
                threshold,

                pause=
                cfg["pause"],
            )

            if not df.empty:

                dfs.append(df)

        if dfs:

            merged = pd.concat(
                dfs,
                ignore_index=True
            )

            merged["max_score"] = (
                merged[
                    [
                        "score_long",
                        "score_short"
                    ]
                ]
                .max(axis=1)
            )

            merged = (
                merged
                .sort_values(
                    "max_score",
                    ascending=False
                )
                .drop(
                    columns="max_score"
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
        stock_provider
    )


# ============================================================
# SIGNAL FORT
# ============================================================

def get_strong_signals(
    all_results
):

    strong_signals = []

    for name, df in (
        all_results.items()
    ):

        if df.empty:
            continue

        if "status" not in df.columns:
            continue

        strong = df[
            df["status"] ==
            "SIGNAL FORT"
        ]

        for _, row in (
            strong.iterrows()
        ):

            strong_signals.append(
                (
                    name,
                    row
                )
            )

    return strong_signals


def has_signal(
    all_results
):

    return (
        len(
            get_strong_signals(
                all_results
            )
        ) > 0
    )


# ============================================================
# FRAÎCHEUR DES DONNÉES
# ============================================================

def freshness_summary(
    all_results,
    now=None
):

    now = (
        now
        or
        datetime.now(timezone.utc)
    )

    lines = []

    for name, df in (
        all_results.items()
    ):

        if (
            df.empty
            or
            "last_candle"
            not in df.columns
        ):

            continue

        most_recent = df[
            "last_candle"
        ].max()

        age_min = (
            now - most_recent
        ).total_seconds() / 60

        flag = (
            " ⚠️ possible donnée figée"
            if age_min > 90
            else
            ""
        )

        lines.append(
            f"{name}: dernière bougie "
            f"{most_recent.strftime('%Y-%m-%d %H:%M UTC')} "
            f"(il y a {age_min:.0f} min)"
            f"{flag}"
        )

    return lines


# ============================================================
# BLOC DÉTAILLÉ D'UN SCORE
# ============================================================

def format_score_block(
    row,
    show_trigger=True
):

    direction = row["direction"]

    best = max(
        row["score_long"],
        row["score_short"]
    )

    trend_value = row[
        "trend_pts"
    ]

    ema_value = row[
        "ema_pts"
    ]

    rsi_value = row[
        "rsi_pts"
    ]

    volume_value = row[
        "volume_pts"
    ]

    breakout_value = row[
        "breakout_pts"
    ]

    lines = [

        f"{row['symbol']} "
        f"{direction} — "
        f"SCORE {best:.0f}/100",

        "",

        f"Tendance 1H       "
        f"{trend_value:+.0f}",

        f"EMA               "
        f"{ema_value:+.0f}",

        f"RSI               "
        f"{rsi_value:+.0f}",

        f"Volume            "
        f"{volume_value:+.0f}",

        f"Breakout          "
        f"{breakout_value:+.0f}",

        "------------------------",

        f"TOTAL              "
        f"{best:.0f}/100",
    ]

    if show_trigger:

        if row["status"] == "SIGNAL FORT":

            lines.extend([

                "",

                "🔥 TRIGGER : "
                "BREAKOUT + VOLUME",

                "➡️ PRISE DE POSITION "
                "IMMÉDIATE",
            ])

        elif row["status"] == "ATTENTE":

            missing = row.get(
                "trigger_missing",
                "TRIGGER"
            )

            lines.extend([

                "",

                f"⏳ TRIGGER INCOMPLET : "
                f"{missing}",

                "➡️ À SURVEILLER",
            ])

        else:

            lines.extend([

                "",

                "⏳ CONDITIONS "
                "NON RÉUNIES",

                "➡️ À SURVEILLER",
            ])

    return "\n".join(
        lines
    )


# ============================================================
# DIRECTION POUR LA WATCHLIST
# ============================================================

def best_direction(
    row
):

    if (
        row["score_long"]
        >=
        row["score_short"]
    ):

        return "LONG"

    return "SHORT"


# ============================================================
# CONDITIONS MANQUANTES
# ============================================================

def missing_conditions(
    row,
    threshold=75
):

    direction = best_direction(
        row
    )

    best_score = max(
        row["score_long"],
        row["score_short"]
    )

    # --------------------------------------------------------
    # 1. SCORE INSUFFISANT
    # --------------------------------------------------------
    # Si le score est inférieur au seuil, on considère que
    # le score est la condition bloquante principale.
    #
    # IMPORTANT :
    # On ne rajoute PAS BREAKOUT et VOLUME ici.
    # Cela évite un diagnostic trompeur du type :
    #
    # "SCORE < 75 + BREAKOUT + VOLUME"
    #
    # alors que le véritable état est simplement :
    #
    # "SOUS SEUIL — manque : SCORE"
    # --------------------------------------------------------

    if best_score < threshold:

        return "SCORE"

    # --------------------------------------------------------
    # 2. SCORE SUFFISANT
    # --------------------------------------------------------
    # Le score ayant atteint le seuil, on vérifie maintenant
    # les deux conditions qui servent de déclencheur :
    #
    # BREAKOUT + VOLUME
    # --------------------------------------------------------

    breakout = row["breakout_pts"]

    volume = row["volume_pts"]

    if direction == "LONG":

        breakout_ok = (
            breakout == 20
        )

        volume_ok = (
            volume == 15
        )

    else:

        breakout_ok = (
            breakout == -20
        )

        volume_ok = (
            volume == -15
        )

    missing = []

    if not breakout_ok:

        missing.append(
            "BREAKOUT"
        )

    if not volume_ok:

        missing.append(
            "VOLUME"
        )

    # --------------------------------------------------------
    # 3. BREAKOUT + VOLUME OK
    # --------------------------------------------------------

    if not missing:

        return "Aucune"

    return " + ".join(
        missing
    )


# ============================================================
# FORMAT WATCHLIST
# ============================================================

def format_watchlist_block(
    name,
    df,
    top=5,
    threshold=75
):

    lines = []

    if df.empty:

        lines.extend([

            name,

            "  Aucune donnée exploitable.",

            ""
        ])

        return lines

    diagnostic = df.copy()

    diagnostic["best_score"] = (
        diagnostic[
            [
                "score_long",
                "score_short"
            ]
        ]
        .max(axis=1)
    )

    diagnostic["best_direction"] = (
        diagnostic.apply(
            best_direction,
            axis=1
        )
    )

    diagnostic = (
        diagnostic
        .sort_values(
            "best_score",
            ascending=False
        )
        .head(top)
    )

    lines.append(
        name
    )

    for _, row in (
        diagnostic.iterrows()
    ):

        score_value = (
            row["best_score"]
        )

        direction = (
            row["best_direction"]
        )

        status = row.get(
            "status",
            "-"
        )

        if status == "SIGNAL FORT":

            status_text = (
                "🔥 PRISE DE POSITION"
            )

        elif (
            score_value >= threshold
        ):

            missing = (
                missing_conditions(
                    row,
                    threshold
                )
            )

            status_text = (
                f"⏳ ATTENTE — "
                f"manque : {missing}"
            )

        else:

            missing = (
                missing_conditions(
                    row,
                    threshold
                )
            )

            status_text = (
                f"SOUS SEUIL — "
                f"manque : {missing}"
            )

        lines.append(

            f"  {row['symbol']}: "
            f"{score_value:.0f}/100 "
            f"({direction}) — "
            f"{status_text}"
        )

    lines.append("")

    return lines


# ============================================================
# MESSAGE COMPLET
# ============================================================

def format_message(
    all_results,
    stock_provider,
    top=5,
    threshold=75,
    now=None
):

    now = (
        now
        or
        datetime.now(timezone.utc)
    )

    ts = now.strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    strong_signals = (
        get_strong_signals(
            all_results
        )
    )

    lines = [

        f"📊 Scan multi-actifs — "
        f"{ts} "
        f"(seuil {threshold:.0f}/100)",

        f"Source "
        f"Actions/Matières premières "
        f"ce run : {stock_provider} | "
        f"Indices : yahoo",

        ""
    ]

    # ========================================================
    # 1. PRISE DE POSITION IMMÉDIATE
    # ========================================================

    lines.append(
        "🔥 PRISE DE POSITION IMMÉDIATE"
    )

    lines.append(
        "Conditions : "
        f"score ≥ {threshold:.0f} "
        "+ BREAKOUT "
        "+ VOLUME"
    )

    lines.append("")

    if not strong_signals:

        lines.append(
            "Aucun actif ne remplit "
            "toutes les conditions."
        )

        lines.append("")

    else:

        for name, row in (
            strong_signals
        ):

            lines.append(
                f"--- {name} ---"
            )

            lines.append(
                format_score_block(
                    row,
                    show_trigger=True
                )
            )

            lines.append("")


    # ========================================================
    # 2. À SURVEILLER
    # ========================================================

    lines.append(
        "👀 À SURVEILLER"
    )

    lines.append(
        "Top 5 de chaque catégorie "
        "— indépendamment du seuil."
    )

    lines.append("")

    for name, df in (
        all_results.items()
    ):

        lines.extend(
            format_watchlist_block(
                name,
                df,
                top=top,
                threshold=threshold
            )
        )


    # ========================================================
    # 3. FRAÎCHEUR
    # ========================================================

    fresh_lines = (
        freshness_summary(
            all_results,
            now
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
# PROGRAMME PRINCIPAL
# ============================================================

if __name__ == "__main__":

    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--threshold",
        type=float,
        default=75
    )

    ap.add_argument(
        "--limit",
        type=int,
        default=1000,

        help=(
            "Nombre de bougies "
            "récupérées par appel."
        )
    )

    ap.add_argument(
        "--top",
        type=int,
        default=5
    )

    args = ap.parse_args()

    # --------------------------------------------------------
    # HEURE DE DÉBUT DU SCAN
    # --------------------------------------------------------

    now = datetime.now(
        timezone.utc
    )

    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    results, stock_provider = (
        scan_all(
            threshold=args.threshold,
            limit=args.limit,
            now=now
        )
    )

    # --------------------------------------------------------
    # HEURE DE GÉNÉRATION
    # --------------------------------------------------------

    now_for_message = (
        datetime.now(
            timezone.utc
        )
    )

    # --------------------------------------------------------
    # MESSAGE
    # --------------------------------------------------------

    message = format_message(

        results,

        stock_provider,

        top=args.top,

        threshold=args.threshold,

        now=now_for_message
    )

    print(
        "\n" + message
    )

    # ========================================================
    # SLACK
    # ========================================================

    if has_signal(results):

        print(
            "\nSIGNAL FORT détecté "
            "— envoi Slack."
        )

        send_slack(

            os.getenv(
                "SLACK_WEBHOOK_URL"
            ),

            message
        )

    else:

        print(
            "\nAucun SIGNAL FORT "
            "— Slack non envoyé."
        )


    # ========================================================
    # EMAIL
    #
    # IMPORTANT :
    # l'email est envoyé À CHAQUE SCAN.
    # ========================================================

    strong_count = len(
        get_strong_signals(
            results
        )
    )

    if strong_count:

        subject = (
            f"🔥 {strong_count} SIGNAL(S) FORT(S) "
            f"| 👀 À SURVEILLER "
            f"— {now.strftime('%Y-%m-%d %H:%M')}"
        )

    else:

        subject = (
            f"👀 Aucun SIGNAL FORT "
            f"| À SURVEILLER "
            f"— {now.strftime('%Y-%m-%d %H:%M')}"
        )


    print(
        "\nEnvoi de l'email du scan..."
    )

    send_email(

        smtp_host=os.getenv(
            "SMTP_HOST",
            "smtp.gmail.com"
        ),

        smtp_port=int(
            os.getenv(
                "SMTP_PORT"
            )
            or
            "465"
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

        subject=subject,

        body=message
    )
```
