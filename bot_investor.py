import os
import time
import json
import requests
import alpaca_trade_api as tradeapi
from datetime import datetime
from pathlib import Path

class BotAlpacaIA:
    def __init__(self):
        # Configuration des clés
        self.alpaca_key = os.environ.get("ALPACA_API_KEY")
        self.alpaca_secret = os.environ.get("ALPACA_SECRET_KEY")
        self.groq_key = "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"
        
        self.api = tradeapi.REST(self.alpaca_key, self.alpaca_secret, "https://paper-api.alpaca.markets", api_version='v2')
        self.journal = []

    def ajouter_log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.journal.append({"heure": timestamp, "message": message})

    def analyser_sentiment_groq(self, ticker):
        """Demande à l'IA Groq d'analyser le sentiment sur X (Simulation de flux)"""
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.groq_key}", "Content-Type": "application/json"}
        
        prompt = f"Analyse les derniers posts sur X (Twitter) avec le cashtag ${ticker}. Donne-moi uniquement un chiffre entre 1 et 10 représentant l'enthousiasme des investisseurs (10 = achat immédiat, 1 = panique). Réponds juste par le chiffre."
        
        try:
            response = requests.post(url, headers=headers, json={
                "model": "llama3-8b-8192",
                "messages": [{"role": "user", "content": prompt}]
            })
            score = int(response.json()['choices'][0]['message']['content'].strip())
            return score
        except:
            return 5 # Score neutre par défaut en cas d'erreur

    def executer(self):
        try:
            clock = self.api.get_clock()
            compte = self.api.get_account()
            ticker = "AAPL"
            
            # 1. Analyse IA
            score_ia = self.analyser_sentiment_groq(ticker)
            self.ajouter_log(f"📊 Analyse X pour ${ticker} : Score IA de {score_ia}/10")

            if not clock.is_open:
                mode = "MODE_ENTRAINEMENT_IA"
                self.ajouter_log("🌙 Marché fermé. L'IA s'entraîne sur les données de X.")
            else:
                mode = "ALPACA_PAPER_LIVE"
                # 2. Logique de décision
                if score_ia >= 7:
                    if float(compte.cash) >= 100:
                        self.api.submit_order(symbol=ticker, notional=100, side='buy', type='market', time_in_force='day')
                        self.ajouter_log(f"✅ Score {score_ia} >= 7 : Achat de 100$ de {ticker} effectué !")
                    else:
                        self.ajouter_log("⚠️ Score élevé mais cash insuffisant.")
                else:
                    self.ajouter_log(f"❌ Score {score_ia} < 7 : Achat déconseillé par l'IA.")

            # Sauvegarde pour le dashboard
            return {
                "meta": {"mode": mode, "cycle": int(time.time())},
                "portefeuille": {
                    "valeur_totale": float(compte.portfolio_value),
                    "pnl_total": float(compte.equity) - float(compte.last_equity),
                    "capital_disponible": float(compte.cash),
                    "positions": [{"ticker": p.symbol, "quantite": p.qty, "pnl_pct": float(p.unrealized_plpc)*100} for p in self.api.list_positions()]
                },
                "analyses": self.journal
            }
        except Exception as e:
            self.ajouter_log(f"Erreur : {str(e)}")

if __name__ == "__main__":
    bot = BotAlpacaIA()
    res = bot.executer()
    with open("data/etat_bot.json", "w") as f:
        json.dump(res, f, indent=2)
