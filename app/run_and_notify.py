"""
Scanner multi-actifs V4.

Règles :

Score maximum = 100

    Tendance 1H : 25
    EMA          : 20
    RSI          : 15
    Volume       : 15
    Breakout     : 25

SIGNAL FORT :
    score >= seuil
    + BREAKOUT
    + VOLUME

ATTENTE :
    score >= seuil
    mais trigger incomplet

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
    Commodities  = Twelve Data/Yahoo
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
# RESOLUTION DES SYMBOLES
# ======================================================================

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

            result.append(
                entry
            )

    return result


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
                "fetcher":
                    klines_binance,
                "interval":
                    "5m",
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
                "interval":
                    "15m",
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
                "interval":
                    "15m",
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
                "interval":
                    "15m",
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
                "interval":
                    "15m",
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
                "interval":
                    "15m",
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
# SCAN GLOBAL
# ======================================================================

def scan_all(
    threshold=75,
    limit=1000,
    top=5
):

    now = datetime.now(
        timezone.utc
    )

    categories, stock_provider = (
        build_categories(
            now
        )
    )

    all_results = {}

    for name, groups in (
        categories.items()
    ):

        frames = []

        for group in groups:

            symbols = group[
                "symbols"
            ]

            if not symbols:
                continue

            print(
                f"\n{name} — "
                f"{len(symbols)} actifs"
            )

            df = scan(
                symbols=symbols,
                fetcher=group[
                    "fetcher"
                ],
                interval=group[
                    "interval"
                ],
                limit=limit,
                threshold=threshold,
                pause=group[
                    "pause"
                ],
            )

            if (
                df is not None
                and not df.empty
            ):

                frames.append(
                    df
                )

        if frames:

            merged = pd.concat(
                frames,
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

            all_results[
                name
            ] = merged

        else:

            all_results[
                name
            ] = pd.DataFrame()

    return (
        all_results,
        stock_provider
    )


# ======================================================================
# SIGNAL FORT
# ======================================================================

def has_signal(
    all_results
):

    for df in all_results.values():

        if (
            not df.empty
            and "status" in df.columns
            and (
                df["status"]
                == "SIGNAL FORT"
            ).any()
        ):

            return True

    return False


def count_signals(
    all_results
):

    total = 0

    for df in all_results.values():

        if (
            not df.empty
            and "status" in df.columns
        ):

            total += int(
                (
                    df["status"]
                    == "SIGNAL FORT"
                ).sum()
            )

    return total


# ======================================================================
# FORMATAGE
# ======================================================================

def relvol_text(
    value
):

    if pd.isna(value):
        return "n/d"

    return f"{float(value):.2f}"


def missing_conditions(
    row,
    threshold=75
):

    missing = []

    best_score = max(
        float(
            row["score_long"]
        ),
        float(
            row["score_short"]
        )
    )

    if best_score < threshold:

        missing.append(
            f"SCORE < {threshold:.0f}"
        )

    if not bool(
        row.get(
            "breakout_ok",
            False
        )
    ):

        missing.append(
            "BREAKOUT"
        )

    if not bool(
        row.get(
            "volume_ok",
            False
        )
    ):

        missing.append(
            "VOLUME"
        )

    if not missing:
        return "aucune"

    return " + ".join(
        missing
    )


def format_score_block(
    row
):

    score_value = float(
        row["best_score"]
    )

    direction = row[
        "direction"
    ]

    status = row[
        "status"
    ]

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

    return (
        f"{row['symbol']} | "
        f"{score_value:.0f}/100 "
        f"{direction} | "
        f"{status}\n"
        f"   "
        f"T:{row['trend_pts']:.0f} "
        f"E:{row['ema_pts']:.0f} "
        f"R:{row['rsi_pts']:.0f} "
        f"V:{row['volume_pts']:.0f} "
        f"B:{row['breakout_pts']:.0f} "
        f"| RSI:{rsi_text} "
        f"| RelVol:{relvol}"
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

    lines = [
        "🕐 FRAÎCHEUR DES DONNÉES"
    ]

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
                " ⚠️ donnée potentiellement figée"
            )

        else:

            flag = ""

        lines.append(
            f"{name}: "
            f"{most_recent.strftime('%H:%M')} UTC "
            f"({age_min:.0f} min){flag}"
        )

    return "\n".join(
        lines
    )


# ======================================================================
# MESSAGE PRINCIPAL
# ======================================================================

def format_full_message(
    all_results,
    stock_provider,
    threshold=75,
    top=5,
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

    strong_count = count_signals(
        all_results
    )

    lines = [

        "📊 SCAN MULTI-ACTIFS V4",

        f"{ts}",

        f"Seuil : "
        f"{threshold:.0f}/100",

        "",

        "🔥 SIGNAL FORT",

        "Conditions : "
        "SCORE ≥ seuil + BREAKOUT + VOLUME",

        f"Total SIGNAL FORT : "
        f"{strong_count}",

        f"Source Actions/Matières : "
        f"{stock_provider}",

        "Indices : yahoo",

        "",
    ]

    # ==============================================================
    # SIGNALS FORTS
    # ==============================================================

    if strong_count == 0:

        lines.extend(
            [
                "Aucun signal fort "
                "sur ce scan.",
                "",
            ]
        )

    else:

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

            lines.append(
                f"--- {name} ---"
            )

            for _, row in (
                strong.iterrows()
            ):

                lines.append(
                    format_score_block(
                        row
                    )
                )

            lines.append("")

    # ==============================================================
    # À SURVEILLER
    # ==============================================================

    lines.extend(
        [
            "👀 À SURVEILLER",
            f"Top {top} par catégorie.",
            "Les actifs sous le seuil "
            "restent affichés.",
            "",
        ]
    )

    for name, df in (
        all_results.items()
    ):

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

        watch = df.copy()

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

            missing = (
                missing_conditions(
                    row,
                    threshold
                )
            )

            relvol = relvol_text(
                row.get(
                    "relvol",
                    float("nan")
                )
            )

            lines.append(
                f"{rank}. "
                f"{row['symbol']} | "
                f"{row['best_score']:.0f}/100 "
                f"{row['direction']} | "
                f"{row['status']}"
            )

            lines.append(
                f"   "
                f"T:{row['trend_pts']:.0f} "
                f"E:{row['ema_pts']:.0f} "
                f"R:{row['rsi_pts']:.0f} "
                f"V:{row['volume_pts']:.0f} "
                f"B:{row['breakout_pts']:.0f}"
                f" | RelVol:{relvol}"
            )

            lines.append(
                f"   Manque : {missing}"
            )

        lines.append("")

    # ==============================================================
    # FRAÎCHEUR
    # ==============================================================

    lines.extend(
        [
            "",
            freshness_summary(
                all_results,
                now
            ),
        ]
    )

    return "\n".join(
        lines
    )


# ======================================================================
# MAIN
# ======================================================================

def main():

    parser = argparse.ArgumentParser(
        description=
        "Scanner multi-actifs V4"
    )

    parser.add_argument(
        "--threshold",
        type=float,
        default=75
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=1000
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

    print(
        f"\nScan multi-actifs — "
        f"{now.strftime('%Y-%m-%d %H:%M')} UTC "
        f"(seuil {args.threshold:.0f}/100)"
    )

    print(
        f"Limite données : "
        f"{args.limit}"
    )

    # ----------------------------------------------------------
    # SCAN
    # ----------------------------------------------------------

    all_results, stock_provider = (
        scan_all(
            threshold=args.threshold,
            limit=args.limit,
            top=args.top
        )
    )

    # ----------------------------------------------------------
    # MESSAGE
    # ----------------------------------------------------------

    message = format_full_message(
        all_results,
        stock_provider,
        threshold=args.threshold,
        top=args.top,
        now=now
    )

    print(
        "\n" + message
    )

    # ----------------------------------------------------------
    # SIGNAL FORT ?
    # ----------------------------------------------------------

    signal_exists = has_signal(
        all_results
    )

    # ----------------------------------------------------------
    # EMAIL : TOUJOURS
    # ----------------------------------------------------------

    email_subject = (
        f"Scan V4 — "
        f"{count_signals(all_results)} "
        f"signal(s) fort(s) — "
        f"{now.strftime('%Y-%m-%d %H:%M')} UTC"
    )

    send_email(
        os.getenv(
            "SMTP_HOST",
            "smtp.gmail.com"
        ),
        os.getenv(
            "SMTP_PORT",
            "465"
        ),
        os.getenv(
            "EMAIL_SENDER"
        ),
        os.getenv(
            "EMAIL_PASSWORD"
        ),
        os.getenv(
            "EMAIL_RECIPIENT"
        ),
        email_subject,
        message
    )

    # ----------------------------------------------------------
    # SLACK : UNIQUEMENT SIGNAL FORT
    # ----------------------------------------------------------

    if signal_exists:

        slack_url = os.getenv(
            "SLACK_WEBHOOK_URL"
        )

        if slack_url:

            send_slack(
                slack_url,
                message
            )

        else:

            print(
                "[Slack] "
                "SLACK_WEBHOOK_URL absent."
            )

    else:

        print(
            "[Slack] Aucun SIGNAL FORT "
            "— Slack non envoyé."
        )


if __name__ == "__main__":
    main()
