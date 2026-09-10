"""
V4.2 — Scanner multi-actifs + routeur intelligent + notifications.

Le routeur choisit automatiquement entre Binance, Yahoo, Finnhub et Twelve Data.
Twelve Data est protégé par un budget journalier et n'est pas appelé inutilement.
"""

import argparse
import os
from datetime import datetime, timezone

import pandas as pd

from data_sources import DataRouter, VOLUME_CONFIRMED, VOLUME_UNAVAILABLE, VOLUME_INVALID
from scan import scan
from notify import send_slack, send_email
from assets import CRYPTO, ACTIONS, FOREX, INDICES, COMMODITIES, COMMODITIES_YAHOO_ONLY


PAUSE_BY_PROVIDER = {"binance": 0.3, "twelvedata": 1.2, "yahoo": 0.5, "finnhub": 1.1}


def resolve_symbols(entries, key=None):
    result = []
    for entry in entries:
        if isinstance(entry, dict):
            value = entry.get(key) if key else entry.get("yahoo") or entry.get("twelvedata") or entry.get("finnhub")
            if value: result.append(value)
        else: result.append(entry)
    return result


def twelvedata_window_active(now=None):
    now = now or datetime.now(timezone.utc)
    return 3 <= now.hour < 19


def asset_groups():
    return [
        ("🪙 Crypto", CRYPTO, "crypto", "5m", "binance", None),
        ("💵 Forex", FOREX, "forex", "15m", "twelvedata", "twelvedata"),
        ("📈 Actions", ACTIONS, "stock", "15m", "yahoo", "yahoo"),
        ("📊 Indices", INDICES, "index", "15m", "yahoo", "yahoo"),
        ("🛢️ Matières premières", COMMODITIES + COMMODITIES_YAHOO_ONLY, "commodity", "15m", "yahoo", "yahoo"),
    ]


def entry_map(entry, canonical):
    """Return provider-specific symbols for one asset."""
    if not isinstance(entry, dict):
        return {"binance": canonical, "yahoo": canonical, "finnhub": canonical, "twelvedata": canonical}
    return {
        "binance": entry.get("binance", canonical),
        "yahoo": entry.get("yahoo", canonical),
        "finnhub": entry.get("finnhub", canonical),
        "twelvedata": entry.get("twelvedata", canonical),
    }


def router_fetcher(router, asset_type, preferred, symbol_maps):
    def fetch(symbol, interval="15m", limit=1000, **kwargs):
        return router.fetch(
            symbol,
            interval=interval,
            limit=limit,
            asset_type=asset_type,
            preferred=preferred,
            require_volume=True,
            symbol_map=symbol_maps.get(symbol, {}),
        )
    fetch._router = True
    return fetch


def scan_all(threshold=75, limit=1000, top=5):
    router = DataRouter()
    all_results = {}
    coverage = {}

    for name, entries, asset_type, interval, preferred, _ in asset_groups():
        symbols = []
        symbol_maps = {}
        for entry in entries:
            if isinstance(entry, dict):
                canonical = entry.get("display") or entry.get("twelvedata") or entry.get("yahoo") or entry.get("finnhub")
            else:
                canonical = entry
            symbols.append(canonical)
            symbol_maps[canonical] = entry_map(entry, canonical)

        print(f"\n{name} — {len(symbols)} actifs")
        fetcher = router_fetcher(router, asset_type, preferred, symbol_maps)
        df = scan(
            symbols,
            fetcher,
            interval=interval,
            limit=limit,
            threshold=threshold,
            pause=PAUSE_BY_PROVIDER.get(preferred or "yahoo", 0.5),
            provider_name="router",
            asset_type=asset_type,
        )
        all_results[name] = df
        analyzed = len(df)
        requested = len(symbols)
        coverage[name] = {"requested": requested, "analyzed": analyzed, "failed": max(requested-analyzed, 0)}

    return all_results, router, coverage

def has_signal(all_results):
    return any(not df.empty and "status" in df.columns and (df["status"] == "SIGNAL FORT").any() for df in all_results.values())


def count_signals(all_results):
    return sum(int((df["status"] == "SIGNAL FORT").sum()) for df in all_results.values() if not df.empty and "status" in df.columns)


def coverage_summary(coverage):
    lines = ["🛡️ COUVERTURE DU SCAN"]; total_r=total_a=total_f=0
    for name, info in coverage.items():
        r=int(info["requested"]); a=int(info["analyzed"]); f=int(info["failed"]); total_r+=r; total_a+=a; total_f+=f
        status="🟢 OK" if r and a==r else ("🟠 PARTIEL" if a else ("🔴 ÉCHEC" if r else "—"))
        lines.append(f"{name}: {a}/{r} analysés — {status}")
    lines += ["", f"{'🟢 SCAN COMPLET' if total_f == 0 else '⚠️ SCAN PARTIEL'} : {total_a}/{total_r} actifs analysés"]
    return "\n".join(lines)


def number_text(value, decimals=4):
    return "n/d" if pd.isna(value) else f"{float(value):.{decimals}f}"


def relvol_text(value): return "n/d" if pd.isna(value) else f"{float(value):.2f}"
def rsi_text(value): return "n/d" if pd.isna(value) else f"{float(value):.1f}"


def format_signal_block(row):
    vol = f"confirmé (RelVol {relvol_text(row.get('relvol'))})" if bool(row.get("volume_available", False)) else "n/d — volume non disponible"
    return (f"{row['symbol']} | {row['direction']} | {row['status']} | Qualité {row.get('quality','D')} | Score {float(row['best_score']):.0f}/100\n"
            f"   Entrée : {number_text(row.get('entry'))}\n   SL : {number_text(row.get('stop_loss'))}\n"
            f"   TP1 : {number_text(row.get('take_profit_1'))} (R:R {number_text(row.get('rr_tp1'),2)})\n"
            f"   TP2 : {number_text(row.get('take_profit_2'))} (R:R {number_text(row.get('rr_tp2'),2)})\n"
            f"   ATR : {number_text(row.get('atr'))} | RSI : {rsi_text(row.get('rsi'))}\n"
            f"   Volume : {vol}\n   Source : {row.get('provider','n/d')} | Statut volume : {row.get('volume_status','n/d')}")


def format_watch_block(row, rank, threshold):
    missing=[]
    if row["best_score"] < threshold: missing.append(f"SCORE < {threshold:.0f}")
    if not bool(row.get("breakout_ok",False)): missing.append("BREAKOUT")
    if bool(row.get("volume_available",False)) and not bool(row.get("volume_ok",False)): missing.append("VOLUME")
    if pd.isna(row.get("rr_tp2")) or row.get("rr_tp2",0) < 1.5: missing.append("R:R")
    return (f"{rank}. {row['symbol']} | {row['best_score']:.0f}/100 | {row['direction']} | {row['status']} | Q:{row.get('quality','D')}\n"
            f"   Entry:{number_text(row.get('entry'))} | SL:{number_text(row.get('stop_loss'))} | TP2:{number_text(row.get('take_profit_2'))}\n"
            f"   RSI:{rsi_text(row.get('rsi'))} | RelVol:{relvol_text(row.get('relvol'))} | ATR:{number_text(row.get('atr'))}\n"
            f"   Source:{row.get('provider','n/d')} | Manque : {' + '.join(missing) if missing else 'aucune'}")


def freshness_summary(all_results, now=None):
    now = now or datetime.now(timezone.utc); lines=["🕐 FRAÎCHEUR DES DONNÉES"]
    for name, df in all_results.items():
        if df.empty or "last_candle" not in df.columns: lines.append(f"{name}: aucune donnée exploitable"); continue
        ts=pd.to_datetime(df["last_candle"],utc=True,errors="coerce"); most_recent=ts.max()
        if pd.isna(most_recent): continue
        age=(pd.Timestamp(now)-most_recent).total_seconds()/60
        flag="🟢 frais" if age<=90 else "🟡 marché probablement fermé" if (pd.Timestamp(now).weekday()>=5 or pd.Timestamp(now).hour<7 or pd.Timestamp(now).hour>=21) else "🔴 données anciennes"
        lines.append(f"{name}: {most_recent.strftime('%H:%M')} UTC ({age:.0f} min) {flag}")
    return "\n".join(lines)


def format_full_message(all_results, router, threshold=75, top=5, now=None, coverage=None):
    now=now or datetime.now(timezone.utc); strong_count=count_signals(all_results)
    lines=["📊 SCAN MULTI-ACTIFS V4.2", now.strftime("%Y-%m-%d %H:%M UTC"), f"Seuil : {threshold:.0f}/100", ""]
    if coverage: lines += [coverage_summary(coverage), ""]
    lines += ["🔥 SIGNAL FORT", "Conditions : SCORE ≥ seuil + BREAKOUT + VOLUME si disponible + R:R ≥ 1.5", f"Total SIGNAL FORT : {strong_count}", "", "--- ROUTAGE DES SOURCES ---"]
    for provider, st in router.stats.items(): lines.append(f"{provider}: {st['success']} succès / {st['fail']} échecs / {st['attempts']} appels")
    lines.append(f"Twelve Data budget : {router.td_used}/{router.td_budget} appels estimés")
    lines.append("")
    if strong_count == 0: lines += ["Aucun signal fort sur ce scan.", ""]
    else:
        for name, df in all_results.items():
            if df.empty: continue
            strong=df[df["status"]=="SIGNAL FORT"]
            if strong.empty: continue
            lines.append(f"--- {name} ---")
            for _, row in strong.iterrows(): lines += [format_signal_block(row), ""]
    lines += ["👀 À SURVEILLER", f"Top {top} par catégorie.", ""]
    for name, df in all_results.items():
        lines.append(f"--- {name} ---")
        if df.empty: lines += ["Aucune donnée exploitable.", ""]; continue
        for rank, (_, row) in enumerate(df.sort_values("best_score", ascending=False).head(top).iterrows(),1): lines += [format_watch_block(row,rank,threshold), ""]
    lines += [freshness_summary(all_results,now)]
    return "\n".join(lines)


def main():
    parser=argparse.ArgumentParser(description="Scanner multi-actifs V4.2")
    parser.add_argument("--threshold",type=float,default=75); parser.add_argument("--limit",type=int,default=1000); parser.add_argument("--top",type=int,default=5)
    args=parser.parse_args(); now=datetime.now(timezone.utc)
    print(f"\nScan multi-actifs — {now.strftime('%Y-%m-%d %H:%M')} UTC (seuil {args.threshold:.0f}/100)")
    all_results, router, coverage=scan_all(args.threshold,args.limit,args.top)
    message=format_full_message(all_results,router,args.threshold,args.top,now,coverage)
    print("\n"+message)
    signal_count=count_signals(all_results)
    send_email(os.getenv("SMTP_HOST","smtp.gmail.com"),os.getenv("SMTP_PORT","465"),os.getenv("EMAIL_SENDER"),os.getenv("EMAIL_PASSWORD"),os.getenv("EMAIL_RECIPIENT"),f"Scan V4.2 — {signal_count} signal(s) fort(s) — {now.strftime('%Y-%m-%d %H:%M')} UTC",message)
    if has_signal(all_results) and os.getenv("SLACK_WEBHOOK_URL"): send_slack(os.getenv("SLACK_WEBHOOK_URL"),message)
    elif not has_signal(all_results): print("[Slack] Aucun SIGNAL FORT — Slack non envoyé.")

if __name__ == "__main__": main()
