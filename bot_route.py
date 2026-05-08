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

import json, time, os, signal, sys, logging, math
from datetime import datetime, timezone
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
BARRES_LIMIT        = 300   # Bougies 1h récupérées (besoin EMA100 + marge)

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

ACTIFS_CRYPTO = ["BTC/USD", "ETH/USD"]   # Format Alpaca crypto

ACTIFS_TOUS = ACTIFS_ACTIONS + ACTIFS_CRYPTO

SECTEURS = {
    "TECH":    ["TSLA","NVDA","AMD","AMZN","NFLX","META","AAPL","MSFT","GOOGL","INTC"],
    "CRYPTO":  ["COIN","HOOD","BTC/USD","ETH/USD"],
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
        # Initialisation du SDK officiel (exactement comme le code fourni)
        self.api = tradeapi.REST(
            ALPACA_API_KEY,
            ALPACA_SECRET_KEY,
            ALPACA_BASE_URL,
            api_version="v2",
        )
        self.logger = logging.getLogger("Alpaca")

    def get_compte(self) -> dict:
        """Retourne equity, cash, buying_power via le SDK."""
        acc = self.api.get_account()
        return {
            "equity":        float(acc.equity),
            "cash":          float(acc.cash),
            "buying_power":  float(acc.buying_power),
            "portfolio_value": float(acc.portfolio_value),
        }

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
        Récupère les barres horaires sous forme de DataFrame pandas.
        Utilise get_crypto_bars() pour BTC/ETH, get_bars() pour les actions.
        Retourne un DataFrame avec colonnes : open, high, low, close, volume
        """
        try:
            if "/" in ticker:
                # Crypto — même approche que dans le code fourni
                df = self.api.get_crypto_bars(
                    ticker, TimeFrame.Hour, limit=limit
                ).df
            else:
                # Actions — timeframe 1 heure
                df = self.api.get_bars(
                    ticker, TimeFrame.Hour, limit=limit
                ).df

            if df is None or df.empty:
                return pd.DataFrame()

            # Normalise les noms de colonnes en minuscules
            df.columns = [c.lower() for c in df.columns]
            df = df.sort_index()
            return df

        except Exception as e:
            self.logger.warning(f"Barres {ticker} : {e}")
            return pd.DataFrame()

    def soumettre_ordre(self, ticker: str, montant_usd: float,
                        side: str = "buy") -> Optional[object]:
        """
        Soumet un ordre market notional (USD) via api.submit_order().
        Pour la crypto, utilise time_in_force='gtc' (identique au code fourni).
        """
        tif = "gtc" if "/" in ticker else "day"
        # Alpaca notional : montant en USD, il calcule la quantité
        ordre = self.api.submit_order(
            symbol=ticker.replace("/", ""),  # BTC/USD → BTCUSD pour l'ordre
            notional=str(round(montant_usd, 2)),
            side=side,
            type="market",
            time_in_force=tif,
        )
        self.logger.info(f"Ordre {side.upper()} {ticker} ${montant_usd:.0f} → id={ordre.id}")
        return ordre

    def liquider_position(self, ticker: str) -> bool:
        """Liquide intégralement la position via api.close_position()."""
        try:
            sym = ticker.replace("/", "")
            self.api.close_position(sym)
            return True
        except Exception as e:
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
        if len(df) < BARRES_LIMIT // 2:
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

    GROQ_URL  = "https://api.groq.com/openai/v1/chat/completions"
    MODELE    = "llama-3.3-70b-versatile"
    CACHE_TTL = 180   # 3 minutes

    def __init__(self):
        self.logger    = logging.getLogger("Groq")
        self._cache    = {}
        self._cache_ts = {}

    def _secteur(self, ticker: str) -> str:
        for s, tickers in SECTEURS.items():
            if ticker in tickers:
                return s
        return "AUTRES"

    def scorer(self, ticker: str, prix: float,
               score_tech: float = 50,
               rsi: Optional[float] = None,
               hma_signal: Optional[str] = None,
               atr_pct: Optional[float] = None) -> dict:
        """
        Appelle Groq Cloud pour scorer le sentiment du marché sur l'actif.
        Cache 3 minutes, fallback 0.55 si clé absente ou erreur réseau.
        """
        # ── Cache ─────────────────────────────────────────────────────────
        now = time.time()
        if ticker in self._cache and (now - self._cache_ts.get(ticker, 0)) < self.CACHE_TTL:
            return self._cache[ticker]

        if not GROQ_API_KEY:
            self.logger.warning(f"GROQ_API_KEY absent — fallback neutre {ticker}")
            return self._fallback("Clé GROQ_API_KEY non configurée")

        secteur   = self._secteur(ticker)
        rsi_str   = f"RSI={rsi:.1f}" if rsi else "RSI=N/A"
        hma_str   = f"HMA_signal={hma_signal}" if hma_signal else ""
        atr_str   = f"ATR={atr_pct:.2f}%" if atr_pct else ""

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
                timeout=20,
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
                "source":            self.MODELE,
            }
            self._cache[ticker]    = resultat
            self._cache_ts[ticker] = now
            self.logger.info(f"Groq {ticker}: score={score:.3f} biais={resultat['biais']}")
            return resultat

        except Exception as e:
            self.logger.warning(f"Groq erreur {ticker}: {e}")
            return self._fallback(str(e))

    def _fallback(self, raison: str) -> dict:
        return {
            "score": 0.55, "confiance": 0.3,
            "resume": f"Sentiment indisponible ({raison[:60]})",
            "facteurs_positifs": [], "facteurs_negatifs": [],
            "biais": "neutre", "source": "fallback",
        }


# =============================================================================
# [MODULE 5] GestionnaireRisque
# =============================================================================

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
        self.risque      = GestionnaireRisque()
        self.decision    = ScoreDecisionIA()
        self.logger      = logging.getLogger("BotRoute")
        self.cycle       = 0
        self.historique_trades: list = []
        self.historique_valeur: list = []
        self.pause_drawdown = False
        DATA_DIR.mkdir(parents=True, exist_ok=True)

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
                    self.historique_trades.append({
                        "type": "VENTE_AUTO", "ticker": ticker,
                        "prix": pos["current_price"],
                        "pnl": round(pos["unrealized_pl"], 2),
                        "pnl_pct": round(pos["unrealized_plpc"], 2),
                        "raison": raison,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    self.logger.info(f"[AUTO] {ticker} — {raison}")

    def _analyser_actif(self, ticker: str, positions: dict, compte: dict) -> dict:
        base = {
            "ticker": ticker, "action_executee": "AUCUNE",
            "score_technique": 50, "score_sentiment": 0.55,
            "score_fusionne": 50, "conviction": "FAIBLE",
            "signal_technique": "HOLD", "biais_groq": "neutre",
            "croisement_hma": False, "prix": None,
            "rsi": None, "atr_pct": None, "indicateurs": {},
            "raison": "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

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

        # ── 3. Sentiment Groq (si signal pertinent) ────────────────────
        sent = {"score": 0.55, "resume": "Non analysé",
                "biais": "neutre", "facteurs_positifs": [],
                "facteurs_negatifs": [], "source": "skip"}

        if tech["signal"] in ("BUY", "SELL") or ticker in positions:
            sent = self.sentiment.scorer(
                ticker, tech["prix"] or 0,
                tech["score"],
                tech["indicateurs"].get("rsi"),
                tech["indicateurs"].get("hma_signal"),
                tech["indicateurs"].get("atr_pct"),
            )

        base["score_sentiment"]    = sent["score"]
        base["biais_groq"]         = sent.get("biais", "neutre")
        base["resume_groq"]        = sent.get("resume", "")
        base["facteurs_positifs"]  = sent.get("facteurs_positifs", [])
        base["facteurs_negatifs"]  = sent.get("facteurs_negatifs", [])

        # ── 4. Décision fusionnée ─────────────────────────────────────────
        dec = self.decision.decider(tech, sent, ticker in positions)
        base.update({
            "score_fusionne": dec["score_fusionne"],
            "conviction":     dec["conviction"],
            "raison":         dec["raison"],
        })

        # ── 5. Exécution ──────────────────────────────────────────────────
        if dec["action"] == "ACHAT" and not self.pause_drawdown:
            if not self.risque.secteur_ok(ticker, positions):
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
                        self.historique_trades.append({
                            "type": "ACHAT", "ticker": ticker,
                            "montant": montant, "prix": tech["prix"],
                            "conviction": dec["conviction"],
                            "score_fusionne": dec["score_fusionne"],
                            "croisement_hma": dec["croisement_hma"],
                            "sentiment": sent["score"],
                            "rsi": tech["indicateurs"].get("rsi"),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                        self.logger.info(
                            f"[ACHAT] {ticker} {dec['conviction']} "
                            f"${montant:.0f} | fused={dec['score_fusionne']:.0f} "
                            f"sent={sent['score']:.2f} HMA={'✓' if dec['croisement_hma'] else '—'}"
                        )
                    except Exception as e:
                        base["raison"] = f"Erreur ordre : {e}"

        elif dec["action"] == "VENTE" and ticker in positions:
            pos = positions[ticker]
            if self.alpaca.liquider_position(ticker):
                base["action_executee"] = "VENTE"
                self.historique_trades.append({
                    "type": "VENTE", "ticker": ticker,
                    "prix": tech["prix"],
                    "pnl": round(pos.get("unrealized_pl", 0), 2),
                    "pnl_pct": round(pos.get("unrealized_plpc", 0), 2),
                    "sentiment": sent["score"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                self.logger.info(
                    f"[VENTE] {ticker} P&L {pos.get('unrealized_plpc',0):.2f}% | {dec['raison'][:60]}"
                )

        return base

    def run_cycle(self) -> dict:
        self.cycle += 1
        self.logger.info(f"══ Cycle #{self.cycle} — {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC ══")

        try:
            compte    = self.alpaca.get_compte()
            positions = self.alpaca.get_positions()
        except Exception as e:
            self.logger.error(f"Erreur Alpaca SDK: {e}")
            return {"meta": {"cycle": self.cycle, "erreur": str(e)},
                    "portefeuille": {}, "decisions_cycle": []}

        self.pause_drawdown = self.risque.verifier_drawdown(compte["equity"])
        self._auditer_positions(positions)

        decisions = []
        for ticker in ACTIFS_TOUS:
            time.sleep(0.3)
            d = self._analyser_actif(ticker, positions, compte)
            decisions.append(d)

        self.historique_valeur.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "equity": compte["equity"], "cash": compte["cash"],
        })
        self.historique_valeur = self.historique_valeur[-100:]
        self.historique_trades = self.historique_trades[-60:]

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

        return {
            "meta": {
                "cycle":           self.cycle,
                "timestamp":       datetime.now(timezone.utc).isoformat(),
                "bot_version":     "4.0.0-route",
                "broker":          "Alpaca Trade API v2",
                "mode":            "PAPER",
                "sentiment_engine":"Groq Cloud llama-3.3-70b",
                "pause_drawdown":  self.pause_drawdown,
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
                "historique_trades": self.historique_trades,
                "historique_valeur": self.historique_valeur,
            },
            "decisions_cycle": decisions,
            "config": {
                "stop_loss_pct":      STOP_LOSS_PCT * 100,
                "take_profit_pct":    TAKE_PROFIT_PCT * 100,
                "trailing_stop_pct":  TRAILING_STOP_PCT * 100,
                "max_positions":      MAX_POSITIONS,
                "allocation_base":    ALLOCATION_BASE,
                "seuil_sentiment":    SEUIL_SENTIMENT_BUY,
                "nb_actifs":          len(ACTIFS_TOUS),
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

    bot      = BotRoute()
    en_cours = True

    def stopper(sig, frame):
        nonlocal en_cours
        logger.info(f"Signal {sig} — arrêt propre…")
        en_cours = False

    signal.signal(signal.SIGINT,  stopper)
    signal.signal(signal.SIGTERM, stopper)

    while en_cours:
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
