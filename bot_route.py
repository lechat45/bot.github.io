"""
=============================================================================
bot_route.py — Bot "Route" v4
Alpaca Trade API + pandas-ta (HMA/EMA/RSI/MACD/BB/ADX) + Groq Cloud Sentiment
=============================================================================

MODULES & RÔLES :

  [MODULE 1] Configuration & Constantes
      → Tous les paramètres en un seul endroit : actifs, indicateurs, risque
      → Clés API lues EXCLUSIVEMENT depuis variables d'environnement
      → NOUVEAU : HMA (Hull Moving Average) ajouté — plus réactif que SMA/EMA
      → NOUVEAU : pandas-ta gère tous les calculs d'indicateurs (plus fiable)
      → Rôle : centre de contrôle — modifier ici change tout le comportement

  [MODULE 2] AlpacaClientV2
      → Utilise alpaca-trade-api (SDK officiel) au lieu de requests bruts
      → Récupère les barres OHLC via api.get_bars() → DataFrame pandas
      → Soumet les ordres via api.submit_order() (identique au code fourni)
      → Supporte actions ET crypto (BTC/USD, ETH/USD...)
      → Rôle : interface broker officielle — plus stable et maintenable

  [MODULE 3] MoteurIndicateurs (NOUVEAU — pandas-ta)
      → HMA(10)    : Hull MA — détecte les croisements rapides de tendance
      → EMA(100)   : tendance de fond longue (inspiré du code fourni)
      → EMA(9/21)  : croisement court terme
      → MACD(12,26,9) : momentum et divergences
      → RSI(14)    : sur-achat/sur-vente avec zones étendues
      → Bollinger Bands(20,2) : squeeze et breakouts
      → ADX(14)    : force de tendance directionnelle
      → Volume SMA : confirmation par pic de volume
      → Score composite 0–100 pondéré
      → Rôle : cerveau mathématique — pandas-ta calcule tout en vectoriel

  [MODULE 4] AnalyseurSentimentGroq (NOUVEAU — Groq Cloud)
      → Remplace Grok xAI par Groq Cloud (groq.com/console)
      → Endpoint : https://api.groq.com/openai/v1/chat/completions
      → Modèle : llama-3.3-70b-versatile (rapide, gratuit, précis)
      → Prompt enrichi : contexte macro + données techniques + secteur
      → Retourne score 0.0–1.0 + résumé + facteurs + biais court terme
      → Cache 3 minutes par actif pour limiter les appels
      → Rôle : filtre émotionnel IA — "variable émotion" Aywen via Groq

  [MODULE 5] GestionnaireRisque
      → Stop-loss dynamique ATR (1.8×) + fixe 3% de secours
      → Take-profit adaptatif ATR (2.7×) → ratio 1.5×
      → Trailing stop −2.5% depuis le plus haut (protège les gains)
      → Drawdown max 5% → pause forcée
      → Sizing adaptatif : 700$/1000$/1200$ selon conviction
      → Contrôle sectoriel : max 2 positions par secteur
      → Rôle : gardien du capital

  [MODULE 6] ScoreDecisionIA
      → Fusionne score technique (60%) + sentiment Groq (40%)
      → Matrice 9 combinaisons : technique × sentiment
      → Conviction FORTE/MOYENNE/FAIBLE → taille de position variable
      → Rôle : décision finale arbitrée entre logique et émotion

  [MODULE 7] BotRoute
      → Orchestre tous les modules en pipeline séquentiel
      → Détecte les croisements HMA comme signal prioritaire (code fourni)
      → Exporte vers data/etat_bot.json pour le dashboard
      → Rôle : chef d'orchestre du cycle de trading

  [MODULE 8] Point d'entrée
      → Boucle infinie SIGINT/SIGTERM-safe
      → Compatible GitHub Actions (4 min run / 5 min cron)
      → Rôle : moteur d'exécution continu

=============================================================================
VARIABLES D'ENVIRONNEMENT REQUISES (GitHub Secrets) :
  ALPACA_API_KEY        → app.alpaca.markets → API Keys
  ALPACA_SECRET_KEY     → app.alpaca.markets → API Keys
  ALPACA_BASE_URL       → https://paper-api.alpaca.markets (paper)
  GROQ_API_KEY          → console.groq.com → API Keys (gratuit)
=============================================================================
"""

import json, time, os, signal, sys, logging, math, sqlite3, statistics, warnings
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# Supprimer les warnings déprecation internes de pandas-ta (ex: 'd' vs 'D' dans Ichimoku)
warnings.filterwarnings("ignore", message=".*'d' is deprecated.*")
warnings.filterwarnings("ignore", category=FutureWarning, module="pandas_ta")
warnings.filterwarnings("ignore", category=FutureWarning, module="pandas")

# ── Dépendances tierces ───────────────────────────────────────────────────
try:
    import alpaca_trade_api as tradeapi   # SDK officiel Alpaca
    from alpaca_trade_api.rest import TimeFrame
    import pandas as pd
    import pandas_ta as ta                # Calcul HMA, EMA, MACD, RSI, BB, ADX
    import requests                       # Appels Groq Cloud
except ImportError as e:
    sys.exit(f"❌  Dépendance manquante : {e}\n   → pip install -r requirements.txt")


# =============================================================================
# [MODULE 1] CONFIGURATION & CONSTANTES
# =============================================================================

# ── Clés API — jamais en dur, toujours depuis l'environnement ────────────
ALPACA_API_KEY    = os.environ.get("ALPACA_API_KEY",    "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL   = os.environ.get("ALPACA_BASE_URL",   "https://paper-api.alpaca.markets")
GROQ_API_KEY      = os.environ.get("GROQ_API_KEY",      "")  # console.groq.com
DISCORD_WEBHOOK   = os.environ.get("DISCORD_WEBHOOK",   "")  # Discord webhook URL

# ── Fichiers persistants ─────────────────────────────────────────────────
HISTORIQUE_FILE = Path("data/historique_trades.json")  # Persistant entre runs

# ── Money management ────────────────────────────────────────────────────
CAPITAL_INITIAL       = 100_000.0
ALLOCATION_EXCELLENCE = 1_500.0   # Score >= 90  → conviction maximale
ALLOCATION_BASE       = 1_000.0   # Allocation standard par trade (score 65-74)
ALLOCATION_FORTE      = 1_200.0   # Conviction FORTE  score 75-89 → +20%
ALLOCATION_FAIBLE     = 700.0     # Conviction FAIBLE score 55-64 → −30%
MAX_POSITIONS         = 9999  # Illimité — seul le cash disponible limite les positions
STOP_LOSS_PCT         = 0.03      # Stop-loss % (fallback si montant fixe non applicable)
TAKE_PROFIT_PCT       = 0.045     # Take-profit % (fallback)
# ── Seuils monétaires fixes (prioritaires sur les % ci-dessus) ───────────
STOP_LOSS_FIXE        = 25.0      # Vente sécurité : perte ≥ 25 $ sur la position
TAKE_PROFIT_ACTIONS   = 50.0      # Prise de profit actions : gain ≥ 50 $
TAKE_PROFIT_CRYPTO    = 100.0     # Prise de profit crypto  : gain ≥ 100 $
ATR_STOP_MULTIPLIER   = 1.8       # Stop dynamique = 1.8× ATR
ATR_TP_MULTIPLIER     = 2.7       # TP   dynamique = 2.7× ATR → ratio 1.5×
TRAILING_STOP_PCT     = 0.025     # Trailing : −2.5% depuis le plus haut
MAX_DRAWDOWN_PCT      = 0.05      # Pause si drawdown > 5%

# ── Paramètres indicateurs (pandas-ta) ──────────────────────────────────
HMA_PERIODE         = 10    # Hull MA — vitesse (code fourni)
EMA_TENDANCE        = 100   # EMA longue — tendance de fond (code fourni)
EMA_RAPIDE          = 9
EMA_LENTE           = 21
MACD_RAPIDE         = 12
MACD_LENT           = 26
MACD_SIGNAL_PERIODE = 9
RSI_PERIODE         = 14
RSI_SURVENTE        = 35
RSI_SURACHAT        = 65   # seuil scoring indicateur (inchangé)
RSI_SURVENTE_EXT    = 20
RSI_SURACHAT_EXT    = 80
BB_PERIODE          = 20
BB_STD              = 2.0
ADX_PERIODE         = 14
ADX_SEUIL           = 25
VOLUME_MULT         = 1.5
BARRES_LIMIT        = 120   # Bougies 1h — suffisant pour EMA100 + tous les indicateurs

# ── Pondération score composite (total = 100) ────────────────────────────
POIDS = {
    "hma_crossover":  25,   # prioritaire — code fourni
    "ema_crossover":  13,   # -2
    "ema_tendance":   13,   # -2
    "macd":           15,   # inchangé
    "rsi":             8,   # -2
    "bollinger":       7,   # -3
    "adx":             3,   # -2
    "volume":          3,   # -2
    "vwap":            5,   # NOUVEAU — référence institutionnelle
    "ichimoku":        8,   # NOUVEAU — filtre tendance Ichimoku Cloud
}
# Sum = 25+13+13+15+8+7+3+3+5+8 = 100

# ── Gestion du risque avancée ────────────────────────────────────────
MAX_HEAT_PCT       = 8.0    # Portfolio heat max — bloque achats si > 8%
MAX_DAILY_LOSS_PCT = 5.0    # Circuit breaker — stop trading si pertes jour > 5%

# ── Portefeuille Long Terme — Sacha Pro (EXPÉRIMENTAL) ──────────────────
# Petit budget séparé géré UNIQUEMENT par les crossovers EMA daily.
# Le bot court-terme ne peut PAS vendre ces positions.
LT_ACTIF             = True    # Activer le portefeuille LT (mettre False pour désactiver)
LT_ALLOC_PCT         = 5.0     # % de l'equity par position (ex: 5% de 10k = 500$)
LT_MAX_POSITIONS     = 3       # Maximum de positions LT simultanées
LT_MIN_USD           = 150     # Montant minimum par position LT ($)
LT_MAX_USD           = 800     # Montant maximum par position LT ($) — sécurité expérimentale
LT_REQUIRE_CROSSOVER = True    # True = n'achète QUE sur crossover (signal fort)
                                # False = achète aussi sur tendance haussière (plus actif)

# ── Seuils de décision ───────────────────────────────────────────────────
SEUIL_TECH_BUY          = 60
SEUIL_TECH_SELL         = 40
SEUIL_SENTIMENT_BUY     = 0.52
SEUIL_SENTIMENT_SELL    = 0.35
SEUIL_CONVICTION_FORTE  = 75
SEUIL_CONVICTION_FAIBLE = 55

# ── 30 actifs (actions + 2 crypto disponibles sur Alpaca Paper) ─────────
ACTIFS_ACTIONS = [
    "TSLA","NVDA","AMD", "AMZN","NFLX",
    "META","AAPL","MSFT","GOOGL","INTC",
    "COIN","SOFI","PLTR","RIVN","LCID",
    "NIO", "UBER","LYFT","SNAP","RBLX",
    "HOOD","DKNG","PENN","GME", "AMC",
    "SPY", "QQQ", "KO",  "DAL", "BA",
    "XLK", "XLV", "XLE", "XLF", "GLD",  # ETF sectoriels + or
]

ACTIFS_CRYPTO = ["BTC/USD", "ETH/USD", "SOL/USD"]   # Format Alpaca crypto (toujours actifs)

# ── Crypto étendue pour mode nuit/weekend (50% de l'analyse) ─────────────
# Ces paires sont disponibles sur Alpaca Paper Trading et tradent 24h/24.
# Quand le marché actions est fermé, le bot analyse autant de cryptos que d'actions.
ACTIFS_CRYPTO_NUIT = [
    "BTC/USD",   # Bitcoin          — liquidité maximale
    "ETH/USD",   # Ethereum         — DeFi / smart contracts
    "SOL/USD",   # Solana           — haute vitesse, volume fort
    "DOGE/USD",  # Dogecoin         — très volatile, bonnes opportunités
    "AVAX/USD",  # Avalanche        — concurrent ETH
    "LINK/USD",  # Chainlink        — oracle, corrélé DeFi
    "LTC/USD",   # Litecoin         — stable, fort volume
    "BCH/USD",   # Bitcoin Cash     — corrélé BTC
    "XRP/USD",   # Ripple           — paiements internationaux
    "UNI/USD",   # Uniswap          — DeFi majeur
]
# ACTIFS_CRYPTO_NUIT doit avoir le même nombre que d'actions scannées la nuit
# → 10 cryptos + 10 actions prioritaires = 20 tickers (50/50)
NB_ACTIONS_NUIT = len(ACTIFS_CRYPTO_NUIT)   # 10 actions top-score choisies dynamiquement

ACTIFS_TOUS = ACTIFS_ACTIONS + ACTIFS_CRYPTO

SECTEURS = {
    "TECH":        ["TSLA","NVDA","AMD","AMZN","NFLX","META","AAPL","MSFT","GOOGL","INTC"],
    "CRYPTO":      ["COIN","HOOD","BTC/USD","ETH/USD","SOL/USD",
                    "DOGE/USD","AVAX/USD","LINK/USD","LTC/USD","BCH/USD",
                    "XRP/USD","UNI/USD"],
    "FINTECH":     ["SOFI","PLTR"],
    "EV":          ["RIVN","LCID","NIO"],
    "MOBILITY":    ["UBER","LYFT"],
    "SOCIAL":      ["SNAP","RBLX"],
    "GAMING":      ["DKNG","PENN","GME","AMC"],
    "ETF":         ["SPY","QQQ"],
    "ETF_SECTEUR": ["XLK","XLV","XLE","XLF"],
    "METAUX":      ["GLD"],
    "AUTRES":      ["KO","DAL","BA"],
}
MAX_PAR_SECTEUR = 2
MAX_CRYPTO_EXPOSURE_PCT = 25.0  # Maximum 25% du capital total en crypto

DATA_DIR    = Path(os.environ.get("BOT_DATA_PATH", "data/etat_bot.json")).parent
JSON_SORTIE = Path(os.environ.get("BOT_DATA_PATH", "data/etat_bot.json"))
INTERVALLE  = int(os.environ.get("BOT_INTERVAL_SEC", "60"))
COOLDOWNS_FILE      = DATA_DIR / "cooldowns.json"       # persistance cooldowns entre runs
TRAILING_HIGHS_FILE = DATA_DIR / "trailing_highs.json"  # persistance trailing stop entre runs

# ── Protection trading ────────────────────────────────────────────────────
MAX_ACHATS_PAR_CYCLE  = 3       # Max nouveaux achats par cycle (actions)
MAX_ACHATS_CRYPTO_NUIT= 5       # Max achats crypto quand marché actions fermé (24/7)
COOLDOWN_APRES_ACHAT  = 1800    # 30 min cooldown après un achat (actions)
COOLDOWN_CRYPTO_NUIT  = 900     # 15 min cooldown crypto hors marché (plus réactif)
RSI_MAX_ACHAT         = 76      # Bloquer achat si RSI > 76 — ajusté d/après données réelles
                                # (NVDA RSI=83, INTC RSI=84, TSLA RSI=74-75 → pertes fréquentes)
SEUIL_CRYPTO_WEEKEND  = 55      # Score minimum crypto le weekend (baissé: 70→55)
BARRES_CACHE_TTL      = 300     # Cache OHLC 5 min pour éviter les requêtes redondantes

# ── Meme stocks — stop-loss serré (haute volatilité) ─────────────────────
STOP_LOSS_MEME        = 0.04    # 4% SL pour meme stocks (vs 3% standard)
MEME_STOCKS           = {"AMC", "GME", "HOOD", "DOGE/USD", "DOGEUSD"}
                                # AMC/GME : volatilité extrême, squeeze imprévisible
                                # HOOD : corrélé retail sentiment
                                # DOGE/USD / DOGEUSD : crypto meme, drawdowns violents (deux formats)

# ── Feature 1 : Pyramiding (renforcer les gagnants) ──────────────────────────
PYRAMIDING_SEUIL_PCT   = 3.0    # % gain minimum pour pyramider
PYRAMIDING_MULT        = 0.30   # Taille du renforcement (30% de la position originale)
PYRAMIDING_MAX_FOIS    = 1      # Maximum 1 pyramiding par position
PYRAMIDING_FILE        = DATA_DIR / "pyramiding.json"  # {ticker: nb_fois_pyramide}

# ── Feature 2 : Paires de trading ────────────────────────────────────────────
PAIRES_TRADING = [
    ("NVDA", "AMD"),
    ("TSLA", "RIVN"),
    ("UBER", "LYFT"),
    ("BTC/USD", "ETH/USD"),
    ("SPY", "QQQ"),
]
# Seuil de divergence: si l'un monte +2% et l'autre pas → signal
PAIRES_DIVERGENCE_SEUIL = 2.0

# ── Feature 3 : Saisonnalité ─────────────────────────────────────────────────
SAISONNALITE = {
    1:  1.10,   # Janvier: effet janvier +10%
    2:  1.00,   # Février: neutre
    3:  1.00,   # Mars: neutre
    4:  1.05,   # Avril: retour impôts +5%
    5:  0.95,   # Mai: "Sell in May" -5%
    6:  0.95,   # Juin: été calme -5%
    7:  0.95,   # Juillet: été calme -5%
    8:  0.90,   # Août: vacances, faible volume -10%
    9:  0.90,   # Septembre: historiquement mauvais -10%
    10: 0.95,   # Octobre: volatil -5%
    11: 1.08,   # Novembre: début rallye de Noël +8%
    12: 1.10,   # Décembre: rallye de Noël +10%
}

# ── Feature 4 : Blacklist faux signaux ───────────────────────────────────────
BLACKLIST_FILE         = DATA_DIR / "blacklist_patterns.json"
BLACKLIST_SEUIL_PERTES = 3      # 3 pertes consécutives → blacklist
BLACKLIST_DUREE_H      = 48     # Blacklist pendant 48h

# ── Feature 6 : Hedging automatique ──────────────────────────────────────────
HEDGE_NB_POSITIONS_SEUIL = 6      # Déclenche hedge si >= 6 positions ouvertes
HEDGE_ALLOC_PCT           = 2.0   # 2% equity pour le hedge
HEDGE_TICKER_BAISSIER     = "GLD" # Or quand marché BAISSIER
HEDGE_TICKER_NEUTRE       = "GLD" # Or aussi en marché LATÉRAL

# ── Feature 7 : Détection de krach ───────────────────────────────────────────
KRACH_SEUIL_PCT      = 3.0    # Chute SPY en 1h pour déclencher mode bunker
KRACH_PAUSE_H        = 48     # Pause en heures après un krach
KRACH_VENTE_PCT      = 0.50   # Vendre 50% des positions
BUNKER_FILE          = DATA_DIR / "bunker_mode.json"  # {actif_jusqu: timestamp}

# ── Feature E : Time Stop ────────────────────────────────────────────────────
TIME_STOP_HEURES = 48    # Sortir si position ouverte > 48h sans gain > 1%
TIME_STOP_FILE   = DATA_DIR / "positions_ouverture.json"  # {ticker: timestamp_ouverture}

# ── Feature F : Kill Switch — pertes consécutives ────────────────────────────
KILL_MAX_PERTES_CONSECUTIVES = 5    # Pause si >= 5 pertes de suite
KILL_PAUSE_PERTES_H          = 2    # Pause de 2h
KILL_FILE                    = DATA_DIR / "kill_switch.json"

# ── Mode Crypto Nuit/Weekend ─────────────────────────────────────────────
# Quand le marché actions est fermé, le bot se concentre sur les cryptos (24/7).
# Les cryptos reçoivent : barres 30min (plus granulaires), allocation +30%,
# seuil de score abaissé, et les ordres sont TOUJOURS exécutés (pas de blocage).
CRYPTO_NUIT_BARRES_MIN = 30     # Timeframe 30 min pour crypto hors marché
CRYPTO_NUIT_ALLOC_MULT = 1.30   # +30% d'allocation crypto quand marché fermé
CRYPTO_NUIT_RSI_PERIODE= 9      # RSI 9 périodes (plus réactif que 14)
CRYPTO_NUIT_SEUIL_BUY  = 52     # Seuil score plus bas la nuit (marché moins actif)

# Groupes d'actifs très corrélés — un seul acheté à la fois
GROUPES_CORRELES = [
    {"NVDA", "AMD"},
    {"RIVN", "LCID", "NIO"},
    {"BTC/USD", "ETH/USD", "COIN"},          # Bitcoin-liés
    {"BTC/USD", "BCH/USD", "LTC/USD"},       # Forks Bitcoin
    {"ETH/USD", "AVAX/USD", "SOL/USD", "UNI/USD", "LINK/USD"},  # Ethereum-liés / L1
    {"UBER", "LYFT"},
    {"GME", "AMC"},
]

# ── Candidats S&P500 pour rotation dynamique de watchlist ────────────
SP500_CANDIDATS = [
    # Fintech / Paiements
    "PYPL","SQ","V","MA","AXP","COF","DFS","SCHW","BLK","GS","MS","JPM","BAC","C","WFC",
    # Tech Cloud / SaaS
    "CRWD","NET","DDOG","SNOW","ZS","OKTA","MDB","GTLB","CFLT","HUBS",
    "CRM","NOW","ADBE","ORCL","IBM","ACN","INTU","ANSS","CDNS","SNPS",
    # Semi-conducteurs
    "AVGO","QCOM","TXN","MU","WDC","STX","MRVL","ON","WOLF","ENTG","AMAT","LRCX","KLAC",
    "SMCI","ARM","SWKS","MTSI",
    # Biotech / Pharma
    "LLY","PFE","MRK","JNJ","ABBV","TMO","DHR","SYK","MDT","ISRG","AMGN",
    "REGN","VRTX","BIIB","GILD","BMY","BSX","EW","DXCM","IDXX",
    # Consommation / Retail
    "WMT","COST","TGT","HD","LOW","NKE","PEP","MCD","SBUX","CMG","LULU","TJX","ROST",
    # Énergie
    "XOM","CVX","COP","OXY","SLB","HAL","PSX","MPC","VLO","EOG",
    # Matériaux / Industrie
    "FCX","NUE","CLF","AA","GE","CAT","DE","HON","RTX","LMT","NOC","GD","BA",
    # Énergie verte
    "ENPH","NEE","FSLR","CEG","VST","AES","RUN","SEDG",
    # Media / Streaming / Gaming
    "DIS","CMCSA","NFLX","SPOT","RDDT","PINS","MTCH","EA","TTWO","RBLX",
    # Telecom
    "T","VZ","TMUS",
    # Travel / Hospitality
    "BKNG","EXPE","MAR","HLT","ABNB","DASH","UAL","LUV",
    # Santé / Assurance
    "UNH","CVS","HUM","CI","CNC","MOH",
    # Crypto-related
    "MSTR","RIOT","MARA","CLSK","BTBT","HUT",
    # Fintech alternatif
    "AFRM","UPST","LC",
    # Véhicules électriques
    "LI","XPEV","FSR","GOEV",
    # Divers haute volatilité / momentum
    "RKT","OPEN","CLOV","SPCE","NKLA","WKHS",
    # ETF sectoriels (peuvent être tradés comme actions)
    "XLF","XLK","XLE","XLV","XLC","ARKK","ARKG","ARKF",
]
# Retirer les actifs déjà surveillés pour éviter les doublons
SP500_CANDIDATS = [t for t in SP500_CANDIDATS if t not in ACTIFS_ACTIONS + ACTIFS_CRYPTO]


# =============================================================================
# [MODULE 2] AlpacaClientV2 — SDK officiel alpaca-trade-api
# =============================================================================

class AlpacaClientV2:
    """
    Utilise le SDK officiel alpaca-trade-api (identique au code fourni).

    Méthodes principales :
      get_compte()          → equity, cash, buying_power
      get_positions()       → positions ouvertes indexées par ticker
      get_barres_actions()  → DataFrame pandas via api.get_bars()
      get_barres_crypto()   → DataFrame pandas via api.get_crypto_bars()
      soumettre_ordre()     → api.submit_order() (achat/vente market)
      liquider_position()   → api.close_position()
    """

    def __init__(self):
        if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
            raise ValueError(
                "ALPACA_API_KEY / ALPACA_SECRET_KEY manquants.\n"
                "→ Définir dans GitHub Secrets ou variables d'environnement."
            )
        self.api = tradeapi.REST(
            ALPACA_API_KEY,
            ALPACA_SECRET_KEY,
            ALPACA_BASE_URL,
            api_version="v2",
        )
        self.logger    = logging.getLogger("Alpaca")
        self._cache_bars: dict    = {}
        self._cache_bars_ts: dict = {}

    def get_compte(self) -> dict:
        """Retourne equity, cash, buying_power via le SDK."""
        acc = self.api.get_account()
        return {
            "equity":        float(acc.equity),
            "cash":          float(acc.cash),
            "buying_power":  float(acc.buying_power),
            "portfolio_value": float(acc.portfolio_value),
        }

    def marche_ouvert(self) -> bool:
        """Vérifie si le marché US est actuellement ouvert via le SDK Alpaca."""
        try:
            clock = self.api.get_clock()
            return clock.is_open
        except Exception as e:
            self.logger.warning(f"Impossible de vérifier l'état du marché : {e}")
            return False

    def get_positions(self) -> dict:
        """Toutes les positions ouvertes, indexées par symbol."""
        positions = {}
        for p in self.api.list_positions():
            positions[p.symbol] = {
                "ticker":          p.symbol,
                "qty":             float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price":   float(p.current_price),
                "market_value":    float(p.market_value),
                "unrealized_pl":   float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc) * 100,
                "highest_price":   float(p.current_price),
            }
        return positions

    def get_barres(self, ticker: str, limit: int = BARRES_LIMIT) -> pd.DataFrame:
        """
        Récupère les barres horaires. Cache 5 min pour éviter les requêtes redondantes.
        Utilise get_crypto_bars() pour BTC/ETH, get_bars() pour les actions.
        """
        now = time.time()
        if ticker in self._cache_bars and (now - self._cache_bars_ts.get(ticker, 0)) < BARRES_CACHE_TTL:
            return self._cache_bars[ticker]
        try:
            end   = datetime.now(timezone.utc)
            start = end.replace(hour=0, minute=0, second=0, microsecond=0)
            # On remonte 10 jours pour avoir assez de bougies horaires
            start = start - timedelta(days=10)
            start_str = start.strftime("%Y-%m-%d")
            end_str   = end.strftime("%Y-%m-%d")

            if "/" in ticker:
                df = self.api.get_crypto_bars(
                    ticker, TimeFrame.Hour,
                    start=start_str, end=end_str, limit=limit
                ).df
            else:
                df = self.api.get_bars(
                    ticker, TimeFrame.Hour,
                    start=start_str, end=end_str, limit=limit,
                    feed="iex"
                ).df

            if df is None or df.empty:
                return pd.DataFrame()

            # Normalise les noms de colonnes en minuscules
            df.columns = [c.lower() for c in df.columns]
            df = df.sort_index()
            self.logger.info(f"Barres {ticker} : {len(df)} bougies reçues")
            self._cache_bars[ticker]    = df
            self._cache_bars_ts[ticker] = time.time()
            return df

        except Exception as e:
            self.logger.warning(f"Barres {ticker} : {e}")
            return pd.DataFrame()

    def get_barres_crypto_nuit(self, ticker: str) -> pd.DataFrame:
        """
        Barres 30 minutes pour crypto hors heures de marché.
        Plus granulaires que les barres 1h → signaux plus réactifs la nuit/weekend.
        Cache 10 min (TTL court car marché crypto bouge vite).
        """
        if "/" not in ticker:
            return self.get_barres(ticker)   # fallback barres 1h pour non-crypto

        cache_key = f"{ticker}_30m"
        now = time.time()
        if (cache_key in self._cache_bars and
                (now - self._cache_bars_ts.get(cache_key, 0)) < 600):   # 10 min cache
            return self._cache_bars[cache_key]
        try:
            end   = datetime.now(timezone.utc)
            start = end - timedelta(days=4)   # 4 jours de 30min = ~192 bougies
            df = self.api.get_crypto_bars(
                ticker,
                TimeFrame.Minute * 30,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                limit=200,
            ).df
            if df is None or df.empty:
                return self.get_barres(ticker)   # fallback 1h
            df.columns = [c.lower() for c in df.columns]
            df = df.sort_index()
            self._cache_bars[cache_key]    = df
            self._cache_bars_ts[cache_key] = time.time()
            self.logger.info(f"Barres 30m {ticker} : {len(df)} bougies (mode nuit)")
            return df
        except Exception as e:
            self.logger.debug(f"Barres 30m {ticker}: {e} — fallback 1h")
            return self.get_barres(ticker)

    def get_barres_daily(self, ticker: str, jours: int = 90) -> pd.DataFrame:
        """
        Récupère les barres DAILY. Cache 30 min (les barres daily ne changent qu'une fois/j).
        Utilisé par StrategieSachaPro (EMA50 daily nécessite ≥ 60 jours).
        """
        cache_key = f"{ticker}_daily"
        now = time.time()
        if (cache_key in self._cache_bars and
                (now - self._cache_bars_ts.get(cache_key, 0)) < 1800):
            return self._cache_bars[cache_key]
        try:
            end   = datetime.now(timezone.utc)
            start = end - timedelta(days=jours + 10)
            start_str = start.strftime("%Y-%m-%d")
            end_str   = end.strftime("%Y-%m-%d")

            if "/" in ticker:
                df = self.api.get_crypto_bars(
                    ticker, TimeFrame.Day,
                    start=start_str, end=end_str, limit=jours + 20
                ).df
            else:
                df = self.api.get_bars(
                    ticker, TimeFrame.Day,
                    start=start_str, end=end_str, limit=jours + 20,
                    feed="iex"
                ).df

            if df is None or df.empty:
                return pd.DataFrame()

            df.columns = [c.lower() for c in df.columns]
            df = df.sort_index()
            self._cache_bars[cache_key]    = df
            self._cache_bars_ts[cache_key] = time.time()
            return df
        except Exception as e:
            self.logger.debug(f"Barres daily {ticker}: {e}")
            return pd.DataFrame()

    def soumettre_ordre(self, ticker: str, montant_usd: float,
                        side: str = "buy") -> Optional[object]:
        """
        Soumet un ordre market notional (USD) via api.submit_order().
        Pour la crypto, utilise time_in_force='gtc' (identique au code fourni).
        Retry 2× sur erreur réseau transitoire.
        """
        tif = "gtc" if "/" in ticker else "day"
        sym = ticker.replace("/", "")  # BTC/USD → BTCUSD pour l'ordre Alpaca
        for tentative in range(3):
            try:
                ordre = self.api.submit_order(
                    symbol=sym,
                    notional=str(round(montant_usd, 2)),
                    side=side,
                    type="market",
                    time_in_force=tif,
                )
                self.logger.info(f"Ordre {side.upper()} {ticker} ${montant_usd:.0f} → id={ordre.id}")
                return ordre
            except Exception as e:
                if tentative < 2:
                    self.logger.warning(f"Ordre {ticker} tentative {tentative+1}/3: {e}")
                    time.sleep(2 ** tentative)
                else:
                    raise

    # Codes d'erreur spéciaux retournés par liquider_position
    LIQUIDATION_OK        = True
    LIQUIDATION_ECHEC     = False
    LIQUIDATION_PDT       = "PDT"     # Pattern Day Trader — ne pas retenter aujourd'hui

    def liquider_position(self, ticker: str):
        """
        Liquide intégralement la position via api.close_position(). Retry 2×.

        Retourne :
          True        → succès
          False       → échec générique (retenter plus tard)
          "PDT"       → Pattern Day Trader bloqué — ne PAS retenter avant demain
        """
        sym = ticker.replace("/", "")
        for tentative in range(3):
            try:
                self.api.close_position(sym)
                return True
            except Exception as e:
                err_msg = str(e).lower()
                # PDT : Pattern Day Trader — erreur permanente pour la journée
                if "day trade" in err_msg or "pdt" in err_msg:
                    self.logger.warning(
                        f"⚠️ PDT {ticker} — jour de trade bloqué (equity < $25k hier). "
                        f"Vente reportée à demain."
                    )
                    return self.LIQUIDATION_PDT
                if tentative < 2:
                    self.logger.warning(f"Liquidation {ticker} tentative {tentative+1}/3: {e}")
                    time.sleep(2 ** tentative)
                else:
                    self.logger.error(f"Erreur liquidation {ticker} : {e}")
                    return False


# =============================================================================
# [MODULE 2b] Fonctions quantitatives avancées (Hurst, Garman-Klass, Volume Delta)
# =============================================================================

import numpy as np  # noqa: E402 — déjà disponible via pandas-ta, import explicite pour sécurité


def _calculer_hurst(prices: np.ndarray) -> float:
    """
    Exposant de Hurst par analyse R/S.
    H < 0.45 → mean-reverting (acheter les creux, vendre les pics)
    H = 0.5  → random walk (marché équilibré)
    H > 0.55 → tendanciel (suivre la tendance)
    Retourne 0.5 si données insuffisantes.
    """
    ts = np.asarray(prices, dtype=float)
    if len(ts) < 40:
        return 0.5
    try:
        lags = range(2, min(20, len(ts) // 3))
        tau = []
        for lag in lags:
            diffs = ts[lag:] - ts[:-lag]
            tau.append(np.sqrt(np.std(diffs) + 1e-10))
        valid = [(lag, t) for lag, t in zip(lags, tau) if t > 0]
        if len(valid) < 3:
            return 0.5
        lags_v, tau_v = zip(*valid)
        poly = np.polyfit(np.log(lags_v), np.log(tau_v), 1)
        return float(np.clip(poly[0] * 2.0, 0.0, 1.0))
    except Exception:
        return 0.5


def _volatilite_garman_klass(opens, highs, lows, closes) -> float:
    """
    Estimateur Garman-Klass : plus précis que std(closes).
    σ²_GK = 0.5×(ln(H/L))² - (2ln2-1)×(ln(C/O))²
    Retourne la volatilité en % (annualisée en base horaire).
    """
    try:
        opens  = np.array(opens,  dtype=float)
        highs  = np.array(highs,  dtype=float)
        lows   = np.array(lows,   dtype=float)
        closes = np.array(closes, dtype=float)
        if len(opens) < 5:
            return 0.0
        log_hl = np.log(highs / (lows + 1e-10))
        log_co = np.log(closes / (opens + 1e-10))
        gk = 0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2
        # Annualisation sur base horaire (8760 heures/an)
        return float(np.sqrt(np.mean(gk) * 8760) * 100)  # En %
    except Exception:
        return 0.0


def _calculer_volume_delta(df) -> float:
    """
    Volume Delta proxy sur barres OHLCV.
    Si close > open → bougie haussière → acheteurs ont dominé → delta positif
    Retourne un score ∈ [-1, +1] : positif = pression acheteurs, négatif = vendeurs.
    """
    try:
        if len(df) < 5:
            return 0.0
        recent = df.tail(min(20, len(df)))
        # Intensité du delta par bougie : (close-open)/(high-low) pondérée par volume
        ranges = recent["high"] - recent["low"]
        ranges = ranges.replace(0, 1e-10)
        delta_intensity = (recent["close"] - recent["open"]) / ranges
        # Pondérer par le volume relatif
        vol_norm = recent["volume"] / (recent["volume"].mean() + 1e-10)
        weighted_delta = (delta_intensity * vol_norm).sum() / (vol_norm.sum() + 1e-10)
        return float(np.clip(weighted_delta, -1.0, 1.0))
    except Exception:
        return 0.0


# =============================================================================
# [MODULE 3] MoteurIndicateurs — pandas-ta (HMA + tous les indicateurs)
# =============================================================================

class MoteurIndicateurs:
    """
    Calcule tous les indicateurs via pandas-ta sur le DataFrame Alpaca.

    Indicateurs :
      HMA(10)      : Hull MA — réactif, détecte les croisements prix/HMA
                     Signal : prix croise au-dessus du HMA → momentum haussier
                     (logique directement inspirée du code fourni)
      EMA(100)     : filtre de tendance longue (code fourni)
      EMA(9/21)    : croisement court terme
      MACD(12,26,9): momentum
      RSI(14)      : sur-achat/sur-vente
      BB(20,2)     : position dans les bandes de Bollinger
      ADX(14)      : force de la tendance
      Volume SMA   : pic de volume
    """

    def __init__(self):
        self.logger = logging.getLogger("Indicateurs")

    def calculer(self, ticker: str, df: pd.DataFrame) -> dict:
        """
        Applique pandas-ta sur le DataFrame et retourne un dict complet
        avec tous les indicateurs, le score composite et le signal final.
        """
        result = {
            "ticker": ticker, "signal": "HOLD", "score": 50,
            "conviction": "FAIBLE", "raison": "Données insuffisantes",
            "indicateurs": {}, "prix": None, "atr": None,
            "croisement_hma": False,
        }

        # Minimum de barres pour calculer EMA100 + marge
        if len(df) < 50:
            return result

        closes  = df["close"]
        highs   = df["high"]
        lows    = df["low"]
        volumes = df["volume"]
        prix    = float(closes.iloc[-1])
        result["prix"] = round(prix, 4)

        ind = {}
        points_buy  = 0.0
        points_sell = 0.0

        # ── 1. HMA — Hull Moving Average (prioritaire, code fourni) ──────
        try:
            hma_serie = ta.hma(closes, length=HMA_PERIODE)
            if hma_serie is not None and len(hma_serie) >= 2:
                hma_now  = float(hma_serie.iloc[-1])
                hma_prev = float(hma_serie.iloc[-2])
                prix_prev = float(closes.iloc[-2])
                ind["hma"] = round(hma_now, 4)

                # Croisement HMA : prix passe AU-DESSUS du HMA (code fourni)
                croisement_hausse = (prix_prev < hma_prev) and (prix > hma_now)
                # Croisement baissier : prix passe EN-DESSOUS du HMA
                croisement_baisse = (prix_prev > hma_prev) and (prix < hma_now)

                result["croisement_hma"] = croisement_hausse

                if croisement_hausse:
                    points_buy  += POIDS["hma_crossover"] * 1.2  # bonus croisement
                    ind["hma_signal"] = "crossover_hausse"
                elif prix > hma_now:
                    points_buy  += POIDS["hma_crossover"] * 0.6
                    ind["hma_signal"] = "au_dessus"
                elif croisement_baisse:
                    points_sell += POIDS["hma_crossover"] * 1.2
                    ind["hma_signal"] = "crossover_baisse"
                else:
                    points_sell += POIDS["hma_crossover"] * 0.6
                    ind["hma_signal"] = "en_dessous"
        except Exception as e:
            self.logger.debug(f"HMA {ticker}: {e}")

        # ── 2. EMA tendance longue (EMA100, code fourni) ─────────────────
        try:
            ema100 = ta.ema(closes, length=EMA_TENDANCE)
            if ema100 is not None:
                val_ema100 = float(ema100.iloc[-1])
                ind["ema_100"] = round(val_ema100, 4)
                if prix > val_ema100:
                    points_buy  += POIDS["ema_tendance"]
                    ind["ema100_signal"] = "prix_au_dessus"
                else:
                    points_sell += POIDS["ema_tendance"]
                    ind["ema100_signal"] = "prix_en_dessous"
        except Exception as e:
            self.logger.debug(f"EMA100 {ticker}: {e}")

        # ── 3. EMA court terme croisement (EMA9 vs EMA21) ────────────────
        try:
            ema9  = ta.ema(closes, length=EMA_RAPIDE)
            ema21 = ta.ema(closes, length=EMA_LENTE)
            if ema9 is not None and ema21 is not None:
                v9  = float(ema9.iloc[-1])
                v21 = float(ema21.iloc[-1])
                ind["ema_9"]  = round(v9,  4)
                ind["ema_21"] = round(v21, 4)
                if v9 > v21:
                    points_buy  += POIDS["ema_crossover"]
                else:
                    points_sell += POIDS["ema_crossover"]
        except Exception as e:
            self.logger.debug(f"EMA9/21 {ticker}: {e}")

        # ── 4. MACD ───────────────────────────────────────────────────────
        try:
            macd_df = ta.macd(closes,
                              fast=MACD_RAPIDE,
                              slow=MACD_LENT,
                              signal=MACD_SIGNAL_PERIODE)
            if macd_df is not None and not macd_df.empty:
                col_macd = [c for c in macd_df.columns if "MACD_" in c and "s" not in c.lower() and "h" not in c.lower()]
                col_sig  = [c for c in macd_df.columns if "MACDs" in c]
                col_hist = [c for c in macd_df.columns if "MACDh" in c]
                if col_macd and col_sig and col_hist:
                    mval  = float(macd_df[col_macd[0]].iloc[-1])
                    msig  = float(macd_df[col_sig[0]].iloc[-1])
                    mhist = float(macd_df[col_hist[0]].iloc[-1])
                    ind["macd"]      = round(mval,  6)
                    ind["macd_sig"]  = round(msig,  6)
                    ind["macd_hist"] = round(mhist, 6)
                    if mval > msig and mhist > 0:
                        points_buy  += POIDS["macd"]
                    elif mval < msig and mhist < 0:
                        points_sell += POIDS["macd"]
                    else:
                        points_buy  += POIDS["macd"] * 0.3
        except Exception as e:
            self.logger.debug(f"MACD {ticker}: {e}")

        # ── 5. RSI ────────────────────────────────────────────────────────
        try:
            rsi_serie = ta.rsi(closes, length=RSI_PERIODE)
            if rsi_serie is not None:
                rsi_val = float(rsi_serie.iloc[-1])
                ind["rsi"] = round(rsi_val, 2)
                if rsi_val < RSI_SURVENTE_EXT:
                    points_buy  += POIDS["rsi"]
                elif rsi_val < RSI_SURVENTE:
                    points_buy  += POIDS["rsi"] * 0.7
                elif rsi_val > RSI_SURACHAT_EXT:
                    points_sell += POIDS["rsi"]
                elif rsi_val > RSI_SURACHAT:
                    points_sell += POIDS["rsi"] * 0.7
                else:
                    points_buy  += POIDS["rsi"] * 0.3
        except Exception as e:
            self.logger.debug(f"RSI {ticker}: {e}")

        # ── 5b. Momentum 4h crypto (8 bougies × 30min) ───────────────────
        try:
            if "/" in ticker and len(df) >= 8:
                ret_4h = (float(df["close"].iloc[-1]) / float(df["close"].iloc[-8]) - 1) * 100
                ind["momentum_4h"] = round(ret_4h, 3)
                if ret_4h > 2.0:    # +2% sur 4h → signal fort haussier
                    points_buy += 5
                elif ret_4h < -2.0:  # -2% sur 4h → signal fort baissier
                    points_sell += 5
        except Exception as e:
            self.logger.debug(f"Momentum4h {ticker}: {e}")

        # ── 6. Bollinger Bands ────────────────────────────────────────────
        try:
            bb_df = ta.bbands(closes, length=BB_PERIODE, std=BB_STD)
            if bb_df is not None and not bb_df.empty:
                col_u = [c for c in bb_df.columns if "BBU" in c]
                col_l = [c for c in bb_df.columns if "BBL" in c]
                col_m = [c for c in bb_df.columns if "BBM" in c]
                if col_u and col_l and col_m:
                    bb_u = float(bb_df[col_u[0]].iloc[-1])
                    bb_l = float(bb_df[col_l[0]].iloc[-1])
                    bb_m = float(bb_df[col_m[0]].iloc[-1])
                    ind["bb_upper"] = round(bb_u, 4)
                    ind["bb_mid"]   = round(bb_m, 4)
                    ind["bb_lower"] = round(bb_l, 4)
                    ind["bb_width"] = round((bb_u - bb_l) / bb_m * 100, 3)
                    if prix <= bb_l:
                        points_buy  += POIDS["bollinger"]
                    elif prix >= bb_u:
                        points_sell += POIDS["bollinger"]
                    elif prix < bb_m:
                        points_buy  += POIDS["bollinger"] * 0.4
                    else:
                        points_sell += POIDS["bollinger"] * 0.4
        except Exception as e:
            self.logger.debug(f"BB {ticker}: {e}")

        # ── 7. ADX ────────────────────────────────────────────────────────
        try:
            adx_df = ta.adx(highs, lows, closes, length=ADX_PERIODE)
            if adx_df is not None and not adx_df.empty:
                col_adx = [c for c in adx_df.columns if c.startswith("ADX_")]
                if col_adx:
                    adx_val = float(adx_df[col_adx[0]].iloc[-1])
                    ind["adx"] = round(adx_val, 2)
                    if adx_val > ADX_SEUIL:
                        bonus = POIDS["adx"]
                        if points_buy >= points_sell:
                            points_buy  += bonus
                        else:
                            points_sell += bonus
        except Exception as e:
            self.logger.debug(f"ADX {ticker}: {e}")

        # ── 8. ATR pour stops dynamiques ─────────────────────────────────
        try:
            atr_serie = ta.atr(highs, lows, closes, length=14)
            if atr_serie is not None:
                atr_val = float(atr_serie.iloc[-1])
                ind["atr"]     = round(atr_val, 4)
                ind["atr_pct"] = round(atr_val / prix * 100, 3)
                result["atr"]  = atr_val
        except Exception as e:
            self.logger.debug(f"ATR {ticker}: {e}")

        # ── 9. Volume Spike ───────────────────────────────────────────────
        try:
            vol_sma = ta.sma(volumes, length=20)
            if vol_sma is not None:
                vol_now = float(volumes.iloc[-1])
                vol_avg = float(vol_sma.iloc[-1])
                if vol_avg > 0:
                    ratio = vol_now / vol_avg
                    ind["volume_ratio"] = round(ratio, 2)
                    if ratio > VOLUME_MULT:
                        if points_buy >= points_sell:
                            points_buy  += POIDS["volume"]
                        else:
                            points_sell += POIDS["volume"]
        except Exception as e:
            self.logger.debug(f"Volume {ticker}: {e}")

        # ── 10. VWAP — référence institutionnelle intraday ───────────────
        try:
            n_bars = min(10, len(df))
            df_vwap = df.iloc[-n_bars:].copy()
            typical  = (df_vwap["high"] + df_vwap["low"] + df_vwap["close"]) / 3
            vol_sum  = df_vwap["volume"].sum()
            if vol_sum > 0:
                vwap_val = float((typical * df_vwap["volume"]).sum() / vol_sum)
                ind["vwap"] = round(vwap_val, 4)
                dist_pct    = (prix - vwap_val) / vwap_val * 100
                ind["vwap_dist_pct"] = round(dist_pct, 2)
                if prix > vwap_val:
                    points_buy  += POIDS["vwap"]
                    ind["vwap_signal"] = "au_dessus"
                else:
                    points_sell += POIDS["vwap"]
                    ind["vwap_signal"] = "en_dessous"
        except Exception as e:
            self.logger.debug(f"VWAP {ticker}: {e}")

        # ── 11. Ichimoku Cloud — filtre de tendance primaire ─────────────
        try:
            if len(df) >= 60:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    ich_result = ta.ichimoku(df["high"], df["low"], df["close"],
                                             tenkan=9, kijun=26, senkou=52)
                if ich_result and ich_result[0] is not None and not ich_result[0].empty:
                    ich_df   = ich_result[0]
                    span_a_c = [c for c in ich_df.columns if "ISA" in c]
                    span_b_c = [c for c in ich_df.columns if "ISB" in c]
                    if span_a_c and span_b_c:
                        span_a      = float(ich_df[span_a_c[0]].dropna().iloc[-1])
                        span_b      = float(ich_df[span_b_c[0]].dropna().iloc[-1])
                        cloud_top   = max(span_a, span_b)
                        cloud_bot   = min(span_a, span_b)
                        ind["ich_cloud_top"] = round(cloud_top, 4)
                        ind["ich_cloud_bot"] = round(cloud_bot, 4)
                        if prix > cloud_top:
                            points_buy  += POIDS["ichimoku"]
                            ind["ich_signal"] = "au_dessus_nuage"
                        elif prix < cloud_bot:
                            points_sell += POIDS["ichimoku"]
                            ind["ich_signal"] = "sous_nuage"
                        else:
                            ind["ich_signal"] = "dans_nuage"
        except Exception as e:
            self.logger.debug(f"Ichimoku {ticker}: {e}")

        # ── Feature A : Exposant de Hurst ────────────────────────────────
        try:
            if len(df) >= 40:
                closes_arr = df["close"].values
                ind["hurst"] = _calculer_hurst(closes_arr)
                hurst = ind["hurst"]
                if hurst > 0.55:
                    ind["regime_hurst"] = "TENDANCIEL"
                elif hurst < 0.45:
                    ind["regime_hurst"] = "MEAN_REVERTING"
                else:
                    ind["regime_hurst"] = "ALEATOIRE"
            else:
                ind["hurst"] = 0.5
                ind["regime_hurst"] = "ALEATOIRE"
        except Exception as e:
            self.logger.debug(f"Hurst {ticker}: {e}")
            ind["hurst"] = 0.5
            ind["regime_hurst"] = "ALEATOIRE"

        # ── Feature B : Volatilité Garman-Klass ──────────────────────────
        try:
            if len(df) >= 10 and all(c in df.columns for c in ["open", "high", "low", "close"]):
                gk_vol = _volatilite_garman_klass(
                    df["open"].values[-20:], df["high"].values[-20:],
                    df["low"].values[-20:], df["close"].values[-20:]
                )
                ind["gk_volatility"] = round(gk_vol, 2)
                if gk_vol > 80:
                    ind["vol_regime"] = "CHAOTIQUE"
                elif gk_vol > 40:
                    ind["vol_regime"] = "HAUTE"
                elif gk_vol > 15:
                    ind["vol_regime"] = "NORMALE"
                else:
                    ind["vol_regime"] = "BASSE"
            else:
                ind["gk_volatility"] = 0.0
                ind["vol_regime"] = "NORMALE"
        except Exception as e:
            self.logger.debug(f"Garman-Klass {ticker}: {e}")
            ind["gk_volatility"] = 0.0
            ind["vol_regime"] = "NORMALE"

        # ── Feature C : Volume Delta (pression acheteurs vs vendeurs) ────
        try:
            delta = _calculer_volume_delta(df)
            ind["volume_delta"] = round(delta, 3)
            if delta > 0.3:
                ind["delta_signal"] = "ACHETEURS"
            elif delta < -0.3:
                ind["delta_signal"] = "VENDEURS"
            else:
                ind["delta_signal"] = "EQUILIBRE"
        except Exception as e:
            self.logger.debug(f"Volume delta {ticker}: {e}")
            ind["volume_delta"] = 0.0
            ind["delta_signal"] = "EQUILIBRE"

        # ── Feature H : Autocorrélation des returns (lag 1) ──────────────
        try:
            if len(df) >= 20:
                returns = df["close"].pct_change().dropna().values[-20:]
                if len(returns) >= 10:
                    a = returns[:-1]
                    b = returns[1:]
                    autocorr = float(np.corrcoef(a, b)[0, 1])
                    ind["autocorr"] = round(autocorr, 3)
                    if autocorr < -0.3:
                        ind["autocorr_signal"] = "MEAN_REVERSION"
                    elif autocorr > 0.3:
                        ind["autocorr_signal"] = "MOMENTUM"
                    else:
                        ind["autocorr_signal"] = "NEUTRE"
                else:
                    ind["autocorr"] = 0.0
                    ind["autocorr_signal"] = "NEUTRE"
            else:
                ind["autocorr"] = 0.0
                ind["autocorr_signal"] = "NEUTRE"
        except Exception as e:
            self.logger.debug(f"Autocorr {ticker}: {e}")
            ind["autocorr"] = 0.0
            ind["autocorr_signal"] = "NEUTRE"

        # ── Score composite ───────────────────────────────────────────────
        total_possible = sum(POIDS.values())
        score_buy  = (points_buy  / total_possible) * 100
        score_sell = (points_sell / total_possible) * 100

        if score_buy > score_sell:
            score = round(50 + score_buy / 2, 1)
        else:
            score = round(50 - score_sell / 2, 1)
        score = max(0.0, min(100.0, score))

        # ── Ajustements Hurst (Feature A) ─────────────────────────────────
        regime_hurst = ind.get("regime_hurst", "ALEATOIRE")
        if regime_hurst == "TENDANCIEL":
            # Marché tendanciel : booster si signal BUY avec tendance
            if score_buy > score_sell:
                score = min(100.0, score + 5)
            elif score_sell > score_buy:
                score = max(0.0, score - 5)  # Pénaliser signal contre-tendance
        elif regime_hurst == "MEAN_REVERTING":
            # Mean-reverting : booster les signaux bollinger/vwap (déjà intégrés dans score)
            bb_signal_buy  = (ind.get("bb_lower") is not None and prix <= ind.get("bb_lower", prix + 1))
            vwap_signal_buy = ind.get("vwap_signal") == "au_dessus"
            if bb_signal_buy or vwap_signal_buy:
                score = min(100.0, score + 3)

        # ── Ajustements Volume Delta (Feature C) ──────────────────────────
        delta = ind.get("volume_delta", 0.0)
        if delta > 0.3 and score_buy > score_sell:
            score = min(100.0, score + 4)   # Fort flux acheteur + signal BUY → bonus
        elif delta < -0.3 and score_sell > score_buy:
            score = max(0.0, score - 4)     # Fort flux vendeur + signal SELL → renforcer

        score = max(0.0, min(100.0, score))

        # ── Signal et conviction ──────────────────────────────────────────
        if score >= SEUIL_TECH_BUY:
            signal = "BUY"
        elif score <= SEUIL_TECH_SELL:
            signal = "SELL"
        else:
            signal = "HOLD"

        conviction = (
            "FORTE"   if score >= SEUIL_CONVICTION_FORTE  else
            "MOYENNE" if score >= SEUIL_CONVICTION_FAIBLE else
            "FAIBLE"
        )

        # Raison lisible
        parts = []
        if ind.get("hma_signal") in ("crossover_hausse", "crossover_baisse"):
            parts.append(f"HMA {'↑' if 'hausse' in ind['hma_signal'] else '↓'} crossover")
        if ind.get("macd_hist", 0) > 0: parts.append("MACD↑")
        if ind.get("macd_hist", 0) < 0: parts.append("MACD↓")
        if ind.get("rsi") and ind["rsi"] < RSI_SURVENTE: parts.append(f"RSI survente({ind['rsi']:.0f})")
        if ind.get("rsi") and ind["rsi"] > RSI_SURACHAT: parts.append(f"RSI surachat({ind['rsi']:.0f})")
        if ind.get("adx") and ind["adx"] > ADX_SEUIL: parts.append(f"ADX fort({ind['adx']:.0f})")
        raison = " | ".join(parts) if parts else f"Score composite {score:.0f}/100"

        result.update({
            "signal": signal, "score": score,
            "conviction": conviction, "raison": raison,
            "indicateurs": ind,
        })
        return result


# =============================================================================
# [MODULE 4] AnalyseurSentimentGroq — API Groq Cloud
# =============================================================================

class AnalyseurSentimentGroq:
    """
    Analyse de sentiment via Groq Cloud (groq.com).
    Endpoint : https://api.groq.com/openai/v1/chat/completions
    Modèle   : llama-3.3-70b-versatile — rapide, gratuit, très précis

    Prompt enrichi avec données techniques + contexte secteur + biais Groq.
    Retourne score 0.0–1.0 + résumé + facteurs positifs/négatifs + biais.

    Table de décision (Aywen/Gemini) :
      0.00–0.35 → Panique   : bloquer achats, déclencher ventes
      0.35–0.52 → Inquiétude: temporiser
      0.52–0.70 → Optimisme : valider les signaux techniques
      0.70–1.00 → Euphorie  : renforcer conviction
    """

    GROQ_URL        = "https://api.groq.com/openai/v1/chat/completions"
    MODELE          = "llama-3.3-70b-versatile"
    CACHE_TTL       = 600    # 10 min marché ouvert
    CACHE_TTL_CLOSED= 3600   # 1 h marché fermé — inutile de re-demander
    MAX_RPM         = 25     # Groq free tier = 30 RPM → on reste à 25

    # Variables de classe — partagées entre toutes les instances dans le run
    _backoff_until: float = 0.0   # timestamp fin de backoff 429
    _recent_calls:  list  = []    # timestamps des appels < 60s

    def __init__(self):
        self.logger    = logging.getLogger("Groq")
        self._cache    = {}
        self._cache_ts = {}

    def _secteur(self, ticker: str) -> str:
        for s, tickers in SECTEURS.items():
            if ticker in tickers:
                return s
        return "AUTRES"

    @classmethod
    def _check_rate_limit(cls, now: float) -> bool:
        """Retourne True si on peut appeler (pas en backoff, pas à la limite RPM)."""
        if now < cls._backoff_until:
            return False
        cls._recent_calls = [t for t in cls._recent_calls if now - t < 60]
        return len(cls._recent_calls) < cls.MAX_RPM

    @classmethod
    def _register_call(cls):
        cls._recent_calls.append(time.time())

    @classmethod
    def _set_backoff(cls, seconds: float = 65.0):
        cls._backoff_until = time.time() + seconds

    def scorer(self, ticker: str, prix: float,
               score_tech: float = 50,
               rsi: Optional[float] = None,
               hma_signal: Optional[str] = None,
               atr_pct: Optional[float] = None,
               marche_ouvert: bool = True) -> dict:
        """
        Appelle Groq Cloud pour scorer le sentiment.
        - Cache 10 min (ouvert) / 1 h (fermé)
        - Rate limiter 25 RPM — backoff 65s sur 429
        - Fallback 0.55 si clé absente, backoff actif, ou erreur
        """
        now = time.time()
        ttl = self.CACHE_TTL if marche_ouvert else self.CACHE_TTL_CLOSED

        # ── Cache ─────────────────────────────────────────────────────────
        if ticker in self._cache and (now - self._cache_ts.get(ticker, 0)) < ttl:
            return self._cache[ticker]

        if not GROQ_API_KEY:
            return self._fallback("GROQ_API_KEY absent")

        # ── Rate limit / backoff ──────────────────────────────────────────
        if not self._check_rate_limit(now):
            reste = max(0, int(type(self)._backoff_until - now))
            raison = (f"Backoff 429 — {reste}s restants" if reste
                      else f"Rate limit {self.MAX_RPM} RPM — pause")
            # Retourner le cache périmé si dispo plutôt que fallback neutre
            if ticker in self._cache:
                return self._cache[ticker]
            return self._fallback(raison)

        secteur = self._secteur(ticker)
        rsi_str = f"RSI={rsi:.1f}" if rsi else "RSI=N/A"
        hma_str = f"HMA_signal={hma_signal}" if hma_signal else ""
        atr_str = f"ATR={atr_pct:.2f}%" if atr_pct else ""

        prompt = f"""Tu es un analyste quantitatif expert en psychologie de marché et trading algorithmique.

=== ACTIF ===
Ticker  : {ticker}
Secteur : {secteur}
Prix    : ${prix:.4f}
Données : {rsi_str} | {hma_str} | {atr_str} | Score technique : {score_tech:.0f}/100
Heure   : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

=== MISSION ===
Évalue le sentiment de marché actuel pour {ticker} en intégrant :
1. Contexte macroéconomique général (taux Fed, inflation, risk-on/off)
2. Psychologie des traders retail et institutionnels sur cet actif
3. Dynamiques sectorielles ({secteur})
4. Cohérence avec les données techniques fournies

=== ÉCHELLE ===
0.0 = Panique absolue | 0.5 = Neutralité | 1.0 = Euphorie totale

=== FORMAT STRICT ===
JSON uniquement, sans texte autour :
{{"score": <float 0.0-1.0>, "confiance": <float 0.0-1.0>, "resume": "<phrase>",
  "facteurs_positifs": ["<f1>","<f2>"], "facteurs_negatifs": ["<f1>","<f2>"],
  "biais_court_terme": "<haussier|baissier|neutre>"}}"""

        try:
            self._register_call()
            r = requests.post(
                self.GROQ_URL,
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       self.MODELE,
                    "max_tokens":  350,
                    "temperature": 0.25,
                    "messages": [
                        {"role": "system",
                         "content": "Tu es un expert en analyse de sentiment financier. Réponds uniquement en JSON valide."},
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=8,
            )
            r.raise_for_status()
            texte = r.json()["choices"][0]["message"]["content"].strip()
            texte = texte.replace("```json", "").replace("```", "").strip()
            data  = json.loads(texte)

            score    = float(max(0.0, min(1.0, data.get("score", 0.55))))
            resultat = {
                "score":             round(score, 3),
                "confiance":         float(data.get("confiance", 0.5)),
                "resume":            str(data.get("resume", "")),
                "facteurs_positifs": data.get("facteurs_positifs", []),
                "facteurs_negatifs": data.get("facteurs_negatifs", []),
                "biais":             data.get("biais_court_terme", "neutre"),
                "source":            "groq/" + self.MODELE,
            }
            self._cache[ticker]    = resultat
            self._cache_ts[ticker] = now
            self.logger.info(f"Groq {ticker}: score={score:.3f} biais={resultat['biais']}")
            return resultat

        except Exception as e:
            msg = str(e)
            if "429" in msg:
                self._set_backoff(65.0)
                self.logger.warning(
                    f"Groq 429 — backoff 65s "
                    f"(RPM utilisés : {len(type(self)._recent_calls)}/{self.MAX_RPM})"
                )
            else:
                self.logger.warning(f"Groq erreur {ticker}: {msg[:80]}")
            if ticker in self._cache:   # Retourne cache périmé plutôt que neutre
                return self._cache[ticker]
            return self._fallback(msg)

    def _fallback(self, raison: str) -> dict:
        return {
            "score": 0.55, "confiance": 0.3,
            "resume": f"Sentiment indisponible ({raison[:60]})",
            "facteurs_positifs": [], "facteurs_negatifs": [],
            "biais": "neutre", "source": "fallback",
        }


# =============================================================================
# [MODULE 4bis] AnalyseurSentimentGemini — Google AI Studio (fallback gratuit)
# =============================================================================

GOOGLE_AI_KEY = os.environ.get("GOOGLE_AI_KEY", "")   # console.cloud.google.com/ai

class AnalyseurSentimentGemini:
    """
    Fallback sentiment via Google AI Studio (Gemini 2.0 Flash — gratuit).
    Endpoint : https://generativelanguage.googleapis.com/v1beta/models/...
    Limite   : 15 RPM / 1500 req/jour (free tier) — largement suffisant.
    Activé uniquement si Groq renvoie un fallback ET que GOOGLE_AI_KEY est défini.
    """

    GEMINI_URL  = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.0-flash:generateContent"
    )
    MODELE      = "gemini-2.0-flash"
    CACHE_TTL   = 600
    CACHE_TTL_CLOSED = 3600

    _backoff_until: float = 0.0
    _recent_calls:  list  = []
    MAX_RPM = 14  # Free tier = 15 RPM

    def __init__(self):
        self.logger    = logging.getLogger("Gemini")
        self._cache    = {}
        self._cache_ts = {}

    def _secteur(self, ticker: str) -> str:
        for s, tickers in SECTEURS.items():
            if ticker in tickers:
                return s
        return "AUTRES"

    @classmethod
    def _check_rate_limit(cls, now: float) -> bool:
        if now < cls._backoff_until:
            return False
        cls._recent_calls = [t for t in cls._recent_calls if now - t < 60]
        return len(cls._recent_calls) < cls.MAX_RPM

    def scorer(self, ticker: str, prix: float,
               score_tech: float = 50,
               rsi: Optional[float] = None,
               hma_signal: Optional[str] = None,
               atr_pct: Optional[float] = None,
               marche_ouvert: bool = True) -> dict:
        """Appelle Gemini 2.0 Flash pour le sentiment — même interface que Groq."""
        now = time.time()
        ttl = self.CACHE_TTL if marche_ouvert else self.CACHE_TTL_CLOSED

        if ticker in self._cache and (now - self._cache_ts.get(ticker, 0)) < ttl:
            return self._cache[ticker]

        if not GOOGLE_AI_KEY:
            return self._fallback("GOOGLE_AI_KEY absent")

        if not self._check_rate_limit(now):
            if ticker in self._cache:
                return self._cache[ticker]
            return self._fallback("Gemini rate limit")

        secteur = self._secteur(ticker)
        rsi_str = f"RSI={rsi:.1f}" if rsi else "RSI=N/A"
        hma_str = f"HMA={hma_signal}" if hma_signal else ""
        atr_str = f"ATR={atr_pct:.2f}%" if atr_pct else ""

        prompt = f"""Tu es un analyste quantitatif expert en trading algorithmique.

Ticker: {ticker} | Secteur: {secteur} | Prix: ${prix:.4f}
{rsi_str} | {hma_str} | {atr_str} | Score technique: {score_tech:.0f}/100

Évalue le sentiment de marché pour {ticker} (contexte macro, psychologie, secteur).

Réponds UNIQUEMENT en JSON valide :
{{"score": <float 0.0-1.0>, "confiance": <float 0.0-1.0>, "resume": "<phrase>",
  "facteurs_positifs": ["<f1>"], "facteurs_negatifs": ["<f1>"],
  "biais_court_terme": "<haussier|baissier|neutre>"}}"""

        try:
            type(self)._recent_calls.append(now)
            r = requests.post(
                self.GEMINI_URL,
                params={"key": GOOGLE_AI_KEY},
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "systemInstruction": {
                        "parts": [{"text": "Expert en sentiment financier. JSON uniquement."}]
                    },
                    "generationConfig": {"temperature": 0.25, "maxOutputTokens": 350},
                },
                timeout=10,
            )
            r.raise_for_status()
            texte = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            texte = texte.replace("```json", "").replace("```", "").strip()
            data  = json.loads(texte)

            score    = float(max(0.0, min(1.0, data.get("score", 0.55))))
            resultat = {
                "score":             round(score, 3),
                "confiance":         float(data.get("confiance", 0.5)),
                "resume":            str(data.get("resume", "")),
                "facteurs_positifs": data.get("facteurs_positifs", []),
                "facteurs_negatifs": data.get("facteurs_negatifs", []),
                "biais":             data.get("biais_court_terme", "neutre"),
                "source":            "gemini/" + self.MODELE,
            }
            self._cache[ticker]    = resultat
            self._cache_ts[ticker] = now
            self.logger.info(f"Gemini {ticker}: score={score:.3f} biais={resultat['biais']}")
            return resultat

        except Exception as e:
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                type(self)._backoff_until = time.time() + 65
                self.logger.warning("Gemini 429 — backoff 65s")
            else:
                self.logger.warning(f"Gemini erreur {ticker}: {msg[:80]}")
            if ticker in self._cache:
                return self._cache[ticker]
            return self._fallback(msg)

    def _fallback(self, raison: str) -> dict:
        return {
            "score": 0.55, "confiance": 0.3,
            "resume": f"Gemini indisponible ({raison[:60]})",
            "facteurs_positifs": [], "facteurs_negatifs": [],
            "biais": "neutre", "source": "fallback",
        }


# =============================================================================
# [MODULE 4e] FearGreedIndex — Indice Fear & Greed (alternative.me)
# =============================================================================

class FearGreedIndex:
    """
    Récupère l'indice Fear & Greed via alternative.me (gratuit, sans clé).
    0 = Peur extrême (opportunité d'achat) | 100 = Euphorie (prudence).
    Cache 1 heure — inutile de rafraîchir plus souvent.
    """
    URL       = "https://api.alternative.me/fng/?limit=2"
    CACHE_TTL = 3600  # 1 heure

    def __init__(self):
        self.logger    = logging.getLogger("FearGreed")
        self._cache    = {}
        self._cache_ts = 0.0

    def get(self) -> dict:
        now = time.time()
        if now - self._cache_ts < self.CACHE_TTL and self._cache:
            return self._cache
        try:
            r = requests.get(self.URL, timeout=8,
                             headers={"User-Agent": "RouteBot/4.1"})
            r.raise_for_status()
            data  = r.json()["data"][0]
            value = int(data["value"])
            label = data["value_classification"]
            prev  = int(r.json()["data"][1]["value"]) if len(r.json()["data"]) > 1 else value
            result = {
                "value":         value,
                "label":         label,
                "prev":          prev,
                "delta":         value - prev,
                "zone":          ("extreme_fear" if value < 25 else
                                  "fear"         if value < 45 else
                                  "neutral"      if value < 55 else
                                  "greed"        if value < 75 else "extreme_greed"),
                "timestamp":     datetime.now(timezone.utc).isoformat(),
            }
            self._cache    = result
            self._cache_ts = now
            self.logger.info(f"Fear & Greed : {value} — {label} (prev {prev})")
            return result
        except Exception as e:
            self.logger.warning(f"Fear & Greed: {e}")
            return self._cache or {
                "value": 50, "label": "Neutral", "prev": 50, "delta": 0,
                "zone": "neutral", "timestamp": datetime.now(timezone.utc).isoformat()
            }


# =============================================================================
# [MODULE 4f] YahooFinanceClient — Données gratuites Yahoo Finance
# =============================================================================

class YahooFinanceClient:
    """
    Données gratuites Yahoo Finance sans clé API :
      - Earnings dates → blackout ±3j avant/après publication
      - Analyst target price → upside potentiel
      - Short ratio → potentiel short squeeze
      - 52w high/low → niveau breakout
      - Revenue growth → qualité fondamentale
      - Pre-market price → direction anticipée
      - Trending tickers US → enrichit la rotation watchlist
      - News headlines → sentiment additionnel
    Cache adaptatif : 24h earnings, 1h fundamentaux, 30min trending.
    """

    BASE     = "https://query1.finance.yahoo.com"
    HEADERS  = {"User-Agent": "Mozilla/5.0 (compatible; RouteBot/4.1)"}

    def __init__(self):
        self.logger     = logging.getLogger("Yahoo")
        self._earn      = {}     # ticker → liste de dates
        self._earn_ts   = {}
        self._fund      = {}     # ticker → dict fondamentaux
        self._fund_ts   = {}
        self._trend     = []
        self._trend_ts  = 0.0

    # ── Earnings dates ──────────────────────────────────────────────────────
    def get_earnings_dates(self, ticker: str) -> list:
        now = time.time()
        if ticker in self._earn and now - self._earn_ts.get(ticker, 0) < 86400:
            return self._earn[ticker]
        try:
            url = f"{self.BASE}/v10/finance/quoteSummary/{ticker}?modules=calendarEvents"
            r   = requests.get(url, headers=self.HEADERS, timeout=8)
            r.raise_for_status()
            res   = r.json().get("quoteSummary", {}).get("result", [{}])
            cal   = (res[0] if res else {}).get("calendarEvents", {})
            dates = []
            for ed in cal.get("earnings", {}).get("earningsDate", []):
                raw = ed.get("raw")
                if raw:
                    dates.append(datetime.fromtimestamp(raw, tz=timezone.utc).isoformat())
            self._earn[ticker]    = dates
            self._earn_ts[ticker] = now
            return dates
        except Exception as e:
            self.logger.debug(f"Yahoo earnings {ticker}: {e}")
            return self._earn.get(ticker, [])

    def est_blackout_earnings(self, ticker: str, jours: int = 3) -> bool:
        """True si une publication de résultats est prévue dans ±jours jours."""
        if "/" in ticker:
            return False  # Crypto n'a pas d'earnings
        now = datetime.now(timezone.utc)
        for d_str in self.get_earnings_dates(ticker):
            try:
                d = datetime.fromisoformat(d_str.replace("Z", "+00:00"))
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                if abs((d - now).total_seconds()) / 86400 <= jours:
                    return True
            except Exception:
                pass
        return False

    # ── Fondamentaux ────────────────────────────────────────────────────────
    def get_fondamentaux(self, ticker: str) -> dict:
        """Retourne target_price, short_ratio, 52w_high, revenue_growth, pre_market, analyst_rating."""
        now = time.time()
        if ticker in self._fund and now - self._fund_ts.get(ticker, 0) < 3600:
            return self._fund[ticker]
        if "/" in ticker:
            return {}  # Pas de fondamentaux pour crypto
        try:
            modules = "summaryDetail,financialData,defaultKeyStatistics,price"
            url = f"{self.BASE}/v10/finance/quoteSummary/{ticker}?modules={modules}"
            r   = requests.get(url, headers=self.HEADERS, timeout=10)
            r.raise_for_status()
            res  = r.json().get("quoteSummary", {}).get("result", [{}])
            data = res[0] if res else {}

            def v(d, k):
                x = d.get(k, {})
                return x.get("raw") if isinstance(x, dict) else x

            sd  = data.get("summaryDetail", {})
            fd  = data.get("financialData", {})
            ks  = data.get("defaultKeyStatistics", {})
            pr  = data.get("price", {})

            target        = v(fd, "targetMeanPrice")
            current       = v(pr, "regularMarketPrice")
            upside        = round((target - current) / current * 100, 1) if target and current and current > 0 else None
            analyst_key   = fd.get("recommendationKey", "")

            result = {
                "target_price":      target,
                "current_price":     current,
                "upside_pct":        upside,
                "analyst_rating":    analyst_key,
                "revenue_growth":    v(fd, "revenueGrowth"),
                "short_ratio":       v(ks, "shortRatio"),
                "short_pct_float":   v(ks, "shortPercentOfFloat"),
                "week52_high":       v(sd, "fiftyTwoWeekHigh"),
                "week52_low":        v(sd, "fiftyTwoWeekLow"),
                "pe_ratio":          v(sd, "trailingPE"),
                "pre_market_price":  v(pr, "preMarketPrice"),
                "pre_market_change": v(pr, "preMarketChangePercent"),
                "squeeze_potential": bool(v(ks, "shortRatio") and (v(ks, "shortRatio") or 0) > 8),
            }
            self._fund[ticker]    = result
            self._fund_ts[ticker] = now
            return result
        except Exception as e:
            self.logger.debug(f"Yahoo fondamentaux {ticker}: {e}")
            return self._fund.get(ticker, {})

    # ── Trending tickers US ─────────────────────────────────────────────────
    def get_trending(self) -> list:
        """Retourne les tickers US en tendance sur Yahoo Finance (cache 30min)."""
        now = time.time()
        if now - self._trend_ts < 1800 and self._trend:
            return self._trend
        try:
            url = f"{self.BASE}/v1/finance/trending/US?count=20"
            r   = requests.get(url, headers=self.HEADERS, timeout=8)
            r.raise_for_status()
            quotes = r.json().get("finance", {}).get("result", [{}])[0].get("quotes", [])
            self._trend    = [q["symbol"] for q in quotes if "symbol" in q]
            self._trend_ts = now
            self.logger.info(f"Yahoo trending : {self._trend[:5]}...")
            return self._trend
        except Exception as e:
            self.logger.debug(f"Yahoo trending: {e}")
            return self._trend

    # ── News headlines sentiment ────────────────────────────────────────────
    def get_news_sentiment(self, ticker: str) -> float:
        """Score 0.0-1.0 basé sur les titres des dernières news Yahoo Finance."""
        try:
            url = f"{self.BASE}/v1/finance/search?q={ticker}&newsCount=8&quotesCount=0"
            r   = requests.get(url, headers=self.HEADERS, timeout=8)
            r.raise_for_status()
            news = r.json().get("news", [])
            if not news:
                return 0.5
            texte = " ".join(n.get("title", "") for n in news).upper()
            pos = sum(texte.count(m) for m in
                      ["BEAT", "SURGE", "RALLY", "BUY", "UPGRADE", "BULL", "GROWTH", "RECORD", "GAIN"])
            neg = sum(texte.count(m) for m in
                      ["MISS", "CRASH", "SELL", "DOWNGRADE", "BEAR", "LOSS", "DECLINE", "CUT", "WARN"])
            total = pos + neg
            return round(pos / total, 2) if total > 0 else 0.5
        except Exception:
            return 0.5


# =============================================================================
# [MODULE 4c-bis] StrategieSachaPro — Traduction Python du Pine Script TradingView
# =============================================================================

class StrategieSachaPro:
    """
    Traduction Python de « Sacha Pro – Multi-TF Strategy v3 » (TradingView Pine Script v5).

    ARCHITECTURE :
      • Mode LONG TERME uniquement  → barres DAILY (défaut recommandé)
        Signal : EMA9 croise EMA21 + prix > EMA50 + RSI non surachat + volume ✓
      • Mode COURT TERME désactivé → BB + Stoch + VWAP sur barres intraday
        (documenté mais désactivé : performance inférieure selon backtests utilisateur)

    INTÉGRATION dans le bot :
      • N'écrase PAS le score existant (HMA/MACD/RSI/BB/ADX)
      • Agit comme couche de CONFIRMATION multiplicative :
          BUY confirmé  → score_tech × 1.12   (+12%)
          SELL confirmé → score_tech × 0.85   (−15% → réduit la confiance achat)
          NEUTRE        → pas de modification
      • Résultat stocké dans base["sacha_pro"] pour le dashboard

    PARAMÈTRES (fidèles au Pine Script) :
      EMA rapide  = 9   | EMA lente   = 21   | EMA tendance = 50
      RSI surachat = 65 | RSI survente = 35
      SL = 2%           | TP = 4%
      Volume filtre : > 1.2× SMA20 des volumes
    """

    # ── EMA (tendance) ────────────────────────────────────────────────────────
    EMA_FAST  = 9
    EMA_SLOW  = 21
    EMA_TREND = 50

    # ── RSI (long terme) ──────────────────────────────────────────────────────
    RSI_LEN   = 14
    RSI_OB    = 65   # surachat → bloque les achats
    RSI_OS    = 35   # survente → bloque les ventes

    # ── BB / Stoch (court terme — documenté, désactivé) ──────────────────────
    BB_LEN    = 20
    BB_MULT   = 2.0
    STOCH_K   = 14
    STOCH_D   = 3
    STOCH_OB  = 80
    STOCH_OS  = 20

    # ── Gestion du risque ─────────────────────────────────────────────────────
    SL_PCT    = 2.0   # Stop-Loss 2%  (référence, appliqué par GestionnaireRisque)
    TP_PCT    = 4.0   # Take-Profit 4% (ratio 1:2)
    VOL_MULT  = 1.2   # Volume min = 1.2× SMA20

    # ── Boost/malus appliqué au score technique existant ─────────────────────
    BOOST_BUY  = 1.12   # signal BUY confirmé  → × 1.12
    BOOST_SELL = 0.85   # signal SELL confirmé → × 0.85 (réduit confiance achat)

    def _calcul_rsi(self, close: "pd.Series") -> "pd.Series":
        """RSI Wilder (identique à TradingView ta.rsi)."""
        delta    = close.diff()
        gain     = delta.clip(lower=0)
        loss     = (-delta).clip(lower=0)
        avg_gain = gain.ewm(com=self.RSI_LEN - 1, adjust=False, min_periods=self.RSI_LEN).mean()
        avg_loss = loss.ewm(com=self.RSI_LEN - 1, adjust=False, min_periods=self.RSI_LEN).mean()
        rs       = avg_gain / avg_loss.replace(0, 1e-10)
        return 100 - 100 / (1 + rs)

    def analyser(self, ticker: str, df: pd.DataFrame) -> dict:
        """
        Analyse les barres DAILY d'un ticker.
        Retourne un dict avec signal, détails et niveaux SL/TP de référence.
        """
        result = {
            "signal":         "NEUTRAL",
            "sl_pct":         self.SL_PCT,
            "tp_pct":         self.TP_PCT,
            "ema_fast":       None,
            "ema_slow":       None,
            "ema_trend":      None,
            "rsi":            None,
            "ema_crossover":  False,   # EMA9 vient de croiser EMA21 à la hausse
            "ema_crossunder": False,   # EMA9 vient de croiser EMA21 à la baisse
            "vol_ok":         False,
            "prix":           None,
            "details":        "Données insuffisantes",
            "boost_applique": 1.0,
        }

        if df is None or df.empty or len(df) < self.EMA_TREND + 5:
            return result

        try:
            close  = df["close"].astype(float)
            volume = df["volume"].astype(float)

            # ── EMA (identique Pine Script ta.ema) ───────────────────────────
            ema_f = close.ewm(span=self.EMA_FAST,  adjust=False).mean()
            ema_s = close.ewm(span=self.EMA_SLOW,  adjust=False).mean()
            ema_t = close.ewm(span=self.EMA_TREND, adjust=False).mean()

            # ── RSI Wilder ────────────────────────────────────────────────────
            rsi = self._calcul_rsi(close)

            # ── Volume : volume > SMA20(volume) × VOL_MULT ────────────────────
            vol_ma = volume.rolling(20).mean()
            vol_ok = bool(len(volume) >= 20 and
                          volume.iloc[-1] > vol_ma.iloc[-1] * self.VOL_MULT)

            # ── Valeurs courantes et précédentes ──────────────────────────────
            f_now,  f_prev  = float(ema_f.iloc[-1]),  float(ema_f.iloc[-2])
            s_now,  s_prev  = float(ema_s.iloc[-1]),  float(ema_s.iloc[-2])
            t_now           = float(ema_t.iloc[-1])
            rsi_now         = float(rsi.iloc[-1])
            prix            = float(close.iloc[-1])

            # ── Croisements (ta.crossover / ta.crossunder en Pine Script) ─────
            # crossover  = bar précédente f < s  ET  bar actuelle f > s
            ema_crossover  = (f_prev <= s_prev) and (f_now > s_now)
            # crossunder = bar précédente f > s  ET  bar actuelle f < s
            ema_crossunder = (f_prev >= s_prev) and (f_now < s_now)

            # ── Signaux LONG TERME (Pine Script lt_buy / lt_sell) ─────────────
            #   lt_buy  = crossover(EMA9, EMA21) AND prix > EMA50 AND RSI < RSI_OB AND vol_ok
            #   lt_sell = crossunder(EMA9, EMA21) AND prix < EMA50 AND RSI > RSI_OS AND vol_ok
            lt_buy  = ema_crossover  and prix > t_now and rsi_now < self.RSI_OB and vol_ok
            lt_sell = ema_crossunder and prix < t_now and rsi_now > self.RSI_OS and vol_ok

            # Note : même sans croisement AUJOURD'HUI, on signale la tendance
            # en cours (utile pour le dashboard)
            tendance_haussiere = f_now > s_now and prix > t_now and rsi_now < self.RSI_OB
            tendance_baissiere = f_now < s_now and prix < t_now and rsi_now > self.RSI_OS

            signal = "BUY" if lt_buy else ("SELL" if lt_sell else "NEUTRAL")
            boost  = (self.BOOST_BUY  if signal == "BUY"  else
                      self.BOOST_SELL if signal == "SELL" else 1.0)

            details = (
                f"EMA{self.EMA_FAST}={f_now:.2f} "
                f"{'>' if f_now > s_now else '<'} "
                f"EMA{self.EMA_SLOW}={s_now:.2f} | "
                f"EMA{self.EMA_TREND}={t_now:.2f} | "
                f"RSI={rsi_now:.1f} | "
                f"Vol={'✓' if vol_ok else '✗'} | "
                f"{'🔺 Crossover !' if ema_crossover else '🔻 Crossunder !' if ema_crossunder else ('↗ Tendance haussière' if tendance_haussiere else '↘ Tendance baissière' if tendance_baissiere else '↔ Neutre')}"
            )

            result.update({
                "signal":         signal,
                "ema_fast":       round(f_now,  4),
                "ema_slow":       round(s_now,  4),
                "ema_trend":      round(t_now,  4),
                "rsi":            round(rsi_now, 2),
                "ema_crossover":  ema_crossover,
                "ema_crossunder": ema_crossunder,
                "vol_ok":         vol_ok,
                "prix":           round(prix, 4),
                "details":        details,
                "boost_applique": boost,
                "tendance_haussiere": tendance_haussiere,
                "tendance_baissiere": tendance_baissiere,
            })

        except Exception as e:
            result["details"] = f"Erreur calcul: {e}"

        return result


# =============================================================================
# [MODULE 4d] PortefeuilleLongTerme — Mini-portefeuille Sacha Pro (expérimental)
# =============================================================================

class PortefeuilleLongTerme:
    """
    Gère un mini-portefeuille long terme basé EXCLUSIVEMENT sur les signaux
    de StrategieSachaPro (crossovers EMA daily).

    PRINCIPE :
      • Budget séparé et limité (LT_ALLOC_PCT % de l'equity, max LT_MAX_USD)
      • Les positions LT ne peuvent PAS être vendues par le bot court-terme
      • Seul un crossunder EMA daily déclenche la vente
      • Stockage persistant dans data/portefeuille_lt.json (survit aux redémarrages)

    CYCLE DE VIE D'UNE POSITION LT :
      1. Sacha Pro détecte EMA9 crossover EMA21 (daily) + conditions remplies
      2. PortefeuilleLongTerme.ouvrir_position() → achat Alpaca + sauvegarde JSON
      3. Chaque cycle : vérifier si EMA9 crossunder EMA21 → vendre si oui
      4. Protection : _analyser_actif() ignore VENTE sur tickers LT

    STATUS EXPÉRIMENTAL : allocation volontairement petite, max 3 positions.
    """

    DATA_PATH = Path("data/portefeuille_lt.json")

    def __init__(self):
        self.logger    = logging.getLogger("PortefeuilleLT")
        self.positions: dict = {}   # {ticker: {entry_price, notional, entry_date, sacha_details}}
        self._charger()

    # ── Persistance ───────────────────────────────────────────────────────────
    def _charger(self):
        """Charge les positions depuis le fichier JSON."""
        try:
            if self.DATA_PATH.exists():
                data = json.loads(self.DATA_PATH.read_text(encoding="utf-8"))
                self.positions = data.get("positions", {})
                self.logger.info(
                    f"📂 Portefeuille LT chargé : {len(self.positions)} position(s) "
                    f"— {list(self.positions.keys())}"
                )
        except Exception as e:
            self.logger.warning(f"Chargement portefeuille LT: {e}")
            self.positions = {}

    def sauvegarder(self):
        """Persiste les positions dans data/portefeuille_lt.json."""
        try:
            self.DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "positions": self.positions,
                "nb_positions": len(self.positions),
                "derniere_maj": datetime.now(timezone.utc).isoformat(),
                "mode": "experimental",
            }
            self.DATA_PATH.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            self.logger.error(f"Sauvegarde portefeuille LT: {e}")

    # ── Gestion des positions ─────────────────────────────────────────────────
    def est_position_lt(self, ticker: str) -> bool:
        """Retourne True si ce ticker est actuellement une position long terme."""
        return ticker in self.positions or ticker.replace("/", "") in self.positions

    def nb_positions(self) -> int:
        return len(self.positions)

    def ouvrir_position(self, ticker: str, prix: float, notional: float,
                        sacha_details: dict = None) -> bool:
        """Enregistre l'ouverture d'une position LT."""
        try:
            self.positions[ticker] = {
                "entry_price":   prix,
                "notional":      notional,
                "entry_date":    datetime.now(timezone.utc).isoformat(),
                "sacha_details": sacha_details or {},
                "status":        "open",
                "highest_price": prix,   # pour trailing reference
            }
            self.sauvegarder()
            self.logger.info(
                f"📈 [LT] Position ouverte : {ticker} | "
                f"${notional:.0f} @ ${prix:.2f}"
            )
            return True
        except Exception as e:
            self.logger.error(f"Ouverture position LT {ticker}: {e}")
            return False

    def fermer_position(self, ticker: str, prix_sortie: float = None,
                        raison: str = "EMA crossunder") -> dict:
        """Enregistre la fermeture et retourne les stats de la trade."""
        pos = self.positions.pop(ticker, self.positions.pop(ticker.replace("/", ""), None))
        self.sauvegarder()
        if pos and prix_sortie:
            pnl_pct = (prix_sortie - pos["entry_price"]) / pos["entry_price"] * 100
            self.logger.info(
                f"📉 [LT] Position fermée : {ticker} | "
                f"PnL: {pnl_pct:+.2f}% | Raison: {raison}"
            )
            return {"ticker": ticker, "pnl_pct": round(pnl_pct, 2), "raison": raison}
        return {"ticker": ticker, "pnl_pct": None, "raison": raison}

    def get_resume(self) -> dict:
        """Retourne un résumé JSON-sérialisable pour le dashboard."""
        return {
            "positions":      self.positions,
            "nb_positions":   len(self.positions),
            "tickers":        list(self.positions.keys()),
            "mode":           "experimental",
            "config": {
                "alloc_pct":     LT_ALLOC_PCT,
                "max_positions": LT_MAX_POSITIONS,
                "max_usd":       LT_MAX_USD,
            }
        }


# =============================================================================
# [MODULE 4c] VeilleReddit — Sentiment Reddit sans clé API
# =============================================================================

class VeilleReddit:
    """
    Veille sentiment Reddit r/wallstreetbets + r/stocks via API JSON publique.
    Aucune clé API nécessaire. Détecte les tickers les plus mentionnés.
    """

    URLS = [
        "https://www.reddit.com/r/wallstreetbets/hot.json?limit=100",
        "https://www.reddit.com/r/stocks/hot.json?limit=50",
    ]
    HEADERS   = {"User-Agent": "Mozilla/5.0 RouteBot/4.1 (trading research)"}
    CACHE_TTL = 1800  # 30 min

    def __init__(self):
        self.logger      = logging.getLogger("Reddit")
        self._cache_ts   = 0.0
        self._cache_data = {}

    def analyser(self, actifs: list) -> dict:
        now = time.time()
        if now - self._cache_ts < self.CACHE_TTL and self._cache_data:
            return self._cache_data
        try:
            textes = []
            for url in self.URLS:
                try:
                    r = requests.get(url, headers=self.HEADERS, timeout=8)
                    if r.status_code == 200:
                        posts = r.json().get("data", {}).get("children", [])
                        for p in posts:
                            d = p.get("data", {})
                            textes.append(
                                (d.get("title", "") + " " + d.get("selftext", "")[:300])
                            )
                except Exception as e:
                    self.logger.debug(f"Reddit {url[:40]}: {e}")
                time.sleep(0.5)

            if not textes:
                return self._cache_data or {
                    "top_tickers": [], "sentiment_global": 0.5,
                    "nb_posts": 0, "timestamp": datetime.now(timezone.utc).isoformat()
                }

            texte_global = " ".join(textes).upper()
            mentions = {}
            for ticker in actifs:
                t = ticker.replace("/USD", "")
                count = texte_global.count(f" {t} ") + texte_global.count(f"${t}")
                if count > 0:
                    mentions[t] = count

            top = sorted(mentions.items(), key=lambda x: x[1], reverse=True)[:10]
            mots_pos = sum(texte_global.count(m) for m in
                           ["BULL", "CALLS", "BUY", "MOON", "YOLO", "SQUEEZE", "PUMP"])
            mots_neg = sum(texte_global.count(m) for m in
                           ["BEAR", "PUTS", "SELL", "CRASH", "SHORT", "RIP", "DUMP"])
            total = mots_pos + mots_neg
            sent  = round(mots_pos / total, 2) if total > 0 else 0.5

            result = {
                "timestamp":        datetime.now(timezone.utc).isoformat(),
                "top_tickers":      [{"ticker": t, "mentions": c} for t, c in top],
                "sentiment_global": sent,
                "nb_posts":         len(textes),
            }
            self._cache_data = result
            self._cache_ts   = now
            self.logger.info(f"Reddit: {len(top)} tickers mentionnés | sent={sent:.2f} | {len(textes)} posts")
            return result
        except Exception as e:
            self.logger.warning(f"Veille Reddit: {e}")
            return self._cache_data or {
                "top_tickers": [], "sentiment_global": 0.5,
                "nb_posts": 0, "timestamp": datetime.now(timezone.utc).isoformat()
            }


# =============================================================================
# [MODULE 4c-extra] GestionnaireBlacklist — Feature 4: Faux signaux
# =============================================================================

class GestionnaireBlacklist:
    """Blackliste temporairement les tickers avec trop de faux signaux."""

    def __init__(self):
        self.logger = logging.getLogger("Blacklist")
        self.data: dict = {}   # {ticker: {"pertes_consecutives": N, "blacklist_jusqu": timestamp}}
        self._charger()

    def _charger(self):
        try:
            if BLACKLIST_FILE.exists():
                with open(BLACKLIST_FILE, encoding="utf-8") as f:
                    self.data = json.load(f)
        except Exception:
            self.data = {}

    def _sauvegarder(self):
        try:
            BLACKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(BLACKLIST_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            self.logger.warning(f"Blacklist save: {e}")

    def est_blackliste(self, ticker: str) -> bool:
        """Retourne True si le ticker est actuellement blacklisté."""
        info = self.data.get(ticker, {})
        jusqu = info.get("blacklist_jusqu", 0)
        return time.time() < jusqu

    def enregistrer_resultat(self, ticker: str, pnl: float, discord_fn=None):
        """Enregistre le résultat d'un trade et blackliste si nécessaire."""
        if ticker not in self.data:
            self.data[ticker] = {"pertes_consecutives": 0, "blacklist_jusqu": 0}

        if pnl < 0:
            self.data[ticker]["pertes_consecutives"] += 1
            consecutives = self.data[ticker]["pertes_consecutives"]
            if consecutives >= BLACKLIST_SEUIL_PERTES:
                expiry = time.time() + BLACKLIST_DUREE_H * 3600
                self.data[ticker]["blacklist_jusqu"] = expiry
                msg = (
                    f"🚫 **BLACKLIST** `{ticker}` — {consecutives} pertes consécutives\n"
                    f"Trading suspendu pendant {BLACKLIST_DUREE_H}h\n"
                    f"Pattern répété sans succès détecté 🔍"
                )
                self.logger.warning(f"[BLACKLIST] {ticker} — {consecutives} pertes → suspendu 48h")
                if discord_fn:
                    discord_fn(msg)
        else:
            # Gain → réinitialiser le compteur
            self.data[ticker]["pertes_consecutives"] = 0

        self._sauvegarder()

    def get_blacklistes(self) -> list:
        now = time.time()
        return [t for t, v in self.data.items() if now < v.get("blacklist_jusqu", 0)]

    def get_pertes_consecutives_global(self) -> int:
        """Retourne le nb max de pertes consécutives parmi tous les tickers."""
        return max((v.get("pertes_consecutives", 0) for v in self.data.values()), default=0)


# =============================================================================
# [MODULE 4c-extra2] MeteoEconomique — Feature 9: Météo économique mondiale
# =============================================================================

class MeteoEconomique:
    """
    Surveillance macro: EUR/USD, GLD (or), pétrole (USO).
    Retourne un signal de risque global pour ajuster l'agressivité du bot.
    """
    CACHE_TTL = 1800  # 30 min

    def __init__(self):
        self.logger = logging.getLogger("Meteo")
        self._cache = {}
        self._cache_ts = 0

    def analyser(self, alpaca_client) -> dict:
        if time.time() - self._cache_ts < self.CACHE_TTL and self._cache:
            return self._cache

        result = {"signal": "NEUTRE", "score_risque": 50, "details": []}
        score_risque = 50

        try:
            # GLD (or) — hausse = peur dans le marché
            df_gld = alpaca_client.get_barres("GLD")
            if df_gld is not None and not df_gld.empty and len(df_gld) >= 5:
                gld_change = (df_gld["close"].iloc[-1] - df_gld["close"].iloc[-5]) / df_gld["close"].iloc[-5] * 100
                if gld_change > 1.5:
                    score_risque += 15
                    result["details"].append(f"Or +{gld_change:.1f}% (signal de peur)")
                elif gld_change < -1.0:
                    score_risque -= 10
                    result["details"].append(f"Or {gld_change:.1f}% (confiance marché)")
        except Exception as e:
            self.logger.debug(f"GLD: {e}")

        try:
            # USO (pétrole) — forte hausse = inflation = mauvais pour tech
            df_uso = alpaca_client.get_barres("USO")
            if df_uso is not None and not df_uso.empty and len(df_uso) >= 5:
                uso_change = (df_uso["close"].iloc[-1] - df_uso["close"].iloc[-5]) / df_uso["close"].iloc[-5] * 100
                if uso_change > 3.0:
                    score_risque += 10
                    result["details"].append(f"Pétrole +{uso_change:.1f}% (pression inflation)")
                elif uso_change < -3.0:
                    score_risque -= 5
                    result["details"].append(f"Pétrole {uso_change:.1f}% (détente économique)")
        except Exception as e:
            self.logger.debug(f"USO: {e}")

        # Signal global
        score_risque = max(0, min(100, score_risque))
        result["score_risque"] = score_risque
        if score_risque >= 70:
            result["signal"] = "RISQUE_ELEVE"
        elif score_risque <= 35:
            result["signal"] = "FAVORABLE"
        else:
            result["signal"] = "NEUTRE"

        self._cache = result
        self._cache_ts = time.time()
        return result


# =============================================================================
# [MODULE 4c-extra3] SuiviInsiders — Feature 10: SEC Form 4
# =============================================================================

class SuiviInsiders:
    """
    Surveille les achats d'initiés via SEC EDGAR (Form 4).
    Données publiques, aucune clé API requise.
    """
    CACHE_TTL = 3600 * 4  # Cache 4h
    HEADERS = {"User-Agent": "RouteBot/4.1 sacha.pellerin.45@icloud.com"}

    def __init__(self):
        self.logger = logging.getLogger("Insiders")
        self._cache = {}
        self._cache_ts = 0

    def get_achats_recents(self, tickers: list) -> dict:
        """Retourne {ticker: True/False} si des initiés ont acheté récemment."""
        if time.time() - self._cache_ts < self.CACHE_TTL and self._cache:
            return self._cache

        result = {t: False for t in tickers}
        try:
            end = datetime.now().strftime("%Y-%m-%d")
            start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            url = f"https://efts.sec.gov/LATEST/search-index?q=%22Form+4%22&dateRange=custom&startdt={start}&enddt={end}"
            resp = requests.get(url, headers=self.HEADERS, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                hits = data.get("hits", {}).get("hits", [])
                for hit in hits:
                    entity = hit.get("_source", {}).get("entity_name", "").upper()
                    for ticker in tickers:
                        if ticker.upper() in entity or entity in ticker.upper():
                            result[ticker] = True
        except Exception as e:
            self.logger.debug(f"Insiders: {e}")

        # Filtrer: retourner seulement les tickers avec achats confirmés
        actifs = {t: v for t, v in result.items() if v}
        if actifs:
            self.logger.info(f"👔 Achats initiés détectés: {list(actifs.keys())}")

        self._cache = result
        self._cache_ts = time.time()
        return result


# =============================================================================
# [MODULE 4d] DetecteurRegime — Régime de marché via SPY/QQQ
# =============================================================================

class DetecteurRegime:
    """
    Détecte le régime de marché (HAUSSIER / BAISSIER / LATÉRAL) via SPY + QQQ.
    Utilisé pour ajuster la stratégie globale du bot.
    """

    CACHE_TTL = 300  # 5 min

    def __init__(self):
        self.logger    = logging.getLogger("Regime")
        self._cache    = {}
        self._cache_ts = 0.0

    def detecter(self, df_spy: pd.DataFrame, df_qqq: pd.DataFrame = None) -> dict:
        now = time.time()
        if now - self._cache_ts < self.CACHE_TTL and self._cache:
            return self._cache
        vide = {"regime": "INDÉTERMINÉ", "spy_vs_ema50": 0.0,
                "qqq_vs_ema50": 0.0, "description": "Données insuffisantes"}
        try:
            if df_spy is None or df_spy.empty or len(df_spy) < 55:
                return vide
            closes_spy = df_spy["close"]
            ema50_spy  = ta.ema(closes_spy, length=50)
            if ema50_spy is None or ema50_spy.empty:
                return vide
            spy_prix  = float(closes_spy.iloc[-1])
            spy_ema50 = float(ema50_spy.iloc[-1])
            spy_delta = (spy_prix - spy_ema50) / spy_ema50 * 100
            spy_trend = (float(closes_spy.iloc[-1]) - float(closes_spy.iloc[-6])
                         if len(closes_spy) >= 6 else 0)

            qqq_delta = 0.0
            if df_qqq is not None and not df_qqq.empty and len(df_qqq) >= 55:
                closes_qqq = df_qqq["close"]
                ema50_qqq  = ta.ema(closes_qqq, length=50)
                if ema50_qqq is not None and not ema50_qqq.empty:
                    qqq_delta = (float(closes_qqq.iloc[-1]) - float(ema50_qqq.iloc[-1])) \
                                / float(ema50_qqq.iloc[-1]) * 100

            avg = (spy_delta + qqq_delta) / 2 if qqq_delta else spy_delta

            if avg > 2.0 and spy_trend > 0:
                regime, desc = "HAUSSIER", f"SPY {spy_delta:+.1f}% EMA50 — tendance haussière"
            elif avg < -2.0 or (spy_trend < 0 and avg < 0):
                regime, desc = "BAISSIER", f"SPY {spy_delta:+.1f}% EMA50 — marché en recul"
            else:
                regime, desc = "LATÉRAL",  f"SPY {spy_delta:+.1f}% EMA50 — consolidation"

            result = {
                "regime":       regime,
                "spy_vs_ema50": round(spy_delta, 2),
                "qqq_vs_ema50": round(qqq_delta, 2),
                "description":  desc,
            }
            self._cache    = result
            self._cache_ts = now
            self.logger.info(f"Régime marché : {regime} (SPY {spy_delta:+.1f}% EMA50)")
            return result
        except Exception as e:
            self.logger.warning(f"Régime: {e}")
            return vide


# =============================================================================
# [MODULE 4e] ShadowPortfolio — Portefeuille fantôme (0 € investi)
# =============================================================================

class ShadowPortfolio:
    """
    Simule des trades sans les exécuter — mesure les opportunités manquées.
    Capital virtuel séparé du capital réel, persistant entre les runs.
    """

    SHADOW_FILE        = Path("data/shadow_portfolio.json")
    CAPITAL_VIRTUEL    = 100_000.0
    ALLOCATION_VIRTUEL = 1_000.0

    def __init__(self):
        self.logger    = logging.getLogger("Shadow")
        self.positions = {}   # {ticker: {prix_entree, qty, montant, timestamp}}
        self.trades    = []
        self.cash      = self.CAPITAL_VIRTUEL
        self._charger()

    def _charger(self):
        try:
            if self.SHADOW_FILE.exists():
                with open(self.SHADOW_FILE, encoding="utf-8") as f:
                    d = json.load(f)
                self.positions = d.get("positions", {})
                self.trades    = d.get("trades",    [])
                self.cash      = d.get("cash",      self.CAPITAL_VIRTUEL)
        except Exception as e:
            self.logger.warning(f"Shadow load: {e}")

    def simuler_achat(self, ticker: str, prix: float, score: float, raison: str = ""):
        if ticker in self.positions or self.cash < self.ALLOCATION_VIRTUEL or prix <= 0:
            return
        qty = self.ALLOCATION_VIRTUEL / prix
        self.positions[ticker] = {
            "ticker":      ticker,
            "prix_entree": prix,
            "prix_actuel": prix,
            "qty":         qty,
            "montant":     self.ALLOCATION_VIRTUEL,
            "score":       score,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "raison":      raison,
        }
        self.cash -= self.ALLOCATION_VIRTUEL
        self.logger.info(f"[SHADOW] BUY {ticker} @ ${prix:.2f}")

    def simuler_vente(self, ticker: str, prix: float, raison: str = ""):
        if ticker not in self.positions:
            return
        pos     = self.positions.pop(ticker)
        pnl     = (prix - pos["prix_entree"]) * pos["qty"]
        pnl_pct = (prix - pos["prix_entree"]) / pos["prix_entree"] * 100
        self.cash += pos["montant"] + pnl
        self.trades.append({
            "type":        "SHADOW_VENTE",
            "ticker":      ticker,
            "prix_entree": pos["prix_entree"],
            "prix_sortie": prix,
            "pnl":         round(pnl, 2),
            "pnl_pct":     round(pnl_pct, 2),
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "raison":      raison,
        })
        self.trades = self.trades[-200:]

    def maj_prix(self, ticker: str, prix: float):
        if ticker in self.positions:
            self.positions[ticker]["prix_actuel"] = prix

    def sauvegarder(self):
        try:
            self.SHADOW_FILE.parent.mkdir(parents=True, exist_ok=True)
            pnl_total = 0.0
            pos_export = {}
            for t, pos in self.positions.items():
                p   = dict(pos)
                pa  = p.get("prix_actuel", p["prix_entree"])
                pnl = (pa - p["prix_entree"]) * p["qty"]
                p["pnl_virtuel"]     = round(pnl, 2)
                p["pnl_virtuel_pct"] = round((pa - p["prix_entree"]) / p["prix_entree"] * 100, 2)
                pnl_total += pnl
                pos_export[t] = p
            with open(self.SHADOW_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "timestamp":         datetime.now(timezone.utc).isoformat(),
                    "positions":         pos_export,
                    "trades":            self.trades[-50:],
                    "cash":              round(self.cash, 2),
                    "pnl_total_virtuel": round(pnl_total, 2),
                    "nb_positions":      len(self.positions),
                    "capital_virtuel":   self.CAPITAL_VIRTUEL,
                }, f, indent=2)
        except Exception as e:
            self.logger.warning(f"Shadow save: {e}")

    def export_dashboard(self) -> dict:
        pnl_total = 0.0
        pos_list  = []
        for t, pos in self.positions.items():
            p   = dict(pos)
            pa  = p.get("prix_actuel", p["prix_entree"])
            pnl = (pa - p["prix_entree"]) * p["qty"]
            p["pnl_virtuel"]     = round(pnl, 2)
            p["pnl_virtuel_pct"] = round((pa - p["prix_entree"]) / p["prix_entree"] * 100, 2)
            pnl_total += pnl
            pos_list.append(p)
        wins     = sum(1 for t in self.trades if t.get("pnl", 0) > 0)
        win_rate = round(wins / len(self.trades) * 100, 1) if self.trades else 0.0
        return {
            "positions":         sorted(pos_list, key=lambda x: x.get("pnl_virtuel", 0), reverse=True),
            "trades_recents":    self.trades[-10:],
            "pnl_total_virtuel": round(pnl_total, 2),
            "nb_positions":      len(self.positions),
            "cash_virtuel":      round(self.cash, 2),
            "win_rate_virtuel":  win_rate,
            "capital_virtuel":   self.CAPITAL_VIRTUEL,
            "nb_trades":         len(self.trades),
        }


# =============================================================================
# [MODULE 4b] NotificateurDiscord
# =============================================================================

class NotificateurDiscord:
    """Envoie des notifications Discord via webhook sur chaque trade."""

    def __init__(self):
        self.logger  = logging.getLogger("Discord")
        self.webhook = DISCORD_WEBHOOK

    def _envoyer(self, contenu: str, embeds: list = None):
        if not self.webhook:
            return
        try:
            payload = {"content": contenu}
            if embeds:
                payload["embeds"] = embeds
            requests.post(self.webhook, json=payload, timeout=5)
        except Exception as e:
            self.logger.warning(f"Discord erreur : {e}")

    def notifier_achat(self, ticker: str, montant: float, prix: float,
                       conviction: str, score: float, sentiment: float):
        emoji = "🟢"
        embed = {
            "title": f"{emoji} ACHAT — {ticker}",
            "color": 0x00ff88,
            "fields": [
                {"name": "Montant",    "value": f"${montant:.0f}",      "inline": True},
                {"name": "Prix",       "value": f"${prix:.4f}",         "inline": True},
                {"name": "Conviction", "value": conviction,             "inline": True},
                {"name": "Score",      "value": f"{score:.0f}/100",     "inline": True},
                {"name": "Sentiment",  "value": f"{sentiment:.2f}",     "inline": True},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._envoyer("", embeds=[embed])

    def notifier_vente(self, ticker: str, pnl: float, pnl_pct: float, raison: str):
        emoji = "🔴" if pnl < 0 else "✅"
        sign  = "+" if pnl >= 0 else ""
        embed = {
            "title": f"{emoji} VENTE — {ticker}",
            "color": 0xff4444 if pnl < 0 else 0x00ff88,
            "fields": [
                {"name": "P&L",   "value": f"{sign}${pnl:.2f} ({sign}{pnl_pct:.2f}%)", "inline": True},
                {"name": "Raison","value": raison[:100],                                 "inline": False},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._envoyer("", embeds=[embed])

    def notifier_resume(self, equity: float, pnl: float, pnl_pct: float,
                        nb_positions: int, achats: int, ventes: int):
        if not self.webhook:
            return
        sign = "+" if pnl >= 0 else ""
        self._envoyer(
            f"📊 **Résumé cycle** | Equity: ${equity:,.2f} | "
            f"P&L: {sign}${pnl:.2f} ({sign}{pnl_pct:.2f}%) | "
            f"Pos: {nb_positions} | Achats: {achats} | Ventes: {ventes}"
        )

    def notifier_erreur_ia(self, ticker: str, pnl: float, details_trade: dict,
                           regime: str, groq_client=None):
        """Demande à Groq d'analyser un trade perdant et envoie l'explication sur Discord."""
        if pnl >= 0 or abs(pnl) < 30:  # Seulement pour pertes > 30$
            return
        try:
            prompt = f"""Un trade a perdu de l'argent. Analyse BRIÈVEMENT (3-4 phrases max) pourquoi et ce qu'il fallait éviter.

Ticker: {ticker}
Perte: ${abs(pnl):.2f}
RSI à l'achat: {details_trade.get('rsi', '?')}
Score technique: {details_trade.get('score', '?')}/100
HMA crossover: {details_trade.get('hma_crossover', '?')}
Régime marché: {regime}
Raison de vente: {details_trade.get('raison_vente', '?')}

Réponds en français, de façon directe et actionnable. Format: 1 ligne d'erreur principale + 1 ligne de conseil."""

            headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
            payload = {
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 150,
                "temperature": 0.3
            }
            resp = requests.post("https://api.groq.com/openai/v1/chat/completions",
                               headers=headers, json=payload, timeout=10)
            if resp.status_code == 200:
                analyse = resp.json()["choices"][0]["message"]["content"].strip()
                self._envoyer(
                    f"🧠 **ANALYSE ERREUR** `{ticker}` | Perte: -${abs(pnl):.2f}\n"
                    f"━━━━━━━━━━━━━━━━━\n{analyse}"
                )
        except Exception as e:
            self.logger.debug(f"IA erreur: {e}")


# =============================================================================
# [MODULE 4c] GestionnaireMomentum — Score vs SPY
# =============================================================================

class GestionnaireMomentum:
    """Compare la performance de chaque actif vs SPY sur 5 jours."""

    def __init__(self):
        self.logger   = logging.getLogger("Momentum")
        self._cache   = {}
        self._cache_ts = {}
        self.CACHE_TTL = 300  # 5 minutes

    def score_vs_spy(self, ticker: str, df_actif: pd.DataFrame,
                     df_spy: pd.DataFrame) -> float:
        """
        Retourne un score momentum entre -1.0 et +1.0.
        +1.0 = actif surperforme fortement SPY
        -1.0 = actif sous-performe fortement SPY
        """
        now = time.time()
        if ticker in self._cache and (now - self._cache_ts.get(ticker, 0)) < self.CACHE_TTL:
            return self._cache[ticker]

        try:
            if len(df_actif) < 5 or len(df_spy) < 5:
                return 0.0
            perf_actif = (df_actif["close"].iloc[-1] - df_actif["close"].iloc[-5]) / df_actif["close"].iloc[-5]
            perf_spy   = (df_spy["close"].iloc[-1]   - df_spy["close"].iloc[-5])   / df_spy["close"].iloc[-5]
            score = max(-1.0, min(1.0, (perf_actif - perf_spy) * 10))
            self._cache[ticker]    = round(score, 3)
            self._cache_ts[ticker] = now
            return self._cache[ticker]
        except Exception:
            return 0.0




# =============================================================================
# [MODULE 4d] GestionnairePersistance — SQLite stdlib (aucune dépendance)
# =============================================================================

class GestionnairePersistance:
    """
    Persistance SQLite pour l'historique complet des trades.
    Utilise sqlite3 (stdlib Python) — aucune dépendance supplémentaire.
    Calcule win rate, avg P&L%, Sharpe approximatif.
    """

    DB_PATH = "data/trades.db"

    def __init__(self):
        self.logger = logging.getLogger("Persistance")
        Path(self.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    type           TEXT,
                    ticker         TEXT,
                    montant        REAL,
                    prix           REAL,
                    pnl            REAL,
                    pnl_pct        REAL,
                    conviction     TEXT,
                    score_fusionne REAL,
                    croisement_hma INTEGER DEFAULT 0,
                    sentiment      REAL,
                    rsi            REAL,
                    raison         TEXT,
                    timestamp      TEXT
                )
            """)
            conn.commit()

    def sauvegarder(self, trade: dict):
        try:
            with sqlite3.connect(self.DB_PATH) as conn:
                conn.execute("""
                    INSERT INTO trades
                    (type,ticker,montant,prix,pnl,pnl_pct,conviction,
                     score_fusionne,croisement_hma,sentiment,rsi,raison,timestamp)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    trade.get("type"),       trade.get("ticker"),
                    trade.get("montant"),    trade.get("prix"),
                    trade.get("pnl"),        trade.get("pnl_pct"),
                    trade.get("conviction"), trade.get("score_fusionne"),
                    1 if trade.get("croisement_hma") else 0,
                    trade.get("sentiment"),  trade.get("rsi"),
                    trade.get("raison"),     trade.get("timestamp"),
                ))
                conn.commit()
        except Exception as e:
            self.logger.warning(f"SQLite save: {e}")

    def charger_recents(self, n: int = 500) -> list:
        try:
            with sqlite3.connect(self.DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (n,)
                ).fetchall()
                result = []
                for r in reversed(rows):
                    d = dict(r)
                    d["croisement_hma"] = bool(d.get("croisement_hma"))
                    result.append(d)
                return result
        except Exception as e:
            self.logger.warning(f"SQLite load: {e}")
            return []

    def importer_json(self, trades: list):
        """Migration one-shot JSON → SQLite au premier run."""
        try:
            with sqlite3.connect(self.DB_PATH) as conn:
                if conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] > 0:
                    return
            for t in trades:
                self.sauvegarder(t)
            self.logger.info(f"Migration JSON→SQLite: {len(trades)} trades importés")
        except Exception as e:
            self.logger.warning(f"Migration: {e}")

    def calculer_metriques(self) -> dict:
        """Win rate, avg P&L%, best/worst trade, Sharpe approx."""
        try:
            with sqlite3.connect(self.DB_PATH) as conn:
                rows = conn.execute(
                    "SELECT pnl, pnl_pct FROM trades "
                    "WHERE type IN ('VENTE','VENTE_AUTO') AND pnl IS NOT NULL"
                ).fetchall()
            if not rows:
                return {"win_rate": 0.0, "avg_pnl_pct": 0.0, "nb_trades": 0,
                        "total_pnl": 0.0, "best_trade": 0.0, "worst_trade": 0.0,
                        "sharpe_approx": 0.0}
            pnls     = [r[0] for r in rows]
            pnl_pcts = [r[1] for r in rows if r[1] is not None]
            wins     = sum(1 for p in pnls if p > 0)
            sharpe   = 0.0
            if len(pnl_pcts) > 1:
                mu  = statistics.mean(pnl_pcts)
                std = statistics.stdev(pnl_pcts)
                sharpe = round(mu / std, 3) if std else 0.0
            return {
                "win_rate":      round(wins / len(pnls) * 100, 1),
                "avg_pnl_pct":   round(statistics.mean(pnl_pcts), 2) if pnl_pcts else 0.0,
                "nb_trades":     len(pnls),
                "total_pnl":     round(sum(pnls), 2),
                "best_trade":    round(max(pnl_pcts), 2) if pnl_pcts else 0.0,
                "worst_trade":   round(min(pnl_pcts), 2) if pnl_pcts else 0.0,
                "sharpe_approx": sharpe,
            }
        except Exception as e:
            self.logger.warning(f"Métriques: {e}")
            return {}


class GestionnaireRisque:
    """
    Stop-loss dynamique ATR + fixe 3%, take-profit ATR (ratio 1.5×),
    trailing stop −2.5%, drawdown max 5%, sizing adaptatif, contrôle sectoriel.
    """

    def __init__(self, capital_initial: float = CAPITAL_INITIAL):
        self.capital_initial = capital_initial
        self.trailing_highs: dict = {}
        self.logger = logging.getLogger("Risque")
        # Feature D : Break-even stop
        self.breakeven_actifs: set  = set()   # Tickers avec breakeven activé
        self.breakeven_levels: dict = {}      # {ticker: entry_price}
        self.breakeven_atr: dict    = {}      # {ticker: atr_pct} mis à jour depuis _analyser_actif
        self._charger_trailing_highs()

    def _charger_trailing_highs(self):
        """Charge les trailing highs depuis data/trailing_highs.json (persistant entre runs)."""
        try:
            if TRAILING_HIGHS_FILE.exists():
                with open(TRAILING_HIGHS_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                self.trailing_highs = {k: float(v) for k, v in data.items()}
                if self.trailing_highs:
                    self.logger.info(f"📂 Trailing highs chargés : {list(self.trailing_highs.keys())}")
        except Exception as e:
            self.logger.warning(f"Chargement trailing_highs : {e}")
            self.trailing_highs = {}

    def _sauvegarder_trailing_highs(self):
        """Sauvegarde les trailing highs dans data/trailing_highs.json."""
        try:
            TRAILING_HIGHS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(TRAILING_HIGHS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.trailing_highs, f, indent=2)
        except Exception as e:
            self.logger.warning(f"Sauvegarde trailing_highs : {e}")

    def verifier_breakeven(self, ticker: str, position: dict, prix_actuel: float) -> bool:
        """
        Déplace le stop au prix d'entrée si gain >= 1× ATR.
        Retourne True si breakeven vient d'être activé.
        """
        try:
            entry = float(position.get("avg_entry_price", 0) or position.get("entry_price", 0))
            if entry <= 0:
                return False
            # Utiliser ATR de 1% par défaut si non disponible
            atr_pct = self.breakeven_atr.get(ticker, 0.01)
            atr_abs = entry * atr_pct
            gain_r = (prix_actuel - entry) / (atr_abs + 1e-10)
            if gain_r >= 1.0 and ticker not in self.breakeven_actifs:
                self.breakeven_actifs.add(ticker)
                self.breakeven_levels[ticker] = entry
                self.logger.info(f"[BREAKEVEN] {ticker} — stop déplacé au prix d'entrée ${entry:.2f} (gain +{gain_r:.1f}R)")
                return True
            return False
        except Exception as e:
            self.logger.debug(f"Breakeven {ticker}: {e}")
            return False

    def verifier_stop_loss(self, pos: dict) -> bool:
        """
        Déclenche la vente si la perte atteint le seuil fixe de 25 $.
        Fallback % si la valeur de marché est trop faible pour évaluer en $.
        """
        ticker = pos.get("ticker", "")
        pl     = pos.get("unrealized_pl", 0)    # P&L absolu en $
        plpc   = pos.get("unrealized_plpc", 0)  # P&L en %

        # ── Seuil monétaire fixe (prioritaire) ────────────────────────────
        if pl <= -STOP_LOSS_FIXE:
            self.logger.warning(
                f"🛑 STOP-LOSS {ticker}: -{abs(pl):.2f}$ "
                f"(seuil fixe -{STOP_LOSS_FIXE:.0f}$) | {plpc:.2f}%"
            )
            return True

        # ── Fallback % pour meme stocks ou petites positions ──────────────
        ticker_norm = ticker.replace("/", "")
        is_meme = ticker in MEME_STOCKS or ticker_norm in MEME_STOCKS
        sl_pct  = STOP_LOSS_MEME if is_meme else STOP_LOSS_PCT
        if plpc <= -(sl_pct * 100):
            self.logger.warning(
                f"🛑 STOP-LOSS {ticker}: {plpc:.2f}% "
                f"(seuil {'MEME' if is_meme else 'STD'} -{sl_pct*100:.0f}%)"
            )
            return True

        # ── Feature D : Vérification breakeven ───────────────────────────
        if ticker in self.breakeven_levels:
            entry       = float(pos.get("avg_entry_price", 0))
            be_level    = self.breakeven_levels[ticker]
            prix_actuel = float(pos.get("current_price", entry))
            if prix_actuel < be_level * 1.001 and prix_actuel > entry * 0.995:
                self.logger.info(f"[BREAKEVEN STOP] {ticker} — prix revenu au niveau d'entrée ${be_level:.2f}")
                return True
        return False

    def verifier_take_profit(self, pos: dict) -> bool:
        """
        Prise de profit sur seuil monétaire fixe :
          • Actions  : gain ≥ 50 $
          • Crypto   : gain ≥ 100 $
        Fallback % si montant insuffisant.
        """
        ticker = pos.get("ticker", "")
        pl     = pos.get("unrealized_pl", 0)    # P&L absolu en $
        plpc   = pos.get("unrealized_plpc", 0)  # P&L en %

        # Détection crypto (slash, liste connue, ou suffixe USD court)
        est_crypto = (
            "/" in ticker
            or ticker in {t.replace("/", "") for t in ACTIFS_CRYPTO}
            or (ticker.endswith("USD") and len(ticker) <= 8)
        )
        seuil_tp = TAKE_PROFIT_CRYPTO if est_crypto else TAKE_PROFIT_ACTIONS

        # ── Seuil monétaire fixe (prioritaire) ────────────────────────────
        if pl >= seuil_tp:
            self.logger.info(
                f"✅ TAKE-PROFIT {ticker}: +{pl:.2f}$ "
                f"(seuil {'CRYPTO' if est_crypto else 'ACTION'} +{seuil_tp:.0f}$) | {plpc:.2f}%"
            )
            return True

        # ── Fallback % si position trop petite pour atteindre le seuil $ ─
        if plpc >= (TAKE_PROFIT_PCT * 100):
            self.logger.info(f"✅ TAKE-PROFIT % {ticker}: {plpc:.2f}%")
            return True

        return False

    def verifier_trailing_stop(self, pos: dict) -> bool:
        ticker = pos["ticker"]
        prix   = pos.get("current_price", 0)
        if ticker not in self.trailing_highs:
            self.trailing_highs[ticker] = prix
            self._sauvegarder_trailing_highs()
        if prix > self.trailing_highs[ticker]:
            self.trailing_highs[ticker] = prix
            self._sauvegarder_trailing_highs()
        plus_haut = self.trailing_highs[ticker]
        recul     = (plus_haut - prix) / plus_haut if plus_haut > 0 else 0
        plpc      = pos.get("unrealized_plpc", 0)
        if plpc > 1.5 and recul >= TRAILING_STOP_PCT:
            self.logger.info(f"📉 TRAILING {ticker}: recul {recul*100:.2f}% depuis ${plus_haut:.2f}")
            return True
        return False

    def verifier_drawdown(self, equity: float) -> bool:
        dd = (self.capital_initial - equity) / self.capital_initial
        if dd >= MAX_DRAWDOWN_PCT:
            self.logger.error(f"🚨 DRAWDOWN {dd*100:.2f}% — pause forcée")
            return True
        return False

    def allocation(self, conviction: str, atr_pct: Optional[float],
                   kelly_base: float = None) -> float:
        """Taille de position. Utilise Kelly si disponible, sinon allocation fixe."""
        if kelly_base is not None:
            base = kelly_base
            if conviction == "FORTE":
                base *= 1.2
            elif conviction == "FAIBLE":
                base *= 0.8
        else:
            base = {"EXCELLENCE": ALLOCATION_EXCELLENCE,
                    "FORTE": ALLOCATION_FORTE,
                    "MOYENNE": ALLOCATION_BASE,
                    "FAIBLE": ALLOCATION_FAIBLE}.get(conviction, ALLOCATION_BASE)
        if atr_pct and atr_pct > 2.0:
            base *= max(0.7, 1 - (atr_pct - 2.0) * 0.1)
        return round(base, 2)

    def calculer_portfolio_heat(self, positions: dict, capital: float) -> float:
        """
        Portfolio Heat = somme des risques ouverts / capital.
        Approximation : risk par position = market_value × STOP_LOSS_PCT.
        Bloque les nouveaux achats si heat ≥ MAX_HEAT_PCT (8%).
        """
        if not positions or capital <= 0:
            return 0.0
        total_risk = sum(
            pos.get("market_value", 0) * STOP_LOSS_PCT
            for pos in positions.values()
        )
        return round(total_risk / capital * 100, 2)

    def calculer_kelly_base(self, conn) -> float:
        """
        Critère de Kelly (1/4 Kelly pour sécurité) depuis l'historique SQLite.
        Retourne la taille de position optimale en USD.
        Minimum ALLOCATION_FAIBLE, maximum ALLOCATION_FORTE × 1.5.
        """
        try:
            rows = conn.execute(
                "SELECT pnl_pct FROM trades WHERE type IN ('VENTE','VENTE_AUTO') "
                "AND pnl_pct IS NOT NULL ORDER BY id DESC LIMIT 50"
            ).fetchall()
            if len(rows) < 15:
                return ALLOCATION_BASE
            pnl_pcts = [r[0] for r in rows]
            wins     = [p for p in pnl_pcts if p > 0]
            losses   = [abs(p) for p in pnl_pcts if p < 0]
            if not wins or not losses:
                return ALLOCATION_BASE
            win_rate = len(wins) / len(pnl_pcts)
            avg_win  = sum(wins)  / len(wins)  / 100
            avg_loss = sum(losses) / len(losses) / 100
            b        = avg_win / avg_loss if avg_loss > 0 else 1.0
            kelly    = (b * win_rate - (1 - win_rate)) / b
            kelly_f  = max(0.005, min(0.08, kelly * 0.25))   # 1/4 Kelly, cap 0.5–8%
            amount   = self.capital_initial * kelly_f
            result   = round(max(ALLOCATION_FAIBLE, min(ALLOCATION_FORTE * 1.5, amount)), 2)
            return result
        except Exception:
            return ALLOCATION_BASE

    def positions_disponibles(self, nb: int) -> bool:
        return nb < MAX_POSITIONS

    def capital_suffisant(self, compte: dict, montant: float) -> bool:
        return compte.get("buying_power", 0) >= montant

    def secteur_ok(self, ticker: str, positions: dict) -> bool:
        for sect, tickers in SECTEURS.items():
            if ticker in tickers:
                count = sum(1 for t in positions if t in tickers)
                return count < MAX_PAR_SECTEUR
        return True


# =============================================================================
# [MODULE 6] ScoreDecisionIA
# =============================================================================

class ScoreDecisionIA:
    """
    Fusionne score technique (60%) + score sentiment Groq (40%).
    Bonus si croisement HMA détecté (signal fort du code fourni).
    """

    POIDS_TECH = 0.60
    POIDS_SENT = 0.40

    def decider(self, analyse_tech: dict, analyse_sent: dict,
                dans_position: bool, fear_greed: int = 50,
                fondamentaux: dict = None) -> dict:

        score_tech   = analyse_tech.get("score",          50)
        signal_tech  = analyse_tech.get("signal",         "HOLD")
        croisement   = analyse_tech.get("croisement_hma", False)
        score_sent   = analyse_sent.get("score",          0.55)
        biais_sent   = analyse_sent.get("biais",          "neutre")

        # Bonus croisement HMA (signal fort inspiré du code fourni)
        if croisement and signal_tech == "BUY":
            score_tech = min(100, score_tech * 1.1)

        score_fusionne = round(
            score_tech * self.POIDS_TECH + score_sent * 100 * self.POIDS_SENT, 1
        )
        score_fusionne = max(0, min(100, score_fusionne))

        # ── Fear & Greed adjustment ───────────────────────────────────────────
        fg_bonus = ""
        if fear_greed < 25:   # Peur extrême → acheter la panique = boost léger
            score_fusionne = min(100, score_fusionne * 1.06)
            fg_bonus = " | F&G:PeurExtrême+6%"
        elif fear_greed > 75:  # Euphorie → prudence = pénalité légère
            score_fusionne = max(0, score_fusionne * 0.94)
            fg_bonus = " | F&G:Euphorie-6%"

        # ── Fundamentals Yahoo adjustment ─────────────────────────────────────
        fund_bonus = ""
        if fondamentaux:
            upside = fondamentaux.get("upside_pct") or 0
            if upside > 20 and not dans_position:
                score_fusionne = min(100, score_fusionne * 1.04)
                fund_bonus += f" | Analystes+{upside:.0f}%"
            elif upside < -10:
                score_fusionne = max(0, score_fusionne * 0.96)
            if fondamentaux.get("squeeze_potential") and not dans_position:
                score_fusionne = min(100, score_fusionne * 1.03)
                fund_bonus += " | ShortSqueeze"
            # Pre-market signal
            pm_chg = fondamentaux.get("pre_market_change") or 0
            if isinstance(pm_chg, float):
                if pm_chg > 0.02 and signal_tech == "BUY":
                    score_fusionne = min(100, score_fusionne * 1.03)
                    fund_bonus += f" | PreMkt+{pm_chg*100:.1f}%"
                elif pm_chg < -0.02 and not dans_position:
                    score_fusionne = max(0, score_fusionne * 0.97)
            # Analyst rating
            ar = fondamentaux.get("analyst_rating", "")
            if ar in ("strong_buy", "buy") and not dans_position:
                score_fusionne = min(100, score_fusionne * 1.02)
            elif ar in ("strong_sell", "sell") and not dans_position:
                score_fusionne = max(0, score_fusionne * 0.98)

        score_fusionne = round(max(0, min(100, score_fusionne)), 1)

        action     = "AUCUNE"
        conviction = "FAIBLE"
        raison     = ""

        if not dans_position:
            if (signal_tech == "BUY" and
                score_sent >= SEUIL_SENTIMENT_BUY and
                score_fusionne >= SEUIL_TECH_BUY):

                action = "ACHAT"
                if score_fusionne >= 90:
                    conviction = "EXCELLENCE"
                elif score_fusionne >= SEUIL_CONVICTION_FORTE:
                    conviction = "FORTE"
                elif score_fusionne >= SEUIL_CONVICTION_FAIBLE:
                    conviction = "MOYENNE"
                raison = (
                    f"Score fusionné {score_fusionne:.0f}/100 "
                    f"(tech={score_tech:.0f} sent={score_sent:.2f} "
                    f"biais={biais_sent}"
                    f"{' | HMA crossover ✓' if croisement else ''}"
                    f"{fg_bonus}{fund_bonus})"
                )

            elif signal_tech == "BUY" and score_sent < SEUIL_SENTIMENT_BUY:
                raison = (
                    f"BUY bloqué par Groq : sent={score_sent:.2f} "
                    f"< {SEUIL_SENTIMENT_BUY} | {analyse_sent.get('resume','')[:50]}"
                )
            else:
                raison = analyse_tech.get("raison", "HOLD — signal insuffisant")

        else:
            if signal_tech == "SELL":
                action     = "VENTE"
                conviction = analyse_tech.get("conviction", "MOYENNE")
                raison     = analyse_tech.get("raison", "Signal SELL technique")
            elif score_sent < SEUIL_SENTIMENT_SELL:
                action     = "VENTE"
                conviction = "FORTE"
                raison     = f"Panique Groq : sent={score_sent:.2f} | {analyse_sent.get('resume','')[:50]}"
            else:
                raison = "Position maintenue"

        return {
            "action":         action,
            "conviction":     conviction,
            "score_fusionne": score_fusionne,
            "croisement_hma": croisement,
            "raison":         raison,
        }


# =============================================================================
# [MODULE 7] BotRoute
# =============================================================================

class BotRoute:

    def __init__(self):
        self.alpaca      = AlpacaClientV2()
        self.indicateurs = MoteurIndicateurs()
        self.sentiment   = AnalyseurSentimentGroq()
        self.gemini      = AnalyseurSentimentGemini()
        self.risque      = GestionnaireRisque()
        self.decision    = ScoreDecisionIA()
        self.discord     = NotificateurDiscord()
        self.momentum    = GestionnaireMomentum()
        self.shadow      = ShadowPortfolio()
        self.reddit      = VeilleReddit()
        self.regime_det  = DetecteurRegime()
        self.fear_greed      = FearGreedIndex()
        self.yahoo           = YahooFinanceClient()
        self.strategie_sacha = StrategieSachaPro()
        self.portefeuille_lt = PortefeuilleLongTerme() if LT_ACTIF else None
        self._dernieres_decisions: dict = {}   # scores du cycle précédent → sélection nuit
        self._dernier_resume_quotidien = None  # date ISO du dernier résumé quotidien envoyé
        self._dernier_regime = None            # régime marché du cycle précédent
        self._fear_greed_value: int  = 50
        self._mins_depuis_open: float = 999.0
        self._mins_avant_close: float = 999.0
        self.df_qqq: pd.DataFrame = pd.DataFrame()
        self.persistance = GestionnairePersistance()
        self.logger      = logging.getLogger("BotRoute")
        self.cycle       = 0
        self.historique_trades: list = self._charger_historique()
        self.historique_valeur: list = []
        self.pause_drawdown    = False
        self.df_spy: pd.DataFrame = pd.DataFrame()
        self._cooldown: dict   = {}   # ticker → timestamp fin de cooldown (persisté)
        self._achats_ce_cycle  = 0    # réinitialisé à chaque run_cycle (actions)
        self._achats_crypto_cycle = 0  # compteur séparé pour les cryptos
        self._portfolio_heat: float = 0.0
        self._circuit_breaker_actif: bool = False
        # Feature 1 : Pyramiding
        self._pyramiding_done: dict = {}
        # Feature 2 : Paires de trading
        self._derniere_alerte_paire: dict = {}
        # Feature 4 : Blacklist
        self.blacklist = GestionnaireBlacklist()
        # Feature 6 : Hedge
        self._dernier_hedge_check: float = 0.0
        # Feature 7 : Bunker
        self._weekend_protection_done = None
        # Feature 9 : Météo économique
        self.meteo = MeteoEconomique()
        # Feature 10 : Suivi initiés
        self.insiders = SuiviInsiders()
        self._insiders_data: dict = {}
        # Feature 11 : Journal hebdomadaire
        self._dernier_journal = None
        # Feature G : Kelly historique (base de calcul d'allocation)
        self._kelly_base = ALLOCATION_BASE
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # Migration one-shot JSON → SQLite
        self.persistance.importer_json(self.historique_trades)
        # Charge les poids optimisés du run nocturne précédent
        self._charger_poids_appris()
        # Charge la watchlist dynamique (rotation S&P500) — persiste entre runs
        self._charger_watchlist()
        # Charge les cooldowns depuis le fichier JSON (persistant entre runs GitHub Actions)
        self._charger_cooldowns()
        # Charge l'état pyramiding (Feature 1)
        self._charger_pyramiding()

    # ── Helpers protection trading ────────────────────────────────────────

    def _mode_temporel(self) -> str:
        """Retourne le mode temporel selon l'heure ET: OUVERTURE/NORMAL/CLOTURE/FERME"""
        now_et = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-4)))  # EDT
        heure  = now_et.hour
        minute = now_et.minute
        if now_et.weekday() >= 5:
            return "FERME"
        if heure == 9 and minute < 30:
            return "FERME"
        if heure == 9 and minute >= 30:
            return "OUVERTURE"  # 9:30-10:00 = volatilité élevée
        if heure >= 15 and heure < 16:
            return "CLOTURE"   # 15:00-16:00 = préférer les ventes
        if 10 <= heure < 15:
            return "NORMAL"
        return "FERME"

    @staticmethod
    def _generer_raison_simple(dec: dict, ticker: str) -> str:
        """Traduit la décision technique en langage accessible."""
        action = dec.get("action", "NEUTRE")
        score  = dec.get("score_fusionne", 50)
        raison = dec.get("raison", "")

        if action == "ACHAT":
            if score >= 85:
                return f"💚 {ticker} montre des signaux très forts — tendance claire à la hausse avec confirmation volume"
            elif score >= 70:
                return f"📈 {ticker} en bonne forme — indicateurs positifs, momentum favorable"
            else:
                return f"🟡 {ticker} légèrement positif — signal modéré, position prudente"
        elif action == "VENTE":
            if "stop" in raison.lower() or "loss" in raison.lower():
                return f"🔴 {ticker} a atteint la limite de perte — vente automatique pour protéger le capital"
            elif "profit" in raison.lower() or "tp" in raison.lower():
                return f"✅ {ticker} a atteint l'objectif de gain — prise de bénéfices"
            else:
                return f"📉 {ticker} montre des signaux de faiblesse — réduction de la position"
        else:
            return f"⏸️ {ticker} — pas d'opportunité claire en ce moment"

    def _en_cooldown(self, ticker: str) -> bool:
        return time.time() < self._cooldown.get(ticker, 0)

    def _definir_cooldown(self, ticker: str, crypto_nuit: bool = False):
        """Cooldown réduit pour crypto hors heures de marché (plus réactif)."""
        duree = COOLDOWN_CRYPTO_NUIT if (crypto_nuit and "/" in ticker) else COOLDOWN_APRES_ACHAT
        self._cooldown[ticker] = time.time() + duree
        self.logger.debug(f"Cooldown {ticker} : {duree // 60} min")
        self._sauvegarder_cooldowns()

    def _charger_cooldowns(self):
        """Charge les cooldowns depuis cooldowns.json — persistant entre runs GitHub Actions."""
        try:
            if COOLDOWNS_FILE.exists():
                with open(COOLDOWNS_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                now = time.time()
                # Ne garder que les cooldowns non expirés
                self._cooldown = {k: v for k, v in data.items() if v > now}
                expires = {k: int((v - now) / 60) for k, v in self._cooldown.items()}
                if expires:
                    self.logger.info(f"⏳ Cooldowns chargés : {expires}")
                else:
                    self.logger.debug("Cooldowns.json vide ou tous expirés")
        except Exception as e:
            self.logger.warning(f"Erreur chargement cooldowns : {e}")
            self._cooldown = {}

    def _sauvegarder_cooldowns(self):
        """Sauvegarde les cooldowns actifs dans cooldowns.json."""
        try:
            COOLDOWNS_FILE.parent.mkdir(parents=True, exist_ok=True)
            now = time.time()
            actifs = {k: v for k, v in self._cooldown.items() if v > now}
            with open(COOLDOWNS_FILE, "w", encoding="utf-8") as f:
                json.dump(actifs, f, indent=2)
        except Exception as e:
            self.logger.warning(f"Erreur sauvegarde cooldowns : {e}")

    def _actifs_correles(self, ticker: str, positions: dict) -> bool:
        """Retourne True si un actif fortement corrélé est déjà en position."""
        ticker_norm = ticker.replace("/", "")
        for groupe in GROUPES_CORRELES:
            if ticker in groupe or ticker_norm in groupe:
                return any(
                    t in positions or t.replace("/", "") in positions
                    for t in groupe - {ticker, ticker_norm}
                )
        return False

    def _charger_historique(self) -> list:
        """Charge l'historique persistant depuis le fichier JSON."""
        try:
            if HISTORIQUE_FILE.exists():
                with open(HISTORIQUE_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                    self.logger.info(f"📂 Historique chargé : {len(data)} trades")
                    return data
        except Exception as e:
            self.logger.warning(f"Historique illisible : {e}")
        return []

    def _sauvegarder_historique(self):
        """Sauvegarde l'historique complet dans un fichier persistant."""
        try:
            HISTORIQUE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(HISTORIQUE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.historique_trades, f, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            self.logger.warning(f"Erreur sauvegarde historique : {e}")

    # ── Routines d'entraînement (marché fermé) ───────────────────────────

    def _peut_executer_routine(self, nom_fichier: str, min_intervalle_h: float = 1.0) -> bool:
        """Garde-fou : retourne True si la routine n'a pas tourné depuis min_intervalle_h heures."""
        try:
            path = DATA_DIR / nom_fichier
            if not path.exists():
                return True
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            ts_str = data.get("timestamp")
            if not ts_str:
                return True
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
            return age_h >= min_intervalle_h
        except Exception:
            return True

    def _charger_watchlist(self):
        """
        Recharge la watchlist dynamique depuis watchlist_dynamique.json.
        CRITIQUE : sans ça, les changements de rotation sont perdus entre les runs GitHub Actions.
        """
        global ACTIFS_ACTIONS, ACTIFS_TOUS, SP500_CANDIDATS
        try:
            wl_path = DATA_DIR / "watchlist_dynamique.json"
            if not wl_path.exists():
                return
            with open(wl_path, encoding="utf-8") as f:
                data = json.load(f)
            actifs_sauvegardes = data.get("actifs_actifs", [])
            if not actifs_sauvegardes or len(actifs_sauvegardes) < 20:
                return
            # Vérifier que tous les actifs sauvegardés sont des strings valides
            if not all(isinstance(a, str) for a in actifs_sauvegardes):
                return
            anciens = set(ACTIFS_ACTIONS)
            nouveaux = set(actifs_sauvegardes)
            ajoutes  = nouveaux - anciens
            retires  = anciens - nouveaux
            ACTIFS_ACTIONS = list(actifs_sauvegardes)
            ACTIFS_TOUS    = ACTIFS_ACTIONS + ACTIFS_CRYPTO
            # Remettre les retirés dans les candidats (s'ils n'y sont pas déjà)
            for t in retires:
                if t not in SP500_CANDIDATS and "/" not in t:
                    SP500_CANDIDATS.append(t)
            # Retirer les actifs maintenant dans la watchlist des candidats
            SP500_CANDIDATS = [t for t in SP500_CANDIDATS if t not in ACTIFS_TOUS]
            if ajoutes or retires:
                self.logger.info(
                    f"📋 Watchlist restaurée : {len(ACTIFS_ACTIONS)} actifs "
                    f"(+{len(ajoutes)} ajoutés, -{len(retires)} retirés depuis dernier run)"
                )
        except Exception as e:
            self.logger.warning(f"Chargement watchlist: {e}")

    def _charger_poids_appris(self):
        """Applique les poids optimisés du run nocturne si disponibles et valides."""
        try:
            poids_path = DATA_DIR / "poids_appris.json"
            if not poids_path.exists():
                return
            with open(poids_path, encoding="utf-8") as f:
                data = json.load(f)
            poids = data.get("poids", {})
            if (set(poids.keys()) == set(POIDS.keys()) and
                    90 <= sum(poids.values()) <= 110):
                POIDS.update(poids)
                self.logger.info(f"📚 Poids appris chargés : {poids}")
        except Exception as e:
            self.logger.warning(f"Chargement poids appris : {e}")

    def _optimiser_poids(self):
        """
        Analyse l'historique SQLite et ajuste les poids des indicateurs
        en fonction de leur corrélation avec les trades gagnants.
        Sauvegarde dans data/poids_appris.json.
        """
        logger = self.logger
        try:
            with sqlite3.connect(GestionnairePersistance.DB_PATH) as conn:
                rows = conn.execute(
                    "SELECT pnl_pct, croisement_hma, sentiment, rsi, score_fusionne "
                    "FROM trades WHERE type IN ('VENTE','VENTE_AUTO') AND pnl_pct IS NOT NULL"
                ).fetchall()

            if len(rows) < 20:
                logger.info(f"Optimisation poids : données insuffisantes ({len(rows)}/20 trades)")
                return

            trades = [
                {"pnl_pct": r[0], "hma": r[1], "sent": r[2], "rsi": r[3], "score": r[4]}
                for r in rows
            ]
            wr_global = sum(1 for t in trades if t["pnl_pct"] > 0) / len(trades)

            # Win rate avec croisement HMA actif
            hma_trades = [t for t in trades if t["hma"]]
            wr_hma = (sum(1 for t in hma_trades if t["pnl_pct"] > 0) / len(hma_trades)
                      if hma_trades else wr_global)

            # Win rate avec RSI sain au moment de l'achat (30–60)
            rsi_ok = [t for t in trades if t["rsi"] and 30 <= t["rsi"] <= 60]
            wr_rsi = (sum(1 for t in rsi_ok if t["pnl_pct"] > 0) / len(rsi_ok)
                      if rsi_ok else wr_global)

            # Win rate avec sentiment élevé (>0.6)
            sent_ok = [t for t in trades if t["sent"] and t["sent"] > 0.6]
            wr_sent = (sum(1 for t in sent_ok if t["pnl_pct"] > 0) / len(sent_ok)
                       if sent_ok else wr_global)

            poids = dict(POIDS)
            ratio_hma = wr_hma / wr_global if wr_global > 0 else 1.0
            ratio_rsi = wr_rsi / wr_global if wr_global > 0 else 1.0

            poids["hma_crossover"] = max(15, min(40, round(POIDS["hma_crossover"] * ratio_hma)))
            poids["rsi"]           = max(5,  min(20, round(POIDS["rsi"]           * ratio_rsi)))

            # Renormaliser à 100
            total = sum(poids.values())
            if total != 100:
                factor = 100 / total
                for k in poids:
                    poids[k] = max(1, round(poids[k] * factor))
                diff = 100 - sum(poids.values())
                poids[max(poids, key=poids.get)] += diff

            result = {
                "timestamp":         datetime.now(timezone.utc).isoformat(),
                "poids":             poids,
                "nb_trades_analyses": len(trades),
                "wr_global":         round(wr_global * 100, 1),
                "wr_hma":            round(wr_hma   * 100, 1),
                "wr_rsi":            round(wr_rsi   * 100, 1),
                "wr_sent":           round(wr_sent  * 100, 1),
            }
            with open(DATA_DIR / "poids_appris.json", "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)

            POIDS.update(poids)
            logger.info(
                f"✅ Poids optimisés ({len(trades)} trades) "
                f"WR global={wr_global*100:.0f}% HMA={wr_hma*100:.0f}% "
                f"RSI={wr_rsi*100:.0f}% → {poids}"
            )
        except Exception as e:
            logger.warning(f"Optimisation poids échouée : {e}")

    def _mini_backtest(self):
        """
        Backtest léger sur 30 jours — 8 actifs représentatifs.
        Rejoue la stratégie sur des barres historiques Alpaca (gratuit).
        Sauvegarde les résultats dans data/backtest_results.json.
        """
        ACTIFS_TEST  = ["TSLA", "NVDA", "AMD", "AMZN", "META", "AAPL", "BTC/USD", "SPY"]
        FENETRE_JOURS = 30
        logger = self.logger
        logger.info("🔬 Mini-backtest démarré…")

        resultats_actifs = []
        end       = datetime.now(timezone.utc)
        start     = end - timedelta(days=FENETRE_JOURS)
        start_str = start.strftime("%Y-%m-%d")
        end_str   = end.strftime("%Y-%m-%d")

        for ticker in ACTIFS_TEST:
            try:
                if "/" in ticker:
                    df = self.alpaca.api.get_crypto_bars(
                        ticker, TimeFrame.Hour, start=start_str, end=end_str
                    ).df
                else:
                    df = self.alpaca.api.get_bars(
                        ticker, TimeFrame.Hour, start=start_str, end=end_str,
                        limit=1000, feed="iex"
                    ).df

                if df is None or df.empty or len(df) < 60:
                    continue
                df.columns = [c.lower() for c in df.columns]
                df = df.sort_index()

                trades_sim  = []
                en_position = False
                prix_entree = None

                # Fenêtre glissante — step 5 pour la vitesse
                for i in range(50, len(df), 5):
                    slice_df = df.iloc[max(0, i - 120):i]
                    if len(slice_df) < 50:
                        continue
                    tech = self.indicateurs.calculer(ticker, slice_df)
                    prix = tech["prix"]
                    if not prix:
                        continue

                    if not en_position and tech["signal"] == "BUY" and tech["score"] >= SEUIL_TECH_BUY:
                        en_position = True
                        prix_entree = prix
                    elif en_position and prix_entree:
                        pnl_pct = (prix - prix_entree) / prix_entree * 100
                        if (pnl_pct <= -(STOP_LOSS_PCT * 100) or
                                pnl_pct >= (TAKE_PROFIT_PCT * 100) or
                                tech["signal"] == "SELL"):
                            trades_sim.append(round(pnl_pct, 3))
                            en_position = False
                            prix_entree = None

                if trades_sim:
                    wr  = sum(1 for p in trades_sim if p > 0) / len(trades_sim) * 100
                    avg = sum(trades_sim) / len(trades_sim)
                    resultats_actifs.append({
                        "ticker":        ticker,
                        "nb_trades":     len(trades_sim),
                        "win_rate":      round(wr, 1),
                        "avg_pnl_pct":   round(avg, 2),
                        "total_pnl_pct": round(sum(trades_sim), 2),
                    })
                    logger.info(f"  BT {ticker:8s}: {len(trades_sim)} trades WR={wr:.0f}% avg={avg:+.2f}%")

            except Exception as e:
                logger.warning(f"Backtest {ticker}: {e}")
                continue

        if resultats_actifs:
            all_wrs  = [r["win_rate"]    for r in resultats_actifs]
            all_avgs = [r["avg_pnl_pct"] for r in resultats_actifs]
            result = {
                "timestamp":       datetime.now(timezone.utc).isoformat(),
                "fenetre_jours":   FENETRE_JOURS,
                "nb_actifs":       len(resultats_actifs),
                "win_rate_moyen":  round(sum(all_wrs)  / len(all_wrs),  1),
                "avg_pnl_moyen":   round(sum(all_avgs) / len(all_avgs), 2),
                "actifs":          resultats_actifs,
            }
            with open(DATA_DIR / "backtest_results.json", "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
            logger.info(
                f"✅ Backtest terminé — {len(resultats_actifs)} actifs "
                f"WR moyen={result['win_rate_moyen']:.1f}% "
                f"avg P&L={result['avg_pnl_moyen']:+.2f}%"
            )
        else:
            logger.warning("Backtest : aucun résultat produit")

    def _prechauffer_groq(self):
        """
        Pré-appelle Groq pour tous les actifs 30 min avant l'ouverture NYSE.
        Remplit le cache sentiment → premier cycle de trading sans latence Groq.
        Sauvegarde un marqueur dans data/groq_warmup.json.
        """
        logger = self.logger
        if not GROQ_API_KEY:
            logger.info("Pré-chauffe Groq ignorée — GROQ_API_KEY absent")
            return
        logger.info("🔥 Pré-chauffe Groq démarrée…")

        rechauffes = 0
        for ticker in ACTIFS_TOUS:
            try:
                df = self.alpaca.get_barres(ticker)
                if df.empty or len(df) < 50:
                    continue
                tech = self.indicateurs.calculer(ticker, df)
                if tech["prix"]:
                    self.sentiment.scorer(
                        ticker, tech["prix"], tech["score"],
                        tech["indicateurs"].get("rsi"),
                        tech["indicateurs"].get("hma_signal"),
                        tech["indicateurs"].get("atr_pct"),
                    )
                    rechauffes += 1
                    time.sleep(0.4)  # Respect rate-limit Groq gratuit
            except Exception as e:
                logger.warning(f"Pré-chauffe {ticker}: {e}")

        with open(DATA_DIR / "groq_warmup.json", "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "nb_rechauffes": rechauffes,
            }, f)
        logger.info(f"✅ Pré-chauffe Groq terminée — {rechauffes}/{len(ACTIFS_TOUS)} actifs")

    def _mettre_a_jour_correlations(self):
        """
        Calcule la matrice de corrélation réelle (Alpaca, 30 derniers jours).
        Détecte automatiquement les paires ≥ 0.85 et met à jour GROUPES_CORRELES.
        Sauvegarde dans data/correlations.json.
        """
        global GROUPES_CORRELES
        SEUIL_CORR    = 0.85
        ACTIFS_CORR   = ACTIFS_ACTIONS[:20]  # 20 actions — volume raisonnable
        FENETRE_JOURS = 30
        logger = self.logger
        logger.info("📊 Mise à jour corrélations démarrée…")

        try:
            end       = datetime.now(timezone.utc)
            start_str = (end - timedelta(days=FENETRE_JOURS)).strftime("%Y-%m-%d")
            end_str   = end.strftime("%Y-%m-%d")

            returns = {}
            for ticker in ACTIFS_CORR:
                try:
                    df = self.alpaca.get_barres(ticker)
                    if df.empty or len(df) < 20:
                        continue
                    ret = df["close"].pct_change().dropna()
                    returns[ticker] = ret
                    time.sleep(0.1)
                except Exception as e:
                    logger.warning(f"Corrélation {ticker}: {e}")

            if len(returns) < 5:
                logger.warning("Corrélations : données insuffisantes (<5 actifs)")
                return

            df_ret   = pd.DataFrame(returns).dropna(how="all")
            corr_mat = df_ret.corr()
            tickers  = list(corr_mat.columns)

            groupes_detectes = []
            traites          = set()
            for i, t1 in enumerate(tickers):
                if t1 in traites:
                    continue
                groupe = {t1}
                for t2 in tickers[i + 1:]:
                    if t2 in traites:
                        continue
                    try:
                        v = corr_mat.loc[t1, t2]
                        if pd.notna(v) and float(v) >= SEUIL_CORR:
                            groupe.add(t2)
                    except Exception:
                        pass
                if len(groupe) > 1:
                    groupes_detectes.append(groupe)
                    traites.update(groupe)

            # Conserver les groupes crypto (non calculés sur actions)
            groupes_crypto  = [g for g in GROUPES_CORRELES if any("/" in t for t in g)]
            nouveaux_groupes = groupes_detectes + groupes_crypto

            if nouveaux_groupes:
                GROUPES_CORRELES = nouveaux_groupes

            # Top 10 corrélations
            pairs = []
            for i, t1 in enumerate(tickers):
                for t2 in tickers[i + 1:]:
                    try:
                        v = float(corr_mat.loc[t1, t2])
                        if pd.notna(v):
                            pairs.append((t1, t2, round(v, 3)))
                    except Exception:
                        pass
            pairs.sort(key=lambda x: abs(x[2]), reverse=True)

            result = {
                "timestamp":        datetime.now(timezone.utc).isoformat(),
                "seuil_corr":       SEUIL_CORR,
                "nb_actifs":        len(returns),
                "nb_groupes":       len(groupes_detectes),
                "groupes":          [sorted(list(g)) for g in nouveaux_groupes],
                "top_correlations": [{"t1": p[0], "t2": p[1], "corr": p[2]} for p in pairs[:10]],
            }
            with open(DATA_DIR / "correlations.json", "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)

            logger.info(
                f"✅ Corrélations mises à jour — {len(groupes_detectes)} groupes détectés "
                f"sur {len(returns)} actifs | seuil={SEUIL_CORR}"
            )
        except Exception as e:
            logger.warning(f"Mise à jour corrélations échouée : {e}")

    def _stress_test(self, positions: dict, equity: float) -> list:
        """
        Simule l'impact de chocs historiques sur le portefeuille actuel.
        Aucune API — calcul purement mathématique.
        """
        SCENARIOS = [
            {"nom": "Flash Crash COVID (Mar 2020)",  "baisse_pct": -34.0, "description": "S&P500 -34% en 33 jours"},
            {"nom": "Bear Market Tech 2022",          "baisse_pct": -25.0, "description": "Nasdaq -35% sur 12 mois"},
            {"nom": "Correction Taux Oct 2018",       "baisse_pct": -20.0, "description": "Fed hawkish, 4 hausses"},
            {"nom": "Crise Financière 2008",          "baisse_pct": -50.0, "description": "S&P -57% sur 17 mois"},
            {"nom": "Hausse Fed +0.75% surprise",     "baisse_pct":  -5.0, "description": "Réaction court terme"},
            {"nom": "Flash Crash Mai 2010",           "baisse_pct": -10.0, "description": "-10% en 36 minutes"},
        ]
        valeur_pos = sum(p.get("market_value", 0) for p in positions.values())
        results    = []
        for sc in SCENARIOS:
            impact     = valeur_pos * (sc["baisse_pct"] / 100)
            eq_apres   = equity + impact
            dd_total   = (eq_apres - self.risque.capital_initial) / self.risque.capital_initial * 100
            results.append({
                "nom":            sc["nom"],
                "baisse_pct":     sc["baisse_pct"],
                "description":    sc["description"],
                "impact_pnl":     round(impact, 2),
                "equity_apres":   round(eq_apres, 2),
                "drawdown_total": round(dd_total, 2),
                "couvert_sl":     abs(sc["baisse_pct"]) <= STOP_LOSS_PCT * 100,
            })
        return results

    def _rotation_watchlist(self, positions: dict):
        """
        Analyse ~15 candidats S&P500 quand le marché est fermé.
        Remplace le moins performant des actifs non tenus si un meilleur est trouvé.
        """
        global ACTIFS_ACTIONS, ACTIFS_TOUS, SP500_CANDIDATS
        if not SP500_CANDIDATS:
            return
        MAX_A    = 25   # 25 candidats × ~3 runs/nuit = ~75 analyses/nuit
        day_idx  = datetime.now(timezone.utc).timetuple().tm_yday
        hour_idx = datetime.now(timezone.utc).hour
        start    = ((day_idx * 24 + hour_idx) * MAX_A) % len(SP500_CANDIDATS)
        candidats = SP500_CANDIDATS[start:start + MAX_A]
        if len(candidats) < MAX_A:
            candidats += SP500_CANDIDATS[:MAX_A - len(candidats)]

        # Priorité aux tickers trending Yahoo Finance
        trending_yahoo = []
        try:
            trending_yahoo = [t for t in self.yahoo.get_trending()
                              if t not in ACTIFS_TOUS and t in SP500_CANDIDATS][:5]
        except Exception:
            pass
        if trending_yahoo:
            candidats = trending_yahoo + [c for c in candidats if c not in trending_yahoo]

        self.logger.info(f"🔄 Rotation watchlist — analyse {len(candidats)} candidats")
        tops = []
        for ticker in candidats:
            try:
                df = self.alpaca.get_barres(ticker)
                if df.empty or len(df) < 50:
                    continue
                tech = self.indicateurs.calculer(ticker, df)
                if tech["score"] >= SEUIL_TECH_BUY:
                    tops.append({"ticker": ticker, "score": tech["score"], "prix": tech["prix"]})
                time.sleep(0.15)
            except Exception as e:
                self.logger.debug(f"Rotation {ticker}: {e}")

        if not tops:
            return
        tops.sort(key=lambda x: x["score"], reverse=True)
        entrant = tops[0]

        # Charger scores backtest pour trouver le moins performant
        scores_bt = {}
        try:
            br = DATA_DIR / "backtest_results.json"
            if br.exists():
                with open(br) as f:
                    bt = json.load(f)
                for a in bt.get("actifs", []):
                    scores_bt[a["ticker"]] = a.get("avg_pnl_pct", 0.0)
        except Exception:
            pass

        remplacables = [a for a in ACTIFS_ACTIONS if a not in positions]
        if not remplacables:
            return
        sortant = min(remplacables, key=lambda a: scores_bt.get(a, 0.0))

        if entrant["score"] >= SEUIL_TECH_BUY and entrant["ticker"] not in ACTIFS_TOUS:
            ACTIFS_ACTIONS.remove(sortant)
            ACTIFS_ACTIONS.append(entrant["ticker"])
            ACTIFS_TOUS = ACTIFS_ACTIONS + ACTIFS_CRYPTO
            if entrant["ticker"] in SP500_CANDIDATS:
                SP500_CANDIDATS.remove(entrant["ticker"])
            SP500_CANDIDATS.append(sortant)
            self.logger.info(f"🔄 Watchlist : {sortant} → {entrant['ticker']} (score {entrant['score']:.0f})")
            try:
                wl_path = DATA_DIR / "watchlist_dynamique.json"
                hist = []
                if wl_path.exists():
                    with open(wl_path) as f:
                        hist = json.load(f).get("historique", [])
                hist.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "sortant": sortant, "entrant": entrant["ticker"],
                    "score_entrant": entrant["score"],
                })
                with open(wl_path, "w") as f:
                    json.dump({
                        "timestamp":   datetime.now(timezone.utc).isoformat(),
                        "actifs_actifs": ACTIFS_ACTIONS,
                        "top_candidats": tops[:5],
                        "historique":  hist[-20:],
                    }, f, indent=2)
            except Exception as e:
                self.logger.warning(f"Watchlist save: {e}")

    def _verifier_sante_bot(self):
        """Envoie une alerte Discord si le bot ne tourne pas correctement."""
        try:
            if JSON_SORTIE.exists():
                age = time.time() - JSON_SORTIE.stat().st_mtime
                if age > 900:  # 15 minutes
                    self.discord._envoyer(
                        f"🚨 **ALERTE — Bot inactif** | Dernier update: il y a {age/60:.0f} min\n"
                        f"→ Vérifier GitHub Actions : https://github.com/lechat45/bot.github.io/actions"
                    )
        except Exception as e:
            self.logger.warning(f"Santé bot: {e}")

    def _verifier_circuit_breaker(self) -> tuple:
        """
        Circuit Breaker : arrête le trading si pertes journalières > MAX_DAILY_LOSS_PCT.
        Calcul basé sur les trades fermés du jour (SQLite).
        Retourne (circuit_ouvert: bool, pnl_jour: float)
        """
        try:
            today = datetime.now(timezone.utc).date().isoformat()
            with sqlite3.connect(GestionnairePersistance.DB_PATH) as conn:
                row = conn.execute(
                    "SELECT SUM(pnl) FROM trades "
                    "WHERE type IN ('VENTE', 'VENTE_AUTO') "
                    "AND DATE(timestamp) = ?",
                    (today,)
                ).fetchone()
            pnl_jour = float(row[0]) if row[0] else 0.0
            pnl_jour_pct = abs(pnl_jour) / self.risque.capital_initial * 100 if pnl_jour < 0 else 0.0
            ouvert = pnl_jour_pct >= MAX_DAILY_LOSS_PCT
            if ouvert:
                self.logger.error(
                    f"🚨 CIRCUIT BREAKER — Pertes jour: ${pnl_jour:.2f} "
                    f"({pnl_jour_pct:.2f}%) ≥ {MAX_DAILY_LOSS_PCT}% — trading suspendu"
                )
                self.discord._envoyer(
                    f"🚨 **CIRCUIT BREAKER** | Pertes jour: ${pnl_jour:.2f} "
                    f"({pnl_jour_pct:.2f}%) | Trading suspendu jusqu'à demain UTC"
                )
            return ouvert, round(pnl_jour, 2)
        except Exception as e:
            self.logger.warning(f"Circuit breaker: {e}")
            return False, 0.0

    def _prefetch_barres_parallele(self, actifs: list):
        """
        Pré-charge les barres OHLCV de tous les actifs en parallèle (8 threads).
        Remplit le cache AlpacaClientV2 → les appels séquentiels suivants sont instantanés.
        Groq reste séquentiel (rate-limit 25 RPM oblige).
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def fetch_one(ticker):
            try:
                return ticker, self.alpaca.get_barres(ticker)
            except Exception as e:
                self.logger.debug(f"Prefetch {ticker}: {e}")
                return ticker, pd.DataFrame()

        self.logger.info(f"⚡ Prefetch parallèle — {len(actifs)} actifs (8 threads)")
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(fetch_one, t): t for t in actifs}
            for future in as_completed(futures, timeout=45):
                try:
                    ticker, df = future.result()
                    if not df.empty:
                        self.alpaca._cache_bars[ticker]    = df
                        self.alpaca._cache_bars_ts[ticker] = time.time()
                except Exception as e:
                    self.logger.debug(f"Prefetch result: {e}")
        self.logger.info(f"⚡ Prefetch terminé en {time.time()-t0:.1f}s")

    def _monte_carlo(self) -> dict:
        """
        Simulation Monte Carlo sur l'historique SQLite (5 000 permutations).
        Calcule la distribution des équités finales et le drawdown max probable.
        Sauvegarde dans data/monte_carlo.json.
        """
        import random as _random
        logger = self.logger
        logger.info("🎲 Monte Carlo démarré…")
        try:
            with sqlite3.connect(GestionnairePersistance.DB_PATH) as conn:
                rows = conn.execute(
                    "SELECT pnl_pct FROM trades "
                    "WHERE type IN ('VENTE','VENTE_AUTO') AND pnl_pct IS NOT NULL "
                    "ORDER BY id DESC LIMIT 200"
                ).fetchall()
            if len(rows) < 20:
                logger.info(f"Monte Carlo: données insuffisantes ({len(rows)}/20)")
                return {}
            returns = [r[0] / 100 for r in rows]
            N_SIM   = 5000
            cap     = self.risque.capital_initial
            final_equities = []
            max_drawdowns  = []
            for _ in range(N_SIM):
                sample = _random.choices(returns, k=len(returns))
                equity = cap
                peak   = cap
                max_dd = 0.0
                for r in sample:
                    equity *= (1 + r)
                    if equity > peak:
                        peak = equity
                    dd = (peak - equity) / peak if peak > 0 else 0
                    if dd > max_dd:
                        max_dd = dd
                final_equities.append(equity)
                max_drawdowns.append(max_dd)
            final_equities.sort()
            max_drawdowns.sort(reverse=True)
            n        = len(final_equities)
            p5_eq    = final_equities[int(n * 0.05)]
            p50_eq   = final_equities[int(n * 0.50)]
            p95_eq   = final_equities[int(n * 0.95)]
            dd_95    = max_drawdowns[int(n * 0.05)]   # 95e percentile des DD
            dd_med   = max_drawdowns[int(n * 0.50)]
            ruin     = sum(1 for e in final_equities if e < cap * 0.5) / n * 100
            # Histogramme 20 buckets
            e_min = final_equities[0]
            e_max = final_equities[-1]
            bsize = (e_max - e_min) / 20 if e_max > e_min else 1
            histo = [0] * 20
            for e in final_equities:
                idx = min(19, int((e - e_min) / bsize))
                histo[idx] += 1
            result = {
                "timestamp":          datetime.now(timezone.utc).isoformat(),
                "nb_simulations":     N_SIM,
                "nb_trades":          len(returns),
                "capital_initial":    cap,
                "p5_equity":          round(p5_eq,  2),
                "p50_equity":         round(p50_eq, 2),
                "p95_equity":         round(p95_eq, 2),
                "worst_dd_95pct":     round(dd_95   * 100, 2),
                "median_dd":          round(dd_med  * 100, 2),
                "ruin_prob":          round(ruin,   2),
                "histogram":          histo,
                "hist_min":           round(e_min,  2),
                "hist_max":           round(e_max,  2),
            }
            with open(DATA_DIR / "monte_carlo.json", "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
            logger.info(
                f"🎲 Monte Carlo terminé — {N_SIM} sim | "
                f"P5:${p5_eq:,.0f} P50:${p50_eq:,.0f} P95:${p95_eq:,.0f} | "
                f"DD95%:{dd_95*100:.1f}% | Ruine:{ruin:.1f}%"
            )
            return result
        except Exception as e:
            logger.warning(f"Monte Carlo: {e}")
            return {}

    def _apprentissage_patterns(self, decisions: list, marche_ouvert: bool):
        """
        Enregistre les signaux forts et vérifie les prédictions 20h+ plus tard.
        Calcule un taux de précision global pour auto-évaluation.
        """
        PATTERNS_FILE = DATA_DIR / "patterns_appris.json"
        try:
            existants = []
            if PATTERNS_FILE.exists():
                with open(PATTERNS_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                existants = data.get("patterns", [])

            now_utc   = datetime.now(timezone.utc)
            n_verif   = 0
            n_correct = 0

            for p in existants:
                if p.get("verifie"):
                    continue
                try:
                    ts  = datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if (now_utc - ts).total_seconds() / 3600 < 20:
                        continue
                    d = next((x for x in decisions if x["ticker"] == p["ticker"]), None)
                    if d and d.get("prix") and p.get("prix"):
                        variation = (d["prix"] - p["prix"]) / p["prix"] * 100
                        correct   = (p["signal"] == "BUY" and variation > 0) or \
                                    (p["signal"] == "SELL" and variation < 0)
                        p.update({"verifie": True, "prix_24h": round(d["prix"], 4),
                                  "variation_pct": round(variation, 2), "correct": correct})
                        n_verif  += 1
                        n_correct += int(correct)
                except Exception:
                    pass

            nouveaux = [
                {"timestamp": now_utc.isoformat(), "ticker": d["ticker"],
                 "signal": d["signal_technique"], "score": d["score_technique"],
                 "prix": d["prix"], "rsi": d.get("rsi"),
                 "marche_ouvert": marche_ouvert, "verifie": False}
                for d in decisions
                if d.get("signal_technique") in ("BUY", "SELL")
                and d.get("score_technique", 0) >= SEUIL_CONVICTION_FAIBLE
                and d.get("prix")
            ]

            tous = (existants + nouveaux)[-500:]
            verifies  = [p for p in tous if p.get("verifie")]
            precision = 0.0
            if verifies:
                precision = round(sum(1 for p in verifies if p.get("correct")) / len(verifies) * 100, 1)

            with open(PATTERNS_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "timestamp":         now_utc.isoformat(),
                    "patterns":          tous,
                    "nb_total":          len(tous),
                    "nb_verifies":       len(verifies),
                    "precision_globale": precision,
                    "verifies_cycle":    n_verif,
                    "corrects_cycle":    n_correct,
                }, f, indent=2)

            if n_verif > 0:
                self.logger.info(f"📐 Patterns: {n_correct}/{n_verif} corrects (précision globale {precision:.1f}%)")
        except Exception as e:
            self.logger.warning(f"Apprentissage patterns: {e}")

    def _auditer_positions(self, positions: dict, marche_ouvert: bool = True):
        """
        Vérifie stop-loss, take-profit et trailing stop sur toutes les positions.
        CRYPTO : audité 24/7 (même marché fermé).
        ACTIONS : audité seulement si marché ouvert.
        """
        # Ensemble de tous les tickers crypto connus (deux formats)
        _crypto_tickers = set(ACTIFS_CRYPTO) | {t.replace("/", "") for t in ACTIFS_CRYPTO}
        for ticker, pos in list(positions.items()):
            # Détection crypto : slash dans le ticker, OU dans la liste connue,
            # OU finit par USD avec ≤8 chars (BTCUSD, DOGEUSD, etc.)
            est_crypto = (
                "/" in ticker
                or ticker in _crypto_tickers
                or (ticker.endswith("USD") and len(ticker) <= 8)
            )
            # Actions : seulement si marché ouvert
            if not est_crypto and not marche_ouvert:
                continue
            # LT positions protégées de la vente automatique CT
            if self.portefeuille_lt and self.portefeuille_lt.est_position_lt(ticker):
                continue
            # PDT / cooldown actif → ne pas retenter avant expiration
            if self._en_cooldown(ticker):
                reste = int(self._cooldown[ticker] - time.time())
                self.logger.debug(f"[AUDIT] {ticker} en cooldown PDT/stop — {reste // 3600}h{(reste % 3600) // 60}m restants")
                continue

            raison = None
            plpc = pos.get("unrealized_plpc", 0)
            try:
                if self.risque.verifier_stop_loss(pos):
                    raison = f"STOP-LOSS {plpc:.2f}%"
                elif self.risque.verifier_take_profit(pos):
                    raison = f"TAKE-PROFIT {plpc:.2f}%"
                elif self.risque.verifier_trailing_stop(pos):
                    raison = f"TRAILING-STOP depuis plus haut"
            except Exception as e:
                self.logger.debug(f"Audit {ticker}: {e}")
                continue

            if raison:
                resultat = self.alpaca.liquider_position(ticker)

                if resultat == self.alpaca.LIQUIDATION_PDT:
                    # ── PDT bloqué : cooldown jusqu'à l'ouverture du marché demain ──
                    # ~23h pour s'assurer qu'on réessaie le lendemain matin
                    self._cooldown[ticker] = time.time() + 82_800   # 23 heures
                    self._sauvegarder_cooldowns()
                    self.logger.warning(
                        f"[PDT] {ticker} — vente bloquée (Pattern Day Trader). "
                        f"Réessai demain à l'ouverture. ({raison})"
                    )
                    # Notifier Discord une seule fois
                    try:
                        self.discord._envoyer(
                            f"⚠️ **PDT BLOQUÉ** — `{ticker}`\n"
                            f"Vente {raison} impossible aujourd'hui (equity < $25k hier).\n"
                            f"La position sera vendue à l'ouverture du marché demain."
                        )
                    except Exception:
                        pass

                elif resultat is True:
                    pnl = round(pos.get("unrealized_pl", 0), 2)
                    trade = {
                        "type": "VENTE_AUTO", "ticker": ticker,
                        "prix": pos.get("current_price", 0),
                        "pnl": pnl,
                        "pnl_pct": round(plpc, 2),
                        "raison": raison,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    self.historique_trades.append(trade)
                    self.persistance.sauvegarder(trade)
                    # Cooldown pour éviter de re-rentrer immédiatement
                    if "STOP-LOSS" in raison or "TRAILING" in raison:
                        self._definir_cooldown(ticker)
                    # Mettre à jour la blacklist (pertes consécutives)
                    try:
                        self.blacklist.enregistrer_resultat(ticker, pnl, self.discord._envoyer)
                    except Exception:
                        pass
                    # Notification Discord
                    self.discord.notifier_vente(ticker, pnl, plpc, raison)
                    self.logger.info(f"[AUTO] {ticker} — {raison} | PnL: {pnl:+.2f}$")

                else:
                    self.logger.error(f"[AUTO] Liquidation ÉCHOUÉE pour {ticker} — {raison}")

    def _analyser_actif(self, ticker: str, positions: dict, compte: dict,
                        executer: bool = True, kelly_base: float = None,
                        mode_crypto_nuit: bool = False) -> dict:
        base = {
            "ticker": ticker, "action_executee": "AUCUNE",
            "mode_crypto_nuit": mode_crypto_nuit,
            "score_technique": 50, "score_sentiment": 0.55,
            "score_fusionne": 50, "conviction": "FAIBLE",
            "signal_technique": "HOLD", "biais_groq": "neutre",
            "croisement_hma": False, "prix": None,
            "rsi": None, "atr_pct": None, "indicateurs": {},
            "momentum_spy": 0.0,
            "raison": "",
            "earnings_blackout": False,
            "fondamentaux": {},
            "sacha_pro": {"signal": "NEUTRAL", "details": "Non calculé"},
            "position_lt": False,   # True si ce ticker est tenu par le portefeuille LT
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Normalisation symbole — Alpaca retourne BTCUSD mais nos actifs sont BTC/USD
        ticker_norm   = ticker.replace("/", "")
        dans_position = ticker in positions or ticker_norm in positions

        # Limite exposition crypto : si déjà > 15% equity dans ce ticker, ne pas racheter
        if "/" in ticker and not dans_position:
            pos_key = ticker_norm  # ex: "BTCUSD"
            pos_data = positions.get(ticker) or positions.get(pos_key)
            if pos_data:
                val = float(pos_data.get("market_value", 0))
                equity = float(compte.get("equity", 100000))
                if val > equity * 0.15:
                    dans_position = True
                    self.logger.info(
                        f"[EXPO] {ticker} déjà à {val/equity*100:.1f}% equity "
                        f"(>${val:.0f}) — achat bloqué (> 15%)"
                    )

        # ── 1. Données OHLC via SDK Alpaca ────────────────────────────────
        # Mode nuit crypto : barres 30min (plus réactives que 1h)
        if mode_crypto_nuit and "/" in ticker:
            df = self.alpaca.get_barres_crypto_nuit(ticker)
        else:
            df = self.alpaca.get_barres(ticker)
        if df.empty or len(df) < 50:
            base["raison"] = "Données OHLC insuffisantes"
            return base

        # ── 2. Indicateurs pandas-ta (HMA, EMA100, RSI, MACD, BB, ADX) ──
        tech = self.indicateurs.calculer(ticker, df)
        base.update({
            "signal_technique": tech["signal"],
            "score_technique":  tech["score"],
            "prix":             tech["prix"],
            "croisement_hma":   tech["croisement_hma"],
            "rsi":              tech["indicateurs"].get("rsi"),
            "atr_pct":          tech["indicateurs"].get("atr_pct"),
            "indicateurs":      tech["indicateurs"],
        })

        # Feature D : Stocker l'ATR pour le calcul breakeven (GestionnaireRisque)
        try:
            atr_pct_val = tech["indicateurs"].get("atr_pct", 1.0) or 1.0
            self.risque.breakeven_atr[ticker] = max(atr_pct_val / 100, 0.005)  # Min 0.5%
        except Exception:
            pass

        # Feature B : Bloquer achat si volatilité Garman-Klass chaotique (actions seulement)
        if not dans_position and "/" not in ticker:
            if tech["indicateurs"].get("vol_regime") == "CHAOTIQUE":
                base["raison"] = "Volatilité extrême (Garman-Klass) — trade bloqué"
                return base

        # ── 2b. Score momentum vs SPY ─────────────────────────────────────
        if ticker != "SPY" and not self.df_spy.empty:
            mom = self.momentum.score_vs_spy(ticker, df, self.df_spy)
            base["momentum_spy"] = mom

        # ── 2c. Fondamentaux Yahoo Finance ─────────────────────────────────
        fondamentaux = {}
        try:
            fondamentaux = self.yahoo.get_fondamentaux(ticker)
            # News sentiment Yahoo (léger)
            news_sent = self.yahoo.get_news_sentiment(ticker)
            fondamentaux["news_sentiment"] = news_sent
        except Exception:
            pass
        base["fondamentaux"] = fondamentaux

        # ── 2d. Sacha Pro Strategy (daily bars — long terme) ───────────────
        # Traduction Python du Pine Script «Sacha Pro – Multi-TF Strategy v3»
        # Agit comme couche de CONFIRMATION : ajuste score_tech avant decider()
        try:
            df_daily = self.alpaca.get_barres_daily(ticker)
            sacha = self.strategie_sacha.analyser(ticker, df_daily)
            base["sacha_pro"] = sacha

            if sacha["signal"] == "BUY":
                # EMA9 croise EMA21 à la hausse sur daily + prix > EMA50 + RSI < 65
                tech["score"] = min(100.0, tech["score"] * StrategieSachaPro.BOOST_BUY)
                self.logger.info(
                    f"[SACHA PRO] {ticker} BUY daily "
                    f"{'🔺 CROSSOVER' if sacha['ema_crossover'] else '↗ tendance'} "
                    f"→ score_tech × {StrategieSachaPro.BOOST_BUY} "
                    f"= {tech['score']:.1f} | {sacha['details']}"
                )
            elif sacha["signal"] == "SELL":
                # EMA9 croise EMA21 à la baisse sur daily + prix < EMA50 + RSI > 35
                tech["score"] = max(0.0, tech["score"] * StrategieSachaPro.BOOST_SELL)
                self.logger.info(
                    f"[SACHA PRO] {ticker} SELL daily "
                    f"{'🔻 CROSSUNDER' if sacha['ema_crossunder'] else '↘ tendance'} "
                    f"→ score_tech × {StrategieSachaPro.BOOST_SELL} "
                    f"= {tech['score']:.1f} | {sacha['details']}"
                )
            # Mise à jour score_technique dans base pour le dashboard
            base["score_technique"] = tech["score"]
        except Exception as e:
            self.logger.debug(f"Sacha Pro {ticker}: {e}")

        # ── 3. Sentiment Groq — uniquement si signal fort ou position ouverte
        sent = {"score": 0.55, "resume": "Non analysé",
                "biais": "neutre", "facteurs_positifs": [],
                "facteurs_negatifs": [], "source": "skip"}

        # ── Filtre volatilité excessive crypto nuit ───────────────────────
        if mode_crypto_nuit and "/" in ticker:
            atr_pct = tech["indicateurs"].get("atr_pct", 0) or 0
            if atr_pct > 8.0:
                base["raison"] = f"Volatilité crypto excessive (ATR {atr_pct:.1f}% > 8%) — nuit"
                return base

        # ── Mode crypto nuit : boost score + seuil abaissé ───────────────────
        if mode_crypto_nuit and "/" in ticker:
            # +15% boost uniquement si indicateurs bien calculés (df >= 50 barres)
            if len(df) >= 50:
                tech["score"] = min(100.0, tech["score"] * 1.15)
            base["score_technique"] = tech["score"]
            # Seuil d'analyse abaissé en mode nuit
            seuil_groq_crypto = CRYPTO_NUIT_SEUIL_BUY
        else:
            seuil_groq_crypto = SEUIL_CONVICTION_FAIBLE

        should_analyze_groq = (
            (tech["signal"] == "BUY"  and tech["score"] >= seuil_groq_crypto) or
            tech["signal"] == "SELL" or
            dans_position
        )
        # Quand marché ACTIONS fermé mais crypto active : analyser en cache prioritaire
        # Pour les cryptos avec signal fort, on force l'analyse même sans cache
        if not executer and not mode_crypto_nuit:
            should_analyze_groq = should_analyze_groq and (
                ticker in self.sentiment._cache or ticker in self.gemini._cache
            )
        elif not executer and mode_crypto_nuit and "/" in ticker:
            # Crypto nuit : analyse si signal fort (même sans cache)
            should_analyze_groq = (
                tech["signal"] == "BUY" and tech["score"] >= seuil_groq_crypto
            ) or dans_position
        if should_analyze_groq:
            sent = self.sentiment.scorer(
                ticker, tech["prix"] or 0,
                tech["score"],
                tech["indicateurs"].get("rsi"),
                tech["indicateurs"].get("hma_signal"),
                tech["indicateurs"].get("atr_pct"),
                marche_ouvert=executer,
            )
            # Fallback Gemini si Groq indisponible (429 ou backoff)
            if sent["source"] == "fallback" and GOOGLE_AI_KEY:
                sent = self.gemini.scorer(
                    ticker, tech["prix"] or 0,
                    tech["score"],
                    tech["indicateurs"].get("rsi"),
                    tech["indicateurs"].get("hma_signal"),
                    tech["indicateurs"].get("atr_pct"),
                    marche_ouvert=executer,
                )

        base["score_sentiment"]    = sent["score"]
        base["biais_groq"]         = sent.get("biais", "neutre")
        base["resume_groq"]        = sent.get("resume", "")
        base["facteurs_positifs"]  = sent.get("facteurs_positifs", [])
        base["facteurs_negatifs"]  = sent.get("facteurs_negatifs", [])

        # ── 3b. Feature 3 : Ajustement saisonnalité ──────────────────────────
        mois = datetime.now().month
        mult_saison = SAISONNALITE.get(mois, 1.0)
        if mult_saison != 1.0 and tech["signal"] == "BUY":
            tech["score"] = min(100.0, tech["score"] * mult_saison)
            base["score_technique"] = tech["score"]
            if mult_saison != 1.0:
                base.setdefault("raison", "")

        # ── 3c. Feature 4 : Vérifier blacklist ───────────────────────────────
        if self.blacklist.est_blackliste(ticker):
            base["raison"] = f"🚫 Ticker blacklisté — trop de faux signaux récents (48h)"
            base["action"] = "NEUTRE"
            return base

        # ── 3d. Feature 10 : Bonus initiés SEC ───────────────────────────────
        if self._insiders_data.get(ticker, False):
            tech["score"] = min(100.0, tech["score"] + 5.0)
            base["score_technique"] = tech["score"]
            base["raison"] = base.get("raison", "") + " | 👔 Achat initié SEC Form 4"

        # ── 4. Décision fusionnée ─────────────────────────────────────────
        dec = self.decision.decider(tech, sent, dans_position,
                                    fear_greed=self._fear_greed_value,
                                    fondamentaux=fondamentaux)
        base.update({
            "score_fusionne": dec["score_fusionne"],
            "conviction":     dec["conviction"],
            "raison":         dec["raison"],
        })

        # ── 4b. Feature 3 : Ajouter mention saisonnalité dans la raison ──────
        if mult_saison != 1.0 and dec.get("action") == "ACHAT":
            base["raison"] = base.get("raison", "") + f" | Saison×{mult_saison:.2f}"

        # ── 5. Exécution ──────────────────────────────────────────────────
        if not executer:
            if dec["action"] == "ACHAT":
                base["raison"] = f"📋 Ordre préparé (marché fermé) — {dec['raison']}"
                base["action_executee"] = "PRÉPARE"
            return base

        # ── Mode temporel — stratégies par heure ─────────────────────────────
        mode_temp = self._mode_temporel()
        if dec["action"] == "ACHAT" and not self.pause_drawdown:
            # ── Filtre mode OUVERTURE : bloquer nouveaux achats 9:30-10:00 ──
            if mode_temp == "OUVERTURE" and "/" not in ticker:
                base["raison"] = "⏰ Ouverture marché — attente 30min (volatilité élevée)"
                return base
            # ── Mode CLOTURE : seuil d'achat rehaussé de 5pts ─────────────
            if mode_temp == "CLOTURE" and "/" not in ticker:
                seuil_cloture = SEUIL_TECH_BUY + 5
                if dec["score_fusionne"] < seuil_cloture:
                    base["raison"] = f"⏰ Clôture proche — score {dec['score_fusionne']:.0f} < {seuil_cloture} (seuil renforcé)"
                    return base

        if dec["action"] == "ACHAT" and not self.pause_drawdown:
            # ── Limite exposition totale crypto (avant tous les autres garde-fous) ──
            if "/" in ticker and not dans_position:
                total_crypto_usd = sum(
                    float(p.get("market_value", 0))
                    for t, p in positions.items()
                    if "/" in t or t.upper() in {
                        "BTCUSD", "ETHUSD", "SOLUSD", "DOGEUSD",
                        "AVAXUSD", "LINKUSD", "LTCUSD", "BCHUSD",
                        "XRPUSD", "UNIUSD"
                    }
                )
                equity = float(compte.get("equity", 100000))
                if total_crypto_usd > equity * MAX_CRYPTO_EXPOSURE_PCT / 100:
                    base["raison"] = (
                        f"Limite crypto {MAX_CRYPTO_EXPOSURE_PCT:.0f}% atteinte "
                        f"({total_crypto_usd / equity * 100:.1f}%)"
                    )
                    return base

            # ── Garde-fous ordonnés par priorité ─────────────────────────
            if dans_position:
                base["raison"] = "Position déjà ouverte — achat ignoré"
            elif self._en_cooldown(ticker):
                reste = int(self._cooldown[ticker] - time.time())
                base["raison"] = f"Cooldown actif — {reste // 60}min {reste % 60}s restants"
            elif "/" in ticker and self._achats_crypto_cycle >= (MAX_ACHATS_CRYPTO_NUIT if mode_crypto_nuit else MAX_ACHATS_PAR_CYCLE):
                limite = MAX_ACHATS_CRYPTO_NUIT if mode_crypto_nuit else MAX_ACHATS_PAR_CYCLE
                base["raison"] = f"Limite {limite} achats crypto/cycle atteinte {'(mode crypto nuit)' if mode_crypto_nuit else ''}"
            elif "/" not in ticker and self._achats_ce_cycle >= MAX_ACHATS_PAR_CYCLE:
                base["raison"] = f"Limite {MAX_ACHATS_PAR_CYCLE} achats actions/cycle atteinte"
            elif self._actifs_correles(ticker, positions):
                base["raison"] = "Actif corrélé déjà en position"
            elif "/" in ticker and tech["score"] < SEUIL_CRYPTO_WEEKEND and not mode_crypto_nuit:
                base["raison"] = f"Score crypto insuffisant — {tech['score']:.0f} < {SEUIL_CRYPTO_WEEKEND}"
            elif not self.risque.secteur_ok(ticker, positions):
                base["raison"] = f"Secteur saturé (max {MAX_PAR_SECTEUR})"
            elif self._portfolio_heat >= MAX_HEAT_PCT:
                base["raison"] = f"Portfolio heat {self._portfolio_heat:.1f}% ≥ {MAX_HEAT_PCT:.0f}% — achat bloqué"
            # Q6: Filtre horaire — évite première et dernière 30min du marché
            elif self._mins_depuis_open < 30:
                base["raison"] = f"Filtre horaire — {self._mins_depuis_open:.0f}min depuis ouverture (< 30min)"
            elif self._mins_avant_close < 30:
                base["raison"] = f"Filtre horaire — {self._mins_avant_close:.0f}min avant clôture (< 30min)"
            # Q7: Earnings Blackout
            elif self.yahoo.est_blackout_earnings(ticker):
                base["raison"] = "📅 EARNINGS BLACKOUT — résultats ±3j"
                base["earnings_blackout"] = True
            # Q8: Filtre RSI suracheté — bloquer achat si RSI > RSI_MAX_ACHAT (76)
            elif tech["indicateurs"].get("rsi") and tech["indicateurs"]["rsi"] > RSI_MAX_ACHAT:
                base["raison"] = f"RSI suracheté {tech['indicateurs']['rsi']:.1f} > {RSI_MAX_ACHAT} — achat bloqué"
            # Q9: Filtre qualité signal — sans HMA crossover, exiger score > 68 (actions seules)
            # Basé sur les données réelles : sans crossover + score < 68 → pertes fréquentes
            # (SPY score=62.5 RSI=77, MSFT score=63.1, PENN score=63.1, COIN score=61.9)
            elif (not tech.get("croisement_hma") and
                  dec["score_fusionne"] < 68 and
                  "/" not in ticker):
                base["raison"] = (
                    f"Qualité insuffisante sans HMA crossover "
                    f"(score {dec['score_fusionne']:.0f} < 68) — signal trop faible"
                )
            else:
                montant = self.risque.allocation(
                    dec["conviction"], tech["indicateurs"].get("atr_pct"),
                    kelly_base=kelly_base)
                # Boost allocation crypto la nuit (seule opportunité active)
                if mode_crypto_nuit and "/" in ticker:
                    montant = round(montant * CRYPTO_NUIT_ALLOC_MULT, 2)
                if not self.risque.capital_suffisant(compte, montant):
                    base["raison"] = f"Capital insuffisant (besoin ${montant:.0f})"
                else:
                    try:
                        self.alpaca.soumettre_ordre(ticker, montant, "buy")
                        base["action_executee"] = "ACHAT"
                        trade = {
                            "type": "ACHAT", "ticker": ticker,
                            "montant": montant, "prix": tech["prix"],
                            "conviction": dec["conviction"],
                            "score_fusionne": dec["score_fusionne"],
                            "croisement_hma": dec["croisement_hma"],
                            "sentiment": sent["score"],
                            "rsi": tech["indicateurs"].get("rsi"),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        self.historique_trades.append(trade)
                        self.persistance.sauvegarder(trade)
                        self._definir_cooldown(ticker, crypto_nuit=mode_crypto_nuit)
                        if "/" in ticker:
                            self._achats_crypto_cycle += 1
                        else:
                            self._achats_ce_cycle += 1
                        self._sauvegarder_historique()
                        self.discord.notifier_achat(
                            ticker, montant, tech["prix"],
                            dec["conviction"], dec["score_fusionne"], sent["score"]
                        )
                        self.logger.info(
                            f"[ACHAT] {ticker} {dec['conviction']} "
                            f"${montant:.0f} | fused={dec['score_fusionne']:.0f} "
                            f"sent={sent['score']:.2f} HMA={'✓' if dec['croisement_hma'] else '—'}"
                        )
                    except Exception as e:
                        base["raison"] = f"Erreur ordre : {e}"

        elif (dec["action"] == "VENTE" or
              (dans_position and mode_temp == "CLOTURE" and
               dec["score_fusionne"] < (SEUIL_TECH_BUY - 10) and "/" not in ticker)) and dans_position:
            # ── Protection portefeuille long terme ────────────────────────────
            # Si ce ticker est tenu par le portefeuille LT, le bot CT ne peut pas vendre.
            # Seul _gerer_portefeuille_lt() peut déclencher la vente (crossunder daily).
            if (self.portefeuille_lt is not None and
                    self.portefeuille_lt.est_position_lt(ticker)):
                base["position_lt"] = True
                base["raison"] = "🔒 Position long terme Sacha Pro — vente CT bloquée"
                sacha_lt = base.get("sacha_pro", {})
                base["raison"] += f" | {sacha_lt.get('details', '')}"
                self.logger.info(f"[LT] {ticker} — vente CT bloquée (position LT protégée)")
                return base

            # PDT / cooldown actif → ne pas retenter la vente
            if self._en_cooldown(ticker):
                reste = int(self._cooldown[ticker] - time.time())
                base["raison"] = f"⏳ PDT cooldown — vente bloquée {reste // 3600}h{(reste % 3600) // 60}m"
                self.logger.debug(f"[SELL] {ticker} en cooldown PDT — {reste // 3600}h{(reste % 3600) // 60}m restants")
                return base

            # Récupère la position avec normalisation du symbole
            pos = positions.get(ticker) or positions.get(ticker_norm, {})
            _res_liq = self.alpaca.liquider_position(ticker)

            if _res_liq == self.alpaca.LIQUIDATION_PDT:
                # PDT bloqué — cooldown 23h + alerte Discord unique
                self._cooldown[ticker] = time.time() + 82_800
                self._sauvegarder_cooldowns()
                self.logger.warning(f"[PDT] {ticker} — vente bloquée (Pattern Day Trader), cooldown 23h.")
                try:
                    self.discord._envoyer(
                        f"⚠️ **PDT BLOQUÉ** — `{ticker}`\n"
                        f"Vente bloquée (equity < $25k hier). Réessai à l'ouverture demain."
                    )
                except Exception:
                    pass

            elif _res_liq is True:
                base["action_executee"] = "VENTE"
                pnl_val = round(pos.get("unrealized_pl", 0), 2)
                trade = {
                    "type": "VENTE", "ticker": ticker,
                    "prix": tech["prix"],
                    "pnl": pnl_val,
                    "pnl_pct": round(pos.get("unrealized_plpc", 0), 2),
                    "sentiment": sent["score"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self.historique_trades.append(trade)
                self.persistance.sauvegarder(trade)
                self._sauvegarder_historique()
                self.discord.notifier_vente(
                    ticker,
                    pnl_val,
                    round(pos.get("unrealized_plpc", 0), 2),
                    dec["raison"]
                )
                self.logger.info(
                    f"[VENTE] {ticker} P&L {pos.get('unrealized_plpc',0):.2f}% | {dec['raison'][:60]}"
                )
                # Feature 4 : enregistrer résultat dans blacklist
                self.blacklist.enregistrer_resultat(ticker, pnl_val, self.discord._envoyer)
                # Feature 5 : IA analyse les erreurs (pertes > 30$)
                if pnl_val < -30:
                    details_trade = {
                        "rsi": tech["indicateurs"].get("rsi"),
                        "score": dec.get("score_fusionne"),
                        "hma_crossover": dec.get("croisement_hma"),
                        "raison_vente": dec.get("raison", ""),
                    }
                    regime_str = getattr(self, '_dernier_regime', "?") or "?"
                    self.discord.notifier_erreur_ia(ticker, pnl_val, details_trade, regime_str)

        # ── Raison en langage simple pour le dashboard ────────────────────
        base["raison_simple"] = self._generer_raison_simple(dec, ticker)

        return base

    # =========================================================================
    # Portefeuille Long Terme — géré par Sacha Pro Strategy (expérimental)
    # =========================================================================
    def _gerer_portefeuille_lt(self, positions: dict, compte: dict,
                               executer: bool = True) -> list:
        """
        Gère le mini-portefeuille long terme Sacha Pro.
        Appelé UNE FOIS par cycle, après la boucle principale (_analyser_actif).

        Logique :
          • Pour chaque ticker dans ACTIFS_ACTIONS (pas crypto — trop volatile LT)
          • Récupère les barres daily → analyse Sacha Pro
          • BUY  : crossover EMA9/EMA21 + conditions + place libre + capital OK → achat
          • SELL : crossunder EMA9/EMA21 → vente (même marché fermé : on prépare)
          • Protège contre double achat (ticker déjà en position CT)

        Retourne la liste des actions LT exécutées (pour dashboard + logs).
        """
        if self.portefeuille_lt is None or not LT_ACTIF:
            return []

        lt_actions = []
        equity = float(compte.get("equity", 10000))
        ticker_norm_map = {t.replace("/", ""): t for t in ACTIFS_ACTIONS}

        for ticker in ACTIFS_ACTIONS:
            try:
                ticker_norm = ticker.replace("/", "")
                en_position_lt = self.portefeuille_lt.est_position_lt(ticker)
                en_position_ct = ticker in positions or ticker_norm in positions

                # Récupère barres daily (déjà en cache depuis _analyser_actif)
                df_daily = self.alpaca.get_barres_daily(ticker)
                sacha = self.strategie_sacha.analyser(ticker, df_daily)

                # ── VENTE LT : crossunder sur daily ─────────────────────────
                if en_position_lt and sacha["ema_crossunder"]:
                    prix_actuel = sacha.get("prix") or 0.0
                    stats = self.portefeuille_lt.fermer_position(
                        ticker, prix_actuel, "EMA crossunder daily"
                    )
                    if executer:
                        self.alpaca.liquider_position(ticker)
                    lt_actions.append({
                        "action": "VENTE_LT",
                        "ticker": ticker,
                        "prix":   prix_actuel,
                        "pnl_pct": stats.get("pnl_pct"),
                        "raison": "🔻 EMA9 crossunder EMA21 (daily) — Sacha Pro SELL",
                        "executer": executer,
                    })
                    self.discord._envoyer(
                        f"📉 **[LT] VENTE** `{ticker}` — EMA crossunder daily | "
                        f"PnL: {stats.get('pnl_pct', '?'):+}% | "
                        f"{'Exécuté' if executer else 'Simulé (marché fermé)'}"
                    )
                    self.logger.info(
                        f"[LT VENTE] {ticker} | crossunder daily | "
                        f"PnL: {stats.get('pnl_pct', '?'):+}% | "
                        f"{'exécuté' if executer else 'simulé'}"
                    )
                    continue

                # ── ACHAT LT : crossover sur daily ──────────────────────────
                # Conditions :
                #  1. Signal BUY Sacha Pro (crossover obligatoire si LT_REQUIRE_CROSSOVER)
                #  2. Pas encore en position LT pour ce ticker
                #  3. Pas en position CT pour ce ticker (évite de doubler l'exposition)
                #  4. Nombre max de positions LT non atteint
                #  5. Marché ouvert (ou simulé si fermé)
                signal_ok = (sacha["ema_crossover"] if LT_REQUIRE_CROSSOVER
                             else sacha["signal"] == "BUY")

                if (signal_ok and
                        not en_position_lt and
                        not en_position_ct and
                        self.portefeuille_lt.nb_positions() < LT_MAX_POSITIONS and
                        not self.pause_drawdown):

                    montant = max(
                        LT_MIN_USD,
                        min(LT_MAX_USD, equity * LT_ALLOC_PCT / 100)
                    )

                    if not self.risque.capital_suffisant(compte, montant):
                        lt_actions.append({
                            "action": "ACHAT_LT_BLOQUE",
                            "ticker": ticker,
                            "raison": f"Capital insuffisant pour LT (besoin ${montant:.0f})",
                        })
                        continue

                    prix_actuel = sacha.get("prix") or 0.0
                    achat_ok = False

                    if executer:
                        try:
                            self.alpaca.soumettre_ordre(ticker, montant, "buy")
                            achat_ok = True
                        except Exception as e:
                            lt_actions.append({
                                "action": "ACHAT_LT_ERREUR",
                                "ticker": ticker,
                                "erreur": str(e),
                            })
                            continue
                    else:
                        # Marché fermé : enregistre quand même la position (price de demain)
                        achat_ok = True

                    if achat_ok:
                        self.portefeuille_lt.ouvrir_position(
                            ticker, prix_actuel, montant, sacha
                        )
                        lt_actions.append({
                            "action":   "ACHAT_LT",
                            "ticker":   ticker,
                            "montant":  montant,
                            "prix":     prix_actuel,
                            "raison":   "🔺 EMA9 crossover EMA21 (daily) — Sacha Pro BUY",
                            "details":  sacha.get("details", ""),
                            "executer": executer,
                        })
                        self.discord._envoyer(
                            f"📈 **[LT] ACHAT** `{ticker}` — EMA crossover daily | "
                            f"${montant:.0f} | RSI={sacha.get('rsi','?')} | "
                            f"{'Exécuté' if executer else 'Simulé (marché fermé)'}\n"
                            f"> {sacha.get('details','')}"
                        )
                        self.logger.info(
                            f"[LT ACHAT] {ticker} | crossover daily | "
                            f"${montant:.0f} @ ${prix_actuel:.2f} | "
                            f"RSI={sacha.get('rsi','?')} | "
                            f"{'exécuté' if executer else 'simulé'}"
                        )

            except Exception as e:
                self.logger.debug(f"[LT] {ticker} erreur: {e}")

        return lt_actions

    # =========================================================================
    # Feature 1 : Pyramiding — renforcer les gagnants
    # =========================================================================

    def _charger_pyramiding(self):
        try:
            if PYRAMIDING_FILE.exists():
                with open(PYRAMIDING_FILE, encoding="utf-8") as f:
                    self._pyramiding_done = json.load(f)
        except Exception:
            self._pyramiding_done = {}

    def _sauvegarder_pyramiding(self):
        try:
            PYRAMIDING_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(PYRAMIDING_FILE, "w", encoding="utf-8") as f:
                json.dump(self._pyramiding_done, f)
        except Exception:
            pass

    def _verifier_pyramiding(self, positions: dict, compte: dict):
        """Renforce les positions gagnantes de +3% (pyramiding)."""
        equity = compte.get("equity", 0)
        for ticker, pos in positions.items():
            try:
                plpc = float(pos.get("unrealized_plpc", 0))
                market_val = float(pos.get("market_value", 0))
                done = self._pyramiding_done.get(ticker, 0)
                if plpc >= PYRAMIDING_SEUIL_PCT and done < PYRAMIDING_MAX_FOIS:
                    montant = round(market_val * PYRAMIDING_MULT, 2)
                    montant = min(montant, equity * 0.03)  # max 3% equity
                    if montant >= 50:
                        self.alpaca.soumettre_ordre(ticker, montant, "buy")
                        self._pyramiding_done[ticker] = done + 1
                        self._sauvegarder_pyramiding()
                        self.discord._envoyer(
                            f"🔺 **PYRAMIDING** `{ticker}` | +{plpc:.1f}% gain\n"
                            f"Renforcement ${montant:.0f} (×{PYRAMIDING_MULT*100:.0f}% position)\n"
                            f"Laisser courir les gagnants 📈"
                        )
                        self.logger.info(f"[PYRAMIDING] {ticker} +{plpc:.1f}% → renforcement ${montant:.0f}")
            except Exception as e:
                self.logger.debug(f"Pyramiding {ticker}: {e}")

    # =========================================================================
    # Feature 2 : Paires de trading — détection divergences
    # =========================================================================

    def _analyser_paires(self, decisions: dict) -> list:
        """Détecte les divergences dans les paires corrélées."""
        signaux = []
        for ticker_a, ticker_b in PAIRES_TRADING:
            dec_a = decisions.get(ticker_a, {})
            dec_b = decisions.get(ticker_b, {})
            score_a = dec_a.get("score_technique", 50)
            score_b = dec_b.get("score_technique", 50)
            diff = abs(score_a - score_b)
            if diff >= 15:  # divergence significative
                fort = ticker_a if score_a > score_b else ticker_b
                faible = ticker_b if score_a > score_b else ticker_a
                signaux.append({
                    "paire": f"{ticker_a}/{ticker_b}",
                    "fort": fort,
                    "faible": faible,
                    "divergence": round(diff, 1),
                    "signal": f"📊 {fort} fort (score {max(score_a,score_b):.0f}) vs {faible} faible (score {min(score_a,score_b):.0f}) — divergence paire"
                })
                # Alerte Discord si divergence > 20 pts, max 1x/heure par paire
                if diff > 20:
                    paire_key = f"{ticker_a}/{ticker_b}"
                    derniere = self._derniere_alerte_paire.get(paire_key, 0)
                    if time.time() - derniere > 3600:
                        self._derniere_alerte_paire[paire_key] = time.time()
                        self.discord._envoyer(
                            f"📊 **DIVERGENCE PAIRE** `{ticker_a}/{ticker_b}`\n"
                            f"Fort: `{fort}` (score {max(score_a,score_b):.0f}) | Faible: `{faible}` (score {min(score_a,score_b):.0f})\n"
                            f"Divergence: {diff:.0f} pts 📈"
                        )
        return signaux

    # =========================================================================
    # Feature 6 : Hedging automatique — GLD
    # =========================================================================

    def _verifier_hedge(self, positions: dict, compte: dict, regime: str):
        """Achète automatiquement GLD si trop de positions + marché incertain."""
        try:
            nb_pos = len(positions)
            equity = compte.get("equity", 0)
            has_hedge = "GLD" in positions

            if nb_pos >= HEDGE_NB_POSITIONS_SEUIL and regime in ("BAISSIER", "LATÉRAL") and not has_hedge:
                montant = round(equity * HEDGE_ALLOC_PCT / 100, 2)
                montant = max(50, min(montant, 500))
                self.alpaca.soumettre_ordre("GLD", montant, "buy")
                self.discord._envoyer(
                    f"🛡️ **HEDGE AUTO** — Achat GLD (or) ${montant:.0f}\n"
                    f"Raison: {nb_pos} positions ouvertes + marché {regime}\n"
                    f"Protection automatique du portefeuille activée"
                )
                self.logger.info(f"[HEDGE] GLD ${montant:.0f} — {nb_pos} positions + {regime}")
            elif has_hedge and regime == "HAUSSIER" and nb_pos < 4:
                # Marché redevenu bon → liquider le hedge
                self.alpaca.liquider_position("GLD")
                self.discord._envoyer(f"✅ **HEDGE** GLD vendu — marché {regime}, risque réduit")
        except Exception as e:
            self.logger.debug(f"Hedge: {e}")

    # =========================================================================
    # Feature 7 : Détection de krach — mode bunker
    # =========================================================================

    def _verifier_mode_bunker(self, positions: dict) -> bool:
        """Vérifie si mode bunker actif. Retourne True si trading bloqué."""
        try:
            # Vérifier si bunker déjà actif
            if BUNKER_FILE.exists():
                with open(BUNKER_FILE) as f:
                    data = json.load(f)
                if time.time() < data.get("actif_jusqu", 0):
                    heures_restantes = (data["actif_jusqu"] - time.time()) / 3600
                    self.logger.info(f"🏰 MODE BUNKER actif — encore {heures_restantes:.1f}h")
                    return True

            # Vérifier chute SPY sur la dernière heure
            df_spy = self.alpaca.get_barres("SPY")
            if df_spy is None or df_spy.empty or len(df_spy) < 2:
                return False

            prix_actuel = float(df_spy["close"].iloc[-1])
            prix_il_y_a_1h = float(df_spy["close"].iloc[-2])
            chute_pct = (prix_actuel - prix_il_y_a_1h) / prix_il_y_a_1h * 100

            if chute_pct <= -KRACH_SEUIL_PCT:
                # KRACH DÉTECTÉ
                self.logger.error(f"🚨 KRACH DÉTECTÉ — SPY {chute_pct:.2f}% en 1h → MODE BUNKER")

                # Sauvegarder bunker
                expiry = time.time() + KRACH_PAUSE_H * 3600
                with open(BUNKER_FILE, "w") as f:
                    json.dump({"actif_jusqu": expiry, "declanche": datetime.now().isoformat(), "chute_spy": chute_pct}, f)

                # Vendre 50% des positions (les plus risquées en premier)
                tickers_risques = [t for t in positions if t in MEME_STOCKS or "/" in t]
                tickers_normaux = [t for t in positions if t not in tickers_risques]
                tickers_a_vendre = tickers_risques + tickers_normaux
                nb_a_vendre = max(1, int(len(tickers_a_vendre) * KRACH_VENTE_PCT))

                vendus = []
                for ticker in tickers_a_vendre[:nb_a_vendre]:
                    try:
                        self.alpaca.liquider_position(ticker)
                        vendus.append(ticker)
                    except Exception as e:
                        self.logger.error(f"Vente bunker {ticker}: {e}")

                self.discord._envoyer(
                    f"🚨 **MODE BUNKER ACTIVÉ** 🏰\n"
                    f"SPY a chuté de {abs(chute_pct):.1f}% en 1h\n"
                    f"Positions vendues ({len(vendus)}/{len(tickers_a_vendre)}) : {', '.join(vendus)}\n"
                    f"Trading suspendu pendant {KRACH_PAUSE_H}h\n"
                    f"📅 Reprise: {datetime.fromtimestamp(expiry).strftime('%d/%m à %Hh%M')}"
                )
                return True
            return False
        except Exception as e:
            self.logger.warning(f"Mode bunker: {e}")
            return False

    # =========================================================================
    # Feature 8 : Protection weekend — vendredi 15h30
    # =========================================================================

    def _protection_weekend(self, positions: dict):
        """Vendredi 15h30-15h45 : vendre les positions risquées avant le weekend."""
        try:
            now_et = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-4)))
            # Vendredi entre 15h30 et 15h45
            if now_et.weekday() != 4 or not (now_et.hour == 15 and 30 <= now_et.minute <= 45):
                return

            # Éviter de répéter plusieurs fois dans les 15 min
            key = now_et.strftime("%Y-%m-%d")
            if getattr(self, '_weekend_protection_done', None) == key:
                return
            self._weekend_protection_done = key

            tickers_risques = []
            for ticker, pos in positions.items():
                plpc = float(pos.get("unrealized_plpc", 0))
                est_risque = (
                    ticker in MEME_STOCKS or
                    "/" in ticker or  # crypto
                    ticker in {"RIVN", "LCID", "NIO", "SPCE", "NKLA"}
                )
                if est_risque and plpc < -0.5:  # En perte + risqué → vendre
                    tickers_risques.append((ticker, plpc))

            vendus = []
            for ticker, plpc in tickers_risques:
                try:
                    self.alpaca.liquider_position(ticker)
                    vendus.append(f"{ticker} ({plpc:+.1f}%)")
                except Exception as e:
                    self.logger.error(f"Weekend protection {ticker}: {e}")

            if vendus:
                self.discord._envoyer(
                    f"🌙 **PROTECTION WEEKEND** — Vendredi 15h30\n"
                    f"Positions risquées vendues : {', '.join(vendus)}\n"
                    f"Raison: weekend = impossible de réagir aux mauvaises nouvelles"
                )
                self.logger.info(f"[WEEKEND] Vendus: {vendus}")
        except Exception as e:
            self.logger.debug(f"Protection weekend: {e}")

    # =========================================================================
    # Feature 11 : Journal hebdomadaire Discord
    # =========================================================================

    def _journal_hebdomadaire(self):
        """Envoie un journal de trading hebdomadaire sur Discord chaque lundi matin."""
        try:
            now_et = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-4)))
            # Lundi entre 9h00 et 9h10
            if now_et.weekday() != 0 or now_et.hour != 9 or now_et.minute > 10:
                return
            key = now_et.strftime("%Y-W%W")
            if getattr(self, '_dernier_journal', None) == key:
                return
            self._dernier_journal = key

            # Charger les trades de la semaine passée
            trades_semaine = []
            if HISTORIQUE_FILE.exists():
                with open(HISTORIQUE_FILE, encoding="utf-8") as f:
                    tous = json.load(f)
                cutoff = (now_et - timedelta(days=7)).timestamp() * 1000
                for t in tous:
                    ts = t.get("timestamp", "")
                    try:
                        t_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if t_dt.timestamp() * 1000 > cutoff and t.get("type") in ("VENTE", "VENTE_AUTO"):
                            trades_semaine.append(t)
                    except Exception:
                        pass

            if not trades_semaine:
                self.discord._envoyer("📔 **Journal hebdo** — Aucun trade fermé cette semaine.")
                return

            # Stats simples
            pnls = [float(t.get("pnl", 0)) for t in trades_semaine]
            total_pnl = sum(pnls)
            gagnants = [p for p in pnls if p > 0]
            perdants = [p for p in pnls if p < 0]

            # Appel Groq pour l'analyse narrative
            resume_trades = "\n".join([
                f"- {t.get('ticker','?')}: {float(t.get('pnl',0)):+.2f}$ ({t.get('raison','?')[:40]})"
                for t in trades_semaine[:15]
            ])

            analyse = "Analyse IA indisponible."
            if GROQ_API_KEY:
                prompt = f"""Analyse cette semaine de trading en 4-5 phrases en français simple.

Trades fermés:
{resume_trades}

PnL total: {total_pnl:+.2f}$
Trades gagnants: {len(gagnants)} | Perdants: {len(perdants)}

Donne: 1) Ce qui a marché 2) Ce qui a échoué 3) Une recommandation pour la semaine prochaine.
Sois direct, pratique, sans jargon technique."""

                try:
                    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
                    payload = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}],
                              "max_tokens": 250, "temperature": 0.4}
                    resp = requests.post("https://api.groq.com/openai/v1/chat/completions",
                                       headers=headers, json=payload, timeout=15)
                    if resp.status_code == 200:
                        analyse = resp.json()["choices"][0]["message"]["content"].strip()
                except Exception:
                    analyse = "Analyse IA indisponible."

            emoji_pnl = "📈" if total_pnl >= 0 else "📉"
            self.discord._envoyer(
                f"📔 **JOURNAL HEBDOMADAIRE** — Semaine {now_et.strftime('%d/%m/%Y')}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{emoji_pnl} PnL semaine: **{total_pnl:+.2f}$**\n"
                f"✅ Gagnants: {len(gagnants)} | ❌ Perdants: {len(perdants)}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🧠 **Analyse IA:**\n{analyse}"
            )
            self.logger.info(f"[JOURNAL] Semaine envoyée sur Discord — PnL: {total_pnl:+.2f}$")
        except Exception as e:
            self.logger.warning(f"Journal hebdo: {e}")

    # =========================================================================
    # Feature F : Kill Switch — pertes consécutives
    # =========================================================================

    def _verifier_kill_switch_pertes(self) -> bool:
        """Pause trading si trop de pertes consécutives. Retourne True si bloqué."""
        try:
            # Vérifier si kill switch déjà actif
            if KILL_FILE.exists():
                with open(KILL_FILE) as f:
                    data = json.load(f)
                if time.time() < data.get("actif_jusqu", 0):
                    return True

            # Vérifier le nb de pertes consécutives
            nb_pertes = self.blacklist.get_pertes_consecutives_global()
            if nb_pertes >= KILL_MAX_PERTES_CONSECUTIVES:
                expiry = time.time() + KILL_PAUSE_PERTES_H * 3600
                KILL_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(KILL_FILE, "w") as f:
                    json.dump({"actif_jusqu": expiry, "raison": f"{nb_pertes} pertes consécutives"}, f)
                self.discord._envoyer(
                    f"🛑 **KILL SWITCH** — {nb_pertes} pertes consécutives détectées\n"
                    f"Trading suspendu {KILL_PAUSE_PERTES_H}h pour protéger le capital\n"
                    f"Reprise: {datetime.fromtimestamp(expiry).strftime('%d/%m à %Hh%M')}"
                )
                self.logger.error(f"[KILL SWITCH] {nb_pertes} pertes consécutives → pause {KILL_PAUSE_PERTES_H}h")
                return True
            return False
        except Exception as e:
            self.logger.debug(f"Kill switch pertes: {e}")
            return False

    # =========================================================================
    # Feature E : Time Stop — fermer les positions "mortes"
    # =========================================================================

    def _verifier_time_stop(self, positions: dict):
        """Ferme les positions ouvertes depuis trop longtemps sans gain."""
        try:
            ts_file = TIME_STOP_FILE
            ouvertures = {}
            if ts_file.exists():
                with open(ts_file, encoding="utf-8") as f:
                    ouvertures = json.load(f)

            now = time.time()
            changed = False
            a_fermer = []

            for ticker, pos in positions.items():
                plpc = float(pos.get("unrealized_plpc", 0))
                # Enregistrer l'heure d'ouverture si pas encore fait
                if ticker not in ouvertures:
                    ouvertures[ticker] = now
                    changed = True
                    continue

                age_heures = (now - ouvertures[ticker]) / 3600

                # Si position ouverte > TIME_STOP_HEURES et gain < 1% → fermer
                if age_heures > TIME_STOP_HEURES and plpc < 0.01:
                    a_fermer.append((ticker, age_heures, plpc * 100))

            # Supprimer les tickers qui ne sont plus en position
            for ticker in list(ouvertures.keys()):
                if ticker not in positions:
                    del ouvertures[ticker]
                    changed = True

            if changed or a_fermer:
                ts_file.parent.mkdir(parents=True, exist_ok=True)
                with open(ts_file, "w", encoding="utf-8") as f:
                    json.dump(ouvertures, f)

            # Fermer les positions time-stopped
            for ticker, age_h, plpc in a_fermer:
                try:
                    self.alpaca.liquider_position(ticker)
                    if ticker in ouvertures:
                        del ouvertures[ticker]
                    ts_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(ts_file, "w", encoding="utf-8") as f:
                        json.dump(ouvertures, f)
                    self.discord._envoyer(
                        f"⏰ **TIME STOP** `{ticker}` — Position fermée\n"
                        f"Ouverte depuis {age_h:.0f}h | Gain: {plpc:+.1f}%\n"
                        f"Position 'morte' éliminée pour libérer du capital"
                    )
                    self.logger.info(f"[TIME STOP] {ticker} fermé après {age_h:.0f}h | {plpc:+.1f}%")
                except Exception as e:
                    self.logger.error(f"Time stop {ticker}: {e}")
        except Exception as e:
            self.logger.warning(f"Time stop: {e}")

    # =========================================================================
    # Feature G : Kelly Fraction Dynamique (historique JSON)
    # =========================================================================

    def _calculer_kelly_historique(self) -> float:
        """
        Calcule la fraction Kelly basée sur les 30 derniers trades fermés.
        Retourne la taille en $ à investir (entre ALLOCATION_FAIBLE et ALLOCATION_EXCELLENCE).
        """
        try:
            if not HISTORIQUE_FILE.exists():
                return ALLOCATION_BASE

            with open(HISTORIQUE_FILE, encoding="utf-8") as f:
                tous = json.load(f)

            # Prendre les 30 derniers trades fermés
            ventes = [t for t in tous if t.get("type") in ("VENTE", "VENTE_AUTO")][-30:]

            if len(ventes) < 10:
                return ALLOCATION_BASE  # Pas assez de données

            pnls = [float(t.get("pnl_pct", 0)) for t in ventes]
            gagnants = [p for p in pnls if p > 0]
            perdants = [abs(p) for p in pnls if p < 0]

            if not gagnants or not perdants:
                return ALLOCATION_BASE

            win_prob = len(gagnants) / len(pnls)
            avg_win  = sum(gagnants) / len(gagnants) / 100  # En fraction
            avg_loss = sum(perdants) / len(perdants) / 100  # En fraction

            # Kelly = (p*b - q) / b
            b = avg_win / (avg_loss + 1e-10)
            q = 1.0 - win_prob
            f_star = (win_prob * b - q) / (b + 1e-10)
            f_star = max(0.0, min(f_star, 0.25))  # Plafonner à 25% Kelly

            # Fraction conservatrice = 25% du Kelly théorique
            kelly_fraction = f_star * 0.25

            if kelly_fraction >= 0.08:
                return ALLOCATION_EXCELLENCE
            elif kelly_fraction >= 0.05:
                return ALLOCATION_FORTE
            elif kelly_fraction >= 0.03:
                return ALLOCATION_BASE
            else:
                return ALLOCATION_FAIBLE
        except Exception as e:
            self.logger.debug(f"Kelly historique: {e}")
            return ALLOCATION_BASE

    def run_cycle(self) -> dict:
        self.cycle += 1
        self.logger.info(f"══ Cycle #{self.cycle} — {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC ══")

        # ── Santé bot — alerte si JSON vieux > 15 min ──────────────────────
        self._verifier_sante_bot()

        self._achats_ce_cycle = 0      # reset compteur achats actions par cycle
        self._achats_crypto_cycle = 0  # reset compteur achats crypto par cycle
        marche_ouvert = self.alpaca.marche_ouvert()

        # ── Fear & Greed (cache 1h) ─────────────────────────────────────────
        fg = self.fear_greed.get()
        self._fear_greed_value = fg.get("value", 50)

        # ── Calcul timing marché ────────────────────────────────────────────
        try:
            if marche_ouvert:
                clock = self.alpaca.api.get_clock()
                next_close = clock.next_close
                if hasattr(next_close, 'tzinfo') and next_close.tzinfo is None:
                    next_close = next_close.replace(tzinfo=timezone.utc)
                else:
                    next_close = next_close.astimezone(timezone.utc)
                market_open_today = next_close - timedelta(hours=6, minutes=30)
                now_utc = datetime.now(timezone.utc)
                self._mins_depuis_open  = max(0, (now_utc - market_open_today).total_seconds() / 60)
                self._mins_avant_close  = max(0, (next_close - now_utc).total_seconds() / 60)
            else:
                self._mins_depuis_open  = 999.0
                self._mins_avant_close  = 999.0
        except Exception:
            self._mins_depuis_open  = 999.0
            self._mins_avant_close  = 999.0
        if marche_ouvert:
            self.logger.info("🟢 Marché OUVERT — mode trading actif")
        else:
            self.logger.info("🔴 Marché FERMÉ — mode analyse/préparation uniquement")

        try:
            compte    = self.alpaca.get_compte()
            positions = self.alpaca.get_positions()
        except Exception as e:
            self.logger.error(f"Erreur Alpaca SDK: {e}")
            return {"meta": {"cycle": self.cycle, "erreur": str(e)},
                    "portefeuille": {}, "decisions_cycle": []}

        # Feature 7 : Mode Bunker — vérifier EN DÉBUT avant tout trading
        if self._verifier_mode_bunker(positions):
            return {"meta": {"cycle": self.cycle, "timestamp": datetime.now(timezone.utc).isoformat(),
                             "mode_bunker": True, "marche_ouvert": marche_ouvert},
                    "portefeuille": {"equity": compte.get("equity", 0)}, "decisions_cycle": []}

        # Feature F : Kill Switch — pertes consécutives (après bunker, avant tout trading)
        if self._verifier_kill_switch_pertes():
            self.logger.warning("Kill switch pertes consécutives actif — cycle ignoré")
            return {"meta": {"cycle": self.cycle, "timestamp": datetime.now(timezone.utc).isoformat(),
                             "kill_switch_pertes": True, "marche_ouvert": marche_ouvert},
                    "portefeuille": {"equity": compte.get("equity", 0)}, "decisions_cycle": []}

        # ── Audit stops : TOUJOURS actif (crypto 24/7, actions si marché ouvert) ──
        self._auditer_positions(positions, marche_ouvert=marche_ouvert)

        # Données SPY pour score momentum
        self.df_spy = self.alpaca.get_barres("SPY")
        # Données QQQ pour détection régime
        self.df_qqq = self.alpaca.get_barres("QQQ")
        # ── Pré-chargement parallèle des barres (8 threads, Groq reste séquentiel) ──
        actifs_prefetch = (
            list(dict.fromkeys(ACTIFS_CRYPTO_NUIT + ACTIFS_TOUS))
            if not marche_ouvert else ACTIFS_TOUS
        )
        self._prefetch_barres_parallele(actifs_prefetch)
        # Régime de marché
        regime_marche = self.regime_det.detecter(self.df_spy, self.df_qqq)

        # ── Notification Discord si changement de régime ──────────────────
        if self._dernier_regime and regime_marche.get('regime') != self._dernier_regime:
            emoji = {"HAUSSIER": "🟢", "BAISSIER": "🔴", "LATÉRAL": "🟡"}.get(
                regime_marche.get('regime', ''), '⚪')
            self.discord._envoyer(
                f"{emoji} **CHANGEMENT RÉGIME MARCHÉ**\n"
                f"{self._dernier_regime} → {regime_marche.get('regime', '?')}\n"
                f"{regime_marche.get('description', '')}"
            )
        self._dernier_regime = regime_marche.get('regime')

        # Feature 9 : Météo économique mondiale
        meteo = self.meteo.analyser(self.alpaca)
        if meteo["signal"] == "RISQUE_ELEVE":
            self.logger.info(f"🌩️ Météo éco: RISQUE ÉLEVÉ ({meteo.get('details', [])})")

        self.pause_drawdown = self.risque.verifier_drawdown(compte["equity"])
        # Alerte Discord préventive à 3% de drawdown (avant la pause à 5%)
        dd_pct = max(0.0, (self.risque.capital_initial - compte["equity"]) / self.risque.capital_initial * 100)
        if 3.0 <= dd_pct < 5.0:
            self.discord._envoyer(
                f"⚠️ **ROUTE/v4 — Alerte drawdown** : {dd_pct:.2f}% "
                f"(limite pause : 5%) | Equity : ${compte['equity']:,.2f}"
            )

        # ── Circuit Breaker journalier ──────────────────────────────────────────
        circuit_ouvert, pnl_jour = self._verifier_circuit_breaker()
        self._circuit_breaker_actif = circuit_ouvert

        # ── Portfolio Heat ──────────────────────────────────────────────────────
        self._portfolio_heat = self.risque.calculer_portfolio_heat(positions, compte["equity"])
        if self._portfolio_heat >= MAX_HEAT_PCT:
            self.logger.warning(f"🌡️  Portfolio heat {self._portfolio_heat:.1f}% ≥ {MAX_HEAT_PCT:.0f}%")

        # ── Kelly Criterion (une fois par cycle) ────────────────────────────────
        kelly_base = None
        try:
            with sqlite3.connect(GestionnairePersistance.DB_PATH) as _conn:
                kelly_base = self.risque.calculer_kelly_base(_conn)
            self.logger.info(f"💰 Kelly base: ${kelly_base:.0f} (standard: ${ALLOCATION_BASE:.0f})")
        except Exception:
            pass

        # Feature G : Kelly Historique (complète le Kelly SQLite avec données JSON)
        try:
            self._kelly_base = self._calculer_kelly_historique()
            if kelly_base is None:
                kelly_base = self._kelly_base
            self.logger.info(f"💰 Kelly historique: ${self._kelly_base:.0f}")
        except Exception:
            pass

        # ── Exécution : crypto toujours active (24/7), actions selon marché ────
        executer_actions = marche_ouvert and not circuit_ouvert
        executer_crypto  = not circuit_ouvert   # Crypto s'exécute TOUJOURS (24/7)
        mode_crypto_nuit = not marche_ouvert    # True = marché fermé → focus crypto

        # ── Construction de la liste de tickers à analyser ce cycle ─────────
        if mode_crypto_nuit:
            # Marché fermé → 50% crypto / 50% actions prioritaires
            # Sélectionner les NB_ACTIONS_NUIT meilleures actions par score précédent
            dernieres_decisions = getattr(self, '_dernieres_decisions', {})
            actions_scorees = sorted(
                ACTIFS_ACTIONS,
                key=lambda t: dernieres_decisions.get(t, {}).get("score_technique", 50),
                reverse=True
            )
            # Actions à scanner cette nuit : top NB_ACTIONS_NUIT + positions ouvertes
            actions_ouvertes = [t for t in positions if "/" not in t]
            actions_nuit = list(dict.fromkeys(
                actions_ouvertes + actions_scorees[:NB_ACTIONS_NUIT]
            ))[:NB_ACTIONS_NUIT]

            # Ordre : TOUTES les cryptos étendues EN PREMIER, puis les actions sélectionnées
            tickers_ordonnes = ACTIFS_CRYPTO_NUIT + actions_nuit
            self.logger.info(
                f"🌙 Mode nuit — {len(ACTIFS_CRYPTO_NUIT)} cryptos + "
                f"{len(actions_nuit)} actions = {len(tickers_ordonnes)} tickers (50/50)"
            )
        else:
            # Marché ouvert → ordre normal (actions + crypto)
            tickers_ordonnes = ACTIFS_TOUS

        decisions = []
        for ticker in tickers_ordonnes:
            time.sleep(0.02)
            is_crypto = "/" in ticker
            executer  = executer_crypto if is_crypto else executer_actions
            d = self._analyser_actif(
                ticker, positions, compte,
                executer=executer,
                kelly_base=kelly_base,
                mode_crypto_nuit=(mode_crypto_nuit and is_crypto),
            )
            decisions.append(d)

        # Mémoriser les scores pour la prochaine sélection d'actions nuit
        self._dernieres_decisions = {d["ticker"]: d for d in decisions}

        # Feature E : Time Stop — fermer les positions trop longtemps sans gain
        try:
            self._verifier_time_stop(positions)
        except Exception as e:
            self.logger.debug(f"Time stop cycle: {e}")

        # Feature D : Break-Even Stop — déplacer stop au prix d'entrée après +1 ATR
        try:
            positions_be = self.alpaca.get_positions()
            for ticker_be, pos_be in positions_be.items():
                try:
                    prix_be = float(pos_be.get("current_price", pos_be.get("avg_entry_price", 0)))
                    if self.risque.verifier_breakeven(ticker_be, pos_be, prix_be):
                        self.discord._envoyer(
                            f"🔒 **BREAK-EVEN** `{ticker_be}` — Stop déplacé au prix d'entrée\n"
                            f"Risque de perte éliminé — position protégée"
                        )
                except Exception as e:
                    self.logger.debug(f"Breakeven check {ticker_be}: {e}")
        except Exception as e:
            self.logger.debug(f"Breakeven cycle: {e}")

        # Feature 10 : Suivi initiés SEC — une fois par cycle
        try:
            self._insiders_data = self.insiders.get_achats_recents(ACTIFS_ACTIONS[:15])
        except Exception:
            self._insiders_data = {}

        # Feature 2 : Analyser les paires de trading
        decisions_dict = {d["ticker"]: d for d in decisions}
        paires_divergence = self._analyser_paires(decisions_dict)

        # Feature 1 : Pyramiding — seulement si pas en pause et pas circuit breaker
        if not self._circuit_breaker_actif and not self.pause_drawdown:
            try:
                positions_fresh = self.alpaca.get_positions()
                # Nettoyer _pyramiding_done des tickers qui ne sont plus en position
                self._pyramiding_done = {
                    t: v for t, v in self._pyramiding_done.items()
                    if t in positions_fresh
                }
                self._verifier_pyramiding(positions_fresh, compte)
            except Exception as e:
                self.logger.debug(f"Pyramiding cycle: {e}")

        # Feature 6 : Hedge automatique — une fois par heure
        if time.time() - self._dernier_hedge_check > 3600:
            self._dernier_hedge_check = time.time()
            self._verifier_hedge(positions, compte, regime_marche.get("regime", "LATÉRAL"))

        # Feature 8 : Protection weekend
        self._protection_weekend(positions)

        # Feature 11 : Journal hebdomadaire
        self._journal_hebdomadaire()

        # ── Portefeuille Long Terme — Sacha Pro (expérimental) ───────────────
        lt_actions = []
        if LT_ACTIF and self.portefeuille_lt is not None:
            try:
                lt_actions = self._gerer_portefeuille_lt(positions, compte, executer)
                if lt_actions:
                    self.logger.info(
                        f"[LT] {len(lt_actions)} action(s) LT ce cycle : "
                        f"{[a['action'] + '/' + a['ticker'] for a in lt_actions]}"
                    )
            except Exception as e:
                self.logger.error(f"Erreur gestion LT: {e}")

        # ── Shadow Portfolio — simulation sans capital réel ───────────────────
        for d in decisions:
            ticker = d["ticker"]
            prix   = d.get("prix") or 0
            if not prix:
                continue
            self.shadow.maj_prix(ticker, prix)
            # Simuler achat virtuel si signal fort non exécuté (marché fermé ou capital insuffisant)
            if (d.get("signal_technique") == "BUY" and
                    d.get("score_technique", 0) >= SEUIL_CONVICTION_FAIBLE and
                    d.get("action_executee") in ("AUCUNE", "PRÉPARE") and
                    ticker not in self.shadow.positions):
                self.shadow.simuler_achat(ticker, prix, d.get("score_technique", 0), d.get("raison", ""))
            # Simuler vente virtuelle sur signal SELL
            elif d.get("signal_technique") == "SELL" and ticker in self.shadow.positions:
                self.shadow.simuler_vente(ticker, prix, "Signal SELL")
        self.shadow.sauvegarder()

        # Apprentissage passif des patterns
        self._apprentissage_patterns(decisions, marche_ouvert)

        # ── Routines d'entraînement nocturnes ────────────────────────────────
        if not marche_ouvert:
            now_utc = datetime.now(timezone.utc)
            heure   = now_utc.hour
            minute  = now_utc.minute
            wday    = now_utc.weekday()   # 0=Lun … 5=Sam, 6=Dim

            # Optimisation des poids — toutes les heures la nuit (UTC 20h–12h)
            if (heure >= 20 or heure < 12) and self._peut_executer_routine("poids_appris.json", 1.0):
                self._optimiser_poids()

            # Mini-backtest — une fois par nuit à 3h UTC (max 1× / 23h)
            if heure == 3 and self._peut_executer_routine("backtest_results.json", 23.0):
                self._mini_backtest()

            # Pré-chauffe Groq — 30 min avant NYSE open (13:00–13:30 UTC = 09:00–09:30 NY)
            if heure == 13 and minute < 30 and self._peut_executer_routine("groq_warmup.json", 6.0):
                self._prechauffer_groq()

            # Corrélations dynamiques — weekend uniquement, max 1× / 12h
            if wday >= 5 and self._peut_executer_routine("correlations.json", 12.0):
                self._mettre_a_jour_correlations()

            # Rotation watchlist — toutes les 3h la nuit (~75 candidats/nuit)
            if self._peut_executer_routine("watchlist_dynamique.json", 3.0):
                self._rotation_watchlist(positions)

            # Monte Carlo — une fois par nuit à 2h UTC (max 1×/23h)
            if heure == 2 and self._peut_executer_routine("monte_carlo.json", 23.0):
                self._monte_carlo()

        # Veille Reddit 24/7 (cache 30 min)
        veille_reddit = self.reddit.analyser(ACTIFS_TOUS + SP500_CANDIDATS[:20])

        self.historique_valeur.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "equity": compte["equity"], "cash": compte["cash"],
        })
        self.historique_valeur = self.historique_valeur[-100:]
        self.historique_trades = self.historique_trades[-500:]  # Garde 500 trades max
        self._sauvegarder_historique()

        try:
            positions_apres = self.alpaca.get_positions()
        except Exception:
            positions_apres = positions

        pnl     = compte["equity"] - self.risque.capital_initial
        pnl_pct = pnl / self.risque.capital_initial * 100

        achats  = sum(1 for d in decisions if d["action_executee"] == "ACHAT")
        ventes  = sum(1 for d in decisions if d["action_executee"] == "VENTE")
        hma_cross = sum(1 for d in decisions if d.get("croisement_hma"))

        self.logger.info(
            f"Cycle #{self.cycle} | ${compte['equity']:,.2f} | "
            f"P&L {pnl:+,.2f}$ ({pnl_pct:+.2f}%) | "
            f"Pos {len(positions_apres)} | "
            f"Achats {achats} Ventes {ventes} | HMA cross {hma_cross}"
        )

        # Résumé Discord en fin de cycle (seulement si trades)
        if achats > 0 or ventes > 0:
            self.discord.notifier_resume(
                compte["equity"], pnl, pnl_pct,
                len(positions_apres), achats, ventes
            )

        # ── Résumé quotidien Discord à 16:05 ET (une seule fois par jour) ──
        now_et = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-4)))
        if now_et.hour == 16 and now_et.minute == 5:
            today_key = now_et.date().isoformat()
            last_daily = getattr(self, '_dernier_resume_quotidien', None)
            if last_daily != today_key:
                self._dernier_resume_quotidien = today_key
                nb_pos     = len(positions_apres)
                gagnants   = [t for t, p in positions_apres.items() if float(p.get("unrealized_pl", 0)) > 0]
                perdants   = [t for t, p in positions_apres.items() if float(p.get("unrealized_pl", 0)) < 0]
                meilleur   = (max(positions_apres.items(),
                                  key=lambda x: float(x[1].get("unrealized_plpc", 0)),
                                  default=(None, None)) if positions_apres else (None, None))
                pire       = (min(positions_apres.items(),
                                  key=lambda x: float(x[1].get("unrealized_plpc", 0)),
                                  default=(None, None)) if positions_apres else (None, None))
                msg = (
                    f"📊 **RÉSUMÉ QUOTIDIEN — {today_key}**\n"
                    f"💰 Capital: ${compte['equity']:,.0f} | PnL jour: ${pnl_jour:+.2f}\n"
                    f"📁 Positions: {nb_pos} ({len(gagnants)} ✅ | {len(perdants)} ❌)\n"
                )
                if meilleur[0]:
                    msg += f"🏆 Meilleur: {meilleur[0]} {float(meilleur[1].get('unrealized_plpc', 0))*100:+.1f}%\n"
                if pire[0]:
                    msg += f"⚠️ Pire: {pire[0]} {float(pire[1].get('unrealized_plpc', 0))*100:+.1f}%\n"
                msg += f"📈 Régime marché: {regime_marche.get('regime', '?')}"
                self.discord._envoyer(msg)

        # Métriques de performance depuis SQLite
        metriques = self.persistance.calculer_metriques()

        # Stress test portefeuille
        stress_test = self._stress_test(positions_apres, compte["equity"])

        # Feature F : état kill switch pour export JSON
        _kill_switch_actif = False
        try:
            if KILL_FILE.exists():
                _ks_data = json.loads(KILL_FILE.read_text())
                _kill_switch_actif = time.time() < _ks_data.get("actif_jusqu", 0)
        except Exception:
            pass

        # Trades récents depuis SQLite (plus fiable que la liste mémoire)
        trades_recents = self.persistance.charger_recents(500)

        return {
            "meta": {
                "cycle":           self.cycle,
                "timestamp":       datetime.now(timezone.utc).isoformat(),
                "bot_version":     "4.1.0-route",
                "broker":          "Alpaca Trade API v2",
                "mode":            "PAPER",
                "sentiment_engine":"Groq Cloud llama-3.3-70b",
                "pause_drawdown":  self.pause_drawdown,
                "marche_ouvert":   marche_ouvert,
                "mode_temporel":   self._mode_temporel(),
            },
            "portefeuille": {
                "capital_initial":   self.risque.capital_initial,
                "equity":            round(compte["equity"], 2),
                "cash":              round(compte["cash"], 2),
                "buying_power":      round(compte["buying_power"], 2),
                "pnl_total":         round(pnl, 2),
                "pnl_total_pct":     round(pnl_pct, 2),
                "positions":         positions_apres,
                "nb_positions":      len(positions_apres),
                "historique_trades": trades_recents[-60:],       # 60 derniers pour dashboard
                "historique_trades_complet": len(trades_recents),
                "historique_valeur": self.historique_valeur,
            },
            "decisions_cycle": decisions,
            "shadow_portfolio":  self.shadow.export_dashboard(),
            "scanner_signaux":   sorted(
                [{"ticker": d["ticker"], "score": d["score_technique"],
                  "signal": d["signal_technique"], "conviction": d.get("conviction",""),
                  "prix": d.get("prix"), "rsi": d.get("rsi"),
                  "croisement_hma": d.get("croisement_hma", False)}
                 for d in decisions if d.get("signal_technique") in ("BUY","SELL")],
                key=lambda x: x["score"], reverse=True
            )[:15],
            "regime_marche":     regime_marche,
            "veille_reddit":     veille_reddit,
            "stress_test":       stress_test,
            "circuit_breaker": {
                "actif":    circuit_ouvert,
                "pnl_jour": pnl_jour,
                "seuil_pct": MAX_DAILY_LOSS_PCT,
            },
            "portfolio_heat": {
                "heat_pct":  self._portfolio_heat,
                "max_pct":   MAX_HEAT_PCT,
                "bloque":    self._portfolio_heat >= MAX_HEAT_PCT,
            },
            "kelly": {
                "base_usd":    kelly_base or ALLOCATION_BASE,
                "standard_usd": ALLOCATION_BASE,
            },
            "fear_greed":     fg,
            "yahoo_trending": self.yahoo.get_trending()[:10],
            "portefeuille_lt": (self.portefeuille_lt.get_resume()
                                if self.portefeuille_lt else {"nb_positions": 0, "positions": {}}),
            "lt_actions_cycle": lt_actions,
            "metriques": metriques,
            # Feature 2 : Paires de trading
            "paires_divergence": paires_divergence,
            # Feature 3 : Saisonnalité
            "saisonnalite_mult": SAISONNALITE.get(datetime.now().month, 1.0),
            # Feature 4 : Blacklist
            "blacklist_actifs": self.blacklist.get_blacklistes(),
            # Feature 9 : Météo économique
            "meteo_economique": meteo,
            # Feature 10 : Insiders
            "insiders_actifs": [t for t, v in self._insiders_data.items() if v],
            # Features avancées (A-H)
            "kill_switch_pertes": {
                "actif": _kill_switch_actif,
                "nb_pertes_consecutives": self.blacklist.get_pertes_consecutives_global(),
            },
            "kelly_historique_usd": self._kelly_base,
            "breakeven_actifs": list(self.risque.breakeven_actifs),
            "config": {
                "stop_loss_fixe_usd":       STOP_LOSS_FIXE,
                "take_profit_actions_usd":  TAKE_PROFIT_ACTIONS,
                "take_profit_crypto_usd":   TAKE_PROFIT_CRYPTO,
                "stop_loss_pct":            STOP_LOSS_PCT * 100,
                "take_profit_pct":          TAKE_PROFIT_PCT * 100,
                "trailing_stop_pct":        TRAILING_STOP_PCT * 100,
                "max_positions":            MAX_POSITIONS,
                "allocation_base":          ALLOCATION_BASE,
                "seuil_sentiment":          SEUIL_SENTIMENT_BUY,
                "nb_actifs":                len(ACTIFS_TOUS),
                "nb_candidats_sp500":       len(SP500_CANDIDATS),
                "indicateurs":              list(POIDS.keys()),
                "sentiment_modele":         AnalyseurSentimentGroq.MODELE,
                "lt_actif":                 LT_ACTIF,
                "lt_alloc_pct":             LT_ALLOC_PCT,
                "lt_max_positions":         LT_MAX_POSITIONS,
            },
        }


# =============================================================================
# [MODULE 8] POINT D'ENTRÉE
# =============================================================================

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logger = logging.getLogger("main")

    logger.info("🚀 Bot Route v4 — alpaca-trade-api + pandas-ta + Groq Cloud")
    logger.info(f"   Actifs    : {len(ACTIFS_TOUS)} ({len(ACTIFS_ACTIONS)} actions + {len(ACTIFS_CRYPTO)} crypto)")
    logger.info(f"   Indicateurs : HMA({HMA_PERIODE}) EMA({EMA_TENDANCE}) MACD RSI BB ADX Volume")
    logger.info(f"   Sentiment : Groq Cloud ({AnalyseurSentimentGroq.MODELE})")
    logger.info(f"   Stops : SL -{STOP_LOSS_FIXE:.0f}$ | TP Actions +{TAKE_PROFIT_ACTIONS:.0f}$ | TP Crypto +{TAKE_PROFIT_CRYPTO:.0f}$ | Trail {TRAILING_STOP_PCT*100:.0f}%")

    if not ALPACA_API_KEY:
        logger.error("ALPACA_API_KEY manquant — arrêt")
        sys.exit(1)

    MAX_CYCLES = 10       # Max cycles par run GitHub Actions
    LIMITE_SEC = 210      # 3min30 — arrêt propre avant timeout GitHub (4 min)
    DEBUT_RUN  = time.time()

    bot      = BotRoute()
    en_cours = True

    def stopper(sig, frame):
        nonlocal en_cours
        logger.info(f"Signal {sig} — arrêt propre…")
        en_cours = False

    signal.signal(signal.SIGINT,  stopper)
    signal.signal(signal.SIGTERM, stopper)

    while en_cours:
        if bot.cycle >= MAX_CYCLES:
            logger.info(f"✅ {MAX_CYCLES} cycles atteints — arrêt propre.")
            break
        if time.time() - DEBUT_RUN >= LIMITE_SEC:
            logger.info(f"✅ Limite {LIMITE_SEC}s atteinte — arrêt propre.")
            break
        try:
            etat = bot.run_cycle()
            tmp  = JSON_SORTIE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(etat, f, ensure_ascii=False, indent=2, default=str)
            tmp.replace(JSON_SORTIE)
            logger.info(f"✓ Export → {JSON_SORTIE}")
        except Exception as e:
            logger.error(f"Erreur cycle : {e}", exc_info=True)
        for _ in range(INTERVALLE):
            if not en_cours:
                break
            time.sleep(1)

    logger.info("Bot arrêté.")


if __name__ == "__main__":
    main()
