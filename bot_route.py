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

import json, time, os, signal, sys, logging, math, sqlite3, statistics
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

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
ALLOCATION_BASE       = 1_000.0   # Allocation standard par trade
ALLOCATION_FORTE      = 1_200.0   # Conviction FORTE  → +20%
ALLOCATION_FAIBLE     = 700.0     # Conviction FAIBLE → −30%
MAX_POSITIONS         = 9999  # Illimité — seul le cash disponible limite les positions
STOP_LOSS_PCT         = 0.03      # Stop-loss fixe de secours 3%
TAKE_PROFIT_PCT       = 0.045     # Take-profit fixe 4.5%
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
RSI_SURACHAT        = 65
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
    "hma_crossover":  25,   # HMA vs prix — signal prioritaire (code fourni)
    "ema_crossover":  15,   # EMA9 vs EMA21
    "ema_tendance":   15,   # Prix vs EMA100
    "macd":           15,   # MACD vs signal line
    "rsi":            10,   # Zones achat/vente
    "bollinger":      10,   # Position dans les bandes
    "adx":            5,    # Force de tendance
    "volume":         5,    # Confirmation volume
}

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
]

ACTIFS_CRYPTO = ["BTC/USD", "ETH/USD", "SOL/USD"]   # Format Alpaca crypto

ACTIFS_TOUS = ACTIFS_ACTIONS + ACTIFS_CRYPTO

SECTEURS = {
    "TECH":    ["TSLA","NVDA","AMD","AMZN","NFLX","META","AAPL","MSFT","GOOGL","INTC"],
    "CRYPTO":  ["COIN","HOOD","BTC/USD","ETH/USD","SOL/USD"],
    "FINTECH": ["SOFI","PLTR"],
    "EV":      ["RIVN","LCID","NIO"],
    "MOBILITY":["UBER","LYFT"],
    "SOCIAL":  ["SNAP","RBLX"],
    "GAMING":  ["DKNG","PENN","GME","AMC"],
    "ETF":     ["SPY","QQQ"],
    "AUTRES":  ["KO","DAL","BA"],
}
MAX_PAR_SECTEUR = 2

DATA_DIR    = Path(os.environ.get("BOT_DATA_PATH", "data/etat_bot.json")).parent
JSON_SORTIE = Path(os.environ.get("BOT_DATA_PATH", "data/etat_bot.json"))
INTERVALLE  = int(os.environ.get("BOT_INTERVAL_SEC", "60"))

# ── Protection trading ────────────────────────────────────────────────────
MAX_ACHATS_PAR_CYCLE  = 3       # Max nouveaux achats par cycle
COOLDOWN_APRES_ACHAT  = 1800    # 30 min de cooldown après un achat
SEUIL_CRYPTO_WEEKEND  = 70      # Score minimum crypto le weekend
BARRES_CACHE_TTL      = 300     # Cache OHLC 5 min pour éviter les requêtes redondantes

# Groupes d'actifs très corrélés — un seul acheté à la fois
GROUPES_CORRELES = [
    {"NVDA", "AMD"},
    {"RIVN", "LCID", "NIO"},
    {"BTC/USD", "ETH/USD", "COIN"},
    {"UBER", "LYFT"},
    {"GME", "AMC"},
]

# ── Candidats S&P500 pour rotation dynamique de watchlist ────────────
SP500_CANDIDATS = [
    "PYPL","SHOP","SQ","ROKU","ZM","DOCU","CRWD","NET","DDOG","SNOW",
    "DASH","ABNB","BKNG","EXPE","MAR","HLT","UAL","LUV",
    "JPM","BAC","C","WFC","GS","MS","BLK","SCHW","V","MA","AXP","COF","DFS",
    "UNH","CVS","WMT","COST","TGT","HD","LOW","NKE","PEP","MCD","SBUX",
    "DIS","CMCSA","T","VZ","TMUS","CRM","NOW","ADBE","ORCL","IBM","ACN",
    "LLY","PFE","MRK","JNJ","ABBV","TMO","DHR","SYK","MDT","ISRG","AMGN",
    "ENPH","NEE","FSLR","CEG","VST","GE","CAT","DE","HON",
    "XOM","CVX","COP","OXY","SLB","HAL","FCX","NUE","CLF","AA",
    "MSTR","RIOT","MARA","CLSK",
    "AFRM","UPST","LI","XPEV",
    "SMCI","ARM","AVGO","QCOM","TXN","MU","WDC","STX",
    "SPOT","PINS","RDDT","MTCH",
    "RKT","OPEN",
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

    def liquider_position(self, ticker: str) -> bool:
        """Liquide intégralement la position via api.close_position(). Retry 2×."""
        sym = ticker.replace("/", "")
        for tentative in range(3):
            try:
                self.api.close_position(sym)
                return True
            except Exception as e:
                if tentative < 2:
                    self.logger.warning(f"Liquidation {ticker} tentative {tentative+1}/3: {e}")
                    time.sleep(2 ** tentative)
                else:
                    self.logger.error(f"Erreur liquidation {ticker} : {e}")
                    return False


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

        # ── Score composite ───────────────────────────────────────────────
        total_possible = sum(POIDS.values())
        score_buy  = (points_buy  / total_possible) * 100
        score_sell = (points_sell / total_possible) * 100

        if score_buy > score_sell:
            score = round(50 + score_buy / 2, 1)
        else:
            score = round(50 - score_sell / 2, 1)
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

    def verifier_stop_loss(self, pos: dict) -> bool:
        plpc = pos.get("unrealized_plpc", 0)
        if plpc <= -(STOP_LOSS_PCT * 100):
            self.logger.warning(f"🛑 STOP-LOSS {pos['ticker']}: {plpc:.2f}%")
            return True
        return False

    def verifier_take_profit(self, pos: dict) -> bool:
        plpc = pos.get("unrealized_plpc", 0)
        if plpc >= (TAKE_PROFIT_PCT * 100):
            self.logger.info(f"✅ TAKE-PROFIT {pos['ticker']}: {plpc:.2f}%")
            return True
        return False

    def verifier_trailing_stop(self, pos: dict) -> bool:
        ticker = pos["ticker"]
        prix   = pos.get("current_price", 0)
        if ticker not in self.trailing_highs:
            self.trailing_highs[ticker] = prix
        if prix > self.trailing_highs[ticker]:
            self.trailing_highs[ticker] = prix
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

    def allocation(self, conviction: str, atr_pct: Optional[float]) -> float:
        base = {"FORTE": ALLOCATION_FORTE,
                "MOYENNE": ALLOCATION_BASE,
                "FAIBLE": ALLOCATION_FAIBLE}.get(conviction, ALLOCATION_BASE)
        if atr_pct and atr_pct > 2.0:
            base *= max(0.7, 1 - (atr_pct - 2.0) * 0.1)
        return round(base, 2)

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
                dans_position: bool) -> dict:

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

        action     = "AUCUNE"
        conviction = "FAIBLE"
        raison     = ""

        if not dans_position:
            if (signal_tech == "BUY" and
                score_sent >= SEUIL_SENTIMENT_BUY and
                score_fusionne >= SEUIL_TECH_BUY):

                action = "ACHAT"
                if score_fusionne >= SEUIL_CONVICTION_FORTE:
                    conviction = "FORTE"
                elif score_fusionne >= SEUIL_CONVICTION_FAIBLE:
                    conviction = "MOYENNE"
                raison = (
                    f"Score fusionné {score_fusionne:.0f}/100 "
                    f"(tech={score_tech:.0f} sent={score_sent:.2f} "
                    f"biais={biais_sent}"
                    f"{' | HMA crossover ✓' if croisement else ''})"
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
        self.df_qqq: pd.DataFrame = pd.DataFrame()
        self.persistance = GestionnairePersistance()
        self.logger      = logging.getLogger("BotRoute")
        self.cycle       = 0
        self.historique_trades: list = self._charger_historique()
        self.historique_valeur: list = []
        self.pause_drawdown    = False
        self.df_spy: pd.DataFrame = pd.DataFrame()
        self._cooldown: dict   = {}   # ticker → timestamp fin de cooldown
        self._achats_ce_cycle  = 0    # réinitialisé à chaque run_cycle
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # Migration one-shot JSON → SQLite
        self.persistance.importer_json(self.historique_trades)
        # Charge les poids optimisés du run nocturne précédent
        self._charger_poids_appris()

    # ── Helpers protection trading ────────────────────────────────────────

    def _en_cooldown(self, ticker: str) -> bool:
        return time.time() < self._cooldown.get(ticker, 0)

    def _definir_cooldown(self, ticker: str):
        self._cooldown[ticker] = time.time() + COOLDOWN_APRES_ACHAT
        self.logger.debug(f"Cooldown {ticker} : {COOLDOWN_APRES_ACHAT // 60} min")

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
        MAX_A    = 15
        day_idx  = datetime.now(timezone.utc).timetuple().tm_yday
        start    = (day_idx * MAX_A) % len(SP500_CANDIDATS)
        candidats = SP500_CANDIDATS[start:start + MAX_A]
        if len(candidats) < MAX_A:
            candidats += SP500_CANDIDATS[:MAX_A - len(candidats)]

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

    def _auditer_positions(self, positions: dict):
        for ticker, pos in list(positions.items()):
            raison = None
            if self.risque.verifier_stop_loss(pos):
                raison = f"STOP-LOSS {pos['unrealized_plpc']:.2f}%"
            elif self.risque.verifier_take_profit(pos):
                raison = f"TAKE-PROFIT {pos['unrealized_plpc']:.2f}%"
            elif self.risque.verifier_trailing_stop(pos):
                raison = "TRAILING-STOP"
            if raison:
                if self.alpaca.liquider_position(ticker):
                    trade = {
                        "type": "VENTE_AUTO", "ticker": ticker,
                        "prix": pos["current_price"],
                        "pnl": round(pos["unrealized_pl"], 2),
                        "pnl_pct": round(pos["unrealized_plpc"], 2),
                        "raison": raison,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    self.historique_trades.append(trade)
                    self.persistance.sauvegarder(trade)
                    # Cooldown après stop-loss pour éviter de re-rentrer immédiatement
                    if "STOP-LOSS" in raison:
                        self._definir_cooldown(ticker)
                    self.logger.info(f"[AUTO] {ticker} — {raison}")

    def _analyser_actif(self, ticker: str, positions: dict, compte: dict, executer: bool = True) -> dict:
        base = {
            "ticker": ticker, "action_executee": "AUCUNE",
            "score_technique": 50, "score_sentiment": 0.55,
            "score_fusionne": 50, "conviction": "FAIBLE",
            "signal_technique": "HOLD", "biais_groq": "neutre",
            "croisement_hma": False, "prix": None,
            "rsi": None, "atr_pct": None, "indicateurs": {},
            "momentum_spy": 0.0,
            "raison": "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Normalisation symbole — Alpaca retourne BTCUSD mais nos actifs sont BTC/USD
        ticker_norm   = ticker.replace("/", "")
        dans_position = ticker in positions or ticker_norm in positions

        # ── 1. Données OHLC via SDK Alpaca ────────────────────────────────
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

        # ── 2b. Score momentum vs SPY ─────────────────────────────────────
        if ticker != "SPY" and not self.df_spy.empty:
            mom = self.momentum.score_vs_spy(ticker, df, self.df_spy)
            base["momentum_spy"] = mom

        # ── 3. Sentiment Groq — uniquement si signal fort ou position ouverte
        sent = {"score": 0.55, "resume": "Non analysé",
                "biais": "neutre", "facteurs_positifs": [],
                "facteurs_negatifs": [], "source": "skip"}

        should_analyze_groq = (
            (tech["signal"] == "BUY"  and tech["score"] >= SEUIL_CONVICTION_FAIBLE) or
            tech["signal"] == "SELL" or
            dans_position
        )
        # Quand marché fermé : n'appeler l'IA QUE si déjà en cache (évite les 429 inutiles)
        if not executer:
            should_analyze_groq = should_analyze_groq and (
                ticker in self.sentiment._cache or ticker in self.gemini._cache
            )
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

        # ── 4. Décision fusionnée ─────────────────────────────────────────
        dec = self.decision.decider(tech, sent, dans_position)
        base.update({
            "score_fusionne": dec["score_fusionne"],
            "conviction":     dec["conviction"],
            "raison":         dec["raison"],
        })

        # ── 5. Exécution ──────────────────────────────────────────────────
        if not executer:
            if dec["action"] == "ACHAT":
                base["raison"] = f"📋 Ordre préparé (marché fermé) — {dec['raison']}"
                base["action_executee"] = "PRÉPARE"
            return base

        if dec["action"] == "ACHAT" and not self.pause_drawdown:
            # ── Garde-fous ordonnés par priorité ─────────────────────────
            if dans_position:
                base["raison"] = "Position déjà ouverte — achat ignoré"
            elif self._en_cooldown(ticker):
                reste = int(self._cooldown[ticker] - time.time())
                base["raison"] = f"Cooldown actif — {reste // 60}min {reste % 60}s restants"
            elif self._achats_ce_cycle >= MAX_ACHATS_PAR_CYCLE:
                base["raison"] = f"Limite {MAX_ACHATS_PAR_CYCLE} achats/cycle atteinte"
            elif self._actifs_correles(ticker, positions):
                base["raison"] = "Actif corrélé déjà en position"
            elif "/" in ticker and datetime.now(timezone.utc).weekday() >= 5 and tech["score"] < SEUIL_CRYPTO_WEEKEND:
                base["raison"] = f"Mode weekend crypto — score {tech['score']:.0f} < {SEUIL_CRYPTO_WEEKEND}"
            elif not self.risque.secteur_ok(ticker, positions):
                base["raison"] = f"Secteur saturé (max {MAX_PAR_SECTEUR})"
            else:
                montant = self.risque.allocation(
                    dec["conviction"], tech["indicateurs"].get("atr_pct"))
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
                        self._definir_cooldown(ticker)
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

        elif dec["action"] == "VENTE" and dans_position:
            # Récupère la position avec normalisation du symbole
            pos = positions.get(ticker) or positions.get(ticker_norm, {})
            if self.alpaca.liquider_position(ticker):
                base["action_executee"] = "VENTE"
                trade = {
                    "type": "VENTE", "ticker": ticker,
                    "prix": tech["prix"],
                    "pnl": round(pos.get("unrealized_pl", 0), 2),
                    "pnl_pct": round(pos.get("unrealized_plpc", 0), 2),
                    "sentiment": sent["score"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self.historique_trades.append(trade)
                self.persistance.sauvegarder(trade)
                self._sauvegarder_historique()
                self.discord.notifier_vente(
                    ticker,
                    round(pos.get("unrealized_pl", 0), 2),
                    round(pos.get("unrealized_plpc", 0), 2),
                    dec["raison"]
                )
                self.logger.info(
                    f"[VENTE] {ticker} P&L {pos.get('unrealized_plpc',0):.2f}% | {dec['raison'][:60]}"
                )

        return base

    def run_cycle(self) -> dict:
        self.cycle += 1
        self.logger.info(f"══ Cycle #{self.cycle} — {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC ══")

        self._achats_ce_cycle = 0   # reset compteur achats par cycle
        marche_ouvert = self.alpaca.marche_ouvert()
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

        self.pause_drawdown = self.risque.verifier_drawdown(compte["equity"])
        # Alerte Discord préventive à 3% de drawdown (avant la pause à 5%)
        dd_pct = max(0.0, (self.risque.capital_initial - compte["equity"]) / self.risque.capital_initial * 100)
        if 3.0 <= dd_pct < 5.0:
            self.discord._envoyer(
                f"⚠️ **ROUTE/v4 — Alerte drawdown** : {dd_pct:.2f}% "
                f"(limite pause : 5%) | Equity : ${compte['equity']:,.2f}"
            )
        if marche_ouvert:
            self._auditer_positions(positions)

        # Données SPY pour score momentum
        self.df_spy = self.alpaca.get_barres("SPY")
        # Données QQQ pour détection régime
        self.df_qqq = self.alpaca.get_barres("QQQ")
        # Régime de marché
        regime_marche = self.regime_det.detecter(self.df_spy, self.df_qqq)

        decisions = []
        for ticker in ACTIFS_TOUS:
            time.sleep(0.05)
            d = self._analyser_actif(ticker, positions, compte, executer=marche_ouvert)
            decisions.append(d)

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

            # Rotation watchlist — toutes les 6h la nuit
            if self._peut_executer_routine("watchlist_dynamique.json", 6.0):
                self._rotation_watchlist(positions)

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

        # Métriques de performance depuis SQLite
        metriques = self.persistance.calculer_metriques()

        # Stress test portefeuille
        stress_test = self._stress_test(positions_apres, compte["equity"])

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
            "metriques": metriques,
            "config": {
                "stop_loss_pct":      STOP_LOSS_PCT * 100,
                "take_profit_pct":    TAKE_PROFIT_PCT * 100,
                "trailing_stop_pct":  TRAILING_STOP_PCT * 100,
                "max_positions":      MAX_POSITIONS,
                "allocation_base":    ALLOCATION_BASE,
                "seuil_sentiment":    SEUIL_SENTIMENT_BUY,
                "nb_actifs":          len(ACTIFS_TOUS),
                "nb_candidats_sp500": len(SP500_CANDIDATS),
                "indicateurs":        list(POIDS.keys()),
                "sentiment_modele":   AnalyseurSentimentGroq.MODELE,
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
    logger.info(f"   Stops : SL {STOP_LOSS_PCT*100:.0f}% / TP {TAKE_PROFIT_PCT*100:.0f}% / Trail {TRAILING_STOP_PCT*100:.0f}%")

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
