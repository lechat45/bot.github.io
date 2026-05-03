import os
import time
import logging
import sys
import alpaca_trade_api as tradeapi
from datetime import datetime
import json
from pathlib import Path

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("BotAlpaca")

class BotInvestisseurAlpaca:
    def __init__(self):
        # Utilisation des variables d'environnement pour la sécurité
        api_key = os.environ.get("ALPACA_API_KEY")
        api_secret = os.environ.get("ALPACA_SECRET_KEY")
        base_url = "https://paper-api.alpaca.markets" # L'extrémité paper trading
        
        if not api_key or not api_secret:
            logger.error("⚠️ Clés API Alpaca manquantes dans les variables d'environnement.")
            sys.exit(1)

        self.api = tradeapi.REST(api_key, api_secret, base_url, api_version='v2')
        self.cycle = 0

    def obtenir_etat_compte(self):
        """Récupère l'état réel du portefeuille chez Alpaca."""
        compte = self.api.get_account()
        positions = self.api.list_positions()
        
        pos_dict = {}
        for p in positions:
            pos_dict[p.symbol] = {
                "ticker": p.symbol,
                "quantite": float(p.qty),
                "prix_achat": float(p.avg_entry_price),
                "prix_courant": float(p.current_price),
                "valeur_actuelle": float(p.market_value),
                "pnl": float(p.unrealized_pl),
                "pnl_pct": float(p.unrealized_plpc) * 100
            }

        return {
            "capital_initial": float(compte.portfolio_value) - float(compte.equity), # Approximation
            "capital_disponible": float(compte.cash),
            "valeur_totale": float(compte.portfolio_value),
            "positions": pos_dict
        }

    def executer_strategie_simple(self, symbol="AAPL", montant_usd=100):
        """
        Exemple de logique d'achat. Sans l'IA, il te faut une logique mathématique.
        Ici, on achète simplement un montant fixe si on n'a pas de position.
        """
        compte = self.api.get_account()
        if float(compte.cash) < montant_usd:
            logger.warning("Fonds insuffisants.")
            return

        try:
            # Vérifier si on possède déjà l'actif pour éviter le spam d'achats
            self.api.get_position(symbol)
            logger.info(f"Position {symbol} déjà existante, attente...")
        except tradeapi.rest.APIError:
            # La position n'existe pas, on achète
            logger.info(f"Soumission d'un ordre d'achat pour {symbol}")
            self.api.submit_order(
                symbol=symbol,
                notional=montant_usd, # Acheter pour une valeur en dollars (fractional shares)
                side='buy',
                type='market',
                time_in_force='day'
            )

    def run_cycle(self):
        self.cycle += 1
        logger.info(f"=== Cycle #{self.cycle} ===")
        
        # 1. Analyser le marché et prendre des décisions (Stratégie à coder ici)
        self.executer_strategie_simple("AAPL", 50)
        self.executer_strategie_simple("TSLA", 50)
        
        # 2. Récupérer l'état mis à jour
        etat_portefeuille = self.obtenir_etat_compte()

        return {
            "meta": {
                "cycle": self.cycle,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "mode": "PAPER_TRADING_ALPACA",
            },
            "portefeuille": etat_portefeuille
        }

def main():
    bot = BotInvestisseurAlpaca()
    chemin_json = Path(os.environ.get("BOT_DATA_PATH", "data/etat_bot.json"))
    chemin_json.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        etat = bot.run_cycle()
        # Export atomique pour le dashboard HTML
        chemin_tmp = chemin_json.with_suffix(".tmp")
        with open(chemin_tmp, "w", encoding="utf-8") as f:
            json.dump(etat, f, ensure_ascii=False, indent=2)
        chemin_tmp.replace(chemin_json)
        logger.info(f"Valeur du compte : {etat['portefeuille']['valeur_totale']}$ | Export OK")
        
    except Exception as e:
        logger.error(f"Erreur d'exécution : {e}")

if __name__ == "__main__":
    main()