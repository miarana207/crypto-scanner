```python
"""
V4.2 — Scan et scoring des actifs.

Responsabilités :
- récupération des données via le routeur multi-sources ;
- contrôle qualité des données ;
- vérification de fraîcheur ;
- calcul des indicateurs ;
- calcul des scores LONG / SHORT ;
- détection des breakouts ;
- calcul des niveaux d'entrée / SL / TP ;
- qualification du signal ;
- production d'un DataFrame homogène.

Le module est conçu pour fonctionner avec le DataRouter V4.2
et le backtest.py / indicators.py corrigé.
"""

import math
import time

import pandas as pd

from backtest import (
    indicators,
    score,
    best_direction,
    calculate_trade_levels,
    signal_quality,
    BREAKOUT_LOOKBACK,
    BREAKOUT_ATR_MULT,
    MIN_RR,
)


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLD = 75
MIN_CANDLES = 120

# Un signal ne doit jamais être généré sur une donnée trop ancienne.
# Cette limite est volontairement relativement permissive pour permettre
# le fonctionnement lorsque les marchés sont fermés.
DEFAULT_MAX_AGE_MULTIPLIER = 4
DEFAULT_MIN_FRESHNESS_MINUTES = 15

VOLUME_CONFIRMED = "VOLUME_CONFIRMED"
VOLUME_UNAVAILABLE = "VOLUME_UNAVAILABLE"
VOLUME_INVALID = "VOLUME_INVALID"


# ---------------------------------------------------------------------------
# UTILITAIRES
# ---------------------------------------------------------------------------

def safe_float(value, default=float("nan")):
    """
    Convertit une valeur en float sans faire planter le scan.
    """
    try:
        x = float(value)

        if math.isnan(x):
            return default

        return x

    except (TypeError, ValueError):
        return default


def format_relvol(value):
    """
    Format lisible du relative volume.
    """
    return "n/d" if pd.isna(value) else f"{float(value):.2f}"


def interval_to_seconds(interval):
    """
    Convertit un intervalle du type 1m / 15m / 1h / 4h / 1d
    en secondes.
    """
    interval = str(interval).lower().strip()

    if not interval:
        return 900

    try:
        value = int(interval[:-1])
    except (TypeError, ValueError):
        return 900

    unit = interval[-1]

    multipliers = {
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 604800,
    }

    return value * multipliers.get(unit, 60)


def max_allowed_age_minutes(interval):
    """
    Calcule l'âge maximal acceptable d'une bougie.

    Exemple :
    - 15m -> max(60 min, 15 min) = 60 min
    - 1h  -> max(240 min, 15 min) = 240 min
    - 4h  -> max(960 min, 15 min) = 960 min
    """
    interval_minutes = interval_to_seconds(interval) / 60

    return max(
        interval_minutes * DEFAULT_MAX_AGE_MULTIPLIER,
        DEFAULT_MIN_FRESHNESS_MINUTES,
    )


# ---------------------------------------------------------------------------
# COLONNES DE SORTIE
# ---------------------------------------------------------------------------

def empty_result_columns():
    """
    Colonnes garanties même lorsque aucun actif n'est analysable.
    """

    return [
        "symbol",
        "provider",
        "provider_symbol",

        "close",

        "score_long",
        "score_short",
        "direction",
        "best_score",

        "status",
        "quality",
        "missing",

        "trend_pts",
        "ema_pts",
        "rsi_pts",
        "volume_pts",
        "breakout_pts",

        "entry",
        "stop_loss",
        "take_profit_1",
        "take_profit_2",
        "risk",
        "rr_tp1",
        "rr_tp2",

        "atr",
        "rsi",
        "relvol",
        "trend1h",

        "breakout_ok",

        "volume_ok",
        "volume_available",
        "volume_status",

        "fresh",
        "age_minutes",

        "candles",

        "last_candle",

        "router_candidates",
        "router_errors",
    ]


# ---------------------------------------------------------------------------
# NORMALISATION
# ---------------------------------------------------------------------------

def _prepare_dataframe(df):
    """
    Nettoie minimalement les données avant calcul des indicateurs.

    Important :
    le volume n'est PAS rempli avec 0.
    """

    if df is None or df.empty:
        return None

    out = df.copy()

    required_price_columns = {
        "open_time",
        "open",
        "high",
        "low",
        "close",
    }

    if not required_price_columns.issubset(out.columns):
        return None

    # Le volume doit exister dans le DataFrame final pour conserver
    # une structure homogène, mais ses valeurs peuvent rester NaN.
    if "volume" not in out.columns:
        out["volume"] = pd.NA

    out["open_time"] = pd.to_datetime(
        out["open_time"],
        utc=True,
        errors="coerce",
    )

    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:
        out[column] = pd.to_numeric(
            out[column],
            errors="coerce",
        )

    # Le prix est obligatoire.
    out = out.dropna(
        subset=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
        ]
    )

    # Contrôle OHLC basique.
    valid_ohlc = (
        (out["high"] >= out[["open", "close"]].max(axis=1))
        &
        (out["low"] <= out[["open", "close"]].min(axis=1))
    )

    out = out.loc[valid_ohlc].copy()

    # Tri + suppression des doublons temporels.
    out = (
        out
        .sort_values("open_time")
        .drop_duplicates(
            subset=["open_time"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    return out


# ---------------------------------------------------------------------------
# FRAÎCHEUR
# ---------------------------------------------------------------------------

def _calculate_freshness(last_candle, interval):
    """
    Détermine si la dernière bougie est suffisamment récente.
    """

    if pd.isna(last_candle):
        return False, float("inf")

    now = pd.Timestamp.now(tz="UTC")

    last = pd.Timestamp(last_candle)

    if last.tzinfo is None:
        last = last.tz_localize("UTC")
    else:
        last = last.tz_convert("UTC")

    age_minutes = max(
        0.0,
        (now - last).total_seconds() / 60.0,
    )

    fresh = age_minutes <= max_allowed_age_minutes(interval)

    return fresh, age_minutes


# ---------------------------------------------------------------------------
# VOLUME
# ---------------------------------------------------------------------------

def _get_volume_status(df):
    """
    Récupère le statut du volume.

    Priorité :
    1. attribut fourni par le DataRouter ;
    2. analyse locale du DataFrame.

    Aucun NaN n'est converti en zéro.
    """

    attrs = getattr(df, "attrs", {}) or {}

    router_status = attrs.get("volume_status")

    if router_status in {
        VOLUME_CONFIRMED,
        VOLUME_UNAVAILABLE,
        VOLUME_INVALID,
    }:
        return router_status

    if "volume" not in df.columns:
        return VOLUME_UNAVAILABLE

    volume = pd.to_numeric(
        df["volume"],
        errors="coerce",
    )

    valid = volume.notna()

    if valid.sum() == 0:
        return VOLUME_UNAVAILABLE

    if (volume.loc[valid] < 0).any():
        return VOLUME_INVALID

    # Même logique que le routeur :
    # on exige une couverture suffisamment importante.
    coverage = valid.mean()

    min_coverage = 0.80

    if coverage < min_coverage:
        return VOLUME_UNAVAILABLE

    return VOLUME_CONFIRMED


# ---------------------------------------------------------------------------
# APPEL DU FETCHER
# ---------------------------------------------------------------------------

def _fetch_data(
    symbol,
    fetcher,
    interval,
    limit,
    asset_type,
    fallback_fetcher=None,
    provider_name="unknown",
    fallback_provider_name=None,
    symbol_map=None,
):
    """
    Appelle le fetcher principal.

    Compatible avec :
    - ancien fetcher simple ;
    - DataRouter V4.2 ;
    - fallback_fetcher éventuel.

    Le routeur V4.2 est privilégié lorsqu'il est fourni.
    """

    primary_error = None

    # ---------------------------------------------------------------
    # 1. FETCHER PRINCIPAL
    # ---------------------------------------------------------------

    try:

        if getattr(fetcher, "_router", False):

            df = fetcher(
                symbol,
                interval=interval,
                limit=limit,
                asset_type=asset_type,
                symbol_map=symbol_map,
            )

        else:

            try:

                df = fetcher(
                    symbol,
                    interval=interval,
                    limit=limit,
                    asset_type=asset_type,
                    symbol_map=symbol_map,
                )

            except TypeError:

                # Compatibilité avec les anciens fetchers.
                df = fetcher(
                    symbol,
                    interval=interval,
                    limit=limit,
                )

        if df is not None and not df.empty:
            return df, None

        primary_error = RuntimeError(
            "Source principale : données vides"
        )

    except Exception as exc:

        primary_error = exc

    # ---------------------------------------------------------------
    # 2. FALLBACK
    # ---------------------------------------------------------------

    if fallback_fetcher is None:
        raise RuntimeError(
            f"{provider_name}: {primary_error}"
        )

    try:

        df = fallback_fetcher(
            symbol,
            interval=interval,
            limit=limit,
        )

        if df is None or df.empty:
            raise RuntimeError(
                "Fallback : données vides"
            )

        return df, None

    except Exception as fallback_error:

        raise RuntimeError(
            f"{provider_name}: {primary_error} | "
            f"{fallback_provider_name or 'fallback'}: "
            f"{fallback_error}"
        )


# ---------------------------------------------------------------------------
# SCAN PRINCIPAL
# ---------------------------------------------------------------------------

def scan(
    symbols,
    fetcher,
    interval="15m",
    limit=1000,
    threshold=DEFAULT_THRESHOLD,
    pause=0.0,
    fallback_fetcher=None,
    provider_name="unknown",
    fallback_provider_name=None,
    asset_type="stock",
    symbol_map=None,
):
    """
    Analyse une liste d'actifs.

    Parameters
    ----------
    symbols : iterable
        Liste des actifs à analyser.

    fetcher : callable
        Fetcher principal ou wrapper du DataRouter V4.2.

    interval : str
        Exemple : 5m, 15m, 1h, 4h, 1d.

    limit : int
        Nombre maximal de bougies demandées.

    threshold : float
        Score minimum pour considérer qu'un signal est potentiellement fort.

    pause : float
        Pause éventuelle entre deux actifs.

    fallback_fetcher : callable | None
        Ancien mécanisme de fallback facultatif.

    asset_type : str
        crypto / forex / stock / index / commodity.

    symbol_map : dict | None
        Mapping fournisseur -> symbole.

    Returns
    -------
    pandas.DataFrame
        Résultats triés par qualité.
    """

    results = []

    requested = 0
    analyzed = 0
    error_count = 0
    insufficient = 0
    stale_count = 0
    invalid_count = 0

    symbols = list(symbols or [])

    # ---------------------------------------------------------------
    # BOUCLE SUR LES ACTIFS
    # ---------------------------------------------------------------

    for symbol in symbols:

        requested += 1

        # -----------------------------------------------------------
        # RÉCUPÉRATION
        # -----------------------------------------------------------

        try:

            df, _ = _fetch_data(
                symbol=symbol,
                fetcher=fetcher,
                interval=interval,
                limit=limit,
                asset_type=asset_type,
                fallback_fetcher=fallback_fetcher,
                provider_name=provider_name,
                fallback_provider_name=fallback_provider_name,
                symbol_map=symbol_map,
            )

        except Exception as exc:

            error_count += 1

            print(
                f"[{symbol}] "
                f"erreur données : {exc}"
            )

            continue

        if df is None or df.empty:

            error_count += 1

            print(
                f"[{symbol}] données vides"
            )

            continue

        # -----------------------------------------------------------
        # NORMALISATION
        # -----------------------------------------------------------

        original_attrs = getattr(df, "attrs", {}).copy()

        df = _prepare_dataframe(df)

        if df is None or df.empty:

            invalid_count += 1

            print(
                f"[{symbol}] données OHLC invalides"
            )

            continue

        # Restaurer les attributs du routeur.
        df.attrs.update(original_attrs)

        # -----------------------------------------------------------
        # NOMBRE MINIMUM DE BOUGIES
        # -----------------------------------------------------------

        if len(df) < MIN_CANDLES:

            insufficient += 1

            print(
                f"[{symbol}] "
                f"données insuffisantes : "
                f"{len(df)}/{MIN_CANDLES}"
            )

            continue

        # -----------------------------------------------------------
        # DERNIÈRE BOUGIE
        # -----------------------------------------------------------

        last_candle = pd.Timestamp(
            df["open_time"].iloc[-1]
        )

        fresh, age_minutes = _calculate_freshness(
            last_candle,
            interval,
        )

        # -----------------------------------------------------------
        # DIAGNOSTICS ROUTEUR
        # -----------------------------------------------------------

        attrs = getattr(df, "attrs", {}) or {}

        used_provider = attrs.get(
            "provider",
            provider_name,
        )

        provider_symbol = attrs.get(
            "provider_symbol",
            symbol,
        )

        router_candidates = attrs.get(
            "router_candidates",
            [],
        )

        router_errors = attrs.get(
            "router_errors",
            [],
        )

        # -----------------------------------------------------------
        # MARCHÉ FERMÉ / DONNÉES ANCIENNES
        # -----------------------------------------------------------
        #
        # On conserve les résultats analytiques pour diagnostic,
        # mais on interdit un "SIGNAL FORT" si la donnée est trop
        # ancienne.
        #
        # C'est important notamment pour :
        # - actions ;
        # - indices ;
        # - matières premières ;
        # lorsque le marché est fermé.
        # -----------------------------------------------------------

        if not fresh:
            stale_count += 1

        # -----------------------------------------------------------
        # INDICATEURS
        # -----------------------------------------------------------

        try:

            d = indicators(df)

        except Exception as exc:

            error_count += 1

            print(
                f"[{symbol}] "
                f"erreur indicateurs : {exc}"
            )

            continue

        if d is None or d.empty or len(d) < MIN_CANDLES:

            insufficient += 1

            print(
                f"[{symbol}] "
                f"indicateurs insuffisants"
            )

            continue

        # -----------------------------------------------------------
        # BREAKOUT
        # -----------------------------------------------------------

        d["prev_high"] = (
            d["high"]
            .rolling(BREAKOUT_LOOKBACK)
            .max()
            .shift(1)
        )

        d["prev_low"] = (
            d["low"]
            .rolling(BREAKOUT_LOOKBACK)
            .min()
            .shift(1)
        )

        d["breakout_long"] = (
            d["close"]
            >
            d["prev_high"]
            +
            d["atr"] * BREAKOUT_ATR_MULT
        )

        d["breakout_short"] = (
            d["close"]
            <
            d["prev_low"]
            -
            d["atr"] * BREAKOUT_ATR_MULT
        )

        # -----------------------------------------------------------
        # DERNIÈRE OBSERVATION
        # -----------------------------------------------------------

        row = d.iloc[-1]

        # -----------------------------------------------------------
        # SCORE
        # -----------------------------------------------------------

        try:

            details = score(
                row,
                return_details=True,
            )

        except Exception as exc:

            error_count += 1

            print(
                f"[{symbol}] "
                f"erreur scoring : {exc}"
            )

            continue

        sl = details["long"]
        ss = details["short"]

        direction = best_direction(
            sl["score"],
            ss["score"],
        )

        best_score = max(
            sl["score"],
            ss["score"],
        )

        selected = (
            sl
            if direction == "LONG"
            else ss
        )

        # -----------------------------------------------------------
        # BREAKOUT SÉLECTIONNÉ
        # -----------------------------------------------------------

        if direction == "LONG":

            breakout_ok = bool(
                details["breakout_long"]
            )

        else:

            breakout_ok = bool(
                details["breakout_short"]
            )

        # -----------------------------------------------------------
        # VOLUME
        # -----------------------------------------------------------

        volume_status = _get_volume_status(df)

        volume_available = (
            volume_status == VOLUME_CONFIRMED
        )

        # Les détails du score restent prioritaires si présents.
        volume_ok = bool(
            details.get(
                "volume_ok",
                False,
            )
        )

        # Si le volume est structurellement indisponible,
        # il ne doit pas pénaliser le signal.
        if volume_status == VOLUME_UNAVAILABLE:

            volume_available = False
            volume_ok = False

        elif volume_status == VOLUME_INVALID:

            volume_available = True
            volume_ok = False

        # -----------------------------------------------------------
        # NIVEAUX DE TRADE
        # -----------------------------------------------------------

        try:

            levels = calculate_trade_levels(
                row["close"],
                row["atr"],
                direction,
            )

        except Exception as exc:

            error_count += 1

            print(
                f"[{symbol}] "
                f"erreur niveaux de trade : {exc}"
            )

            continue

        # -----------------------------------------------------------
        # R:R
        # -----------------------------------------------------------

        rr_tp2 = safe_float(
            levels.get("rr_tp2")
        )

        rr_valid = (
            pd.notna(rr_tp2)
            and rr_tp2 >= MIN_RR
        )

        # -----------------------------------------------------------
        # QUALITÉ
        # -----------------------------------------------------------

        quality = signal_quality(
            best_score,
            breakout_ok,
            volume_ok,
            volume_available,
            rr_tp2,
        )

        # -----------------------------------------------------------
        # SIGNAL FORT
        # -----------------------------------------------------------
        #
        # Conditions :
        #
        # 1. score >= seuil
        # 2. breakout confirmé
        # 3. volume OK si disponible
        # 4. R:R suffisant
        # 5. donnée suffisamment fraîche
        # 6. données non invalides
        #
        # Pour Forex / certains indices :
        # volume indisponible => PAS de pénalité.
        # -----------------------------------------------------------

        strong = (
            best_score >= threshold
            and breakout_ok
            and (
                volume_ok
                or not volume_available
            )
            and rr_valid
            and fresh
            and volume_status != VOLUME_INVALID
        )

        # -----------------------------------------------------------
        # STATUT
        # -----------------------------------------------------------

        if strong:

            status = "SIGNAL FORT"

        elif not fresh:

            status = "DONNÉES ANCIENNES"

        elif best_score >= threshold:

            status = "ATTENTE"

        else:

            status = "SOUS SEUIL"

        # -----------------------------------------------------------
        # CONDITIONS MANQUANTES
        # -----------------------------------------------------------

        missing = []

        if best_score < threshold:

            missing.append(
                f"SCORE < {threshold:.0f}"
            )

        if not fresh:

            missing.append(
                "DONNÉES ANCIENNES"
            )

        if not breakout_ok:

            missing.append(
                "BREAKOUT"
            )

        if volume_status == VOLUME_INVALID:

            missing.append(
                "VOLUME INVALIDE"
            )

        elif (
            volume_available
            and not volume_ok
        ):

            missing.append(
                "VOLUME"
            )

        if not rr_valid:

            missing.append(
                "R:R"
            )

        # -----------------------------------------------------------
        # AJOUT DU RÉSULTAT
        # -----------------------------------------------------------

        results.append(
            {
                "symbol": symbol,

                "provider": used_provider,

                "provider_symbol": provider_symbol,

                "close": safe_float(
                    row["close"]
                ),

                "score_long": safe_float(
                    sl["score"]
                ),

                "score_short": safe_float(
                    ss["score"]
                ),

                "direction": direction,

                "best_score": safe_float(
                    best_score
                ),

                "status": status,

                "quality": safe_float(
                    quality
                ),

                "missing": (
                    " + ".join(missing)
                    if missing
                    else "aucune"
                ),

                "trend_pts": safe_float(
                    selected.get(
                        "trend_pts",
                        float("nan"),
                    )
                ),

                "ema_pts": safe_float(
                    selected.get(
                        "ema_pts",
                        float("nan"),
                    )
                ),

                "rsi_pts": safe_float(
                    selected.get(
                        "rsi_pts",
                        float("nan"),
                    )
                ),

                "volume_pts": safe_float(
                    selected.get(
                        "volume_pts",
                        float("nan"),
                    )
                ),

                "breakout_pts": safe_float(
                    selected.get(
                        "breakout_pts",
                        float("nan"),
                    )
                ),

                "entry": levels.get(
                    "entry"
                ),

                "stop_loss": levels.get(
                    "stop_loss"
                ),

                "take_profit_1": levels.get(
                    "take_profit_1"
                ),

                "take_profit_2": levels.get(
                    "take_profit_2"
                ),

                "risk": levels.get(
                    "risk"
                ),

                "rr_tp1": levels.get(
                    "rr_tp1"
                ),

                "rr_tp2": levels.get(
                    "rr_tp2"
                ),

                "atr": safe_float(
                    row.get(
                        "atr",
                        float("nan"),
                    )
                ),

                "rsi": safe_float(
                    row.get(
                        "rsi",
                        float("nan"),
                    )
                ),

                "relvol": safe_float(
                    row.get(
                        "relvol",
                        float("nan"),
                    )
                ),

                "trend1h": row.get(
                    "trend1h",
                    None,
                ),

                "breakout_ok": breakout_ok,

                "volume_ok": volume_ok,

                "volume_available": volume_available,

                "volume_status": volume_status,

                "fresh": fresh,

                "age_minutes": age_minutes,

                "candles": len(df),

                "last_candle": last_candle,

                "router_candidates": (
                    ", ".join(
                        map(
                            str,
                            router_candidates,
                        )
                    )
                    if router_candidates
                    else ""
                ),

                "router_errors": (
                    " | ".join(
                        map(
                            str,
                            router_errors,
                        )
                    )
                    if router_errors
                    else ""
                ),
            }
        )

        analyzed += 1

        # -----------------------------------------------------------
        # PAUSE OPTIONNELLE
        # -----------------------------------------------------------

        if pause:

            time.sleep(
                max(0.0, float(pause))
            )

    # ----------------------------------------------------------------
    # STATISTIQUES
    # ----------------------------------------------------------------

    print(
        f"Couverture: "
        f"{analyzed}/{requested} analysés "
        f"— erreurs={error_count}, "
        f"insuffisants={insufficient}, "
        f"anciens={stale_count}, "
        f"invalides={invalid_count}"
    )

    # ----------------------------------------------------------------
    # AUCUN RÉSULTAT
    # ----------------------------------------------------------------

    if not results:

        return pd.DataFrame(
            columns=empty_result_columns()
        )

    # ----------------------------------------------------------------
    # DATAFRAME FINAL
    # ----------------------------------------------------------------

    result_df = pd.DataFrame(
        results
    )

    # Garantit toutes les colonnes attendues.
    for column in empty_result_columns():

        if column not in result_df.columns:

            result_df[column] = pd.NA

    # Ordre stable des colonnes.
    result_df = result_df[
        empty_result_columns()
    ]

    # ----------------------------------------------------------------
    # TRI
    # ----------------------------------------------------------------
    #
    # Priorité :
    # 1. SIGNAL FORT
    # 2. ATTENTE
    # 3. SOUS SEUIL
    # 4. DONNÉES ANCIENNES
    #
    # Puis score décroissant.
    # ----------------------------------------------------------------

    status_priority = {
        "SIGNAL FORT": 0,
        "ATTENTE": 1,
        "SOUS SEUIL": 2,
        "DONNÉES ANCIENNES": 3,
    }

    result_df["_status_priority"] = (
        result_df["status"]
        .map(status_priority)
        .fillna(99)
    )

    result_df = (
        result_df
        .sort_values(
            [
                "_status_priority",
                "best_score",
                "quality",
            ],
            ascending=[
                True,
                False,
                False,
            ],
            na_position="last",
        )
        .drop(
            columns=["_status_priority"]
        )
        .reset_index(drop=True)
    )

    return result_df
```
