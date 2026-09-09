"""
Lance le scan multi-actifs et envoie les résultats sur Slack + Email —
uniquement si au moins un signal dépasse le seuil.

Répartition des sources de données :
- Crypto            : Binance, toujours (24/7, gratuit, sans clé)
- Forex             : Twelve Data, toujours
- Actions           : Twelve Data de 3h à 19h UTC, Yahoo Finance le reste
- Indices            : Yahoo Finance, toujours
- Matières premières : Twelve Data de 3h à 19h UTC, Yahoo Finance le reste

v1.4 — diagnostic détaillé :
- Affichage des composantes du score pour les meilleurs actifs sous le seuil.
- Permet de voir précisément les points de tendance, EMA, RSI, volume
  et breakout.
- Conservation de l'affichage de la fraîcheur des données.
"""

import argparse
import os
from datetime import datetime, timezone

import pandas as pd

from backtest import klines as klines_binance
from data_sources import klines_yahoo, klines_twelvedata
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


# =========================================================
# PAUSE ENTRE LES APPELS API
# =========================================================

PAUSE_BY_PROVIDER = {
    "binance": 0.3,
    "twelvedata": 9.0,
    "yahoo": 0.5,
}


# =========================================================
# FENÊTRE TWELVE DATA
# =========================================================

def twelvedata_window_active(now=None):
    now = now or datetime.now(timezone.utc)
    return 3 <= now.hour < 19


# =========================================================
# CONSTRUCTION DES CATÉGORIES
# =========================================================

def build_categories(now=None):
    use_twelvedata = twelvedata_window_active(now)

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

    stock_pause = PAUSE_BY_PROVIDER[stock_provider]

    def resolve_symbols(entries, key):
        return [e[key] for e in entries]

    categories = {
        "🪙 Crypto": [
            {
                "symbols": CRYPTO,
                "fetcher": klines_binance,
                "interval": "5m",
                "pause": PAUSE_BY_PROVIDER["binance"],
            }
        ],

        "💵 Forex": [
            {
                "symbols": resolve_symbols(FOREX, "twelvedata"),
                "fetcher": klines_twelvedata,
                "interval": "15m",
                "pause": PAUSE_BY_PROVIDER["twelvedata"],
            }
        ],

        "📈 Actions": [
            {
                "symbols": resolve_symbols(
                    ACTIONS,
                    stock_provider
                ),
                "fetcher": stock_fetcher,
                "interval": "15m",
                "pause": stock_pause,
            }
        ],

        "📊 Indices": [
            {
                "symbols": resolve_symbols(
                    INDICES,
                    "yahoo"
                ),
                "fetcher": klines_yahoo,
                "interval": "15m",
                "pause": PAUSE_BY_PROVIDER["yahoo"],
            }
        ],

        "🛢️ Matières premières": [
            {
                "symbols": resolve_symbols(
                    COMMODITIES,
                    stock_provider
                ),
                "fetcher": stock_fetcher,
                "interval": "15m",
                "pause": stock_pause,
            },
            {
                "symbols": resolve_symbols(
                    COMMODITIES_YAHOO_ONLY,
                    "yahoo"
                ),
                "fetcher": klines_yahoo,
                "interval": "15m",
                "pause": PAUSE_BY_PROVIDER["yahoo"],
            },
        ],
    }

    return categories, stock_provider


# =========================================================
# SCAN DE TOUTES LES CATÉGORIES
# =========================================================

def scan_all(threshold=60, limit=500, now=None):
    categories, stock_provider = build_categories(now)

    results = {}

    for name, groups in categories.items():

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
                ignore_index=True
            )

            merged["max_score"] = merged[
                ["score_long", "score_short"]
            ].max(axis=1)

            merged = (
                merged
                .sort_values(
                    "max_score",
                    ascending=False
                )
                .drop(columns="max_score")
                .reset_index(drop=True)
            )

            results[name] = merged

        else:
            results[name] = pd.DataFrame()

    return results, stock_provider


# =========================================================
# DÉTECTION D'UN SIGNAL
# =========================================================

def has_signal(all_results):

    for df in all_results.values():

        if (
            not df.empty
            and df["direction"]
            .isin(["LONG", "SHORT"])
            .any()
        ):
            return True

    return False


# =========================================================
# FRAÎCHEUR DES DONNÉES
# =========================================================

def freshness_summary(all_results, now=None):

    now = now or datetime.now(timezone.utc)

    lines = []

    for name, df in all_results.items():

        if (
            df.empty
            or "last_candle" not in df.columns
        ):
            continue

        most_recent = df["last_candle"].max()

        age_min = (
            now - most_recent
        ).total_seconds() / 60

        flag = (
            " ⚠️ possible donnée figée"
            if age_min > 90
            else ""
        )

        lines.append(
            f"{name}: dernière bougie "
            f"{most_recent.strftime('%Y-%m-%d %H:%M UTC')} "
            f"(il y a {age_min:.0f} min)"
            f"{flag}"
        )

    return lines


# =========================================================
# FORMATAGE DU MESSAGE
# =========================================================

def format_message(
    all_results,
    stock_provider,
    top=5,
    threshold=60,
    now=None,
):

    now = now or datetime.now(timezone.utc)

    ts = now.strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    lines = [
        f"📊 Scan multi-actifs — "
        f"{ts} (seuil {threshold:.0f})",

        f"Source Actions/Matières premières "
        f"ce run : {stock_provider} | "
        f"Indices : yahoo "
        f"(fixe, indices non couverts par "
        f"le plan gratuit Twelve Data)",

        "",
    ]

    any_signal = False

    # =====================================================
    # 1. SIGNAUX AU-DESSUS DU SEUIL
    # =====================================================

    for name, df in all_results.items():

        if df.empty:
            continue

        signals = df[
            df["direction"].isin(
                ["LONG", "SHORT"]
            )
        ]

        if signals.empty:
            continue

        any_signal = True

        lines.append(
            f"--- {name} ---"
        )

        for _, row in signals.head(top).iterrows():

            best = max(
                row["score_long"],
                row["score_short"]
            )

            icon = (
                "🟢"
                if row["direction"] == "LONG"
                else "🔴"
            )

            relvol_display = (
                "n/d"
                if pd.isna(row["relvol"])
                else row["relvol"]
            )

            lines.append(
                f"{icon} {row['symbol']}: "
                f"{row['direction']} "
                f"(score {best:.0f}) | "
                f"close={row['close']} | "
                f"RSI={row['rsi']} | "
                f"relvol={relvol_display}"
            )

        lines.append("")

    # =====================================================
    # 2. AUCUN SIGNAL :
    #    DIAGNOSTIC DES MEILLEURS SCORES
    # =====================================================

    if not any_signal:

        lines.append(
            "Aucun signal au-dessus du seuil "
            "sur aucune catégorie."
        )

        lines.append("")

        lines.append(
            "--- Meilleurs scores sous le seuil ---"
        )

        for name, df in all_results.items():

            if df.empty:
                continue

            diagnostic = df.copy()

            # Score le plus élevé entre LONG et SHORT
            diagnostic["best_score"] = diagnostic[
                ["score_long", "score_short"]
            ].max(axis=1)

            # Direction correspondant au meilleur score
            diagnostic["best_direction"] = diagnostic.apply(
                lambda row:
                    "LONG"
                    if row["score_long"]
                    >= row["score_short"]
                    else "SHORT",
                axis=1,
            )

            # Classement des meilleurs actifs
            diagnostic = (
                diagnostic
                .sort_values(
                    "best_score",
                    ascending=False
                )
                .head(top)
            )

            lines.append(name)

            for _, row in diagnostic.iterrows():

                relvol_display = (
                    "n/d"
                    if pd.isna(row["relvol"])
                    else row["relvol"]
                )

                lines.append(
                    f"  {row['symbol']}: "
                    f"{row['best_score']:.0f} "
                    f"({row['best_direction']}) | "
                    f"tendance={row['trend_pts']:+.0f} | "
                    f"EMA={row['ema_pts']:+.0f} | "
                    f"RSI={row['rsi_pts']:+.0f} | "
                    f"volume={row['volume_pts']:+.0f} | "
                    f"breakout={row['breakout_pts']:+.0f} | "
                    f"relvol={relvol_display}"
                )

            lines.append("")

    # =====================================================
    # 3. FRAÎCHEUR DES DONNÉES
    # =====================================================

    fresh_lines = freshness_summary(
        all_results,
        now
    )

    if fresh_lines:

        lines.append(
            "--- Fraîcheur des données ---"
        )

        lines.extend(fresh_lines)

    return "\n".join(lines)


# =========================================================
# PROGRAMME PRINCIPAL
# =========================================================

if __name__ == "__main__":

    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--threshold",
        type=float,
        default=60,
    )

    ap.add_argument(
        "--limit",
        type=int,
        default=150,
        help=(
            "Nombre de bougies récupérées par appel "
            "(150 suffit largement pour "
            "EMA50/RSI14/relvol20 ; une valeur plus "
            "haute comme 500 consomme inutilement "
            "plus de crédits API Twelve Data et peut "
            "déclencher un 429 malgré la pause entre appels)."
        ),
    )

    ap.add_argument(
        "--top",
        type=int,
        default=5,
    )

    args = ap.parse_args()

    # Heure du début du scan
    now = datetime.now(timezone.utc)

    # Scan complet
    results, stock_provider = scan_all(
        threshold=args.threshold,
        limit=args.limit,
        now=now,
    )

    # Heure utilisée pour la fraîcheur
    now_for_freshness = datetime.now(
        timezone.utc
    )

    # Construction du message
    message = format_message(
        results,
        stock_provider,
        top=args.top,
        threshold=args.threshold,
        now=now_for_freshness,
    )

    # Affichage console
    print("\n" + message)

    # =====================================================
    # NOTIFICATIONS
    # =====================================================

    if not has_signal(results):

        print(
            "\nAucun signal détecté sur aucune "
            "catégorie — notifications non envoyées."
        )

    else:

        send_slack(
            os.getenv("SLACK_WEBHOOK_URL"),
            message,
        )

        send_email(
            smtp_host=os.getenv(
                "SMTP_HOST",
                "smtp.gmail.com",
            ),

            smtp_port=int(
                os.getenv("SMTP_PORT")
                or "465"
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
                f"Scan multi-actifs — "
                f"{now.strftime('%Y-%m-%d %H:%M')}"
            ),

            body=message,
        )
