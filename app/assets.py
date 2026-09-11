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

# Analyse du carnet : elle intervient APRÈS les filtres rapides mais AVANT
# le seuil de volume. Les actifs sous 5 M$ sont donc eux aussi audités.
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

# Audit en mémoire uniquement : aucun CSV n'est créé.
BINANCE_AUDIT_REJECTED_TOP = int(
    os.getenv("BINANCE_AUDIT_REJECTED_TOP", "50")
)
BINANCE_AUDIT_EXAMPLES_PER_REASON = int(
    os.getenv("BINANCE_AUDIT_EXAMPLES_PER_REASON", "5")
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


def _binance_volume_quality(volume: float) -> float:
    if volume <= 0:
        return 0.0
    return min(
        1.0,
        math.log10(1.0 + volume)
        / math.log10(1.0 + 100_000_000.0),
    )


def _binance_liquidity_score(
    depth_score: float,
    volume: float,
    spread_pct: float,
    trades: int,
    age_days: float,
) -> float:
    """Score composite 0..100 : profondeur 30 %, volume 25 %,
    spread 20 %, trades 15 %, stabilité/âge 10 %."""
    depth_q = max(0.0, min(1.0, depth_score))
    volume_q = _binance_volume_quality(volume)
    spread_q = max(
        0.0,
        min(1.0, 1.0 - spread_pct / max(BINANCE_MAX_SPREAD_PCT, 1e-9)),
    )
    trades_q = min(1.0, max(0.0, trades / 100000.0))
    stability_q = min(1.0, max(0.0, age_days / 365.0))
    return 100.0 * (
        0.30 * depth_q
        + 0.25 * volume_q
        + 0.20 * spread_q
        + 0.15 * trades_q
        + 0.10 * stability_q
    )


def _binance_liquidity_grade(
    score: Optional[float],
    sufficient: bool = True,
) -> str:
    if not sufficient or score is None:
        return "F"
    if score >= 90.0:
        return "A"
    if score >= 80.0:
        return "B"
    if score >= 70.0:
        return "C"
    if score >= 60.0:
        return "D"
    return "E"


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
    """Fiche d'audit complète conservée uniquement en mémoire."""
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
        "quote_volume_24h": None,
        "trades_24h": None,
        "last_price": None,
        "bid_price": None,
        "ask_price": None,
        "spread_pct": None,
        "age_days": None,
        "volatility_pct": None,
        "depth_010_notional": None,
        "depth_025_notional": None,
        "depth_050_notional": None,
        "depth_score": None,
        "liquidity_score": None,
        "liquidity_grade": "F",
        "universe_quality": 0.0,
        "decision": "PENDING",
        "final_status": "",
        "selected": False,
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
        ("trades >= seuil", counts.get("trades_rejected", 0), counts.get("trades_ok", 0)),
        ("prix valide", counts.get("price_rejected", 0), counts.get("price_ok", 0)),
        ("spread <= seuil", counts.get("spread_rejected", 0), counts.get("spread_ok", 0)),
        ("âge >= seuil", counts.get("age_rejected", 0), counts.get("age_ok", 0)),
        ("volatilité <= seuil", counts.get("volatility_rejected", 0), counts.get("volatility_ok", 0)),
        ("profondeur >= seuil", counts.get("depth_rejected", 0), counts.get("depth_ok", 0)),
        ("volume >= seuil", counts.get("volume_rejected", 0), counts.get("volume_ok", 0)),
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
    """Construit l'univers Spot Binance à chaque exécution.

    Pipeline : filtres rapides -> profondeur -> score/grade -> volume ->
    déduplication -> plafond. L'audit reste entièrement en mémoire.
    """
    global CRYPTO, _DYNAMIC_CRYPTO_SYMBOLS, _DYNAMIC_CRYPTO_METADATA

    configured_limit = BINANCE_MAX_UNIVERSE if max_symbols is None else int(max_symbols)
    limit = configured_limit if configured_limit > 0 else None
    session = requests.Session()
    session.headers.update({"User-Agent": "V4.2.5-Binance-Universe/1.0", "Accept": "application/json"})

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
    counts = {k: 0 for k in (
        "non_trading", "trading", "not_spot", "spot", "quote_rejected", "quote_ok",
        "structural_rejected", "candidate", "ticker_missing", "ticker_ok", "volume_rejected",
        "volume_ok", "trades_rejected", "trades_ok", "price_rejected", "price_ok",
        "spread_rejected", "spread_ok", "age_rejected", "age_ok", "volatility_rejected",
        "volatility_ok", "depth_rejected", "depth_ok", "depth_failed", "duplicate_rejected",
        "unique_ok", "cap_rejected", "selected"
    )}

    # 1. Exchange info / filtres structurels.
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
        permission_ok = isinstance(permissions, list) and "SPOT" in {str(x).upper() for x in permissions}
        if spot_allowed is True:
            spot_ok, spot_basis = True, "isSpotTradingAllowed=True"
        elif permission_ok:
            spot_ok, spot_basis = True, "permissions=SPOT"
        elif spot_allowed is False:
            spot_ok, spot_basis = False, "isSpotTradingAllowed=False"
        else:
            spot_ok, spot_basis = True, "champ Spot absent; compatibilité V4"
        row["spot_allowed"] = str(spot_ok)
        row["spot_basis"] = spot_basis
        if not spot_ok:
            counts["not_spot"] += 1
            _binance_reject(row, "SPOT", spot_basis)
            continue
        counts["spot"] += 1

        quote_asset = str(item.get("quoteAsset", "")).upper()
        base_asset = str(item.get("baseAsset", "")).upper()
        row["quote_asset"], row["base_asset"] = quote_asset, base_asset
        if quote_asset not in BINANCE_ALLOWED_QUOTE_ASSETS:
            counts["quote_rejected"] += 1
            _binance_reject(row, "quote", f"quote={quote_asset}")
            continue
        counts["quote_ok"] += 1

        special, special_reason = _binance_is_excluded_product(symbol, base_asset)
        if base_asset in BINANCE_STABLECOIN_BASE_ASSETS:
            counts["structural_rejected"] += 1
            _binance_reject(row, "structure", "stablecoin_or_fiat_base")
            continue
        if base_asset in BINANCE_EXCLUDED_BASE_ASSETS:
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

    # 2. Ticker + filtres rapides. Le volume n'est PAS filtré ici.
    print("[Binance Universe] ticker 24h...")
    tickers, ticker_base = _binance_get_json(session, "/api/v3/ticker/24hr")
    if not isinstance(tickers, list):
        raise RuntimeError("Binance ticker/24hr: format invalide")
    ticker_by_symbol = {str(x.get("symbol", "")).upper(): x for x in tickers if isinstance(x, dict)}
    quick: List[Dict[str, Any]] = []

    for symbol, info in candidates.items():
        row = rows[symbol]
        ticker = ticker_by_symbol.get(symbol)
        if not ticker:
            counts["ticker_missing"] += 1
            _binance_reject(row, "ticker", "ticker_24h_missing")
            continue
        counts["ticker_ok"] += 1
        quote_volume = _binance_float(ticker.get("quoteVolume"))
        trades = int(_binance_float(ticker.get("count")))
        last_price = _binance_float(ticker.get("lastPrice"))
        bid = _binance_float(ticker.get("bidPrice"))
        ask = _binance_float(ticker.get("askPrice"))
        row.update({"quote_volume_24h": quote_volume, "trades_24h": trades,
                    "last_price": last_price, "bid_price": bid, "ask_price": ask})

        if trades < BINANCE_MIN_TRADES_24H:
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
        row["age_days"], row["volatility_pct"] = age_days, volatility_pct
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
        quick.append({
            "symbol": symbol, "quote_asset": str(info.get("quoteAsset", "")).upper(),
            "base_asset": str(info.get("baseAsset", "")).upper(),
            "quote_volume_24h": quote_volume, "trades_24h": trades,
            "bid": bid, "ask": ask, "spread_pct": spread_pct,
            "volatility_pct": volatility_pct, "age_days": age_days,
            "onboard_date": info.get("onboardDate"), "status": info.get("status"),
        })

    print(
        f"[Binance Universe] filtres rapides | candidats={counts['candidate']} | "
        f"ticker OK={counts['ticker_ok']} | trades OK={counts['trades_ok']} | prix OK={counts['price_ok']} | "
        f"spread OK={counts['spread_ok']} | âge OK={counts['age_ok']} | volatilité OK={counts['volatility_ok']} | "
        f"rejets: trades={counts['trades_rejected']}, prix={counts['price_rejected']}, "
        f"spread={counts['spread_rejected']}, âge={counts['age_rejected']}, "
        f"volatilité={counts['volatility_rejected']}, ticker_manquant={counts['ticker_missing']}"
    )

    # 3. Profondeur AVANT le seuil de volume.
    if quick:
        print(f"[Binance Universe] analyse profondeur carnet: {len(quick)} paires | workers={max(1, BINANCE_DEPTH_WORKERS)}")
        workers = max(1, min(BINANCE_DEPTH_WORKERS, len(quick)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_binance_fetch_depth, item["symbol"]): item for item in quick}
            for future in as_completed(futures):
                item = futures[future]
                row = rows[item["symbol"]]
                try:
                    _, payload, source = future.result()
                except Exception as exc:
                    counts["depth_failed"] += 1
                    item["depth_rejected"] = True
                    item["depth_score"] = 0.0
                    item["liquidity_score"] = None
                    item["liquidity_grade"] = "F"
                    _binance_reject(row, "profondeur", f"depth_error:{exc}")
                    continue
                mid = (float(item["bid"]) + float(item["ask"])) / 2.0
                metrics = _binance_depth_metrics(payload, mid) if payload else {}
                depth_score = _binance_depth_score(metrics)
                item.update(metrics)
                item["depth_score"] = depth_score
                item["depth_source"] = source
                row["depth_010_notional"] = metrics.get("depth_010_notional", 0.0)
                row["depth_025_notional"] = metrics.get("depth_025_notional", 0.0)
                row["depth_050_notional"] = metrics.get("depth_050_notional", 0.0)
                row["depth_score"] = depth_score
                depth025 = metrics.get("depth_025_notional", 0.0)
                item["depth_rejected"] = depth025 < BINANCE_MIN_DEPTH_025_NOTIONAL
                if item["depth_rejected"]:
                    counts["depth_rejected"] += 1
                    _binance_reject(row, "profondeur", f"depth_025<{BINANCE_MIN_DEPTH_025_NOTIONAL:g}")
                else:
                    counts["depth_ok"] += 1

                score = _binance_liquidity_score(depth_score, item["quote_volume_24h"],
                                                 item["spread_pct"], item["trades_24h"], item["age_days"])
                grade = _binance_liquidity_grade(score)
                item["liquidity_score"], item["liquidity_grade"] = score, grade
                item["universe_quality"] = score / 100.0
                row["liquidity_score"], row["liquidity_grade"] = score, grade
                row["universe_quality"] = score / 100.0

    depth_passed = [x for x in quick if not x.get("depth_rejected", True) and not rows[x["symbol"]].get("first_rejection_stage")]
    print(f"[Binance Universe] profondeur OK={counts['depth_ok']} | rejetée={counts['depth_rejected']} | erreurs={counts['depth_failed']}")

    # 4. Volume production seulement après l'analyse du carnet.
    volume_passed: List[Dict[str, Any]] = []
    for item in depth_passed:
        row = rows[item["symbol"]]
        if item["quote_volume_24h"] < BINANCE_MIN_QUOTE_VOLUME_24H:
            counts["volume_rejected"] += 1
            _binance_reject(row, "volume", f"quote_volume_24h<{BINANCE_MIN_QUOTE_VOLUME_24H:g}")
            continue
        counts["volume_ok"] += 1
        volume_passed.append(item)
    print(f"[Binance Universe] volume production | seuil={BINANCE_MIN_QUOTE_VOLUME_24H:,.0f} | OK={counts['volume_ok']} | rejetés={counts['volume_rejected']}")

    # 5. Déduplication par base : qualité, puis profondeur, volume, quote.
    quote_rank = {quote: i for i, quote in enumerate(BINANCE_QUOTE_PRIORITY)}
    best_by_base: Dict[str, Dict[str, Any]] = {}
    duplicate_losers: List[Dict[str, Any]] = []
    for item in volume_passed:
        base = item["base_asset"]
        current = best_by_base.get(base)
        if current is None:
            best_by_base[base] = item
            continue
        current_key = (current["liquidity_score"], current["depth_score"], current["quote_volume_24h"], -quote_rank.get(current["quote_asset"], 999))
        new_key = (item["liquidity_score"], item["depth_score"], item["quote_volume_24h"], -quote_rank.get(item["quote_asset"], 999))
        if new_key > current_key:
            duplicate_losers.append(current)
            best_by_base[base] = item
        else:
            duplicate_losers.append(item)

    for loser in duplicate_losers:
        winner = best_by_base.get(loser["base_asset"], {})
        counts["duplicate_rejected"] += 1
        _binance_reject(rows[loser["symbol"]], "déduplication", f"duplicate_base;winner={winner.get('symbol', '')}")
    ranked_unique = list(best_by_base.values())
    counts["unique_ok"] = len(ranked_unique)
    ranked_unique.sort(key=lambda x: (x["liquidity_score"], x["depth_score"], x["quote_volume_24h"], -quote_rank.get(x["quote_asset"], 999)), reverse=True)

    # 6. Plafond éventuel.
    if limit is None:
        selected, dropped_by_cap = ranked_unique, []
    else:
        selected, dropped_by_cap = ranked_unique[:limit], ranked_unique[limit:]
        counts["cap_rejected"] = len(dropped_by_cap)
        for item in dropped_by_cap:
            _binance_reject(rows[item["symbol"]], "plafond univers", f"rank>{limit}")
    counts["selected"] = len(selected)

    for item in selected:
        row = rows[item["symbol"]]
        row.update({"decision": "SELECTED", "final_status": "selected", "selected": True,
                    "selected_quote": item["quote_asset"]})
    for row in rows.values():
        if not row.get("final_status"):
            row["final_status"] = "rejected" if row.get("first_rejection_stage") else "not_selected"
        if row.get("decision") == "PENDING":
            row["decision"] = "REJECTED"

    # 7. Audit agrégé en mémoire.
    reason_counts: Dict[str, int] = {}
    stage_counts: Dict[str, int] = {}
    rejected_rows = []
    for row in rows.values():
        reason = row.get("first_rejection_reason") or "PAS_REJETE"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        stage = row.get("first_rejection_stage")
        if stage:
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
            rejected_rows.append(row)

    _print_binance_cascade(counts, discovered)
    print("[Binance Audit] NOTE : la note mesure la qualité de liquidité, pas l'éligibilité production ; un A peut donc être rejeté par volume < 5 M$.")
    print("[Binance Audit] REJETS — résumé par étape")
    for stage, n in sorted(stage_counts.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {n:>6} | {stage}")
    print("[Binance Audit] REJETS — motifs détaillés")
    for reason, n in sorted(reason_counts.items(), key=lambda x: (-x[1], x[0]))[:20]:
        print(f"  {n:>6} | {reason}")

    rejected_ranked = sorted(rejected_rows, key=lambda r: (float(r.get("liquidity_score") or 0.0), float(r.get("depth_025_notional") or 0.0), float(r.get("quote_volume_24h") or 0.0)), reverse=True)
    print("[Binance Audit] TOP ACTIFS REJETÉS — meilleurs scores")
    for rank, row in enumerate(rejected_ranked[:max(0, BINANCE_AUDIT_REJECTED_TOP)], 1):
        print(f"  #{rank:02d} {row['symbol']:<14} score={float(row.get('liquidity_score') or 0):6.1f} grade={row.get('liquidity_grade','F')} vol24h={float(row.get('quote_volume_24h') or 0):,.0f} depth025={float(row.get('depth_025_notional') or 0):,.0f} motif={row.get('first_rejection_reason','')}")

    low_volume_good = [r for r in rows.values() if r.get("liquidity_grade") in {"A", "B", "C"} and float(r.get("quote_volume_24h") or 0.0) < BINANCE_MIN_QUOTE_VOLUME_24H]
    low_volume_good.sort(key=lambda r: float(r.get("liquidity_score") or 0.0), reverse=True)
    print("[Binance Audit] SOUS 5M MAIS LIQUIDITÉ A/B/C")
    if not low_volume_good:
        print("  Aucun actif.")
    for row in low_volume_good[:max(0, BINANCE_AUDIT_REJECTED_TOP)]:
        print(f"  {row['symbol']:<14} grade={row.get('liquidity_grade','F')} score={float(row.get('liquidity_score') or 0):.1f} vol24h={float(row.get('quote_volume_24h') or 0):,.0f} depth025={float(row.get('depth_025_notional') or 0):,.0f}")

    print("[Binance Audit] EXEMPLES PAR MOTIF")
    examples: Dict[str, int] = {}
    for row in sorted(rejected_rows, key=lambda r: str(r.get("first_rejection_reason", ""))):
        reason = str(row.get("first_rejection_reason", ""))
        if examples.get(reason, 0) >= BINANCE_AUDIT_EXAMPLES_PER_REASON:
            continue
        print(f"  {row['symbol']:<14} | {reason} | score={float(row.get('liquidity_score') or 0):.1f} | grade={row.get('liquidity_grade','F')}")
        examples[reason] = examples.get(reason, 0) + 1

    selected_symbols = [x["symbol"] for x in selected]
    _DYNAMIC_CRYPTO_SYMBOLS = set(selected_symbols)
    _DYNAMIC_CRYPTO_METADATA = {x["symbol"]: x for x in selected}
    CRYPTO = list(selected_symbols)
    for symbol in selected_symbols:
        existing = CRYPTO_SYMBOLS.get(symbol)
        if existing is None:
            CRYPTO_SYMBOLS[symbol] = {"binance": symbol}
        else:
            existing["binance"] = symbol
    ASSET_GROUPS["crypto"] = CRYPTO

    print(f"[Binance Universe] ticker_endpoint={ticker_base} | sélection finale={len(selected_symbols)} sur {len(ranked_unique)} actifs admissibles | audit=memoire")
    for rank, item in enumerate(selected[:10], 1):
        print(f"  #{rank:02d} {item['symbol']:<14} grade={item.get('liquidity_grade','F')} score={item.get('liquidity_score',0):.1f} vol24h={item['quote_volume_24h']:,.0f} spread={item['spread_pct']:.3f}% volatilité={item['volatility_pct']:.2f}% ±0.10=${item.get('depth_010_notional',0):,.0f} ±0.25=${item.get('depth_025_notional',0):,.0f} ±0.50=${item.get('depth_050_notional',0):,.0f}")
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

