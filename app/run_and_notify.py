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
- SOUS SEUIL  = score < seuil

Notification :
- Email : envoyé à CHAQUE scan
- Slack : envoyé uniquement lorsqu'il existe au moins
          un SIGNAL FORT

Rapport Email :
1. PRISE DE POSITION IMMÉDIATE
   Tous les actifs remplissant les conditions.

2. À SURVEILLER
   Top 5 par catégorie parmi les actifs qui ne sont
   pas déjà des SIGNAL FORT, même si leur score est
   inférieur au seuil.

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
from data_sources import (
    klines_yahoo,
    klines_twelvedata
)
from scan import scan
from notify import (
    send_slack,
    send_email
)

from assets import (
    CRYPTO,
    ACTIONS,
    FOREX,
    INDICES,
    COMMODITIES,
    COMMODITIES_YAHOO_ONLY
)


PAUSE_BY_PROVIDER={

    "binance":0.3,

    "twelvedata":9.0,

    "yahoo":0.5,

}


def twelvedata_window_active(
    now=None
):

    now=(
        now
        or datetime.now(timezone.utc)
    )

    return (
        3 <= now.hour < 19
    )


def build_categories(
    now=None
):

    use_twelvedata=(
        twelvedata_window_active(now)
    )


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


    stock_pause=(
        PAUSE_BY_PROVIDER[
            stock_provider
        ]
    )


    def resolve_symbols(
        entries,
        key
    ):

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

                "pause":
                PAUSE_BY_PROVIDER[
                    "binance"
                ],

            }

        ],


        "💵 Forex":[

            {

                "symbols":
                resolve_symbols(
                    FOREX,
                    "twelvedata"
                ),

                "fetcher":
                klines_twelvedata,

                "interval":"15m",

                "pause":
                PAUSE_BY_PROVIDER[
                    "twelvedata"
                ],

            }

        ],


        "📈 Actions":[

            {

                "symbols":
                resolve_symbols(
                    ACTIONS,
                    stock_provider
                ),

                "fetcher":
                stock_fetcher,

                "interval":"15m",

                "pause":
                stock_pause,

            }

        ],


        "📊 Indices":[

            {

                "symbols":
                resolve_symbols(
                    INDICES,
                    "yahoo"
                ),

                "fetcher":
                klines_yahoo,

                "interval":"15m",

                "pause":
                PAUSE_BY_PROVIDER[
                    "yahoo"
                ],

            }

        ],


        "🛢️ Matières premières":[

            {

                "symbols":
                resolve_symbols(
                    COMMODITIES,
                    stock_provider
                ),

                "fetcher":
                stock_fetcher,

                "interval":"15m",

                "pause":
                stock_pause,

            },


            {

                "symbols":
                resolve_symbols(
                    COMMODITIES_YAHOO_ONLY,
                    "yahoo"
                ),

                "fetcher":
                klines_yahoo,

                "interval":"15m",

                "pause":
                PAUSE_BY_PROVIDER[
                    "yahoo"
                ],

            }

        ],

    }


    return (
        categories,
        stock_provider
    )


def scan_all(
    threshold=75,
    limit=1000,
    now=None
):

    categories,stock_provider=(
        build_categories(now)
    )


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
                [
                    "score_long",
                    "score_short"
                ]
            ].max(axis=1)


            merged=(

                merged

                .sort_values(
                    "max_score",
                    ascending=False
                )

                .drop(
                    columns="max_score"
                )

                .reset_index(
                    drop=True
                )

            )


            results[name]=merged


        else:

            results[name]=pd.DataFrame()


    return (
        results,
        stock_provider
    )


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
                df["status"]=="SIGNAL FORT"
            ).any()

        ):

            return True


    return False


def count_signals(
    all_results
):

    count=0


    for df in all_results.values():

        if (

            not df.empty

            and
            "status" in df.columns

        ):

            count+=int(
                (
                    df["status"]=="SIGNAL FORT"
                ).sum()
            )


    return count


def best_direction(
    row
):

    if row["score_long"]>row["score_short"]:

        return "LONG"

    if row["score_short"]>row["score_long"]:

        return "SHORT"

    return "-"


def missing_conditions(
    row,
    threshold=75
):

    direction=best_direction(row)

    best_score=max(
        row["score_long"],
        row["score_short"]
    )


    missing=[]


    # -----------------------------------------------------------------------
    # SCORE
    # -----------------------------------------------------------------------

    if best_score<threshold:

        missing.append(
            f"score < {threshold:.0f}"
        )


    # -----------------------------------------------------------------------
    # BREAKOUT
    # -----------------------------------------------------------------------

    if direction=="LONG":

        breakout_ok=(
            row["breakout_pts"]==20
        )

        volume_ok=(
            row["volume_pts"]==15
        )


    elif direction=="SHORT":

        breakout_ok=(
            row["breakout_pts"]==-20
        )

        volume_ok=(
            row["volume_pts"]==-15
        )


    else:

        breakout_ok=False
        volume_ok=False


    if not breakout_ok:

        missing.append(
            "breakout"
        )


    if not volume_ok:

        missing.append(
            "volume"
        )


    if not missing:

        return "aucune"


    return ", ".join(
        missing
    )


def format_score_block(
    row
):

    direction=row["direction"]


    best=max(
        row["score_long"],
        row["score_short"]
    )


    trend_text=(
        f"{row['trend_pts']:+.0f}"
    )

    ema_text=(
        f"{row['ema_pts']:+.0f}"
    )

    rsi_text=(
        f"{row['rsi_pts']:+.0f}"
    )

    volume_text=(
        f"{row['volume_pts']:+.0f}"
    )

    breakout_text=(
        f"{row['breakout_pts']:+.0f}"
    )


    lines=[

        f"{row['symbol']} "
        f"{direction} — "
        f"SCORE {best:.0f}/100",

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

            "🔥 TRIGGER : "
            "BREAKOUT + VOLUME",

            "➡️ SIGNAL FORT "
            "POUR ENTRÉE",

        ])


    elif row["status"]=="ATTENTE":

        lines.extend([

            "",

            "⏳ TRIGGER INCOMPLET",

            "➡️ ATTENTE",

        ])


    return "\n".join(lines)


def format_signal_message(
    all_results,
    stock_provider,
    threshold=75,
    now=None
):

    """
    Message court destiné à Slack.

    Slack ne reçoit que les véritables
    SIGNAL FORT.
    """

    now=(
        now
        or datetime.now(timezone.utc)
    )


    ts=now.strftime(
        "%Y-%m-%d %H:%M UTC"
    )


    lines=[

        f"🔥 SIGNALS FORTS — "
        f"{ts}",

        f"Seuil : {threshold:.0f}/100",

        f"Source Actions/Matières : "
        f"{stock_provider} | "
        f"Indices : yahoo",

        ""

    ]


    total=0


    for name,df in all_results.items():

        if df.empty:

            continue


        strong=df[
            df["status"]=="SIGNAL FORT"
        ]


        if strong.empty:

            continue


        lines.append(
            f"--- {name} ---"
        )


        for _,row in strong.iterrows():

            total+=1


            lines.extend([

                f"🔥 {row['symbol']} "
                f"{row['direction']} — "
                f"{max(row['score_long'],row['score_short']):.0f}/100",

                "➡️ ENTRÉE IMMÉDIATE",

                f"Breakout : "
                f"{row['breakout_pts']:+.0f}/20",

                f"Volume : "
                f"{row['volume_pts']:+.0f}/15",

                "",

            ])


    lines.append(
        f"Total SIGNAL FORT : {total}"
    )


    return "\n".join(lines)


def freshness_summary(
    all_results,
    now=None
):

    now=(
        now
        or datetime.now(timezone.utc)
    )


    lines=[]


    for name,df in all_results.items():

        if (

            df.empty

            or
            "last_candle" not in df.columns

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


def format_email_message(
    all_results,
    stock_provider,
    top=5,
    threshold=75,
    now=None
):

    """
    Rapport complet envoyé à CHAQUE scan.

    1. Prise de position immédiate :
       tous les SIGNAL FORT.

    2. À surveiller :
       top N par catégorie, en excluant
       les SIGNAL FORT déjà présentés.
    """

    now=(
        now
        or datetime.now(timezone.utc)
    )


    ts=now.strftime(
        "%Y-%m-%d %H:%M UTC"
    )


    total_signals=count_signals(
        all_results
    )


    lines=[

        "📊 SCAN MULTI-ACTIFS",

        f"{ts}",

        "",

        f"Seuil stratégique : "
        f"{threshold:.0f}/100",

        f"Source Actions/Matières : "
        f"{stock_provider}",

        "Indices : yahoo",

        "",

    ]


    # =======================================================================
    # 1. PRISE DE POSITION IMMÉDIATE
    # =======================================================================

    lines.extend([

        "🔥 PRISE DE POSITION IMMÉDIATE",

        "Conditions : "
        "score ≥ seuil + breakout + volume",

        "",

    ])


    if total_signals==0:

        lines.extend([

            "Aucun actif ne remplit "
            "actuellement toutes les conditions.",

            "",

        ])

    else:

        lines.append(

            f"{total_signals} actif(s) "
            "remplissent les conditions :"

        )

        lines.append("")


        for name,df in all_results.items():

            if df.empty:

                continue


            strong=df[
                df["status"]=="SIGNAL FORT"
            ]


            if strong.empty:

                continue


            lines.append(
                f"--- {name} ---"
            )

            lines.append("")


            # IMPORTANT :
            # on affiche TOUS les SIGNAL FORT,
            # pas seulement les 5 meilleurs.

            for _,row in strong.iterrows():

                lines.append(
                    format_score_block(row)
                )

                lines.append("")


    # =======================================================================
    # 2. À SURVEILLER
    # =======================================================================

    lines.extend([

        "👀 À SURVEILLER",

        f"Top {top} par catégorie "
        "hors SIGNAL FORT.",

        "Le classement inclut les actifs "
        "sous le seuil de 75/100.",

        "",

    ])


    for name,df in all_results.items():

        lines.append(
            f"--- {name} ---"
        )


        if df.empty:

            lines.extend([

                "Aucune donnée exploitable.",

                "",

            ])

            continue


        # Les SIGNAL FORT sont déjà présentés
        # dans la première partie.
        watch=df[
            df["status"]!="SIGNAL FORT"
        ].copy()


        if watch.empty:

            lines.extend([

                "Tous les actifs disponibles "
                "sont actuellement en SIGNAL FORT.",

                "",

            ])

            continue


        watch["best_score"]=watch[
            [
                "score_long",
                "score_short"
            ]
        ].max(axis=1)


        watch["best_direction"]=watch.apply(

            best_direction,

            axis=1

        )


        watch=(

            watch

            .sort_values(
                "best_score",
                ascending=False
            )

            .head(top)

        )


        for rank,(_,row) in enumerate(
            watch.iterrows(),
            start=1
        ):

            score_value=row[
                "best_score"
            ]

            direction=row[
                "best_direction"
            ]

            status=row[
                "status"
            ]


            if status=="ATTENTE":

                status_text=(
                    "⏳ ATTENTE"
                )

            else:

                status_text=(
                    "🟡 SOUS SEUIL"
                )


            missing=missing_conditions(
                row,
                threshold
            )


            lines.extend([

                f"{rank}. "
                f"{row['symbol']} — "
                f"{direction} — "
                f"{score_value:.0f}/100",

                f"   {status_text}",

                f"   Conditions manquantes : "
                f"{missing}",

            ])


        lines.append("")


    # =======================================================================
    # 3. FRAÎCHEUR DES DONNÉES
    # =======================================================================

    fresh_lines=freshness_summary(
        all_results,
        now
    )


    if fresh_lines:

        lines.extend([

            "--- Fraîcheur des données ---",

        ])


        lines.extend(
            fresh_lines
        )


        lines.append("")


    # =======================================================================
    # 4. RÉSUMÉ FINAL
    # =======================================================================

    lines.extend([

        "--- Résumé ---",

        f"🔥 Entrées immédiates : "
        f"{total_signals}",

        f"👀 Catégories surveillées : "
        f"{len(all_results)}",

        "",

        "Breakout renforcé : "
        "+0,10 % au-dessus du précédent "
        "plus haut / "
        "-0,10 % sous le précédent plus bas.",

    ])


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


    now_for_freshness=(
        datetime.now(
            timezone.utc
        )
    )


    # -----------------------------------------------------------------------
    # RAPPORT EMAIL COMPLET
    # -----------------------------------------------------------------------

    email_message=format_email_message(

        results,

        stock_provider,

        top=args.top,

        threshold=args.threshold,

        now=now_for_freshness

    )


    print(
        "\n"+email_message
    )


    # -----------------------------------------------------------------------
    # SLACK
    # -----------------------------------------------------------------------

    if has_signal(results):

        slack_message=format_signal_message(

            results,

            stock_provider,

            threshold=args.threshold,

            now=now_for_freshness

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


    # -----------------------------------------------------------------------
    # EMAIL — TOUJOURS ENVOYÉ
    # -----------------------------------------------------------------------

    signal_count=count_signals(
        results
    )


    if signal_count:

        subject=(

            f"🔥 {signal_count} SIGNAL FORT"
            f"{'S' if signal_count>1 else ''} "
            f"| 👀 À surveiller — "
            f"{now.strftime('%Y-%m-%d %H:%M')}"

        )

    else:

        subject=(

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
