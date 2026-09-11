"""
V4.3 — Scanner multi-actifs + audit Binance + notifications.

Le rapport distingue explicitement :
1. univers Binance réellement tradable SPOT/TRADING découvert en direct;
2. marchés techniquement exploitables pour le scan;
3. marchés écartés pour une raison de données/critère;
4. signaux techniques issus de scan.py.
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from assets import (
    FOREX, STOCKS, INDICES, COMMODITIES, COMMODITIES_YAHOO_ONLY,
    build_binance_universe, get_binance_universe_metadata,
    get_binance_universe_audit, get_asset_type, get_symbol_map,
)
from data_sources import DataRouter, DataSourceError
import scan
from notify import send_email, send_slack

PAUSE_BY_ASSET_TYPE = {"crypto": 0.05, "forex": 0.10, "stock": 0.10, "index": 0.10, "commodity": 0.10}
INTERVAL_BY_ASSET_TYPE = {"crypto": "5min", "forex": "15min", "stock": "15min", "index": "15min", "commodity": "15min"}


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return default if pd.isna(x) else x
    except (TypeError, ValueError):
        return default


def _format_price(v: Any) -> str:
    x = _safe_float(v)
    if x == 0: return "-"
    if abs(x) >= 1000: return f"{x:,.2f}"
    if abs(x) >= 1: return f"{x:.6f}"
    return f"{x:.10f}"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def router_fetcher(router: DataRouter, canonical_symbol: str, asset_type: str, symbol_map: dict[str, Any], limit: int) -> pd.DataFrame:
    interval = INTERVAL_BY_ASSET_TYPE.get(asset_type, "15min")
    require_volume = asset_type in {"crypto", "stock"}
    try:
        data = router.fetch(canonical_symbol, interval=interval, limit=limit, symbol_map=symbol_map, require_volume=require_volume, asset_type=asset_type)
    except TypeError:
        data = router.fetch(canonical_symbol, interval=interval, limit=limit, symbol_map=symbol_map, require_volume=require_volume)
    if data is None or not isinstance(data, pd.DataFrame) or data.empty:
        raise DataSourceError(f"[{canonical_symbol}] données insuffisantes.")
    return data


def scan_one_asset(router: DataRouter, canonical_symbol: str, threshold: float, limit: int) -> dict[str, Any]:
    asset_type = get_asset_type(canonical_symbol)
    result: dict[str, Any] = {"symbol": canonical_symbol, "asset": canonical_symbol, "asset_type": asset_type, "status": "error", "signal": None, "score": 0.0, "error": None}
    try:
        data = router_fetcher(router, canonical_symbol, asset_type, get_symbol_map(canonical_symbol), limit)
        provider = str(data.attrs.get("provider", ""))
        scan_result = scan.scan_asset(data, symbol=canonical_symbol, threshold=threshold, provider_name=provider)
        if isinstance(scan_result, dict): result.update(scan_result)
        elif scan_result is not None: result["result"] = scan_result
        result["score"] = _safe_float(result.get("score", result.get("total_score", 0)))
        result["signal"] = result.get("signal") or result.get("direction")
        result["provider"] = result.get("provider") or provider or "-"
        result["timestamp"] = result.get("timestamp") or result.get("last_timestamp")
        result["volume_status"] = result.get("volume_status") or data.attrs.get("volume_status")
        if result.get("status") in {None, "", "error"}: result["status"] = "ok"
        return result
    except DataSourceError as exc:
        result["status"] = "insufficient"; result["error"] = str(exc); return result
    except Exception as exc:
        result["status"] = "error"; result["error"] = str(exc); return result


def scan_all(threshold: float = 75.0, limit: int = 1000, top: int = 5) -> dict[str, Any]:
    router = DataRouter()
    crypto_universe = build_binance_universe()
    crypto_metadata = get_binance_universe_metadata()
    binance_audit = get_binance_universe_audit()

    all_assets: list[str] = []
    for symbol in crypto_universe + [*FOREX, *STOCKS, *INDICES, *COMMODITIES, *COMMODITIES_YAHOO_ONLY]:
        if symbol not in all_assets: all_assets.append(symbol)

    print("=" * 90)
    print("V4.3 — SCANNER MULTI-ACTIFS")
    print("=" * 90)
    print(f"Binance SPOT/TRADING recensé : {binance_audit['spot_trading_symbols']}")
    print(f"Binance techniquement exploitable : {binance_audit['analysis_eligible']}")
    print(f"Binance envoyé au scan : {binance_audit['selected_for_scan']}")
    print(f"Univers total scanné : {len(all_assets)}")
    print()

    results: list[dict[str, Any]] = []
    for i, symbol in enumerate(all_assets, 1):
        result = scan_one_asset(router, symbol, threshold, limit)
        results.append(result)
        print(f"[{i}/{len(all_assets)}] {symbol} | {result.get('status')} | score={result.get('score', 0):.1f}")
        pause = PAUSE_BY_ASSET_TYPE.get(get_asset_type(symbol), 0.05)
        if pause and i < len(all_assets): time.sleep(pause)

    strong = [r for r in results if r.get("status") in {"ok", "signal", "strong"} and r.get("signal") in {"LONG", "SHORT"} and _safe_float(r.get("score")) >= threshold]
    strong.sort(key=lambda x: _safe_float(x.get("score")), reverse=True)
    strong = strong[:max(1, top)]
    errors = [r for r in results if r.get("status") == "error"]
    insufficient = [r for r in results if r.get("status") == "insufficient"]
    analyzed = [r for r in results if r.get("status") in {"ok", "signal", "strong"}]

    stats = {"total": len(results), "analyzed": len(analyzed), "errors": len(errors), "insufficient": len(insufficient), "strong_signals": len(strong), "crypto_universe": len(crypto_universe), "crypto_top_symbols": crypto_universe[:10], "crypto_metadata": crypto_metadata, "binance_audit": binance_audit}
    return {"results": results, "strong_signals": strong, "errors": errors, "insufficient": insufficient, "router": router, "stats": stats}


def freshness_summary(results: list[dict[str, Any]]) -> str:
    now = _now_utc(); ages=[]; future=0; stale=0
    for item in results:
        ts = item.get("timestamp", item.get("last_timestamp"))
        if ts is None: continue
        try:
            t = pd.Timestamp(ts)
            t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
            age = (now - t.to_pydatetime()).total_seconds()
            if age < -120: future += 1; continue
            if age > 90*60: stale += 1
            ages.append(max(0, age))
        except Exception: pass
    if not ages and not future: return "Fraîcheur : aucune donnée temporelle exploitable."
    parts=[]
    if ages: parts += [f"médiane={sorted(ages)[len(ages)//2]:.0f}s", f"max={max(ages):.0f}s"]
    if stale: parts.append(f"anciennes={stale}")
    if future: parts.append(f"futures={future}")
    return "Fraîcheur : " + " | ".join(parts)


def router_summary(router: DataRouter) -> str:
    stats = getattr(router, "stats", None)
    if not isinstance(stats, dict): return "Statistiques routeur : non disponibles."
    parts=[]
    for provider in ("binance", "finnhub", "yahoo", "twelve_data"):
        s=stats.get(provider)
        if isinstance(s, dict):
            parts.append(f"{provider}: appels={s.get('calls',0)} succès={s.get('success',0)} échecs={s.get('failures',0)}")
    return " | ".join(parts) if parts else "Statistiques routeur : non disponibles."


def format_signal(item: dict[str, Any]) -> str:
    symbol=item.get("symbol", "?"); side=item.get("signal") or item.get("direction", "?")
    score=_safe_float(item.get("score", item.get("total_score",0)))
    price=item.get("price", item.get("entry", item.get("entry_price")))
    sl=item.get("stop_loss", item.get("sl")); tp1=item.get("take_profit_1", item.get("tp1")); tp2=item.get("take_profit_2", item.get("tp2"))
    rr=_safe_float(item.get("rr_tp2", item.get("risk_reward", item.get("rr"))))
    return "\n".join([f"🚨 {symbol} — {side}", f"Score : {score:.1f}/100", f"Entrée : {_format_price(price)}", f"Stop Loss : {_format_price(sl)}", f"TP1 : {_format_price(tp1)}", f"TP2 : {_format_price(tp2)}", f"RR TP2 : {rr:.2f}", f"Source : {item.get('provider','-')}"])


def _reason_label(reasons: list[str]) -> str:
    return ",".join(reasons) if reasons else "-"


def _binance_audit_text(audit: dict[str, Any], max_rows: int = 80) -> list[str]:
    rows=audit.get("rows", [])
    lines=["=== BINANCE : RECENSEMENT ET TRI ===", "", f"Symboles exchangeInfo : {audit.get('exchange_info_symbols',0)}", f"SPOT + TRADING : {audit.get('spot_trading_symbols',0)}", f"Ticker exploitable : {audit.get('ticker_available',0)}", f"Éligibles au scan : {audit.get('analysis_eligible',0)}", f"Envoyés au scan : {audit.get('selected_for_scan',0)}", "", "Aucun filtre économique n'est appliqué par défaut : volume, trades, spread, volatilité, âge et profondeur restent des variables d'audit.", ""]
    reason_counts: dict[str,int]={}
    for r in rows:
        for reason in r.get("selection_reasons",[]): reason_counts[reason]=reason_counts.get(reason,0)+1
    lines.append("Répartition des exclusions :")
    if reason_counts:
        for k,v in sorted(reason_counts.items(), key=lambda x:x[1], reverse=True): lines.append(f"- {k}: {v}")
    else: lines.append("- aucune exclusion économique")
    lines += ["", "Top marchés sélectionnés (liquidité) :", "Symbol | Quote 24h | Trades | Spread | Depth ±0,25% | Score"]
    selected=sorted([r for r in rows if r.get("selected_for_scan")], key=lambda x:x.get("liquidity_score",0), reverse=True)[:30]
    for r in selected:
        lines.append(f"{r['symbol']} | {r.get('quote_volume_24h',0):,.0f} | {r.get('trades_24h',0):,} | {r.get('spread_pct',0):.3f}% | {r.get('depth_025_pct',0):,.0f} | {r.get('liquidity_score',0):.1f}")
    rejected=[r for r in rows if not r.get("selected_for_scan")]
    if rejected:
        lines += ["", f"Marchés non envoyés au scan (max {max_rows}) :", "Symbol | Raisons | Risque de faux rejet | Score"]
        for r in rejected[:max_rows]: lines.append(f"{r['symbol']} | {_reason_label(r.get('selection_reasons',[]))} | {r.get('false_exclusion_risk','LOW')} | {r.get('liquidity_score',0):.1f}")
    return lines


def build_notification_message(report: dict[str, Any], threshold: float) -> str:
    stats=report["stats"]; results=report["results"]; strong=report["strong_signals"]
    lines=["V4.3 — RAPPORT DU SCANNER", "", f"Actifs scannés : {stats['total']} | analysés : {stats['analyzed']} | erreurs : {stats['errors']} | insuffisants : {stats['insufficient']}", f"Seuil signal : {threshold:.0f}/100 | signaux forts : {len(strong)}", "", freshness_summary(results), router_summary(report["router"]), ""]
    lines += _binance_audit_text(stats["binance_audit"])
    lines += ["", "=== SIGNAUX FORTS ===", ""]
    if strong:
        for i,item in enumerate(strong,1): lines += [f"#{i}", format_signal(item), ""]
    else: lines.append("Aucun signal fort détecté.")
    return "\n".join(lines)


def main() -> int:
    parser=argparse.ArgumentParser(description="V4.3 — Scanner multi-actifs")
    parser.add_argument("--threshold", type=float, default=75.0)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--top", type=int, default=5)
    args=parser.parse_args()
    threshold=max(0,min(100,args.threshold)); limit=max(120,args.limit); top=max(1,args.top)
    try:
        report=scan_all(threshold,limit,top)
    except Exception as exc:
        msg=f"V4.3 — ERREUR CRITIQUE\n\nLe scanner n'a pas pu terminer.\n\nErreur : {exc}"
        send_email("V4.3 — Erreur critique du scanner",msg)
        return 1
    message=build_notification_message(report,threshold)
    print(message)
    email_ok=send_email(f"V4.3 — {len(report['strong_signals'])} signal(s) fort(s)",message)
    slack_ok=False
    if report["strong_signals"]: slack_ok=send_slack(message)
    print(f"[Notification] Email={'OK' if email_ok else 'NON'} Slack={'OK' if slack_ok else 'NON'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
