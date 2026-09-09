"""
Lance le scan multi-actifs et envoie les résultats sur Slack + Email.

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

Répartition des sources de données :
- Crypto            : Binance
- Forex             : Twelve Data
- Actions            : Twelve Data pendant la fenêtre active,
                        Yahoo Finance sinon
- Indices             : Yahoo Finance
- Matières premières  : Twelve Data/Yahoo selon disponibilité
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
    COMMODITIES_YAHOO_ONLY
)


PAUSE_BY_PROVIDER = {
    "binance": 0.3,
    "twelvedata": 9.0,
    "yahoo": 0.5,
}


def twelvedata_window_active(now=None):

    now=now or datetime.now(timezone.utc)

    return 3 <= now.hour < 19


def build_categories(now=None):

    use_twelvedata=twelvedata_window_active(now)

    stock_provider=(
        "twelvedata"
        if use_twelvedata
        else
        "yahoo"
    )

    stock_fetcher=(
        klines_twelvedata
        if use_twelvedata
        else
        klines_yahoo
    )

    stock_pause=PAUSE_BY_PROVIDER[
        stock_provider
    ]


    def resolve_symbols(entries,key):

        return [
            e[key]
            for e in entries
        ]


    categories={

        "🪙 Crypto":[
            {
                "symbols":CRYPTO,
                "fetcher":klines_binance,
                "interval":"5m",
                "pause":PAUSE_BY_PROVIDER["binance"],
            }
        ],

        "💵 Forex":[
            {
                "symbols":resolve_symbols(
                    FOREX,
                    "twelvedata"
                ),
                "fetcher":klines_twelvedata,
                "interval":"15m",
                "pause":PAUSE_BY_PROVIDER["twelvedata"],
            }
        ],

        "📈 Actions":[
            {
                "symbols":resolve_symbols(
                    ACTIONS,
                    stock_provider
                ),
                "fetcher":stock_fetcher,
                "interval":"15m",
                "pause":stock_pause,
            }
        ],

        "📊 Indices":[
            {
                "symbols":resolve_symbols(
                    INDICES,
                    "yahoo"
                ),
                "fetcher":klines_yahoo,
                "interval":"15m",
                "pause":PAUSE_BY_PROVIDER["yahoo"],
            }
        ],

        "🛢️ Matières premières":[

            {
                "symbols":resolve_symbols(
                    COMMODITIES,
                    stock_provider
                ),
                "fetcher":stock_fetcher,
                "interval":"15m",
                "pause":stock_pause,
            },

            {
                "symbols":resolve_symbols(
                    COMMODITIES_YAHOO_ONLY,
                    "yahoo"
                ),
                "fetcher":klines_yahoo,
                "interval":"15m",
                "pause":PAUSE_BY_PROVIDER["yahoo"],
            }

        ],
    }

    return categories,stock_provider


def scan_all(
    threshold=75,
    limit=1000,
    now=None
):

    categories,stock_provider=build_categories(now)

    results={}

    for name,groups in categories.items():

        dfs=[]

        for cfg in groups:

            print(
                f"--- {name} "
                f"({cfg['fetcher'].__name__}, "
                f"pause={cfg['pause']}s) ---"
            )

            df=scan(
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

            merged=pd.concat(
                dfs,
                ignore_index=True
            )

            merged["max_score"]=merged[
                ["score_long","score_short"]
            ].max(axis=1)

            merged=(
                merged
                .sort_values(
                    "max_score",
                    ascending=False
                )
                .drop(columns="max_score")
                .reset_index(drop=True)
            )

            results[name]=merged

        else:

            results[name]=pd.DataFrame()


    return results,stock_provider


def has_signal(all_results):

    for df in all_results.values():

        if (
            not df.empty
            and "status" in df.columns
            and (df["status"]=="SIGNAL FORT").any()
        ):
            return True

    return False


def freshness_summary(
    all_results,
    now=None
):

    now=now or datetime.now(timezone.utc)

    lines=[]

    for name,df in all_results.items():

        if (
            df.empty
            or "last_candle" not in df.columns
        ):
            continue

        most_recent=df[
            "last_candle"
        ].max()

        age_min=(
            now-most_recent
        ).total_seconds()/60

        flag=(
            " ⚠️ possible donnée figée"
            if age_min>90
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


def format_score_block(row):

    direction=row["direction"]

    if direction=="LONG":

        trend_value=row["trend_pts"]
        ema_value=row["ema_pts"]
        rsi_value=row["rsi_pts"]
        volume_value=row["volume_pts"]
        breakout_value=row["breakout_pts"]

    else:

        trend_value=row["trend_pts"]
        ema_value=row["ema_pts"]
        rsi_value=row["rsi_pts"]
        volume_value=row["volume_pts"]
        breakout_value=row["breakout_pts"]


    best=max(
        row["score_long"],
        row["score_short"]
    )

    trend_text=f"{trend_value:+.0f}"
    ema_text=f"{ema_value:+.0f}"
    rsi_text=f"{rsi_value:+.0f}"
    volume_text=f"{volume_value:+.0f}"
    breakout_text=f"{breakout_value:+.0f}"


    lines=[
        f"{row['symbol']} {direction} — SCORE {best:.0f}/100",
        "",
        f"Tendance 1H       {trend_text}",
        f"EMA               {ema_text}",
        f"RSI               {rsi_text}",
        f"Volume            {volume_text}",
        f"Breakout          {breakout_text}",
        "------------------------",
        f"TOTAL              {best:.0f}/100",
    ]


    if row["status"]=="SIGNAL FORT":

        lines.extend([
            "",
            "🔥 TRIGGER : BREAKOUT + VOLUME",
            "➡️ SIGNAL FORT POUR ENTRÉE",
        ])

    elif row["status"]=="ATTENTE":

        lines.extend([
            "",
            "⏳ TRIGGER INCOMPLET",
            "➡️ ATTENTE",
        ])


    return "\n".join(lines)


def format_message(
    all_results,
    stock_provider,
    top=5,
    threshold=75,
    now=None
):

    now=now or datetime.now(timezone.utc)

    ts=now.strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    lines=[
        f"📊 Scan multi-actifs — "
        f"{ts} (seuil {threshold:.0f}/100)",

        f"Source Actions/Matières premières "
        f"ce run : {stock_provider} | "
        f"Indices : yahoo",

        ""
    ]

    any_signal=False


    # ---------------------------------------------------------
    # SIGNALS FORTS
    # ---------------------------------------------------------

    for name,df in all_results.items():

        if df.empty:
            continue

        strong=df[
            df["status"]=="SIGNAL FORT"
        ]

        if strong.empty:
            continue

        any_signal=True

        lines.append(
            f"--- {name} ---"
        )

        for _,row in strong.head(top).iterrows():

            lines.append(
                format_score_block(row)
            )

            lines.append("")


    # ---------------------------------------------------------
    # DIAGNOSTIC SI AUCUN SIGNAL FORT
    # ---------------------------------------------------------

    if not any_signal:

        lines.append(
            "Aucun SIGNAL FORT au-dessus du seuil."
        )

        lines.append("")

        lines.append(
            "--- Meilleurs scores ---"
        )


        for name,df in all_results.items():

            if df.empty:
                continue


            diagnostic=df.copy()

            diagnostic["best_score"]=diagnostic[
                ["score_long","score_short"]
            ].max(axis=1)


            diagnostic["best_direction"]=diagnostic.apply(

                lambda row:
                    "LONG"
                    if row["score_long"]>=row["score_short"]
                    else
                    "SHORT",

                axis=1
            )


            diagnostic=(
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


            for _,row in diagnostic.iterrows():

                direction=row[
                    "best_direction"
                ]

                score_value=row[
                    "best_score"
                ]

                status=row.get(
                    "status",
                    "-"
                )


                if score_value>=threshold:

                    status_text=status

                else:

                    status_text="SOUS SEUIL"


                lines.append(

                    f"  {row['symbol']}: "
                    f"{score_value:.0f}/100 "
                    f"({direction}) — "
                    f"{status_text}"

                )


            lines.append("")


    # ---------------------------------------------------------
    # FRAÎCHEUR
    # ---------------------------------------------------------

    fresh_lines=freshness_summary(
        all_results,
        now
    )

    if fresh_lines:

        lines.append(
            "--- Fraîcheur des données ---"
        )

        lines.extend(
            fresh_lines
        )


    return "\n".join(lines)


if __name__=="__main__":

    ap=argparse.ArgumentParser()

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
            "Nombre de bougies récupérées "
            "par appel."
        )
    )

    ap.add_argument(
        "--top",
        type=int,
        default=5
    )

    args=ap.parse_args()


    now=datetime.now(
        timezone.utc
    )


    results,stock_provider=scan_all(
        threshold=args.threshold,
        limit=args.limit,
        now=now
    )


    now_for_freshness=datetime.now(
        timezone.utc
    )


    message=format_message(
        results,
        stock_provider,
        top=args.top,
        threshold=args.threshold,
        now=now_for_freshness
    )


    print(
        "\n"+message
    )


    if not has_signal(results):

        print(
            "\nAucun SIGNAL FORT détecté "
            "— notifications non envoyées."
        )

    else:

        send_slack(
            os.getenv(
                "SLACK_WEBHOOK_URL"
            ),
            message
        )

        send_email(
            smtp_host=os.getenv(
                "SMTP_HOST",
                "smtp.gmail.com"
            ),

            smtp_port=int(
                os.getenv("SMTP_PORT") or "465"
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

            body=message
        )
