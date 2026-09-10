```python
"""
V4.2 — Univers d'actifs multi-fournisseurs.

Fournisseurs supportés :
- Binance
- Yahoo Finance
- Finnhub
- Twelve Data

Principe :
- Chaque actif peut avoir un symbole différent selon le fournisseur.
- Le routeur V4.2 choisit ensuite dynamiquement la source la plus adaptée.
- L'univers n'est volontairement pas limité à 40 actifs.
- Les actifs sont regroupés par classe pour permettre un routage spécifique.
"""

# ============================================================
# CRYPTO
# ============================================================
# Binance est la source prioritaire pour les cryptos :
# - marché 24/7
# - volume réel exploitable
# - données gratuites
# - pas de clé API nécessaire pour les klines publiques
#
# Le symbole est directement compatible avec Binance.

CRYPTO = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "TRXUSDT",
    "LTCUSDT",
    "BCHUSDT",
    "ATOMUSDT",
    "UNIUSDT",
    "ETCUSDT",
    "XLMUSDT",
    "NEARUSDT",
    "APTUSDT",
    "FILUSDT",
]


# ============================================================
# FOREX
# ============================================================
# Ordre logique du routeur :
# Finnhub / Twelve Data / Yahoo
#
# Finnhub et Twelve Data utilisent des identifiants différents
# de Yahoo.
#
# Attention :
# Le Forex n'a pas un volume centralisé comparable à celui
# d'une action ou d'un marché crypto.
# Le routeur V4.2 ne devra donc PAS bloquer un signal Forex
# uniquement parce que le volume est indisponible.

FOREX = [
    {
        "twelvedata": "EUR/USD",
        "finnhub": "OANDA:EUR_USD",
        "yahoo": "EURUSD=X",
        "display": "EUR/USD",
    },
    {
        "twelvedata": "GBP/USD",
        "finnhub": "OANDA:GBP_USD",
        "yahoo": "GBPUSD=X",
        "display": "GBP/USD",
    },
    {
        "twelvedata": "USD/JPY",
        "finnhub": "OANDA:USD_JPY",
        "yahoo": "JPY=X",
        "display": "USD/JPY",
    },
    {
        "twelvedata": "AUD/USD",
        "finnhub": "OANDA:AUD_USD",
        "yahoo": "AUDUSD=X",
        "display": "AUD/USD",
    },
    {
        "twelvedata": "USD/CHF",
        "finnhub": "OANDA:USD_CHF",
        "yahoo": "CHF=X",
        "display": "USD/CHF",
    },
    {
        "twelvedata": "USD/CAD",
        "finnhub": "OANDA:USD_CAD",
        "yahoo": "CAD=X",
        "display": "USD/CAD",
    },
    {
        "twelvedata": "NZD/USD",
        "finnhub": "OANDA:NZD_USD",
        "yahoo": "NZDUSD=X",
        "display": "NZD/USD",
    },
    {
        "twelvedata": "EUR/GBP",
        "finnhub": "OANDA:EUR_GBP",
        "yahoo": "EURGBP=X",
        "display": "EUR/GBP",
    },
    {
        "twelvedata": "EUR/JPY",
        "finnhub": "OANDA:EUR_JPY",
        "yahoo": "EURJPY=X",
        "display": "EUR/JPY",
    },
    {
        "twelvedata": "GBP/JPY",
        "finnhub": "OANDA:GBP_JPY",
        "yahoo": "GBPJPY=X",
        "display": "GBP/JPY",
    },
]


# ============================================================
# ACTIONS
# ============================================================
# Actions US principalement liquides.
#
# Yahoo :
#   excellente couverture et fallback gratuit.
#
# Finnhub :
#   symbole généralement identique au ticker US.
#
# Twelve Data :
#   symbole généralement identique au ticker US.
#
# Le routeur décidera dynamiquement lequel utiliser.

ACTIONS = [
    {
        "yahoo": "AAPL",
        "twelvedata": "AAPL",
        "finnhub": "AAPL",
        "display": "Apple",
    },
    {
        "yahoo": "MSFT",
        "twelvedata": "MSFT",
        "finnhub": "MSFT",
        "display": "Microsoft",
    },
    {
        "yahoo": "GOOGL",
        "twelvedata": "GOOGL",
        "finnhub": "GOOGL",
        "display": "Alphabet",
    },
    {
        "yahoo": "AMZN",
        "twelvedata": "AMZN",
        "finnhub": "AMZN",
        "display": "Amazon",
    },
    {
        "yahoo": "NVDA",
        "twelvedata": "NVDA",
        "finnhub": "NVDA",
        "display": "Nvidia",
    },
    {
        "yahoo": "META",
        "twelvedata": "META",
        "finnhub": "META",
        "display": "Meta",
    },
    {
        "yahoo": "TSLA",
        "twelvedata": "TSLA",
        "finnhub": "TSLA",
        "display": "Tesla",
    },
    {
        "yahoo": "AVGO",
        "twelvedata": "AVGO",
        "finnhub": "AVGO",
        "display": "Broadcom",
    },
    {
        "yahoo": "AMD",
        "twelvedata": "AMD",
        "finnhub": "AMD",
        "display": "AMD",
    },
    {
        "yahoo": "QCOM",
        "twelvedata": "QCOM",
        "finnhub": "QCOM",
        "display": "Qualcomm",
    },
    {
        "yahoo": "ORCL",
        "twelvedata": "ORCL",
        "finnhub": "ORCL",
        "display": "Oracle",
    },
    {
        "yahoo": "ADBE",
        "twelvedata": "ADBE",
        "finnhub": "ADBE",
        "display": "Adobe",
    },
    {
        "yahoo": "CRM",
        "twelvedata": "CRM",
        "finnhub": "CRM",
        "display": "Salesforce",
    },
    {
        "yahoo": "NFLX",
        "twelvedata": "NFLX",
        "finnhub": "NFLX",
        "display": "Netflix",
    },
    {
        "yahoo": "JPM",
        "twelvedata": "JPM",
        "finnhub": "JPM",
        "display": "JPMorgan",
    },
    {
        "yahoo": "BAC",
        "twelvedata": "BAC",
        "finnhub": "BAC",
        "display": "Bank of America",
    },
    {
        "yahoo": "GS",
        "twelvedata": "GS",
        "finnhub": "GS",
        "display": "Goldman Sachs",
    },
    {
        "yahoo": "V",
        "twelvedata": "V",
        "finnhub": "V",
        "display": "Visa",
    },
    {
        "yahoo": "MA",
        "twelvedata": "MA",
        "finnhub": "MA",
        "display": "Mastercard",
    },
    {
        "yahoo": "UNH",
        "twelvedata": "UNH",
        "finnhub": "UNH",
        "display": "UnitedHealth",
    },
    {
        "yahoo": "JNJ",
        "twelvedata": "JNJ",
        "finnhub": "JNJ",
        "display": "Johnson & Johnson",
    },
    {
        "yahoo": "PFE",
        "twelvedata": "PFE",
        "finnhub": "PFE",
        "display": "Pfizer",
    },
    {
        "yahoo": "XOM",
        "twelvedata": "XOM",
        "finnhub": "XOM",
        "display": "Exxon Mobil",
    },
    {
        "yahoo": "CVX",
        "twelvedata": "CVX",
        "finnhub": "CVX",
        "display": "Chevron",
    },
    {
        "yahoo": "WMT",
        "twelvedata": "WMT",
        "finnhub": "WMT",
        "display": "Walmart",
    },
    {
        "yahoo": "COST",
        "twelvedata": "COST",
        "finnhub": "COST",
        "display": "Costco",
    },
    {
        "yahoo": "PG",
        "twelvedata": "PG",
        "finnhub": "PG",
        "display": "Procter & Gamble",
    },
    {
        "yahoo": "KO",
        "twelvedata": "KO",
        "finnhub": "KO",
        "display": "Coca-Cola",
    },
    {
        "yahoo": "PEP",
        "twelvedata": "PEP",
        "finnhub": "PEP",
        "display": "PepsiCo",
    },
    {
        "yahoo": "HD",
        "twelvedata": "HD",
        "finnhub": "HD",
        "display": "Home Depot",
    },
    {
        "yahoo": "DIS",
        "twelvedata": "DIS",
        "finnhub": "DIS",
        "display": "Disney",
    },
    {
        "yahoo": "NKE",
        "twelvedata": "NKE",
        "finnhub": "NKE",
        "display": "Nike",
    },
    {
        "yahoo": "IBM",
        "twelvedata": "IBM",
        "finnhub": "IBM",
        "display": "IBM",
    },
    {
        "yahoo": "INTC",
        "twelvedata": "INTC",
        "finnhub": "INTC",
        "display": "Intel",
    },
    {
        "yahoo": "CSCO",
        "twelvedata": "CSCO",
        "finnhub": "CSCO",
        "display": "Cisco",
    },
]


# ============================================================
# INDICES
# ============================================================
# Yahoo est conservé comme source principale pour les indices
# car les symboles Yahoo sont particulièrement simples et
# largement disponibles.
#
# Nous n'imposons PAS ici un faux symbole Finnhub/Twelve Data.
# Le routeur pourra utiliser Yahoo sans gaspiller le quota
# Twelve Data.

INDICES = [
    {
        "yahoo": "^GSPC",
        "display": "S&P 500",
    },
    {
        "yahoo": "^IXIC",
        "display": "Nasdaq Composite",
    },
    {
        "yahoo": "^DJI",
        "display": "Dow Jones",
    },
    {
        "yahoo": "^RUT",
        "display": "Russell 2000",
    },
    {
        "yahoo": "^FCHI",
        "display": "CAC 40",
    },
    {
        "yahoo": "^GDAXI",
        "display": "DAX",
    },
    {
        "yahoo": "^FTSE",
        "display": "FTSE 100",
    },
    {
        "yahoo": "^N225",
        "display": "Nikkei 225",
    },
    {
        "yahoo": "^HSI",
        "display": "Hang Seng",
    },
    {
        "yahoo": "^STOXX50E",
        "display": "Euro Stoxx 50",
    },
]


# ============================================================
# MATIÈRES PREMIÈRES — SYMBOLISATION MULTI-SOURCES
# ============================================================
# L'or et l'argent disposent d'un symbole spot dans Twelve Data.
# Yahoo utilise principalement les futures.
#
# Pour le pétrole et les autres matières premières, Yahoo est
# particulièrement pratique via les contrats futures.
#
# Le routeur décidera ensuite si Twelve Data est justifié.

COMMODITIES = [
    {
        "yahoo": "GC=F",
        "twelvedata": "XAU/USD",
        "display": "Or",
    },
    {
        "yahoo": "SI=F",
        "twelvedata": "XAG/USD",
        "display": "Argent",
    },
    {
        "yahoo": "CL=F",
        "twelvedata": "WTI/USD",
        "display": "Pétrole WTI",
    },
    {
        "yahoo": "BZ=F",
        "twelvedata": "BRENT/USD",
        "display": "Pétrole Brent",
    },
    {
        "yahoo": "NG=F",
        "display": "Gaz naturel",
    },
    {
        "yahoo": "HG=F",
        "display": "Cuivre",
    },
]


# ============================================================
# MATIÈRES PREMIÈRES YAHOO UNIQUEMENT
# ============================================================
# Cette liste est conservée séparément pour permettre au routeur
# de savoir qu'il ne doit pas gaspiller une requête Twelve Data
# lorsqu'une autre représentation n'est pas définie.

COMMODITIES_YAHOO_ONLY = [
    {
        "yahoo": "ZC=F",
        "display": "Maïs",
    },
    {
        "yahoo": "ZS=F",
        "display": "Soja",
    },
    {
        "yahoo": "ZW=F",
        "display": "Blé",
    },
    {
        "yahoo": "KC=F",
        "display": "Café",
    },
    {
        "yahoo": "SB=F",
        "display": "Sucre",
    },
]


# ============================================================
# OUTILS UTILITAIRES
# ============================================================

def all_assets():
    """
    Retourne l'ensemble de l'univers V4.2 sous forme de liste
    de tuples :

        (asset_type, asset_definition)

    Cette fonction permet au scanner de parcourir tout l'univers
    sans dépendre d'une limite arbitraire de 40 actifs.
    """

    assets = []

    for symbol in CRYPTO:
        assets.append(("crypto", symbol))

    for asset in FOREX:
        assets.append(("forex", asset))

    for asset in ACTIONS:
        assets.append(("stock", asset))

    for asset in INDICES:
        assets.append(("index", asset))

    for asset in COMMODITIES:
        assets.append(("commodity", asset))

    for asset in COMMODITIES_YAHOO_ONLY:
        assets.append(("commodity", asset))

    return assets


def asset_count():
    """Retourne le nombre total d'actifs de l'univers V4.2."""
    return len(all_assets())


def get_display_name(asset_type, asset):
    """
    Retourne le nom d'affichage d'un actif.

    Pour les cryptos, le symbole Binance est utilisé.
    Pour les autres classes, le champ 'display' est prioritaire.
    """

    if isinstance(asset, str):
        return asset

    return asset.get("display") or asset.get("yahoo") or asset.get("twelvedata") or asset.get("finnhub")


def get_symbol(asset, provider):
    """
    Retourne le symbole correspondant à un fournisseur.

    Exemple :
        get_symbol(forex_asset, "finnhub")
        -> OANDA:EUR_USD

    Si le fournisseur n'est pas explicitement défini,
    retourne None.

    Cela permet au routeur V4.2 de décider lui-même du fallback
    sans inventer un symbole.
    """

    if isinstance(asset, str):
        # Les cryptos sont directement utilisables par Binance.
        return asset if provider == "binance" else None

    return asset.get(provider)


# ============================================================
# MÉTADONNÉES DE L'UNIVERS
# ============================================================

ASSET_COUNTS = {
    "crypto": len(CRYPTO),
    "forex": len(FOREX),
    "stock": len(ACTIONS),
    "index": len(INDICES),
    "commodity": len(COMMODITIES) + len(COMMODITIES_YAHOO_ONLY),
}


# Nombre total d'actifs à scanner.
TOTAL_ASSETS = sum(ASSET_COUNTS.values())
```
