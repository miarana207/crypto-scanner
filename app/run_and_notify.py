```python
"""
V4.2 — Scanner multi-actifs + routeur intelligent + notifications.

Architecture :
- Binance pour la crypto ;
- Yahoo Finance / Finnhub / Twelve Data pour les autres actifs ;
- routage dynamique via DataRouter ;
- Twelve Data protégé par son budget journalier ;
- gestion stricte du volume ;
- contrôle de fraîcheur ;
- notifications uniquement lorsqu'au moins un SIGNAL FORT existe ;
- une erreur Slack ou Email n'arrête jamais le scanner.
"""

import argparse
import os
from datetime import datetime, timezone

import pandas as pd

from data_sources import (
    DataRouter,
    VOLUME_CONFIRMED,
    VOLUME_UNAVAILABLE,
    VOLUME_INVALID,
)

from scan import scan

from notify import send_notifications

from assets import (
    CRYPTO,
    ACTIONS,
    FOREX,
    INDICES,
    COMMODITIES,
    COMMODITIES_YAHOO_ONLY,
)


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLD = 75
DEFAULT_LIMIT = 1000
DEFAULT_TOP = 5

# Pause légère entre deux actifs.
#
# Le fournisseur réel étant choisi dynamiquement par DataRouter,
# il ne faut PAS utiliser la pause du "provider préféré" comme
# si ce fournisseur était obligatoirement appelé.
DEFAULT_PAUSE = float(
    os.getenv(
        "SCAN_PAUSE",
        "0.25",
    )
)


# ---------------------------------------------------------------------------
# OUTILS
# ---------------------------------------------------------------------------

def resolve_symbols(entries, key=None):
    """
    Transforme une liste d'entrées assets.py en liste de symboles.

    Compatible avec :
    - liste de chaînes ;
    - liste de dictionnaires multi-fournisseurs.
    """

    result = []

    for entry in entries:

        if isinstance(entry, dict):

            if key:

                value = entry.get(key)

            else:

                value = (
                    entry.get("display")
                    or entry.get("yahoo")
                    or entry.get("twelvedata")
                    or entry.get("finnhub")
                    or entry.get("binance")
                )

            if value:
                result.append(value)

        else:

            if entry:
                result.append(entry)

    return result


def twelvedata_window_active(now=None):
    """
    Indique si nous sommes dans la fenêtre historique principale
    d'utilisation de Twelve Data.

    Madagascar = UTC+3.

    Fenêtre définie :
    03:00 → 19:00 UTC
    soit :
    06:00 → 22:00 heure de Madagascar.

    IMPORTANT :
    cette fonction est informative.
    Le DataRouter V4.2 reste libre de choisir Twelve Data si
    cela est réellement nécessaire.
    """

    now = now or datetime.now(timezone.utc)

    return 3 <= now.hour < 19


# ---------------------------------------------------------------------------
# GROUPES D'ACTIFS
# ---------------------------------------------------------------------------

def asset_groups():
    """
    Univers complet à scanner.

    Les préférences indiquées ici servent uniquement de préférence
    initiale au routeur.

    Le DataRouter peut basculer automatiquement vers une autre
    source selon :
    - disponibilité ;
    - fraîcheur ;
    - volume ;
    - qualité ;
    - quota Twelve Data ;
    - symbole fournisseur.
    """

    return [
        (
            "🪙 Crypto",
            CRYPTO,
            "crypto",
            "5m",
            "binance",
        ),

        (
            "💵 Forex",
            FOREX,
            "forex",
            "15m",
            "finnhub",
        ),

        (
            "📈 Actions",
            ACTIONS,
            "stock",
            "15m",
            "yahoo",
        ),

        (
            "📊 Indices",
            INDICES,
            "index",
            "15m",
            "yahoo",
        ),

        (
            "🛢️ Matières premières",
            COMMODITIES + COMMODITIES_YAHOO_ONLY,
            "commodity",
            "15m",
            "yahoo",
        ),
    ]


# ---------------------------------------------------------------------------
# MAPPING FOURNISSEURS
# ---------------------------------------------------------------------------

def entry_map(entry, canonical):
    """
    Retourne les symboles spécifiques à chaque fournisseur.

    Exemple :
        XAU/USD
        Yahoo     -> GC=F
        Twelve    -> XAU/USD
        Finnhub   -> ...
    """

    if not isinstance(entry, dict):

        return {
            "binance": canonical,
            "yahoo": canonical,
            "finnhub": canonical,
            "twelvedata": canonical,
        }

    return {
        "binance": entry.get(
            "binance",
            canonical,
        ),

        "yahoo": entry.get(
            "yahoo",
            canonical,
        ),

        "finnhub": entry.get(
            "finnhub",
            canonical,
        ),

        "twelvedata": entry.get(
            "twelvedata",
            canonical,
        ),
    }


# ---------------------------------------------------------------------------
# FETCHER DU ROUTEUR
# ---------------------------------------------------------------------------

def router_fetcher(
    router,
    asset_type,
    preferred,
    symbol_maps,
):
    """
    Crée un fetcher compatible avec scan.py.

    Le routeur reste maître du choix final du fournisseur.
    """

    def fetch(
        symbol,
        interval="15m",
        limit=1000,
        **kwargs,
    ):

        return router.fetch(
            symbol=symbol,
            interval=interval,
            limit=limit,
            asset_type=asset_type,
            preferred=preferred,
            require_volume=True,
            symbol_map=symbol_maps.get(
                symbol,
                {},
            ),
        )

    # Permet à scan.py d'identifier explicitement
    # le fetcher comme étant un routeur.
    fetch._router = True

    return fetch


# ---------------------------------------------------------------------------
# SCAN COMPLET
# ---------------------------------------------------------------------------

def scan_all(
    threshold=DEFAULT_THRESHOLD,
    limit=DEFAULT_LIMIT,
    top=DEFAULT_TOP,
):
    """
    Lance le scan complet des 86 actifs.

    Retourne :
        all_results
        router
        coverage
    """

    router = DataRouter()

    all_results = {}

    coverage = {}

    current_time = datetime.now(
        timezone.utc
    )

    # ---------------------------------------------------------------
    # GROUPES
    # ---------------------------------------------------------------

    for (
        name,
        entries,
        asset_type,
        interval,
        preferred,
    ) in asset_groups():

        symbols = []

        symbol_maps = {}

        # -----------------------------------------------------------
        # CONSTRUCTION DE L'UNIVERS
        # -----------------------------------------------------------

        for entry in entries:

            if isinstance(entry, dict):

                canonical = (
                    entry.get("display")
                    or entry.get("twelvedata")
                    or entry.get("yahoo")
                    or entry.get("finnhub")
                    or entry.get("binance")
                )

            else:

                canonical = entry

            if not canonical:
                continue

            symbols.append(
                canonical
            )

            symbol_maps[
                canonical
            ] = entry_map(
                entry,
                canonical,
            )

        print()

        print(
            f"{name} — "
            f"{len(symbols)} actifs"
        )

        print(
            f"   Type     : {asset_type}"
        )

        print(
            f"   Interval : {interval}"
        )

        print(
            f"   Préférence initiale : "
            f"{preferred or 'automatique'}"
        )

        # -----------------------------------------------------------
        # ROUTEUR
        # -----------------------------------------------------------

        fetcher = router_fetcher(
            router=router,
            asset_type=asset_type,
            preferred=preferred,
            symbol_maps=symbol_maps,
        )

        # -----------------------------------------------------------
        # SCAN
        # -----------------------------------------------------------

        try:

            df = scan(
                symbols=symbols,

                fetcher=fetcher,

                interval=interval,

                limit=limit,

                threshold=threshold,

                pause=DEFAULT_PAUSE,

                provider_name="router",

                asset_type=asset_type,

                symbol_map=symbol_maps,
            )

        except Exception as exc:

            # Une erreur sur un groupe ne doit pas empêcher
            # les groupes suivants d'être analysés.

            print(
                f"[{name}] "
                f"Erreur groupe : {exc}"
            )

            df = pd.DataFrame()

        all_results[name] = df

        # -----------------------------------------------------------
        # COUVERTURE
        # -----------------------------------------------------------

        requested = len(symbols)

        analyzed = (
            len(df)
            if df is not None
            else 0
        )

        failed = max(
            requested - analyzed,
            0,
        )

        coverage[name] = {
            "requested": requested,
            "analyzed": analyzed,
            "failed": failed,
        }

    # ---------------------------------------------------------------
    # RÉSULTAT
    # ---------------------------------------------------------------

    print()

    print(
        "Scan multi-actifs terminé."
    )

    print(
        f"Groupes analysés : "
        f"{len(all_results)}"
    )

    print(
        f"Twelve Data : "
        f"{router.td_used}/{router.td_budget}"
    )

    print(
        f"Fenêtre TD active : "
        f"{'oui' if twelvedata_window_active(current_time) else 'non'}"
    )

    return (
        all_results,
        router,
        coverage,
    )


# ---------------------------------------------------------------------------
# SIGNALS
# ---------------------------------------------------------------------------

def has_signal(all_results):
    """
    Retourne True si au moins un SIGNAL FORT existe.
    """

    for df in all_results.values():

        if (
            df is None
            or df.empty
            or "status" not in df.columns
        ):
            continue

        if (
            df["status"]
            .eq("SIGNAL FORT")
            .any()
        ):
            return True

    return False


def count_signals(all_results):
    """
    Compte le nombre total de SIGNAL FORT.
    """

    total = 0

    for df in all_results.values():

        if (
            df is None
            or df.empty
            or "status" not in df.columns
        ):
            continue

        total += int(
            df["status"]
            .eq("SIGNAL FORT")
            .sum()
        )

    return total


# ---------------------------------------------------------------------------
# COUVERTURE
# ---------------------------------------------------------------------------

def coverage_summary(coverage):
    """
    Résumé de couverture du scan.
    """

    lines = [
        "🛡️ COUVERTURE DU SCAN"
    ]

    total_requested = 0
    total_analyzed = 0
    total_failed = 0

    for name, info in coverage.items():

        requested = int(
            info.get(
                "requested",
                0,
            )
        )

        analyzed = int(
            info.get(
                "analyzed",
                0,
            )
        )

        failed = int(
            info.get(
                "failed",
                0,
            )
        )

        total_requested += requested
        total_analyzed += analyzed
        total_failed += failed

        if requested == 0:

            status = "—"

        elif analyzed == requested:

            status = "🟢 OK"

        elif analyzed > 0:

            status = "🟠 PARTIEL"

        else:

            status = "🔴 ÉCHEC"

        lines.append(
            f"{name}: "
            f"{analyzed}/{requested} analysés "
            f"— {status}"
        )

    overall = (
        "🟢 SCAN COMPLET"
        if total_failed == 0
        else "⚠️ SCAN PARTIEL"
    )

    lines.extend(
        [
            "",
            f"{overall} : "
            f"{total_analyzed}/"
            f"{total_requested} "
            f"actifs analysés",
        ]
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# FORMATAGE
# ---------------------------------------------------------------------------

def number_text(
    value,
    decimals=4,
):
    """
    Format numérique sûr.
    """

    if value is None:
        return "n/d"

    try:

        if pd.isna(value):
            return "n/d"

        return (
            f"{float(value):.{decimals}f}"
        )

    except (
        TypeError,
        ValueError,
    ):

        return "n/d"


def relvol_text(value):
    """
    Format relative volume.
    """

    if value is None:
        return "n/d"

    try:

        if pd.isna(value):
            return "n/d"

        return f"{float(value):.2f}"

    except (
        TypeError,
        ValueError,
    ):

        return "n/d"


def rsi_text(value):
    """
    Format RSI.
    """

    if value is None:
        return "n/d"

    try:

        if pd.isna(value):
            return "n/d"

        return f"{float(value):.1f}"

    except (
        TypeError,
        ValueError,
    ):

        return "n/d"


# ---------------------------------------------------------------------------
# BLOC SIGNAL FORT
# ---------------------------------------------------------------------------

def format_signal_block(row):
    """
    Format d'un signal fort.
    """

    volume_status = row.get(
        "volume_status",
        VOLUME_UNAVAILABLE,
    )

    if volume_status == VOLUME_CONFIRMED:

        volume_text = (
            "confirmé "
            f"(RelVol "
            f"{relvol_text(row.get('relvol'))})"
        )

    elif volume_status == VOLUME_INVALID:

        volume_text = (
            "INVALIDE"
        )

    else:

        volume_text = (
            "n/d — volume non disponible"
        )

    freshness = row.get(
        "fresh",
        True,
    )

    freshness_text = (
        "frais"
        if bool(freshness)
        else "ANCIEN"
    )

    return (
        f"{row['symbol']} | "
        f"{row['direction']} | "
        f"{row['status']} | "
        f"Qualité "
        f"{row.get('quality', 'D')} | "
        f"Score "
        f"{number_text(row.get('best_score'), 0)}/100\n"

        f"   Entrée : "
        f"{number_text(row.get('entry'))}\n"

        f"   SL : "
        f"{number_text(row.get('stop_loss'))}\n"

        f"   TP1 : "
        f"{number_text(row.get('take_profit_1'))} "
        f"(R:R "
        f"{number_text(row.get('rr_tp1'), 2)})\n"

        f"   TP2 : "
        f"{number_text(row.get('take_profit_2'))} "
        f"(R:R "
        f"{number_text(row.get('rr_tp2'), 2)})\n"

        f"   ATR : "
        f"{number_text(row.get('atr'))} | "
        f"RSI : "
        f"{rsi_text(row.get('rsi'))}\n"

        f"   Volume : "
        f"{volume_text}\n"

        f"   Source : "
        f"{row.get('provider', 'n/d')} "
        f"({row.get('provider_symbol', 'n/d')})\n"

        f"   Statut volume : "
        f"{volume_status}\n"

        f"   Fraîcheur : "
        f"{freshness_text} "
        f"({number_text(row.get('age_minutes'), 0)} min)"
    )


# ---------------------------------------------------------------------------
# BLOC À SURVEILLER
# ---------------------------------------------------------------------------

def format_watch_block(
    row,
    rank,
    threshold,
):
    """
    Format d'un actif à surveiller.
    """

    missing = []

    best_score = row.get(
        "best_score"
    )

    if (
        pd.notna(best_score)
        and best_score < threshold
    ):

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

    volume_status = row.get(
        "volume_status",
        VOLUME_UNAVAILABLE,
    )

    if (
        volume_status == VOLUME_INVALID
    ):

        missing.append(
            "VOLUME INVALIDE"
        )

    elif (
        bool(
            row.get(
                "volume_available",
                False,
            )
        )
        and not bool(
            row.get(
                "volume_ok",
                False,
            )
        )
    ):

        missing.append(
            "VOLUME"
        )

    rr_tp2 = row.get(
        "rr_tp2"
    )

    if (
        pd.isna(rr_tp2)
        or rr_tp2 < 1.5
    ):

        missing.append(
            "R:R"
        )

    if not bool(
        row.get(
            "fresh",
            True,
        )
    ):

        missing.append(
            "DONNÉES ANCIENNES"
        )

    return (
        f"{rank}. "
        f"{row['symbol']} | "
        f"{number_text(best_score, 0)}/100 | "
        f"{row['direction']} | "
        f"{row['status']} | "
        f"Q:{row.get('quality', 'D')}\n"

        f"   Entry:"
        f"{number_text(row.get('entry'))} | "
        f"SL:"
        f"{number_text(row.get('stop_loss'))} | "
        f"TP2:"
        f"{number_text(row.get('take_profit_2'))}\n"

        f"   RSI:"
        f"{rsi_text(row.get('rsi'))} | "
        f"RelVol:"
        f"{relvol_text(row.get('relvol'))} | "
        f"ATR:"
        f"{number_text(row.get('atr'))}\n"

        f"   Source:"
        f"{row.get('provider', 'n/d')} | "
        f"Manque : "
        f"{' + '.join(missing) if missing else 'aucune'}"
    )


# ---------------------------------------------------------------------------
# FRAÎCHEUR
# ---------------------------------------------------------------------------

def freshness_summary(
    all_results,
    now=None,
):
    """
    Résume la fraîcheur des données par catégorie.
    """

    now = (
        now
        or datetime.now(timezone.utc)
    )

    now_ts = pd.Timestamp(
        now
    )

    lines = [
        "🕐 FRAÎCHEUR DES DONNÉES"
    ]

    for name, df in all_results.items():

        if (
            df is None
            or df.empty
            or "last_candle" not in df.columns
        ):

            lines.append(
                f"{name}: "
                "aucune donnée exploitable"
            )

            continue

        ts = pd.to_datetime(
            df["last_candle"],
            utc=True,
            errors="coerce",
        )

        most_recent = ts.max()

        if pd.isna(
            most_recent
        ):
            continue

        age = (
            now_ts - most_recent
        ).total_seconds() / 60

        if age <= 90:

            flag = "🟢 frais"

        elif (
            now_ts.weekday() >= 5
            or now_ts.hour < 7
            or now_ts.hour >= 21
        ):

            flag = (
                "🟡 marché "
                "probablement fermé"
            )

        else:

            flag = (
                "🔴 données anciennes"
            )

        lines.append(
            f"{name}: "
            f"{most_recent.strftime('%H:%M')} UTC "
            f"({age:.0f} min) "
            f"{flag}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MESSAGE COMPLET
# ---------------------------------------------------------------------------

def format_full_message(
    all_results,
    router,
    threshold=DEFAULT_THRESHOLD,
    top=DEFAULT_TOP,
    now=None,
    coverage=None,
):
    """
    Construit le message complet Slack / Email.
    """

    now = (
        now
        or datetime.now(timezone.utc)
    )

    strong_count = count_signals(
        all_results
    )

    lines = [
        "📊 SCAN MULTI-ACTIFS V4.2",

        now.strftime(
            "%Y-%m-%d %H:%M UTC"
        ),

        f"Seuil : "
        f"{threshold:.0f}/100",

        "",
    ]

    # ---------------------------------------------------------------
    # COUVERTURE
    # ---------------------------------------------------------------

    if coverage:

        lines.extend(
            [
                coverage_summary(
                    coverage
                ),
                "",
            ]
        )

    # ---------------------------------------------------------------
    # SIGNALS
    # ---------------------------------------------------------------

    lines.extend(
        [
            "🔥 SIGNAL FORT",

            (
                "Conditions : "
                "SCORE ≥ seuil + "
                "BREAKOUT + "
                "VOLUME si disponible + "
                "R:R ≥ 1.5 + "
                "données fraîches"
            ),

            f"Total SIGNAL FORT : "
            f"{strong_count}",

            "",
        ]
    )

    # ---------------------------------------------------------------
    # ROUTAGE
    # ---------------------------------------------------------------

    lines.extend(
        [
            "--- ROUTAGE DES SOURCES ---"
        ]
    )

    for provider, stats in router.stats.items():

        lines.append(
            f"{provider}: "
            f"{stats.get('success', 0)} succès / "
            f"{stats.get('fail', 0)} échecs / "
            f"{stats.get('attempts', 0)} appels"
        )

    lines.append(
        "Twelve Data budget : "
        f"{router.td_used}/"
        f"{router.td_budget} appels"
    )

    lines.append(
        "Fenêtre TD : "
        + (
            "active"
            if twelvedata_window_active(now)
            else "hors fenêtre principale"
        )
    )

    lines.append("")

    # ---------------------------------------------------------------
    # SIGNALS FORTS
    # ---------------------------------------------------------------

    if strong_count == 0:

        lines.extend(
            [
                "Aucun signal fort "
                "sur ce scan.",
                "",
            ]
        )

    else:

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
                f"--- {name} ---"
            )

            for _, row in strong.iterrows():

                lines.extend(
                    [
                        format_signal_block(
                            row
                        ),
                        "",
                    ]
                )

    # ---------------------------------------------------------------
    # WATCHLIST
    # ---------------------------------------------------------------

    lines.extend(
        [
            "👀 À SURVEILLER",
            f"Top {top} par catégorie.",
            "",
        ]
    )

    for name, df in all_results.items():

        lines.append(
            f"--- {name} ---"
        )

        if (
            df is None
            or df.empty
        ):

            lines.extend(
                [
                    "Aucune donnée exploitable.",
                    "",
                ]
            )

            continue

        ranked = (
            df
            .sort_values(
                "best_score",
                ascending=False,
                na_position="last",
            )
            .head(top)
        )

        for rank, (_, row) in enumerate(
            ranked.iterrows(),
            1,
        ):

            lines.extend(
                [
                    format_watch_block(
                        row,
                        rank,
                        threshold,
                    ),
                    "",
                ]
            )

    # ---------------------------------------------------------------
    # FRAÎCHEUR
    # ---------------------------------------------------------------

    lines.extend(
        [
            freshness_summary(
                all_results,
                now,
            ),
        ]
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    """
    Point d'entrée du scanner V4.2.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Scanner multi-actifs V4.2"
        )
    )

    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=(
            "Seuil de score "
            "(défaut: 75)"
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=(
            "Nombre maximal de bougies "
            "(défaut: 1000)"
        ),
    )

    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        help=(
            "Nombre d'actifs à surveiller "
            "par catégorie "
            "(défaut: 5)"
        ),
    )

    args = parser.parse_args()

    # ---------------------------------------------------------------
    # VALIDATION
    # ---------------------------------------------------------------

    threshold = max(
        0,
        min(
            float(args.threshold),
            100,
        ),
    )

    limit = max(
        120,
        int(args.limit),
    )

    top = max(
        1,
        int(args.top),
    )

    # ---------------------------------------------------------------
    # DATE / HEURE
    # ---------------------------------------------------------------

    now = datetime.now(
        timezone.utc
    )

    print()

    print(
        "=================================================="
    )

    print(
        "SCAN MULTI-ACTIFS V4.2"
    )

    print(
        "=================================================="
    )

    print(
        f"Heure : "
        f"{now.strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    print(
        f"Seuil : "
        f"{threshold:.0f}/100"
    )

    print(
        f"Limite : "
        f"{limit} bougies"
    )

    print(
        f"Top : "
        f"{top}"
    )

    print(
        "=================================================="
    )

    # ---------------------------------------------------------------
    # SCAN
    # ---------------------------------------------------------------

    all_results, router, coverage = scan_all(
        threshold=threshold,
        limit=limit,
        top=top,
    )

    # ---------------------------------------------------------------
    # MESSAGE
    # ---------------------------------------------------------------

    message = format_full_message(
        all_results=all_results,
        router=router,
        threshold=threshold,
        top=top,
        now=now,
        coverage=coverage,
    )

    print()

    print(message)

    # ---------------------------------------------------------------
    # SIGNAL
    # ---------------------------------------------------------------

    signal_count = count_signals(
        all_results
    )

    signal_detected = (
        signal_count > 0
    )

    print()

    print(
        "=================================================="
    )

    print(
        f"SIGNAL FORT : "
        f"{signal_count}"
    )

    print(
        "=================================================="
    )

    # ---------------------------------------------------------------
    # NOTIFICATIONS
    # ---------------------------------------------------------------
    #
    # RÈGLE V4.2 :
    #
    # Aucun SIGNAL FORT
    #       ↓
    # aucune notification
    #
    # Au moins 1 SIGNAL FORT
    #       ↓
    # Slack + Email
    #
    # Et si Slack ou Email échoue :
    #       ↓
    # le scanner est terminé normalement.
    # ---------------------------------------------------------------

    if not signal_detected:

        print(
            "[Notifications] "
            "Aucun SIGNAL FORT "
            "— aucune notification envoyée."
        )

        return

    subject = (
        "Scan V4.2 — "
        f"{signal_count} signal(s) fort(s) — "
        f"{now.strftime('%Y-%m-%d %H:%M')} UTC"
    )

    notification_result = send_notifications(
        message=message,

        slack_webhook_url=os.getenv(
            "SLACK_WEBHOOK_URL"
        ),

        smtp_host=os.getenv(
            "SMTP_HOST",
            "smtp.gmail.com",
        ),

        smtp_port=os.getenv(
            "SMTP_PORT",
            "465",
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
    )

    print()

    print(
        "[Notifications] "
        f"Slack="
        f"{'OK' if notification_result.get('slack') else 'ECHEC/IGNORÉ'} | "
        f"Email="
        f"{'OK' if notification_result.get('email') else 'ECHEC/IGNORÉ'}"
    )


# ---------------------------------------------------------------------------
# EXECUTION
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
```
