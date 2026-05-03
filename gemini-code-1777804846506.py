import os
import time
import json
import requests
import alpaca_trade_api as tradeapi
from datetime import datetime

# Configuration du fuseau horaire pour la France
os.environ['TZ'] = 'Europe/Paris'
try:
    time.tzset()
except:
    pass # Pour les systèmes ne supportant pas tzset

class BotEliteIA:
    def __init__(self):
        # Clés API (Récupérées depuis GitHub Secrets)
        self.alpaca_key = os.environ.get("ALPACA_API_KEY")
        self.alpaca_secret = os.environ.get("ALPACA_SECRET_KEY")
        self.groq_key = os.environ.get("GROQ_API_KEY", "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")
        
        # Connexion Alpaca Paper
        self.api = tradeapi.REST(self.alpaca_key, self.alpaca_secret, "https://paper-api.alpaca.markets", api_version='v2')
        self.journal = []
        
        # Paramètres de l'Ingénieur
        self.STOP_LOSS_PCT = 0.05    # -5%
        self.TAKE_PROFIT_PCT = 0.10  # +10%
        self.MAX_DIVERSIFICATION = 3 # Actions simultanées max
        self.BUDGET_PAR_ACTION = 100 # Montant d'un achat en $

    def ajouter_log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {message}")
        self.journal.append({"heure": timestamp, "message": message})
        if len(self.journal) > 50: self.journal.pop(0)

    def analyser_ia_avancee(self, ticker, prix_actuel):
        """Requête sécurisée vers Groq pour l'analyse de sentiment X"""
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.groq_key}", "Content-Type": "application/json"}
        
        prompt = f"""
        Analyse financière du cashtag ${ticker} sur X (Twitter). Prix: {prix_actuel}$.
        Donne un score d'achat de 1 à 10 et une analyse courte (détecte le sarcasme).
        Répond uniquement en JSON: {{"score": 8, "analyse": "raison courte"}}
        """
        
        try:
            response = requests.post(url, headers=headers, json={
                "model": "llama3-8b-8192", 
                "messages": [{"role": "user", "content": prompt}],
                "response_format": { "type": "json_object" }
            }, timeout=10)
            return json.loads(response.json()['choices'][0]['message']['content'])
        except Exception as e:
            self.ajouter_log(f"Erreur API Groq sur {ticker}: {str(e)}")
            return {"score": 5, "analyse": "Analyse technique neutre (IA indisponible)"}

    def executer(self):
        try:
            clock = self.api.get_clock()
            compte = self.api.get_account()
            actifs = ["AAPL", "GOOGL", "TSLA"]
            
            # 1. Gestion des Sorties (Stop-Loss / Take-Profit)
            positions_actuelles = self.api.list_positions()
            for p in positions_actuelles:
                pnl = float(p.unrealized_plpc)
                if pnl <= -self.STOP_LOSS_PCT or pnl >= self.TAKE_PROFIT_PCT:
                    self.api.submit_order(symbol=p.symbol, qty=p.qty, side='sell', type='market', time_in_force='day')
                    self.ajouter_log(f"⚡ Vente auto {p.symbol}: {pnl*100:.1f}%")

            # 2. Analyse et Achat
            for ticker in actifs:
                prix = float(self.api.get_latest_bar(ticker).c)
                ia = self.analyser_ia_avancee(ticker, prix)
                self.ajouter_log(f"🤖 {ticker}: {ia['score']}/10 - {ia['analyse']}")

                if clock.is_open and ia['score'] >= 7 and len(positions_actuelles) < self.MAX_DIVERSIFICATION:
                    if float(compte.cash) >= self.BUDGET_PAR_ACTION:
                        self.api.submit_order(symbol=ticker, notional=self.BUDGET_PAR_ACTION, side='buy', type='market', time_in_force='day')
                        self.ajouter_log(f"✅ Signal fort: Achat de {ticker} ({ia['score']}/10)")

            # 3. Récupération de l'historique précédent pour le graphique
            historique = []
            try:
                with open("data/etat_bot.json", "r") as f:
                    vieux_data = json.load(f)
                    historique = vieux_data.get("historique", [])
            except: pass
            
            historique.append({"date": datetime.now().strftime("%H:%M"), "valeur": float(compte.portfolio_value)})
            if len(historique) > 50: historique.pop(0)

            # 4. Export JSON
            return {
                "meta": {
                    "mode": "⚡ LIVE" if clock.is_open else "🧪 TEST",
                    "cycle": int(time.time()),
                    "derniere_maj": datetime.now().strftime("%d/%m %H:%M:%S")
                },
                "portefeuille": {
                    "valeur_totale": float(compte.portfolio_value),
                    "capital_disponible": float(compte.cash),
                    "pnl_total": float(compte.equity) - float(compte.last_equity),
                    "positions": [
                        {
                            "ticker": p.symbol, 
                            "quantite": float(p.qty), 
                            "pnl_pct": float(p.unrealized_plpc)*100, 
                            "prix_achat": float(p.avg_entry_price)
                        } for p in self.api.list_positions()
                    ]
                },
                "analyses": self.journal,
                "historique": historique
            }
        except Exception as e:
            self.ajouter_log(f"❌ Erreur critique: {str(e)}")

if __name__ == "__main__":
    bot = BotEliteIA()
    res = bot.executer()
    os.makedirs("data", exist_ok=True)
    with open("data/etat_bot.json", "w") as f:
        json.dump(res, f, indent=2)