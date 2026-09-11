"""
V4.2 — Scanner multi-actifs.

Responsabilités :
- nettoyage des données
- validation temporelle
- calcul des indicateurs
- détection breakout
- scoring
- génération LONG / SHORT
- validation RR
- gestion du volume selon sa disponibilité
"""

from __future__ import annotations

from datetime import timezone
from typing import Any, Dict, Optional

import pandas as pd

from backtest import (
    MIN_RR,
    get_volume_status,
    indicators,
    score,
    signal_quality,
)


# ============================================================
# CONFIGURATION
# ============================================================

MIN_CANDLES = 120

# Une donnée légèrement future peut provenir d'un décalage
# de publication. Au-delà de cette tolérance, on rejette.
FUTURE_TOLERANCE_MINUTES = 2.0

# Au-delà de cette durée, la donnée est considérée comme
# trop ancienne pour produire un signal fiable.
MAX_AGE_MINUTES = 180.0

# Breakout
BREAKOUT_LOOKBACK = 20

# Risk / Reward
MIN_REQUIRED_RR = MIN_RR


# ============================================================
# UTILITAIRES
# ============================================================

def _normalize_datetime(
    values: Any,
) -> pd.Series:
    """
    Force les timestamps au type exact :

        datetime64[ns, UTC]

    Cela évite notamment les incompatibilités pandas
    datetime64[ms, UTC] / datetime64[us, UTC].
    """

    s = pd.to_datetime(
        values,
        utc=True,
        errors="coerce",
    )

    try:
        s = s.astype(
            "datetime64[ns, UTC]"
        )
    except (TypeError, ValueError):
        s = pd.Series(s).dt.as_unit("ns")

    return pd.Series(s)


def _now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(
        tz="UTC"
    )


def _safe_float(
    value: Any,
) -> Optional[float]:

    try:
        if value is None:
            return None

        value = float(value)

        if pd.isna(value):
            return None

        return value

    except (TypeError, ValueError):
        return None


# ============================================================
# NETTOYAGE
# ============================================================

def _clean_dataframe(
    df: pd.DataFrame,
) -> pd.DataFrame:

    if df is None:
        raise ValueError(
            "DataFrame absent"
        )

    if not isinstance(
        df,
        pd.DataFrame,
    ):
        raise ValueError(
            "Les données doivent être un DataFrame"
        )

    if df.empty:
        raise ValueError(
            "DataFrame vide"
        )

    data = df.copy()

    required = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
    ]

    missing = [
        col
        for col in required
        if col not in data.columns
    ]

    if missing:
        raise ValueError(
            f"Colonnes manquantes : {missing}"
        )

    if "volume" not in data.columns:
        data["volume"] = pd.NA

    # --------------------------------------------------------
    # Timestamp
    # --------------------------------------------------------

    data["open_time"] = _normalize_datetime(
        data["open_time"]
    )

    data = data.dropna(
        subset=["open_time"]
    )

    # --------------------------------------------------------
    # Numérique
    # --------------------------------------------------------

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    for column in numeric_columns:
        data[column] = pd.to_numeric(
            data[column],
            errors="coerce",
        )

    data = data.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    )

    # --------------------------------------------------------
    # OHLC valide
    # --------------------------------------------------------

    invalid_ohlc = (
        (data["high"] < data["low"])
        | (data["high"] < data["open"])
        | (data["high"] < data["close"])
        | (data["low"] > data["open"])
        | (data["low"] > data["close"])
    )

    data = data.loc[
        ~invalid_ohlc
    ].copy()

    # Prix strictement positifs.
    data = data.loc[
        (data["open"] > 0)
        & (data["high"] > 0)
        & (data["low"] > 0)
        & (data["close"] > 0)
    ].copy()

    # --------------------------------------------------------
    # Tri + dédoublonnage
    # --------------------------------------------------------

    data = (
        data
        .sort_values("open_time")
        .drop_duplicates(
            subset=["open_time"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    if data.empty:
        raise ValueError(
            "Aucune bougie valide après nettoyage"
        )

    # Réaffirme le type exact après tri/dédoublonnage.
    data["open_time"] = _normalize_datetime(
        data["open_time"]
    )

    return data


# ============================================================
# VALIDATION TEMPORELLE
# ============================================================

def _validate_time(
    data: pd.DataFrame,
) -> pd.DataFrame:

    if data.empty:
        raise ValueError(
            "Aucune donnée temporelle"
        )

    now = _now_utc()

    latest = data["open_time"].iloc[-1]

    if pd.isna(latest):
        raise ValueError(
            "Dernier timestamp invalide"
        )

    future_limit = (
        now
        + pd.Timedelta(
            minutes=FUTURE_TOLERANCE_MINUTES
        )
    )

    if latest > future_limit:
        raise ValueError(
            "timestamp futur détecté"
        )

    # Retire les bougies franchement futures.
    data = data.loc[
        data["open_time"]
        <= future_limit
    ].copy()

    if data.empty:
        raise ValueError(
            "Toutes les bougies sont futures"
        )

    data = (
        data
        .sort_values("open_time")
        .reset_index(drop=True)
    )

    return data


# ============================================================
# BOUGIE INCOMPLÈTE
# ============================================================

def _drop_incomplete_last_candle(
    data: pd.DataFrame,
    interval: str,
) -> pd.DataFrame:

    if data.empty:
        return data

    interval_minutes = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240,
        "1d": 1440,
    }.get(
        interval,
        15,
    )

    now = _now_utc()

    last_time = data[
        "open_time"
    ].iloc[-1]

    candle_end = (
        last_time
        + pd.Timedelta(
            minutes=interval_minutes
        )
    )

    if now < candle_end:
        data = data.iloc[:-1].copy()

    return data.reset_index(
        drop=True
    )


# ============================================================
# FRAÎCHEUR
# ============================================================

def _freshness(
    data: pd.DataFrame,
) -> Dict[str, Any]:

    if data.empty:
        return {
            "age_minutes": None,
            "fresh": False,
            "future": False,
        }

    now = _now_utc()

    latest = data[
        "open_time"
    ].iloc[-1]

    age_minutes = (
        now - latest
    ).total_seconds() / 60.0

    if age_minutes < -FUTURE_TOLERANCE_MINUTES:
        return {
            "age_minutes": age_minutes,
            "fresh": False,
            "future": True,
        }

    return {
        "age_minutes": age_minutes,
        "fresh": (
            age_minutes
            <= MAX_AGE_MINUTES
        ),
        "future": False,
    }


# ============================================================
# BREAKOUT
# ============================================================

def _calculate_breakout(
    data: pd.DataFrame,
) -> pd.DataFrame:

    result = data.copy()

    lookback = BREAKOUT_LOOKBACK

    result["prev_high"] = (
        result["high"]
        .rolling(
            lookback,
            min_periods=lookback,
        )
        .max()
        .shift(1)
    )

    result["prev_low"] = (
        result["low"]
        .rolling(
            lookback,
            min_periods=lookback,
        )
        .min()
        .shift(1)
    )

    result["breakout_long"] = (
        result["close"]
        > result["prev_high"]
    )

    result["breakout_short"] = (
        result["close"]
        < result["prev_low"]
    )

    return result


# ============================================================
# VOLUME
# ============================================================

def _volume_information(
    data: pd.DataFrame,
) -> Dict[str, Any]:

    if (
        "volume" not in data.columns
    ):
        return {
            "status": "unavailable",
            "coverage": 0.0,
            "invalid": False,
        }

    volume = pd.to_numeric(
        data["volume"],
        errors="coerce",
    )

    status = get_volume_status(
        volume
    )

    coverage = float(
        volume.notna().mean()
    )

    invalid = bool(
        (
            volume.dropna()
            < 0
        ).any()
    )

    return {
        "status": status,
        "coverage": coverage,
        "invalid": invalid,
    }


# ============================================================
# SCORE
# ============================================================

def _score_latest(
    data: pd.DataFrame,
) -> Dict[str, Any]:

    if data.empty:
        raise ValueError(
            "Données insuffisantes pour scoring"
        )

    # Le statut volume doit être explicitement disponible
    # dans la ligne utilisée par score().
    volume_info = _volume_information(
        data
    )

    data["volume_status"] = (
        volume_info["status"]
    )

    data["volume_coverage"] = (
        volume_info["coverage"]
    )

    data["volume_invalid"] = (
        volume_info["invalid"]
    )

    latest = data.iloc[-1].copy()

    result = score(
        latest,
        return_details=True,
    )

    if isinstance(
        result,
        tuple,
    ):
        total_score = result[0]
        details = (
            result[1]
            if len(result) > 1
            else {}
        )
    elif isinstance(
        result,
        dict,
    ):
        details = result.copy()

        total_score = (
            details.get("score")
            or details.get("total_score")
            or details.get("best_score")
        )

    else:
        total_score = result
        details = {}

    total_score = _safe_float(
        total_score
    )

    if total_score is None:
        raise ValueError(
            "Score invalide"
        )

    details["score"] = total_score
    details["volume_status"] = (
        volume_info["status"]
    )
    details["volume_coverage"] = (
        volume_info["coverage"]
    )
    details["volume_invalid"] = (
        volume_info["invalid"]
    )

    return details


# ============================================================
# SIGNAL
# ============================================================

def _extract_rr(
    details: Dict[str, Any],
) -> Optional[float]:

    candidates = [
        details.get("rr"),
        details.get("risk_reward"),
        details.get("rr_tp2"),
        details.get("tp2_rr"),
        details.get("best_rr"),
    ]

    for value in candidates:
        number = _safe_float(
            value
        )

        if number is not None:
            return number

    return None


def _extract_side(
    details: Dict[str, Any],
) -> Optional[str]:

    side = details.get(
        "side"
    )

    if side is None:
        side = details.get(
            "direction"
        )

    if side is None:
        return None

    side = str(
        side
    ).upper().strip()

    if side in {
        "LONG",
        "SHORT",
    }:
        return side

    return None


def _build_signal(
    data: pd.DataFrame,
    details: Dict[str, Any],
    threshold: float,
) -> Optional[Dict[str, Any]]:

    latest = data.iloc[-1]

    score_value = _safe_float(
        details.get("score")
    )

    if score_value is None:
        return None

    if score_value < threshold:
        return None

    close = _safe_float(
        latest["close"]
    )

    if close is None or close <= 0:
        return None

    breakout_long = bool(
        latest.get(
            "breakout_long",
            False,
        )
    )

    breakout_short = bool(
        latest.get(
            "breakout_short",
            False,
        )
    )

    # --------------------------------------------------------
    # Direction
    # --------------------------------------------------------

    side = _extract_side(
        details
    )

    if side is None:

        if (
            breakout_long
            and not breakout_short
        ):
            side = "LONG"

        elif (
            breakout_short
            and not breakout_long
        ):
            side = "SHORT"

        else:
            # Pas de direction suffisamment claire.
            return None

    # --------------------------------------------------------
    # Breakout obligatoire
    # --------------------------------------------------------

    if side == "LONG" and not breakout_long:
        return None

    if side == "SHORT" and not breakout_short:
        return None

    # --------------------------------------------------------
    # RR
    # --------------------------------------------------------

    rr = _extract_rr(
        details
    )

    if (
        rr is not None
        and rr < MIN_REQUIRED_RR
    ):
        return None

    # --------------------------------------------------------
    # Qualité
    # --------------------------------------------------------

    quality = signal_quality(
        score_value
    )

    # --------------------------------------------------------
    # Prix / SL / TP
    # --------------------------------------------------------

    entry = close

    atr = _safe_float(
        latest.get("atr")
    )

    sl = _safe_float(
        details.get("sl")
    )

    tp1 = _safe_float(
        details.get("tp1")
    )

    tp2 = _safe_float(
        details.get("tp2")
    )

    # Si score() n'a pas fourni les niveaux,
    # on reconstruit à partir de l'ATR.
    if (
        atr is not None
        and atr > 0
    ):

        if sl is None:

            if side == "LONG":
                sl = (
                    entry
                    - 1.50 * atr
                )
            else:
                sl = (
                    entry
                    + 1.50 * atr
                )

        if tp1 is None:

            if side == "LONG":
                tp1 = (
                    entry
                    + 2.25 * atr
                )
            else:
                tp1 = (
                    entry
                    - 2.25 * atr
                )

        if tp2 is None:

            if side == "LONG":
                tp2 = (
                    entry
                    + 3.00 * atr
                )
            else:
                tp2 = (
                    entry
                    - 3.00 * atr
                )

    # --------------------------------------------------------
    # Validation niveaux
    # --------------------------------------------------------

    if sl is None or tp2 is None:
        return None

    risk = abs(
        entry - sl
    )

    reward = abs(
        tp2 - entry
    )

    if risk <= 0:
        return None

    calculated_rr = (
        reward / risk
    )

    if calculated_rr < MIN_REQUIRED_RR:
        return None

    if rr is None:
        rr = calculated_rr

    # --------------------------------------------------------
    # Signal final
    # --------------------------------------------------------

    return {
        "signal": side,
        "direction": side,
        "entry": entry,
        "stop_loss": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr": rr,
        "score": score_value,
        "quality": quality,
        "breakout": True,
        "volume_status": details.get(
            "volume_status"
        ),
        "volume_coverage": details.get(
            "volume_coverage"
        ),
        "timestamp": latest[
            "open_time"
        ],
    }


# ============================================================
# SCAN PRINCIPAL
# ============================================================

def scan(
    df: pd.DataFrame,
    threshold: float = 75.0,
    provider_name: Optional[str] = None,
    symbol: Optional[str] = None,
    asset_type: Optional[str] = None,
    interval: Optional[str] = None,
) -> Dict[str, Any]:

    result: Dict[str, Any] = {
        "symbol": symbol,
        "asset_type": asset_type,
        "interval": interval,
        "provider": provider_name,
        "analyzed": False,
        "signal": None,
        "error": None,
        "insufficient": False,
    }

    # ========================================================
    # CLEAN
    # ========================================================

    try:
        data = _clean_dataframe(
            df
        )

        data = _validate_time(
            data
        )

        if interval:
            data = _drop_incomplete_last_candle(
                data,
                interval,
            )

    except Exception as exc:

        result["error"] = str(
            exc
        )

        return result

    # ========================================================
    # MINIMUM DATA
    # ========================================================

    if len(data) < MIN_CANDLES:

        result["insufficient"] = True
        result["candles"] = len(
            data
        )
        result["error"] = (
            f"Données insuffisantes : "
            f"{len(data)}/{MIN_CANDLES}"
        )

        return result

    # ========================================================
    # FRESHNESS
    # ========================================================

    freshness = _freshness(
        data
    )

    result.update(
        freshness
    )

    if freshness["future"]:

        result["error"] = (
            "timestamp futur détecté"
        )

        return result

    # Une donnée trop ancienne peut être retournée
    # comme insuffisante plutôt qu'un signal erroné.
    if not freshness["fresh"]:

        result["insufficient"] = True
        result["error"] = (
            "données trop anciennes"
        )

        return result

    # ========================================================
    # INDICATEURS
    # ========================================================

    try:

        data = indicators(
            data
        )

    except Exception as exc:

        result["error"] = (
            f"erreur indicateurs : {exc}"
        )

        return result

    # Réaffirme timestamp exact après indicators().
    if "open_time" in data.columns:

        data["open_time"] = _normalize_datetime(
            data["open_time"]
        )

        data = (
            data
            .sort_values("open_time")
            .reset_index(drop=True)
        )

    # ========================================================
    # BREAKOUT
    # ========================================================

    try:

        data = _calculate_breakout(
            data
        )

    except Exception as exc:

        result["error"] = (
            f"erreur breakout : {exc}"
        )

        return result

    # ========================================================
    # SCORE
    # ========================================================

    try:

        details = _score_latest(
            data
        )

    except Exception as exc:

        result["error"] = (
            f"erreur score : {exc}"
        )

        return result

    # ========================================================
    # INFORMATIONS DE BASE
    # ========================================================

    result["analyzed"] = True
    result["candles"] = len(
        data
    )

    result["score"] = _safe_float(
        details.get("score")
    )

    result["volume_status"] = (
        details.get("volume_status")
    )

    result["volume_coverage"] = (
        details.get(
            "volume_coverage"
        )
    )

    result["timestamp"] = (
        data["open_time"].iloc[-1]
    )

    result["close"] = _safe_float(
        data["close"].iloc[-1]
    )

    result["breakout_long"] = bool(
        data["breakout_long"].iloc[-1]
    )

    result["breakout_short"] = bool(
        data["breakout_short"].iloc[-1]
    )

    # ========================================================
    # SIGNAL
    # ========================================================

    signal = _build_signal(
        data,
        details,
        threshold,
    )

    if signal is not None:

        result.update(
            signal
        )

    else:

        result["signal"] = None

    # ========================================================
    # ATTRIBUTS PROVIDER
    # ========================================================

    if provider_name is None:

        provider_name = df.attrs.get(
            "provider"
        )

    if provider_name:

        result["provider"] = (
            provider_name
        )

    source_symbol = df.attrs.get(
        "symbol"
    )

    if symbol is None and source_symbol:
        result["symbol"] = (
            source_symbol
        )

    return result


# ============================================================
# ALIAS COMPATIBILITÉ
# ============================================================

def scan_asset(
    df: pd.DataFrame,
    threshold: float = 75.0,
    provider_name: Optional[str] = None,
    symbol: Optional[str] = None,
    asset_type: Optional[str] = None,
    interval: Optional[str] = None,
) -> Dict[str, Any]:

    return scan(
        df=df,
        threshold=threshold,
        provider_name=provider_name,
        symbol=symbol,
        asset_type=asset_type,
        interval=interval,
    )

