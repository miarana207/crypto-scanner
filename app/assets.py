"""
V4.2 — Univers multi-actifs.

Univers :
- 20 cryptos
- 10 Forex
- 35 actions
- 10 indices
- 6 commodities multi-sources
- 5 commodities Yahoo-only

Total : 86 actifs

Les mappings fournisseurs sont explicites.

Principe important V4.2 :
si un fournisseur n'a pas de mapping pour un actif,
le DataRouter doit ignorer ce fournisseur au lieu
d'envoyer aveuglément le symbole canonique.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import csv
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


# ============================================================
# CRYPTO
# ============================================================

CRYPTO: List[str] = []

# Ensemble dynamique rempli par Binance Universe Builder.
_DYNAMIC_CRYPTO_SYMBOLS: set[str] = set()
_DYNAMIC_CRYPTO_METADATA: Dict[str, Dict[str, Any]] = {}


# ============================================================
# BINANCE UNIVERSE BUILDER
# ============================================================

# Binance recommande data-api.binance.vision pour les endpoints de
# données de marché publiques. Le workflow V4 utilise déjà cette URL via
# BINANCE_DATA_URL ; on la réutilise donc ici pour éviter de contourner
# l'endpoint public de market data.
BINANCE_UNIVERSE_URL = os.getenv(
    "BINANCE_UNIVERSE_URL",
    os.getenv(
        "BINANCE_DATA_URL",
        "https://data-api.binance.vision",
    ),
).rstrip("/")

# Fallbacks utilisés uniquement si l'endpoint principal échoue.
# Ils ne modifient aucun paramètre de stratégie.
BINANCE_UNIVERSE_FALLBACK_URLS = [
    x.strip().rstrip("/")
    for x in os.getenv(
        "BINANCE_UNIVERSE_FALLBACK_URLS",
        "https://api.binance.com,https://api-gcp.binance.com,https://api1.binance.com,https://api2.binance.com,https://api3.binance.com,https://api4.binance.com",
    ).split(",")
    if x.strip()
]

BINANCE_UNIVERSE_TIMEOUT = int(
    os.getenv("BINANCE_UNIVERSE_TIMEOUT", "20")
)

# Ces paramètres concernent UNIQUEMENT la construction dynamique
# de l'univers crypto. Ils ne modifient aucun paramètre du moteur
# technique ou des autres catégories d'actifs.
BINANCE_MIN_QUOTE_VOLUME_24H = float(
    os.getenv("BINANCE_MIN_QUOTE_VOLUME_24H", "5000000")
)
BINANCE_MAX_SPREAD_PCT = float(
    os.getenv("BINANCE_MAX_SPREAD_PCT", "0.30")
)
BINANCE_MIN_TRADES_24H = int(
    os.getenv("BINANCE_MIN_TRADES_24H", "500")
)
# 0 = aucun plafond. V4 explore alors tous les marchés admissibles
# après les filtres de marché. Un plafond explicite reste possible via
# la variable d'environnement si nécessaire pour un test.
BINANCE_MAX_UNIVERSE = int(
    os.getenv("BINANCE_MAX_UNIVERSE", "0")
)

# Analyse du carnet : elle intervient APRÈS les filtres bulk (volume,
# trades, spread, âge, volatilité), afin de ne pas appeler /depth sur
# les milliers de symboles qui ne sont pas pertinents.
BINANCE_DEPTH_LIMIT = int(
    os.getenv("BINANCE_DEPTH_LIMIT", "100")
)
BINANCE_DEPTH_WORKERS = int(
    os.getenv("BINANCE_DEPTH_WORKERS", "8")
)
BINANCE_MIN_DEPTH_025_NOTIONAL = float(
    os.getenv("BINANCE_MIN_DEPTH_025_NOTIONAL", "25000")
)
BINANCE_DEPTH_TARGET_010 = float(
    os.getenv("BINANCE_DEPTH_TARGET_010", "100000")
)
BINANCE_DEPTH_TARGET_025 = float(
    os.getenv("BINANCE_DEPTH_TARGET_025", "250000")
)
BINANCE_DEPTH_TARGET_050 = float(
    os.getenv("BINANCE_DEPTH_TARGET_050", "500000")
)
BINANCE_ALLOWED_QUOTE_ASSETS = {
    x.strip().upper()
    for x in os.getenv(
        "BINANCE_ALLOWED_QUOTE_ASSETS",
        "USDT,USDC,FDUSD",
    ).split(",")
    if x.strip()
}

# Actifs qui ne doivent pas être considérés comme des cryptomonnaies
# directionnelles. Ils peuvent être extrêmement liquides sur Binance,
# mais leur sélection fausserait le scanner : stablecoins, devises fiat
# et certains actifs synthétiques/tokenisés.
BINANCE_STABLECOIN_BASE_ASSETS = {
    x.strip().upper()
    for x in os.getenv(
        "BINANCE_STABLECOIN_BASE_ASSETS",
        "USDT,USDC,FDUSD,USD1,RLUSD,EURI,EURC,EURT,AEUR,USDE,DAI,TUSD,USDP,PYUSD,GUSD,FRAX,LUSD,SUSD,CRVUSD,USDD,USTC,BUSD",
    ).replace("\n", "").split(",")
    if x.strip()
}

BINANCE_EXCLUDED_BASE_ASSETS = {
    x.strip().upper()
    for x in os.getenv(
        "BINANCE_EXCLUDED_BASE_ASSETS",
        "USDT,USDC,FDUSD,USD1,RLUSD,EUR,GBP,TRY,BRL,UAH,PLN,ZAR,ARS,MXN,NGN,RON,JPY,AUD",
    ).replace("\n", "").split(",")
    if x.strip()
}

# Préférence de cotation pour éviter de scanner plusieurs fois le même
# actif sous USDT/USDC/FDUSD. USDT est le marché canonique par défaut.
BINANCE_QUOTE_PRIORITY = [
    x.strip().upper()
    for x in os.getenv(
        "BINANCE_QUOTE_PRIORITY",
        "USDT,USDC,FDUSD",
    ).split(",")
    if x.strip()
]

# Binance peut lister sur Spot des produits qui ne sont pas des
# cryptomonnaies natives : bStocks/tokenized securities, or synthétiques.
# Cette liste est volontairement configurable et sert de filet de sécurité
# en plus des règles structurelles ci-dessous.
BINANCE_SPECIAL_BASE_ASSETS = {
    x.strip().upper()
    for x in os.getenv(
        "BINANCE_SPECIAL_BASE_ASSETS",
        "CRCLB,MSTRB,NVDAB,SNDKB,TSLAB,SPCXB,AXTIB,CRWVB,INTWB,KORUB,MUUB,MVLLB,ORCLB,QNTB,SNXXB,TQQQB,MUB,QQQB,XAUT,BFUSD",
    ).replace("\n", "").split(",")
    if x.strip()
}

# Les leveraged tokens Binance (ex. BTCUP/BTCDOWN) sont des produits
# dérivés/synthétiques et ne doivent pas être mélangés aux cryptos spot
# directionnelles du scanner.
BINANCE_LEVERAGED_TOKEN_SUFFIXES = (
    "UP",
    "DOWN",
    "BULL",
    "BEAR",
)

# Protection contre les marchés extrêmement erratiques ou fraîchement
# introduits. Ces seuils concernent UNIQUEMENT l'univers crypto dynamique.
# Ils n'altèrent aucun paramètre de backtest/scoring des autres actifs.
BINANCE_MAX_UNIVERSE_VOLATILITY_PCT = float(
    os.getenv("BINANCE_MAX_UNIVERSE_VOLATILITY_PCT", "40.0")
)
BINANCE_MIN_UNIVERSE_AGE_DAYS = float(
    os.getenv("BINANCE_MIN_UNIVERSE_AGE_DAYS", "3.0")
)


def _binance_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if not math.isfinite(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def _binance_age_days(onboard_date: Any) -> float:
    if onboard_date in (None, "", 0):
        return 3650.0
    try:
        age = (time.time() * 1000.0 - float(onboard_date)) / 86400000.0
        return max(0.0, age)
    except (TypeError, ValueError):
        return 3650.0


def _binance_is_excluded_product(
    symbol: str,
    base_asset: str,
) -> tuple[bool, str]:
    """Identifie les produits Spot qui ne doivent pas entrer dans CRYPTO."""
    base = str(base_asset or "").upper().strip()
    sym = str(symbol or "").upper().strip()

    if base in BINANCE_SPECIAL_BASE_ASSETS:
        return True, "special/tokenized"

    # Leveraged tokens : on teste le base asset, pas le symbole complet,
    # afin de ne pas confondre le suffixe USDT avec un produit spécial.
    if base.endswith(BINANCE_LEVERAGED_TOKEN_SUFFIXES):
        return True, "leveraged-token"

    return False, ""


def _binance_get_json(
    session: requests.Session,
    path: str,
) -> tuple[Any, str]:
    """
    Récupère un endpoint Binance public avec bascule automatique.

    L'ordre est :
        1. BINANCE_UNIVERSE_URL / BINANCE_DATA_URL
        2. endpoints publics Binance de secours

    On essaie chaque endpoint une seule fois par ressource afin de ne pas
    multiplier inutilement les appels API.
    """
    urls: List[str] = []
    for base in [BINANCE_UNIVERSE_URL, *BINANCE_UNIVERSE_FALLBACK_URLS]:
        base = str(base).strip().rstrip("/")
        if not base or base in urls:
            continue
        urls.append(base)

    errors: List[str] = []

    for base in urls:
        url = f"{base}{path}"
        try:
            response = session.get(
                url,
                timeout=BINANCE_UNIVERSE_TIMEOUT,
            )
            response.raise_for_status()
            return response.json(), base
        except requests.RequestException as exc:
            status = getattr(exc.response, "status_code", None)
            if status is not None:
                errors.append(f"{base} -> HTTP {status}")
            else:
                errors.append(f"{base} -> {exc}")
        except ValueError as exc:
            errors.append(f"{base} -> JSON invalide: {exc}")

    raise RuntimeError(
        f"Binance: aucun endpoint disponible pour {path}. "
        + " | ".join(errors)
    )


def _binance_depth_metrics(
    depth: Any,
    mid: float,
) -> Dict[str, float]:
    """Calcule la profondeur bid+ask autour du mid-market.

    Les bandes sont relatives au prix (±0,10 %, ±0,25 %, ±0,50 %),
    ce qui est plus pertinent qu'une distance fixe en dollars pour
    comparer BTC, ETH et des altcoins.
    """
    if not isinstance(depth, dict) or mid <= 0:
        return {}

    bids = depth.get("bids", [])
    asks = depth.get("asks", [])
    if not isinstance(bids, list) or not isinstance(asks, list):
        return {}

    bands = (0.001, 0.0025, 0.005)
    result: Dict[str, float] = {}

    for band in bands:
        bid_notional = 0.0
        ask_notional = 0.0
        bid_limit = mid * (1.0 - band)
        ask_limit = mid * (1.0 + band)

        for level in bids:
            if not isinstance(level, (list, tuple)) or len(level) < 2:
                continue
            price = _binance_float(level[0])
            qty = _binance_float(level[1])
            if price > 0 and qty > 0 and price >= bid_limit:
                bid_notional += price * qty

        for level in asks:
            if not isinstance(level, (list, tuple)) or len(level) < 2:
                continue
            price = _binance_float(level[0])
            qty = _binance_float(level[1])
            if price > 0 and qty > 0 and price <= ask_limit:
                ask_notional += price * qty

        key = f"{int(band * 10000):03d}"
        result[f"depth_bid_{key}_notional"] = bid_notional
        result[f"depth_ask_{key}_notional"] = ask_notional
        result[f"depth_{key}_notional"] = bid_notional + ask_notional

    return result


def _binance_depth_score(depth_metrics: Dict[str, float]) -> float:
    """Score 0..1 de profondeur, avec plafonnement logarithmique."""
    if not depth_metrics:
        return 0.0

    def norm(value: float, target: float) -> float:
        if value <= 0 or target <= 0:
            return 0.0
        # Logarithmique : 10x plus de profondeur n'est pas 10x meilleur.
        import math as _math
        return min(1.0, _math.log10(1.0 + value) / _math.log10(1.0 + target))

    s010 = norm(depth_metrics.get("depth_010_notional", 0.0), BINANCE_DEPTH_TARGET_010)
    s025 = norm(depth_metrics.get("depth_025_notional", 0.0), BINANCE_DEPTH_TARGET_025)
    s050 = norm(depth_metrics.get("depth_050_notional", 0.0), BINANCE_DEPTH_TARGET_050)
    return 0.30 * s010 + 0.35 * s025 + 0.35 * s050


def _binance_fetch_depth(
    symbol: str,
) -> tuple[str, Optional[Dict[str, float]], str]:
    """Récupère le carnet Spot d'un symbole avec les fallbacks Binance."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "V4.2.2-Binance-Depth/1.0",
        "Accept": "application/json",
    })

    urls: List[str] = []
    for base in [BINANCE_UNIVERSE_URL, *BINANCE_UNIVERSE_FALLBACK_URLS]:
        base = str(base).strip().rstrip("/")
        if base and base not in urls:
            urls.append(base)

    last_error = ""
    for base in urls:
        try:
            response = session.get(
                f"{base}/api/v3/depth",
                params={"symbol": symbol, "limit": BINANCE_DEPTH_LIMIT},
                timeout=BINANCE_UNIVERSE_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            return symbol, payload, base
        except (requests.RequestException, ValueError) as exc:
            last_error = str(exc)

    return symbol, None, last_error


def _binance_audit_row(
    symbol: str,
    info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Crée une ligne d'audit complète pour un symbole Binance."""
    info = info or {}
    return {
        "symbol": symbol,
        "base_asset": str(info.get("baseAsset", "") or "").upper(),
        "quote_asset": str(info.get("quoteAsset", "") or "").upper(),
        "exchange_status": str(info.get("status", "") or ""),
        "spot_allowed": "",
        "spot_basis": "",
        "first_rejection_stage": "",
        "first_rejection_reason": "",
        "quote_volume_24h": "",
        "trades_24h": "",
        "last_price": "",
        "bid_price": "",
        "ask_price": "",
        "spread_pct": "",
        "age_days": "",
        "volatility_pct": "",
        "depth_010_notional": "",
        "depth_025_notional": "",
        "depth_050_notional": "",
        "depth_score": "",
        "universe_quality": "",
        "final_status": "",
        "selected": "",
        "selected_quote": "",
    }


def _binance_reject(
    row: Dict[str, Any],
    stage: str,
    reason: str,
) -> None:
    """Enregistre uniquement la première raison de rejet."""
    if not row.get("first_rejection_stage"):
        row["first_rejection_stage"] = stage
        row["first_rejection_reason"] = reason
        row["final_status"] = "rejected"


def _write_binance_audit(rows: List[Dict[str, Any]]) -> str:
    """Écrit l'audit exhaustif Binance dans un CSV exploitable par GitHub Actions."""
    path = os.getenv("BINANCE_AUDIT_PATH", "binance_universe_audit.csv")
    fieldnames = [
        "symbol", "base_asset", "quote_asset", "exchange_status",
        "spot_allowed", "spot_basis", "first_rejection_stage",
        "first_rejection_reason", "quote_volume_24h", "trades_24h",
        "last_price", "bid_price", "ask_price", "spread_pct", "age_days",
        "volatility_pct", "depth_010_notional", "depth_025_notional",
        "depth_050_notional", "depth_score", "universe_quality",
        "final_status", "selected", "selected_quote",
    ]
    try:
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"[Binance Audit] CSV écrit : {path} | lignes={len(rows)}")
    except Exception as exc:
        print(f"[Binance Audit] impossible d'écrire {path}: {exc}")
    return path


def _print_binance_cascade(
    counts: Dict[str, int],
    discovered: int,
) -> None:
    """Affiche une cascade de rejet lisible et séquentielle."""
    stages = [
        ("exchangeInfo", discovered, 0),
        ("TRADING", counts.get("non_trading", 0), counts.get("trading", 0)),
        ("SPOT", counts.get("not_spot", 0), counts.get("spot", 0)),
        ("quote autorisée", counts.get("quote_rejected", 0), counts.get("quote_ok", 0)),
        ("structure", counts.get("structural_rejected", 0), counts.get("candidate", 0)),
        ("ticker disponible", counts.get("ticker_missing", 0), counts.get("ticker_ok", 0)),
        ("volume >= seuil", counts.get("volume_rejected", 0), counts.get("volume_ok", 0)),
        ("trades >= seuil", counts.get("trades_rejected", 0), counts.get("trades_ok", 0)),
        ("prix valide", counts.get("price_rejected", 0), counts.get("price_ok", 0)),
        ("spread <= seuil", counts.get("spread_rejected", 0), counts.get("spread_ok", 0)),
        ("âge >= seuil", counts.get("age_rejected", 0), counts.get("age_ok", 0)),
        ("volatilité <= seuil", counts.get("volatility_rejected", 0), counts.get("volatility_ok", 0)),
        ("profondeur >= seuil", counts.get("depth_rejected", 0), counts.get("depth_ok", 0)),
        ("déduplication", counts.get("duplicate_rejected", 0), counts.get("unique_ok", 0)),
        ("plafond univers", counts.get("cap_rejected", 0), counts.get("selected", 0)),
    ]
    print("[Binance Audit] CASCADE DE SÉLECTION")
    print("  Étape                         Rejetés    Restants")
    print("  " + "-" * 53)
    for label, rejected, remaining in stages:
        print(f"  {label:<30} {rejected:>7} {remaining:>11}")


def build_binance_universe(
    max_symbols: Optional[int] = None,
) -> List[str]:
    """
    Construit l'univers Spot Binance à chaque exécution et audite chaque symbole.

    Le pipeline conserve les seuils V4.2.3 par défaut. Cette version ajoute :
    - audit exhaustif de /exchangeInfo ;
    - motif de première exclusion par symbole ;
    - détection élargie des stablecoins, dont EURI ;
    - audit CSV complet ;
    - déduplication qualité d'abord, préférence de quote en cas d'égalité ;
    - statistiques séquentielles de cascade.
    """
    global CRYPTO, _DYNAMIC_CRYPTO_SYMBOLS, _DYNAMIC_CRYPTO_METADATA

    configured_limit = BINANCE_MAX_UNIVERSE if max_symbols is None else int(max_symbols)
    limit = configured_limit if configured_limit > 0 else None
    session = requests.Session()
    session.headers.update({
        "User-Agent": "V4.2.4-Binance-Universe-Audit/1.0",
        "Accept": "application/json",
    })

    print("[Binance Universe] exchangeInfo...")
    exchange, exchange_base = _binance_get_json(session, "/api/v3/exchangeInfo")
    if not isinstance(exchange, dict):
        raise RuntimeError("Binance exchangeInfo: réponse JSON invalide")

    symbols = exchange.get("symbols", [])
    if not isinstance(symbols, list):
        raise RuntimeError("Binance exchangeInfo: format symbols invalide")

    discovered = 0
    rows: Dict[str, Dict[str, Any]] = {}
    candidates: Dict[str, Dict[str, Any]] = {}
    counts = {
        "non_trading": 0, "trading": 0, "not_spot": 0, "spot": 0,
        "quote_rejected": 0, "quote_ok": 0, "structural_rejected": 0,
        "candidate": 0, "ticker_missing": 0, "ticker_ok": 0,
        "volume_rejected": 0, "volume_ok": 0, "trades_rejected": 0,
        "trades_ok": 0, "price_rejected": 0, "price_ok": 0,
        "spread_rejected": 0, "spread_ok": 0, "age_rejected": 0,
        "age_ok": 0, "volatility_rejected": 0, "volatility_ok": 0,
        "depth_rejected": 0, "depth_ok": 0, "depth_failed": 0,
        "duplicate_rejected": 0, "unique_ok": 0, "cap_rejected": 0,
        "selected": 0,
    }

    for item in symbols:
        if not isinstance(item, dict):
            continue
        discovered += 1
        symbol = str(item.get("symbol", "")).upper().strip()
        if not symbol:
            continue
        row = _binance_audit_row(symbol, item)
        rows[symbol] = row

        status = str(item.get("status", "")).upper()
        if status != "TRADING":
            counts["non_trading"] += 1
            _binance_reject(row, "TRADING", f"status={status or 'UNKNOWN'}")
            continue
        counts["trading"] += 1

        spot_allowed = item.get("isSpotTradingAllowed")
        permissions = item.get("permissions", [])
        permission_ok = isinstance(permissions, list) and (
            "SPOT" in {str(x).upper() for x in permissions}
        )
        if spot_allowed is True:
            spot_ok = True
            spot_basis = "isSpotTradingAllowed=True"
        elif permission_ok:
            spot_ok = True
            spot_basis = "permissions=SPOT"
        elif spot_allowed is False:
            spot_ok = False
            spot_basis = "isSpotTradingAllowed=False"
        else:
            spot_ok = True
            spot_basis = "champ Spot absent; compatibilité V4"
        row["spot_allowed"] = str(spot_ok)
        row["spot_basis"] = spot_basis
        if not spot_ok:
            counts["not_spot"] += 1
            _binance_reject(row, "SPOT", spot_basis)
            continue
        counts["spot"] += 1

        quote_asset = str(item.get("quoteAsset", "")).upper()
        base_asset = str(item.get("baseAsset", "")).upper()
        row["quote_asset"] = quote_asset
        row["base_asset"] = base_asset

        if quote_asset not in BINANCE_ALLOWED_QUOTE_ASSETS:
            counts["quote_rejected"] += 1
            _binance_reject(row, "quote", f"quote={quote_asset}")
            continue
        counts["quote_ok"] += 1

        special, special_reason = _binance_is_excluded_product(symbol, base_asset)
        stable = base_asset in BINANCE_STABLECOIN_BASE_ASSETS
        excluded_base = base_asset in BINANCE_EXCLUDED_BASE_ASSETS
        if stable:
            counts["structural_rejected"] += 1
            _binance_reject(row, "structure", "stablecoin_or_fiat_base")
            continue
        if excluded_base:
            counts["structural_rejected"] += 1
            _binance_reject(row, "structure", "excluded_base_asset")
            continue
        if special:
            counts["structural_rejected"] += 1
            _binance_reject(row, "structure", special_reason)
            continue

        candidates[symbol] = item
        counts["candidate"] += 1

    print(
        f"[Binance Universe] endpoint={exchange_base} | connus={discovered} | "
        f"TRADING={counts['trading']} | non-TRADING={counts['non_trading']} | "
        f"SPOT={counts['spot']} | non-SPOT={counts['not_spot']} | "
        f"quotes OK={counts['quote_ok']} | quotes rejetées={counts['quote_rejected']} | "
        f"structure OK={counts['candidate']} | structure rejetée={counts['structural_rejected']}"
    )

    print("[Binance Universe] ticker 24h...")
    tickers, ticker_base = _binance_get_json(session, "/api/v3/ticker/24hr")
    if not isinstance(tickers, list):
        raise RuntimeError("Binance ticker/24hr: format invalide")
    ticker_by_symbol = {
        str(item.get("symbol", "")).upper(): item
        for item in tickers
        if isinstance(item, dict)
    }

    ranked: List[Dict[str, Any]] = []
    for symbol, info in candidates.items():
        row = rows[symbol]
        ticker = ticker_by_symbol.get(symbol)
        if not ticker:
            counts["ticker_missing"] += 1
            _binance_reject(row, "ticker", "ticker_24h_missing")
            continue
        counts["ticker_ok"] += 1

        quote_volume = _binance_float(ticker.get("quoteVolume"))
        count = int(_binance_float(ticker.get("count")))
        last_price = _binance_float(ticker.get("lastPrice"))
        bid = _binance_float(ticker.get("bidPrice"))
        ask = _binance_float(ticker.get("askPrice"))
        row.update({
            "quote_volume_24h": quote_volume,
            "trades_24h": count,
            "last_price": last_price,
            "bid_price": bid,
            "ask_price": ask,
        })

        if quote_volume < BINANCE_MIN_QUOTE_VOLUME_24H:
            counts["volume_rejected"] += 1
            _binance_reject(row, "volume", f"quote_volume_24h<{BINANCE_MIN_QUOTE_VOLUME_24H:g}")
            continue
        counts["volume_ok"] += 1

        if count < BINANCE_MIN_TRADES_24H:
            counts["trades_rejected"] += 1
            _binance_reject(row, "trades", f"trades_24h<{BINANCE_MIN_TRADES_24H}")
            continue
        counts["trades_ok"] += 1

        if last_price <= 0 or bid <= 0 or ask <= 0 or ask < bid:
            counts["price_rejected"] += 1
            _binance_reject(row, "prix", "invalid_bid_ask_or_last_price")
            continue
        counts["price_ok"] += 1

        mid = (bid + ask) / 2.0
        spread_pct = ((ask - bid) / mid) * 100.0 if mid > 0 else 999.0
        row["spread_pct"] = spread_pct
        if spread_pct > BINANCE_MAX_SPREAD_PCT:
            counts["spread_rejected"] += 1
            _binance_reject(row, "spread", f"spread_pct>{BINANCE_MAX_SPREAD_PCT:g}")
            continue
        counts["spread_ok"] += 1

        high = _binance_float(ticker.get("highPrice"), last_price)
        low = _binance_float(ticker.get("lowPrice"), last_price)
        volatility_pct = ((high - low) / last_price) * 100.0 if last_price > 0 else 0.0
        age_days = _binance_age_days(info.get("onboardDate"))
        row["age_days"] = age_days
        row["volatility_pct"] = volatility_pct

        if age_days < BINANCE_MIN_UNIVERSE_AGE_DAYS:
            counts["age_rejected"] += 1
            _binance_reject(row, "âge", f"age_days<{BINANCE_MIN_UNIVERSE_AGE_DAYS:g}")
            continue
        counts["age_ok"] += 1

        if volatility_pct > BINANCE_MAX_UNIVERSE_VOLATILITY_PCT:
            counts["volatility_rejected"] += 1
            _binance_reject(row, "volatilité", f"24h_range_pct>{BINANCE_MAX_UNIVERSE_VOLATILITY_PCT:g}")
            continue
        counts["volatility_ok"] += 1

        stability = min(1.0, age_days / 365.0)
        liquidity = min(1.0, quote_volume / max(BINANCE_MIN_QUOTE_VOLUME_24H * 20.0, 1.0))
        trade_liquidity = min(1.0, count / 100000.0)
        spread_quality = max(0.0, 1.0 - spread_pct / max(BINANCE_MAX_SPREAD_PCT, 0.0001))
        volatility_quality = max(0.0, 1.0 - volatility_pct / max(BINANCE_MAX_UNIVERSE_VOLATILITY_PCT, 0.0001))

        ranked.append({
            "symbol": symbol,
            "quote_asset": info.get("quoteAsset"),
            "base_asset": info.get("baseAsset"),
            "quote_volume_24h": quote_volume,
            "trades_24h": count,
            "bid": bid,
            "ask": ask,
            "spread_pct": spread_pct,
            "volatility_pct": volatility_pct,
            "stability": stability,
            "onboard_date": info.get("onboardDate"),
            "liquidity": liquidity,
            "trade_liquidity": trade_liquidity,
            "spread_quality": spread_quality,
            "volatility_quality": volatility_quality,
            "universe_quality": (
                0.30 * liquidity
                + 0.20 * trade_liquidity
                + 0.15 * spread_quality
                + 0.10 * stability
                + 0.25 * volatility_quality
            ),
            "status": info.get("status"),
        })

    print(
        "[Binance Universe] filtres ticker | "
        f"candidats={counts['candidate']} | ticker OK={counts['ticker_ok']} | "
        f"volume OK={counts['volume_ok']} | trades OK={counts['trades_ok']} | "
        f"prix OK={counts['price_ok']} | spread OK={counts['spread_ok']} | "
        f"âge OK={counts['age_ok']} | volatilité OK={counts['volatility_ok']} | "
        f"rejets: volume={counts['volume_rejected']}, trades={counts['trades_rejected']}, "
        f"prix={counts['price_rejected']}, spread={counts['spread_rejected']}, "
        f"âge={counts['age_rejected']}, volatilité={counts['volatility_rejected']}, "
        f"ticker_manquant={counts['ticker_missing']}"
    )

    ranked.sort(key=lambda x: (x["universe_quality"], x["liquidity"], x["quote_volume_24h"]), reverse=True)

    # ------------------------------------------------------------
    # PROFONDEUR DU CARNET
    # ------------------------------------------------------------
    if ranked:
        print(
            f"[Binance Universe] analyse profondeur carnet: {len(ranked)} paires | "
            f"workers={max(1, BINANCE_DEPTH_WORKERS)}"
        )
        workers = max(1, min(BINANCE_DEPTH_WORKERS, len(ranked)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_binance_fetch_depth, item["symbol"]): item for item in ranked}
            for future in as_completed(futures):
                item = futures[future]
                row = rows[item["symbol"]]
                try:
                    symbol, depth_payload, depth_source = future.result()
                except Exception as exc:
                    counts["depth_failed"] += 1
                    item["depth_score"] = 0.0
                    item["depth_error"] = str(exc)
                    _binance_reject(row, "profondeur", f"depth_error:{exc}")
                    continue

                mid = (float(item["bid"]) + float(item["ask"])) / 2.0
                metrics = _binance_depth_metrics(depth_payload, mid) if depth_payload else {}
                score = _binance_depth_score(metrics)
                item.update(metrics)
                item["depth_score"] = score
                item["depth_source"] = depth_source
                row["depth_010_notional"] = metrics.get("depth_010_notional", 0.0)
                row["depth_025_notional"] = metrics.get("depth_025_notional", 0.0)
                row["depth_050_notional"] = metrics.get("depth_050_notional", 0.0)
                row["depth_score"] = score

                depth025 = metrics.get("depth_025_notional", 0.0)
                if depth025 < BINANCE_MIN_DEPTH_025_NOTIONAL:
                    counts["depth_rejected"] += 1
                    item["depth_rejected"] = True
                    _binance_reject(row, "profondeur", f"depth_025<{BINANCE_MIN_DEPTH_025_NOTIONAL:g}")
                else:
                    counts["depth_ok"] += 1
                    item["depth_rejected"] = False

    ranked = [item for item in ranked if not item.get("depth_rejected", False) and not rows[item["symbol"]].get("first_rejection_stage")]

    for item in ranked:
        item["universe_quality"] = (
            0.35 * item.get("depth_score", 0.0)
            + 0.25 * item.get("liquidity", 0.0)
            + 0.20 * item.get("spread_quality", 0.0)
            + 0.10 * item.get("trade_liquidity", 0.0)
            + 0.10 * item.get("stability", 0.0)
        )
        rows[item["symbol"]]["universe_quality"] = item["universe_quality"]

    print(
        f"[Binance Universe] profondeur OK={counts['depth_ok']} | "
        f"rejetée={counts['depth_rejected']} | erreurs={counts['depth_failed']}"
    )

    # ------------------------------------------------------------
    # DÉDUPLICATION : qualité d'abord, quote ensuite
    # ------------------------------------------------------------
    quote_rank = {quote: i for i, quote in enumerate(BINANCE_QUOTE_PRIORITY)}
    best_by_base: Dict[str, Dict[str, Any]] = {}
    duplicate_losers: List[Dict[str, Any]] = []

    for item in ranked:
        base = str(item.get("base_asset", "")).upper()
        current = best_by_base.get(base)
        if current is None:
            best_by_base[base] = item
            continue

        current_key = (
            current["universe_quality"],
            current.get("depth_score", 0.0),
            current["liquidity"],
            current["quote_volume_24h"],
            -quote_rank.get(str(current.get("quote_asset", "")).upper(), 999),
        )
        new_key = (
            item["universe_quality"],
            item.get("depth_score", 0.0),
            item["liquidity"],
            item["quote_volume_24h"],
            -quote_rank.get(str(item.get("quote_asset", "")).upper(), 999),
        )
        if new_key > current_key:
            duplicate_losers.append(current)
            best_by_base[base] = item
        else:
            duplicate_losers.append(item)

    for loser in duplicate_losers:
        row = rows[loser["symbol"]]
        winner = best_by_base.get(str(loser.get("base_asset", "")).upper(), {})
        counts["duplicate_rejected"] += 1
        _binance_reject(
            row,
            "déduplication",
            f"duplicate_base;winner={winner.get('symbol', '')}",
        )

    ranked_unique = list(best_by_base.values())
    counts["unique_ok"] = len(ranked_unique)
    print(
        f"[Binance Universe] déduplication base_asset | avant={len(ranked)} | "
        f"après={len(ranked_unique)} | paires supprimées={counts['duplicate_rejected']}"
    )

    ranked_unique.sort(
        key=lambda x: (x["universe_quality"], x.get("depth_score", 0.0), x["liquidity"], x["quote_volume_24h"]),
        reverse=True,
    )

    if limit is None:
        selected = ranked_unique
        dropped_by_cap = []
    else:
        selected = ranked_unique[:limit]
        dropped_by_cap = ranked_unique[limit:]
        counts["cap_rejected"] = len(dropped_by_cap)
        for item in dropped_by_cap:
            _binance_reject(rows[item["symbol"]], "plafond univers", f"rank>{limit}")

    counts["selected"] = len(selected)
    for item in selected:
        row = rows[item["symbol"]]
        row["final_status"] = "selected"
        row["selected"] = "True"
        row["selected_quote"] = str(item.get("quote_asset", ""))
        row["universe_quality"] = item["universe_quality"]

    for symbol, row in rows.items():
        if not row.get("final_status"):
            row["final_status"] = "not_selected"

    audit_path = _write_binance_audit(list(rows.values()))
    _print_binance_cascade(counts, discovered)

    # Résumé des motifs de première exclusion.
    reason_counts: Dict[str, int] = {}
    for row in rows.values():
        reason = row.get("first_rejection_reason") or "PAS_REJETE"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    print("[Binance Audit] PREMIERS MOTIFS DE REJET")
    for reason, count in sorted(reason_counts.items(), key=lambda x: (-x[1], x[0]))[:20]:
        print(f"  {count:>5} | {reason}")

    selected_symbols = [item["symbol"] for item in selected]
    _DYNAMIC_CRYPTO_SYMBOLS = set(selected_symbols)
    _DYNAMIC_CRYPTO_METADATA = {item["symbol"]: item for item in selected}
    CRYPTO = list(selected_symbols)

    for symbol in selected_symbols:
        existing = CRYPTO_SYMBOLS.get(symbol)
        if existing is None:
            CRYPTO_SYMBOLS[symbol] = {"binance": symbol}
        else:
            existing["binance"] = symbol

    ASSET_GROUPS["crypto"] = CRYPTO

    print(
        f"[Binance Universe] ticker_endpoint={ticker_base} | sélection finale={len(selected_symbols)} "
        f"sur {len(ranked_unique)} actifs admissibles ({len(ranked)} paires) | audit={audit_path}"
    )

    for rank, item in enumerate(selected[:10], start=1):
        print(
            f"  #{rank:02d} {item['symbol']:<14} "
            f"vol24h={item['quote_volume_24h']:,.0f} "
            f"spread={item['spread_pct']:.3f}% "
            f"volatilité={item['volatility_pct']:.2f}% "
            f"±0.10=${item.get('depth_010_notional', 0.0):,.0f} "
            f"±0.25=${item.get('depth_025_notional', 0.0):,.0f} "
            f"±0.50=${item.get('depth_050_notional', 0.0):,.0f} "
            f"depth_score={item.get('depth_score', 0.0):.3f} "
            f"qualité={item['universe_quality']:.3f}"
        )

    return selected_symbols


def get_binance_universe_metadata() -> Dict[str, Dict[str, Any]]:
    return dict(_DYNAMIC_CRYPTO_METADATA)



# ============================================================
# FOREX
# ============================================================

FOREX = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "USD/CHF",
    "USD/CAD",
    "NZD/USD",
    "EUR/GBP",
    "EUR/JPY",
    "GBP/JPY",
]


# ============================================================
# ACTIONS
# ============================================================

STOCKS = [
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "NVDA",
    "META",
    "TSLA",
    "AVGO",
    "AMD",
    "QCOM",
    "ORCL",
    "ADBE",
    "CRM",
    "NFLX",
    "JPM",
    "BAC",
    "GS",
    "V",
    "MA",
    "UNH",
    "JNJ",
    "PFE",
    "XOM",
    "CVX",
    "WMT",
    "COST",
    "PG",
    "KO",
    "PEP",
    "HD",
    "DIS",
    "NKE",
    "IBM",
    "INTC",
    "CSCO",
]


# ============================================================
# INDICES
# ============================================================

INDICES = [
    "^GSPC",
    "^IXIC",
    "^DJI",
    "^RUT",
    "^FCHI",
    "^GDAXI",
    "^FTSE",
    "^N225",
    "^HSI",
    "^STOXX50E",
]


# ============================================================
# COMMODITIES MULTI-SOURCES
# ============================================================

COMMODITIES = [
    "GC=F",
    "SI=F",
    "CL=F",
    "BZ=F",
    "NG=F",
    "HG=F",
]


# ============================================================
# COMMODITIES YAHOO ONLY
# ============================================================

COMMODITIES_YAHOO_ONLY = [
    "ZC=F",
    "ZS=F",
    "ZW=F",
    "KC=F",
    "SB=F",
]


# ============================================================
# ALIAS HISTORIQUE
# ============================================================

ACTIONS = STOCKS

YAHOO_COMMODITIES = (
    COMMODITIES_YAHOO_ONLY
)


# ============================================================
# MAPPINGS CRYPTO
# ============================================================

CRYPTO_SYMBOLS: Dict[str, Dict[str, Optional[str]]] = {

    "BTCUSDT": {
        "binance": "BTCUSDT",
        "yahoo": "BTC-USD",
        "twelve_data": "BTC/USD",
    },

    "ETHUSDT": {
        "binance": "ETHUSDT",
        "yahoo": "ETH-USD",
        "twelve_data": "ETH/USD",
    },

    "SOLUSDT": {
        "binance": "SOLUSDT",
        "yahoo": "SOL-USD",
        "twelve_data": "SOL/USD",
    },

    "BNBUSDT": {
        "binance": "BNBUSDT",
        "yahoo": "BNB-USD",
        "twelve_data": "BNB/USD",
    },

    "XRPUSDT": {
        "binance": "XRPUSDT",
        "yahoo": "XRP-USD",
        "twelve_data": "XRP/USD",
    },

    "ADAUSDT": {
        "binance": "ADAUSDT",
        "yahoo": "ADA-USD",
        "twelve_data": "ADA/USD",
    },

    "DOGEUSDT": {
        "binance": "DOGEUSDT",
        "yahoo": "DOGE-USD",
        "twelve_data": "DOGE/USD",
    },

    "AVAXUSDT": {
        "binance": "AVAXUSDT",
        "yahoo": "AVAX-USD",
        "twelve_data": "AVAX/USD",
    },

    "LINKUSDT": {
        "binance": "LINKUSDT",
        "yahoo": "LINK-USD",
        "twelve_data": "LINK/USD",
    },

    "DOTUSDT": {
        "binance": "DOTUSDT",
        "yahoo": "DOT-USD",
        "twelve_data": "DOT/USD",
    },

    "TRXUSDT": {
        "binance": "TRXUSDT",
        "yahoo": "TRX-USD",
        "twelve_data": "TRX/USD",
    },

    "LTCUSDT": {
        "binance": "LTCUSDT",
        "yahoo": "LTC-USD",
        "twelve_data": "LTC/USD",
    },

    "BCHUSDT": {
        "binance": "BCHUSDT",
        "yahoo": "BCH-USD",
        "twelve_data": "BCH/USD",
    },

    "ATOMUSDT": {
        "binance": "ATOMUSDT",
        "yahoo": "ATOM-USD",
        "twelve_data": "ATOM/USD",
    },

    "UNIUSDT": {
        "binance": "UNIUSDT",
        "yahoo": "UNI-USD",
        "twelve_data": "UNI/USD",
    },

    "ETCUSDT": {
        "binance": "ETCUSDT",
        "yahoo": "ETC-USD",
        "twelve_data": "ETC/USD",
    },

    "XLMUSDT": {
        "binance": "XLMUSDT",
        "yahoo": "XLM-USD",
        "twelve_data": "XLM/USD",
    },

    "NEARUSDT": {
        "binance": "NEARUSDT",
        "yahoo": "NEAR-USD",
        "twelve_data": "NEAR/USD",
    },

    "APTUSDT": {
        "binance": "APTUSDT",
        "yahoo": "APT-USD",
        "twelve_data": "APT/USD",
    },

    "FILUSDT": {
        "binance": "FILUSDT",
        "yahoo": "FIL-USD",
        "twelve_data": "FIL/USD",
    },
}


# ============================================================
# MAPPINGS FOREX
# ============================================================

FOREX_SYMBOLS: Dict[str, Dict[str, Optional[str]]] = {

    "EUR/USD": {
        "twelve_data": "EUR/USD",
        "finnhub": "OANDA:EUR_USD",
        "yahoo": "EURUSD=X",
    },

    "GBP/USD": {
        "twelve_data": "GBP/USD",
        "finnhub": "OANDA:GBP_USD",
        "yahoo": "GBPUSD=X",
    },

    "USD/JPY": {
        "twelve_data": "USD/JPY",
        "finnhub": "OANDA:USD_JPY",
        "yahoo": "JPY=X",
    },

    "AUD/USD": {
        "twelve_data": "AUD/USD",
        "finnhub": "OANDA:AUD_USD",
        "yahoo": "AUDUSD=X",
    },

    "USD/CHF": {
        "twelve_data": "USD/CHF",
        "finnhub": "OANDA:USD_CHF",
        "yahoo": "CHF=X",
    },

    "USD/CAD": {
        "twelve_data": "USD/CAD",
        "finnhub": "OANDA:USD_CAD",
        "yahoo": "CAD=X",
    },

    "NZD/USD": {
        "twelve_data": "NZD/USD",
        "finnhub": "OANDA:NZD_USD",
        "yahoo": "NZDUSD=X",
    },

    "EUR/GBP": {
        "twelve_data": "EUR/GBP",
        "finnhub": "OANDA:EUR_GBP",
        "yahoo": "EURGBP=X",
    },

    "EUR/JPY": {
        "twelve_data": "EUR/JPY",
        "finnhub": "OANDA:EUR_JPY",
        "yahoo": "EURJPY=X",
    },

    "GBP/JPY": {
        "twelve_data": "GBP/JPY",
        "finnhub": "OANDA:GBP_JPY",
        "yahoo": "GBPJPY=X",
    },
}


# ============================================================
# MAPPINGS ACTIONS
# ============================================================

STOCK_SYMBOLS: Dict[
    str,
    Dict[str, Optional[str]],
] = {}

for _symbol in STOCKS:
    STOCK_SYMBOLS[_symbol] = {
        "finnhub": _symbol,
        "yahoo": _symbol,
        "twelve_data": _symbol,
    }


# ============================================================
# MAPPINGS INDICES
# ============================================================

INDEX_SYMBOLS: Dict[
    str,
    Dict[str, Optional[str]],
] = {

    "^GSPC": {
        "yahoo": "^GSPC",
        "finnhub": "SPX",
        "twelve_data": "SPX",
    },

    "^IXIC": {
        "yahoo": "^IXIC",
        "finnhub": "IXIC",
        "twelve_data": "IXIC",
    },

    "^DJI": {
        "yahoo": "^DJI",
        "finnhub": "DJI",
        "twelve_data": "DJI",
    },

    "^RUT": {
        "yahoo": "^RUT",
        "finnhub": "RUT",
        "twelve_data": "RUT",
    },

    "^FCHI": {
        "yahoo": "^FCHI",
        "finnhub": "CAC40",
        "twelve_data": "CAC",
    },

    "^GDAXI": {
        "yahoo": "^GDAXI",
        "finnhub": "DAX",
        "twelve_data": "DAX",
    },

    "^FTSE": {
        "yahoo": "^FTSE",
        "finnhub": "FTSE",
        "twelve_data": "FTSE",
    },

    "^N225": {
        "yahoo": "^N225",
        "finnhub": "N225",
        "twelve_data": "N225",
    },

    "^HSI": {
        "yahoo": "^HSI",
        "finnhub": "HSI",
        "twelve_data": "HSI",
    },

    "^STOXX50E": {
        "yahoo": "^STOXX50E",
        "finnhub": "STOXX50E",
        "twelve_data": "STOXX",
    },
}


# ============================================================
# MAPPINGS COMMODITIES
# ============================================================

COMMODITY_SYMBOLS: Dict[
    str,
    Dict[str, Optional[str]],
] = {

    "GC=F": {
        "yahoo": "GC=F",
        "twelve_data": "XAU/USD",
    },

    "SI=F": {
        "yahoo": "SI=F",
        "twelve_data": "XAG/USD",
    },

    "CL=F": {
        "yahoo": "CL=F",
        "twelve_data": "WTI/USD",
    },

    "BZ=F": {
        "yahoo": "BZ=F",
        "twelve_data": "BRENT/USD",
    },

    "NG=F": {
        "yahoo": "NG=F",
    },

    "HG=F": {
        "yahoo": "HG=F",
    },

    "ZC=F": {
        "yahoo": "ZC=F",
    },

    "ZS=F": {
        "yahoo": "ZS=F",
    },

    "ZW=F": {
        "yahoo": "ZW=F",
    },

    "KC=F": {
        "yahoo": "KC=F",
    },

    "SB=F": {
        "yahoo": "SB=F",
    },
}


# ============================================================
# UNIVERS COMPLET
# ============================================================

ASSET_GROUPS: Dict[str, List[str]] = {
    "crypto": CRYPTO,
    "forex": FOREX,
    "stock": STOCKS,
    "index": INDICES,
    "commodity": COMMODITIES,
    "commodity_yahoo": COMMODITIES_YAHOO_ONLY,
}


# ============================================================
# COMPATIBILITÉ ANCIENNES CLÉS
# ============================================================

ASSET_GROUPS["stocks"] = STOCKS
ASSET_GROUPS["indices"] = INDICES
ASSET_GROUPS["commodities"] = COMMODITIES
ASSET_GROUPS["commodities_yahoo"] = (
    COMMODITIES_YAHOO_ONLY
)


# ============================================================
# COMPTAGES
# ============================================================

ASSET_COUNTS = {
    "crypto": len(CRYPTO),
    "forex": len(FOREX),
    "stock": len(STOCKS),
    "index": len(INDICES),
    "commodity": len(COMMODITIES),
    "commodity_yahoo": len(
        COMMODITIES_YAHOO_ONLY
    ),
}


def all_assets() -> List[str]:
    """
    Retourne l'univers complet sans doublons.
    """

    result = []

    for group in [
        CRYPTO,
        FOREX,
        STOCKS,
        INDICES,
        COMMODITIES,
        COMMODITIES_YAHOO_ONLY,
    ]:
        for asset in group:
            if asset not in result:
                result.append(asset)

    return result


def asset_count() -> int:
    return len(
        all_assets()
    )


# ============================================================
# TYPE D'ACTIF
# ============================================================

def get_asset_type(
    symbol: str,
) -> str:

    if symbol in CRYPTO or symbol in _DYNAMIC_CRYPTO_SYMBOLS:
        return "crypto"

    if symbol in FOREX:
        return "forex"

    if symbol in STOCKS:
        return "stock"

    if symbol in INDICES:
        return "index"

    if symbol in COMMODITIES:
        return "commodity"

    if symbol in COMMODITIES_YAHOO_ONLY:
        return "commodity"

    raise ValueError(
        f"Actif inconnu : {symbol}"
    )


# ============================================================
# DISPLAY NAMES
# ============================================================

DISPLAY_NAMES: Dict[str, str] = {

    # Crypto
    "BTCUSDT": "Bitcoin",
    "ETHUSDT": "Ethereum",
    "SOLUSDT": "Solana",
    "BNBUSDT": "BNB",
    "XRPUSDT": "XRP",
    "ADAUSDT": "Cardano",
    "DOGEUSDT": "Dogecoin",
    "AVAXUSDT": "Avalanche",
    "LINKUSDT": "Chainlink",
    "DOTUSDT": "Polkadot",
    "TRXUSDT": "TRON",
    "LTCUSDT": "Litecoin",
    "BCHUSDT": "Bitcoin Cash",
    "ATOMUSDT": "Cosmos",
    "UNIUSDT": "Uniswap",
    "ETCUSDT": "Ethereum Classic",
    "XLMUSDT": "Stellar",
    "NEARUSDT": "NEAR Protocol",
    "APTUSDT": "Aptos",
    "FILUSDT": "Filecoin",

    # Forex
    "EUR/USD": "EUR/USD",
    "GBP/USD": "GBP/USD",
    "USD/JPY": "USD/JPY",
    "AUD/USD": "AUD/USD",
    "USD/CHF": "USD/CHF",
    "USD/CAD": "USD/CAD",
    "NZD/USD": "NZD/USD",
    "EUR/GBP": "EUR/GBP",
    "EUR/JPY": "EUR/JPY",
    "GBP/JPY": "GBP/JPY",

    # Actions
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "GOOGL": "Alphabet",
    "AMZN": "Amazon",
    "NVDA": "NVIDIA",
    "META": "Meta Platforms",
    "TSLA": "Tesla",
    "AVGO": "Broadcom",
    "AMD": "AMD",
    "QCOM": "Qualcomm",
    "ORCL": "Oracle",
    "ADBE": "Adobe",
    "CRM": "Salesforce",
    "NFLX": "Netflix",
    "JPM": "JPMorgan Chase",
    "BAC": "Bank of America",
    "GS": "Goldman Sachs",
    "V": "Visa",
    "MA": "Mastercard",
    "UNH": "UnitedHealth",
    "JNJ": "Johnson & Johnson",
    "PFE": "Pfizer",
    "XOM": "Exxon Mobil",
    "CVX": "Chevron",
    "WMT": "Walmart",
    "COST": "Costco",
    "PG": "Procter & Gamble",
    "KO": "Coca-Cola",
    "PEP": "PepsiCo",
    "HD": "Home Depot",
    "DIS": "Disney",
    "NKE": "Nike",
    "IBM": "IBM",
    "INTC": "Intel",
    "CSCO": "Cisco",

    # Indices
    "^GSPC": "S&P 500",
    "^IXIC": "NASDAQ Composite",
    "^DJI": "Dow Jones",
    "^RUT": "Russell 2000",
    "^FCHI": "CAC 40",
    "^GDAXI": "DAX",
    "^FTSE": "FTSE 100",
    "^N225": "Nikkei 225",
    "^HSI": "Hang Seng",
    "^STOXX50E": "Euro Stoxx 50",

    # Commodities
    "GC=F": "Gold",
    "SI=F": "Silver",
    "CL=F": "WTI Crude Oil",
    "BZ=F": "Brent Crude Oil",
    "NG=F": "Natural Gas",
    "HG=F": "Copper",
    "ZC=F": "Corn",
    "ZS=F": "Soybeans",
    "ZW=F": "Wheat",
    "KC=F": "Coffee",
    "SB=F": "Sugar",
}


def get_display_name(
    symbol: str,
) -> str:

    return DISPLAY_NAMES.get(
        symbol,
        symbol,
    )


# ============================================================
# SYMBOLE CANONIQUE
# ============================================================

def get_symbol(
    asset: str,
) -> str:

    return asset


# ============================================================
# SYMBOLE FOURNISSEUR
# ============================================================

def get_provider_symbol(
    asset: str,
    provider: str,
) -> Optional[str]:

    mapping = get_symbol_map(
        asset
    )

    return mapping.get(
        provider
    )


# ============================================================
# MAPPING FOURNISSEUR
# ============================================================

def get_symbol_map(
    asset: str,
) -> Dict[str, Optional[str]]:
    """
    Retourne le mapping fournisseur complet.

    Les clés absentes sont volontairement absentes :
    le DataRouter doit alors ignorer ce fournisseur.

    On ajoute aussi "symbol" pour compatibilité avec
    certaines parties du code V4/V4.1.
    """

    if asset in CRYPTO or asset in _DYNAMIC_CRYPTO_SYMBOLS:
        mapping = dict(
            CRYPTO_SYMBOLS.get(
                asset,
                {"binance": asset},
            )
        )

    elif asset in FOREX:
        mapping = dict(
            FOREX_SYMBOLS.get(
                asset,
                {},
            )
        )

    elif asset in STOCKS:
        mapping = dict(
            STOCK_SYMBOLS.get(
                asset,
                {},
            )
        )

    elif asset in INDICES:
        mapping = dict(
            INDEX_SYMBOLS.get(
                asset,
                {},
            )
        )

    elif (
        asset in COMMODITIES
        or asset in COMMODITIES_YAHOO_ONLY
    ):
        mapping = dict(
            COMMODITY_SYMBOLS.get(
                asset,
                {},
            )
        )

    else:
        raise ValueError(
            f"Actif inconnu : {asset}"
        )

    mapping["symbol"] = asset

    return mapping


# ============================================================
# VALIDATION
# ============================================================

def validate_assets() -> Dict[str, object]:
    """Vérifie uniquement les univers statiques au chargement.

    L'univers crypto est volontairement dynamique et sera validé par
    build_binance_universe() au début de chaque scan.
    """
    static_assets = []
    for group in [FOREX, STOCKS, INDICES, COMMODITIES, COMMODITIES_YAHOO_ONLY]:
        for asset in group:
            if asset not in static_assets:
                static_assets.append(asset)

    missing_maps = []
    for asset in static_assets:
        try:
            if not get_symbol_map(asset):
                missing_maps.append(asset)
        except Exception:
            missing_maps.append(asset)

    return {
        "static_total": len(static_assets),
        "missing_maps": missing_maps,
        "valid": not missing_maps,
    }


_ASSET_VALIDATION = validate_assets()

if not _ASSET_VALIDATION["valid"]:
    raise RuntimeError(
        "Univers statique invalide : "
        + str(_ASSET_VALIDATION)
    )

