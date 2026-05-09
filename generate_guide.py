# -*- coding: utf-8 -*-
"""
Générateur de PDF: Guide d'Amélioration d'un Bot de Trading Algorithmique
"""

import os
import os.path
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm, mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak,
    Table, TableStyle, HRFlowable, KeepTogether
)
from reportlab.platypus.flowables import Flowable
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
import os

# ── Palette de couleurs ────────────────────────────────────────────────────────
DARK_BG       = HexColor('#0D1117')
DARK_CARD     = HexColor('#161B22')
ACCENT_BLUE   = HexColor('#58A6FF')
ACCENT_GREEN  = HexColor('#3FB950')
ACCENT_ORANGE = HexColor('#F78166')
ACCENT_PURPLE = HexColor('#BC8CFF')
ACCENT_YELLOW = HexColor('#E3B341')
TEXT_PRIMARY  = HexColor('#C9D1D9')
TEXT_MUTED    = HexColor('#8B949E')
CODE_BG       = HexColor('#1F2937')
BORDER_COLOR  = HexColor('#30363D')
WHITE         = colors.white

OUTPUT_PATH = r"C:\Users\sacha\code\bot.github.io-main\bot.github.io-main\guide_amelioration_bot.pdf"

# ── Styles personnalisés ───────────────────────────────────────────────────────
def build_styles():
    base = getSampleStyleSheet()

    styles = {}

    styles['cover_title'] = ParagraphStyle(
        'cover_title',
        fontName='Helvetica-Bold',
        fontSize=26,
        textColor=WHITE,
        leading=34,
        alignment=TA_CENTER,
        spaceAfter=10,
    )
    styles['cover_subtitle'] = ParagraphStyle(
        'cover_subtitle',
        fontName='Helvetica',
        fontSize=13,
        textColor=ACCENT_BLUE,
        leading=18,
        alignment=TA_CENTER,
        spaceAfter=6,
    )
    styles['cover_meta'] = ParagraphStyle(
        'cover_meta',
        fontName='Helvetica',
        fontSize=10,
        textColor=TEXT_MUTED,
        leading=14,
        alignment=TA_CENTER,
    )
    styles['section_header'] = ParagraphStyle(
        'section_header',
        fontName='Helvetica-Bold',
        fontSize=18,
        textColor=ACCENT_BLUE,
        leading=24,
        spaceBefore=18,
        spaceAfter=10,
    )
    styles['subsection_header'] = ParagraphStyle(
        'subsection_header',
        fontName='Helvetica-Bold',
        fontSize=13,
        textColor=ACCENT_GREEN,
        leading=18,
        spaceBefore=12,
        spaceAfter=6,
    )
    styles['body'] = ParagraphStyle(
        'body',
        fontName='Helvetica',
        fontSize=9.5,
        textColor=TEXT_PRIMARY,
        leading=15,
        spaceBefore=4,
        spaceAfter=4,
        alignment=TA_JUSTIFY,
    )
    styles['bullet'] = ParagraphStyle(
        'bullet',
        fontName='Helvetica',
        fontSize=9.5,
        textColor=TEXT_PRIMARY,
        leading=14,
        spaceBefore=2,
        spaceAfter=2,
        leftIndent=14,
        bulletIndent=4,
        bulletFontName='Helvetica',
        bulletFontSize=9.5,
    )
    styles['code'] = ParagraphStyle(
        'code',
        fontName='Courier',
        fontSize=8,
        textColor=ACCENT_GREEN,
        leading=12,
        spaceBefore=4,
        spaceAfter=4,
        leftIndent=10,
        rightIndent=10,
        backColor=CODE_BG,
    )
    styles['code_comment'] = ParagraphStyle(
        'code_comment',
        fontName='Courier',
        fontSize=8,
        textColor=TEXT_MUTED,
        leading=12,
        leftIndent=10,
        rightIndent=10,
        backColor=CODE_BG,
    )
    styles['highlight'] = ParagraphStyle(
        'highlight',
        fontName='Helvetica-Bold',
        fontSize=9.5,
        textColor=ACCENT_YELLOW,
        leading=14,
        spaceBefore=4,
        spaceAfter=4,
    )
    styles['toc_title'] = ParagraphStyle(
        'toc_title',
        fontName='Helvetica-Bold',
        fontSize=20,
        textColor=ACCENT_BLUE,
        leading=26,
        alignment=TA_CENTER,
        spaceAfter=20,
    )
    styles['toc_entry'] = ParagraphStyle(
        'toc_entry',
        fontName='Helvetica',
        fontSize=10,
        textColor=TEXT_PRIMARY,
        leading=18,
        leftIndent=20,
    )
    styles['toc_section'] = ParagraphStyle(
        'toc_section',
        fontName='Helvetica-Bold',
        fontSize=11,
        textColor=ACCENT_GREEN,
        leading=20,
        leftIndent=0,
        spaceBefore=6,
    )
    styles['caption'] = ParagraphStyle(
        'caption',
        fontName='Helvetica-Oblique',
        fontSize=8.5,
        textColor=TEXT_MUTED,
        leading=12,
        alignment=TA_CENTER,
        spaceAfter=6,
    )
    return styles


# ── Flowables personnalisés ────────────────────────────────────────────────────
class ColoredBackground(Flowable):
    """Bande de couleur pleine largeur."""
    def __init__(self, height, color):
        super().__init__()
        self.height = height
        self.color  = color
        self.width  = 0  # sera écrasé par wrap

    def wrap(self, available_width, available_height):
        self.width = available_width
        return available_width, self.height

    def draw(self):
        self.canv.setFillColor(self.color)
        self.canv.rect(0, 0, self.width, self.height, fill=1, stroke=0)


class CodeBlock(Flowable):
    """Bloc de code avec fond sombre et bordure gauche colorée."""
    def __init__(self, lines, width=None, accent=None):
        super().__init__()
        self._lines  = lines
        self._width  = width
        self._accent = accent or ACCENT_GREEN
        self.height  = 0

    def wrap(self, available_width, available_height):
        self._width = available_width
        # 12 pts per line + 16 padding
        self.height = len(self._lines) * 12 + 16
        return available_width, self.height

    def draw(self):
        c = self.canv
        w, h = self._width, self.height
        # fond
        c.setFillColor(CODE_BG)
        c.rect(0, 0, w, h, fill=1, stroke=0)
        # bordure gauche
        c.setFillColor(self._accent)
        c.rect(0, 0, 3, h, fill=1, stroke=0)
        # texte
        c.setFillColor(ACCENT_GREEN)
        c.setFont('Courier', 8)
        y = h - 12
        for line in self._lines:
            if line.startswith('#'):
                c.setFillColor(TEXT_MUTED)
            else:
                c.setFillColor(ACCENT_GREEN)
            c.drawString(12, y, line)
            y -= 12


class SectionDivider(Flowable):
    """Séparateur décoratif entre sections."""
    def __init__(self, color=None, thickness=1):
        super().__init__()
        self.color     = color or ACCENT_BLUE
        self.thickness = thickness

    def wrap(self, available_width, available_height):
        self._width = available_width
        return available_width, 8

    def draw(self):
        c = self.canv
        c.setStrokeColor(self.color)
        c.setLineWidth(self.thickness)
        c.line(0, 4, self._width, 4)


class InfoBox(Flowable):
    """Encadré informatif avec icône."""
    def __init__(self, text, color=None, label="INFO", width=None):
        super().__init__()
        self._text  = text
        self._color = color or ACCENT_BLUE
        self._label = label
        self._w     = width

    def wrap(self, available_width, available_height):
        self._w = available_width
        # rough estimate: 3 lines
        self.height = 48
        return available_width, self.height

    def draw(self):
        c = self.canv
        w, h = self._w, self.height
        c.setFillColor(HexColor('#1C2333'))
        c.roundRect(0, 0, w, h, 4, fill=1, stroke=0)
        c.setFillColor(self._color)
        c.roundRect(0, 0, 3, h, 2, fill=1, stroke=0)
        c.setFont('Helvetica-Bold', 8)
        c.setFillColor(self._color)
        c.drawString(12, h - 14, self._label)
        c.setFont('Helvetica', 8)
        c.setFillColor(TEXT_PRIMARY)
        # simple word-wrap
        words = self._text.split()
        line, lines = [], []
        max_chars = int((w - 20) / 5)
        for word in words:
            if sum(len(x)+1 for x in line) + len(word) < max_chars:
                line.append(word)
            else:
                lines.append(' '.join(line))
                line = [word]
        if line:
            lines.append(' '.join(line))
        y = h - 28
        for ln in lines[:2]:
            c.drawString(12, y, ln)
            y -= 12


# ── Page de couverture ─────────────────────────────────────────────────────────
def build_cover_page(c_obj, doc):
    w, h = A4
    # Fond noir total
    c_obj.setFillColor(DARK_BG)
    c_obj.rect(0, 0, w, h, fill=1, stroke=0)

    # Bande décorative supérieure
    c_obj.setFillColor(ACCENT_BLUE)
    c_obj.rect(0, h - 8, w, 8, fill=1, stroke=0)

    # Gradient simulé (rectangles empilés)
    for i in range(60):
        alpha = 0.6 - i * 0.01
        col = HexColor('#1A2744') if i < 30 else HexColor('#0D1117')
        c_obj.setFillColor(col)
        c_obj.rect(0, h - 8 - (i + 1) * 6, w, 6, fill=1, stroke=0)

    # Titre principal
    c_obj.setFillColor(WHITE)
    c_obj.setFont('Helvetica-Bold', 28)
    c_obj.drawCentredString(w / 2, h - 140, "Guide d'Amélioration d'un")
    c_obj.setFont('Helvetica-Bold', 30)
    c_obj.setFillColor(ACCENT_BLUE)
    c_obj.drawCentredString(w / 2, h - 178, "Bot de Trading Algorithmique")

    # Ligne décorative
    c_obj.setStrokeColor(ACCENT_BLUE)
    c_obj.setLineWidth(1.5)
    c_obj.line(60, h - 200, w - 60, h - 200)

    # Sous-titre
    c_obj.setFont('Helvetica', 12)
    c_obj.setFillColor(TEXT_MUTED)
    c_obj.drawCentredString(w / 2, h - 225,
        "Analyse complète et recommandations d'implémentation")

    # Carte de contexte du bot
    card_y = h - 390
    c_obj.setFillColor(DARK_CARD)
    c_obj.roundRect(50, card_y, w - 100, 140, 8, fill=1, stroke=0)
    c_obj.setStrokeColor(BORDER_COLOR)
    c_obj.setLineWidth(1)
    c_obj.roundRect(50, card_y, w - 100, 140, 8, fill=0, stroke=1)

    c_obj.setFont('Helvetica-Bold', 10)
    c_obj.setFillColor(ACCENT_GREEN)
    c_obj.drawString(70, card_y + 115, "CONTEXTE DU BOT ACTUEL")
    c_obj.setFont('Helvetica', 9)
    c_obj.setFillColor(TEXT_PRIMARY)
    specs = [
        ("API",         "Alpaca Trade API (Paper Trading)"),
        ("Indicateurs", "HMA(10), EMA(100/9/21), MACD(12,26,9), RSI(14), BB(20,2), ADX(14)"),
        ("IA",          "Groq Cloud + Gemini AI (analyse de sentiment)"),
        ("Exécution",   "GitHub Actions — cron toutes les 5 min, timeout 4 min"),
        ("Persistance", "SQLite — 32 actifs (30 actions + BTC/USD, ETH/USD)"),
    ]
    for idx, (key, val) in enumerate(specs):
        y_pos = card_y + 90 - idx * 18
        c_obj.setFont('Helvetica-Bold', 9)
        c_obj.setFillColor(ACCENT_YELLOW)
        c_obj.drawString(70, y_pos, f"{key}:")
        c_obj.setFont('Helvetica', 9)
        c_obj.setFillColor(TEXT_PRIMARY)
        c_obj.drawString(155, y_pos, val)

    # Sections du guide
    c_obj.setFont('Helvetica-Bold', 10)
    c_obj.setFillColor(ACCENT_BLUE)
    c_obj.drawCentredString(w / 2, h - 420, "7 SECTIONS • 15+ PAGES • EXEMPLES DE CODE")

    # Badges des sections
    sections = [
        ("1", "Algorithme & Signaux",     ACCENT_BLUE),
        ("2", "Gestion du Risque",        ACCENT_ORANGE),
        ("3", "IA & Machine Learning",    ACCENT_PURPLE),
        ("4", "Architecture",             ACCENT_GREEN),
        ("5", "Indicateurs Avancés",      ACCENT_YELLOW),
        ("6", "Backtesting",              ACCENT_BLUE),
        ("7", "Cas Réels",               ACCENT_GREEN),
    ]
    cols = 4
    badge_w, badge_h = (w - 100) / cols - 6, 32
    for i, (num, name, col) in enumerate(sections):
        row, colp = divmod(i, cols)
        bx = 50 + colp * (badge_w + 6)
        by = h - 510 - row * 38
        c_obj.setFillColor(HexColor('#1C2333'))
        c_obj.roundRect(bx, by, badge_w, badge_h, 4, fill=1, stroke=0)
        c_obj.setFillColor(col)
        c_obj.roundRect(bx, by, badge_w, badge_h, 4, fill=0, stroke=1)
        c_obj.setFont('Helvetica-Bold', 8)
        c_obj.setFillColor(col)
        c_obj.drawString(bx + 8, by + 20, f"§{num}")
        c_obj.setFont('Helvetica', 7.5)
        c_obj.setFillColor(TEXT_PRIMARY)
        c_obj.drawString(bx + 24, by + 20, name)

    # Footer
    c_obj.setFillColor(DARK_CARD)
    c_obj.rect(0, 0, w, 50, fill=1, stroke=0)
    c_obj.setFillColor(ACCENT_BLUE)
    c_obj.rect(0, 49, w, 1, fill=1, stroke=0)
    c_obj.setFont('Helvetica', 9)
    c_obj.setFillColor(TEXT_MUTED)
    c_obj.drawCentredString(w / 2, 30, "Guide Professionnel de Trading Algorithmique  •  Version 1.0  •  2025")
    c_obj.drawCentredString(w / 2, 16, "Confidentiel — À usage interne uniquement")


# ── Numérotation des pages ─────────────────────────────────────────────────────
class PageTemplate:
    def __init__(self):
        self.page_number = 0

    def on_page(self, canvas_obj, doc):
        canvas_obj.saveState()
        w, h = A4

        # En-tête
        canvas_obj.setFillColor(DARK_CARD)
        canvas_obj.rect(0, h - 28, w, 28, fill=1, stroke=0)
        canvas_obj.setFillColor(ACCENT_BLUE)
        canvas_obj.rect(0, h - 29, w, 1, fill=1, stroke=0)
        canvas_obj.setFont('Helvetica-Bold', 8)
        canvas_obj.setFillColor(ACCENT_BLUE)
        canvas_obj.drawString(30, h - 19, "GUIDE D'AMÉLIORATION DU BOT DE TRADING ALGORITHMIQUE")
        canvas_obj.setFont('Helvetica', 8)
        canvas_obj.setFillColor(TEXT_MUTED)
        canvas_obj.drawRightString(w - 30, h - 19, f"Page {doc.page}")

        # Pied de page
        canvas_obj.setFillColor(DARK_CARD)
        canvas_obj.rect(0, 0, w, 24, fill=1, stroke=0)
        canvas_obj.setFillColor(ACCENT_BLUE)
        canvas_obj.rect(0, 23, w, 1, fill=1, stroke=0)
        canvas_obj.setFont('Helvetica', 7.5)
        canvas_obj.setFillColor(TEXT_MUTED)
        canvas_obj.drawString(30, 8, "Alpaca Trade API  |  Groq + Gemini AI  |  GitHub Actions  |  SQLite")
        canvas_obj.drawRightString(w - 30, 8, "© 2025 — Trading Bot Guide")

        canvas_obj.restoreState()


def bp(styles, text, bullet_char="▸"):
    """Helper: retourne un Paragraph de type bullet."""
    return Paragraph(f"{bullet_char}  {text}", styles['bullet'])


def h2(styles, text):
    return Paragraph(text, styles['subsection_header'])


def body(styles, text):
    return Paragraph(text, styles['body'])


def sp(n=6):
    return Spacer(1, n)


# ── Construction du contenu ────────────────────────────────────────────────────
def build_content(styles):
    story = []

    # ── Page de garde (dessinée séparément, page vierge ici) ──────────────────
    story.append(PageBreak())

    # ── Table des matières ────────────────────────────────────────────────────
    story.append(Paragraph("Table des Matières", styles['toc_title']))
    story.append(SectionDivider(ACCENT_BLUE, 1.5))
    story.append(sp(12))

    toc_data = [
        ("Section 1", "Algorithme & Signaux",                         "Analyse multi-temporelle, VWAP, divergences, Volume Profile, Order Flow"),
        ("Section 2", "Gestion du Risque",                            "Kelly Criterion, Stop-loss dynamique, Portfolio Heat, Circuit Breaker"),
        ("Section 3", "Intelligence Artificielle & Machine Learning",  "RL/DQN, LSTM/Transformer, HMM, Ensemble, SHAP"),
        ("Section 4", "Architecture & Infrastructure",                 "Event-driven, GitHub Actions, Microservices, Pipeline"),
        ("Section 5", "Indicateurs Avancés",                          "Ichimoku, Elliott Wave, Harmoniques, Microstructure, Options Flow"),
        ("Section 6", "Backtesting & Validation",                      "Walk-forward, Monte Carlo, Out-of-sample, Sensibilité"),
        ("Section 7", "Cas d'Usage & Bots Réels",                     "Freqtrade, Jesse, Hummingbot, Renaissance, Two Sigma"),
    ]
    for sec, title, desc in toc_data:
        story.append(Paragraph(f"<b><font color='#58A6FF'>{sec}</font></b>  —  <b>{title}</b>", styles['toc_section']))
        story.append(Paragraph(desc, styles['toc_entry']))
        story.append(sp(4))

    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 1 — ALGORITHME & SIGNAUX
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Section 1 — Algorithme &amp; Signaux", styles['section_header']))
    story.append(SectionDivider(ACCENT_BLUE))
    story.append(sp(8))

    # ── 1.1 Analyse Multi-Temporelle ──────────────────────────────────────────
    story.append(h2(styles, "1.1  Analyse Multi-Temporelle (MTF)"))
    story.append(body(styles,
        "L'analyse multi-temporelle (Multi-TimeFrame — MTF) est l'une des techniques les plus puissantes "
        "pour améliorer la précision des signaux de trading. L'idée fondamentale est d'aligner la "
        "direction de la tendance sur un timeframe supérieur (4h, journalier) avant d'entrer sur le "
        "timeframe d'exécution (5 min, 1h). Des bots comme <b>Freqtrade</b> implémentent nativement "
        "cette approche via le paramètre <font color='#58A6FF'>informative_timeframe</font>."
    ))
    story.append(sp(6))
    story.append(body(styles,
        "<b>Principe de confirmation 1h + 4h :</b> Le timeframe 4h définit la tendance macro (EMA 100 "
        "ascendant = tendance haussière). Le timeframe 1h génère l'entrée précise (croisement HMA/EMA9). "
        "Un signal d'achat n'est valide que si les deux timeframes sont alignés."
    ))
    story.append(sp(6))

    story.append(CodeBlock([
        "# Exemple d'implémentation MTF avec pandas-ta",
        "import pandas_ta as ta",
        "import pandas as pd",
        "",
        "def get_mtf_signal(df_1h: pd.DataFrame, df_4h: pd.DataFrame) -> str:",
        "    # Tendance 4h: EMA 100",
        "    df_4h['ema100'] = ta.ema(df_4h['close'], length=100)",
        "    trend_4h = 'UP' if df_4h['close'].iloc[-1] > df_4h['ema100'].iloc[-1] else 'DOWN'",
        "",
        "    # Signal 1h: croisement HMA/EMA9",
        "    df_1h['hma10']  = ta.hma(df_1h['close'], length=10)",
        "    df_1h['ema9']   = ta.ema(df_1h['close'], length=9)",
        "    cross_up = (df_1h['hma10'].iloc[-1] > df_1h['ema9'].iloc[-1] and",
        "                df_1h['hma10'].iloc[-2] <= df_1h['ema9'].iloc[-2])",
        "",
        "    # Confirmation MTF",
        "    if trend_4h == 'UP' and cross_up:",
        "        return 'BUY'   # signal validé",
        "    return 'NEUTRAL'",
    ], accent=ACCENT_GREEN))
    story.append(sp(6))

    story.append(body(styles,
        "<b>Adaptation pour GitHub Actions :</b> Comme l'exécution est limitée à 4 minutes, les données "
        "multi-temporelles doivent être pré-calculées et stockées en SQLite. À chaque run, on charge "
        "df_4h depuis la base et on le met à jour uniquement si 4h se sont écoulées depuis la dernière "
        "mise à jour, évitant les appels API inutiles."
    ))
    story.append(sp(8))

    # ── 1.2 VWAP ─────────────────────────────────────────────────────────────
    story.append(h2(styles, "1.2  VWAP — Volume-Weighted Average Price"))
    story.append(body(styles,
        "Le VWAP est la référence intraday des traders institutionnels. Il représente le prix moyen "
        "pondéré par le volume depuis l'ouverture de session. Un prix au-dessus du VWAP indique une "
        "pression acheteuse; en dessous, vendeuse. Les teneurs de marché (market makers) utilisent "
        "le VWAP comme benchmark d'exécution — c'est la raison pour laquelle le prix y revient "
        "fréquemment."
    ))
    story.append(sp(6))

    story.append(CodeBlock([
        "# Calcul du VWAP intraday",
        "def calculate_vwap(df: pd.DataFrame) -> pd.Series:",
        "    \"\"\"df doit contenir: high, low, close, volume — reset à chaque session.\"\"\"",
        "    typical_price = (df['high'] + df['low'] + df['close']) / 3",
        "    cumulative_tpv = (typical_price * df['volume']).cumsum()",
        "    cumulative_vol  = df['volume'].cumsum()",
        "    return cumulative_tpv / cumulative_vol",
        "",
        "# Bandes VWAP (équivalent Bollinger sur VWAP)",
        "def vwap_bands(df, n_std=1.5):",
        "    vwap = calculate_vwap(df)",
        "    tp   = (df['high'] + df['low'] + df['close']) / 3",
        "    variance = ((tp - vwap) ** 2 * df['volume']).cumsum() / df['volume'].cumsum()",
        "    std  = variance ** 0.5",
        "    return vwap, vwap + n_std * std, vwap - n_std * std",
    ], accent=ACCENT_BLUE))
    story.append(sp(6))

    story.append(body(styles,
        "<b>Stratégie VWAP Reversion :</b> Acheter quand le prix touche la bande inférieure VWAP "
        "en tendance haussière (confirmée par EMA100 4h). Vendre au VWAP central. Ratio R/R typique: 2:1. "
        "Cette approche est utilisée par des fonds HFT et est implémentée dans le bot open-source "
        "<b>Jesse</b> via le module de moyennes mobiles pondérées."
    ))
    story.append(sp(8))

    # ── 1.3 Divergences MACD/RSI ─────────────────────────────────────────────
    story.append(h2(styles, "1.3  Divergences MACD / RSI"))
    story.append(body(styles,
        "Une divergence se produit quand le prix fait un nouveau plus haut (ou plus bas) mais que "
        "l'indicateur ne confirme pas. C'est l'un des signaux de retournement les plus fiables en "
        "analyse technique. On distingue deux types principaux:"
    ))
    story.append(bp(styles, "<b>Divergence haussière (bullish):</b> Prix fait un nouveau plus bas (LL), RSI/MACD fait un plus bas plus élevé (HL). Signal d'achat."))
    story.append(bp(styles, "<b>Divergence baissière (bearish):</b> Prix fait un nouveau plus haut (HH), RSI/MACD fait un plus haut plus bas (LH). Signal de vente."))
    story.append(sp(6))

    story.append(CodeBlock([
        "# Détection automatique de divergences",
        "from scipy.signal import argrelextrema",
        "import numpy as np",
        "",
        "def detect_divergence(df: pd.DataFrame, lookback=14) -> dict:",
        "    closes = df['close'].values",
        "    rsi    = ta.rsi(df['close'], length=14).values",
        "",
        "    # Trouver les pivots locaux",
        "    price_lows = argrelextrema(closes, np.less,  order=5)[0]",
        "    rsi_lows   = argrelextrema(rsi,    np.less,  order=5)[0]",
        "",
        "    # Comparer les 2 derniers pivots bas",
        "    if len(price_lows) >= 2 and len(rsi_lows) >= 2:",
        "        p1, p2 = closes[price_lows[-2]], closes[price_lows[-1]]",
        "        r1, r2 = rsi[rsi_lows[-2]],   rsi[rsi_lows[-1]]",
        "        if p2 < p1 and r2 > r1:  # prix LL, RSI HL",
        "            return {'type': 'bullish', 'strength': (r2 - r1) / r1}",
        "    return {'type': 'none'}",
    ], accent=ACCENT_ORANGE))
    story.append(sp(6))

    story.append(body(styles,
        "<b>Intégration avec votre bot :</b> Ajoutez la détection de divergences comme filtre "
        "supplémentaire avant une entrée. Une divergence haussière RSI + signal HMA/EMA = conviction "
        "élevée. Pondérez la taille de position à +20% en présence d'une divergence confirmée."
    ))

    story.append(PageBreak())

    # ── 1.4 Volume Profile ────────────────────────────────────────────────────
    story.append(h2(styles, "1.4  Volume Profile (POC, VAH, VAL)"))
    story.append(body(styles,
        "Le Volume Profile est une représentation de la distribution des échanges par niveau de prix "
        "sur une période donnée. Il révèle où le marché a passé le plus de temps et créé de la valeur. "
        "Les trois niveaux clés sont:"
    ))
    story.append(bp(styles, "<b>POC (Point of Control):</b> Niveau de prix avec le plus grand volume échangé. Zone d'équilibre principal, souvent un support/résistance magnétique."))
    story.append(bp(styles, "<b>VAH (Value Area High):</b> Limite supérieure de la zone de valeur (70% du volume). Au-dessus = premium, potentiel retour vers VA."))
    story.append(bp(styles, "<b>VAL (Value Area Low):</b> Limite inférieure de la zone de valeur (70% du volume). En dessous = discount, potentiel rebond."))
    story.append(sp(6))

    story.append(CodeBlock([
        "# Calcul simplifié du Volume Profile",
        "def volume_profile(df: pd.DataFrame, bins=50) -> dict:",
        "    price_min = df['low'].min()",
        "    price_max = df['high'].max()",
        "    price_levels = np.linspace(price_min, price_max, bins)",
        "    vol_at_price  = np.zeros(bins)",
        "",
        "    for _, row in df.iterrows():",
        "        # Distribuer le volume entre high et low",
        "        idx_low  = np.searchsorted(price_levels, row['low'])",
        "        idx_high = np.searchsorted(price_levels, row['high'])",
        "        if idx_high > idx_low:",
        "            vol_at_price[idx_low:idx_high] += row['volume'] / (idx_high - idx_low)",
        "",
        "    poc_idx = np.argmax(vol_at_price)",
        "    poc     = price_levels[poc_idx]",
        "",
        "    # Value Area (70% du volume total)",
        "    total_vol = vol_at_price.sum()",
        "    sorted_idx = np.argsort(vol_at_price)[::-1]",
        "    cumvol, va_indices = 0, []",
        "    for i in sorted_idx:",
        "        if cumvol >= 0.70 * total_vol: break",
        "        va_indices.append(i); cumvol += vol_at_price[i]",
        "    vah = price_levels[max(va_indices)]",
        "    val = price_levels[min(va_indices)]",
        "    return {'poc': poc, 'vah': vah, 'val': val}",
    ], accent=ACCENT_PURPLE))
    story.append(sp(8))

    # ── 1.5 Order Flow ────────────────────────────────────────────────────────
    story.append(h2(styles, "1.5  Order Flow Imbalance"))
    story.append(body(styles,
        "L'Order Flow Imbalance (OFI) mesure le déséquilibre entre les ordres d'achat et de vente "
        "dans le carnet d'ordres. Un OFI positif élevé indique une pression acheteuse agressive; "
        "négatif, une pression vendeuse. Bien que l'accès au Level 2 soit limité avec Alpaca API, "
        "un proxy efficace peut être calculé depuis les données OHLCV:"
    ))
    story.append(sp(6))

    story.append(CodeBlock([
        "# Proxy Order Flow depuis données OHLCV (Alpaca compatible)",
        "def order_flow_proxy(df: pd.DataFrame) -> pd.Series:",
        "    \"\"\"",
        "    Approximation: si close > open -> volume acheteur",
        "                   si close < open -> volume vendeur",
        "    \"\"\"",
        "    buy_vol  = df['volume'] * (df['close'] > df['open']).astype(float)",
        "    sell_vol = df['volume'] * (df['close'] < df['open']).astype(float)",
        "    ofi = (buy_vol - sell_vol).rolling(10).sum()",
        "    return ofi / df['volume'].rolling(10).sum()  # normalisé [-1, 1]",
        "",
        "# Signal combiné",
        "def combined_signal(df):",
        "    ofi = order_flow_proxy(df)",
        "    rsi = ta.rsi(df['close'], 14)",
        "    # Fort signal haussier: OFI > 0.3 ET RSI en zone neutre (40-60)",
        "    bull = (ofi.iloc[-1] > 0.3) and (40 < rsi.iloc[-1] < 60)",
        "    return 'BUY' if bull else 'NEUTRAL'",
    ], accent=ACCENT_YELLOW))

    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 2 — GESTION DU RISQUE
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Section 2 — Gestion du Risque", styles['section_header']))
    story.append(SectionDivider(ACCENT_ORANGE))
    story.append(sp(8))

    story.append(body(styles,
        "La gestion du risque est le facteur différenciateur entre un bot profitable à long terme "
        "et un bot qui brûle son capital. Les fonds quantitatifs comme Renaissance Technologies "
        "attribuent 60% de leur performance à la gestion du risque, et seulement 40% aux signaux "
        "d'entrée/sortie. Voici les mécanismes essentiels à implémenter."
    ))
    story.append(sp(8))

    # ── 2.1 Kelly Criterion ───────────────────────────────────────────────────
    story.append(h2(styles, "2.1  Critère de Kelly — Sizing Optimal"))
    story.append(body(styles,
        "Le Critère de Kelly détermine la fraction optimale du capital à risquer sur chaque trade "
        "pour maximiser la croissance logarithmique du portefeuille. Développé par John Kelly (1956) "
        "et popularisé par Ed Thorp, il est utilisé par Buffett, Renaissance et la plupart des fonds "
        "quantitatifs (souvent en version fractionnelle: 1/2 ou 1/4 Kelly pour réduire la variance)."
    ))
    story.append(sp(6))

    story.append(CodeBlock([
        "# Critère de Kelly",
        "def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float,",
        "                   kelly_fraction: float = 0.25) -> float:",
        "    \"\"\"",
        "    win_rate: taux de victoire (ex: 0.55)",
        "    avg_win:  gain moyen en % (ex: 0.02 pour 2%)",
        "    avg_loss: perte moyenne en % (ex: 0.01 pour 1%)",
        "    kelly_fraction: fraction de Kelly (0.25 = quart-Kelly recommandé)",
        "    \"\"\"",
        "    b = avg_win / avg_loss    # ratio gain/perte",
        "    p = win_rate",
        "    q = 1 - p",
        "    kelly = (b * p - q) / b  # formule originale",
        "    return min(kelly * kelly_fraction, 0.10)  # cap à 10% du capital",
        "",
        "# Exemple avec historique SQLite",
        "def get_kelly_from_history(conn, symbol: str) -> float:",
        "    df = pd.read_sql(",
        "        'SELECT pnl_pct FROM trades WHERE symbol=? ORDER BY closed_at DESC LIMIT 50',",
        "        conn, params=[symbol])",
        "    wins = df[df['pnl_pct'] > 0]['pnl_pct']",
        "    losses = df[df['pnl_pct'] < 0]['pnl_pct'].abs()",
        "    if len(wins) < 5: return 0.01  # insuffisant: taille minimale",
        "    return kelly_fraction(len(wins)/len(df), wins.mean(), losses.mean())",
    ], accent=ACCENT_ORANGE))
    story.append(sp(8))

    # ── 2.2 Stop-loss dynamique ───────────────────────────────────────────────
    story.append(h2(styles, "2.2  Stop-loss Dynamique Basé sur la Volatilité"))
    story.append(body(styles,
        "Un stop-loss fixe en pourcentage (ex: -2%) est sous-optimal car il ignore le régime de "
        "volatilité du marché. En période volatile, le prix oscille naturellement de 3-4% sans "
        "changement de tendance. Un stop trop serré = sortie prématurée. Un stop basé sur l'ATR "
        "(Average True Range) s'adapte automatiquement au régime."
    ))
    story.append(sp(6))

    story.append(CodeBlock([
        "# Stop-loss dynamique basé sur ATR",
        "def dynamic_stop_loss(df: pd.DataFrame, entry_price: float,",
        "                      atr_multiplier: float = 2.0) -> dict:",
        "    atr = ta.atr(df['high'], df['low'], df['close'], length=14)",
        "    current_atr = atr.iloc[-1]",
        "",
        "    stop_long  = entry_price - (current_atr * atr_multiplier)",
        "    stop_short = entry_price + (current_atr * atr_multiplier)",
        "    take_profit_long  = entry_price + (current_atr * atr_multiplier * 2)",
        "",
        "    # Régime de volatilité",
        "    avg_atr = atr.rolling(50).mean().iloc[-1]",
        "    vol_regime = 'HIGH' if current_atr > avg_atr * 1.5 else 'NORMAL'",
        "",
        "    if vol_regime == 'HIGH':",
        "        atr_multiplier *= 1.3  # Élargir stop en volatilité élevée",
        "",
        "    return {",
        "        'stop_loss':   stop_long,",
        "        'take_profit': take_profit_long,",
        "        'atr':         current_atr,",
        "        'regime':      vol_regime",
        "    }",
    ], accent=ACCENT_ORANGE))
    story.append(sp(8))

    # ── 2.3 Portfolio Heat ────────────────────────────────────────────────────
    story.append(h2(styles, "2.3  Portfolio Heat — Exposition Totale au Risque"))
    story.append(body(styles,
        "Le <b>Portfolio Heat</b> est la somme du risque ouvert sur toutes les positions actives "
        "simultanément. Si chaque position risque 1% du capital, 5 positions simultanées = 5% de "
        "heat. La règle générale est de limiter le heat total à 6-10% du portefeuille. "
        "Two Sigma et Citadel utilisent des modèles de heat dynamiques basés sur la corrélation."
    ))
    story.append(sp(6))

    story.append(CodeBlock([
        "# Calcul du Portfolio Heat",
        "def calculate_portfolio_heat(positions: list, capital: float) -> float:",
        "    \"\"\"positions: [{'symbol':..., 'entry':..., 'stop':..., 'qty':...}]\"\"\"",
        "    total_risk = 0",
        "    for pos in positions:",
        "        risk_per_share = abs(pos['entry'] - pos['stop'])",
        "        position_risk  = risk_per_share * pos['qty']",
        "        total_risk    += position_risk",
        "    return total_risk / capital  # % du capital en risque",
        "",
        "def can_open_position(positions, new_risk_pct, capital, max_heat=0.08):",
        "    current_heat = calculate_portfolio_heat(positions, capital)",
        "    return (current_heat + new_risk_pct) <= max_heat",
    ], accent=ACCENT_ORANGE))
    story.append(sp(8))

    # ── 2.4 Circuit Breaker ───────────────────────────────────────────────────
    story.append(h2(styles, "2.4  Circuit Breaker — Coupe-Circuit de Perte Journalière"))
    story.append(body(styles,
        "Un circuit breaker arrête automatiquement le trading quand les pertes journalières "
        "dépassent un seuil (typiquement -3% à -5%). Cette règle protège contre les scénarios "
        "catastrophiques (bug algorithmique, flash crash, données erronées). Persiste via SQLite."
    ))
    story.append(sp(6))

    story.append(CodeBlock([
        "# Circuit Breaker journalier",
        "def check_circuit_breaker(conn, capital: float,",
        "                           max_daily_loss: float = 0.03) -> bool:",
        "    today = pd.Timestamp.now().date().isoformat()",
        "    result = conn.execute(",
        "        'SELECT SUM(pnl) FROM trades WHERE DATE(closed_at) = ?', [today]",
        "    ).fetchone()[0] or 0",
        "    daily_loss_pct = abs(result) / capital",
        "    if daily_loss_pct >= max_daily_loss:",
        "        conn.execute(",
        "            'INSERT INTO circuit_breaker (triggered_at, daily_loss) VALUES (?, ?)',",
        "            [pd.Timestamp.now().isoformat(), result])",
        "        conn.commit()",
        "        return True  # STOP — ne pas trader",
        "    return False",
    ], accent=ACCENT_ORANGE))
    story.append(sp(6))

    # ── 2.5 Corrélation ───────────────────────────────────────────────────────
    story.append(h2(styles, "2.5  Sizing Ajusté par Corrélation"))
    story.append(body(styles,
        "Si votre portefeuille contient des actifs fortement corrélés (ex: AAPL et MSFT, corrélation "
        "0.85), le risque réel est supérieur au risque apparent. En cas de choc de marché, les deux "
        "positions perdront simultanément. Réduisez la taille de position proportionnellement à la "
        "corrélation avec les positions existantes."
    ))
    story.append(bp(styles, "<b>Règle pratique:</b> Pour 32 actifs, calculez la matrice de corrélation hebdomadaire et limitez la somme des corrélations avec les positions ouvertes à 2.0."))
    story.append(bp(styles, "<b>Implémentation:</b> Utiliser <font color='#58A6FF'>df.corr()</font> sur les rendements journaliers; stocker en SQLite, mettre à jour chaque matin."))

    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 3 — IA & ML
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Section 3 — Intelligence Artificielle &amp; Machine Learning", styles['section_header']))
    story.append(SectionDivider(ACCENT_PURPLE))
    story.append(sp(8))

    story.append(body(styles,
        "L'intégration de l'apprentissage automatique dans un bot de trading doit être approchée "
        "avec rigueur. Les modèles ML ne sont pas magiques — ils nécessitent des données de qualité, "
        "une validation rigoureuse et une architecture adaptée aux contraintes temps-réel. "
        "Voici les approches validées en production par des fonds quantitatifs."
    ))
    story.append(sp(8))

    # ── 3.1 Reinforcement Learning ───────────────────────────────────────────
    story.append(h2(styles, "3.1  Reinforcement Learning — Q-Learning / DQN"))
    story.append(body(styles,
        "Le Reinforcement Learning (RL) est particulièrement adapté au trading car il optimise "
        "directement une récompense (P&amp;L) plutôt qu'une métrique proxy. L'agent apprend par "
        "interaction avec un environnement simulé. Le Deep Q-Network (DQN) de DeepMind, adapté "
        "au trading, a montré des résultats prometteurs sur données haute fréquence."
    ))
    story.append(sp(6))
    story.append(body(styles, "<b>Architecture d'un environnement de trading RL:</b>"))
    story.append(bp(styles, "<b>État (State):</b> Vecteur de features — [close_norm, rsi/100, macd_norm, volume_norm, position_flag, unrealized_pnl]"))
    story.append(bp(styles, "<b>Actions:</b> {0=Hold, 1=Buy, 2=Sell} ou sizing continu [0, 1]"))
    story.append(bp(styles, "<b>Récompense:</b> Sharpe ratio différentiel, ou log-return ajusté du drawdown"))
    story.append(bp(styles, "<b>Bibliothèques:</b> stable-baselines3 (PPO, A2C), FinRL (dédié finance)"))
    story.append(sp(6))

    story.append(CodeBlock([
        "# Squelette d'environnement Gym pour trading",
        "import gymnasium as gym",
        "import numpy as np",
        "",
        "class TradingEnv(gym.Env):",
        "    def __init__(self, df: pd.DataFrame, initial_capital=10000):",
        "        super().__init__()",
        "        self.df      = df",
        "        self.capital = initial_capital",
        "        # Espace d'observation: 10 features normalisées",
        "        self.observation_space = gym.spaces.Box(",
        "            low=-np.inf, high=np.inf, shape=(10,), dtype=np.float32)",
        "        # Espace d'action: Hold/Buy/Sell",
        "        self.action_space = gym.spaces.Discrete(3)",
        "",
        "    def _get_obs(self):",
        "        row = self.df.iloc[self.current_step]",
        "        return np.array([",
        "            row['close'] / self.df['close'].mean(),",
        "            row['rsi'] / 100,",
        "            row['macd'] / self.df['macd'].std(),",
        "            row['volume'] / self.df['volume'].mean(),",
        "            float(self.position > 0),",
        "            self.unrealized_pnl / self.capital",
        "        ], dtype=np.float32)",
        "",
        "    def step(self, action):",
        "        reward = self._execute_action(action)",
        "        self.current_step += 1",
        "        done = self.current_step >= len(self.df) - 1",
        "        return self._get_obs(), reward, done, False, {}",
    ], accent=ACCENT_PURPLE))
    story.append(sp(8))

    # ── 3.2 LSTM/Transformer ─────────────────────────────────────────────────
    story.append(h2(styles, "3.2  LSTM &amp; Transformer pour la Prédiction de Prix"))
    story.append(body(styles,
        "Les réseaux LSTM (Long Short-Term Memory) et Transformers captent les dépendances "
        "temporelles dans les séries financières. Le modèle Temporal Fusion Transformer (TFT), "
        "développé par Google, surpasse les LSTM sur les prévisions financières selon plusieurs "
        "études académiques (Lim et al., 2021). Cependant, <b>l'objectif ne doit pas être de "
        "prédire le prix exact mais la direction et la volatilité attendue.</b>"
    ))
    story.append(sp(6))

    story.append(CodeBlock([
        "# Architecture LSTM pour prédiction de direction",
        "import torch",
        "import torch.nn as nn",
        "",
        "class TradingLSTM(nn.Module):",
        "    def __init__(self, input_size=15, hidden_size=128, num_layers=2):",
        "        super().__init__()",
        "        self.lstm = nn.LSTM(input_size, hidden_size, num_layers,",
        "                            batch_first=True, dropout=0.2)",
        "        self.fc   = nn.Sequential(",
        "            nn.Linear(hidden_size, 64),",
        "            nn.ReLU(),",
        "            nn.Dropout(0.3),",
        "            nn.Linear(64, 3)  # [down, neutral, up]",
        "        )",
        "",
        "    def forward(self, x):",
        "        # x: [batch, seq_len=20, features=15]",
        "        lstm_out, _ = self.lstm(x)",
        "        return self.fc(lstm_out[:, -1, :])  # dernier timestep",
        "",
        "# Features d'entrée recommandées (15 features):",
        "# close_pct, volume_pct, rsi, macd, macd_signal, bb_pct,",
        "# ema9_dist, ema21_dist, ema100_dist, hma_dist,",
        "# adx, atr_pct, vwap_dist, hour_sin, hour_cos",
    ], accent=ACCENT_PURPLE))
    story.append(sp(8))

    # ── 3.3 HMM ──────────────────────────────────────────────────────────────
    story.append(h2(styles, "3.3  Détection de Régime — Hidden Markov Models (HMM)"))
    story.append(body(styles,
        "Les Hidden Markov Models (HMM) permettent d'identifier le régime de marché actuel: "
        "tendance haussière, tendance baissière, ou range. Different régimes nécessitent "
        "différentes stratégies: les stratégies de momentum performent en tendance, "
        "les stratégies de mean-reversion performent en range. <b>Deux Sigma</b> et <b>AQR Capital</b> "
        "utilisent des modèles de détection de régime comme couche primaire de leur architecture."
    ))
    story.append(sp(6))

    story.append(CodeBlock([
        "# Détection de régime avec hmmlearn",
        "from hmmlearn import hmm",
        "import numpy as np",
        "",
        "def fit_regime_model(returns: np.ndarray, n_states=3):",
        "    \"\"\"Entraîne un HMM Gaussien sur les rendements journaliers.\"\"\"",
        "    model = hmm.GaussianHMM(n_components=n_states,",
        "                            covariance_type='full',",
        "                            n_iter=100)",
        "    X = returns.reshape(-1, 1)",
        "    model.fit(X)",
        "    return model",
        "",
        "def get_current_regime(model, recent_returns: np.ndarray) -> str:",
        "    state = model.predict(recent_returns.reshape(-1, 1))[-1]",
        "    means = model.means_.flatten()",
        "    # État avec moyenne la plus élevée = bullish",
        "    labels = {np.argmax(means): 'BULL',",
        "              np.argmin(means): 'BEAR',}",
        "    labels.setdefault(3 - np.argmax(means) - np.argmin(means), 'RANGE')",
        "    return labels.get(state, 'UNKNOWN')",
    ], accent=ACCENT_PURPLE))
    story.append(sp(8))

    # ── 3.4 Ensemble ─────────────────────────────────────────────────────────
    story.append(h2(styles, "3.4  Méthodes Ensemble — Combiner les Modèles"))
    story.append(body(styles,
        "Aucun modèle seul n'est optimal dans toutes les conditions de marché. Les méthodes "
        "ensemble combinent plusieurs modèles pour réduire la variance et améliorer la robustesse. "
        "Pour votre bot (Groq + Gemini déjà en place), l'architecture suivante est recommandée:"
    ))
    story.append(bp(styles, "<b>Couche 1 — Techniques:</b> Signal HMA/EMA/MACD (déjà implémenté) + Divergences RSI + MTF"))
    story.append(bp(styles, "<b>Couche 2 — ML:</b> LSTM direction (probabilité) + HMM régime"))
    story.append(bp(styles, "<b>Couche 3 — Sentiment:</b> Groq/Gemini score (déjà implémenté, -1 à +1)"))
    story.append(bp(styles, "<b>Agrégation:</b> Vote pondéré — Technique 40%, ML 35%, Sentiment 25%"))
    story.append(sp(8))

    # ── 3.5 SHAP ─────────────────────────────────────────────────────────────
    story.append(h2(styles, "3.5  Analyse d'Importance des Features — SHAP Values"))
    story.append(body(styles,
        "SHAP (SHapley Additive exPlanations) décompose la prédiction de chaque modèle en "
        "contributions individuelles par feature. Essentiel pour comprendre pourquoi le modèle "
        "prend une décision, détecter les données aberrantes, et identifier les features redondantes. "
        "Utiliser <font color='#58A6FF'>shap.TreeExplainer</font> pour XGBoost/LightGBM, "
        "<font color='#58A6FF'>shap.DeepExplainer</font> pour les réseaux de neurones."
    ))
    story.append(bp(styles, "<b>Résultats typiques:</b> RSI, ATR et Volume sont généralement les features les plus importantes pour les signaux intraday court terme."))
    story.append(bp(styles, "<b>Action:</b> Supprimer les features avec SHAP moyen < 0.001 pour simplifier le modèle et réduire l'overfitting."))

    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 4 — ARCHITECTURE & INFRASTRUCTURE
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Section 4 — Architecture &amp; Infrastructure", styles['section_header']))
    story.append(SectionDivider(ACCENT_GREEN))
    story.append(sp(8))

    # ── 4.1 Event-driven ─────────────────────────────────────────────────────
    story.append(h2(styles, "4.1  Architecture Event-Driven vs Poll-Based"))
    story.append(body(styles,
        "Votre architecture actuelle est <b>poll-based</b>: GitHub Actions exécute le bot "
        "toutes les 5 minutes. C'est simple mais sous-optimal — le bot peut manquer des "
        "opportunités entre deux runs et consomme des ressources même quand le marché est inactif. "
        "Une architecture <b>event-driven</b> réagit instantanément aux événements du marché."
    ))
    story.append(sp(6))

    # Comparaison sous forme de tableau
    table_data = [
        [Paragraph("<b>Critère</b>",       styles['highlight']),
         Paragraph("<b>Poll-based (actuel)</b>", styles['highlight']),
         Paragraph("<b>Event-driven (cible)</b>", styles['highlight'])],
        [Paragraph("Latence",              styles['body']),
         Paragraph("5 minutes",            styles['body']),
         Paragraph("&lt;100ms",            styles['body'])],
        [Paragraph("Complexité",           styles['body']),
         Paragraph("Simple",               styles['body']),
         Paragraph("Moyenne",              styles['body'])],
        [Paragraph("Coût",                 styles['body']),
         Paragraph("GitHub Actions gratuit", styles['body']),
         Paragraph("VPS requis (~$5/mois)", styles['body'])],
        [Paragraph("Manque d'opportunités", styles['body']),
         Paragraph("Oui (entre les runs)",  styles['body']),
         Paragraph("Non",                   styles['body'])],
        [Paragraph("Adapté au projet",      styles['body']),
         Paragraph("Oui",                   styles['body']),
         Paragraph("Amélioration future",   styles['body'])],
    ]
    t = Table(table_data, colWidths=[3.5 * cm, 6 * cm, 6 * cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), DARK_CARD),
        ('BACKGROUND', (0, 1), (-1, -1), CODE_BG),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [CODE_BG, HexColor('#1A2030')]),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(t)
    story.append(sp(10))

    # ── 4.2 Réduire la latence GitHub Actions ────────────────────────────────
    story.append(h2(styles, "4.2  Optimisation de la Latence sur GitHub Actions"))
    story.append(body(styles,
        "Avec un timeout de 4 minutes, chaque milliseconde compte. Voici les optimisations "
        "les plus impactantes pour votre architecture actuelle:"
    ))
    story.append(bp(styles, "<b>Cache des dépendances:</b> Utiliser <font color='#58A6FF'>actions/cache@v4</font> pour cacher le venv Python. Réduit le temps de démarrage de 60-90 secondes."))
    story.append(bp(styles, "<b>Données en cache:</b> Stocker les données OHLCV des dernières 24h en SQLite et ne charger que les nouvelles bougies via l'API Alpaca."))
    story.append(bp(styles, "<b>Traitement parallèle:</b> Utiliser <font color='#58A6FF'>concurrent.futures.ThreadPoolExecutor</font> pour analyser les 32 actifs en parallèle."))
    story.append(bp(styles, "<b>Lazy loading:</b> Charger les modèles ML une seule fois et sérialiser avec <font color='#58A6FF'>pickle</font> ou <font color='#58A6FF'>joblib</font>."))
    story.append(sp(6))

    story.append(CodeBlock([
        "# Traitement parallèle des 32 actifs",
        "from concurrent.futures import ThreadPoolExecutor, as_completed",
        "",
        "def analyze_all_symbols(symbols: list, alpaca_api, db_conn) -> list:",
        "    signals = []",
        "    with ThreadPoolExecutor(max_workers=8) as executor:",
        "        futures = {",
        "            executor.submit(analyze_symbol, sym, alpaca_api, db_conn): sym",
        "            for sym in symbols",
        "        }",
        "        for future in as_completed(futures, timeout=180):",
        "            sym = futures[future]",
        "            try:",
        "                result = future.result()",
        "                if result['signal'] != 'NEUTRAL':",
        "                    signals.append(result)",
        "            except Exception as e:",
        "                print(f'Error {sym}: {e}')",
        "    return sorted(signals, key=lambda x: x['score'], reverse=True)",
    ], accent=ACCENT_GREEN))
    story.append(sp(8))

    # ── 4.3 Microservices ────────────────────────────────────────────────────
    story.append(h2(styles, "4.3  Pattern Microservices pour Bots de Trading"))
    story.append(body(styles,
        "Une architecture microservices décompose le bot en services indépendants avec "
        "des responsabilités claires. Chaque service peut être déployé, mis à l'échelle "
        "et debuggé indépendamment. Architecture recommandée pour votre bot:"
    ))
    story.append(bp(styles, "<b>Service Data:</b> Collecte et normalisation des données (Alpaca API, stockage SQLite)"))
    story.append(bp(styles, "<b>Service Signal:</b> Calcul des indicateurs techniques et génération des signaux"))
    story.append(bp(styles, "<b>Service Sentiment:</b> Appels Groq/Gemini, cache des résultats (TTL 1h)"))
    story.append(bp(styles, "<b>Service Risk:</b> Validation Kelly, heat check, circuit breaker"))
    story.append(bp(styles, "<b>Service Execution:</b> Ordres Alpaca, gestion des positions ouvertes"))
    story.append(sp(8))

    # ── 4.4 Pipeline de données ───────────────────────────────────────────────
    story.append(h2(styles, "4.4  Optimisation du Pipeline de Données"))
    story.append(body(styles,
        "La qualité des données est le fondement de toute stratégie algorithmique. "
        "Pour 32 actifs avec des données 5-min, voici les optimisations critiques:"
    ))
    story.append(bp(styles, "<b>Compression:</b> Stocker les OHLCV en format Parquet (compression zstd) plutôt que CSV. 10x moins d'espace, 5x plus rapide à lire."))
    story.append(bp(styles, "<b>Indexation SQLite:</b> Créer des index sur (symbol, timestamp) pour les requêtes fréquentes. Gain de 100x sur les lookups."))
    story.append(bp(styles, "<b>Détection des gaps:</b> Vérifier la continuité temporelle des données avant calcul des indicateurs — une bougie manquante fausse le MACD."))
    story.append(bp(styles, "<b>Normalisation:</b> Utiliser des rendements logarithmiques plutôt que prix absolus pour la comparaison cross-assets."))

    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 5 — INDICATEURS AVANCÉS
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Section 5 — Indicateurs Avancés", styles['section_header']))
    story.append(SectionDivider(ACCENT_YELLOW))
    story.append(sp(8))

    # ── 5.1 Ichimoku ─────────────────────────────────────────────────────────
    story.append(h2(styles, "5.1  Nuage d'Ichimoku — Interprétation Complète"))
    story.append(body(styles,
        "L'Ichimoku Kinko Hyo est un système d'analyse technique complet développé par Goichi Hosoda "
        "en 1969. Il fournit simultanément: tendance, momentum, support/résistance et signaux d'entrée. "
        "Les fonds japonais et certains algorithmes de Citadel l'utilisent comme filtre de tendance primaire."
    ))
    story.append(sp(6))
    story.append(body(styles, "<b>Les 5 composantes:</b>"))
    story.append(bp(styles, "<b>Tenkan-sen (9):</b> Ligne de conversion — (highest_high + lowest_low) / 2 sur 9 périodes. Signal rapide."))
    story.append(bp(styles, "<b>Kijun-sen (26):</b> Ligne de base — même calcul sur 26 périodes. Support/résistance fort."))
    story.append(bp(styles, "<b>Senkou Span A:</b> (Tenkan + Kijun) / 2, projeté 26 périodes en avant. Bord du nuage."))
    story.append(bp(styles, "<b>Senkou Span B:</b> (H+L)/2 sur 52 périodes, projeté 26 en avant. Autre bord du nuage."))
    story.append(bp(styles, "<b>Chikou Span:</b> Close actuel projeté 26 périodes en arrière. Confirmation de tendance."))
    story.append(sp(6))

    story.append(CodeBlock([
        "# Calcul Ichimoku avec pandas-ta",
        "def ichimoku_signal(df: pd.DataFrame) -> str:",
        "    ich = ta.ichimoku(df['high'], df['low'], df['close'],",
        "                      tenkan=9, kijun=26, senkou=52)",
        "    isa_26 = ich[0]['ISA_9']   # Span A",
        "    isb_26 = ich[0]['ISB_26']  # Span B",
        "    its_9  = ich[0]['ITS_9']   # Tenkan",
        "    iks_26 = ich[0]['IKS_26']  # Kijun",
        "    ics_26 = ich[0]['ICS_26']  # Chikou",
        "",
        "    close = df['close'].iloc[-1]",
        "    # Condition bullish parfait ('Kumo Breakout')",
        "    bullish = (",
        "        close > max(isa_26.iloc[-1], isb_26.iloc[-1]) and  # Au-dessus du nuage",
        "        its_9.iloc[-1] > iks_26.iloc[-1] and              # TK cross haussier",
        "        ics_26.iloc[-27] > df['close'].iloc[-27]           # Chikou au-dessus des prix",
        "    )",
        "    return 'STRONG_BUY' if bullish else 'NEUTRAL'",
    ], accent=ACCENT_YELLOW))
    story.append(sp(8))

    # ── 5.2 Elliott Wave ─────────────────────────────────────────────────────
    story.append(h2(styles, "5.2  Vagues d'Elliott — Détection Algorithmique"))
    story.append(body(styles,
        "La théorie d'Elliott décrit les marchés en vagues impulsives (5 vagues dans la direction "
        "de la tendance) et correctives (3 vagues contre la tendance). La détection algorithmique "
        "utilise des pivots fractals et les ratios de Fibonacci (0.382, 0.618, 1.618) pour "
        "identifier les vagues et projeter les cibles."
    ))
    story.append(bp(styles, "<b>Vague 3:</b> Toujours la plus longue et la plus forte. Extension Fibonacci typique: 1.618x la vague 1. Signal d'entrée optimal."))
    story.append(bp(styles, "<b>Vague 4:</b> Correction, ne peut pas entrer dans le territoire de la vague 1. Retracement Fibonacci 0.382 de la vague 3."))
    story.append(bp(styles, "<b>Implémentation:</b> Bibliothèque <font color='#58A6FF'>elliott-wave-python</font> ou algorithme de détection de pivots avec scipy.signal."))
    story.append(sp(8))

    # ── 5.3 Harmoniques ──────────────────────────────────────────────────────
    story.append(h2(styles, "5.3  Patterns Harmoniques — Gartley, Butterfly, Bat"))
    story.append(body(styles,
        "Les patterns harmoniques combinent les retracements de Fibonacci avec des structures "
        "géométriques précises pour identifier des zones de retournement de haute probabilité. "
        "Chaque pattern a des ratios Fibonacci spécifiques pour les points XABCD."
    ))
    story.append(sp(4))

    harm_data = [
        [Paragraph("<b>Pattern</b>", styles['highlight']),
         Paragraph("<b>AB/XA</b>",   styles['highlight']),
         Paragraph("<b>BC/AB</b>",   styles['highlight']),
         Paragraph("<b>CD/BC</b>",   styles['highlight']),
         Paragraph("<b>PRZ cible</b>", styles['highlight'])],
        [Paragraph("Gartley",    styles['body']),
         Paragraph("0.618",      styles['body']),
         Paragraph("0.382-0.886", styles['body']),
         Paragraph("1.272-1.618", styles['body']),
         Paragraph("0.786 de XA", styles['body'])],
        [Paragraph("Butterfly",  styles['body']),
         Paragraph("0.786",      styles['body']),
         Paragraph("0.382-0.886", styles['body']),
         Paragraph("1.618-2.24", styles['body']),
         Paragraph("1.272 de XA", styles['body'])],
        [Paragraph("Bat",        styles['body']),
         Paragraph("0.382-0.5",  styles['body']),
         Paragraph("0.382-0.886", styles['body']),
         Paragraph("1.618-2.618", styles['body']),
         Paragraph("0.886 de XA", styles['body'])],
        [Paragraph("Crab",       styles['body']),
         Paragraph("0.382-0.618", styles['body']),
         Paragraph("0.382-0.886", styles['body']),
         Paragraph("2.618-3.14", styles['body']),
         Paragraph("1.618 de XA", styles['body'])],
    ]
    t2 = Table(harm_data, colWidths=[3*cm, 2.8*cm, 3.2*cm, 3.2*cm, 3.3*cm])
    t2.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), DARK_CARD),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [CODE_BG, HexColor('#1A2030')]),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(t2)
    story.append(sp(8))

    # ── 5.4 Microstructure ───────────────────────────────────────────────────
    story.append(h2(styles, "5.4  Microstructure de Marché — Analyse du Spread Bid-Ask"))
    story.append(body(styles,
        "Le spread bid-ask est le coût implicite de chaque transaction. Un spread élevé indique "
        "une liquidité faible et un impact de marché élevé. Pour votre bot avec 32 actifs, "
        "évitez les actifs avec spread > 0.1% en période calme. Le spread s'élargit typiquement "
        "à l'ouverture (9h30), à la fermeture, et avant les annonces économiques."
    ))
    story.append(bp(styles, "<b>Règle pratique:</b> Ne pas trader les actifs dont le spread représente > 20% du take-profit attendu."))
    story.append(bp(styles, "<b>Source gratuite:</b> Alpaca API fournit le bid/ask dans les données de quote (<font color='#58A6FF'>get_latest_quote()</font>)."))
    story.append(sp(8))

    # ── 5.5 Options Flow ─────────────────────────────────────────────────────
    story.append(h2(styles, "5.5  Options Flow — Indicateur Avancé (Sources Gratuites)"))
    story.append(body(styles,
        "Le flux d'options (unusual options activity) précède souvent les mouvements de prix des "
        "actions sous-jacentes de 1-3 jours. Les grands acteurs institutionnels ne peuvent pas "
        "masquer leurs transactions en options. Sources gratuites disponibles:"
    ))
    story.append(bp(styles, "<b>Market Chameleon (marketchameleon.com):</b> Volume d'options inhabituel, ratio put/call par action. Scraping éthique possible."))
    story.append(bp(styles, "<b>Barchart (barchart.com/options):</b> Top options activity gratuit avec export CSV."))
    story.append(bp(styles, "<b>Intégration recommandée:</b> Filtrer les actifs de votre liste (32 assets) avec ratio Put/Call > 2.0 comme signal bearish fort."))

    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 6 — BACKTESTING & VALIDATION
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Section 6 — Backtesting &amp; Validation", styles['section_header']))
    story.append(SectionDivider(ACCENT_BLUE))
    story.append(sp(8))

    story.append(body(styles,
        "Un backtest non rigoureux est pire qu'aucun backtest — il crée une fausse confiance. "
        "Les biais les plus courants sont: le look-ahead bias (utiliser des données futures), "
        "le survivorship bias (tester sur des actifs encore existants), et l'overfitting "
        "(sur-adapter les paramètres à l'historique)."
    ))
    story.append(sp(8))

    # ── 6.1 Walk-forward ─────────────────────────────────────────────────────
    story.append(h2(styles, "6.1  Walk-Forward Optimization — Éviter l'Overfitting"))
    story.append(body(styles,
        "La Walk-Forward Optimization (WFO) divise les données en fenêtres glissantes: "
        "entraînement (in-sample) suivi de test (out-of-sample). Les paramètres optimisés "
        "sur la fenêtre d'entraînement sont testés sur la fenêtre suivante, simulant "
        "les conditions réelles. C'est la méthode standard de validation des bots Freqtrade."
    ))
    story.append(sp(6))

    story.append(CodeBlock([
        "# Walk-Forward Optimization",
        "def walk_forward_test(df: pd.DataFrame, strategy_fn,",
        "                       train_size=252, test_size=63) -> list:",
        "    \"\"\"",
        "    train_size: ~1 an de données d'entraînement",
        "    test_size:  ~1 trimestre de test out-of-sample",
        "    \"\"\"",
        "    results = []",
        "    n = len(df)",
        "    step = test_size",
        "",
        "    for start in range(0, n - train_size - test_size, step):",
        "        train_end = start + train_size",
        "        test_end  = train_end + test_size",
        "",
        "        train_df = df.iloc[start:train_end]",
        "        test_df  = df.iloc[train_end:test_end]",
        "",
        "        # Optimiser les paramètres sur train",
        "        best_params = optimize_params(train_df, strategy_fn)",
        "",
        "        # Tester sur out-of-sample",
        "        oos_result = backtest(test_df, strategy_fn, best_params)",
        "        results.append({'period': f'{train_end}-{test_end}',",
        "                        'sharpe': oos_result['sharpe'],",
        "                        'params': best_params})",
        "    return results",
    ], accent=ACCENT_BLUE))
    story.append(sp(8))

    # ── 6.2 Monte Carlo ───────────────────────────────────────────────────────
    story.append(h2(styles, "6.2  Simulation Monte Carlo — Évaluation du Risque"))
    story.append(body(styles,
        "La simulation Monte Carlo génère des milliers de scénarios en permutant "
        "aléatoirement l'ordre des trades. Elle révèle la distribution des drawdowns "
        "possibles et le risque de ruine pour un niveau de confiance donné. "
        "Résultat: 'Il y a 5% de chances d'avoir un drawdown supérieur à -15%.'"
    ))
    story.append(sp(6))

    story.append(CodeBlock([
        "# Simulation Monte Carlo sur séquence de trades",
        "def monte_carlo_simulation(trade_returns: list,",
        "                            n_simulations=10000) -> dict:",
        "    results = []",
        "    for _ in range(n_simulations):",
        "        shuffled = np.random.choice(trade_returns,",
        "                                     size=len(trade_returns),",
        "                                     replace=True)",
        "        equity = np.cumprod(1 + np.array(shuffled))",
        "        max_dd = (equity / equity.cummax() - 1).min()",
        "        results.append({'final_equity': equity[-1],",
        "                        'max_drawdown': max_dd})",
        "",
        "    df_results = pd.DataFrame(results)",
        "    return {",
        "        'worst_5pct_dd':  df_results['max_drawdown'].quantile(0.05),",
        "        'median_return':  df_results['final_equity'].median() - 1,",
        "        'ruin_prob':      (df_results['final_equity'] < 0.5).mean()",
        "    }",
    ], accent=ACCENT_BLUE))
    story.append(sp(8))

    # ── 6.3 Out-of-sample ────────────────────────────────────────────────────
    story.append(h2(styles, "6.3  Méthodologie Out-of-Sample"))
    story.append(body(styles,
        "Règle d'or: ne jamais voir les données de test pendant le développement. "
        "Structurer les données comme suit:"
    ))
    story.append(bp(styles, "<b>Training set (60%):</b> 2018-2021 — Développement de la stratégie et optimisation initiale"))
    story.append(bp(styles, "<b>Validation set (20%):</b> 2022-2023 — Ajustement des hyperparamètres, sélection du modèle"))
    story.append(bp(styles, "<b>Test set (20%):</b> 2024-2025 — Évaluation finale, toucher une seule fois"))
    story.append(bp(styles, "<b>Règle des 5:1:</b> Freqtrade recommande au minimum 5 trades par paramètre optimisé pour éviter l'overfitting"))
    story.append(sp(8))

    # ── 6.4 Sensibilité ───────────────────────────────────────────────────────
    story.append(h2(styles, "6.4  Analyse de Sensibilité des Paramètres"))
    story.append(body(styles,
        "Un paramètre robuste est peu sensible à de petites variations. Si changer RSI de 14 "
        "à 13 ou 15 fait chuter le Sharpe de 1.5 à 0.3, le paramètre est fragile et probablement "
        "overfit. Testez ±20% autour de chaque paramètre optimal et visualisez la surface de performance."
    ))
    story.append(bp(styles, "<b>Paramètres à tester en priorité:</b> RSI period, EMA lengths, MACD parameters, ADX threshold, ATR multiplier"))
    story.append(bp(styles, "<b>Outil recommandé:</b> Optuna (bayesian optimization) pour l'exploration efficace de l'espace des hyperparamètres"))
    story.append(sp(8))

    # ── 6.5 Modélisation des coûts ───────────────────────────────────────────
    story.append(h2(styles, "6.5  Modélisation des Coûts de Transaction"))
    story.append(body(styles,
        "Une stratégie profitable en backtest sans coûts peut être perdante en réel. "
        "Composantes à modéliser: commission (Alpaca: $0 pour actions, $0.003/contrat options), "
        "spread bid-ask (0.01-0.05% actions liquides), slippage (~0.02-0.1% selon la liquidité)."
    ))
    story.append(bp(styles, "<b>Slippage modèle:</b> <font color='#58A6FF'>slippage = alpha * sqrt(qty / ADV)</font> où ADV = Average Daily Volume. Typiquement 5-10 bps pour les trades < 0.1% de l'ADV."))
    story.append(bp(styles, "<b>Impact du turnover:</b> Si votre bot fait 10 trades/jour sur 32 actifs, le coût implicite total peut dépasser 1% par mois — assurez-vous que l'edge est suffisant."))

    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 7 — CAS D'USAGE & BOTS RÉELS
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Section 7 — Cas d'Usage &amp; Bots Réels", styles['section_header']))
    story.append(SectionDivider(ACCENT_GREEN))
    story.append(sp(8))

    # ── 7.1 Freqtrade ────────────────────────────────────────────────────────
    story.append(h2(styles, "7.1  Freqtrade — Architecture du Bot Open-Source de Référence"))
    story.append(body(styles,
        "<b>Freqtrade</b> (github.com/freqtrade/freqtrade — 28k+ étoiles) est le bot de trading "
        "algorithmique open-source le plus mature. Écrit en Python, il supporte Binance, "
        "Kraken, et d'autres exchanges. Son architecture est un modèle d'excellence:"
    ))
    story.append(bp(styles, "<b>Strategy class:</b> Chaque stratégie est une classe Python héritant de <font color='#58A6FF'>IStrategy</font>. Méthodes clés: <font color='#58A6FF'>populate_indicators()</font>, <font color='#58A6FF'>populate_entry_trend()</font>, <font color='#58A6FF'>populate_exit_trend()</font>."))
    story.append(bp(styles, "<b>FreqAI:</b> Module ML intégré supportant CatBoost, LightGBM, scikit-learn. Utilise la WFO automatiquement. Produit les labels et features automatiquement."))
    story.append(bp(styles, "<b>Backtesting:</b> Commande <font color='#58A6FF'>freqtrade backtesting</font> avec simulation de spread et slippage. Résultats: Sharpe, Calmar, Profit Factor, max drawdown."))
    story.append(bp(styles, "<b>Hyperopt:</b> Optimisation bayésienne native des paramètres de stratégie avec Hyperopt ou Optuna backend."))
    story.append(sp(6))

    story.append(CodeBlock([
        "# Pattern Freqtrade: stratégie MTF avec FreqAI",
        "class MyStrategy(IStrategy):",
        "    # Timeframe principal",
        "    timeframe = '5m'",
        "    # Timeframe informatif (4h pour tendance)",
        "    informative_timeframes = ['1h', '4h']",
        "",
        "    def informative_pairs(self):",
        "        return [(pair, tf) for pair in self.dp.current_whitelist()",
        "                for tf in self.informative_timeframes]",
        "",
        "    @informative('4h')",
        "    def populate_indicators_4h(self, df, metadata):",
        "        df['ema100'] = ta.EMA(df['close'], timeperiod=100)",
        "        df['trend']  = (df['close'] > df['ema100']).astype(int)",
        "        return df",
        "",
        "    def populate_entry_trend(self, df, metadata):",
        "        df.loc[",
        "            (df['trend_4h'] == 1) &   # Tendance 4h haussière",
        "            (df['rsi'] < 40) &         # RSI survente",
        "            (df['volume'] > 0),",
        "            'enter_long'] = 1",
        "        return df",
    ], accent=ACCENT_GREEN))
    story.append(sp(8))

    # ── 7.2 Jesse ────────────────────────────────────────────────────────────
    story.append(h2(styles, "7.2  Jesse — Patterns de Stratégie Python Avancés"))
    story.append(body(styles,
        "<b>Jesse</b> (jesse.trade) est un framework Python moderne pour le trading algorithmique, "
        "conçu pour la simplicité et la puissance. Contrairement à Freqtrade, Jesse utilise un "
        "paradigme orienté événements avec des callbacks clairs. Particulièrement apprécié pour:"
    ))
    story.append(bp(styles, "<b>Structure de stratégie:</b> Méthodes <font color='#58A6FF'>before()</font>, <font color='#58A6FF'>go_long()</font>, <font color='#58A6FF'>go_short()</font>, <font color='#58A6FF'>should_cancel_entry()</font> — très lisible."))
    story.append(bp(styles, "<b>Gestion du risque native:</b> <font color='#58A6FF'>self.buy = qty, entry_price</font> avec stop-loss et take-profit automatiquement gérés."))
    story.append(bp(styles, "<b>Indicateurs:</b> Bibliothèque complète <font color='#58A6FF'>jesse.indicators</font>, compatible avec votre stack pandas-ta."))
    story.append(bp(styles, "<b>Leçon clé pour votre bot:</b> Jesse sépare strictement la logique de signal (strategy) de la logique d'exécution (jesse core) — ce pattern réduit les bugs."))
    story.append(sp(8))

    # ── 7.3 Hummingbot ───────────────────────────────────────────────────────
    story.append(h2(styles, "7.3  Hummingbot — Market-Making Algorithmique"))
    story.append(body(styles,
        "<b>Hummingbot</b> (hummingbot.io) est spécialisé dans le market-making — poser "
        "simultanément des ordres bid et ask pour capturer le spread. "
        "La stratégie Pure Market Making pose des ordres des deux côtés du carnet "
        "à des distances dynamiques basées sur la volatilité (ATR). En crypto, "
        "cette stratégie peut générer 0.1-0.5% quotidien sur BTC/ETH en conditions normales."
    ))
    story.append(bp(styles, "<b>Application à votre bot:</b> Sur BTC/USD et ETH/USD, une stratégie de market-making avec Alpaca pourrait compléter vos stratégies directionnelles existantes."))
    story.append(bp(styles, "<b>Paramètres clés:</b> Bid/Ask spread (0.1-0.3%), order refresh time (30s-5min), inventory skew (éviter l'accumulation unidirectionnelle)."))
    story.append(sp(8))

    # ── 7.4 Renaissance ───────────────────────────────────────────────────────
    story.append(h2(styles, "7.4  Renaissance Technologies — Leçons du Medallion Fund"))
    story.append(body(styles,
        "Le <b>Medallion Fund</b> de Renaissance Technologies est le fonds le plus performant "
        "de l'histoire: +66% annualisé brut depuis 1988. Fondé par Jim Simons (mathématicien), "
        "voici les principes publiquement connus de leur approche:"
    ))
    story.append(bp(styles, "<b>Données alternatives:</b> Renaissance exploite massivement des données non-conventionnelles: données satellites, de transaction, météo, linguistiques."))
    story.append(bp(styles, "<b>Diversification extrême:</b> Milliers de micro-signaux non-corrélés plutôt qu'une seule grande stratégie. La somme des edges crée une performance robuste."))
    story.append(bp(styles, "<b>Exécution optimale:</b> 50% de la performance est attribuée à l'optimisation de l'exécution (réduction du market impact) selon certains analystes."))
    story.append(bp(styles, "<b>Mean reversion à haute fréquence:</b> La majorité de leurs stratégies exploitent la mean-reversion sur des timeframes de minutes à heures."))
    story.append(bp(styles, "<b>Aucun jugement humain:</b> Les décisions sont 100% algorithmiques. Aucune exception, aucun override humain."))
    story.append(sp(8))

    # ── 7.5 Two Sigma / Citadel ───────────────────────────────────────────────
    story.append(h2(styles, "7.5  Two Sigma &amp; Citadel — Stratégies Quantitatives"))
    story.append(body(styles,
        "<b>Two Sigma</b> (65+ milliards AUM) et <b>Citadel</b> (63+ milliards AUM) sont les "
        "leaders du trading quantitatif institutionnel. Leurs stratégies publiquement connues:"
    ))
    story.append(sp(6))
    story.append(body(styles, "<b>Two Sigma — Approches documentées:</b>"))
    story.append(bp(styles, "<b>Statistical Arbitrage:</b> Paires d'actions coïntégrées (ex: MSFT/AAPL). Quand le spread s'écarte de 2 sigma, parier sur le retour à la moyenne."))
    story.append(bp(styles, "<b>Factor Investing:</b> Modèles multi-facteurs (momentum, value, quality, low-volatility). Portfolio optimisé par Markowitz avec contraintes de risque."))
    story.append(bp(styles, "<b>NLP massif:</b> Analyse de milliers de transcripts d'earnings calls, brevets, publications scientifiques pour signals alternatifs."))
    story.append(sp(6))
    story.append(body(styles, "<b>Citadel — Stratégies documentées:</b>"))
    story.append(bp(styles, "<b>Global Macro Quantitative:</b> Modèles macro (taux d'intérêt, FX, commodities) avec ML pour prédire les décisions des banques centrales."))
    story.append(bp(styles, "<b>Equities Long/Short:</b> Score propriétaire combinant fondamentaux + momentum + sentiment. Top quintile long, bottom quintile short."))
    story.append(bp(styles, "<b>Leçon actionnable:</b> Commencer par le statistical arbitrage entre vos 30 actions — calculer les paires coïntégrées avec <font color='#58A6FF'>statsmodels.tsa.stattools.coint()</font>."))

    story.append(PageBreak())

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 8 — CONCLUSION & FEUILLE DE ROUTE
    # ══════════════════════════════════════════════════════════════════════════
    story.append(Paragraph("Conclusion &amp; Feuille de Route d'Implémentation", styles['section_header']))
    story.append(SectionDivider(ACCENT_BLUE))
    story.append(sp(8))

    story.append(body(styles,
        "Ce guide a couvert l'ensemble des axes d'amélioration possibles pour votre bot de "
        "trading algorithmique. La clé du succès est une implémentation progressive et rigoureuse, "
        "avec validation à chaque étape. Voici une feuille de route recommandée par ordre de "
        "priorité et d'impact:"
    ))
    story.append(sp(10))

    roadmap_data = [
        [Paragraph("<b>Priorité</b>", styles['highlight']),
         Paragraph("<b>Amélioration</b>", styles['highlight']),
         Paragraph("<b>Impact</b>", styles['highlight']),
         Paragraph("<b>Effort</b>", styles['highlight'])],
        [Paragraph("1 — Urgent", styles['body']),
         Paragraph("Circuit Breaker journalier (Section 2.4)", styles['body']),
         Paragraph("Critique", styles['body']),
         Paragraph("1 jour", styles['body'])],
        [Paragraph("2 — Haute", styles['body']),
         Paragraph("Portfolio Heat + Kelly Criterion (2.1-2.3)", styles['body']),
         Paragraph("Très élevé", styles['body']),
         Paragraph("3 jours", styles['body'])],
        [Paragraph("3 — Haute", styles['body']),
         Paragraph("ATR Stop-loss dynamique (2.2)", styles['body']),
         Paragraph("Élevé", styles['body']),
         Paragraph("2 jours", styles['body'])],
        [Paragraph("4 — Haute", styles['body']),
         Paragraph("VWAP + Divergences RSI (1.2-1.3)", styles['body']),
         Paragraph("Élevé", styles['body']),
         Paragraph("3 jours", styles['body'])],
        [Paragraph("5 — Moyenne", styles['body']),
         Paragraph("Multi-timeframe 1h+4h (1.1)", styles['body']),
         Paragraph("Élevé", styles['body']),
         Paragraph("1 semaine", styles['body'])],
        [Paragraph("6 — Moyenne", styles['body']),
         Paragraph("Walk-Forward Backtesting (6.1)", styles['body']),
         Paragraph("Élevé", styles['body']),
         Paragraph("1 semaine", styles['body'])],
        [Paragraph("7 — Moyenne", styles['body']),
         Paragraph("Traitement parallèle 32 actifs (4.2)", styles['body']),
         Paragraph("Moyen", styles['body']),
         Paragraph("2 jours", styles['body'])],
        [Paragraph("8 — Faible", styles['body']),
         Paragraph("Ichimoku + Volume Profile (5.1, 1.4)", styles['body']),
         Paragraph("Moyen", styles['body']),
         Paragraph("2 semaines", styles['body'])],
        [Paragraph("9 — Faible", styles['body']),
         Paragraph("LSTM / Régime HMM (3.2-3.3)", styles['body']),
         Paragraph("Variable", styles['body']),
         Paragraph("1 mois", styles['body'])],
        [Paragraph("10 — Long terme", styles['body']),
         Paragraph("Statistical Arb paires (7.5)", styles['body']),
         Paragraph("Élevé", styles['body']),
         Paragraph("2 mois", styles['body'])],
    ]
    t3 = Table(roadmap_data, colWidths=[3.5*cm, 7.5*cm, 2.5*cm, 2.5*cm])
    t3.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), DARK_CARD),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [CODE_BG, HexColor('#1A2030')]),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 1), (0, 1), HexColor('#3D1515')),
        ('BACKGROUND', (0, 2), (0, 4), HexColor('#2D2515')),
        ('BACKGROUND', (0, 5), (0, 7), HexColor('#1A2515')),
    ]))
    story.append(t3)
    story.append(sp(16))

    story.append(body(styles,
        "<b>Principe de Pareto appliqué au trading algorithmique:</b> 80% de l'amélioration "
        "de performance viendra de 20% des changements. Les priorités 1 à 4 (gestion du risque "
        "et signaux de base) ont historiquement le plus grand impact. Ne pas négliger la "
        "validation rigoureuse (Section 6) avant tout déploiement en production."
    ))
    story.append(sp(10))

    story.append(SectionDivider(ACCENT_BLUE))
    story.append(sp(10))

    story.append(body(styles,
        "<b>Ressources recommandées pour aller plus loin:</b>"
    ))
    story.append(bp(styles, "<b>Livres:</b> 'Advances in Financial Machine Learning' (Lopez de Prado), 'Algorithmic Trading' (Ernest Chan), 'Inside the Black Box' (Rishi Narang)"))
    story.append(bp(styles, "<b>Repositories GitHub:</b> freqtrade/freqtrade, jesse-ai/jesse, hummingbot/hummingbot, microsoft/qlib, AI4Finance-Foundation/FinRL"))
    story.append(bp(styles, "<b>Données gratuites:</b> Yahoo Finance (yfinance), Alpha Vantage (free tier), FRED (macroéconomie), Quandl"))
    story.append(bp(styles, "<b>Communautés:</b> r/algotrading, QuantConnect forums, Freqtrade Discord, Twitter/X #quanttrading"))
    story.append(sp(16))

    story.append(Paragraph(
        "<i>Ce guide a été rédigé à des fins éducatives. Le trading algorithmique comporte "
        "des risques financiers significatifs. Toujours valider rigoureusement en paper trading "
        "avant tout déploiement avec du capital réel.</i>",
        ParagraphStyle('disclaimer', fontName='Helvetica-Oblique', fontSize=8,
                       textColor=TEXT_MUTED, leading=12, alignment=TA_CENTER)
    ))

    return story


# ── Génération du PDF ──────────────────────────────────────────────────────────
def generate_pdf():
    print(f"Génération du PDF: {OUTPUT_PATH}")
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    styles    = build_styles()
    pt        = PageTemplate()
    content   = build_content(styles)

    doc = SimpleDocTemplate(
        OUTPUT_PATH,
        pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.8*cm,  bottomMargin=1.8*cm,
        title="Guide d'Amélioration d'un Bot de Trading Algorithmique",
        author="Trading Bot Guide",
        subject="Algorithmic Trading — Amélioration du Bot",
    )

    # Première page: couverture personnalisée
    def first_page(canvas_obj, doc_obj):
        build_cover_page(canvas_obj, doc_obj)

    def later_pages(canvas_obj, doc_obj):
        pt.on_page(canvas_obj, doc_obj)

    doc.build(content, onFirstPage=first_page, onLaterPages=later_pages)
    print(f"PDF généré avec succès: {OUTPUT_PATH}")
    size = os.path.getsize(OUTPUT_PATH) / 1024
    print(f"Taille du fichier: {size:.1f} Ko")


if __name__ == "__main__":
    generate_pdf()
