"""
=============================================================================
bot_route.py — Bot de trading "Route" inspiré de l'analyse Aywen/Gemini
=============================================================================

MODULES & RÔLES :

  [MODULE 1] Configuration & Constantes
      → Toute la configuration en un seul endroit : actifs, seuils SMA/RSI,
        règles de money management (stop-loss 3%, allocation par position)
      → Les clés API sont lues EXCLUSIVEMENT depuis les variables d'environnement
      → Rôle : centre de contrôle — modifier une valeur ici change tout le comportement

  [MODULE 2] AlpacaClient
      → Wrapper autour de l'API REST Alpaca Paper Trading
      → Fonctions : récupérer les barres OHLC, soumettre des ordres market,
        lire les positions ouvertes et le solde du compte
      → Rôle : unique point de contact avec le broker — tout ordre passe ici

  [MODULE 3] MoteurTechnique
      → Calcule SMA court terme (5 min) et long terme (3h) sur les données OHLC
      → Calcule le RSI(14) pour détecter sur-achat / sur-vente
      → Génère un signal : BUY / SELL / HOLD avec justification textuelle
      → Rôle : cerveau mathématique du bot — logique pure sans émotion

  [MODULE 4] AnalyseurSentiment
      → Appelle l'API Claude (Anthropic) pour scorer le sentiment de marché
      → Simule un flux de "news/tweets" fictif horodaté sur l'actif analysé
      → Retourne un score 0.0–1.0 : <0.5 = pessimiste, >0.5 = optimiste
      → Rôle : filtre émotionnel — bloque les achats en période de panique

  [MODULE 5] GestionnaireRisque
      → Applique les règles de money management strictes (inspiré analyse Gemini) :
          • Stop-loss automatique à 3% sous le prix d'achat
          • Take-profit à 4.5% (ratio gain/perte = 1.5×)
          • Maximum 5 positions simultanées (1 000 € chacune sur 5 000 € capital)
          • Drawdown max 5% du portefeuille total avant pause forcée
      → Rôle : gardien du capital — la survie avant la performance

  [MODULE 6] BotRoute
      → Orchestre tous les modules : données → technique → sentiment → risque → ordre
      → Décision finale : BUY validé seulement si signal technique ET sentiment > 0.5
      → Journalise chaque décision dans data/etat_bot.json pour le dashboard HTML
      → Rôle : chef d'orchestre — assemble toutes les pièces en un cycle cohérent

  [MODULE 7] Point d'entrée
      → Boucle infinie avec intervalle configurable (BOT_INTERVAL_SEC)
      → Gestion propre des signaux SIGINT/SIGTERM pour arrêt sans ordre orphelin
      → Rôle : moteur d'exécution continu compatible GitHub Actions

=============================================================================
SÉCURITÉ — Variables d'environnement requises (GitHub Secrets) :
  ALPACA_API_KEY        → Clé API Alpaca (régénère-la sur app.alpaca.markets !)
  ALPACA_SECRET_KEY     → Secret Alpaca
  ALPACA_BASE_URL       → https://paper-api.alpaca.markets  (paper trading)
  ANTHROPIC_API_KEY     → Clé Anthropic pour l'analyse de sentiment
=============================================================================
"""

# ── Imports stdlib ──────────────────────────────────────────────────────────
import json
import time
import os
import signal
import sys
import logging
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# ── Imports tiers (requirements.txt) ────────────────────────────────────────
try:
    import requests          # Appels HTTP vers Alpaca et Anthropic
except ImportError:
    sys.exit("❌  Installe les dépendances : pip install -r requirements.txt")


# =============================================================================
# [MODULE 1] CONFIGURATION & CONSTANTES
# =============================================================================

# ── Clés API — jamais en dur, toujours depuis l'environnement ───────────────
ALPACA_API_KEY    = os.environ.get("ALPACA_API_KEY",    "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL   = os.environ.get("ALPACA_BASE_URL",   "https://paper-api.alpaca.markets/v2")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Portefeuille & money management (analyse Gemini) ────────────────────────
CAPITAL_INITIAL       = 5_000.0   # USD fictifs (paper trading)
ALLOCATION_PAR_TRADE  = 1_000.0   # ~20% du capital par position
MAX_POSITIONS         = 5         # Maximum simultané
STOP_LOSS_PCT         = 0.03      # 3% — stoppez l'hémorragie
TAKE_PROFIT_PCT       = 0.045     # 4.5% — ratio gain/perte = 1.5×
MAX_DRAWDOWN_PCT      = 0.05      # Pause forcée si -5% du portefeuille

# ── Seuils indicateurs techniques ───────────────────────────────────────────
RSI_SURVENTE          = 35        # En dessous = sur-vendu → opportunité achat
RSI_SURACHAT          = 65        # Au dessus  = sur-acheté → éviter / vendre
RSI_PERIODE           = 14        # Période standard RSI
SMA_COURT_MINUTES     = 5         # SMA rapide : capture le momentum
SMA_LONG_MINUTES      = 180       # SMA lente  : tendance demi-journée (3h)
BARRES_REQUISES       = 200       # Nombre de bougies 1-min à récupérer

# ── Seuil de sentiment ───────────────────────────────────────────────────────
SEUIL_SENTIMENT_ACHAT = 0.50      # < 0.5 = marché pessimiste → pas d'achat
SEUIL_SENTIMENT_VENTE = 0.30      # < 0.3 = panique → vendre si position ouverte

# ── 30 actifs volatils sélectionnés (liste Aywen) ───────────────────────────
ACTIFS = [
    "TSLA", "NVDA", "AMD",  "AMZN", "NFLX",
    "META", "AAPL", "MSFT", "GOOGL","INTC",
    "COIN", "SOFI", "PLTR", "RIVN", "LCID",
    "NIO",  "UBER", "LYFT", "SNAP", "RBLX",
    "HOOD", "DKNG", "PENN", "GME",  "AMC",
    "SPY",  "QQQ",  "KO",   "DAL",  "BA",
]

# ── Chemins de sortie ────────────────────────────────────────────────────────
DATA_DIR      = Path(os.environ.get("BOT_DATA_PATH", "data/etat_bot.json")).parent
JSON_SORTIE   = Path(os.environ.get("BOT_DATA_PATH", "data/etat_bot.json"))
INTERVALLE    = int(os.environ.get("BOT_INTERVAL_SEC", "60"))


# =============================================================================
# [MODULE 2] AlpacaClient — Wrapper REST Alpaca Paper Trading
# =============================================================================

class AlpacaClient:
    """
    Interface complète avec l'API Alpaca v2.
    Toutes les opérations broker passent exclusivement par cette classe.
    
    Endpoints utilisés :
      GET  /v2/account              → solde et equity
      GET  /v2/positions            → positions ouvertes
      GET  /v2/stocks/{sym}/bars    → données OHLC historiques
      POST /v2/orders               → soumettre un ordre market
      DELETE /v2/positions/{sym}    → liquider une position
    """

    def __init__(self):
        if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
            raise ValueError(
                "❌  ALPACA_API_KEY et ALPACA_SECRET_KEY doivent être définis "
                "dans les variables d'environnement (GitHub Secrets)."
            )
        self.base    = ALPACA_BASE_URL.rstrip("/")
        self.headers = {
            "APCA-API-KEY-ID":     ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
            "Content-Type":        "application/json",
        }
        self.logger = logging.getLogger("AlpacaClient")

    def _get(self, endpoint: str, params: dict = None) -> dict:
        """GET générique avec gestion des erreurs HTTP."""
        url = f"{self.base}/{endpoint.lstrip('/')}"
        r = requests.get(url, headers=self.headers, params=params, timeout=15)
        if r.status_code == 429:
            self.logger.warning("Rate limit Alpaca — attente 5s")
            time.sleep(5)
            r = requests.get(url, headers=self.headers, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def _post(self, endpoint: str, payload: dict) -> dict:
        """POST générique."""
        url = f"{self.base}/{endpoint.lstrip('/')}"
        r = requests.post(url, headers=self.headers, json=payload, timeout=15)
        r.raise_for_status()
        return r.json()

    def _delete(self, endpoint: str) -> dict:
        """DELETE générique (liquidation de position)."""
        url = f"{self.base}/{endpoint.lstrip('/')}"
        r = requests.delete(url, headers=self.headers, timeout=15)
        if r.status_code == 204:
            return {}
        r.raise_for_status()
        return r.json()

    # ── Compte ────────────────────────────────────────────────────────────────
    def get_compte(self) -> dict:
        """Retourne l'equity, le cash disponible et la valeur du portefeuille."""
        data = self._get("/account")
        return {
            "equity":          float(data.get("equity",          0)),
            "cash":            float(data.get("cash",             0)),
            "buying_power":    float(data.get("buying_power",     0)),
            "portfolio_value": float(data.get("portfolio_value",  0)),
            "currency":        data.get("currency", "USD"),
        }

    # ── Positions ─────────────────────────────────────────────────────────────
    def get_positions(self) -> dict:
        """
        Retourne toutes les positions ouvertes sous forme de dict indexé par ticker.
        Chaque entrée contient : qty, avg_entry_price, current_price, unrealized_pl
        """
        data = self._get("/positions")
        positions = {}
        for p in data:
            sym = p["symbol"]
            positions[sym] = {
                "ticker":            sym,
                "qty":               float(p.get("qty", 0)),
                "avg_entry_price":   float(p.get("avg_entry_price", 0)),
                "current_price":     float(p.get("current_price", 0)),
                "market_value":      float(p.get("market_value", 0)),
                "unrealized_pl":     float(p.get("unrealized_pl", 0)),
                "unrealized_plpc":   float(p.get("unrealized_plpc", 0)) * 100,
                "side":              p.get("side", "long"),
            }
        return positions

    # ── Données OHLC ──────────────────────────────────────────────────────────
    def get_barres(self, ticker: str, limit: int = BARRES_REQUISES) -> list:
        """
        Récupère les N dernières bougies 1-minute pour un actif.
        Retourne une liste de dicts {t, o, h, l, c, v} triée par date croissante.
        Utilise l'endpoint /v2/stocks/{symbol}/bars (Alpaca Data API v2).
        """
        # L'endpoint data est sur une URL différente pour Alpaca
        data_base = "https://data.alpaca.markets/v2"
        url    = f"{data_base}/stocks/{ticker}/bars"
        params = {
            "timeframe": "1Min",
            "limit":     limit,
            "sort":      "asc",
        }
        r = requests.get(url, headers=self.headers, params=params, timeout=15)
        if r.status_code in (403, 422):
            # Actif non disponible ou marché fermé — retourne liste vide
            return []
        r.raise_for_status()
        barres_raw = r.json().get("bars", []) or []
        return [
            {
                "t": b["t"],
                "o": float(b["o"]),
                "h": float(b["h"]),
                "l": float(b["l"]),
                "c": float(b["c"]),
                "v": int(b.get("v", 0)),
            }
            for b in barres_raw
        ]

    # ── Ordres ────────────────────────────────────────────────────────────────
    def acheter_market(self, ticker: str, montant_usd: float) -> dict:
        """
        Soumet un ordre d'achat market en mode "notional" (montant en USD).
        Alpaca calcule automatiquement la quantité de fractions d'actions.
        """
        payload = {
            "symbol":        ticker,
            "notional":      str(round(montant_usd, 2)),
            "side":          "buy",
            "type":          "market",
            "time_in_force": "day",
        }
        return self._post("/orders", payload)

    def vendre_position(self, ticker: str) -> dict:
        """Liquide intégralement la position sur un actif."""
        return self._delete(f"/positions/{ticker}")

    def get_prix_courant(self, ticker: str) -> Optional[float]:
        """Récupère le dernier prix via les barres (fallback sur position)."""
        barres = self.get_barres(ticker, limit=2)
        if barres:
            return barres[-1]["c"]
        return None


# =============================================================================
# [MODULE 3] MoteurTechnique — SMA + RSI + signal de décision
# =============================================================================

class MoteurTechnique:
    """
    Calcule les indicateurs SMA et RSI sur les données OHLC Alpaca,
    puis génère un signal de trading structuré.
    
    Stratégie implémentée (fidèle à l'analyse Gemini/Aywen) :
      - Croisement SMA5 > SMA180 = momentum haussier
      - RSI < RSI_SURVENTE       = sur-vendu, rebond probable
      - RSI > RSI_SURACHAT       = sur-acheté, éviter l'entrée
      - Signal BUY  : SMA haussier ET RSI non sur-acheté
      - Signal SELL : SMA baissier OU RSI sur-acheté
      - Signal HOLD : signal insuffisant ou RSI neutre
    """

    def __init__(self):
        self.logger = logging.getLogger("MoteurTechnique")

    @staticmethod
    def _sma(closes: list, periode: int) -> Optional[float]:
        """Moyenne mobile simple sur 'periode' dernières valeurs."""
        if len(closes) < periode:
            return None
        return sum(closes[-periode:]) / periode

    @staticmethod
    def _rsi(closes: list, periode: int = RSI_PERIODE) -> Optional[float]:
        """
        RSI de Wilder sur 'periode' périodes.
        Formule : RSI = 100 - (100 / (1 + RS))  où RS = moyenne gains / moyenne pertes
        """
        if len(closes) < periode + 1:
            return None
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains  = [d for d in deltas if d > 0]
        pertes = [abs(d) for d in deltas if d < 0]
        # Utilise les 'periode' dernières valeurs
        gains_r  = deltas[-periode:]
        pertes_r = deltas[-periode:]
        moy_gains  = sum(g for g in gains_r if g > 0) / periode
        moy_pertes = sum(abs(p) for p in pertes_r if p < 0) / periode
        if moy_pertes == 0:
            return 100.0
        rs = moy_gains / moy_pertes
        return round(100 - (100 / (1 + rs)), 2)

    def analyser(self, ticker: str, barres: list) -> dict:
        """
        Analyse technique complète sur les barres OHLC.
        Retourne un dict avec tous les indicateurs et le signal final.
        """
        if len(barres) < SMA_LONG_MINUTES + RSI_PERIODE:
            return {
                "ticker": ticker,
                "signal": "HOLD",
                "raison": "Données insuffisantes pour l'analyse",
                "sma_court": None,
                "sma_long":  None,
                "rsi":       None,
                "prix":      barres[-1]["c"] if barres else None,
            }

        closes = [b["c"] for b in barres]
        prix_courant = closes[-1]

        sma_court = self._sma(closes, SMA_COURT_MINUTES)
        sma_long  = self._sma(closes, SMA_LONG_MINUTES)
        rsi       = self._rsi(closes, RSI_PERIODE)

        # ── Logique de signal ────────────────────────────────────────────────
        signal = "HOLD"
        raison = ""

        if sma_court and sma_long and rsi is not None:
            croisement_haussier = sma_court > sma_long
            croisement_baissier = sma_court < sma_long

            if croisement_haussier and rsi < RSI_SURACHAT:
                if rsi < RSI_SURVENTE:
                    signal = "BUY"
                    raison = f"SMA5 > SMA180 (momentum↑) + RSI {rsi:.1f} en zone survente — rebond probable"
                else:
                    signal = "BUY"
                    raison = f"SMA5 > SMA180 (momentum↑), RSI {rsi:.1f} neutre — signal valide"

            elif croisement_baissier or rsi > RSI_SURACHAT:
                signal = "SELL"
                if rsi > RSI_SURACHAT:
                    raison = f"RSI {rsi:.1f} en zone surachat — fatigue du marché"
                else:
                    raison = f"SMA5 < SMA180 (momentum↓) — tendance baissière confirmée"
            else:
                signal = "HOLD"
                raison = f"Signal insuffisant — SMA neutre, RSI {rsi:.1f}"
        else:
            raison = "Indicateurs non calculables (données trop courtes)"

        variation_sma = ((sma_court - sma_long) / sma_long * 100) if (sma_court and sma_long) else 0

        return {
            "ticker":       ticker,
            "signal":       signal,
            "raison":       raison,
            "sma_court":    round(sma_court, 4) if sma_court else None,
            "sma_long":     round(sma_long, 4)  if sma_long  else None,
            "variation_sma_pct": round(variation_sma, 3),
            "rsi":          rsi,
            "prix":         round(prix_courant, 4),
            "timestamp":    datetime.now(timezone.utc).isoformat(),
        }


# =============================================================================
# [MODULE 4] AnalyseurSentiment — Score 0.0–1.0 via Claude (Anthropic)
# =============================================================================

class AnalyseurSentiment:
    """
    Implémente la "variable émotion" de l'analyse Aywen.
    
    Flux :
      1. Génère un contexte textuel fictif horodaté simulant des flux de
         tweets/news pour l'actif demandé (pas de scraping Twitter réel
         requis — le LLM génère et analyse un scénario réaliste)
      2. Soumet le contexte à Claude via l'API Anthropic
      3. Extrait le score numérique 0.0–1.0 de la réponse
    
    Interprétation du score (table Gemini) :
      0.0–0.3 → Panique       : bloquer les achats
      0.3–0.5 → Inquiétude    : temporiser
      0.5–0.7 → Optimisme     : valider les signaux techniques
      0.7–1.0 → Euphorie      : confirmation forte d'achat
    """

    ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

    def __init__(self):
        self.logger = logging.getLogger("AnalyseurSentiment")
        self._cache: dict = {}         # Cache 5 min pour éviter les appels redondants
        self._cache_ts: dict = {}

    def _construire_prompt(self, ticker: str, prix: float, rsi: Optional[float]) -> str:
        """
        Construit le prompt qui demande à Claude d'analyser le sentiment
        de marché pour cet actif et de retourner uniquement un score 0.0–1.0.
        """
        rsi_info = f", RSI actuel : {rsi:.1f}" if rsi else ""
        return f"""Tu es un analyste quantitatif spécialisé dans l'analyse de sentiment de marché.

Actif analysé : {ticker} (prix courant : ${prix:.2f}{rsi_info})
Date/heure : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

Génère un scénario réaliste de sentiment de marché actuel pour {ticker} en te basant sur :
- Les conditions macroéconomiques générales du moment
- La psychologie typique des traders sur cet actif
- Le niveau de prix et le RSI fournis

Puis évalue le sentiment global du marché pour {ticker} sur une échelle de 0.0 à 1.0 :
  0.0 = panique totale / pessimisme extrême
  0.5 = neutralité / équilibre
  1.0 = euphorie totale / optimisme maximal

RÉPONDS UNIQUEMENT avec un JSON strict, sans aucun texte autour :
{{"score": <float entre 0.0 et 1.0>, "resume": "<une phrase décrivant le sentiment>"}}"""

    def scorer(self, ticker: str, prix: float, rsi: Optional[float] = None) -> dict:
        """
        Retourne le score de sentiment pour un actif.
        Utilise un cache de 5 minutes pour limiter les appels API.
        En cas d'erreur API (clé absente, rate limit), retourne 0.55 (neutre optimiste).
        """
        # ── Cache 5 minutes ──────────────────────────────────────────────────
        maintenant = time.time()
        if ticker in self._cache and (maintenant - self._cache_ts.get(ticker, 0)) < 300:
            return self._cache[ticker]

        # ── Fallback si pas de clé Anthropic ────────────────────────────────
        if not ANTHROPIC_API_KEY:
            self.logger.warning(f"ANTHROPIC_API_KEY absent — score neutre pour {ticker}")
            return {"score": 0.55, "resume": "Analyse de sentiment indisponible (clé API manquante)"}

        prompt = self._construire_prompt(ticker, prix, rsi)

        try:
            r = requests.post(
                self.ANTHROPIC_URL,
                headers={
                    "x-api-key":         ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type":      "application/json",
                },
                json={
                    "model":      "claude-sonnet-4-20250514",
                    "max_tokens": 150,
                    "messages":   [{"role": "user", "content": prompt}],
                },
                timeout=20,
            )
            r.raise_for_status()
            texte = r.json()["content"][0]["text"].strip()

            # Nettoyage des éventuels backticks markdown
            texte = texte.replace("```json", "").replace("```", "").strip()
            data  = json.loads(texte)

            score  = float(data.get("score", 0.55))
            score  = max(0.0, min(1.0, score))   # Clamp entre 0 et 1
            resume = str(data.get("resume", ""))

            resultat = {"score": round(score, 3), "resume": resume}
            self._cache[ticker]    = resultat
            self._cache_ts[ticker] = maintenant
            return resultat

        except Exception as e:
            self.logger.warning(f"Erreur sentiment {ticker} : {e} — score neutre")
            return {"score": 0.55, "resume": f"Erreur d'analyse : {e}"}


# =============================================================================
# [MODULE 5] GestionnaireRisque — Money management strict
# =============================================================================

class GestionnaireRisque:
    """
    Implémente toutes les règles de protection du capital issues de l'analyse Gemini :
      - Stop-loss 3% → coupe les pertes immédiatement
      - Take-profit 4.5% → ratio gain/perte de 1.5×
      - Max 5 positions → concentration contrôlée
      - Drawdown max 5% → pause forcée si perte globale excessive
    
    Toutes les décisions de sortie d'urgence passent par ce module.
    """

    def __init__(self, capital_initial: float = CAPITAL_INITIAL):
        self.capital_initial = capital_initial
        self.logger = logging.getLogger("GestionnaireRisque")

    def verifier_stop_loss(self, position: dict) -> bool:
        """
        Retourne True si la position a atteint le stop-loss (perte >= 3%).
        La perte est exprimée en pourcentage négatif dans unrealized_plpc.
        """
        plpc = position.get("unrealized_plpc", 0)
        if plpc <= -(STOP_LOSS_PCT * 100):
            self.logger.warning(
                f"🛑 STOP-LOSS {position['ticker']} : "
                f"perte {plpc:.2f}% ≥ {STOP_LOSS_PCT*100:.0f}%"
            )
            return True
        return False

    def verifier_take_profit(self, position: dict) -> bool:
        """
        Retourne True si la position a atteint le take-profit (gain >= 4.5%).
        """
        plpc = position.get("unrealized_plpc", 0)
        if plpc >= (TAKE_PROFIT_PCT * 100):
            self.logger.info(
                f"✅ TAKE-PROFIT {position['ticker']} : "
                f"gain {plpc:.2f}% ≥ {TAKE_PROFIT_PCT*100:.0f}%"
            )
            return True
        return False

    def verifier_drawdown(self, equity_actuelle: float) -> bool:
        """
        Retourne True si le drawdown depuis le capital initial dépasse MAX_DRAWDOWN_PCT.
        Déclenche une pause forcée du bot.
        """
        drawdown = (self.capital_initial - equity_actuelle) / self.capital_initial
        if drawdown >= MAX_DRAWDOWN_PCT:
            self.logger.error(
                f"🚨 DRAWDOWN MAX {drawdown*100:.2f}% ≥ {MAX_DRAWDOWN_PCT*100:.0f}% "
                f"— Pause forcée du bot"
            )
            return True
        return False

    def capital_suffisant(self, compte: dict) -> bool:
        """Vérifie qu'on a assez de buying power pour un nouveau trade."""
        return compte.get("buying_power", 0) >= ALLOCATION_PAR_TRADE

    def positions_disponibles(self, nb_positions: int) -> bool:
        """Vérifie qu'on n'a pas atteint le maximum de positions simultanées."""
        return nb_positions < MAX_POSITIONS


# =============================================================================
# [MODULE 6] BotRoute — Orchestrateur principal
# =============================================================================

class BotRoute:
    """
    Assemble tous les modules et exécute la boucle de décision complète.
    
    Cycle d'exécution pour chaque actif :
      1. Récupère les barres OHLC depuis Alpaca
      2. Calcule SMA + RSI → signal technique
      3. Score le sentiment de marché via Claude
      4. Applique le filtre risque (drawdown, positions max, capital)
      5. Valide la décision finale :
           BUY  : signal=BUY  ET sentiment>0.5 ET risque OK
           SELL : signal=SELL OU sentiment<0.3 OU stop-loss/take-profit atteint
      6. Exécute l'ordre via Alpaca
      7. Exporte l'état complet vers data/etat_bot.json
    """

    def __init__(self):
        self.alpaca    = AlpacaClient()
        self.technique = MoteurTechnique()
        self.sentiment = AnalyseurSentiment()
        self.risque    = GestionnaireRisque()
        self.logger    = logging.getLogger("BotRoute")
        self.cycle     = 0
        self.historique_trades: list = []
        self.historique_valeur: list = []
        self.decisions_cycle: list   = []
        self.pause_drawdown: bool    = False
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── Vérifications de sortie d'urgence sur positions ouvertes ─────────────
    def _auditer_positions(self, positions: dict):
        """
        Parcourt toutes les positions ouvertes et applique stop-loss / take-profit.
        Exécuté en priorité absolue au début de chaque cycle.
        """
        for ticker, pos in list(positions.items()):
            if self.risque.verifier_stop_loss(pos) or self.risque.verifier_take_profit(pos):
                try:
                    self.alpaca.vendre_position(ticker)
                    raison = (
                        f"STOP-LOSS {pos['unrealized_plpc']:.2f}%"
                        if pos["unrealized_plpc"] <= -(STOP_LOSS_PCT * 100)
                        else f"TAKE-PROFIT {pos['unrealized_plpc']:.2f}%"
                    )
                    trade = {
                        "type":       "VENTE_AUTO",
                        "ticker":     ticker,
                        "prix":       pos["current_price"],
                        "pnl":        round(pos["unrealized_pl"], 2),
                        "pnl_pct":    round(pos["unrealized_plpc"], 2),
                        "raison":     raison,
                        "timestamp":  datetime.now(timezone.utc).isoformat(),
                    }
                    self.historique_trades.append(trade)
                    self.logger.info(f"[VENTE AUTO] {ticker} — {raison}")
                except Exception as e:
                    self.logger.error(f"Erreur vente auto {ticker} : {e}")

    # ── Analyse d'un actif et décision ────────────────────────────────────────
    def _analyser_actif(self, ticker: str, positions: dict, compte: dict) -> dict:
        """
        Pipeline complet pour un actif : données → technique → sentiment → décision.
        Retourne un dict de décision enrichi.
        """
        decision = {
            "ticker":              ticker,
            "action_executee":     "AUCUNE",
            "signal_technique":    "HOLD",
            "score_sentiment":     0.55,
            "resume_sentiment":    "",
            "raison_technique":    "",
            "raison_finale":       "",
            "prix":                None,
            "rsi":                 None,
            "sma_court":           None,
            "sma_long":            None,
            "timestamp":           datetime.now(timezone.utc).isoformat(),
        }

        # ── 1. Données OHLC ──────────────────────────────────────────────────
        try:
            barres = self.alpaca.get_barres(ticker)
        except Exception as e:
            decision["raison_finale"] = f"Erreur données OHLC : {e}"
            return decision

        if len(barres) < 20:
            decision["raison_finale"] = "Pas assez de barres (marché fermé ?)"
            return decision

        # ── 2. Analyse technique ─────────────────────────────────────────────
        tech = self.technique.analyser(ticker, barres)
        decision.update({
            "signal_technique": tech["signal"],
            "raison_technique": tech["raison"],
            "prix":             tech["prix"],
            "rsi":              tech["rsi"],
            "sma_court":        tech["sma_court"],
            "sma_long":         tech["sma_long"],
        })

        # ── 3. Score de sentiment (uniquement si signal non HOLD) ────────────
        sent = {"score": 0.55, "resume": "Non analysé (signal HOLD)"}
        if tech["signal"] in ("BUY", "SELL"):
            sent = self.sentiment.scorer(ticker, tech["prix"], tech["rsi"])
        decision["score_sentiment"]  = sent["score"]
        decision["resume_sentiment"] = sent["resume"]

        # ── 4. Logique de décision finale ────────────────────────────────────
        dans_position = ticker in positions

        if tech["signal"] == "BUY" and not dans_position:
            # Filtre sentiment : achat uniquement si marché > 50% optimiste
            if sent["score"] < SEUIL_SENTIMENT_ACHAT:
                decision["raison_finale"] = (
                    f"Signal BUY bloqué : sentiment trop bas ({sent['score']:.2f} < {SEUIL_SENTIMENT_ACHAT})"
                )
            elif self.pause_drawdown:
                decision["raison_finale"] = "Pause forcée (drawdown max atteint)"
            elif not self.risque.positions_disponibles(len(positions)):
                decision["raison_finale"] = f"Max positions atteint ({MAX_POSITIONS})"
            elif not self.risque.capital_suffisant(compte):
                decision["raison_finale"] = "Capital insuffisant"
            else:
                # ── ACHAT ────────────────────────────────────────────────────
                try:
                    ordre = self.alpaca.acheter_market(ticker, ALLOCATION_PAR_TRADE)
                    decision["action_executee"] = "ACHAT"
                    decision["raison_finale"]   = (
                        f"BUY validé : {tech['raison']} | sentiment {sent['score']:.2f}"
                    )
                    trade = {
                        "type":      "ACHAT",
                        "ticker":    ticker,
                        "montant":   ALLOCATION_PAR_TRADE,
                        "prix":      tech["prix"],
                        "rsi":       tech["rsi"],
                        "sentiment": sent["score"],
                        "ordre_id":  ordre.get("id", ""),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    self.historique_trades.append(trade)
                    self.logger.info(
                        f"[ACHAT] {ticker} @ ${tech['prix']:.2f} | "
                        f"RSI {tech['rsi']:.1f} | sentiment {sent['score']:.2f}"
                    )
                except Exception as e:
                    decision["raison_finale"] = f"Erreur ordre achat : {e}"
                    self.logger.error(f"Erreur achat {ticker} : {e}")

        elif tech["signal"] == "SELL" and dans_position:
            # Filtre panique : vente si signal baissier OU sentiment en panique
            if sent["score"] < SEUIL_SENTIMENT_VENTE or tech["signal"] == "SELL":
                try:
                    self.alpaca.vendre_position(ticker)
                    pos = positions[ticker]
                    decision["action_executee"] = "VENTE"
                    decision["raison_finale"]   = (
                        f"SELL validé : {tech['raison']} | sentiment {sent['score']:.2f}"
                    )
                    trade = {
                        "type":      "VENTE",
                        "ticker":    ticker,
                        "prix":      tech["prix"],
                        "pnl":       round(pos.get("unrealized_pl", 0), 2),
                        "pnl_pct":   round(pos.get("unrealized_plpc", 0), 2),
                        "sentiment": sent["score"],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    self.historique_trades.append(trade)
                    self.logger.info(
                        f"[VENTE] {ticker} @ ${tech['prix']:.2f} | "
                        f"P&L {pos.get('unrealized_plpc',0):.2f}%"
                    )
                except Exception as e:
                    decision["raison_finale"] = f"Erreur ordre vente : {e}"
                    self.logger.error(f"Erreur vente {ticker} : {e}")
            else:
                decision["raison_finale"] = (
                    f"SELL différé : sentiment encore élevé ({sent['score']:.2f}) "
                    f"— attente pour maximiser le gain"
                )
        else:
            if not decision["raison_finale"]:
                decision["raison_finale"] = tech["raison"]

        return decision

    # ── Cycle principal ────────────────────────────────────────────────────────
    def run_cycle(self) -> dict:
        """
        Exécute un cycle complet sur tous les actifs de la liste.
        Retourne l'état complet sérialisable en JSON.
        """
        self.cycle += 1
        self.logger.info(f"=== Cycle #{self.cycle} — {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')} ===")

        # ── Récupération de l'état du compte et des positions ────────────────
        try:
            compte    = self.alpaca.get_compte()
            positions = self.alpaca.get_positions()
        except Exception as e:
            self.logger.error(f"Erreur connexion Alpaca : {e}")
            return self._etat_erreur(str(e))

        # ── Vérification drawdown (sécurité absolue) ─────────────────────────
        self.pause_drawdown = self.risque.verifier_drawdown(compte["equity"])

        # ── Audit stop-loss / take-profit sur positions existantes ───────────
        self._auditer_positions(positions)

        # ── Analyse de chaque actif ──────────────────────────────────────────
        decisions = []
        for ticker in ACTIFS:
            # Petite pause pour éviter le rate-limiting Alpaca
            time.sleep(0.3)
            d = self._analyser_actif(ticker, positions, compte)
            decisions.append(d)
            if d["action_executee"] != "AUCUNE":
                self.logger.info(
                    f"  → {d['action_executee']} {ticker} | {d['raison_finale']}"
                )

        # ── Snapshot de la valeur du portefeuille ────────────────────────────
        self.historique_valeur.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "equity":    compte["equity"],
            "cash":      compte["cash"],
        })
        self.historique_valeur = self.historique_valeur[-100:]
        self.historique_trades = self.historique_trades[-50:]

        # Re-lecture des positions après les ordres du cycle
        try:
            positions_apres = self.alpaca.get_positions()
        except Exception:
            positions_apres = positions

        pnl_total     = compte["equity"] - self.risque.capital_initial
        pnl_total_pct = (pnl_total / self.risque.capital_initial) * 100

        etat = {
            "meta": {
                "cycle":       self.cycle,
                "timestamp":   datetime.now(timezone.utc).isoformat(),
                "bot_version": "2.0.0-route",
                "broker":      "Alpaca Paper Trading",
                "mode":        "PAPER",
                "pause_drawdown": self.pause_drawdown,
            },
            "portefeuille": {
                "capital_initial":    self.risque.capital_initial,
                "equity":             round(compte["equity"], 2),
                "cash":               round(compte["cash"], 2),
                "buying_power":       round(compte["buying_power"], 2),
                "pnl_total":          round(pnl_total, 2),
                "pnl_total_pct":      round(pnl_total_pct, 2),
                "positions":          positions_apres,
                "nb_positions":       len(positions_apres),
                "historique_trades":  self.historique_trades,
                "historique_valeur":  self.historique_valeur,
            },
            "decisions_cycle": decisions,
            "config": {
                "stop_loss_pct":    STOP_LOSS_PCT * 100,
                "take_profit_pct":  TAKE_PROFIT_PCT * 100,
                "max_positions":    MAX_POSITIONS,
                "allocation_trade": ALLOCATION_PAR_TRADE,
                "seuil_sentiment":  SEUIL_SENTIMENT_ACHAT,
                "nb_actifs":        len(ACTIFS),
            },
        }

        # Résumé console
        achats  = sum(1 for d in decisions if d["action_executee"] == "ACHAT")
        ventes  = sum(1 for d in decisions if d["action_executee"] == "VENTE")
        self.logger.info(
            f"Cycle #{self.cycle} terminé | "
            f"Equity : ${compte['equity']:,.2f} | "
            f"P&L : {'+' if pnl_total>=0 else ''}{pnl_total:,.2f}$ ({pnl_total_pct:+.2f}%) | "
            f"Achats : {achats} | Ventes : {ventes} | "
            f"Positions : {len(positions_apres)}/{MAX_POSITIONS}"
        )
        return etat

    def _etat_erreur(self, message: str) -> dict:
        return {
            "meta": {
                "cycle": self.cycle,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "erreur": message,
            },
            "portefeuille": {},
            "decisions_cycle": [],
        }


# =============================================================================
# [MODULE 7] POINT D'ENTRÉE — Boucle infinie production
# =============================================================================

def configurer_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def exporter_json(etat: dict, chemin: Path) -> bool:
    """Écriture atomique du JSON (tmp → rename) pour éviter les lectures partielles."""
    tmp = chemin.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(etat, f, ensure_ascii=False, indent=2, default=str)
        tmp.replace(chemin)
        return True
    except Exception as e:
        logging.error(f"Export JSON échoué : {e}")
        if tmp.exists():
            tmp.unlink()
        return False


def main():
    configurer_logging()
    logger = logging.getLogger("main")

    logger.info("🚀 Bot Route v2 — Alpaca Paper Trading")
    logger.info(f"   Broker        : {ALPACA_BASE_URL}")
    logger.info(f"   Actifs suivis : {len(ACTIFS)}")
    logger.info(f"   Intervalle    : {INTERVALLE}s")
    logger.info(f"   Stop-loss     : {STOP_LOSS_PCT*100:.0f}% | Take-profit : {TAKE_PROFIT_PCT*100:.0f}%")
    logger.info(f"   Max positions : {MAX_POSITIONS} × {ALLOCATION_PAR_TRADE:.0f}$")

    if not ALPACA_API_KEY:
        logger.error("❌  ALPACA_API_KEY non défini — arrêt")
        sys.exit(1)

    bot      = BotRoute()
    en_cours = True

    def stopper(sig, frame):
        nonlocal en_cours
        logger.info(f"Signal {sig} reçu — arrêt propre...")
        en_cours = False

    signal.signal(signal.SIGINT,  stopper)
    signal.signal(signal.SIGTERM, stopper)

    while en_cours:
        try:
            etat   = bot.run_cycle()
            succes = exporter_json(etat, JSON_SORTIE)
            logger.info(f"Export JSON : {'✓' if succes else '✗'} → {JSON_SORTIE}")
        except Exception as e:
            logger.error(f"Erreur critique cycle : {e}", exc_info=True)

        # Attente interruptible par signal
        for _ in range(INTERVALLE):
            if not en_cours:
                break
            time.sleep(1)

    logger.info("Bot arrêté proprement.")


if __name__ == "__main__":
    main()
