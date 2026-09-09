"""
Lance le scan multi-actifs et envoie les résultats sur Slack + Email.

V4 :

Score maximum = 100
- Tendance 1H : 25
- EMA          : 20
- RSI          : 15
- Volume       : 15
- Breakout     : 25

SIGNAL FORT :
    score >= seuil
    + BREAKOUT
    + VOLUME

ATTENTE :
    score >= seuil mais trigger incomplet

SOUS SEUIL :
    score < seuil

Notifications :
    Email = chaque scan
    Slack = uniquement SIGNAL FORT

Sources :
    Crypto       = Binance
    Forex        = Twelve Data
    Actions      = Twelve Data 03:00-19:00 UTC,
                   Yahoo sinon
    Indices      = Yahoo
    Commodities  = Twelve Data/Yahoo selon configuration
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


# ======================================================================
# PAUSES
# ======================================================================

PAUSE_BY_PROVIDER = {
    "binance": 0.3,
    "twelvedata": 9.0,
    "yahoo": 0.5,
}


# ======================================================================
# FENÊTRE TWELVE DATA
# ======================================================================

def twelvedata_window_active(
    now=None
):
    now = (
        now
        or datetime.now(
            timezone.utc
        )
    )

    return (
        3 <= now.hour < 19
    )


# ======================================================================
# CATEGORIES
# ======================================================================

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
        result = []

        for entry in entries:

            if isinstance(
                entry,
                dict
            ):
                value = entry.get(
                    key
                )

                if value:
                    result.append(
                        value
                    )

            else:
                # Compatibilité si assets.py
                # contient directement des strings.
                result.append(
                    entry
                )

        return result

    categories = {

        # ==============================================================
        # CRYPTO
        # ==============================================================

        "🪙 Crypto": [
            {
                "symbols": resolve_symbols(
                    CRYPTO,
                    "binance"
                ),
                "fetcher": klines_binance,
                "interval": "5m",
                "pause":
                    PAUSE_BY_PROVIDER[
                        "binance"
                    ],
            }
        ],

        # ==============================================================
        # FOREX
        # ==============================================================

        "💵 Forex": [
            {
                "symbols": resolve_symbols(
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

        # ==============================================================
        # ACTIONS
        # ==============================================================

        "📈 Actions": [
            {
                "symbols": resolve_symbols(
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

        # ==============================================================
        # INDICES
        # ==============================================================

        "📊 Indices": [
            {
                "symbols": resolve_symbols(
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

        # ==============================================================
        # MATIÈRES PREMIÈRES
        # ==============================================================

        "🛢️ Matières premières": [

            {
                "symbols": resolve_symbols(
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
                "symbols": resolve_symbols(
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
            },
        ],
    }

    return (
        categories,
        stock_provider
    )


# ======================================================================
# SCAN COMPLET
# ======================================================================

def scan_all(
    threshold=75,
    limit=1000,
    now=None
):

    categories, stock_provider = (
        build_categories(
            now
        )
    )

    results = {}

    for name, groups in categories.items():

        dfs = []

        for cfg in groups:

            symbols = cfg[
                "symbols"
            ]

            if not symbols:
                continue

            print(
                f"--- {name} "
                f"({cfg['fetcher'].__name__}, "
                f"pause={cfg['pause']}s) ---"
            )

            df = scan(
                symbols,
                fetcher=cfg[
                    "fetcher"
                ],
                interval=cfg[
                    "interval"
                ],
                limit=limit,
                threshold=threshold,
                pause=cfg[
                    "pause"
                ],
            )

            if not df.empty:
                dfs.append(
                    df
                )

        if dfs:

            merged = pd.concat(
                dfs,
                ignore_index=True
            )

            merged = (
                merged
                .sort_values(
                    "best_score",
                    ascending=False
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


# ======================================================================
# SIGNALS
# ======================================================================

def has_signal(
    all_results
):

    for df in all_results.values():

        if (
            not df.empty
            and
            "status" in df.columns
            and
            (
                df["status"]
                == "SIGNAL FORT"
            ).any()
        ):
            return True

    return False


def count_signals(
    all_results
):

    count = 0

    for df in all_results.values():

        if (
            not df.empty
            and
            "status" in df.columns
        ):

            count += int(
                (
                    df["status"]
                    == "SIGNAL FORT"
                ).sum()
            )

    return count


# ======================================================================
# CONDITIONS MANQUANTES
# ======================================================================

def missing_conditions(
    row,
    threshold=75
):

    best_score = max(
        row["score_long"],
        row["score_short"]
    )

    direction = row[
        "direction"
    ]

    missing = []

    if best_score < threshold:
        missing.append(
            f"SCORE < {threshold:.0f}"
        )

    breakout_ok = bool(
        row.get(
            "breakout_ok",
            False
        )
    )

    volume_ok = bool(
        row.get(
            "volume_ok",
            False
        )
    )

    if not breakout_ok:
        missing.append(
            "BREAKOUT"
        )

    if not volume_ok:
        missing.append(
            "VOLUME"
        )

    if not missing:
        return "aucune"

    return " + ".join(
        missing
    )


# ======================================================================
# FORMAT RELVOL
# ======================================================================

def relvol_text(
    value
):

    if pd.isna(value):
        return "n/d"

    return f"{float(value):.2f}"


# ======================================================================
# BLOC SCORE
# ======================================================================

def format_score_block(
    row
):

    direction = row[
        "direction"
    ]

    best = max(
        row["score_long"],
        row["score_short"]
    )

    relvol = relvol_text(
        row.get(
            "relvol",
            float("nan")
        )
    )

    rsi = row.get(
        "rsi",
        float("nan")
    )

    if pd.isna(rsi):
        rsi_text = "n/d"
    else:
        rsi_text = f"{float(rsi):.1f}"

    trend_text = (
        f"{row['trend_pts']:+.0f}/25"
    )

    ema_text = (
        f"{row['ema_pts']:+.0f}/20"
    )

    rsi_pts_text = (
        f"{row['rsi_pts']:+.0f}/15"
    )

    volume_text = (
        f"{row['volume_pts']:+.0f}/15"
    )

    breakout_text = (
        f"{row['breakout_pts']:+.0f}/25"
    )

    lines = [

        f"{row['symbol']} "
        f"{direction} — "
        f"SCORE {best:.0f}/100",

        "",

        f"Tendance 1H       {trend_text}",

        f"EMA               {ema_text}",

        f"RSI               {rsi_pts_text} "
        f"(RSI {rsi_text})",

        f"Volume            {volume_text} "
        f"(RelVol {relvol})",

        f"Breakout          {breakout_text}",

        "------------------------",

        f"TOTAL              {best:.0f}/100",
    ]

    if row["status"] == "SIGNAL FORT":

        lines.extend(
            [
                "",
                "🔥 TRIGGER : BREAKOUT + VOLUME",
                "➡️ SIGNAL FORT",
                "🔥 PRISE DE POSITION IMMÉDIATE",
            ]
        )

    elif row["status"] == "ATTENTE":

        missing = missing_conditions(
            row
        )

        lines.extend(
            [
                "",
                "⏳ TRIGGER INCOMPLET",
                f"➡️ ATTENTE — manque : {missing}",
            ]
        )

    else:

        missing = missing_conditions(
            row
        )

        lines.extend(
            [
                "",
                f"🟡 SOUS SEUIL — manque : {missing}",
            ]
        )

    return "\n".join(
        lines
    )


# ======================================================================
# MESSAGE SLACK
# ======================================================================

def format_signal_message(
    all_results,
    stock_provider,
    threshold=75,
    now=None
):

    now = (
        now
        or datetime.now(
            timezone.utc
        )
    )

    ts = now.strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    lines = [

        "🔥 SIGNALS FORTS",

        f"{ts}",

        f"Seuil : "
        f"{threshold:.0f}/100",

        "Conditions : "
        "SCORE ≥ seuil + BREAKOUT + VOLUME",

        f"Source Actions/Matières : "
        f"{stock_provider}",

        "Indices : yahoo",

        "",
    ]

    total = 0

    for name, df in all_results.items():

        if df.empty:
            continue

        strong = df[
            df["status"]
            == "SIGNAL FORT"
        ]

        if strong.empty:
            continue

        lines.append(
            f"--- {name} ---"
        )

        lines.append("")

        for _, row in strong.iterrows():

            total += 1

            lines.append(
                format_score_block(
                    row
                )
            )

            lines.append("")

    lines.append(
        f"Total SIGNAL FORT : {total}"
    )

    return "\n".join(
        lines
    )


# ======================================================================
# FRAÎCHEUR
# ======================================================================

def freshness_summary(
    all_results,
    now=None
):

    now = (
        now
        or datetime.now(
            timezone.utc
        )
    )

    lines = []

    for name, df in all_results.items():

        if (
            df.empty
            or
            "last_candle"
            not in df.columns
        ):
            continue

        most_recent = pd.to_datetime(
            df["last_candle"],
            utc=True,
            errors="coerce"
        ).max()

        if pd.isna(
            most_recent
        ):
            continue

        age_min = (
            now
            - most_recent.to_pydatetime()
        ).total_seconds() / 60

        if age_min > 90:

            flag = (
                " ⚠️ possible donnée figée"
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


# ======================================================================
# RAPPORT EMAIL
# ======================================================================

def format_email_message(
    all_results,
    stock_provider,
    top=5,
    threshold=75,
    now=None
):

    now = (
        now
        or datetime.now(
            timezone.utc
        )
    )

    ts = now.strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    total_signals = count_signals(
        all_results
    )

    lines = [

        "📊 SCAN MULTI-ACTIFS",

        f"{ts}",

        "",

        f"Seuil stratégique : "
        f"{threshold:.0f}/100",

        "Score : "
        "Trend 25 + EMA 20 + RSI 15 "
        "+ Volume 15 + Breakout 25",

        f"Source Actions/Matières : "
        f"{stock_provider}",

        "Indices : yahoo",

        "",

    ]

    # ==================================================================
    # 1. SIGNAL FORT
    # ==================================================================

    lines.extend(
        [
            "🔥 PRISE DE POSITION IMMÉDIATE",

            "Conditions : "
            "score ≥ seuil + breakout + volume",

            "",
        ]
    )

    if total_signals == 0:

        lines.extend(
            [
                "Aucun actif ne remplit "
                "actuellement toutes les conditions.",
                "",
            ]
        )

    else:

        lines.extend(
            [
                f"{total_signals} actif(s) "
                "remplissent les conditions :",
                "",
            ]
        )

        for name, df in all_results.items():

            if df.empty:
                continue

            strong = df[
                df["status"]
                == "SIGNAL FORT"
            ]

            if strong.empty:
                continue

            lines.append(
                f"--- {name} ---"
            )

            lines.append("")

            # Tous les SIGNAL FORT
            for _, row in strong.iterrows():

                lines.append(
                    format_score_block(
                        row
                    )
                )

                lines.append("")

    # ==================================================================
    # 2. À SURVEILLER
    # ==================================================================

    lines.extend(
        [
            "👀 À SURVEILLER",

            f"Top {top} par catégorie "
            "hors SIGNAL FORT.",

            "Le classement inclut "
            "les actifs sous le seuil.",

            "",
        ]
    )

    for name, df in all_results.items():

        lines.append(
            f"--- {name} ---"
        )

        if df.empty:

            lines.extend(
                [
                    "Aucune donnée exploitable.",
                    "",
                ]
            )

            continue

        watch = df[
            df["status"]
            != "SIGNAL FORT"
        ].copy()

        if watch.empty:

            lines.extend(
                [
                    "Tous les actifs disponibles "
                    "sont actuellement en SIGNAL FORT.",
                    "",
                ]
            )

            continue

        watch["best_score"] = (
            watch[
                [
                    "score_long",
                    "score_short",
                ]
            ]
            .max(axis=1)
        )

        watch = (
            watch
            .sort_values(
                "best_score",
                ascending=False
            )
            .head(top)
        )

        for rank, (_, row) in enumerate(
            watch.iterrows(),
            start=1
        ):

            score_value = row[
                "best_score"
            ]

            direction = row[
                "direction"
            ]

            status = row[
                "status"
            ]

            missing = missing_conditions(
                row,
                threshold
            )

            relvol = relvol_text(
                row.get(
                    "relvol",
                    float("nan")
                )
            )

            if status == "ATTENTE":

                status_text = (
                    "⏳ ATTENTE"
                )

            else:

                status_text = (
                    "🟡 SOUS SEUIL"
                )

            lines.extend(
                [
                    f"{rank}. "
                    f"{row['symbol']} — "
                    f"{direction} — "
                    f"{score_value:.0f}/100",

                    f"   {status_text}",

                    f"   Manque : "
                    f"{missing}",

                    f"   Tendance {row['trend_pts']:+.0f} | "
                    f"EMA {row['ema_pts']:+.0f} | "
                    f"RSI {row['rsi_pts']:+.0f} | "
                    f"Volume {row['volume_pts']:+.0f} | "
                    f"Breakout {row['breakout_pts']:+.0f}",

                    f"   RelVol : {relvol}",

                    "",
                ]
            )

    # ==================================================================
    # 3. FRAÎCHEUR
    # ==================================================================

    fresh_lines = freshness_summary(
        all_results,
        now
    )

    if fresh_lines:

        lines.extend(
            [
                "--- Fraîcheur des données ---",
            ]
        )

        lines.extend(
            fresh_lines
        )

        lines.append("")

    # ==================================================================
    # 4. RÉSUMÉ
    # ==================================================================

    lines.extend(
        [
            "--- Résumé ---",

            f"🔥 Entrées immédiates : "
            f"{total_signals}",

            f"👀 Catégories surveillées : "
            f"{len(all_results)}",

            "",

            "RelVol = volume de la dernière "
            "bougie clôturée / moyenne des "
            "20 bougies clôturées précédentes.",

            "Seuil volume : RelVol ≥ 1,50.",

            "Breakout renforcé : "
            "+0,10 % au-dessus du précédent "
            "plus haut / "
            "-0,10 % sous le précédent plus bas.",
        ]
    )

    return "\n".join(
        lines
    )


# ======================================================================
# MAIN
# ======================================================================

if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--threshold",
        type=float,
        default=75
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help=(
            "Nombre de bougies récupérées "
            "par appel."
        )
    )

    parser.add_argument(
        "--top",
        type=int,
        default=5
    )

    args = parser.parse_args()

    now = datetime.now(
        timezone.utc
    )

    # ==============================================================
    # SCAN
    # ==============================================================

    results, stock_provider = scan_all(
        threshold=args.threshold,
        limit=args.limit,
        now=now
    )

    now_for_notifications = (
        datetime.now(
            timezone.utc
        )
    )

    # ==============================================================
    # RAPPORT EMAIL
    # ==============================================================

    email_message = format_email_message(
        results,
        stock_provider,
        top=args.top,
        threshold=args.threshold,
        now=now_for_notifications
    )

    print(
        "\n"
        + email_message
    )

    # ==============================================================
    # SLACK — UNIQUEMENT SIGNAL FORT
    # ==============================================================

    if has_signal(results):

        slack_message = (
            format_signal_message(
                results,
                stock_provider,
                threshold=args.threshold,
                now=now_for_notifications
            )
        )

        send_slack(
            os.getenv(
                "SLACK_WEBHOOK_URL"
            ),
            slack_message
        )

    else:

        print(
            "\nAucun SIGNAL FORT "
            "— notification Slack non envoyée."
        )

    # ==============================================================
    # EMAIL — TOUJOURS ENVOYÉ
    # ==============================================================

    signal_count = count_signals(
        results
    )

    if signal_count:

        subject = (
            f"🔥 {signal_count} SIGNAL FORT"
            f"{'S' if signal_count > 1 else ''} "
            f"| 👀 À surveiller — "
            f"{now.strftime('%Y-%m-%d %H:%M')}"
        )

    else:

        subject = (
            f"👀 Aucun SIGNAL FORT "
            f"| À surveiller — "
            f"{now.strftime('%Y-%m-%d %H:%M')}"
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

        body=email_message
    )
