"""
V4.2 — Scanner multi-actifs + routeur intelligent + notifications.

Architecture :
- Binance : crypto 24/7
- Yahoo Finance : source gratuite large
- Finnhub : source gratuite complémentaire
- Twelve Data : source de secours / complément, avec budget journalier
- Le DataRouter choisit automatiquement la meilleure source disponible.
- Le scanner couvre l'ensemble de l'univers défini dans assets.py.
- Les actifs sans volume structurellement disponible (Forex / indices)
  ne sont pas bloqués par l'absence de volume.
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
from notify import send_slack, send_email
from assets import (
    CRYPTO,
    ACTIONS,
    FOREX,
    INDICES,
    COMMODITIES,
    COMMODITIES_YAHOO_ONLY,
)


# Pause de sécurité minimale entre les analyses.
# Le routeur gère lui-même le choix du fournisseur.
# Ces valeurs servent uniquement à éviter une cadence excessive.
PAUSE_BY_ASSET_TYPE = {
    "crypto": 0.20,
    "forex": 0.20,
    "stock": 0.20,
    "index": 0.20,
    "commodity": 0.20,
}


# ---------------------------------------------------------------------------
# OUTILS
# ---------------------------------------------------------------------------

def resolve_symbols(entries, key=None):
    """
    Transforme une liste d'entrées assets.py en liste de symboles.

    Compatible avec :
    - liste de chaînes
    - liste de dictionnaires contenant plusieurs symboles fournisseurs
    """
    result = []

    for entry in entries:
        if isinstance(entry, dict):
            value = None

            if key:
                value = entry.get(key)

            if not value:
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
            result.append(entry)

    return result


def entry_map(entry, canonical):
    """
    Retourne la correspondance des symboles entre fournisseurs.

    Le symbole canonique sert de secours si une correspondance spécifique
    n'est pas présente.
    """
    if not isinstance(entry, dict):
        return {
            "binance": canonical,
            "yahoo": canonical,
            "finnhub": canonical,
            "twelvedata": canonical,
        }

    return {
        "binance": entry.get("binance", canonical),
        "yahoo": entry.get("yahoo", canonical),
        "finnhub": entry.get("finnhub", canonical),
        "twelvedata": entry.get("twelvedata", canonical),
    }


def asset_groups():
    """
    Univers complet du scanner.

    IMPORTANT :
    Aucun fournisseur n'est imposé ici.
    Le DataRouter V4.2 choisit dynamiquement la meilleure source.
    """

    return [
        (
            "🪙 Crypto",
            CRYPTO,
            "crypto",
            "5m",
        ),
        (
            "💵 Forex",
            FOREX,
            "forex",
            "15m",
        ),
        (
            "📈 Actions",
            ACTIONS,
            "stock",
            "15m",
        ),
        (
            "📊 Indices",
            INDICES,
            "index",
            "15m",
        ),
        (
            "🛢️ Matières premières",
            COMMODITIES + COMMODITIES_YAHOO_ONLY,
            "commodity",
            "15m",
        ),
    ]


# ---------------------------------------------------------------------------
# ROUTEUR
# ---------------------------------------------------------------------------

def router_fetcher(router, asset_type, symbol_maps):
    """
    Adaptateur entre scan.py et DataRouter.

    Le choix du fournisseur est volontairement laissé au routeur.

    require_volume :
    - False pour Forex et indices : le volume n'est généralement pas
      une donnée de marché exploitable de manière uniforme.
    - True pour crypto/actions/commodités : on privilégie une source
      capable de fournir un vrai volume.
    """

    require_volume = asset_type not in {"forex", "index"}

    def fetch(symbol, interval="15m", limit=1000, **kwargs):
        return router.fetch(
            symbol,
            interval=interval,
            limit=limit,
            asset_type=asset_type,
            preferred=None,
            require_volume=require_volume,
            symbol_map=symbol_maps.get(symbol, {}),
        )

    fetch._router = True
    return fetch


# ---------------------------------------------------------------------------
# SCAN COMPLET
# ---------------------------------------------------------------------------

def scan_all(threshold=75, limit=1000, top=5):
    """
    Lance le scan de l'ensemble de l'univers V4.2.

    Le DataRouter est créé UNE SEULE FOIS afin que :
    - le cache soit partagé ;
    - les statistiques soient consolidées ;
    - le budget Twelve Data soit partagé sur tout le scan ;
    - le routeur puisse arbitrer intelligemment entre fournisseurs.
    """

    router = DataRouter()

    all_results = {}
    coverage = {}

    for name, entries, asset_type, interval in asset_groups():

        symbols = []
        symbol_maps = {}

        for entry in entries:

            if isinstance(entry, dict):
                canonical = (
                    entry.get("display")
                    or entry.get("yahoo")
                    or entry.get("twelvedata")
                    or entry.get("finnhub")
                    or entry.get("binance")
                )
            else:
                canonical = entry

            if not canonical:
                continue

            symbols.append(canonical)
            symbol_maps[canonical] = entry_map(entry, canonical)

        print()
        print("=" * 72)
        print(f"{name} — {len(symbols)} actifs")
        print("=" * 72)

        fetcher = router_fetcher(
            router=router,
            asset_type=asset_type,
            symbol_maps=symbol_maps,
        )

        pause = PAUSE_BY_ASSET_TYPE.get(asset_type, 0.20)

        df = scan(
            symbols,
            fetcher,
            interval=interval,
            limit=limit,
            threshold=threshold,
            pause=pause,
            provider_name="router",
            asset_type=asset_type,
        )

        all_results[name] = df

        analyzed = len(df)
        requested = len(symbols)

        coverage[name] = {
            "requested": requested,
            "analyzed": analyzed,
            "failed": max(requested - analyzed, 0),
        }

    return all_results, router, coverage


# ---------------------------------------------------------------------------
# SIGNALS
# ---------------------------------------------------------------------------

def has_signal(all_results):
    """Retourne True si au moins un SIGNAL FORT existe."""

    for df in all_results.values():

        if df.empty:
            continue

        if "status" not in df.columns:
            continue

        if (df["status"] == "SIGNAL FORT").any():
            return True

    return False


def count_signals(all_results):
    """Compte le nombre total de SIGNAL FORT."""

    total = 0

    for df in all_results.values():

        if df.empty:
            continue

        if "status" not in df.columns:
            continue

        total += int(
            (df["status"] == "SIGNAL FORT").sum()
        )

    return total


# ---------------------------------------------------------------------------
# COUVERTURE
# ---------------------------------------------------------------------------

def coverage_summary(coverage):
    """Résumé de la couverture du scan."""

    lines = ["🛡️ COUVERTURE DU SCAN"]

    total_requested = 0
    total_analyzed = 0
    total_failed = 0

    for name, info in coverage.items():

        requested = int(info["requested"])
        analyzed = int(info["analyzed"])
        failed = int(info["failed"])

        total_requested += requested
        total_analyzed += analyzed
        total_failed += failed

        if requested and analyzed == requested:
            status = "🟢 OK"

        elif analyzed > 0:
            status = "🟠 PARTIEL"

        elif requested:
            status = "🔴 ÉCHEC"

        else:
            status = "—"

        lines.append(
            f"{name}: {analyzed}/{requested} analysés — {status}"
        )

    if total_failed == 0:
        global_status = "🟢 SCAN COMPLET"
    else:
        global_status = "⚠️ SCAN PARTIEL"

    lines.extend(
        [
            "",
            (
                f"{global_status} : "
                f"{total_analyzed}/{total_requested} actifs analysés"
            ),
        ]
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# FORMATAGE
# ---------------------------------------------------------------------------

def number_text(value, decimals=4):
    """Formate un nombre sans afficher nan."""

    if value is None:
        return "n/d"

    try:
        if pd.isna(value):
            return "n/d"

        return f"{float(value):.{decimals}f}"

    except (TypeError, ValueError):
        return "n/d"


def relvol_text(value):
    """Formate le Relative Volume."""

    if value is None:
        return "n/d"

    try:
        if pd.isna(value):
            return "n/d"

        return f"{float(value):.2f}"

    except (TypeError, ValueError):
        return "n/d"


def rsi_text(value):
    """Formate le RSI."""

    if value is None:
        return "n/d"

    try:
        if pd.isna(value):
            return "n/d"

        return f"{float(value):.1f}"

    except (TypeError, ValueError):
        return "n/d"


def volume_text(row):
    """
    Formate le statut du volume.

    On distingue :
    - volume confirmé ;
    - volume indisponible ;
    - volume invalide.
    """

    volume_status = str(
        row.get("volume_status", "")
    ).upper()

    volume_available = bool(
        row.get("volume_available", False)
    )

    relvol = relvol_text(
        row.get("relvol")
    )

    if volume_status == VOLUME_CONFIRMED:
        return f"confirmé (RelVol {relvol})"

    if volume_status == VOLUME_INVALID:
        return "invalide / non exploitable"

    if volume_status == VOLUME_UNAVAILABLE:
        return "n/d — non disponible"

    if volume_available:
        return f"disponible (RelVol {relvol})"

    return "n/d — volume non disponible"


def format_signal_block(row):
    """Bloc de notification pour un signal fort."""

    return (
        f"{row['symbol']} | "
        f"{row['direction']} | "
        f"{row['status']} | "
        f"Qualité {row.get('quality', 'D')} | "
        f"Score {float(row['best_score']):.0f}/100\n"

        f"   Entrée : "
        f"{number_text(row.get('entry'))}\n"

        f"   SL : "
        f"{number_text(row.get('stop_loss'))}\n"

        f"   TP1 : "
        f"{number_text(row.get('take_profit_1'))} "
        f"(R:R {number_text(row.get('rr_tp1'), 2)})\n"

        f"   TP2 : "
        f"{number_text(row.get('take_profit_2'))} "
        f"(R:R {number_text(row.get('rr_tp2'), 2)})\n"

        f"   ATR : "
        f"{number_text(row.get('atr'))} | "
        f"RSI : "
        f"{rsi_text(row.get('rsi'))}\n"

        f"   Volume : "
        f"{volume_text(row)}\n"

        f"   Source : "
        f"{row.get('provider', 'n/d')} | "

        f"Statut volume : "
        f"{row.get('volume_status', 'n/d')}"
    )


def format_watch_block(row, rank, threshold):
    """Bloc pour les actifs à surveiller."""

    missing = []

    best_score = row.get("best_score")

    if best_score is None or pd.isna(best_score):
        missing.append("SCORE")

    elif best_score < threshold:
        missing.append(
            f"SCORE < {threshold:.0f}"
        )

    if not bool(row.get("breakout_ok", False)):
        missing.append("BREAKOUT")

    volume_available = bool(
        row.get("volume_available", False)
    )

    if (
        volume_available
        and not bool(row.get("volume_ok", False))
    ):
        missing.append("VOLUME")

    rr_tp2 = row.get("rr_tp2")

    if rr_tp2 is None or pd.isna(rr_tp2):
        missing.append("R:R")

    elif rr_tp2 < 1.5:
        missing.append("R:R")

    return (
        f"{rank}. {row['symbol']} | "
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
        f"Volume:"
        f"{row.get('volume_status', 'n/d')} | "

        f"Manque : "
        f"{' + '.join(missing) if missing else 'aucune'}"
    )


# ---------------------------------------------------------------------------
# FRAÎCHEUR
# ---------------------------------------------------------------------------

def freshness_summary(all_results, now=None):
    """
    Résumé de fraîcheur des données.

    Le calcul se base sur la dernière bougie réellement disponible.
    """

    now = now or datetime.now(timezone.utc)

    lines = [
        "🕐 FRAÎCHEUR DES DONNÉES"
    ]

    for name, df in all_results.items():

        if (
            df.empty
            or "last_candle" not in df.columns
        ):
            lines.append(
                f"{name}: aucune donnée exploitable"
            )
            continue

        timestamps = pd.to_datetime(
            df["last_candle"],
            utc=True,
            errors="coerce",
        )

        timestamps = timestamps.dropna()

        if timestamps.empty:
            lines.append(
                f"{name}: horodatage indisponible"
            )
            continue

        most_recent = timestamps.max()

        age = (
            pd.Timestamp(now) - most_recent
        ).total_seconds() / 60

        # Week-end ou période nocturne :
        # une donnée plus ancienne n'est pas automatiquement considérée
        # comme défectueuse.
        market_closed = (
            pd.Timestamp(now).weekday() >= 5
            or pd.Timestamp(now).hour < 7
            or pd.Timestamp(now).hour >= 21
        )

        if age <= 90:
            flag = "🟢 frais"

        elif market_closed:
            flag = "🟡 marché probablement fermé"

        else:
            flag = "🔴 données anciennes"

        lines.append(
            f"{name}: "
            f"{most_recent.strftime('%H:%M')} UTC "
            f"({age:.0f} min) {flag}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ROUTAGE / STATISTIQUES
# ---------------------------------------------------------------------------

def router_summary(router):
    """
    Résume l'utilisation des fournisseurs.

    Compatible avec les statistiques V4.2 exposées par DataRouter.
    """

    lines = [
        "--- ROUTAGE DES SOURCES ---"
    ]

    stats = getattr(
        router,
        "stats",
        {},
    )

    if not stats:
        lines.append(
            "Aucune statistique fournisseur disponible."
        )
    else:
        for provider, state in stats.items():

            if not isinstance(state, dict):
                lines.append(
                    f"{provider}: {state}"
                )
                continue

            success = int(
                state.get("success", 0)
            )

            fail = int(
                state.get("fail", 0)
            )

            attempts = int(
                state.get("attempts", 0)
            )

            lines.append(
                f"{provider}: "
                f"{success} succès / "
                f"{fail} échecs / "
                f"{attempts} appels"
            )

    td_used = getattr(
        router,
        "td_used",
        0,
    )

    td_budget = getattr(
        router,
        "td_budget",
        0,
    )

    lines.append(
        f"Twelve Data budget : "
        f"{td_used}/{td_budget} appels estimés"
    )

    return lines


# ---------------------------------------------------------------------------
# MESSAGE COMPLET
# ---------------------------------------------------------------------------

def format_full_message(
    all_results,
    router,
    threshold=75,
    top=5,
    now=None,
    coverage=None,
):
    """Construit le message Slack / Email complet."""

    now = now or datetime.now(timezone.utc)

    strong_count = count_signals(
        all_results
    )

    lines = [
        "📊 SCAN MULTI-ACTIFS V4.2",
        now.strftime(
            "%Y-%m-%d %H:%M UTC"
        ),
        f"Seuil : {threshold:.0f}/100",
        "",
    ]

    if coverage:
        lines.extend(
            [
                coverage_summary(
                    coverage
                ),
                "",
            ]
        )

    lines.extend(
        [
            "🔥 SIGNAL FORT",
            (
                "Conditions : "
                "SCORE ≥ seuil + BREAKOUT + "
                "VOLUME si disponible + R:R ≥ 1.5"
            ),
            f"Total SIGNAL FORT : {strong_count}",
            "",
        ]
    )

    lines.extend(
        router_summary(router)
    )

    lines.append("")

    # ------------------------------------------------------------------
    # SIGNALS FORTS
    # ------------------------------------------------------------------

    if strong_count == 0:

        lines.extend(
            [
                "Aucun signal fort sur ce scan.",
                "",
            ]
        )

    else:

        for name, df in all_results.items():

            if df.empty:
                continue

            if "status" not in df.columns:
                continue

            strong = df[
                df["status"] == "SIGNAL FORT"
            ]

            if strong.empty:
                continue

            lines.append(
                f"--- {name} ---"
            )

            for _, row in strong.iterrows():

                lines.extend(
                    [
                        format_signal_block(row),
                        "",
                    ]
                )

    # ------------------------------------------------------------------
    # WATCHLIST
    # ------------------------------------------------------------------

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

        if df.empty:
            lines.extend(
                [
                    "Aucune donnée exploitable.",
                    "",
                ]
            )
            continue

        if "best_score" not in df.columns:
            lines.extend(
                [
                    "Résultats incomplets.",
                    "",
                ]
            )
            continue

        watch = (
            df
            .sort_values(
                "best_score",
                ascending=False,
            )
            .head(top)
        )

        for rank, (_, row) in enumerate(
            watch.iterrows(),
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

    # ------------------------------------------------------------------
    # FRAÎCHEUR
    # ------------------------------------------------------------------

    lines.append(
        freshness_summary(
            all_results,
            now,
        )
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Scanner multi-actifs intelligent V4.2"
        )
    )

    parser.add_argument(
        "--threshold",
        type=float,
        default=75,
        help=(
            "Seuil minimum du score "
            "(défaut: 75)"
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help=(
            "Nombre maximal de bougies "
            "(défaut: 1000)"
        ),
    )

    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help=(
            "Nombre d'actifs à afficher "
            "dans la watchlist par catégorie "
            "(défaut: 5)"
        ),
    )

    args = parser.parse_args()

    now = datetime.now(timezone.utc)

    print()
    print("=" * 72)
    print(
        "SCAN MULTI-ACTIFS V4.2"
    )
    print(
        f"{now.strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )
    print(
        f"Seuil : {args.threshold:.0f}/100"
    )
    print(
        f"Limite bougies : {args.limit}"
    )
    print("=" * 72)

    try:

        all_results, router, coverage = scan_all(
            threshold=args.threshold,
            limit=args.limit,
            top=args.top,
        )

    except Exception as exc:

        print(
            f"[FATAL] Échec du scan : {exc}"
        )

        raise

    message = format_full_message(
        all_results,
        router,
        threshold=args.threshold,
        top=args.top,
        now=now,
        coverage=coverage,
    )

    print()
    print(message)
    print()

    signal_count = count_signals(
        all_results
    )

    # ------------------------------------------------------------------
    # EMAIL
    # ------------------------------------------------------------------

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
        (
            f"Scan V4.2 — "
            f"{signal_count} signal(s) fort(s) — "
            f"{now.strftime('%Y-%m-%d %H:%M')} UTC"
        ),
        message,
    )

    # ------------------------------------------------------------------
    # SLACK
    # ------------------------------------------------------------------

    slack_webhook = os.getenv(
        "SLACK_WEBHOOK_URL"
    )

    if has_signal(all_results):

        if slack_webhook:

            send_slack(
                slack_webhook,
                message,
            )

        else:

            print(
                "[Slack] "
                "SLACK_WEBHOOK_URL non configuré."
            )

    else:

        print(
            "[Slack] Aucun SIGNAL FORT "
            "— Slack non envoyé."
        )


if __name__ == "__main__":
    main()
