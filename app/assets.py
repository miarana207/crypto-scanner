"""
V4.2 — Univers multi-actifs.

Univers :
- Binance : univers crypto dynamique
- 10 Forex
- 35 actions
- 10 indices
- 6 commodities multi-sources
- 5 commodities Yahoo-only

Principe important V4.2 :
si un fournisseur n'a pas de mapping pour un actif,
le DataRouter doit ignorer ce fournisseur au lieu
d'envoyer aveuglément le symbole canonique.

Binance :
- univers dynamique via exchangeInfo
- audit complet en mémoire
- aucun CSV généré
- profondeur du carnet AVANT le filtre de volume
- score de liquidité A/B/C/D/E/F
- analyse des actifs rejetés
- déduplication par base_asset
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

BINANCE_UNIVERSE_URL = os.getenv(
    "BINANCE_UNIVERSE_URL",
    os.getenv(
        "BINANCE_DATA_URL",
        "https://data-api.binance.vision",
    ),
).rstrip("/")


BINANCE_UNIVERSE_FALLBACK_URLS = [
    x.strip().rstrip("/")
    for x in os.getenv(
        "BINANCE_UNIVERSE_FALLBACK_URLS",
        (
            "https://api.binance.com,"
            "https://api-gcp.binance.com,"
            "https://api1.binance.com,"
            "https://api2.binance.com,"
            "https://api3.binance.com,"
            "https://api4.binance.com"
        ),
    ).split(",")
    if x.strip()
]


BINANCE_UNIVERSE_TIMEOUT = int(
    os.getenv("BINANCE_UNIVERSE_TIMEOUT", "20")
)


# ============================================================
# SEUILS DE PRODUCTION
# ============================================================

BINANCE_MIN_QUOTE_VOLUME_24H = float(
    os.getenv(
        "BINANCE_MIN_QUOTE_VOLUME_24H",
        "5000000",
    )
)

BINANCE_MAX_SPREAD_PCT = float(
    os.getenv(
        "BINANCE_MAX_SPREAD_PCT",
        "0.30",
    )
)

BINANCE_MIN_TRADES_24H = int(
    os.getenv(
        "BINANCE_MIN_TRADES_24H",
        "500",
    )
)

# 0 = aucun plafond.
BINANCE_MAX_UNIVERSE = int(
    os.getenv(
        "BINANCE_MAX_UNIVERSE",
        "0",
    )
)


# ============================================================
# AUDIT
# ============================================================

# Nombre maximal de rejets détaillés affichés dans les logs.
# Les statistiques globales portent toujours sur l'ensemble
# des symboles Binance recensés.
BINANCE_AUDIT_REJECTED_TOP = int(
    os.getenv(
        "BINANCE_AUDIT_REJECTED_TOP",
        "50",
    )
)

# Nombre d'exemples affichés par motif.
BINANCE_AUDIT_EXAMPLES_PER_REASON = int(
    os.getenv(
        "BINANCE_AUDIT_EXAMPLES_PER_REASON",
        "5",
    )
)


# ============================================================
# PROFONDEUR DU CARNET
# ============================================================

# IMPORTANT :
# La profondeur est volontairement analysée AVANT le filtre
# de volume 24h.
#
# Cela permet d'identifier des actifs qui ont un volume inférieur
# à 5 M$ mais dont le carnet reste réellement liquide.
BINANCE_DEPTH_LIMIT = int(
    os.getenv(
        "BINANCE_DEPTH_LIMIT",
        "100",
    )
)

BINANCE_DEPTH_WORKERS = int(
    os.getenv(
        "BINANCE_DEPTH_WORKERS",
        "8",
    )
)

BINANCE_MIN_DEPTH_025_NOTIONAL = float(
    os.getenv(
        "BINANCE_MIN_DEPTH_025_NOTIONAL",
        "25000",
    )
)

BINANCE_DEPTH_TARGET_010 = float(
    os.getenv(
        "BINANCE_DEPTH_TARGET_010",
        "100000",
    )
)

BINANCE_DEPTH_TARGET_025 = float(
    os.getenv(
        "BINANCE_DEPTH_TARGET_025",
        "250000",
    )
)

BINANCE_DEPTH_TARGET_050 = float(
    os.getenv(
        "BINANCE_DEPTH_TARGET_050",
        "500000",
    )
)


# ============================================================
# QUOTES AUTORISÉES
# ============================================================

BINANCE_ALLOWED_QUOTE_ASSETS = {
    x.strip().upper()
    for x in os.getenv(
        "BINANCE_ALLOWED_QUOTE_ASSETS",
        "USDT,USDC,FDUSD",
    ).split(",")
    if x.strip()
}


# ============================================================
# BASES EXCLUES
# ============================================================

BINANCE_STABLECOIN_BASE_ASSETS = {
    x.strip().upper()
    for x in os.getenv(
        "BINANCE_STABLECOIN_BASE_ASSETS",
        (
            "USDT,USDC,FDUSD,USD1,RLUSD,EURI,EURC,EURT,"
            "AEUR,USDE,DAI,TUSD,USDP,PYUSD,GUSD,FRAX,LUSD,"
            "SUSD,CRVUSD,USDD,USTC,BUSD"
        ),
    ).replace("\n", "").split(",")
    if x.strip()
}


BINANCE_EXCLUDED_BASE_ASSETS = {
    x.strip().upper()
    for x in os.getenv(
        "BINANCE_EXCLUDED_BASE_ASSETS",
        (
            "USDT,USDC,FDUSD,USD1,RLUSD,EUR,GBP,TRY,BRL,"
            "UAH,PLN,ZAR,ARS,MXN,NGN,RON,JPY,AUD"
        ),
    ).replace("\n", "").split(",")
    if x.strip()
}


# ============================================================
# PRIORITÉ DES QUOTES
# ============================================================

BINANCE_QUOTE_PRIORITY = [
    x.strip().upper()
    for x in os.getenv(
        "BINANCE_QUOTE_PRIORITY",
        "USDT,USDC,FDUSD",
    ).split(",")
    if x.strip()
]


# ============================================================
# PRODUITS SPÉCIAUX / TOKENISÉS
# ============================================================

BINANCE_SPECIAL_BASE_ASSETS = {
    x.strip().upper()
    for x in os.getenv(
        "BINANCE_SPECIAL_BASE_ASSETS",
        (
            "CRCLB,MSTRB,NVDAB,SNDKB,TSLAB,SPCXB,AXTIB,"
            "CRWVB,INTWB,KORUB,MUUB,MVLLB,ORCLB,QNTB,SNXXB,"
            "TQQQB,MUB,QQQB,XAUT,BFUSD"
        ),
    ).replace("\n", "").split(",")
    if x.strip()
}


BINANCE_LEVERAGED_TOKEN_SUFFIXES = (
    "UP",
    "DOWN",
    "BULL",
    "BEAR",
)


# ============================================================
# ÂGE / VOLATILITÉ
# ============================================================

BINANCE_MAX_UNIVERSE_VOLATILITY_PCT = float(
    os.getenv(
        "BINANCE_MAX_UNIVERSE_VOLATILITY_PCT",
        "40.0",
    )
)

BINANCE_MIN_UNIVERSE_AGE_DAYS = float(
    os.getenv(
        "BINANCE_MIN_UNIVERSE_AGE_DAYS",
        "3.0",
    )
)


# ============================================================
# HELPERS BINANCE
# ============================================================

def _binance_float(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        number = float(value)

        if not math.isfinite(number):
            return default

        return number

    except (TypeError, ValueError):
        return default


def _binance_age_days(
    onboard_date: Any,
) -> float:

    if onboard_date in (None, "", 0):
        return 3650.0

    try:
        age = (
            time.time() * 1000.0
            - float(onboard_date)
        ) / 86400000.0

        return max(0.0, age)

    except (TypeError, ValueError):
        return 3650.0


def _binance_is_excluded_product(
    symbol: str,
    base_asset: str,
) -> tuple[bool, str]:

    base = str(
        base_asset or ""
    ).upper().strip()

    sym = str(
        symbol or ""
    ).upper().strip()

    if base in BINANCE_SPECIAL_BASE_ASSETS:
        return True, "special/tokenized"

    if base.endswith(
        BINANCE_LEVERAGED_TOKEN_SUFFIXES
    ):
        return True, "leveraged-token"

    return False, ""


# ============================================================
# HTTP BINANCE
# ============================================================

def _binance_get_json(
    session: requests.Session,
    path: str,
) -> tuple[Any, str]:

    urls: List[str] = []

    for base in [
        BINANCE_UNIVERSE_URL,
        *BINANCE_UNIVERSE_FALLBACK_URLS,
    ]:

        base = str(
            base
        ).strip().rstrip("/")

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

            return (
                response.json(),
                base,
            )

        except requests.RequestException as exc:

            status = getattr(
                exc.response,
                "status_code",
                None,
            )

            if status is not None:
                errors.append(
                    f"{base} -> HTTP {status}"
                )
            else:
                errors.append(
                    f"{base} -> {exc}"
                )

        except ValueError as exc:

            errors.append(
                f"{base} -> JSON invalide: {exc}"
            )

    raise RuntimeError(
        f"Binance: aucun endpoint disponible pour {path}. "
        + " | ".join(errors)
    )


# ============================================================
# PROFONDEUR
# ============================================================

def _binance_depth_metrics(
    depth: Any,
    mid: float,
) -> Dict[str, float]:

    if not isinstance(depth, dict) or mid <= 0:
        return {}

    bids = depth.get(
        "bids",
        [],
    )

    asks = depth.get(
        "asks",
        [],
    )

    if not isinstance(bids, list):
        return {}

    if not isinstance(asks, list):
        return {}

    bands = (
        0.001,
        0.0025,
        0.005,
    )

    result: Dict[str, float] = {}

    for band in bands:

        bid_notional = 0.0
        ask_notional = 0.0

        bid_limit = (
            mid * (1.0 - band)
        )

        ask_limit = (
            mid * (1.0 + band)
        )

        for level in bids:

            if (
                not isinstance(
                    level,
                    (list, tuple),
                )
                or len(level) < 2
            ):
                continue

            price = _binance_float(
                level[0]
            )

            qty = _binance_float(
                level[1]
            )

            if (
                price > 0
                and qty > 0
                and price >= bid_limit
            ):
                bid_notional += (
                    price * qty
                )

        for level in asks:

            if (
                not isinstance(
                    level,
                    (list, tuple),
                )
                or len(level) < 2
            ):
                continue

            price = _binance_float(
                level[0]
            )

            qty = _binance_float(
                level[1]
            )

            if (
                price > 0
                and qty > 0
                and price <= ask_limit
            ):
                ask_notional += (
                    price * qty
                )

        key = f"{int(band * 10000):03d}"

        result[
            f"depth_bid_{key}_notional"
        ] = bid_notional

        result[
            f"depth_ask_{key}_notional"
        ] = ask_notional

        result[
            f"depth_{key}_notional"
        ] = (
            bid_notional
            + ask_notional
        )

    return result


def _binance_depth_score(
    depth_metrics: Dict[str, float],
) -> float:

    if not depth_metrics:
        return 0.0

    def norm(
        value: float,
        target: float,
    ) -> float:

        if (
            value <= 0
            or target <= 0
        ):
            return 0.0

        return min(
            1.0,
            math.log10(
                1.0 + value
            )
            / math.log10(
                1.0 + target
            ),
        )

    s010 = norm(
        depth_metrics.get(
            "depth_010_notional",
            0.0,
        ),
        BINANCE_DEPTH_TARGET_010,
    )

    s025 = norm(
        depth_metrics.get(
            "depth_025_notional",
            0.0,
        ),
        BINANCE_DEPTH_TARGET_025,
    )

    s050 = norm(
        depth_metrics.get(
            "depth_050_notional",
            0.0,
        ),
        BINANCE_DEPTH_TARGET_050,
    )

    return (
        0.30 * s010
        + 0.35 * s025
        + 0.35 * s050
    )


def _binance_fetch_depth(
    symbol: str,
) -> tuple[
    str,
    Optional[Dict[str, Any]],
    str,
]:

    session = requests.Session()

    session.headers.update({
        "User-Agent": (
            "V4.2.5-Binance-Depth-Audit/1.0"
        ),
        "Accept": "application/json",
    })

    urls: List[str] = []

    for base in [
        BINANCE_UNIVERSE_URL,
        *BINANCE_UNIVERSE_FALLBACK_URLS,
    ]:

        base = str(
            base
        ).strip().rstrip("/")

        if (
            base
            and base not in urls
        ):
            urls.append(base)

    last_error = ""

    for base in urls:

        try:

            response = session.get(
                f"{base}/api/v3/depth",
                params={
                    "symbol": symbol,
                    "limit": BINANCE_DEPTH_LIMIT,
                },
                timeout=BINANCE_UNIVERSE_TIMEOUT,
            )

            response.raise_for_status()

            payload = response.json()

            return (
                symbol,
                payload,
                base,
            )

        except (
            requests.RequestException,
            ValueError,
        ) as exc:

            last_error = str(exc)

    return (
        symbol,
        None,
        last_error,
    )


# ============================================================
# QUALITÉ LIQUIDITÉ
# ============================================================

def _binance_volume_quality(
    volume: float,
) -> float:

    if volume <= 0:
        return 0.0

    # Échelle logarithmique :
    # 5 M$ n'est pas considéré comme 5 fois meilleur que 1 M$.
    return min(
        1.0,
        math.log10(
            1.0 + volume
        )
        / math.log10(
            1.0 + 100_000_000.0
        ),
    )


def _binance_trades_quality(
    trades: float,
) -> float:

    if trades <= 0:
        return 0.0

    return min(
        1.0,
        trades / 100000.0,
    )


def _binance_spread_quality(
    spread_pct: float,
) -> float:

    if spread_pct < 0:
        return 0.0

    return max(
        0.0,
        1.0
        - (
            spread_pct
            / max(
                BINANCE_MAX_SPREAD_PCT,
                0.0001,
            )
        ),
    )


def _binance_stability_quality(
    age_days: float,
) -> float:

    if age_days <= 0:
        return 0.0

    return min(
        1.0,
        age_days / 365.0,
    )


def _binance_liquidity_score(
    depth_score: float,
    volume: float,
    spread_pct: float,
    trades: float,
    age_days: float,
) -> float:
    """
    Score global 0..100.

    Pondération :
    - profondeur : 30 %
    - volume : 25 %
    - spread : 20 %
    - trades : 15 %
    - stabilité/âge : 10 %
    """

    depth_quality = max(
        0.0,
        min(1.0, depth_score),
    )

    volume_quality = (
        _binance_volume_quality(volume)
    )

    spread_quality = (
        _binance_spread_quality(
            spread_pct
        )
    )

    trades_quality = (
        _binance_trades_quality(
            trades
        )
    )

    stability_quality = (
        _binance_stability_quality(
            age_days
        )
    )

    score = 100.0 * (
        0.30 * depth_quality
        + 0.25 * volume_quality
        + 0.20 * spread_quality
        + 0.15 * trades_quality
        + 0.10 * stability_quality
    )

    return max(
        0.0,
        min(
            100.0,
            score,
        ),
    )


def _binance_liquidity_grade(
    score: Optional[float],
) -> str:

    if score is None:
        return "F"

    try:
        score = float(score)
    except (TypeError, ValueError):
        return "F"

    if not math.isfinite(score):
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


# ============================================================
# FORMATAGE AUDIT
# ============================================================

def _binance_fmt_money(
    value: Any,
) -> str:

    if value in (
        None,
        "",
    ):
        return "-"

    number = _binance_float(
        value,
        default=-1.0,
    )

    if number < 0:
        return "-"

    if number >= 1_000_000_000:
        return (
            f"{number / 1_000_000_000:.2f} Md$"
        )

    if number >= 1_000_000:
        return (
            f"{number / 1_000_000:.2f} M$"
        )

    if number >= 1_000:
        return (
            f"{number / 1_000:.1f} K$"
        )

    return f"{number:,.0f}$"


def _binance_fmt_number(
    value: Any,
) -> str:

    if value in (
        None,
        "",
    ):
        return "-"

    number = _binance_float(
        value,
        default=-1.0,
    )

    if number < 0:
        return "-"

    return f"{number:,.0f}"


def _binance_fmt_pct(
    value: Any,
    decimals: int = 2,
) -> str:

    if value in (
        None,
        "",
    ):
        return "-"

    number = _binance_float(
        value,
        default=-1.0,
    )

    if number < 0:
        return "-"

    return (
        f"{number:.{decimals}f}%"
    )


def _binance_fmt_score(
    value: Any,
) -> str:

    if value in (
        None,
        "",
    ):
        return "-"

    number = _binance_float(
        value,
        default=-1.0,
    )

    if number < 0:
        return "-"

    return f"{number:.1f}/100"


# ============================================================
# REJET
# ============================================================

def _binance_reject(
    row: Dict[str, Any],
    stage: str,
    reason: str,
) -> None:

    # On conserve UNIQUEMENT le premier rejet.
    # Cela représente la décision réelle de la cascade.
    if not row.get(
        "first_rejection_stage"
    ):

        row[
            "first_rejection_stage"
        ] = stage

        row[
            "first_rejection_reason"
        ] = reason

        row[
            "final_status"
        ] = "rejected"


# ============================================================
# AUDIT BINANCE
# ============================================================

def _binance_print_rejection_audit(
    rows: Dict[str, Dict[str, Any]],
    counts: Dict[str, int],
    discovered: int,
) -> None:

    rejected = [
        row
        for row in rows.values()
        if row.get(
            "first_rejection_stage"
        )
    ]

    selected = [
        row
        for row in rows.values()
        if row.get(
            "final_status"
        ) == "selected"
    ]

    # --------------------------------------------------------
    # RÉSUMÉ
    # --------------------------------------------------------

    print("")
    print("=" * 100)
    print("[Binance Audit] RÉSUMÉ COMPLET")
    print("=" * 100)

    print(
        f"Actifs recensés       : {discovered:,}"
    )

    print(
        f"Actifs rejetés        : {len(rejected):,}"
    )

    print(
        f"Actifs sélectionnés   : {len(selected):,}"
    )

    # --------------------------------------------------------
    # CASCADE
    # --------------------------------------------------------

    print("")
    print(
        "ÉTAPE                         REJETÉS       RESTANTS"
    )
    print("-" * 60)

    stages = [
        (
            "TRADING",
            counts.get(
                "non_trading",
                0,
            ),
            counts.get(
                "trading",
                0,
            ),
        ),
        (
            "SPOT",
            counts.get(
                "not_spot",
                0,
            ),
            counts.get(
                "spot",
                0,
            ),
        ),
        (
            "Quote autorisée",
            counts.get(
                "quote_rejected",
                0,
            ),
            counts.get(
                "quote_ok",
                0,
            ),
        ),
        (
            "Structure",
            counts.get(
                "structural_rejected",
                0,
            ),
            counts.get(
                "candidate",
                0,
            ),
        ),
        (
            "Ticker 24h",
            counts.get(
                "ticker_missing",
                0,
            ),
            counts.get(
                "ticker_ok",
                0,
            ),
        ),
        (
            "Trades",
            counts.get(
                "trades_rejected",
                0,
            ),
            counts.get(
                "trades_ok",
                0,
            ),
        ),
        (
            "Prix",
            counts.get(
                "price_rejected",
                0,
            ),
            counts.get(
                "price_ok",
                0,
            ),
        ),
        (
            "Spread",
            counts.get(
                "spread_rejected",
                0,
            ),
            counts.get(
                "spread_ok",
                0,
            ),
        ),
        (
            "Âge",
            counts.get(
                "age_rejected",
                0,
            ),
            counts.get(
                "age_ok",
                0,
            ),
        ),
        (
            "Volatilité",
            counts.get(
                "volatility_rejected",
                0,
            ),
            counts.get(
                "volatility_ok",
                0,
            ),
        ),
        (
            "Profondeur",
            counts.get(
                "depth_rejected",
                0,
            ),
            counts.get(
                "depth_ok",
                0,
            ),
        ),
        (
            "Volume 24h",
            counts.get(
                "volume_rejected",
                0,
            ),
            counts.get(
                "volume_ok",
                0,
            ),
        ),
        (
            "Déduplication",
            counts.get(
                "duplicate_rejected",
                0,
            ),
            counts.get(
                "unique_ok",
                0,
            ),
        ),
        (
            "Plafond univers",
            counts.get(
                "cap_rejected",
                0,
            ),
            counts.get(
                "selected",
                0,
            ),
        ),
    ]

    for label, rejected_count, remaining in stages:

        print(
            f"{label:<28}"
            f"{rejected_count:>10,}"
            f"{remaining:>14,}"
        )

    # --------------------------------------------------------
    # MOTIFS DÉTAILLÉS
    # --------------------------------------------------------

    reason_counts: Dict[str, int] = {}

    reason_examples: Dict[
        str,
        List[str],
    ] = {}

    for row in rejected:

        reason = (
            row.get(
                "first_rejection_reason"
            )
            or "raison_inconnue"
        )

        reason_counts[
            reason
        ] = (
            reason_counts.get(
                reason,
                0,
            )
            + 1
        )

        examples = reason_examples.setdefault(
            reason,
            [],
        )

        if (
            len(examples)
            < BINANCE_AUDIT_EXAMPLES_PER_REASON
        ):
            examples.append(
                str(
                    row.get(
                        "symbol",
                        "",
                    )
                )
            )

    print("")
    print(
        "MOTIFS DE PREMIER REJET"
    )
    print("-" * 100)

    for reason, count in sorted(
        reason_counts.items(),
        key=lambda x: (
            -x[1],
            x[0],
        ),
    )[:30]:

        examples = ", ".join(
            reason_examples.get(
                reason,
                [],
            )
        )

        print(
            f"{count:>6,} | "
            f"{reason:<55} | "
            f"{examples}"
        )

    # --------------------------------------------------------
    # DISTRIBUTION A/B/C/D/E/F
    # --------------------------------------------------------

    grades: Dict[str, int] = {
        "A": 0,
        "B": 0,
        "C": 0,
        "D": 0,
        "E": 0,
        "F": 0,
    }

    for row in rows.values():

        grade = str(
            row.get(
                "liquidity_grade",
                "F",
            )
        ).upper()

        if grade not in grades:
            grade = "F"

        grades[grade] += 1

    print("")
    print(
        "QUALITÉ DE LIQUIDITÉ"
    )
    print("-" * 60)

    for grade in [
        "A",
        "B",
        "C",
        "D",
        "E",
        "F",
    ]:

        print(
            f"{grade} : {grades[grade]:>6,}"
        )

    print("")
    print(
        "Pondération du score : "
        "Depth 30% | Volume 25% | Spread 20% | "
        "Trades 15% | Âge 10%"
    )

    print(
        "Important : une note A/B/C mesure la qualité de "
        "liquidité et ne signifie pas automatiquement "
        "éligibilité à la production."
    )

    # --------------------------------------------------------
    # MEILLEURS REJETÉS
    # --------------------------------------------------------

    scored_rejected = [
        row
        for row in rejected
        if row.get(
            "liquidity_score"
        ) is not None
    ]

    scored_rejected.sort(
        key=lambda row: (
            _binance_float(
                row.get(
                    "liquidity_score"
                ),
                0.0,
            ),
            _binance_float(
                row.get(
                    "depth_025_notional"
                ),
                0.0,
            ),
            _binance_float(
                row.get(
                    "quote_volume_24h"
                ),
                0.0,
            ),
        ),
        reverse=True,
    )

    print("")
    print(
        f"TOP {BINANCE_AUDIT_REJECTED_TOP} "
        "ACTIFS REJETÉS — MEILLEURE LIQUIDITÉ"
    )
    print("-" * 140)

    print(
        f"{'SYMBOLE':<16}"
        f"{'GRADE':<7}"
        f"{'SCORE':>9}"
        f"{'VOL24H':>14}"
        f"{'TRADES':>12}"
        f"{'SPREAD':>10}"
        f"{'DEPTH .25%':>14}"
        f"{'ÂGE':>10}"
        f"{'REJET':<35}"
    )

    print("-" * 140)

    for row in scored_rejected[
        :max(
            0,
            BINANCE_AUDIT_REJECTED_TOP,
        )
    ]:

        print(
            f"{str(row.get('symbol', '')):<16}"
            f"{str(row.get('liquidity_grade', 'F')):<7}"
            f"{_binance_fmt_score(row.get('liquidity_score')):>9}"
            f"{_binance_fmt_money(row.get('quote_volume_24h')):>14}"
            f"{_binance_fmt_number(row.get('trades_24h')):>12}"
            f"{_binance_fmt_pct(row.get('spread_pct'), 3):>10}"
            f"{_binance_fmt_money(row.get('depth_025_notional')):>14}"
            f"{_binance_fmt_number(row.get('age_days')):>10}"
            f"{str(row.get('first_rejection_reason', ''))[:35]:<35}"
        )

    # --------------------------------------------------------
    # SOUS 5 M$ MAIS BONNE LIQUIDITÉ
    # --------------------------------------------------------

    sub_5m = [
        row
        for row in rejected
        if (
            row.get(
                "first_rejection_stage"
            )
            == "volume"
            and _binance_float(
                row.get(
                    "quote_volume_24h"
                ),
                0.0,
            )
            < BINANCE_MIN_QUOTE_VOLUME_24H
            and row.get(
                "liquidity_grade"
            )
            in {
                "A",
                "B",
                "C",
            }
        )
    ]

    sub_5m.sort(
        key=lambda row: (
            _binance_float(
                row.get(
                    "liquidity_score"
                ),
                0.0,
            ),
            _binance_float(
                row.get(
                    "depth_025_notional"
                ),
                0.0,
            ),
        ),
        reverse=True,
    )

    print("")
    print(
        "ACTIFS < 5 M$ MAIS LIQUIDITÉ A/B/C"
    )
    print("-" * 130)

    if not sub_5m:

        print(
            "Aucun actif trouvé."
        )

    else:

        print(
            f"{'SYMBOLE':<16}"
            f"{'GRADE':<7}"
            f"{'SCORE':>9}"
            f"{'VOL24H':>14}"
            f"{'DEPTH .10%':>14}"
            f"{'DEPTH .25%':>14}"
            f"{'DEPTH .50%':>14}"
            f"{'SPREAD':>10}"
            f"{'TRADES':>12}"
        )

        print("-" * 130)

        for row in sub_5m[
            :max(
                0,
                BINANCE_AUDIT_REJECTED_TOP,
            )
        ]:

            print(
                f"{str(row.get('symbol', '')):<16}"
                f"{str(row.get('liquidity_grade', 'F')):<7}"
                f"{_binance_fmt_score(row.get('liquidity_score')):>9}"
                f"{_binance_fmt_money(row.get('quote_volume_24h')):>14}"
                f"{_binance_fmt_money(row.get('depth_010_notional')):>14}"
                f"{_binance_fmt_money(row.get('depth_025_notional')):>14}"
                f"{_binance_fmt_money(row.get('depth_050_notional')):>14}"
                f"{_binance_fmt_pct(row.get('spread_pct'), 3):>10}"
                f"{_binance_fmt_number(row.get('trades_24h')):>12}"
            )

    # --------------------------------------------------------
    # AUDIT DÉTAILLÉ
    # --------------------------------------------------------

    print("")
    print(
        "AUDIT DÉTAILLÉ DES PRINCIPAUX REJETÉS"
    )
    print("-" * 100)

    for row in scored_rejected[
        :max(
            0,
            BINANCE_AUDIT_REJECTED_TOP,
        )
    ]:

        print(
            f"{row.get('symbol', '')}"
            f" | base={row.get('base_asset', '')}"
            f" | quote={row.get('quote_asset', '')}"
            f" | grade={row.get('liquidity_grade', 'F')}"
            f" | score={_binance_fmt_score(row.get('liquidity_score'))}"
            f" | rejet={row.get('first_rejection_stage', '')}"
            f" | motif={row.get('first_rejection_reason', '')}"
        )

        print(
            f"    Volume 24h    : "
            f"{_binance_fmt_money(row.get('quote_volume_24h'))}"
        )

        print(
            f"    Trades 24h    : "
            f"{_binance_fmt_number(row.get('trades_24h'))}"
        )

        print(
            f"    Prix           : "
            f"{_binance_float(row.get('last_price')):.8g}"
        )

        print(
            f"    Bid / Ask      : "
            f"{_binance_float(row.get('bid_price')):.8g}"
            f" / "
            f"{_binance_float(row.get('ask_price')):.8g}"
        )

        print(
            f"    Spread         : "
            f"{_binance_fmt_pct(row.get('spread_pct'), 3)}"
        )

        print(
            f"    Âge            : "
            f"{_binance_fmt_number(row.get('age_days'))} jours"
        )

        print(
            f"    Volatilité 24h : "
            f"{_binance_fmt_pct(row.get('volatility_pct'))}"
        )

        print(
            f"    Depth ±0.10%   : "
            f"{_binance_fmt_money(row.get('depth_010_notional'))}"
        )

        print(
            f"    Depth ±0.25%   : "
            f"{_binance_fmt_money(row.get('depth_025_notional'))}"
        )

        print(
            f"    Depth ±0.50%   : "
            f"{_binance_fmt_money(row.get('depth_050_notional'))}"
        )

        print(
            f"    Depth score    : "
            f"{_binance_float(row.get('depth_score')):.3f}"
        )

        print(
            f"    Score liquidité: "
            f"{_binance_fmt_score(row.get('liquidity_score'))}"
        )

        print(
            f"    Grade          : "
            f"{row.get('liquidity_grade', 'F')}"
        )

        print("")


# ============================================================
# BUILD BINANCE UNIVERSE
# ============================================================

def build_binance_universe(
    max_symbols: Optional[int] = None,
) -> List[str]:

    global (
        CRYPTO,
        _DYNAMIC_CRYPTO_SYMBOLS,
        _DYNAMIC_CRYPTO_METADATA,
    )

    configured_limit = (
        BINANCE_MAX_UNIVERSE
        if max_symbols is None
        else int(max_symbols)
    )

    limit = (
        configured_limit
        if configured_limit > 0
        else None
    )

    session = requests.Session()

    session.headers.update({
        "User-Agent": (
            "V4.2.5-Binance-Universe-Audit/1.0"
        ),
        "Accept": "application/json",
    })

    # ========================================================
    # EXCHANGE INFO
    # ========================================================

    print(
        "[Binance Universe] exchangeInfo..."
    )

    exchange, exchange_base = (
        _binance_get_json(
            session,
            "/api/v3/exchangeInfo",
        )
    )

    if not isinstance(
        exchange,
        dict,
    ):
        raise RuntimeError(
            "Binance exchangeInfo: réponse JSON invalide"
        )

    symbols = exchange.get(
        "symbols",
        [],
    )

    if not isinstance(
        symbols,
        list,
    ):
        raise RuntimeError(
            "Binance exchangeInfo: format symbols invalide"
        )

    discovered = 0

    rows: Dict[
        str,
        Dict[str, Any],
    ] = {}

    candidates: Dict[
        str,
        Dict[str, Any],
    ] = {}

    counts = {
        "non_trading": 0,
        "trading": 0,
        "not_spot": 0,
        "spot": 0,
        "quote_rejected": 0,
        "quote_ok": 0,
        "structural_rejected": 0,
        "candidate": 0,
        "ticker_missing": 0,
        "ticker_ok": 0,
        "volume_rejected": 0,
        "volume_ok": 0,
        "trades_rejected": 0,
        "trades_ok": 0,
        "price_rejected": 0,
        "price_ok": 0,
        "spread_rejected": 0,
        "spread_ok": 0,
        "age_rejected": 0,
        "age_ok": 0,
        "volatility_rejected": 0,
        "volatility_ok": 0,
        "depth_rejected": 0,
        "depth_ok": 0,
        "depth_failed": 0,
        "duplicate_rejected": 0,
        "unique_ok": 0,
        "cap_rejected": 0,
        "selected": 0,
    }

    # ========================================================
    # EXCHANGE INFO FILTERS
    # ========================================================

    for item in symbols:

        if not isinstance(
            item,
            dict,
        ):
            continue

        discovered += 1

        symbol = str(
            item.get(
                "symbol",
                "",
            )
        ).upper().strip()

        if not symbol:
            continue

        row: Dict[str, Any] = {
            "symbol": symbol,
            "base_asset": str(
                item.get(
                    "baseAsset",
                    "",
                )
                or ""
            ).upper(),
            "quote_asset": str(
                item.get(
                    "quoteAsset",
                    "",
                )
                or ""
            ).upper(),
            "exchange_status": str(
                item.get(
                    "status",
                    "",
                )
                or ""
            ),
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
            "final_status": "",
            "selected": "",
            "selected_quote": "",
        }

        rows[symbol] = row

        status = str(
            item.get(
                "status",
                "",
            )
        ).upper()

        if status != "TRADING":

            counts[
                "non_trading"
            ] += 1

            _binance_reject(
                row,
                "TRADING",
                f"status={status or 'UNKNOWN'}",
            )

            continue

        counts[
            "trading"
        ] += 1

        spot_allowed = item.get(
            "isSpotTradingAllowed"
        )

        permissions = item.get(
            "permissions",
            [],
        )

        permission_ok = (
            isinstance(
                permissions,
                list,
            )
            and (
                "SPOT"
                in {
                    str(x).upper()
                    for x in permissions
                }
            )
        )

        if spot_allowed is True:

            spot_ok = True

            spot_basis = (
                "isSpotTradingAllowed=True"
            )

        elif permission_ok:

            spot_ok = True

            spot_basis = (
                "permissions=SPOT"
            )

        elif spot_allowed is False:

            spot_ok = False

            spot_basis = (
                "isSpotTradingAllowed=False"
            )

        else:

            spot_ok = True

            spot_basis = (
                "champ Spot absent; "
                "compatibilité V4"
            )

        row[
            "spot_allowed"
        ] = str(
            spot_ok
        )

        row[
            "spot_basis"
        ] = spot_basis

        if not spot_ok:

            counts[
                "not_spot"
            ] += 1

            _binance_reject(
                row,
                "SPOT",
                spot_basis,
            )

            continue

        counts[
            "spot"
        ] += 1

        quote_asset = str(
            item.get(
                "quoteAsset",
                "",
            )
        ).upper()

        base_asset = str(
            item.get(
                "baseAsset",
                "",
            )
        ).upper()

        row[
            "quote_asset"
        ] = quote_asset

        row[
            "base_asset"
        ] = base_asset

        if (
            quote_asset
            not in BINANCE_ALLOWED_QUOTE_ASSETS
        ):

            counts[
                "quote_rejected"
            ] += 1

            _binance_reject(
                row,
                "quote",
                f"quote={quote_asset}",
            )

            continue

        counts[
            "quote_ok"
        ] += 1

        special, special_reason = (
            _binance_is_excluded_product(
                symbol,
                base_asset,
            )
        )

        stable = (
            base_asset
            in BINANCE_STABLECOIN_BASE_ASSETS
        )

        excluded_base = (
            base_asset
            in BINANCE_EXCLUDED_BASE_ASSETS
        )

        if stable:

            counts[
                "structural_rejected"
            ] += 1

            _binance_reject(
                row,
                "structure",
                "stablecoin_or_fiat_base",
            )

            continue

        if excluded_base:

            counts[
                "structural_rejected"
            ] += 1

            _binance_reject(
                row,
                "structure",
                "excluded_base_asset",
            )

            continue

        if special:

            counts[
                "structural_rejected"
            ] += 1

            _binance_reject(
                row,
                "structure",
                special_reason,
            )

            continue

        candidates[
            symbol
        ] = item

        counts[
            "candidate"
        ] += 1

    print(
        f"[Binance Universe] "
        f"endpoint={exchange_base} | "
        f"connus={discovered} | "
        f"TRADING={counts['trading']} | "
        f"non-TRADING={counts['non_trading']} | "
        f"SPOT={counts['spot']} | "
        f"non-SPOT={counts['not_spot']} | "
        f"quotes OK={counts['quote_ok']} | "
        f"quotes rejetées={counts['quote_rejected']} | "
        f"structure OK={counts['candidate']} | "
        f"structure rejetée={counts['structural_rejected']}"
    )

    # ========================================================
    # TICKER 24H
    # ========================================================

    print(
        "[Binance Universe] ticker 24h..."
    )

    tickers, ticker_base = (
        _binance_get_json(
            session,
            "/api/v3/ticker/24hr",
        )
    )

    if not isinstance(
        tickers,
        list,
    ):
        raise RuntimeError(
            "Binance ticker/24hr: format invalide"
        )

    ticker_by_symbol = {
        str(
            item.get(
                "symbol",
                "",
            )
        ).upper(): item
        for item in tickers
        if isinstance(
            item,
            dict,
        )
    }

    quick_candidates: List[
        Dict[str, Any]
    ] = []

    # ========================================================
    # FILTRES RAPIDES
    #
    # IMPORTANT :
    # PAS DE FILTRE VOLUME ICI.
    # ========================================================

    for symbol, info in candidates.items():

        row = rows[
            symbol
        ]

        ticker = ticker_by_symbol.get(
            symbol
        )

        if not ticker:

            counts[
                "ticker_missing"
            ] += 1

            _binance_reject(
                row,
                "ticker",
                "ticker_24h_missing",
            )

            continue

        counts[
            "ticker_ok"
        ] += 1

        quote_volume = _binance_float(
            ticker.get(
                "quoteVolume"
            )
        )

        count = int(
            _binance_float(
                ticker.get(
                    "count"
                )
            )
        )

        last_price = _binance_float(
            ticker.get(
                "lastPrice"
            )
        )

        bid = _binance_float(
            ticker.get(
                "bidPrice"
            )
        )

        ask = _binance_float(
            ticker.get(
                "askPrice"
            )
        )

        row.update({
            "quote_volume_24h": quote_volume,
            "trades_24h": count,
            "last_price": last_price,
            "bid_price": bid,
            "ask_price": ask,
        })

        # ----------------------------------------------------
        # TRADES
        # ----------------------------------------------------

        if count < BINANCE_MIN_TRADES_24H:

            counts[
                "trades_rejected"
            ] += 1

            _binance_reject(
                row,
                "trades",
                (
                    f"trades_24h={count}"
                    f"<{BINANCE_MIN_TRADES_24H}"
                ),
            )

            continue

        counts[
            "trades_ok"
        ] += 1

        # ----------------------------------------------------
        # PRIX
        # ----------------------------------------------------

        if (
            last_price <= 0
            or bid <= 0
            or ask <= 0
            or ask < bid
        ):

            counts[
                "price_rejected"
            ] += 1

            _binance_reject(
                row,
                "prix",
                "invalid_bid_ask_or_last_price",
            )

            continue

        counts[
            "price_ok"
        ] += 1

        # ----------------------------------------------------
        # SPREAD
        # ----------------------------------------------------

        mid = (
            bid + ask
        ) / 2.0

        spread_pct = (
            (
                ask - bid
            )
            / mid
        ) * 100.0 if mid > 0 else 999.0

        row[
            "spread_pct"
        ] = spread_pct

        if (
            spread_pct
            > BINANCE_MAX_SPREAD_PCT
        ):

            counts[
                "spread_rejected"
            ] += 1

            _binance_reject(
                row,
                "spread",
                (
                    f"spread_pct="
                    f"{spread_pct:.4f}"
                    f">{BINANCE_MAX_SPREAD_PCT:g}"
                ),
            )

            continue

        counts[
            "spread_ok"
        ] += 1

        # ----------------------------------------------------
        # ÂGE / VOLATILITÉ
        # ----------------------------------------------------

        high = _binance_float(
            ticker.get(
                "highPrice"
            ),
            last_price,
        )

        low = _binance_float(
            ticker.get(
                "lowPrice"
            ),
            last_price,
        )

        volatility_pct = (
            (
                high - low
            )
            / last_price
        ) * 100.0 if last_price > 0 else 0.0

        age_days = _binance_age_days(
            info.get(
                "onboardDate"
            )
        )

        row[
            "age_days"
        ] = age_days

        row[
            "volatility_pct"
        ] = volatility_pct

        if (
            age_days
            < BINANCE_MIN_UNIVERSE_AGE_DAYS
        ):

            counts[
                "age_rejected"
            ] += 1

            _binance_reject(
                row,
                "âge",
                (
                    f"age_days="
                    f"{age_days:.2f}"
                    f"<{BINANCE_MIN_UNIVERSE_AGE_DAYS:g}"
                ),
            )

            continue

        counts[
            "age_ok"
        ] += 1

        if (
            volatility_pct
            > BINANCE_MAX_UNIVERSE_VOLATILITY_PCT
        ):

            counts[
                "volatility_rejected"
            ] += 1

            _binance_reject(
                row,
                "volatilité",
                (
                    f"24h_range_pct="
                    f"{volatility_pct:.2f}"
                    f">{BINANCE_MAX_UNIVERSE_VOLATILITY_PCT:g}"
                ),
            )

            continue

        counts[
            "volatility_ok"
        ] += 1

        # ----------------------------------------------------
        # QUALITÉ PARTIELLE AVANT DEPTH
        # ----------------------------------------------------

        volume_quality = (
            _binance_volume_quality(
                quote_volume
            )
        )

        trade_quality = (
            _binance_trades_quality(
                count
            )
        )

        spread_quality = (
            _binance_spread_quality(
                spread_pct
            )
        )

        stability_quality = (
            _binance_stability_quality(
                age_days
            )
        )

        quick_candidates.append({
            "symbol": symbol,
            "quote_asset": info.get(
                "quoteAsset"
            ),
            "base_asset": info.get(
                "baseAsset"
            ),
            "quote_volume_24h": quote_volume,
            "trades_24h": count,
            "bid": bid,
            "ask": ask,
            "spread_pct": spread_pct,
            "volatility_pct": volatility_pct,
            "age_days": age_days,
            "stability": stability_quality,
            "onboard_date": info.get(
                "onboardDate"
            ),
            "volume_quality": volume_quality,
            "trade_liquidity": trade_quality,
            "spread_quality": spread_quality,
            "status": info.get(
                "status"
            ),
            "depth_score": 0.0,
            "depth_rejected": False,
            "depth_error": "",
        })

    print(
        "[Binance Universe] "
        "filtres rapides | "
        f"candidats structure={counts['candidate']} | "
        f"ticker OK={counts['ticker_ok']} | "
        f"trades OK={counts['trades_ok']} | "
        f"prix OK={counts['price_ok']} | "
        f"spread OK={counts['spread_ok']} | "
        f"âge OK={counts['age_ok']} | "
        f"volatilité OK={counts['volatility_ok']} | "
        f"volume volontairement reporté APRÈS depth"
    )

    # ========================================================
    # DEPTH AVANT VOLUME
    # ========================================================

    if quick_candidates:

        print(
            "[Binance Universe] "
            f"analyse profondeur carnet : "
            f"{len(quick_candidates)} paires | "
            f"workers={max(1, BINANCE_DEPTH_WORKERS)}"
        )

        workers = max(
            1,
            min(
                BINANCE_DEPTH_WORKERS,
                len(quick_candidates),
            ),
        )

        with ThreadPoolExecutor(
            max_workers=workers
        ) as executor:

            futures = {
                executor.submit(
                    _binance_fetch_depth,
                    item["symbol"],
                ): item
                for item in quick_candidates
            }

            for future in as_completed(
                futures
            ):

                item = futures[
                    future
                ]

                symbol = item[
                    "symbol"
                ]

                row = rows[
                    symbol
                ]

                try:

                    (
                        returned_symbol,
                        depth_payload,
                        depth_source,
                    ) = future.result()

                except Exception as exc:

                    counts[
                        "depth_failed"
                    ] += 1

                    item[
                        "depth_score"
                    ] = 0.0

                    item[
                        "depth_error"
                    ] = str(exc)

                    row[
                        "depth_score"
                    ] = 0.0

                    row[
                        "liquidity_score"
                    ] = None

                    row[
                        "liquidity_grade"
                    ] = "F"

                    _binance_reject(
                        row,
                        "profondeur",
                        f"depth_error:{exc}",
                    )

                    item[
                        "depth_rejected"
                    ] = True

                    continue

                mid = (
                    float(item["bid"])
                    + float(item["ask"])
                ) / 2.0

                metrics = (
                    _binance_depth_metrics(
                        depth_payload,
                        mid,
                    )
                    if depth_payload
                    else {}
                )

                score = (
                    _binance_depth_score(
                        metrics
                    )
                )

                item.update(
                    metrics
                )

                item[
                    "depth_score"
                ] = score

                item[
                    "depth_source"
                ] = depth_source

                row[
                    "depth_010_notional"
                ] = metrics.get(
                    "depth_010_notional",
                    0.0,
                )

                row[
                    "depth_025_notional"
                ] = metrics.get(
                    "depth_025_notional",
                    0.0,
                )

                row[
                    "depth_050_notional"
                ] = metrics.get(
                    "depth_050_notional",
                    0.0,
                )

                row[
                    "depth_score"
                ] = score

                depth025 = metrics.get(
                    "depth_025_notional",
                    0.0,
                )

                if (
                    not metrics
                    or depth025
                    < BINANCE_MIN_DEPTH_025_NOTIONAL
                ):

                    counts[
                        "depth_rejected"
                    ] += 1

                    item[
                        "depth_rejected"
                    ] = True

                    if not metrics:

                        reason = (
                            "depth_unavailable"
                        )

                    else:

                        reason = (
                            f"depth_025="
                            f"{depth025:.0f}"
                            f"<"
                            f"{BINANCE_MIN_DEPTH_025_NOTIONAL:g}"
                        )

                    _binance_reject(
                        row,
                        "profondeur",
                        reason,
                    )

                else:

                    counts[
                        "depth_ok"
                    ] += 1

                    item[
                        "depth_rejected"
                    ] = False

                # ------------------------------------------------
                # SCORE FINAL DE LIQUIDITÉ
                #
                # Il est calculé AVANT le filtre volume.
                # Cela permet d'auditer les <5 M$.
                # ------------------------------------------------

                liquidity_score = (
                    _binance_liquidity_score(
                        depth_score=score,
                        volume=item[
                            "quote_volume_24h"
                        ],
                        spread_pct=item[
                            "spread_pct"
                        ],
                        trades=item[
                            "trades_24h"
                        ],
                        age_days=item[
                            "age_days"
                        ],
                    )
                )

                item[
                    "liquidity_score"
                ] = liquidity_score

                item[
                    "liquidity_grade"
                ] = _binance_liquidity_grade(
                    liquidity_score
                )

                row[
                    "liquidity_score"
                ] = liquidity_score

                row[
                    "liquidity_grade"
                ] = _binance_liquidity_grade(
                    liquidity_score
                )

    # ========================================================
    # CANDIDATS APRÈS DEPTH
    # ========================================================

    depth_candidates = [
        item
        for item in quick_candidates
        if (
            not item.get(
                "depth_rejected",
                False,
            )
            and not rows[
                item["symbol"]
            ].get(
                "first_rejection_stage"
            )
        )
    ]

    print(
        f"[Binance Universe] "
        f"profondeur OK={counts['depth_ok']} | "
        f"rejetée={counts['depth_rejected']} | "
        f"erreurs={counts['depth_failed']}"
    )

    # ========================================================
    # VOLUME APRÈS DEPTH
    # ========================================================

    volume_candidates: List[
        Dict[str, Any]
    ] = []

    for item in depth_candidates:

        symbol = item[
            "symbol"
        ]

        row = rows[
            symbol
        ]

        volume = _binance_float(
            item.get(
                "quote_volume_24h"
            )
        )

        if (
            volume
            < BINANCE_MIN_QUOTE_VOLUME_24H
        ):

            counts[
                "volume_rejected"
            ] += 1

            _binance_reject(
                row,
                "volume",
                (
                    f"quote_volume_24h="
                    f"{volume:.0f}"
                    f"<"
                    f"{BINANCE_MIN_QUOTE_VOLUME_24H:g}"
                ),
            )

            continue

        counts[
            "volume_ok"
        ] += 1

        volume_candidates.append(
            item
        )

    print(
        "[Binance Universe] "
        f"volume après depth | "
        f"OK={counts['volume_ok']} | "
        f"rejetés={counts['volume_rejected']} | "
        f"seuil={BINANCE_MIN_QUOTE_VOLUME_24H:,.0f}$"
    )

    # ========================================================
    # QUALITÉ FINALE
    # ========================================================

    for item in volume_candidates:

        symbol = item[
            "symbol"
        ]

        row = rows[
            symbol
        ]

        score = _binance_liquidity_score(
            depth_score=item.get(
                "depth_score",
                0.0,
            ),
            volume=item.get(
                "quote_volume_24h",
                0.0,
            ),
            spread_pct=item.get(
                "spread_pct",
                0.0,
            ),
            trades=item.get(
                "trades_24h",
                0,
            ),
            age_days=item.get(
                "age_days",
                0.0,
            ),
        )

        grade = _binance_liquidity_grade(
            score
        )

        item[
            "liquidity_score"
        ] = score

        item[
            "liquidity_grade"
        ] = grade

        # Compatibilité avec les anciennes parties
        # du code qui utilisent universe_quality.
        item[
            "universe_quality"
        ] = score / 100.0

        row[
            "liquidity_score"
        ] = score

        row[
            "liquidity_grade"
        ] = grade

        row[
            "universe_quality"
        ] = score / 100.0

    # ========================================================
    # TRI AVANT DÉDUPLICATION
    # ========================================================

    volume_candidates.sort(
        key=lambda x: (
            x.get(
                "liquidity_score",
                0.0,
            ),
            x.get(
                "depth_score",
                0.0,
            ),
            x.get(
                "quote_volume_24h",
                0.0,
            ),
        ),
        reverse=True,
    )

    # ========================================================
    # DÉDUPLICATION PAR BASE
    # ========================================================

    quote_rank = {
        quote: index
        for index, quote
        in enumerate(
            BINANCE_QUOTE_PRIORITY
        )
    }

    best_by_base: Dict[
        str,
        Dict[str, Any],
    ] = {}

    duplicate_losers: List[
        Dict[str, Any]
    ] = []

    for item in volume_candidates:

        base = str(
            item.get(
                "base_asset",
                "",
            )
        ).upper()

        current = best_by_base.get(
            base
        )

        if current is None:

            best_by_base[
                base
            ] = item

            continue

        current_key = (
            current.get(
                "liquidity_score",
                0.0,
            ),
            current.get(
                "depth_score",
                0.0,
            ),
            current.get(
                "quote_volume_24h",
                0.0,
            ),
            -quote_rank.get(
                str(
                    current.get(
                        "quote_asset",
                        "",
                    )
                ).upper(),
                999,
            ),
        )

        new_key = (
            item.get(
                "liquidity_score",
                0.0,
            ),
            item.get(
                "depth_score",
                0.0,
            ),
            item.get(
                "quote_volume_24h",
                0.0,
            ),
            -quote_rank.get(
                str(
                    item.get(
                        "quote_asset",
                        "",
                    )
                ).upper(),
                999,
            ),
        )

        if new_key > current_key:

            duplicate_losers.append(
                current
            )

            best_by_base[
                base
            ] = item

        else:

            duplicate_losers.append(
                item
            )

    for loser in duplicate_losers:

        row = rows[
            loser["symbol"]
        ]

        base = str(
            loser.get(
                "base_asset",
                "",
            )
        ).upper()

        winner = (
            best_by_base.get(
                base,
                {},
            )
        )

        counts[
            "duplicate_rejected"
        ] += 1

        _binance_reject(
            row,
            "déduplication",
            (
                "duplicate_base;"
                f"winner={winner.get('symbol', '')}"
            ),
        )

    ranked_unique = list(
        best_by_base.values()
    )

    counts[
        "unique_ok"
    ] = len(
        ranked_unique
    )

    print(
        "[Binance Universe] "
        f"déduplication base_asset | "
        f"avant={len(volume_candidates)} | "
        f"après={len(ranked_unique)} | "
        f"paires supprimées={counts['duplicate_rejected']}"
    )

    # ========================================================
    # TRI FINAL
    # ========================================================

    ranked_unique.sort(
        key=lambda x: (
            x.get(
                "liquidity_score",
                0.0,
            ),
            x.get(
                "depth_score",
                0.0,
            ),
            x.get(
                "quote_volume_24h",
                0.0,
            ),
        ),
        reverse=True,
    )

    # ========================================================
    # PLAFOND ÉVENTUEL
    # ========================================================

    if limit is None:

        selected = ranked_unique
        dropped_by_cap: List[
            Dict[str, Any]
        ] = []

    else:

        selected = (
            ranked_unique[
                :limit
            ]
        )

        dropped_by_cap = (
            ranked_unique[
                limit:
            ]
        )

        counts[
            "cap_rejected"
        ] = len(
            dropped_by_cap
        )

        for item in dropped_by_cap:

            row = rows[
                item["symbol"]
            ]

            _binance_reject(
                row,
                "plafond univers",
                f"rank>{limit}",
            )

    counts[
        "selected"
    ] = len(
        selected
    )

    # ========================================================
    # STATUT FINAL
    # ========================================================

    for item in selected:

        symbol = item[
            "symbol"
        ]

        row = rows[
            symbol
        ]

        row[
            "final_status"
        ] = "selected"

        row[
            "selected"
        ] = "True"

        row[
            "selected_quote"
        ] = str(
            item.get(
                "quote_asset",
                "",
            )
        )

        row[
            "universe_quality"
        ] = item.get(
            "universe_quality",
            item.get(
                "liquidity_score",
                0.0,
            ) / 100.0,
        )

    for symbol, row in rows.items():

        if not row.get(
            "final_status"
        ):

            row[
                "final_status"
            ] = "not_selected"

    # ========================================================
    # AUDIT COMPLET DANS LES LOGS
    # ========================================================

    _binance_print_rejection_audit(
        rows,
        counts,
        discovered,
    )

    # ========================================================
    # UNIVERS DYNAMIQUE
    # ========================================================

    selected_symbols = [
        item[
            "symbol"
        ]
        for item in selected
    ]

    _DYNAMIC_CRYPTO_SYMBOLS = set(
        selected_symbols
    )

    _DYNAMIC_CRYPTO_METADATA = {
        item[
            "symbol"
        ]: item
        for item in selected
    }

    CRYPTO = list(
        selected_symbols
    )

    for symbol in selected_symbols:

        existing = CRYPTO_SYMBOLS.get(
            symbol
        )

        if existing is None:

            CRYPTO_SYMBOLS[
                symbol
            ] = {
                "binance": symbol
            }

        else:

            existing[
                "binance"
            ] = symbol

    ASSET_GROUPS[
        "crypto"
    ] = CRYPTO

    # ========================================================
    # RÉSUMÉ FINAL
    # ========================================================

    print("")
    print(
        "=" * 100
    )

    print(
        "[Binance Universe] "
        f"sélection finale="
        f"{len(selected_symbols)} "
        f"| candidats uniques="
        f"{len(ranked_unique)} "
        f"| paires après depth/volume="
        f"{len(volume_candidates)} "
        f"| paires après filtres rapides="
        f"{len(quick_candidates)}"
    )

    print(
        f"[Binance Universe] "
        f"ticker_endpoint={ticker_base}"
    )

    print(
        "IMPORTANT : aucun CSV d'audit n'est généré."
    )

    print(
        "=" * 100
    )

    # ========================================================
    # TOP 10
    # ========================================================

    print(
        "[Binance Universe] TOP 10 SÉLECTION"
    )

    for rank, item in enumerate(
        selected[
            :10
        ],
        start=1,
    ):

        print(
            f"  #{rank:02d} "
            f"{item['symbol']:<14} "
            f"grade={item.get('liquidity_grade', 'F')} "
            f"score={item.get('liquidity_score', 0.0):.1f}/100 "
            f"vol24h="
            f"{item['quote_volume_24h']:,.0f} "
            f"spread="
            f"{item['spread_pct']:.3f}% "
            f"trades="
            f"{item['trades_24h']:,} "
            f"±0.10%="
            f"${item.get('depth_010_notional', 0.0):,.0f} "
            f"±0.25%="
            f"${item.get('depth_025_notional', 0.0):,.0f} "
            f"±0.50%="
            f"${item.get('depth_050_notional', 0.0):,.0f}"
        )

    return selected_symbols


# ============================================================
# MÉTADONNÉES BINANCE
# ============================================================

def get_binance_universe_metadata() -> Dict[
    str,
    Dict[str, Any],
]:

    return dict(
        _DYNAMIC_CRYPTO_METADATA
    )


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

CRYPTO_SYMBOLS: Dict[
    str,
    Dict[str, Optional[str]],
] = {

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

FOREX_SYMBOLS: Dict[
    str,
    Dict[str, Optional[str]],
] = {

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

    STOCK_SYMBOLS[
        _symbol
    ] = {
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

ASSET_GROUPS: Dict[
    str,
    List[str],
] = {
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

ASSET_GROUPS[
    "stocks"
] = STOCKS

ASSET_GROUPS[
    "indices"
] = INDICES

ASSET_GROUPS[
    "commodities"
] = COMMODITIES

ASSET_GROUPS[
    "commodities_yahoo"
] = COMMODITIES_YAHOO_ONLY


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

    if (
        symbol in CRYPTO
        or symbol in _DYNAMIC_CRYPTO_SYMBOLS
    ):
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

DISPLAY_NAMES: Dict[
    str,
    str,
] = {

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
) -> Dict[
    str,
    Optional[str],
]:

    if (
        asset in CRYPTO
        or asset in _DYNAMIC_CRYPTO_SYMBOLS
    ):

        mapping = dict(
            CRYPTO_SYMBOLS.get(
                asset,
                {
                    "binance": asset
                },
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

    mapping[
        "symbol"
    ] = asset

    return mapping


# ============================================================
# VALIDATION
# ============================================================

def validate_assets() -> Dict[
    str,
    object,
]:

    static_assets = []

    for group in [
        FOREX,
        STOCKS,
        INDICES,
        COMMODITIES,
        COMMODITIES_YAHOO_ONLY,
    ]:

        for asset in group:

            if asset not in static_assets:
                static_assets.append(
                    asset
                )

    missing_maps = []

    for asset in static_assets:

        try:

            if not get_symbol_map(
                asset
            ):
                missing_maps.append(
                    asset
                )

        except Exception:

            missing_maps.append(
                asset
            )

    return {
        "static_total": len(
            static_assets
        ),
        "missing_maps": missing_maps,
        "valid": not missing_maps,
    }


_ASSET_VALIDATION = (
    validate_assets()
)


if not _ASSET_VALIDATION[
    "valid"
]:

    raise RuntimeError(
        "Univers statique invalide : "
        + str(
            _ASSET_VALIDATION
        )
    )
