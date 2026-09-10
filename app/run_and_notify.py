"""
V4.2 — Scanner multi-actifs + routeur intelligent + notifications.

Architecture :
- Binance : crypto 24/7
- Yahoo Finance : source large et gratuite
- Finnhub : source gratuite complémentaire
- Twelve Data : source de secours / complément avec quota journalier
- DataRouter : sélection automatique de la meilleure source disponible
- Univers : 86 actifs
- Notifications : email + Slack

Ce fichier orchestre :
1. récupération des données
2. scan technique
3. agrégation des résultats
4. contrôle de fraîcheur
5. résumé du routage
6. notifications
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from assets import (
    ASSET_GROUPS,
    get_asset_type,
    get_symbol_map,
)

from data_sources import (
    DataRouter,
    DataSourceError,
    VOLUME_CONFIRMED,
    VOLUME_PARTIAL,
    VOLUME_UNAVAILABLE,
    VOLUME_INVALID,
)

import scan

from notify import (
    send_email,
    send_slack,
)


# ============================================================================
# CONFIGURATION
# ============================================================================

PAUSE_BY_ASSET_TYPE = {
    "crypto": 0.20,
    "forex": 0.20,
    "stock": 0.20,
    "index": 0.20,
    "commodity": 0.20,
}


INTERVAL_BY_ASSET_TYPE = {
    "crypto": "5min",
    "forex": "15min",
    "stock": "15min",
    "index": "15min",
    "commodity": "15min",
}


# ============================================================================
# OUTILS
# ============================================================================

def _safe_float(value: Any, default: float = 0.0) -> float:
    """Conversion robuste vers float."""

    try:
        if value is None:
            return default

        result = float(value)

        if pd.isna(result):
            return default

        return result

    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    """Conversion robuste vers int."""

    try:
        if value is None:
            return default

        result = int(value)

        if pd.isna(result):
            return default

        return result

    except (TypeError, ValueError):
        return default


def _format_price(value: Any) -> str:
    """Format compact d'un prix."""

    number = _safe_float(value)

    if number == 0:
        return "-"

    if abs(number) >= 1000:
        return f"{number:,.2f}"

    if abs(number) >= 1:
        return f"{number:.4f}"

    return f"{number:.8f}"


def _format_percent(value: Any) -> str:
    """Format d'un pourcentage."""

    number = _safe_float(value)

    return f"{number:.2f}%"


def _now_utc() -> datetime:
    """Heure UTC actuelle."""

    return datetime.now(timezone.utc)


# ============================================================================
# ROUTER FETCHER
# ============================================================================

def router_fetcher(
    router: DataRouter,
    canonical_symbol: str,
    asset_type: str,
    symbol_map: dict[str, Any],
    limit: int,
) -> pd.DataFrame:
    """
    Récupère les données via le DataRouter.

    Règle de volume :
    - crypto : volume obligatoire
    - stock  : volume obligatoire
    - forex  : volume non obligatoire
    - index  : volume non obligatoire
    - commodity : volume non obligatoire

    Le symbol_map provenant de assets.py est transmis tel quel au routeur.
    """

    interval = INTERVAL_BY_ASSET_TYPE.get(
        asset_type,
        "15min",
    )

    require_volume = asset_type in {
        "crypto",
        "stock",
    }

    try:
        data = router.fetch(
            canonical_symbol,
            interval=interval,
            limit=limit,
            symbol_map=symbol_map,
            require_volume=require_volume,
            asset_type=asset_type,
        )

    except TypeError:
        # Compatibilité avec une éventuelle signature légèrement différente
        # du routeur tout en conservant le nouveau routage par symbol_map.
        data = router.fetch(
            canonical_symbol,
            interval=interval,
            limit=limit,
            symbol_map=symbol_map,
            require_volume=require_volume,
        )

    if data is None:
        raise DataSourceError(
            f"[{canonical_symbol}] aucune donnée retournée."
        )

    if not isinstance(data, pd.DataFrame):
        raise DataSourceError(
            f"[{canonical_symbol}] type de données invalide : "
            f"{type(data).__name__}"
        )

    if data.empty:
        raise DataSourceError(
            f"[{canonical_symbol}] DataFrame vide."
        )

    return data


# ============================================================================
# SCAN D'UN ACTIF
# ============================================================================

def scan_one_asset(
    router: DataRouter,
    canonical_symbol: str,
    threshold: float,
    limit: int,
) -> dict[str, Any]:
    """
    Scanne un actif.

    Retourne toujours un dictionnaire afin qu'une erreur sur un actif
    n'arrête jamais le scan global.
    """

    asset_type = get_asset_type(canonical_symbol)

    symbol_map = get_symbol_map(canonical_symbol)

    result: dict[str, Any] = {
        "symbol": canonical_symbol,
        "asset": canonical_symbol,
        "asset_type": asset_type,
        "status": "error",
        "signal": None,
        "score": 0.0,
        "error": None,
    }

    try:

        # --------------------------------------------------------------
        # Récupération
        # --------------------------------------------------------------

        data = router_fetcher(
            router=router,
            canonical_symbol=canonical_symbol,
            asset_type=asset_type,
            symbol_map=symbol_map,
            limit=limit,
        )

        # --------------------------------------------------------------
        # Scan technique
        # --------------------------------------------------------------

        scan_result = scan.scan_asset(
            data,
            symbol=canonical_symbol,
            threshold=threshold,
            provider_name=str(
                data.attrs.get("provider", "")
            ),
        )

        if scan_result is None:
            result["status"] = "insufficient"
            return result

        if isinstance(scan_result, dict):
            result.update(scan_result)

        else:
            result["result"] = scan_result

        # --------------------------------------------------------------
        # Normalisation
        # --------------------------------------------------------------

        score = result.get(
            "score",
            result.get(
                "total_score",
                0.0,
            ),
        )

        result["score"] = _safe_float(score)

        signal = result.get("signal")

        if signal is None:
            signal = result.get("direction")

        result["signal"] = signal

        if result.get("status") in {
            None,
            "",
            "error",
        }:
            result["status"] = "ok"

        return result

    except DataSourceError as exc:

        result["status"] = "insufficient"
        result["error"] = str(exc)

        print(
            f"[{canonical_symbol}] données insuffisantes : {exc}"
        )

        return result

    except Exception as exc:

        result["status"] = "error"
        result["error"] = str(exc)

        print(
            f"[{canonical_symbol}] erreur scan : {exc}"
        )

        return result


# ============================================================================
# SCAN GLOBAL
# ============================================================================

def scan_all(
    threshold: float = 75.0,
    limit: int = 1000,
    top: int = 5,
) -> dict[str, Any]:
    """
    Scanne l'ensemble de l'univers V4.2.

    Retourne :
        {
            "results": [...],
            "strong_signals": [...],
            "errors": [...],
            "insufficient": [...],
            "router": router,
            "stats": {...}
        }
    """

    router = DataRouter()

    all_assets: list[str] = []

    # ------------------------------------------------------------------
    # Construction de l'univers
    # ------------------------------------------------------------------

    for group_name, symbols in ASSET_GROUPS.items():

        # Éviter les doublons provoqués par les alias éventuels
        for symbol in symbols:

            if symbol not in all_assets:
                all_assets.append(symbol)

    print("=" * 80)
    print("V4.2 — SCANNER MULTI-ACTIFS")
    print("=" * 80)

    print(f"Univers total : {len(all_assets)} actifs")
    print(f"Seuil signal  : {threshold}")
    print(f"Limite data   : {limit}")
    print()

    results: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Scan actif par actif
    # ------------------------------------------------------------------

    for index, canonical_symbol in enumerate(
        all_assets,
        start=1,
    ):

        asset_type = get_asset_type(canonical_symbol)

        print(
            f"[{index}/{len(all_assets)}] "
            f"{canonical_symbol} "
            f"({asset_type})"
        )

        result = scan_one_asset(
            router=router,
            canonical_symbol=canonical_symbol,
            threshold=threshold,
            limit=limit,
        )

        results.append(result)

        pause = PAUSE_BY_ASSET_TYPE.get(
            asset_type,
            0.20,
        )

        if pause > 0 and index < len(all_assets):
            time.sleep(pause)

    # ------------------------------------------------------------------
    # Classement
    # ------------------------------------------------------------------

    strong_signals = [
        item
        for item in results
        if item.get("status") in {
            "ok",
            "signal",
            "strong",
        }
        and item.get("signal") in {
            "LONG",
            "SHORT",
        }
        and _safe_float(
            item.get("score", 0)
        ) >= threshold
    ]

    strong_signals.sort(
        key=lambda item: _safe_float(
            item.get("score", 0)
        ),
        reverse=True,
    )

    strong_signals = strong_signals[:max(1, top)]

    errors = [
        item
        for item in results
        if item.get("status") == "error"
    ]

    insufficient = [
        item
        for item in results
        if item.get("status") == "insufficient"
    ]

    analyzed = [
        item
        for item in results
        if item.get("status") in {
            "ok",
            "signal",
            "strong",
        }
    ]

    # ------------------------------------------------------------------
    # Statistiques
    # ------------------------------------------------------------------

    stats = {
        "total": len(results),
        "analyzed": len(analyzed),
        "errors": len(errors),
        "insufficient": len(insufficient),
        "strong_signals": len(strong_signals),
    }

    print()
    print("=" * 80)
    print(
        f"TOTAL : {len(results)} actifs | "
        f"analysés={len(analyzed)} | "
        f"erreurs={len(errors)} | "
        f"insuffisants={len(insufficient)}"
    )

    print(
        f"SIGNAUX FORTS : {len(strong_signals)}"
    )

    return {
        "results": results,
        "strong_signals": strong_signals,
        "errors": errors,
        "insufficient": insufficient,
        "router": router,
        "stats": stats,
    }


# ============================================================================
# FRAÎCHEUR DES DONNÉES
# ============================================================================

def freshness_summary(
    results: list[dict[str, Any]],
) -> str:
    """
    Résume la fraîcheur des données.

    Un timestamp futur est explicitement considéré comme anormal.
    """

    now = _now_utc()

    ages: list[float] = []
    future_count = 0
    stale_count = 0

    for item in results:

        timestamp = item.get(
            "timestamp",
            item.get("last_timestamp"),
        )

        if timestamp is None:
            continue

        try:

            ts = pd.Timestamp(timestamp)

            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            else:
                ts = ts.tz_convert("UTC")

            age_seconds = (
                now - ts.to_pydatetime()
            ).total_seconds()

            # ----------------------------------------------------------
            # Timestamp futur
            # ----------------------------------------------------------

            if age_seconds < -120:
                future_count += 1
                continue

            # ----------------------------------------------------------
            # Donnée ancienne
            # ----------------------------------------------------------

            if age_seconds > 90 * 60:
                stale_count += 1

            ages.append(
                max(
                    0.0,
                    age_seconds,
                )
            )

        except Exception:
            continue

    if not ages and future_count == 0:
        return (
            "Fraîcheur : aucune donnée temporelle exploitable."
        )

    parts: list[str] = []

    if ages:
        parts.append(
            f"âge médian={sorted(ages)[len(ages)//2]:.0f}s"
        )

        parts.append(
            f"max={max(ages):.0f}s"
        )

    if stale_count:
        parts.append(
            f"anciennes={stale_count}"
        )

    if future_count:
        parts.append(
            f"futures={future_count}"
        )

    return "Fraîcheur : " + " | ".join(parts)


# ============================================================================
# RÉSUMÉ DU ROUTEUR
# ============================================================================

def router_summary(router: DataRouter) -> str:
    """
    Produit un résumé des statistiques du DataRouter.

    Fonction volontairement tolérante :
    elle accepte plusieurs noms d'attributs selon la version interne
    du routeur.
    """

    stats = getattr(
        router,
        "stats",
        None,
    )

    if isinstance(stats, dict):

        parts: list[str] = []

        for provider in (
            "binance",
            "finnhub",
            "yahoo",
            "twelve_data",
        ):

            provider_stats = stats.get(
                provider
            )

            if isinstance(provider_stats, dict):

                calls = provider_stats.get(
                    "calls",
                    provider_stats.get(
                        "appels",
                        0,
                    ),
                )

                success = provider_stats.get(
                    "success",
                    provider_stats.get(
                        "succès",
                        0,
                    ),
                )

                failures = provider_stats.get(
                    "failures",
                    provider_stats.get(
                        "échecs",
                        0,
                    ),
                )

                parts.append(
                    f"{provider} "
                    f"appels={calls} "
                    f"succès={success} "
                    f"échecs={failures}"
                )

        if parts:
            return " | ".join(parts)

    # ------------------------------------------------------------------
    # Compatibilité avec les attributs séparés
    # ------------------------------------------------------------------

    provider_names = (
        "binance",
        "finnhub",
        "yahoo",
        "twelve_data",
    )

    parts = []

    for provider in provider_names:

        provider_stat = getattr(
            router,
            f"{provider}_stats",
            None,
        )

        if isinstance(provider_stat, dict):

            calls = provider_stat.get(
                "calls",
                provider_stat.get(
                    "appels",
                    0,
                ),
            )

            success = provider_stat.get(
                "success",
                provider_stat.get(
                    "succès",
                    0,
                ),
            )

            failures = provider_stat.get(
                "failures",
                provider_stat.get(
                    "échecs",
                    0,
                ),
            )

            parts.append(
                f"{provider} "
                f"appels={calls} "
                f"succès={success} "
                f"échecs={failures}"
            )

    if parts:
        return " | ".join(parts)

    return "Statistiques routeur : non disponibles."


# ============================================================================
# VOLUME
# ============================================================================

def volume_summary(
    results: list[dict[str, Any]],
) -> str:
    """
    Résume l'état de disponibilité du volume.
    """

    counters = {
        VOLUME_CONFIRMED: 0,
        VOLUME_PARTIAL: 0,
        VOLUME_UNAVAILABLE: 0,
        VOLUME_INVALID: 0,
    }

    for item in results:

        status = item.get(
            "volume_status"
        )

        if status in counters:
            counters[status] += 1

    return (
        "Volume : "
        f"confirmé={counters[VOLUME_CONFIRMED]} "
        f"partiel={counters[VOLUME_PARTIAL]} "
        f"indisponible={counters[VOLUME_UNAVAILABLE]} "
        f"invalide={counters[VOLUME_INVALID]}"
    )


# ============================================================================
# FORMATAGE DES SIGNAUX
# ============================================================================

def format_signal(
    item: dict[str, Any],
) -> str:
    """Formate un signal pour email/Slack."""

    symbol = item.get(
        "symbol",
        item.get("asset", "?"),
    )

    signal = item.get(
        "signal",
        item.get("direction", "?"),
    )

    score = _safe_float(
        item.get(
            "score",
            item.get(
                "total_score",
                0,
            ),
        )
    )

    price = item.get(
        "price",
        item.get(
            "entry",
            item.get(
                "entry_price",
            ),
        ),
    )

    sl = item.get(
        "stop_loss",
        item.get(
            "sl",
        ),
    )

    tp1 = item.get(
        "take_profit_1",
        item.get(
            "tp1",
        ),
    )

    tp2 = item.get(
        "take_profit_2",
        item.get(
            "tp2",
        ),
    )

    rr = item.get(
        "rr_tp2",
        item.get(
            "risk_reward",
            item.get("rr"),
        ),
    )

    provider = item.get(
        "provider",
        "-",
    )

    lines = [
        f"🚨 {symbol} — {signal}",
        f"Score : {score:.1f}/100",
        f"Entrée : {_format_price(price)}",
        f"Stop Loss : {_format_price(sl)}",
        f"TP1 : {_format_price(tp1)}",
        f"TP2 : {_format_price(tp2)}",
        f"RR TP2 : {_safe_float(rr):.2f}",
        f"Source : {provider}",
    ]

    return "\n".join(lines)


# ============================================================================
# MESSAGE COMPLET
# ============================================================================

def build_notification_message(
    report: dict[str, Any],
    threshold: float,
) -> str:
    """Construit le message complet de notification."""

    stats = report["stats"]

    strong_signals = report[
        "strong_signals"
    ]

    results = report[
        "results"
    ]

    lines = [
        "V4.2 — RAPPORT DU SCANNER",
        "",
        (
            f"Actifs : {stats['total']} | "
            f"analysés : {stats['analyzed']} | "
            f"erreurs : {stats['errors']} | "
            f"insuffisants : {stats['insufficient']}"
        ),
        (
            f"Seuil : {threshold:.0f}/100 | "
            f"signaux forts : {len(strong_signals)}"
        ),
        "",
        freshness_summary(results),
        volume_summary(results),
        "",
    ]

    router = report.get("router")

    if router is not None:
        lines.append(
            router_summary(router)
        )
        lines.append("")

    if strong_signals:

        lines.append(
            "=== SIGNAUX FORTS ==="
        )

        lines.append("")

        for position, item in enumerate(
            strong_signals,
            start=1,
        ):

            lines.append(
                f"#{position}"
            )

            lines.append(
                format_signal(item)
            )

            lines.append("")

    else:

        lines.append(
            "Aucun signal fort détecté."
        )

    return "\n".join(lines)


# ============================================================================
# MAIN
# ============================================================================

def main() -> int:
    """Point d'entrée principal."""

    parser = argparse.ArgumentParser(
        description=(
            "V4.2 — Scanner multi-actifs "
            "avec DataRouter intelligent."
        )
    )

    parser.add_argument(
        "--threshold",
        type=float,
        default=75.0,
        help=(
            "Score minimal pour un signal "
            "(défaut: 75)"
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help=(
            "Nombre maximal de bougies demandées "
            "(défaut: 1000)"
        ),
    )

    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help=(
            "Nombre maximal de signaux forts "
            "à notifier (défaut: 5)"
        ),
    )

    args = parser.parse_args()

    threshold = max(
        0.0,
        min(
            100.0,
            args.threshold,
        ),
    )

    limit = max(
        120,
        args.limit,
    )

    top = max(
        1,
        args.top,
    )

    print()
    print("=" * 80)
    print("V4.2 — DÉMARRAGE")
    print("=" * 80)
    print(
        f"threshold={threshold}"
        f" | limit={limit}"
        f" | top={top}"
    )
    print()

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    try:

        report = scan_all(
            threshold=threshold,
            limit=limit,
            top=top,
        )

    except Exception as exc:

        print(
            f"[FATAL] Erreur générale du scanner : {exc}"
        )

        error_message = (
            "V4.2 — ERREUR CRITIQUE\n\n"
            f"Le scanner n'a pas pu terminer.\n\n"
            f"Erreur : {exc}"
        )

        send_email(
            subject="V4.2 — Erreur critique du scanner",
            body=error_message,
        )

        return 1

    # ------------------------------------------------------------------
    # Rapport
    # ------------------------------------------------------------------

    message = build_notification_message(
        report=report,
        threshold=threshold,
    )

    print()
    print("=" * 80)
    print(message)
    print("=" * 80)

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------

    email_ok = send_email(
        subject=(
            "V4.2 — "
            f"{len(report['strong_signals'])} "
            "signal(s) fort(s)"
        ),
        body=message,
    )

    # ------------------------------------------------------------------
    # Slack
    #
    # Slack uniquement si un signal fort existe.
    # ------------------------------------------------------------------

    slack_ok = False

    if report["strong_signals"]:

        slack_ok = send_slack(
            message
        )

    # ------------------------------------------------------------------
    # Résumé notifications
    # ------------------------------------------------------------------

    print()
    print(
        f"[Notification] "
        f"Email={'OK' if email_ok else 'NON'} "
        f"Slack={'OK' if slack_ok else 'NON'}"
    )

    # ------------------------------------------------------------------
    # Code retour
    #
    # Un échec de notification ne transforme pas un scan valide
    # en échec GitHub Actions.
    # ------------------------------------------------------------------

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
```
