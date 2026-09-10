"""
V4.2 — Scanner multi-actifs + notifications.

Architecture :
    Crypto       : Binance → Finnhub → Yahoo → Twelve Data
    Actions      : Finnhub → Yahoo → Twelve Data
    Forex        : Finnhub → Yahoo → Twelve Data
    Indices      : Yahoo → Finnhub → Twelve Data
    Commodities  : Yahoo → Finnhub → Twelve Data

Le mapping fournisseur est fourni par assets.py.
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
    get_symbol_map,
)


PAUSE_BY_ASSET_TYPE = {
    "crypto": 0.20,
    "forex": 0.20,
    "stock": 0.20,
    "index": 0.20,
    "commodity": 0.20,
}


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

    mapping = symbol_maps.get(
        symbol,
        get_symbol_map(symbol),
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


def asset_groups():

    groups = [
        {
            "name": "Crypto",
            "asset_type": "crypto",
            "interval": "5m",
            "entries": CRYPTO,
        },
        {
            "name": "Forex",
            "asset_type": "forex",
            "interval": "15m",
            "entries": FOREX,
        },
        {
            "name": "Actions",
            "asset_type": "stock",
            "interval": "15m",
            "entries": ACTIONS,
        },
        {
            "name": "Indices",
            "asset_type": "index",
            "interval": "15m",
            "entries": INDICES,
        },
    ]

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


def scan_all(
    threshold=75,
    limit=1000,
    top=5,
):

    router = DataRouter()

    all_results = {}
    coverage = []

    for group in asset_groups():

        name = group["name"]
        asset_type = group["asset_type"]
        interval = group["interval"]
        entries = group["entries"]

        canonical_symbols = []
        symbol_maps = {}

        for entry in entries:

            if isinstance(entry, dict):

                canonical = (
                    entry.get("symbol")
                    or entry.get("display")
                    or entry.get("yahoo")
                    or entry.get("twelvedata")
                )

                if not canonical:
                    continue

                canonical_symbols.append(
                    canonical
                )

                symbol_maps[canonical] = entry

            else:

                canonical = str(entry)

                canonical_symbols.append(
                    canonical
                )

                # IMPORTANT :
                # utiliser le mapping officiel de assets.py
                symbol_maps[canonical] = (
                    get_symbol_map(canonical)
                )

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
            f"{name} — {requested} actifs"
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

                def fetcher(
                    s,
                    interval_value=interval,
                    limit_value=limit,
                ):
                    return router_fetcher(
                        router,
                        s,
                        interval_value,
                        limit_value,
                        asset_type,
                        symbol_maps,
                    )

                result = scan(
                    [symbol],
                    fetcher,
                    interval=interval,
                    limit=limit,
                    threshold=threshold,
                    pause=0.0,
                    provider_name="router",
                    asset_type=asset_type,
                )

                if (
                    result is not None
                    and not result.empty
                ):

                    all_results[symbol] = result
                    analyzed += 1

                else:
                    insufficient += 1

            except DataSourceError as exc:

                errors += 1

                print(
                    f"[{symbol}] erreur source : "
                    f"{exc}"
                )

            except Exception as exc:

                errors += 1

                print(
                    f"[{symbol}] erreur scan : "
                    f"{exc}"
                )

            pause = PAUSE_BY_ASSET_TYPE.get(
                asset_type,
                0.20,
            )

            if pause > 0:
                time.sleep(pause)

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
            f"{analyzed}/{requested} analysés "
            f"| erreurs={errors} "
            f"| insuffisants={insufficient}"
        )

    router._coverage = coverage

    return all_results, router


def count_signals(all_results):

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


def has_signal(all_results):
    return count_signals(
        all_results
    ) > 0


def coverage_summary(router):

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

        requested = item["requested"]
        analyzed = item["analyzed"]
        errors = item["errors"]
        insufficient = item["insufficient"]

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

    lines.append("")
    lines.append(
        f"TOTAL : "
        f"{total_analyzed}/{total_requested} analysés "
        f"| erreurs={total_errors} "
        f"| insuffisants={total_insufficient}"
    )

    return "\n".join(lines)


def router_summary(router):

    stats = router.stats()

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

    for provider in [
        "binance",
        "finnhub",
        "yahoo",
        "twelve_data",
    ]:

        lines.append(
            f"{provider:<13} "
            f"appels={provider_calls.get(provider, 0)} "
            f"| succès={provider_successes.get(provider, 0)} "
            f"| échecs={provider_failures.get(provider, 0)}"
        )

    lines.extend(
        [
            "",
            "📦 VOLUME",
            f"confirmé : {volume_status.get(VOLUME_CONFIRMED, 0)}",
            f"partiel : {volume_status.get(VOLUME_PARTIAL, 0)}",
            f"indisponible : {volume_status.get(VOLUME_UNAVAILABLE, 0)}",
            f"invalide : {volume_status.get(VOLUME_INVALID, 0)}",
            "",
            f"Requêtes routeur : {stats.get('calls', 0)}",
            f"Succès : {stats.get('successes', 0)}",
            f"Échecs : {stats.get('failures', 0)}",
            f"Cache hits : {stats.get('cache_hits', 0)}",
        ]
    )

    budget = stats.get(
        "twelve_data",
        {},
    )

    lines.extend(
        [
            "",
            "💳 TWELVE DATA",
            f"utilisé : "
            f"{budget.get('used', 0)}/"
            f"{budget.get('daily_limit', 0)}",
            f"restant : "
            f"{budget.get('remaining', 0)}",
        ]
    )

    cooldowns = stats.get(
        "provider_cooldowns",
        {},
    )

    if cooldowns:

        lines.append("")
        lines.append(
            "⏸️ CIRCUIT BREAKERS"
        )

        for provider, seconds in cooldowns.items():

            if seconds > 0:
                lines.append(
                    f"{provider} : "
                    f"{seconds}s restantes"
                )

    return "\n".join(lines)


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

        if df is None or df.empty:
            continue

        if "last_candle" not in df.columns:
            continue

        most_recent = df[
            "last_candle"
        ].max()

        if pd.isna(most_recent):
            continue

        most_recent = pd.Timestamp(
            most_recent
        )

        if most_recent.tzinfo is None:
            most_recent = (
                most_recent.tz_localize("UTC")
            )
        else:
            most_recent = (
                most_recent.tz_convert("UTC")
            )

        age_min = (
            now
            - most_recent.to_pydatetime()
        ).total_seconds() / 60

        if age_min < 0:
            flag = " ❌ TIMESTAMP FUTUR"
        elif age_min > 90:
            flag = " ⚠️ possible donnée figée"
        else:
            flag = ""

        lines.append(
            f"{name}: "
            f"{most_recent.strftime('%Y-%m-%d %H:%M UTC')} "
            f"(il y a {age_min:.0f} min)"
            f"{flag}"
        )

    return "\n".join(lines)


def strong_signals_summary(all_results):

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

            score_value = float(
                row.get(
                    "score",
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

            lines.append(
                f"🔥 {symbol} "
                f"{direction} — "
                f"{score_value:.0f}/100"
            )

            lines.append(
                "➡️ ENTRÉE IMMÉDIATE"
            )

            entry = row.get(
                "entry",
                float("nan"),
            )

            stop = row.get(
                "stop_loss",
                float("nan"),
            )

            tp1 = row.get(
                "take_profit_1",
                float("nan"),
            )

            if pd.notna(entry):
                lines.append(
                    f"Entrée : {entry:.6g}"
                )

            if pd.notna(stop):
                lines.append(
                    f"Stop : {stop:.6g}"
                )

            if pd.notna(tp1):
                lines.append(
                    f"TP1 : {tp1:.6g}"
                )

            breakout = row.get(
                "breakout_pts",
                float("nan"),
            )

            volume = row.get(
                "volume_pts",
                float("nan"),
            )

            if pd.notna(breakout):
                lines.append(
                    f"Breakout : {breakout:+.0f}/25"
                )

            if pd.notna(volume):
                lines.append(
                    f"Volume : {volume:+.0f}/15"
                )

    lines.extend(
        [
            "",
            f"Total SIGNAL FORT : {total}",
        ]
    )

    return "\n".join(lines)


def watchlist_summary(
    all_results,
    top=5,
):

    candidates = []

    for name, df in all_results.items():

        if (
            df is None
            or df.empty
            or "status" not in df.columns
        ):
            continue

        for _, row in df.iterrows():

            if row.get(
                "status",
                "",
            ) == "SIGNAL FORT":
                continue

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
                    "score": float(
                        row.get(
                            "score",
                            0,
                        )
                    ),
                    "status": row.get(
                        "status",
                        "",
                    ),
                }
            )

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    selected = candidates[
        :max(int(top), 0)
    ]

    lines = [
        "👀 À SURVEILLER"
    ]

    if not selected:
        lines.append(
            "Aucun actif à surveiller."
        )
        return "\n".join(lines)

    for item in selected:
        lines.append(
            f"{item['symbol']} "
            f"{item['direction']} "
            f"— {item['score']:.0f}/100 "
            f"— {item['status']}"
        )

    return "\n".join(lines)


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

    lines = [
        "📊 SCAN MULTI-ACTIFS V4.2",
        now.strftime(
            "%Y-%m-%d %H:%M UTC"
        ),
        "",
        f"Seuil stratégique : "
        f"{threshold:.0f}/100",
        f"Actifs analysés : "
        f"{len(all_results)}",
        "",
        coverage_summary(router),
        "",
        router_summary(router),
        "",
        strong_signals_summary(
            all_results
        ),
        "",
        watchlist_summary(
            all_results,
            top=top,
        ),
        "",
        freshness_summary(
            all_results,
            now=now,
        ),
    ]

    return "\n".join(lines)


def main():

    parser = argparse.ArgumentParser(
        description="Scanner multi-actifs V4.2"
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
        f"Limite données : {args.limit}"
    )

    all_results, router = scan_all(
        threshold=args.threshold,
        limit=args.limit,
        top=args.top,
    )

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

    signal_count = count_signals(
        all_results
    )

    email_subject = (
        f"Scan V4.2 — "
        f"{signal_count} signal(s) fort(s) — "
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
        os.getenv("EMAIL_SENDER"),
        os.getenv("EMAIL_PASSWORD"),
        os.getenv("EMAIL_RECIPIENT"),
        email_subject,
        message,
    )

    if signal_count > 0:

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


if __name__ == "__main__":
    main()
