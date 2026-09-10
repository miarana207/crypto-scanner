"""
V4.1 — Scanner multi-actifs + notifications.

SIGNAL FORT :

    Score >= seuil
    + breakout
    + volume confirmé OU volume indisponible
    + R:R >= 1.5

Qualité :

    A = excellent setup
    B = bon setup
    C = surveillance
    D = faible

Sources :

    Crypto       = Binance
    Forex        = Twelve Data
    Actions      = Twelve Data pendant fenêtre active,
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
# TWELVE DATA
# ======================================================================

def twelvedata_window_active(
    now=None,
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
# SYMBOLES
# ======================================================================

def resolve_symbols(
    entries,
    key,
):
    result = []

    for entry in entries:

        if isinstance(
            entry,
            dict,
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
# CATÉGORIES
# ======================================================================

def build_categories(
    now=None,
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

    stock_fallback = (
        klines_yahoo
        if use_twelvedata
        else klines_twelvedata
    )

    stock_fallback_name = (
        "yahoo"
        if use_twelvedata
        else "twelvedata"
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
                "symbols":
                    resolve_symbols(
                        CRYPTO,
                        "binance",
                    ),

                "fetcher":
                    klines_binance,

                "fallback":
                    None,

                "provider":
                    "binance",

                "fallback_provider":
                    None,

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
                "symbols":
                    resolve_symbols(
                        FOREX,
                        "twelvedata",
                    ),

                "fetcher":
                    klines_twelvedata,

                "fallback":
                    None,

                "provider":
                    "twelvedata",

                "fallback_provider":
                    None,

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
                "symbols":
                    resolve_symbols(
                        ACTIONS,
                        stock_provider,
                    ),

                "fetcher":
                    stock_fetcher,

                "fallback":
                    stock_fallback,

                "provider":
                    stock_provider,

                "fallback_provider":
                    stock_fallback_name,

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
                "symbols":
                    resolve_symbols(
                        INDICES,
                        "yahoo",
                    ),

                "fetcher":
                    klines_yahoo,

                "fallback":
                    None,

                "provider":
                    "yahoo",

                "fallback_provider":
                    None,

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
                "symbols":
                    resolve_symbols(
                        COMMODITIES,
                        stock_provider,
                    ),

                "fetcher":
                    stock_fetcher,

                "fallback":
                    stock_fallback,

                "provider":
                    stock_provider,

                "fallback_provider":
                    stock_fallback_name,

                "interval":
                    "15m",

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

                "fallback":
                    None,

                "provider":
                    "yahoo",

                "fallback_provider":
                    None,

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
        stock_provider,
    )


# ======================================================================
# SCAN GLOBAL
# ======================================================================

def scan_all(
    threshold=75,
    limit=1000,
    top=5,
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

                fallback_fetcher=group.get(
                    "fallback"
                ),

                provider_name=group.get(
                    "provider",
                    "unknown",
                ),

                fallback_provider_name=group.get(
                    "fallback_provider"
                ),

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
                ignore_index=True,
            )

            merged = (
                merged
                .sort_values(
                    "best_score",
                    ascending=False,
                )
                .reset_index(drop=True)
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
        stock_provider,
    )


# ======================================================================
# SIGNALS
# ======================================================================

def has_signal(
    all_results,
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
    all_results,
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

def number_text(
    value,
    decimals=4,
):
    if pd.isna(value):
        return "n/d"

    return (
        f"{float(value):.{decimals}f}"
    )


def relvol_text(
    value,
):
    if pd.isna(value):
        return "n/d"

    return f"{float(value):.2f}"


def rsi_text(
    value,
):
    if pd.isna(value):
        return "n/d"

    return f"{float(value):.1f}"


def missing_conditions(
    row,
    threshold=75,
):
    missing = []

    score_value = float(
        row["best_score"]
    )

    if score_value < threshold:

        missing.append(
            f"SCORE < {threshold:.0f}"
        )

    if not bool(
        row.get(
            "breakout_ok",
            False,
        )
    ):

        missing.append(
            "BREAKOUT"
        )

    volume_available = bool(
        row.get(
            "volume_available",
            False,
        )
    )

    volume_ok = bool(
        row.get(
            "volume_ok",
            False,
        )
    )

    if (
        volume_available
        and not volume_ok
    ):

        missing.append(
            "VOLUME"
        )

    rr = row.get(
        "rr_tp2",
        float("nan"),
    )

    if (
        pd.isna(rr)
        or rr < 1.5
    ):

        missing.append(
            "R:R"
        )

    if not missing:

        return "aucune"

    return " + ".join(
        missing
    )


# ======================================================================
# BLOC SIGNAL
# ======================================================================

def format_signal_block(
    row,
):
    direction = row[
        "direction"
    ]

    status = row[
        "status"
    ]

    quality = row.get(
        "quality",
        "D",
    )

    score_value = float(
        row["best_score"]
    )

    entry = number_text(
        row.get(
            "entry",
            float("nan"),
        )
    )

    sl = number_text(
        row.get(
            "stop_loss",
            float("nan"),
        )
    )

    tp1 = number_text(
        row.get(
            "take_profit_1",
            float("nan"),
        )
    )

    tp2 = number_text(
        row.get(
            "take_profit_2",
            float("nan"),
        )
    )

    rr1 = row.get(
        "rr_tp1",
        float("nan"),
    )

    rr2 = row.get(
        "rr_tp2",
        float("nan"),
    )

    rsi = rsi_text(
        row.get(
            "rsi",
            float("nan"),
        )
    )

    atr = number_text(
        row.get(
            "atr",
            float("nan"),
        )
    )

    relvol = relvol_text(
        row.get(
            "relvol",
            float("nan"),
        )
    )

    provider = row.get(
        "provider",
        "n/d",
    )

    volume_available = bool(
        row.get(
            "volume_available",
            False,
        )
    )

    if volume_available:

        volume_text = (
            f"confirmé "
            f"(RelVol {relvol})"
        )

    else:

        volume_text = (
            "n/d — volume "
            "non disponible"
        )

    return (
        f"{row['symbol']} | "
        f"{direction} | "
        f"{status} | "
        f"Qualité {quality} | "
        f"Score {score_value:.0f}/100\n"

        f"   "
        f"Entrée : {entry}\n"

        f"   "
        f"SL : {sl}\n"

        f"   "
        f"TP1 : {tp1} "
        f"(R:R {number_text(rr1, 2)})\n"

        f"   "
        f"TP2 : {tp2} "
        f"(R:R {number_text(rr2, 2)})\n"

        f"   "
        f"ATR : {atr} | "
        f"RSI : {rsi}\n"

        f"   "
        f"Volume : {volume_text}\n"

        f"   "
        f"Source : {provider}"
    )


# ======================================================================
# WATCH
# ======================================================================

def format_watch_block(
    row,
    rank,
    threshold,
):
    missing = missing_conditions(
        row,
        threshold,
    )

    return (
        f"{rank}. "
        f"{row['symbol']} | "
        f"{row['best_score']:.0f}/100 | "
        f"{row['direction']} | "
        f"{row['status']} | "
        f"Q:{row.get('quality', 'D')}\n"

        f"   "
        f"Entry:{number_text(row.get('entry'))} "
        f"| SL:{number_text(row.get('stop_loss'))} "
        f"| TP2:{number_text(row.get('take_profit_2'))}\n"

        f"   "
        f"RSI:{rsi_text(row.get('rsi'))} "
        f"| RelVol:{relvol_text(row.get('relvol'))} "
        f"| ATR:{number_text(row.get('atr'))}\n"

        f"   "
        f"Manque : {missing}"
    )


# ======================================================================
# FRAÎCHEUR
# ======================================================================

def freshness_summary(
    all_results,
    now=None,
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
            or "last_candle"
            not in df.columns
        ):

            continue

        timestamps = pd.to_datetime(
            df["last_candle"],
            utc=True,
            errors="coerce",
        )

        most_recent = (
            timestamps.max()
        )

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
                " ⚠️ potentiellement figée"
            )

        else:

            flag = ""

        lines.append(
            f"{name}: "
            f"{most_recent.strftime('%H:%M')} UTC "
            f"({age_min:.0f} min)"
            f"{flag}"
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
    now=None,
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

        "📊 SCAN MULTI-ACTIFS V4.1",

        f"{ts}",

        f"Seuil : "
        f"{threshold:.0f}/100",

        "",

        "🔥 SIGNAL FORT",

        "Conditions : "
        "SCORE ≥ seuil + BREAKOUT "
        "+ VOLUME si disponible "
        "+ R:R ≥ 1.5",

        f"Total SIGNAL FORT : "
        f"{strong_count}",

        f"Actions/Matières : "
        f"{stock_provider}",

        "Indices : yahoo",

        "",
    ]

    # ==================================================================
    # SIGNALS FORTS
    # ==================================================================

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
                    format_signal_block(
                        row
                    )
                )

                lines.append("")

    # ==================================================================
    # SURVEILLANCE
    # ==================================================================

    lines.extend(
        [
            "👀 À SURVEILLER",
            f"Top {top} par catégorie.",
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

        watch = (
            df.sort_values(
                "best_score",
                ascending=False,
            )
            .head(top)
        )

        for rank, (_, row) in enumerate(
            watch.iterrows(),
            start=1,
        ):

            lines.append(
                format_watch_block(
                    row,
                    rank,
                    threshold,
                )
            )

            lines.append("")

    # ==================================================================
    # FRAÎCHEUR
    # ==================================================================

    lines.extend(
        [
            "",
            freshness_summary(
                all_results,
                now,
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
        "Scanner multi-actifs V4.1"
    )

    parser.add_argument(
        "--threshold",
        type=float,
        default=75,
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
    )

    parser.add_argument(
        "--top",
        type=int,
        default=5,
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

    # ==================================================================
    # SCAN
    # ==================================================================

    (
        all_results,
        stock_provider,
    ) = scan_all(
        threshold=args.threshold,
        limit=args.limit,
        top=args.top,
    )

    # ==================================================================
    # MESSAGE
    # ==================================================================

    message = format_full_message(
        all_results,
        stock_provider,
        threshold=args.threshold,
        top=args.top,
        now=now,
    )

    print(
        "\n" + message
    )

    # ==================================================================
    # SIGNAL
    # ==================================================================

    signal_exists = has_signal(
        all_results
    )

    signal_count = count_signals(
        all_results
    )

    # ==================================================================
    # EMAIL
    # ==================================================================

    email_subject = (
        f"Scan V4.1 — "
        f"{signal_count} "
        f"signal(s) fort(s) — "
        f"{now.strftime('%Y-%m-%d %H:%M')} UTC"
    )

    send_email(
        os.getenv(
            "SMTP_HOST",
            "smtp.gmail.com",
        ),

        os.getenv(
            "SMTP_PORT",
            "465",
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

        message,
    )

    # ==================================================================
    # SLACK
    # ==================================================================

    if signal_exists:

        slack_url = os.getenv(
            "SLACK_WEBHOOK_URL"
        )

        if slack_url:

            send_slack(
                slack_url,
                message,
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


# ======================================================================
# EXECUTION
# ======================================================================

if __name__ == "__main__":
    main()
