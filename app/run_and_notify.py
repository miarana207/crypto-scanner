"""
V4.2 — Scanner multi-actifs + notifications.

Architecture :
    - Crypto       : Binance prioritaire
    - Actions      : Finnhub → Yahoo → Twelve Data
    - Forex        : Finnhub → Yahoo → Twelve Data
    - Indices      : Yahoo → Finnhub → Twelve Data
    - Commodities  : Yahoo → Finnhub → Twelve Data
    - Twelve Data  : fallback intelligent + quota journalier

Notifications :
    - Email : chaque scan
    - Slack : uniquement si SIGNAL FORT

Score maximum = 100
"""

import argparse
import os
import time
from datetime import datetime, timezone

import pandas as pd

from data_sources import (
    DataRouter,
    DataSourceError,
    VOLUME_CONFIRMED,
    VOLUME_PARTIAL,
    VOLUME_UNAVAILABLE,
    VOLUME_INVALID,
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
# CONFIGURATION
# ======================================================================

PAUSE_BY_ASSET_TYPE = {
    "crypto": 0.20,
    "forex": 0.20,
    "stock": 0.20,
    "index": 0.20,
    "commodity": 0.20,
}


# ======================================================================
# SYMBOLS
# ======================================================================

def resolve_symbols(
    entries,
    key=None,
):
    """
    Transforme une liste de chaînes/dictionnaires
    en liste de symboles.
    """

    result = []

    for entry in entries:

        if isinstance(
            entry,
            dict,
        ):

            if key:

                value = entry.get(
                    key
                )

            else:

                value = (
                    entry.get("symbol")
                    or entry.get("yahoo")
                    or entry.get("twelvedata")
                    or entry.get("finnhub")
                )

            if value:
                result.append(value)

        else:

            result.append(entry)

    return result


def entry_map(
    entry,
    canonical,
):
    """
    Transforme une entrée assets.py en mapping
    utilisable par DataRouter.
    """

    if not isinstance(
        entry,
        dict,
    ):
        return {
            canonical: {
                "binance": entry,
                "yahoo": entry,
                "finnhub": entry,
                "twelvedata": entry,
                "twelve_data": entry,
            }
        }

    mapping = {}

    for key in [
        "binance",
        "yahoo",
        "finnhub",
        "twelvedata",
        "twelve_data",
    ]:

        value = entry.get(key)

        if value:
            mapping[key] = value

    return {
        canonical: mapping
    }


# ======================================================================
# GROUPES
# ======================================================================

def asset_groups():

    groups = []

    groups.append(
        {
            "name": "Crypto",
            "asset_type": "crypto",
            "interval": "5m",
            "entries": CRYPTO,
        }
    )

    groups.append(
        {
            "name": "Forex",
            "asset_type": "forex",
            "interval": "15m",
            "entries": FOREX,
        }
    )

    groups.append(
        {
            "name": "Actions",
            "asset_type": "stock",
            "interval": "15m",
            "entries": ACTIONS,
        }
    )

    groups.append(
        {
            "name": "Indices",
            "asset_type": "index",
            "interval": "15m",
            "entries": INDICES,
        }
    )

    if COMMODITIES:

        groups.append(
            {
                "name": "Matières premières",
                "asset_type": "commodity",
                "interval": "15m",
                "entries": COMMODITIES,
            }
        )

    if COMMODITIES_YAHOO_ONLY:

        groups.append(
            {
                "name": "Matières premières Yahoo",
                "asset_type": "commodity",
                "interval": "15m",
                "entries": COMMODITIES_YAHOO_ONLY,
            }
        )

    return groups


# ======================================================================
# FETCH ROUTER
# ======================================================================

def router_fetcher(
    router,
    symbol,
    interval,
    limit,
    asset_type,
    symbol_maps,
):

    require_volume = (
        asset_type
        not in {
            "forex",
            "index",
        }
    )

    mapping = (
        symbol_maps.get(
            symbol,
            {},
        )
    )

    return router.fetch(
        symbol=symbol,
        interval=interval,
        limit=limit,
        asset_type=asset_type,
        preferred=None,
        require_volume=require_volume,
        symbol_map=mapping,
    )


# ======================================================================
# SCAN GLOBAL
# ======================================================================

def scan_all(
    threshold=75,
    limit=1000,
    top=5,
):

    router = DataRouter()

    all_results = {}

    coverage = []

    groups = asset_groups()

    for group in groups:

        name = group["name"]

        asset_type = group[
            "asset_type"
        ]

        interval = group[
            "interval"
        ]

        entries = group[
            "entries"
        ]

        canonical_symbols = []

        symbol_maps = {}

        for entry in entries:

            if isinstance(
                entry,
                dict,
            ):

                canonical = (
                    entry.get(
                        "symbol"
                    )
                    or entry.get(
                        "display"
                    )
                    or entry.get(
                        "yahoo"
                    )
                    or entry.get(
                        "twelvedata"
                    )
                )

                if not canonical:
                    continue

                canonical_symbols.append(
                    canonical
                )

                symbol_maps[
                    canonical
                ] = entry

            else:

                canonical = str(
                    entry
                )

                canonical_symbols.append(
                    canonical
                )

                symbol_maps[
                    canonical
                ] = {
                    "symbol": canonical,
                    "binance": canonical,
                    "yahoo": canonical,
                    "finnhub": canonical,
                    "twelvedata": canonical,
                    "twelve_data": canonical,
                }

        requested = len(
            canonical_symbols
        )

        analyzed = 0
        errors = 0
        insufficient = 0

        print(
            f"\n{'=' * 70}"
        )

        print(
            f"{name} — "
            f"{requested} actifs"
        )

        print(
            f"Intervalle : {interval}"
        )

        print(
            f"Type : {asset_type}"
        )

        print(
            f"{'=' * 70}"
        )

        for symbol in canonical_symbols:

            try:

                fetcher = lambda s, interval=interval, limit=limit: (
                    router_fetcher(
                        router,
                        s,
                        interval,
                        limit,
                        asset_type,
                        symbol_maps,
                    )
                )

                result = scan(
                    [symbol],
                    fetcher,
                    interval=interval,
                    limit=limit,
                    threshold=threshold,
                    pause=0.0,
                    provider_name="router",
                )

                if result is not None and not result.empty:

                    all_results[
                        symbol
                    ] = result

                    analyzed += 1

                else:

                    insufficient += 1

            except DataSourceError as exc:

                errors += 1

                print(
                    f"[{symbol}] "
                    f"erreur source router: "
                    f"{exc}"
                )

            except Exception as exc:

                errors += 1

                print(
                    f"[{symbol}] "
                    f"erreur scan: "
                    f"{exc}"
                )

            pause = PAUSE_BY_ASSET_TYPE.get(
                asset_type,
                0.20,
            )

            if pause > 0:

                time.sleep(
                    pause
                )

        coverage.append(
            {
                "name": name,
                "requested": requested,
                "analyzed": analyzed,
                "errors": errors,
                "insufficient": insufficient,
            }
        )

        print(
            f"{name}: "
            f"{analyzed}/{requested} analysés | "
            f"erreurs={errors} | "
            f"insuffisants={insufficient}"
        )

    router._coverage = coverage

    return (
        all_results,
        router,
    )


# ======================================================================
# SIGNALS
# ======================================================================

def count_signals(
    all_results,
):

    total = 0

    for df in all_results.values():

        if df is None or df.empty:
            continue

        if "status" not in df.columns:
            continue

        total += int(
            (
                df["status"]
                == "SIGNAL FORT"
            ).sum()
        )

    return total


def has_signal(
    all_results,
):

    return (
        count_signals(
            all_results
        )
        > 0
    )


# ======================================================================
# COUVERTURE
# ======================================================================

def coverage_summary(
    router,
):

    coverage = getattr(
        router,
        "_coverage",
        [],
    )

    if not coverage:
        return "Couverture : aucune donnée."

    lines = [
        "📡 COUVERTURE DU SCAN"
    ]

    total_requested = 0
    total_analyzed = 0
    total_errors = 0
    total_insufficient = 0

    for item in coverage:

        requested = item[
            "requested"
        ]

        analyzed = item[
            "analyzed"
        ]

        errors = item[
            "errors"
        ]

        insufficient = item[
            "insufficient"
        ]

        total_requested += requested
        total_analyzed += analyzed
        total_errors += errors
        total_insufficient += insufficient

        lines.append(
            f"{item['name']} : "
            f"{analyzed}/{requested} "
            f"| erreurs={errors} "
            f"| insuffisants={insufficient}"
        )

    lines.append(
        ""
    )

    lines.append(
        f"TOTAL : "
        f"{total_analyzed}/{total_requested} analysés "
        f"| erreurs={total_errors} "
        f"| insuffisants={total_insufficient}"
    )

    return "\n".join(
        lines
    )


# ======================================================================
# ROUTER SUMMARY
# ======================================================================

def router_summary(
    router,
):

    # --------------------------------------------------------------
    # IMPORTANT :
    # router.stats est une MÉTHODE.
    # Il faut donc appeler router.stats().
    # --------------------------------------------------------------

    stats_method = getattr(
        router,
        "stats",
        None,
    )

    if callable(
        stats_method
    ):

        stats = stats_method()

    else:

        stats = {}

    provider_calls = stats.get(
        "provider_calls",
        {},
    )

    provider_successes = stats.get(
        "provider_successes",
        {},
    )

    provider_failures = stats.get(
        "provider_failures",
        {},
    )

    volume_status = stats.get(
        "volume_status",
        {},
    )

    lines = [
        "🧠 ROUTEUR V4.2"
    ]

    providers = [
        "binance",
        "finnhub",
        "yahoo",
        "twelve_data",
    ]

    for provider in providers:

        calls = int(
            provider_calls.get(
                provider,
                0,
            )
        )

        successes = int(
            provider_successes.get(
                provider,
                0,
            )
        )

        failures = int(
            provider_failures.get(
                provider,
                0,
            )
        )

        lines.append(
            f"{provider:<13} "
            f"appels={calls} "
            f"| succès={successes} "
            f"| échecs={failures}"
        )

    lines.append(
        ""
    )

    lines.append(
        "📦 VOLUME"
    )

    lines.append(
        f"confirmé : "
        f"{volume_status.get(VOLUME_CONFIRMED, 0)}"
    )

    lines.append(
        f"partiel : "
        f"{volume_status.get(VOLUME_PARTIAL, 0)}"
    )

    lines.append(
        f"indisponible : "
        f"{volume_status.get(VOLUME_UNAVAILABLE, 0)}"
    )

    lines.append(
        f"invalide : "
        f"{volume_status.get(VOLUME_INVALID, 0)}"
    )

    lines.append(
        ""
    )

    lines.append(
        f"Requêtes routeur : "
        f"{stats.get('calls', 0)}"
    )

    lines.append(
        f"Succès : "
        f"{stats.get('successes', 0)}"
    )

    lines.append(
        f"Échecs : "
        f"{stats.get('failures', 0)}"
    )

    lines.append(
        f"Cache hits : "
        f"{stats.get('cache_hits', 0)}"
    )

    # --------------------------------------------------------------
    # TWELVE DATA
    # --------------------------------------------------------------

    budget = stats.get(
        "twelve_data",
        None,
    )

    if not isinstance(
        budget,
        dict,
    ):

        getter = getattr(
            router,
            "get_twelve_data_budget",
            None,
        )

        if callable(getter):

            budget = getter()

        else:

            budget = {
                "daily_limit": 0,
                "used": 0,
                "remaining": 0,
            }

    td_limit = int(
        budget.get(
            "daily_limit",
            0,
        )
    )

    td_used = int(
        budget.get(
            "used",
            0,
        )
    )

    td_remaining = int(
        budget.get(
            "remaining",
            max(
                0,
                td_limit - td_used,
            ),
        )
    )

    lines.append(
        ""
    )

    lines.append(
        "💳 TWELVE DATA"
    )

    lines.append(
        f"utilisé : "
        f"{td_used}/{td_limit}"
    )

    lines.append(
        f"restant : "
        f"{td_remaining}"
    )

    return "\n".join(
        lines
    )


# ======================================================================
# FRESHNESS
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
        "🕒 FRAÎCHEUR DES DONNÉES"
    ]

    for name, df in all_results.items():

        if (
            df is None
            or df.empty
        ):
            continue

        if (
            "last_candle"
            not in df.columns
        ):
            continue

        most_recent = df[
            "last_candle"
        ].max()

        if pd.isna(
            most_recent
        ):
            continue

        if isinstance(
            most_recent,
            pd.Timestamp,
        ):

            if most_recent.tzinfo is None:

                most_recent = (
                    most_recent.tz_localize(
                        "UTC"
                    )
                )

        else:

            most_recent = pd.Timestamp(
                most_recent,
                tz="UTC",
            )

        age_min = (
            now
            - most_recent.to_pydatetime()
        ).total_seconds() / 60

        flag = (
            " ⚠️ possible donnée figée"
            if age_min > 90
            else ""
        )

        lines.append(
            f"{name}: "
            f"{most_recent.strftime('%Y-%m-%d %H:%M UTC')} "
            f"(il y a {age_min:.0f} min)"
            f"{flag}"
        )

    return "\n".join(
        lines
    )


# ======================================================================
# SIGNALS FORTS
# ======================================================================

def strong_signals_summary(
    all_results,
):

    lines = [
        "🔥 SIGNALS FORTS"
    ]

    total = 0

    for name, df in all_results.items():

        if (
            df is None
            or df.empty
            or "status" not in df.columns
        ):
            continue

        strong = df[
            df["status"]
            == "SIGNAL FORT"
        ]

        if strong.empty:
            continue

        lines.append(
            f"\n--- {name} ---"
        )

        for _, row in strong.iterrows():

            total += 1

            symbol = row.get(
                "symbol",
                name,
            )

            direction = row.get(
                "direction",
                "N/A",
            )

            score_long = float(
                row.get(
                    "score_long",
                    0,
                )
            )

            score_short = float(
                row.get(
                    "score_short",
                    0,
                )
            )

            best_score = max(
                score_long,
                score_short,
            )

            breakout = row.get(
                "breakout_pts",
                float("nan"),
            )

            volume = row.get(
                "volume_pts",
                float("nan"),
            )

            entry = row.get(
                "entry",
                float("nan"),
            )

            stop_loss = row.get(
                "stop_loss",
                float("nan"),
            )

            take_profit = row.get(
                "take_profit_1",
                float("nan"),
            )

            lines.append(
                f"🔥 {symbol} "
                f"{direction} — "
                f"{best_score:.0f}/100"
            )

            lines.append(
                "➡️ ENTRÉE IMMÉDIATE"
            )

            if pd.notna(
                entry
            ):

                lines.append(
                    f"Entrée : {entry:.6g}"
                )

            if pd.notna(
                stop_loss
            ):

                lines.append(
                    f"Stop : {stop_loss:.6g}"
                )

            if pd.notna(
                take_profit
            ):

                lines.append(
                    f"TP1 : {take_profit:.6g}"
                )

            if pd.notna(
                breakout
            ):

                lines.append(
                    f"Breakout : "
                    f"{breakout:+.0f}/20"
                )

            if pd.notna(
                volume
            ):

                lines.append(
                    f"Volume : "
                    f"{volume:+.0f}/15"
                )

    lines.append(
        ""
    )

    lines.append(
        f"Total SIGNAL FORT : {total}"
    )

    return "\n".join(
        lines
    )


# ======================================================================
# WATCHLIST
# ======================================================================

def watchlist_summary(
    all_results,
    top=5,
):

    candidates = []

    for name, df in all_results.items():

        if (
            df is None
            or df.empty
        ):
            continue

        if "status" not in df.columns:
            continue

        for _, row in df.iterrows():

            status = row.get(
                "status",
                "",
            )

            if status == "SIGNAL FORT":
                continue

            score_long = float(
                row.get(
                    "score_long",
                    0,
                )
            )

            score_short = float(
                row.get(
                    "score_short",
                    0,
                )
            )

            best_score = max(
                score_long,
                score_short,
            )

            candidates.append(
                {
                    "category": name,
                    "symbol": row.get(
                        "symbol",
                        name,
                    ),
                    "direction": row.get(
                        "direction",
                        "N/A",
                    ),
                    "score": best_score,
                    "status": status,
                }
            )

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    selected = candidates[
        :max(
            int(top),
            0,
        )
    ]

    lines = [
        "👀 À SURVEILLER"
    ]

    if not selected:

        lines.append(
            "Aucun actif à surveiller."
        )

        return "\n".join(
            lines
        )

    for item in selected:

        lines.append(
            f"{item['symbol']} "
            f"{item['direction']} "
            f"— {item['score']:.0f}/100 "
            f"— {item['status']}"
        )

    return "\n".join(
        lines
    )


# ======================================================================
# MESSAGE COMPLET
# ======================================================================

def format_full_message(
    all_results,
    router,
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

    lines = []

    lines.append(
        "📊 SCAN MULTI-ACTIFS V4.2"
    )

    lines.append(
        now.strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    )

    lines.append(
        ""
    )

    lines.append(
        f"Seuil stratégique : "
        f"{threshold:.0f}/100"
    )

    lines.append(
        f"Actifs analysés : "
        f"{len(all_results)}"
    )

    lines.append(
        ""
    )

    lines.append(
        coverage_summary(
            router
        )
    )

    lines.append(
        ""
    )

    lines.append(
        router_summary(
            router
        )
    )

    lines.append(
        ""
    )

    lines.append(
        strong_signals_summary(
            all_results
        )
    )

    lines.append(
        ""
    )

    lines.append(
        watchlist_summary(
            all_results,
            top=top,
        )
    )

    lines.append(
        ""
    )

    lines.append(
        freshness_summary(
            all_results,
            now=now,
        )
    )

    return "\n".join(
        lines
    )


# ======================================================================
# MAIN
# ======================================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Scanner multi-actifs V4.2"
        )
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

    # --------------------------------------------------------------
    # SCAN
    # --------------------------------------------------------------

    all_results, router = scan_all(
        threshold=args.threshold,
        limit=args.limit,
        top=args.top,
    )

    # --------------------------------------------------------------
    # MESSAGE
    # --------------------------------------------------------------

    message = format_full_message(
        all_results,
        router,
        threshold=args.threshold,
        top=args.top,
        now=now,
    )

    print(
        "\n" + message
    )

    # --------------------------------------------------------------
    # SIGNAL FORT ?
    # --------------------------------------------------------------

    signal_exists = has_signal(
        all_results
    )

    signal_count = count_signals(
        all_results
    )

    # --------------------------------------------------------------
    # EMAIL : TOUJOURS
    # --------------------------------------------------------------

    email_subject = (
        f"Scan V4.2 — "
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
            "EMAIL_SENDER",
        ),
        os.getenv(
            "EMAIL_PASSWORD",
        ),
        os.getenv(
            "EMAIL_RECIPIENT",
        ),
        email_subject,
        message,
    )

    # --------------------------------------------------------------
    # SLACK : UNIQUEMENT SIGNAL FORT
    # --------------------------------------------------------------

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
