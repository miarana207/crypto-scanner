"""
V4.3 — Univers d'actifs.

Principe Binance :
- l'univers SPOT TRADING est découvert dynamiquement depuis exchangeInfo;
- aucun filtre économique (quote, volume, spread, nombre de trades, âge,
  volatilité, stablecoin, token à effet de levier, etc.) ne supprime un actif
  du recensement;
- les critères de qualité/liquidité servent ensuite à classer les marchés et
  à construire l'univers de scan, avec audit complet des rejets.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

BINANCE_UNIVERSE_URL = os.getenv(
    "BINANCE_UNIVERSE_URL",
    os.getenv("BINANCE_DATA_URL", "https://data-api.binance.vision"),
).rstrip("/")

BINANCE_FALLBACK_URLS = [
    u.rstrip("/")
    for u in os.getenv(
        "BINANCE_UNIVERSE_FALLBACK_URLS",
        "https://data-api.binance.vision,https://data-api.binance.com,https://api.binance.com",
    ).split(",")
    if u.strip()
]

BINANCE_TIMEOUT = int(os.getenv("BINANCE_UNIVERSE_TIMEOUT", "20"))
BINANCE_DEPTH_LIMIT = int(os.getenv("BINANCE_DEPTH_LIMIT", "100"))
BINANCE_DEPTH_WORKERS = int(os.getenv("BINANCE_DEPTH_WORKERS", "12"))

BINANCE_MIN_DEPTH_NOTIONAL = float(
    os.getenv("BINANCE_MIN_DEPTH_NOTIONAL", "0")
)
BINANCE_MIN_QUOTE_VOLUME = float(
    os.getenv("BINANCE_MIN_QUOTE_VOLUME", "0")
)
BINANCE_MIN_TRADES = int(
    os.getenv("BINANCE_MIN_TRADES", "0")
)
BINANCE_MAX_SPREAD_PCT = float(
    os.getenv("BINANCE_MAX_SPREAD_PCT", "5")
)
BINANCE_MAX_24H_RANGE_PCT = float(
    os.getenv("BINANCE_MAX_24H_RANGE_PCT", "1000")
)
BINANCE_MIN_AGE_DAYS = float(
    os.getenv("BINANCE_MIN_AGE_DAYS", "0")
)
BINANCE_MAX_UNIVERSE = int(
    os.getenv("BINANCE_MAX_UNIVERSE", "0")
)

FOREX = [
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CHF",
    "USD/CAD", "NZD/USD", "EUR/GBP", "EUR/JPY", "GBP/JPY",
]

STOCKS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    "AVGO", "AMD", "QCOM", "ORCL", "ADBE", "CRM", "NFLX",
    "JPM", "BAC", "GS", "V", "MA", "UNH", "JNJ", "PFE",
    "XOM", "CVX", "WMT", "COST", "PG", "KO", "PEP", "HD",
    "DIS", "NKE", "IBM", "INTC", "CSCO",
]

INDICES = [
    "^GSPC", "^IXIC", "^DJI", "^RUT", "^FCHI", "^GDAXI",
    "^FTSE", "^N225", "^HSI", "^STOXX50E",
]

COMMODITIES = [
    "GC=F", "SI=F", "CL=F", "BZ=F", "NG=F", "HG=F",
]

COMMODITIES_YAHOO_ONLY = [
    "ZC=F", "ZS=F", "ZW=F", "KC=F", "SB=F",
]

CRYPTO: list[str] = []
CRYPTO_SYMBOLS: dict[str, dict[str, str]] = {}

_DYNAMIC_CRYPTO_SYMBOLS: list[str] = []
_DYNAMIC_CRYPTO_METADATA: dict[str, dict[str, Any]] = {}
_BINANCE_AUDIT: dict[str, Any] = {}

ASSET_GROUPS: dict[str, list[str]] = {
    "crypto": CRYPTO,
    "forex": FOREX,
    "stock": STOCKS,
    "stocks": STOCKS,
    "index": INDICES,
    "indices": INDICES,
    "commodity": COMMODITIES,
    "commodities": COMMODITIES,
    "commodities_yahoo_only": COMMODITIES_YAHOO_ONLY,
}


def _get_json(
    path: str,
    params: dict[str, Any] | None = None,
) -> Any:

    urls: list[str] = []

    for base in [
        BINANCE_UNIVERSE_URL,
        *BINANCE_FALLBACK_URLS,
    ]:

        if base and base not in urls:
            urls.append(base)

    last_error: Exception | None = None

    for base in urls:

        try:

            response = requests.get(
                f"{base}{path}",
                params=params,
                timeout=BINANCE_TIMEOUT,
            )

            response.raise_for_status()

            return response.json()

        except Exception as exc:

            last_error = exc

    raise RuntimeError(
        f"Binance API indisponible: {last_error}"
    )


def _has_spot_permission(
    symbol: dict[str, Any],
) -> bool:

    permissions = symbol.get(
        "permissions"
    ) or []

    if isinstance(permissions, str):
        permissions = [permissions]

    if "SPOT" in permissions:
        return True

    for group in symbol.get(
        "permissionSets"
    ) or []:

        if isinstance(group, str):
            group = [group]

        if "SPOT" in (
            group or []
        ):

            return True

    return False


def _ticker_map() -> dict[str, dict[str, Any]]:

    raw = _get_json(
        "/api/v3/ticker/24hr"
    )

    return {
        str(item.get("symbol")): item
        for item in raw
        if isinstance(item, dict)
        and item.get("symbol")
    }


def _book_map() -> dict[str, dict[str, Any]]:

    raw = _get_json(
        "/api/v3/ticker/bookTicker"
    )

    return {
        str(item.get("symbol")): item
        for item in raw
        if isinstance(item, dict)
        and item.get("symbol")
    }


def _depth_one(
    symbol: str,
) -> tuple[
    str,
    dict[str, float] | None,
    str | None,
]:

    try:

        data = _get_json(
            "/api/v3/depth",
            {
                "symbol": symbol,
                "limit": BINANCE_DEPTH_LIMIT,
            },
        )

        bids = [
            (float(price), float(qty))
            for price, qty in data.get(
                "bids",
                [],
            )
        ]

        asks = [
            (float(price), float(qty))
            for price, qty in data.get(
                "asks",
                [],
            )
        ]

        best_bid = max(
            (price for price, _ in bids),
            default=0.0,
        )

        best_ask = min(
            (price for price, _ in asks),
            default=0.0,
        )

        mid = (
            (best_bid + best_ask) / 2
            if best_bid and best_ask
            else 0.0
        )

        if not mid:
            return (
                symbol,
                None,
                "depth_invalid",
            )

        metrics: dict[str, float] = {
            "bid_ask_spread_pct": (
                (best_ask - best_bid)
                / mid
                * 100.0
            )
        }

        for pct in (
            0.10,
            0.25,
            0.50,
        ):

            lower = mid * (
                1 - pct / 100
            )

            upper = mid * (
                1 + pct / 100
            )

            metrics[
                f"depth_{pct:.2f}_pct"
            ] = (
                sum(
                    price * qty
                    for price, qty in bids
                    if price >= lower
                )
                +
                sum(
                    price * qty
                    for price, qty in asks
                    if price <= upper
                )
            )

        return (
            symbol,
            metrics,
            None,
        )

    except Exception as exc:

        return (
            symbol,
            None,
            str(exc),
        )


def _liquidity_score(
    metrics: dict[str, Any],
) -> float:

    volume = float(
        metrics.get(
            "quote_volume",
            0,
        )
        or 0
    )

    trades = float(
        metrics.get(
            "trades",
            0,
        )
        or 0
    )

    spread = float(
        metrics.get(
            "spread_pct",
            999,
        )
        or 999
    )

    depth = float(
        metrics.get(
            "depth_025_pct",
            0,
        )
        or 0
    )

    volume_score = min(
        100.0,
        (
            max(volume, 0.0)
            / 5_000_000
        ) ** 0.5
        * 100.0,
    )

    trades_score = min(
        100.0,
        (
            max(trades, 0.0)
            / 5_000
        ) ** 0.5
        * 100.0,
    )

    spread_score = (
        100.0
        if spread <= 0
        else max(
            0.0,
            min(
                100.0,
                100.0
                * (
                    1.0
                    - spread / 1.0
                ),
            ),
        )
    )

    depth_score = min(
        100.0,
        (
            max(depth, 0.0)
            / 250_000
        ) ** 0.5
        * 100.0,
    )

    return round(
        0.30 * volume_score
        + 0.20 * trades_score
        + 0.20 * spread_score
        + 0.30 * depth_score,
        2,
    )


def build_binance_universe() -> list[str]:
    """
    Construit dynamiquement l'univers SPOT/TRADING Binance.

    Aucun seuil économique ne supprime un actif du recensement.
    """

    global CRYPTO
    global _DYNAMIC_CRYPTO_SYMBOLS
    global _DYNAMIC_CRYPTO_METADATA
    global _BINANCE_AUDIT

    info = _get_json(
        "/api/v3/exchangeInfo",
        {
            "showPermissionSets": "true",
        },
    )

    symbols = (
        info.get(
            "symbols",
            [],
        )
        if isinstance(info, dict)
        else []
    )

    tradable = [
        symbol
        for symbol in symbols
        if (
            isinstance(symbol, dict)
            and symbol.get("status")
            == "TRADING"
            and _has_spot_permission(symbol)
        )
    ]

    try:
        tickers = _ticker_map()
    except Exception:
        tickers = {}

    try:
        books = _book_map()
    except Exception:
        books = {}

    now_ms = int(
        time.time() * 1000
    )

    audit_rows: list[
        dict[str, Any]
    ] = []

    for symbol_info in tradable:

        symbol = str(
            symbol_info["symbol"]
        )

        ticker = tickers.get(
            symbol,
            {},
        )

        book = books.get(
            symbol,
            {},
        )

        try:

            price = float(
                ticker.get(
                    "lastPrice"
                )
                or 0
            )

            quote_volume = float(
                ticker.get(
                    "quoteVolume"
                )
                or 0
            )

            trades = int(
                float(
                    ticker.get(
                        "count"
                    )
                    or 0
                )
            )

            high = float(
                ticker.get(
                    "highPrice"
                )
                or 0
            )

            low = float(
                ticker.get(
                    "lowPrice"
                )
                or 0
            )

            bid = float(
                book.get(
                    "bidPrice"
                )
                or 0
            )

            ask = float(
                book.get(
                    "askPrice"
                )
                or 0
            )

            spread = (
                ask / bid - 1.0
                if bid > 0
                else 999.0
            )

            spread_pct = (
                spread * 100.0
                if spread < 100
                else 999.0
            )

            open_time = int(
                float(
                    ticker.get(
                        "openTime"
                    )
                    or now_ms
                )
            )

            age_days = max(
                0.0,
                (
                    now_ms
                    - open_time
                )
                / 86_400_000,
            )

            range_pct = (
                (
                    high - low
                )
                / low
                * 100.0
                if low > 0
                else 999.0
            )

        except Exception:

            price = 0.0
            quote_volume = 0.0
            trades = 0
            spread_pct = 999.0
            age_days = 0.0
            range_pct = 999.0

        audit_rows.append(
            {
                "symbol": symbol,
                "base_asset": symbol_info.get(
                    "baseAsset"
                ),
                "quote_asset": symbol_info.get(
                    "quoteAsset"
                ),
                "status": symbol_info.get(
                    "status"
                ),
                "spot": True,
                "price": price,
                "quote_volume_24h": quote_volume,
                "trades_24h": trades,
                "spread_pct": spread_pct,
                "age_days": age_days,
                "range_24h_pct": range_pct,
                "ticker_available": bool(
                    ticker
                ),
                "exchange_info": symbol_info,
            }
        )

    candidates = [
        row["symbol"]
        for row in audit_rows
        if row["ticker_available"]
    ]

    depth_map: dict[
        str,
        dict[str, float] | None,
    ] = {}

    depth_errors: dict[
        str,
        str,
    ] = {}

    with ThreadPoolExecutor(
        max_workers=max(
            1,
            BINANCE_DEPTH_WORKERS,
        )
    ) as pool:

        futures = {
            pool.submit(
                _depth_one,
                symbol,
            ): symbol
            for symbol in candidates
        }

        for future in as_completed(
            futures
        ):

            symbol, metrics, error = (
                future.result()
            )

            depth_map[
                symbol
            ] = metrics

            if error:
                depth_errors[
                    symbol
                ] = error

    for row in audit_rows:

        depth = (
            depth_map.get(
                row["symbol"]
            )
            or {}
        )

        row[
            "depth_010_pct"
        ] = float(
            depth.get(
                "depth_0.10_pct",
                0,
            )
        )

        row[
            "depth_025_pct"
        ] = float(
            depth.get(
                "depth_0.25_pct",
                0,
            )
        )

        row[
            "depth_050_pct"
        ] = float(
            depth.get(
                "depth_0.50_pct",
                0,
            )
        )

        row[
            "depth_error"
        ] = depth_errors.get(
            row["symbol"]
        )

        row[
            "liquidity_score"
        ] = _liquidity_score(
            {
                "quote_volume":
                    row[
                        "quote_volume_24h"
                    ],
                "trades":
                    row[
                        "trades_24h"
                    ],
                "spread_pct":
                    row[
                        "spread_pct"
                    ],
                "depth_025_pct":
                    row[
                        "depth_025_pct"
                    ],
            }
        )

        reasons: list[str] = []

        if not row[
            "ticker_available"
        ]:
            reasons.append(
                "ticker_missing"
            )

        if row["price"] <= 0:
            reasons.append(
                "invalid_price"
            )

        if (
            BINANCE_MIN_TRADES
            and row[
                "trades_24h"
            ]
            < BINANCE_MIN_TRADES
        ):
            reasons.append(
                "trades_below_threshold"
            )

        if (
            BINANCE_MIN_QUOTE_VOLUME
            and row[
                "quote_volume_24h"
            ]
            < BINANCE_MIN_QUOTE_VOLUME
        ):
            reasons.append(
                "quote_volume_below_threshold"
            )

        if (
            BINANCE_MAX_SPREAD_PCT
            < 999
            and row["spread_pct"]
            > BINANCE_MAX_SPREAD_PCT
        ):
            reasons.append(
                "spread_above_threshold"
            )

        if (
            BINANCE_MAX_24H_RANGE_PCT
            < 999
            and row["range_24h_pct"]
            > BINANCE_MAX_24H_RANGE_PCT
        ):
            reasons.append(
                "range_above_threshold"
            )

        if (
            BINANCE_MIN_AGE_DAYS
            and row["age_days"]
            < BINANCE_MIN_AGE_DAYS
        ):
            reasons.append(
                "age_below_threshold"
            )

        if (
            BINANCE_MIN_DEPTH_NOTIONAL
            and row[
                "depth_025_pct"
            ]
            < BINANCE_MIN_DEPTH_NOTIONAL
        ):
            reasons.append(
                "depth_below_threshold"
            )

        row[
            "selection_reasons"
        ] = reasons

        row[
            "analysis_eligible"
        ] = (
            row[
                "ticker_available"
            ]
            and row["price"] > 0
        )

        threshold_reasons = [
            reason
            for reason in reasons
            if (
                reason.endswith(
                    "_below_threshold"
                )
                or reason.endswith(
                    "_above_threshold"
                )
            )
        ]

        row[
            "false_exclusion_risk"
        ] = (
            "HIGH"
            if (
                threshold_reasons
                and len(
                    threshold_reasons
                ) == 1
                and row[
                    "liquidity_score"
                ] >= 70
            )
            else "MEDIUM"
            if threshold_reasons
            else "LOW"
        )

    audit_rows.sort(
        key=lambda row: (
            row[
                "analysis_eligible"
            ],
            row[
                "liquidity_score"
            ],
            row[
                "quote_volume_24h"
            ],
        ),
        reverse=True,
    )

    selected = [
        row
        for row in audit_rows
        if row[
            "analysis_eligible"
        ]
    ]

    if (
        BINANCE_MAX_UNIVERSE
        > 0
    ):
        selected = selected[
            :BINANCE_MAX_UNIVERSE
        ]

    selected_symbols = [
        row["symbol"]
        for row in selected
    ]

    selected_set = set(
        selected_symbols
    )

    for row in audit_rows:

        if (
            row["symbol"]
            not in selected_set
            and not row[
                "selection_reasons"
            ]
        ):

            row[
                "selection_reasons"
            ] = (
                ["operational_cap"]
                if BINANCE_MAX_UNIVERSE
                else ["not_selected"]
            )

        row[
            "selected_for_scan"
        ] = (
            row["symbol"]
            in selected_set
        )

    CRYPTO = selected_symbols

    _DYNAMIC_CRYPTO_SYMBOLS = list(
        selected_symbols
    )

    _DYNAMIC_CRYPTO_METADATA = {
        row["symbol"]: {
            key: value
            for key, value
            in row.items()
            if key != "exchange_info"
        }
        for row in audit_rows
    }

    _BINANCE_AUDIT = {
        "exchange_info_symbols":
            len(symbols),
        "spot_trading_symbols":
            len(tradable),
        "ticker_available":
            sum(
                bool(
                    row[
                        "ticker_available"
                    ]
                )
                for row in audit_rows
            ),
        "analysis_eligible":
            sum(
                bool(
                    row[
                        "analysis_eligible"
                    ]
                )
                for row in audit_rows
            ),
        "selected_for_scan":
            len(selected_symbols),
        "rows":
            audit_rows,
        "selection_thresholds": {
            "min_quote_volume":
                BINANCE_MIN_QUOTE_VOLUME,
            "min_trades":
                BINANCE_MIN_TRADES,
            "max_spread_pct":
                BINANCE_MAX_SPREAD_PCT,
            "max_24h_range_pct":
                BINANCE_MAX_24H_RANGE_PCT,
            "min_age_days":
                BINANCE_MIN_AGE_DAYS,
            "min_depth_notional":
                BINANCE_MIN_DEPTH_NOTIONAL,
            "max_universe":
                BINANCE_MAX_UNIVERSE,
        },
    }

    ASSET_GROUPS[
        "crypto"
    ] = CRYPTO

    return list(
        CRYPTO
    )


def get_binance_universe_metadata():
    return {
        key: dict(value)
        for key, value
        in _DYNAMIC_CRYPTO_METADATA.items()
    }


def get_binance_universe_audit():
    return {
        "exchange_info_symbols":
            _BINANCE_AUDIT.get(
                "exchange_info_symbols",
                0,
            ),
        "spot_trading_symbols":
            _BINANCE_AUDIT.get(
                "spot_trading_symbols",
                0,
            ),
        "ticker_available":
            _BINANCE_AUDIT.get(
                "ticker_available",
                0,
            ),
        "analysis_eligible":
            _BINANCE_AUDIT.get(
                "analysis_eligible",
                0,
            ),
        "selected_for_scan":
            _BINANCE_AUDIT.get(
                "selected_for_scan",
                0,
            ),
        "selection_thresholds":
            dict(
                _BINANCE_AUDIT.get(
                    "selection_thresholds",
                    {},
                )
            ),
        "rows": [
            dict(row)
            for row
            in _BINANCE_AUDIT.get(
                "rows",
                [],
            )
        ],
    }


def get_asset_type(
    asset: str,
) -> str:

    if asset in _DYNAMIC_CRYPTO_SYMBOLS:
        return "crypto"

    if asset in FOREX:
        return "forex"

    if asset in STOCKS:
        return "stock"

    if asset in INDICES:
        return "index"

    if (
        asset in COMMODITIES
        or asset
        in COMMODITIES_YAHOO_ONLY
    ):
        return "commodity"

    return (
        "crypto"
        if asset in CRYPTO
        else "unknown"
    )


def get_symbol_map(
    asset: str,
) -> dict[str, str]:

    if asset in _DYNAMIC_CRYPTO_SYMBOLS:

        return {
            "binance": asset,
            "symbol": asset,
        }

    if asset in CRYPTO_SYMBOLS:

        return {
            **CRYPTO_SYMBOLS[
                asset
            ],
            "symbol": asset,
        }

    if asset in FOREX:

        td_symbol = asset.replace(
            "/",
            "",
        )

        return {
            "yahoo":
                asset.replace(
                    "/",
                    "",
                )
                + "=X",
            "twelve_data":
                td_symbol,
            "finnhub":
                td_symbol,
            "symbol":
                asset,
        }

    if asset in STOCKS:

        return {
            "yahoo": asset,
            "twelve_data": asset,
            "finnhub": asset,
            "symbol": asset,
        }

    if asset in INDICES:

        return {
            "yahoo": asset,
            "twelve_data": asset,
            "finnhub": asset,
            "symbol": asset,
        }

    if (
        asset in COMMODITIES
        or asset
        in COMMODITIES_YAHOO_ONLY
    ):

        return {
            "yahoo": asset,
            "twelve_data": asset,
            "symbol": asset,
        }

    return {
        "symbol": asset
    }


def get_provider_symbol(
    asset: str,
    provider: str,
) -> str | None:

    return get_symbol_map(
        asset
    ).get(provider)


def get_symbol(
    asset: str,
) -> str:
    return asset


def get_asset_display_name(
    asset: str,
) -> str:
    return asset


def all_assets() -> list[str]:

    output: list[str] = []

    for group in (
        CRYPTO,
        FOREX,
        STOCKS,
        INDICES,
        COMMODITIES,
        COMMODITIES_YAHOO_ONLY,
    ):

        for asset in group:

            if asset not in output:
                output.append(asset)

    return output


def asset_count() -> int:
    return len(
        all_assets()
    )


def validate_assets() -> list[str]:

    return [
        asset
        for asset in all_assets()
        if get_asset_type(
            asset
        )
        == "unknown"
    ]


ASSET_COUNTS = {
    "crypto": 0,
    "forex": len(FOREX),
    "stock": len(STOCKS),
    "index": len(INDICES),
    "commodity":
        len(COMMODITIES)
        + len(
            COMMODITIES_YAHOO_ONLY
        ),
}


__all__ = [
    "ASSET_GROUPS",
    "ASSET_COUNTS",
    "CRYPTO",
    "FOREX",
    "STOCKS",
    "INDICES",
    "COMMODITIES",
    "COMMODITIES_YAHOO_ONLY",
    "CRYPTO_SYMBOLS",
    "all_assets",
    "asset_count",
    "get_asset_type",
    "get_symbol",
    "get_provider_symbol",
    "get_symbol_map",
    "get_asset_display_name",
    "validate_assets",
    "build_binance_universe",
    "get_binance_universe_metadata",
    "get_binance_universe_audit",
]
