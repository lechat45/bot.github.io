“””
╔══════════════════════════════════════════════════════════════════════════════╗
║           SACHA INVESTOR BOT  —  VERSION PRO  —  v4.0                      ║
║                                                                              ║
║  Stratégies :                                                                ║
║   • Analyse technique : RSI, EMA9/21/50, MACD, Bollinger Bands, ATR        ║
║   • Volume : OBV, Volume Spike                                               ║
║   • Sentiment IA : Groq LLaMA 3 (contexte macro + indicateurs)             ║
║   • Régime de marché : tendance / range / volatile                          ║
║   • Scoring multi-signal pondéré (technique + IA)                           ║
║   • Gestion du risque : Kelly Criterion, position sizing dynamique          ║
║   • Stops dynamiques : Trailing Stop (-6%), ATR Stop, ATR Target            ║
║   • Filtre circuit breaker : arrêt si drawdown journalier > 4%              ║
║   • Mémoire persistante : win rate et profit factor calculés en live        ║
╚══════════════════════════════════════════════════════════════════════════════╝
“””

import os, time, json, requests
import alpaca_trade_api as tradeapi
from datetime import datetime, timedelta, timezone
from statistics import mean, stdev

os.environ[‘TZ’] = ‘Europe/Paris’
try:
time.tzset()
except Exception:
pass

# ══════════════════════════════════════════════════════════════════════════════

# INDICATEURS TECHNIQUES  (sans librairie externe)

# ══════════════════════════════════════════════════════════════════════════════

def ema(valeurs, periode):
if len(valeurs) < periode:
return []
k = 2 / (periode + 1)
result = [mean(valeurs[:periode])]
for v in valeurs[periode:]:
result.append(v * k + result[-1] * (1 - k))
return result

def rsi(fermetures, periode=14):
if len(fermetures) < periode + 1:
return None
deltas = [fermetures[i+1] - fermetures[i] for i in range(len(fermetures)-1)]
gains  = [max(d, 0)   for d in deltas[-periode:]]
pertes = [abs(min(d,0)) for d in deltas[-periode:]]
avg_g  = mean(gains)
avg_l  = mean(pertes)
if avg_l == 0:
return 100.0
return round(100 - 100 / (1 + avg_g / avg_l), 2)

def macd(fermetures):
e12 = ema(fermetures, 12)
e26 = ema(fermetures, 26)
if not e12 or not e26:
return None
diff = len(e12) - len(e26)
e12s = e12[diff:] if diff > 0 else e12
line = [e12s[i] - e26[i] for i in range(len(e26))]
sig  = ema(line, 9)
if not sig:
return None
return {
“macd”:        round(line[-1], 4),
“signal”:      round(sig[-1], 4),
“histogramme”: round(line[-1] - sig[-1], 4)
}

def bollinger(fermetures, periode=20, k=2.0):
if len(fermetures) < periode:
return None
w   = fermetures[-periode:]
m   = mean(w)
std = stdev(w)
haut = m + k * std
bas  = m - k * std
prix = fermetures[-1]
pct_b   = (prix - bas) / (haut - bas) if haut != bas else 0.5
largeur = (haut - bas) / m * 100
return {“haut”: round(haut, 2), “milieu”: round(m, 2), “bas”: round(bas, 2),
“pct_b”: round(pct_b, 3), “largeur”: round(largeur, 2)}

def atr(highs, lows, closes, periode=14):
if len(closes) < periode + 1:
return None
tr_list = []
for i in range(1, len(closes)):
tr = max(highs[i] - lows[i],
abs(highs[i] - closes[i-1]),
abs(lows[i]  - closes[i-1]))
tr_list.append(tr)
return round(mean(tr_list[-periode:]), 4)

def obv_haussier(fermetures, volumes):
if len(fermetures) < 6 or len(volumes) < 6:
return False
v = [0]
for i in range(1, len(fermetures)):
if fermetures[i] > fermetures[i-1]:
v.append(v[-1] + volumes[i])
elif fermetures[i] < fermetures[i-1]:
v.append(v[-1] - volumes[i])
else:
v.append(v[-1])
return v[-1] > v[-5]

def volume_spike(volumes, seuil=1.5):
if len(volumes) < 21:
return False
avg = mean(volumes[-21:-1])
return volumes[-1] > avg * seuil if avg > 0 else False

def kelly_size(win_rate, gain_m, perte_m, capital, max_pct=0.20):
if perte_m <= 0 or gain_m <= 0:
return capital * 0.05
b = gain_m / perte_m
p = win_rate
q = 1 - p
k = (b * p - q) / b
k = k / 4  # 1/4 Kelly conservateur
k = max(0.02, min(k, max_pct))
return round(capital * k, 2)

# ══════════════════════════════════════════════════════════════════════════════

# BOT PRINCIPAL

# ══════════════════════════════════════════════════════════════════════════════

class BotProIA:

```
UNIVERS = ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN",
           "TSLA", "AMD", "PLTR", "COIN", "SPY", "QQQ"]

TRAILING_STOP_PCT    = 0.06
STOP_LOSS_ATR_MULT   = 2.0
TAKE_PROFIT_ATR_MULT = 4.0
MAX_POSITIONS        = 4
MAX_DRAWDOWN_JOUR    = 0.04
CAPITAL_MIN_RESERVE  = 200
SCORE_MIN_ACHAT      = 62
MAX_PCT_PAR_POSITION = 0.15

def __init__(self):
    self.alpaca_key    = os.environ.get("ALPACA_API_KEY")
    self.alpaca_secret = os.environ.get("ALPACA_SECRET_KEY")
    self.groq_key      = os.environ.get("GROQ_API_KEY")
    for nom, val in [("ALPACA_API_KEY", self.alpaca_key),
                     ("ALPACA_SECRET_KEY", self.alpaca_secret),
                     ("GROQ_API_KEY", self.groq_key)]:
        if not val:
            raise EnvironmentError(f"Secret manquant : {nom}")

    self.api = tradeapi.REST(self.alpaca_key, self.alpaca_secret,
                             "https://paper-api.alpaca.markets", api_version='v2')
    self.journal     = []
    self.indicateurs = {}
    self._charger_memoire()

# ── Mémoire ───────────────────────────────────────────────────────────────

def _charger_memoire(self):
    try:
        with open("data/memoire_trades.json") as f:
            self.mem = json.load(f)
    except Exception:
        self.mem = {"trades": [], "peaks": {}, "atr_achat": {}}

def _sauvegarder_memoire(self):
    os.makedirs("data", exist_ok=True)
    with open("data/memoire_trades.json", "w") as f:
        json.dump(self.mem, f, indent=2)

def _win_rate(self):
    t = self.mem["trades"][-50:]
    if len(t) < 5:
        return 0.5
    return sum(1 for x in t if x.get("pnl", 0) > 0) / len(t)

def _gain_perte(self):
    t = self.mem["trades"][-50:]
    g = [x["pnl"] for x in t if x.get("pnl", 0) > 0]
    p = [abs(x["pnl"]) for x in t if x.get("pnl", 0) <= 0]
    return (mean(g) if g else 0.05, mean(p) if p else 0.03)

# ── Log ───────────────────────────────────────────────────────────────────

def log(self, msg, niv="INFO"):
    ts   = datetime.now().strftime("%H:%M:%S")
    icns = {"INFO":"ℹ️","OK":"✅","WARN":"⚠️","SELL":"💰","STOP":"🛑",
            "BUY":"📈","ERR":"❌","IA":"🤖"}
    self.journal.append({"heure": ts, "message": f"{icns.get(niv,'•')} {msg}", "niveau": niv})
    print(f"[{ts}] {msg}")
    if len(self.journal) > 80:
        self.journal.pop(0)

# ── Données marché ────────────────────────────────────────────────────────

def _get_barres(self, ticker, nb=60):
    try:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=nb * 2)
        df    = self.api.get_bars(
            ticker, tradeapi.rest.TimeFrame.Day,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"), limit=nb
        ).df
        if df.empty or len(df) < 20:
            return None
        return {"closes": df["close"].tolist(), "highs": df["high"].tolist(),
                "lows": df["low"].tolist(), "volumes": df["volume"].tolist()}
    except Exception as e:
        self.log(f"Données {ticker}: {e}", "ERR")
        return None

# ── Indicateurs techniques ────────────────────────────────────────────────

def _analyser_technique(self, ticker, data):
    closes, highs, lows, volumes = (data["closes"], data["highs"],
                                     data["lows"], data["volumes"])
    prix = closes[-1]
    score = 0
    ind   = {}

    # EMA tendance (30 pts)
    e9  = ema(closes, 9)
    e21 = ema(closes, 21)
    e50 = ema(closes, 50)
    s_ema = 0
    if e9 and e21 and e50:
        if prix > e9[-1]:    s_ema += 8
        if e9[-1] > e21[-1]: s_ema += 8
        if e21[-1] > e50[-1]: s_ema += 8
        if len(e21) >= 3 and e21[-1] > e21[-3]: s_ema += 6
    ind["ema"] = {"ema9": round(e9[-1],2) if e9 else None,
                  "ema21": round(e21[-1],2) if e21 else None,
                  "ema50": round(e50[-1],2) if e50 else None}
    score += s_ema

    # RSI + MACD (25 pts)
    rsi_v  = rsi(closes)
    macd_v = macd(closes)
    s_mom  = 0
    if rsi_v is not None:
        if 45 <= rsi_v <= 65:  s_mom += 10
        elif 35 <= rsi_v < 45: s_mom += 6
        elif rsi_v < 35:       s_mom += 3
    if macd_v:
        if macd_v["histogramme"] > 0: s_mom += 10
        if macd_v["macd"] > macd_v["signal"]: s_mom += 5
    ind["rsi"]  = rsi_v
    ind["macd"] = macd_v
    score += s_mom

    # Volume (15 pts)
    spike = volume_spike(volumes)
    obv_h = obv_haussier(closes, volumes)
    s_vol = (8 if obv_h else 0) + (7 if spike else 0)
    ind["volume"] = {"spike": spike, "obv_haussier": obv_h}
    score += s_vol

    # Bollinger (15 pts)
    boll  = bollinger(closes)
    s_bol = 0
    if boll:
        if 0.2 <= boll["pct_b"] <= 0.6: s_bol += 10
        elif boll["pct_b"] < 0.2:        s_bol += 7
        if boll["largeur"] < 5.0:        s_bol += 5
    ind["bollinger"]      = boll
    ind["score_bollinger"] = s_bol
    score += s_bol

    ind["atr"]             = atr(highs, lows, closes)
    ind["prix"]            = round(prix, 2)
    ind["score_technique"] = min(score, 85)
    return ind

def _regime(self, closes):
    if len(closes) < 30:
        return "INCONNU"
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    if not e20 or not e50:
        return "INCONNU"
    rsi_v  = rsi(closes) or 50
    vol_cv = stdev(closes[-20:]) / mean(closes[-20:]) * 100
    if vol_cv > 5:
        return "VOLATILE"
    if e20[-1] > e50[-1] and rsi_v > 50:
        return "TENDANCE_HAUSSIERE"
    if e20[-1] < e50[-1] and rsi_v < 50:
        return "TENDANCE_BAISSIERE"
    return "RANGE"

# ── Analyse IA ────────────────────────────────────────────────────────────

def _ia(self, ticker, prix, ind):
    prompt = (
        f"Tu es un trader quantitatif senior. Analyse {ticker} @ {prix}$.\n"
        f"RSI={ind.get('rsi','N/A')} | MACD histo={ind.get('macd',{}).get('histogramme','N/A')}"
        f" | Bollinger %B={ind.get('bollinger',{}).get('pct_b','N/A')}"
        f" | EMA9={ind.get('ema',{}).get('ema9','N/A')}"
        f" | Vol spike={ind.get('volume',{}).get('spike',False)}"
        f" | ATR={ind.get('atr','N/A')}\n"
        f"Score additionnel de conviction 0-15. Setup bullish ?\n"
        f"JSON UNIQUEMENT: {{\"score_ia\":10,\"setup\":\"10 mots max\",\"risque\":\"8 mots max\"}}"
    )
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.groq_key}", "Content-Type": "application/json"},
            json={"model": "llama3-8b-8192",
                  "messages": [{"role": "user", "content": prompt}],
                  "response_format": {"type": "json_object"},
                  "temperature": 0.2, "max_tokens": 120},
            timeout=15
        )
        r.raise_for_status()
        d = json.loads(r.json()["choices"][0]["message"]["content"])
        return {"score_ia": max(0, min(15, int(d.get("score_ia", 7)))),
                "setup":    str(d.get("setup", ""))[:80],
                "risque":   str(d.get("risque", ""))[:60]}
    except Exception as e:
        self.log(f"Groq {ticker}: {e}", "WARN")
        return {"score_ia": 7, "setup": "IA indisponible", "risque": "inconnu"}

# ── Stops dynamiques ──────────────────────────────────────────────────────

def _gerer_stops(self, positions):
    peaks  = self.mem.get("peaks", {})
    atrs   = self.mem.get("atr_achat", {})
    vendus = []

    for p in positions:
        ticker = p.symbol
        actuel = float(p.current_price)
        achat  = float(p.avg_entry_price)

        # Mise à jour pic pour trailing
        if actuel > peaks.get(ticker, achat):
            peaks[ticker] = actuel
        peak  = peaks.get(ticker, achat)
        recul = (actuel - peak) / peak

        vendu = False
        pnl   = float(p.unrealized_plpc) * 100

        if recul <= -self.TRAILING_STOP_PCT:
            self._vendre(p, f"Trailing Stop {recul*100:.1f}% depuis pic | PnL: {pnl:+.1f}%", "STOP")
            self._log_trade(ticker, achat, actuel, pnl, "trailing_stop")
            vendu = True

        elif ticker in atrs:
            atr_v   = atrs[ticker]
            stop    = achat - self.STOP_LOSS_ATR_MULT * atr_v
            target  = achat + self.TAKE_PROFIT_ATR_MULT * atr_v
            if actuel <= stop:
                self._vendre(p, f"ATR Stop @ {actuel:.2f}$ (seuil: {stop:.2f}$) | PnL: {pnl:+.1f}%", "STOP")
                self._log_trade(ticker, achat, actuel, pnl, "atr_stop")
                vendu = True
            elif actuel >= target:
                self._vendre(p, f"ATR Target @ {actuel:.2f}$ | PnL: {pnl:+.1f}%", "SELL")
                self._log_trade(ticker, achat, actuel, pnl, "take_profit")
                vendu = True

        if vendu:
            vendus.append(ticker)
            peaks.pop(ticker, None)
            atrs.pop(ticker, None)

    self.mem["peaks"]    = peaks
    self.mem["atr_achat"] = atrs
    return vendus

def _vendre(self, position, msg, niv="SELL"):
    try:
        self.api.submit_order(symbol=position.symbol, qty=position.qty,
                              side='sell', type='market', time_in_force='day')
        self.log(msg, niv)
    except Exception as e:
        self.log(f"Erreur vente {position.symbol}: {e}", "ERR")

def _log_trade(self, ticker, entree, sortie, pnl, raison):
    self.mem["trades"].append({
        "ticker": ticker, "entree": round(entree, 2),
        "sortie": round(sortie, 2), "pnl": round(pnl, 2),
        "raison": raison, "date": datetime.now().strftime("%Y-%m-%d %H:%M")
    })
    if len(self.mem["trades"]) > 200:
        self.mem["trades"] = self.mem["trades"][-200:]

# ── Circuit breaker ───────────────────────────────────────────────────────

def _circuit_breaker(self, compte):
    eq = float(compte.equity)
    le = float(compte.last_equity)
    if le <= 0:
        return False
    dd = (eq - le) / le
    if dd <= -self.MAX_DRAWDOWN_JOUR:
        self.log(f"CIRCUIT BREAKER: drawdown {dd*100:.1f}% > {self.MAX_DRAWDOWN_JOUR*100:.0f}%", "STOP")
        return True
    return False

# ── Scoring complet ───────────────────────────────────────────────────────

def _scorer(self, ticker):
    data = self._get_barres(ticker)
    if not data:
        return None
    regime = self._regime(data["closes"])
    if regime == "TENDANCE_BAISSIERE":
        return None

    ind       = self._analyser_technique(ticker, data)
    ia        = self._ia(ticker, ind["prix"], ind)
    score     = min(100, ind["score_technique"] + ia["score_ia"])
    ind["ia"] = ia
    ind["score_total"] = score
    ind["regime"]      = regime
    self.indicateurs[ticker] = ind

    self.log(
        f"{ticker} @ {ind['prix']:.2f}$ | {score}/100"
        f" (tech:{ind['score_technique']} ia:{ia['score_ia']})"
        f" | {regime} | {ia['setup']}", "IA"
    )
    return {"ticker": ticker, "prix": ind["prix"], "score": score, "ind": ind}

# ── Exécution ─────────────────────────────────────────────────────────────

def executer(self):
    try:
        clock  = self.api.get_clock()
        compte = self.api.get_account()
        equity = float(compte.portfolio_value)
        wr     = self._win_rate()
        self.log(f"Portfolio: {equity:.2f}$ | Win rate: {wr*100:.0f}% | "
                 f"Marché: {'OUVERT' if clock.is_open else 'FERMÉ'}", "INFO")

        if self._circuit_breaker(compte):
            return self._export(clock, compte)

        # Gestion des stops
        positions = self.api.list_positions()
        self._gerer_stops(positions)

        # Reload
        positions      = self.api.list_positions()
        en_portfolio   = {p.symbol for p in positions}
        nb_pos         = len(positions)
        cash           = float(self.api.get_account().cash)

        # Analyse univers
        candidats = []
        for ticker in self.UNIVERS:
            try:
                r = self._scorer(ticker)
                if r and ticker not in en_portfolio and r["score"] >= self.SCORE_MIN_ACHAT:
                    candidats.append(r)
            except Exception as e:
                self.log(f"Erreur {ticker}: {e}", "ERR")

        candidats.sort(key=lambda x: x["score"], reverse=True)

        # Achats
        if clock.is_open:
            gain_m, perte_m = self._gain_perte()
            for c in candidats:
                if nb_pos >= self.MAX_POSITIONS: break
                if cash < self.CAPITAL_MIN_RESERVE + 50: break

                montant = kelly_size(wr, gain_m, perte_m,
                                    cash - self.CAPITAL_MIN_RESERVE,
                                    self.MAX_PCT_PAR_POSITION)
                montant = max(50, min(montant, cash - self.CAPITAL_MIN_RESERVE))

                try:
                    self.api.submit_order(symbol=c["ticker"], notional=montant,
                                          side='buy', type='market', time_in_force='day')
                    self.log(
                        f"Achat {c['ticker']} {montant:.0f}$ "
                        f"(score:{c['score']}/100 | {montant/equity*100:.1f}% portfolio)", "BUY"
                    )
                    self.mem["atr_achat"][c["ticker"]] = c["ind"].get("atr") or 0
                    self.mem["peaks"][c["ticker"]]     = c["prix"]
                    nb_pos += 1
                    cash   -= montant
                except Exception as e:
                    self.log(f"Erreur achat {c['ticker']}: {e}", "ERR")
        else:
            self.log("Marché fermé — analyse uniquement", "INFO")

        self._sauvegarder_memoire()
        return self._export(clock, compte)

    except Exception as e:
        self.log(f"Erreur critique: {e}", "ERR")
        import traceback; traceback.print_exc()
        return self._export_erreur()

# ── Export JSON ───────────────────────────────────────────────────────────

def _export(self, clock, compte):
    eq = float(compte.equity)
    le = float(compte.last_equity)
    pnl = eq - le if le > 0 else 0

    hist = []
    try:
        with open("data/etat_bot.json") as f:
            hist = json.load(f).get("historique", [])
    except Exception:
        pass
    hist.append({"date": datetime.now().strftime("%d/%m %H:%M"),
                 "valeur": round(eq, 2)})
    if len(hist) > 100: hist = hist[-100:]

    trades    = self.mem.get("trades", [])
    wr        = self._win_rate()
    gain_m, perte_m = self._gain_perte()
    pf = (gain_m * wr) / (perte_m * (1-wr)) if perte_m > 0 and 0 < wr < 1 else 0

    scores = {}
    for tk, ind in self.indicateurs.items():
        scores[tk] = {
            "score":        ind.get("score_total", 0),
            "score_tech":   ind.get("score_technique", 0),
            "score_ia":     ind.get("ia", {}).get("score_ia", 0),
            "rsi":          ind.get("rsi"),
            "regime":       ind.get("regime", ""),
            "setup":        ind.get("ia", {}).get("setup", ""),
            "risque":       ind.get("ia", {}).get("risque", ""),
            "ema9":         ind.get("ema", {}).get("ema9"),
            "ema21":        ind.get("ema", {}).get("ema21"),
            "atr":          ind.get("atr"),
            "volume_spike": ind.get("volume", {}).get("spike", False),
        }

    pos_finales = self.api.list_positions()
    return {
        "meta": {
            "mode":          "⚡ LIVE" if clock.is_open else "🧪 MARCHÉ FERMÉ",
            "cycle":         int(time.time()),
            "derniere_maj":  datetime.now().strftime("%d/%m %H:%M:%S"),
            "nb_analyses":   len(self.indicateurs),
            "strategie":     "Multi-Signal Pro v4.0"
        },
        "portefeuille": {
            "valeur_totale":      round(float(compte.portfolio_value), 2),
            "capital_disponible": round(float(compte.cash), 2),
            "pnl_total":         round(pnl, 2),
            "pnl_pct":           round(pnl / le * 100 if le > 0 else 0, 2),
            "positions": [{
                "ticker":      p.symbol,
                "quantite":    round(float(p.qty), 4),
                "pnl_pct":     round(float(p.unrealized_plpc) * 100, 2),
                "prix_achat":  round(float(p.avg_entry_price), 2),
                "prix_actuel": round(float(p.current_price), 2),
                "valeur":      round(float(p.market_value), 2),
                "score":       scores.get(p.symbol, {}).get("score", 0)
            } for p in pos_finales]
        },
        "performance": {
            "win_rate":      round(wr * 100, 1),
            "gain_moyen":    round(gain_m * 100, 2),
            "perte_moyenne": round(perte_m * 100, 2),
            "profit_factor": round(pf, 2),
            "nb_trades":     len(trades)
        },
        "scores":     scores,
        "analyses":   self.journal,
        "historique": hist
    }

def _export_erreur(self):
    return {
        "meta": {"mode":"❌ ERREUR","cycle":int(time.time()),
                 "derniere_maj":datetime.now().strftime("%d/%m %H:%M:%S"),
                 "nb_analyses":0,"strategie":"Multi-Signal Pro v4.0"},
        "portefeuille":{"valeur_totale":0,"capital_disponible":0,
                        "pnl_total":0,"pnl_pct":0,"positions":[]},
        "performance":{"win_rate":0,"gain_moyen":0,"perte_moyenne":0,
                       "profit_factor":0,"nb_trades":0},
        "scores":{},"analyses":self.journal,"historique":[]
    }
```

# ══════════════════════════════════════════════════════════════════════════════

if **name** == “**main**”:
bot    = BotProIA()
result = bot.executer()
os.makedirs(“data”, exist_ok=True)
with open(“data/etat_bot.json”, “w”, encoding=“utf-8”) as f:
json.dump(result, f, indent=2, ensure_ascii=False)
print(“✅ etat_bot.json mis à jour.”)
