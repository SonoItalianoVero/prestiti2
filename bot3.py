import os
import io
from datetime import datetime

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from xml.sax.saxutils import escape
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    Image,
    KeepTogether,
    KeepInFrame,
)
from reportlab.lib.utils import ImageReader


from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# === timezone Rome (Europe/Rome) ===
try:
    from zoneinfo import ZoneInfo
    TZ_ROME = ZoneInfo("Europe/Rome")
except Exception:
    TZ_ROME = None

def now_rome_str() -> str:
    if TZ_ROME:
        dt = datetime.now(TZ_ROME)
    else:
        dt = datetime.now()
    return dt.strftime("%d/%m/%y %H:%M")

def now_rome_date() -> str:
    if TZ_ROME:
        dt = datetime.now(TZ_ROME)
    else:
        dt = datetime.now()
    return dt.strftime("%d/%m/%Y")


try:
    pdfmetrics.registerFont(TTFont("PTMono", "fonts/PTMono-Regular.ttf"))
    pdfmetrics.registerFont(TTFont("PTMono-Bold", "fonts/PTMono-Bold.ttf"))
    _PTMONO = "PTMono"
    _PTMONO_B = "PTMono-Bold"
except Exception:
    _PTMONO = "Courier"
    _PTMONO_B = "Courier-Bold"

# --------------------- ИСХОДНАЯ ЧАСТЬ (контракт) ---------------------

ASK_CLIENTE, ASK_IMPORTO, ASK_TAN, ASK_TAEG, ASK_DURATA = range(5)

TOKEN = os.getenv("BOT_TOKEN")

MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("Сделать контракт"), KeyboardButton("Создать Мандат")],
        [KeyboardButton("АМЛ Комиссия"), KeyboardButton("Комиссия 2"), KeyboardButton("Комиссия 3")],
    ],
    resize_keyboard=True,
)

SIG_TARGET_W   = 72 * mm
SIG_MAX_H      = 34 * mm
SIG_ROW_H      = 36 * mm
SIG_BOTTOM_PAD = -8
SIG_LINE_THICK = 1.2

def fmt_eur(v: float) -> str:
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"€ {s}"

def parse_num(txt: str) -> float:
    t = txt.strip().replace(" ", "")
    t = t.replace(".", "").replace(",", ".")
    return float(t)

def monthly_payment(principal: float, tan_percent: float, months: int) -> float:
    if months <= 0:
        return 0.0
    r = (tan_percent / 100.0) / 12.0
    if r == 0:
        return principal / months
    return principal * (r / (1 - (1 + r) ** (-months)))

def sig_image(path: str, target_w=SIG_TARGET_W, max_h=SIG_MAX_H):
    if not os.path.exists(path):
        return None
    ir = ImageReader(path)
    iw, ih = ir.getSize()
    ratio = ih / float(iw)
    w = target_w
    h = w * ratio
    if h > max_h:
        h = max_h
        w = h / ratio
    return Image(path, width=w, height=h)

def draw_border_and_pagenum(canv, doc):
    width, height = A4
    canv.saveState()
    outer_margin = 10 * mm
    inner_offset = 6
    line_w = 2
    canv.setStrokeColor(colors.red)
    canv.setLineWidth(line_w)
    canv.rect(outer_margin, outer_margin, width - 2*outer_margin, height - 2*outer_margin, stroke=1, fill=0)
    canv.rect(
        outer_margin + inner_offset,
        outer_margin + inner_offset,
        width - 2*(outer_margin + inner_offset),
        height - 2*(outer_margin + inner_offset),
        stroke=1,
        fill=0,
    )
    canv.setFont(_PTMONO, 9)
    canv.setFillColor(colors.black)
    canv.drawCentredString(width/2.0, 5*mm, str(canv.getPageNumber()))
    canv.restoreState()

def build_pdf(values: dict) -> bytes:

    cliente = (values.get("cliente", "") or "").strip()
    importo = float(values.get("importo", 0) or 0)
    tan     = float(values.get("tan", 0) or 0)
    taeg    = float(values.get("taeg", 0) or 0)
    durata  = int(values.get("durata", 0) or 0)

    rata       = monthly_payment(importo, tan, durata)
    interessi  = max(rata * durata - importo, 0)
    totale     = importo + interessi

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm,
        topMargin=15*mm, bottomMargin=15*mm,
    )


    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="H1Mono",   fontName=_PTMONO_B, fontSize=16.2, leading=18, spaceAfter=4))
    styles.add(ParagraphStyle(name="H2Mono",   fontName=_PTMONO_B, fontSize=13.0, leading=15, spaceBefore=6, spaceAfter=3))
    styles.add(ParagraphStyle(name="Mono",     fontName=_PTMONO,   fontSize=10.8, leading=12.6))
    styles.add(ParagraphStyle(name="MonoSm",   fontName=_PTMONO,   fontSize=10.0, leading=11.3))  # компактно
    styles.add(ParagraphStyle(name="MonoXs",   fontName=_PTMONO,   fontSize=9.2,  leading=10.4))
    styles.add(ParagraphStyle(name="RightXs",  fontName=_PTMONO,   fontSize=9.4,  leading=11, alignment=2))
    styles.add(ParagraphStyle(name="SigHead",  fontName=_PTMONO,   fontSize=12.0, leading=14, alignment=1))
    styles.add(ParagraphStyle(name="SigCap",   fontName=_PTMONO,   fontSize=10.2, leading=12, alignment=1))

    story = []

    def _logo_row():
        cells = []
        for p, w in [("banca_dalba_logo.png", 90*mm), ("bcc_logo.png", 18*mm), ("2fin_logo.png", 18*mm)]:
            if os.path.exists(p):
                ir = ImageReader(p); iw, ih = ir.getSize()
                h = 18*mm
                cells.append(Image(p, width=w, height=h))
            else:
                cells.append(Paragraph("", styles["Mono"]))
        t = Table([cells], colWidths=[110*mm, 25*mm, 25*mm])
        t.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"), ("ALIGN",(1,0),(2,0),"RIGHT")]))
        return t

    story += [_logo_row(), Spacer(1, 2)]
    story.append(Paragraph("Banca d'Alba — Credito Cooperativo", styles["H1Mono"]))
    story.append(Paragraph("Sede Legale: Via Cavour 4, 12051 Alba (CN)", styles["MonoSm"]))
    story.append(Paragraph("Approvazione bancaria confermata – Documento preliminare", styles["H1Mono"]))
    story.append(Spacer(1, 1))

    story.append(Paragraph(f"Cliente: {cliente or '____________________'}", styles["Mono"]))
    story.append(Paragraph("La banca ha approvato la concessione del credito; il presente è un documento preliminare di notifica delle condizioni.", styles["MonoSm"]))
    story.append(Paragraph("Comunicazioni e gestione pratica: 2FIN SRL (Agente in attivita finanziaria – OAM A15135)", styles["MonoSm"]))
    story.append(Paragraph("Contatto: Telegram @operatore_2fin", styles["MonoSm"]))
    story.append(Paragraph(f"Creato: {now_rome_date()}", styles["RightXs"]))
    story.append(Spacer(1, 2))

    status_tbl = Table([
        [Paragraph("<b>Stato pratica:</b>", styles["Mono"]),
         Paragraph("<b>APPROVATO</b> (conferma dell’istituto)", styles["Mono"])],
        [Paragraph("<b>Tipo documento:</b>", styles["Mono"]),
         Paragraph("<b>Pre-contratto / Documento preliminare</b>", styles["Mono"])],
        [Paragraph("<b>Manca ancora:</b>", styles["Mono"]),
         Paragraph("invio del contratto definitivo e del piano di ammortamento", styles["Mono"])],
        [Paragraph("<b>Erogazione:</b>", styles["Mono"]),
         Paragraph("dopo la firma del contratto definitivo", styles["Mono"])],
    ], colWidths=[43*mm, doc.width-43*mm])
    status_tbl.setStyle(TableStyle([
        ("BOX", (0,0), (-1,-1), 0.9, colors.HexColor("#96A6C8")),
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#EEF3FF")),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING",(0,0), (-1,-1), 6),
        ("RIGHTPADDING",(0,0),(-1,-1), 6),
        ("TOPPADDING",(0,0),(-1,-1), 3),
        ("BOTTOMPADDING",(0,0),(-1,-1), 3),
    ]))
    story += [status_tbl, Spacer(1, 4)]

    params = [
        ["Parametro", "Dettagli"],
        ["Importo del credito", fmt_eur(importo)],
        ["Tasso fisso (TAN)",  f"{tan:.2f} %"],
        ["TAEG indicativo",    f"{taeg:.2f} %"],
        ["Durata",             f"{durata} mesi"],
        ["Rata mensile*",      fmt_eur(rata)],
        ["Spese di istruttoria", "€ 0"],
        ["Commissione incasso", "€ 0"],
        ["Contributo amministrativo", "€ 0"],
        ["Premio assicurativo", "€ 280 (se richiesto)"],
        ["Erogazione fondi",   "30-60 min dopo la firma del contratto finale"],
    ]
    tbl = Table(params, colWidths=[75*mm, doc.width-75*mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#ececec")),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("ALIGN",(0,0),(-1,0),"CENTER"),
        ("GRID",(0,0),(-1,-1),0.3,colors.grey),
        ("FONTNAME",(0,1),(-1,-1),_PTMONO),
        ("FONTSIZE",(0,1),(-1,-1),10.2),
        ("LEFTPADDING",(0,0),(-1,-1),5),
        ("RIGHTPADDING",(0,0),(-1,-1),5),
        ("TOPPADDING",(0,0),(-1,-1),2.2),
        ("BOTTOMPADDING",(0,0),(-1,-1),2.2),
    ]))
    story += [tbl, Spacer(1, 1)]
    story.append(Paragraph("*Rata calcolata alla data dell'offerta.", styles["MonoXs"]))
    story.append(Spacer(1, 4))

    story.append(Paragraph("Vantaggi", styles["H2Mono"]))
    for it in [
        "• Possibilità di sospendere fino a 3 rate",
        "• Estinzione anticipata senza penali",
        "• Riduzione del TAN -0,10 p.p. ogni 12 mesi puntuali (fino a 5,95%)",
        "• Sospensione straordinaria delle rate in caso di perdita del lavoro (previo consenso della banca)",
    ]:
        story.append(Paragraph(it, styles["MonoSm"]))

    story.append(Paragraph("Penali e interessi di mora", styles["H2Mono"]))
    for it in [
        "• Ritardo oltre 5 giorni: TAN + 2 p.p.",
        "• Sollecito: €10 cartaceo / €5 digitale",
        "• 2 rate non pagate: risoluzione del contratto e recupero crediti",
        "• Penale per risoluzione anticipata solo in caso di violazione delle condizioni contrattuali",
    ]:
        story.append(Paragraph(it, styles["MonoSm"]))

    story.append(Paragraph("Comunicazioni e pagamento servizi 2FIN", styles["H2Mono"]))
    for it in [
        "• Tutte le comunicazioni tra banca e cliente gestite solo tramite 2FIN SRL.",
        "• Contratto e allegati inviati in PDF via Telegram.",
        "• Servizi 2FIN – quota fissa €100 (non commissione bancaria), pagamento via SEPA / SEPA Instant al commercialista indipendente.",
    ]:
        story.append(Paragraph(it, styles["MonoSm"]))

    # ---- Страница 2 ----
    story.append(PageBreak())

    faq = (
        'Domanda frequente: “Pre-approvazione = approvazione?”<br/>'
        '<b>Risposta:</b> Sì: la concessione è approvata; questo file è il pre-contratto informativo. '
        'Il vincolo giuridico nasce con la firma del contratto definitivo.'
    )
    faq_box = Table([[Paragraph(faq, styles["MonoSm"])]], colWidths=[doc.width])
    faq_box.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),0.9,colors.HexColor("#96A6C8")),
        ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#EEF3FF")),
        ("LEFTPADDING",(0,0),(-1,-1),6),
        ("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),3),
        ("BOTTOMPADDING",(0,0),(-1,-1),3),
    ]))
    story += [faq_box, Spacer(1, 6)]

    # riepilogo
    story.append(Paragraph("Riepilogo economico", styles["H2Mono"]))
    riepilogo = Table([
        ["Importo del credito", fmt_eur(importo)],
        ["Interessi stimati (durata)", fmt_eur(interessi)],
        ["Spese una tantum", "€ 0"],
        ["Commissione incasso", "€ 0"],
        ["Totale dovuto (stima)", fmt_eur(totale)],
        ["Durata", f"{durata} mesi"],
    ], colWidths=[75*mm, doc.width-75*mm])
    riepilogo.setStyle(TableStyle([
        ("GRID",(0,0),(-1,-1),0.3,colors.grey),
        ("BACKGROUND",(0,0),(-1,-1),colors.whitesmoke),
        ("FONTNAME",(0,0),(-1,-1),_PTMONO),
        ("FONTSIZE",(0,0),(-1,-1),10.2),
        ("LEFTPADDING",(0,0),(-1,-1),5),
        ("RIGHTPADDING",(0,0),(-1,-1),5),
        ("TOPPADDING",(0,0),(-1,-1),2.2),
        ("BOTTOMPADDING",(0,0),(-1,-1),2.2),
    ]))
    story += [riepilogo, Spacer(1, 5)]

    story.append(Paragraph("Informazioni legali (estratto)", styles["H2Mono"]))
    for it in [
        "• L'offerta è preliminare e pre-approvata: con l'accettazione del cliente diventa vincolante alle condizioni sopra descritte.",
        "• Il TAEG è indicativo e può variare alla data di firma del contratto.",
        "• Il cliente ha diritto a ricevere SECCI e piano di ammortamento completo dopo la firma.",
        "• Il cliente ha diritto di recesso nei termini di legge.",
        "• Reclami tramite 2FIN o Arbitro Bancario Finanziario (ABF).",
        "• Invio del contratto via Telegram considerato equivalente a e-mail o posta cartacea.",
        "• Pagamento servizi 2FIN solo via SEPA/SEPA Instant al commercialista indipendente.",
        "• Trattamento dati personali secondo la normativa vigente.",
    ]:
        story.append(Paragraph(it, styles["MonoSm"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Firme", styles["H2Mono"]))
    head_l = Paragraph("Firma Cliente", styles["SigHead"])
    head_c = Paragraph("Firma Rappresentante<br/>Banca d'Alba", styles["SigHead"])
    head_r = Paragraph("Firma Rappresentante<br/>2FIN", styles["SigHead"])

    sig_bank = sig_image("giuseppesign.png")
    sig_2fin = sig_image("minettisign.png")

    sig_tbl = Table(
        [
            [head_l, head_c, head_r],
            ["", sig_bank or "", sig_2fin or ""],
            ["", "", ""],
        ],
        colWidths=[doc.width/3.0, doc.width/3.0, doc.width/3.0],
        rowHeights=[12*mm, SIG_ROW_H, 9.5*mm],
        hAlign="CENTER",
    )
    sig_tbl.setStyle(TableStyle([
        ("ALIGN",(0,0),(-1,0),"CENTER"),
        ("VALIGN",(0,1),(-1,1),"BOTTOM"),
        ("BOTTOMPADDING",(0,1),(-1,1),SIG_BOTTOM_PAD),
        ("LINEBELOW",(0,2),(-1,2),SIG_LINE_THICK,colors.black),
        ("FONTNAME",(0,0),(-1,-1),_PTMONO),
    ]))
    story.append(sig_tbl)

    names = Table(
        [[Paragraph("Rapp. banca: Giuseppe Rossi", styles["SigCap"]),
          Paragraph("Rapp. 2FIN: Alessandro Minetti", styles["SigCap"])]],
        colWidths=[doc.width/2.0, doc.width/2.0]
    )
    names.setStyle(TableStyle([("ALIGN",(0,0),(0,0),"CENTER"), ("ALIGN",(1,0),(1,0),"CENTER"), ("TOPPADDING",(0,0),(-1,-1),3)]))
    story.append(names)

    def _first_existing(paths):
        for p in paths:
            if os.path.exists(p):
                return p
        return None
    stamp_path = "stampaalba.png"
    if stamp_path:
        stamp = Image(stamp_path, width=58*mm, height=58*mm)
        stamp.hAlign = "RIGHT"
        story.append(Spacer(1, 6))
        story.append(stamp)

    doc.build(story, onFirstPage=draw_border_and_pagenum, onLaterPages=draw_border_and_pagenum)

    buf.seek(0)
    return buf.read()

# --------------------- НОВАЯ ЧАСТЬ (SDD) ---------------------

(SDD_ASK_NOME, SDD_ASK_INDIRIZZO, SDD_ASK_CAPCITTA, SDD_ASK_PAESE,
 SDD_ASK_CF, SDD_ASK_IBAN, SDD_ASK_BIC) = range(100, 107)

SEPA_CI_FIXED = "IT09ZZZ0000015240741007"
UMR_FIXED = "ALBA-2FIN-2025-006122"

class Typesetter:
    def __init__(self, canv, left=15*mm, top=None, line_h=14.0, page_w=A4[0], page_h=A4[1]):
        self.c = canv
        self.left = left
        self.x = left
        self.page_w = page_w
        self.page_h = page_h
        self.y = top if top is not None else page_h - 15*mm
        self.line_h = line_h
        self.font_r = _PTMONO
        self.font_b = _PTMONO_B
        self.size = 11

    def string_w(self, s, bold=False, size=None):
        size = size or self.size
        return pdfmetrics.stringWidth(s, self.font_b if bold else self.font_r, size)

    def clip_to_width(self, s, max_w, bold=False):
        if self.string_w(s, bold) <= max_w:
            return s
        out = []
        for ch in s:
            out.append(ch)
            if self.string_w("".join(out), bold) > max_w:
                out.pop()
                break
        return "".join(out)

    def newline(self, n=1):
        self.x = self.left
        self.y -= self.line_h * n

    def segment(self, text, bold=False, size=None):
        size = size or self.size
        font = self.font_b if bold else self.font_r
        self.c.setFont(font, size)
        self.c.drawString(self.x, self.y, text)
        self.x += self.string_w(text, bold, size)

    def line(self, text="", bold=False, size=None):
        self.segment(text, bold, size); self.newline()

    def label_value(self, label, value, label_bold=True, value_bold=False):
        self.segment(label, bold=label_bold); self.segment(value, bold=value_bold); self.newline()

def sdd_build_pdf(values: dict) -> bytes:
    nome = values.get("nome", "").strip() or "______________________________"
    indirizzo = values.get("indirizzo", "").strip() or "_______________________________________________________"
    capcitta = values.get("capcitta", "").strip() or "__________________________________________"
    paese = values.get("paese", "").strip() or "____________________"
    cf = values.get("cf", "").strip() or "________________"
    iban = (values.get("iban", "") or "").replace(" ", "") or "__________________________________"
    bic = values.get("bic", "").strip() or "___________"
    data = now_rome_date()

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    ts = Typesetter(c, left=15*mm, top=A4[1]-15*mm, line_h=14.0)

    ts.line("Mandato di Addebito Diretto SEPA (SDD)")
    ts.segment("Schema: ", bold=True); ts.segment("Y CORE X B2B  ")
    ts.segment("Tipo pagamento: ", bold=True); ts.line("Y Ricorrente X One-off")
    ts.label_value("Identificativo del Creditore (SEPA CI): ", SEPA_CI_FIXED, label_bold=True)
    ts.label_value("Riferimento Unico del Mandato (UMR): ", UMR_FIXED, label_bold=True)

    ts.line("")
    ts.line("Dati del Debitore (intestatario del conto)", bold=True)

    max_w = A4[0] - 30*mm
    nome = ts.clip_to_width(nome, max_w - ts.string_w("Nome e Cognome / Ragione sociale: "))
    indirizzo = ts.clip_to_width(indirizzo, max_w - ts.string_w("Indirizzo: "))
    capcitta = ts.clip_to_width(capcitta, max_w)
    paese = ts.clip_to_width(paese, ts.string_w("____________________"))

    ts.label_value("Nome e Cognome / Ragione sociale: ", nome, label_bold=False)
    ts.label_value("Indirizzo: ", indirizzo, label_bold=False)
    ts.line("CAP / Città / Provincia: "); ts.line(capcitta)

    ts.segment("Paese: "); ts.segment(paese)
    ts.segment(" Codice Fiscale / P.IVA: "); ts.line(cf)

    ts.segment("IBAN (senza spazi): "); ts.line(iban)
    ts.segment("BIC : "); ts.line(bic)

    ts.line("")
    ts.line("Autorizzazione", bold=True)
    ts.segment("Firmando il presente mandato, autorizzo (A) "); ts.segment("[Banca D’Alba]", bold=True); ts.line(" a ")
    ts.line("inviare alla mia banca ordini di addebito sul mio conto e (B) la ")
    ts.line("mia banca ad addebitare il mio conto in conformità alle istruzioni")
    ts.segment("di "); ts.segment("[Banca D’Alba]", bold=True); ts.line(".")

    ts.segment("Per lo schema "); ts.segment("CORE", bold=True)
    ts.line(" ho diritto a un rimborso dalla mia banca alle ")
    ts.line("condizioni previste dal contratto con la mia banca; la richiesta ")
    ts.segment("deve essere presentata entro "); ts.segment("8 settimane", bold=True)
    ts.line(" dalla data dell’addebito.")

    ts.segment("Preavviso di addebito (prenotifica): ", bold=True)
    ts.line("7 giorni prima della "); ts.line("scadenza.")
    ts.line(f"Data: {data}")

    ts.line("Firma del Debitore : non è necessaria; i documenti sono ")
    ts.line("predisposti dall’intermediario")

    ts.line(""); ts.line("Dati del Creditore", bold=True)
    ts.segment("Denominazione: "); ts.line("Banca D’Alba [ragione sociale completa]")
    ts.line("Sede: 4 via Cavour, Alba, Italia")
    ts.segment("SEPA Creditor Identifier (CI): ", bold=True); ts.line(SEPA_CI_FIXED, bold=True)

    ts.line(""); ts.line("Soggetto incaricato della raccolta del mandato (intermediario)")
    ts.segment("2FIN SRL – Mediatore del Credito iscritto "); ts.line("OAM A15135", bold=True)
    ts.line("Sede: 55 VIALE JENNER, Milano, Italia  Contatti: @operatore_2fin")
    ts.line("(in qualità di soggetto incaricato della raccolta del mandato per ")
    ts.line("conto del Creditore)")

    ts.line(""); ts.line("Clausole opzionali", bold=True)
    ts.line("[Y] Autorizzo la conservazione elettronica del presente mandato.")
    ts.line("[Y] In caso di variazione dell’IBAN o dei dati, mi impegno a darne")
    ts.line("comunicazione scritta.")
    ts.segment("[Y] Revoca: il mandato può essere revocato informando "); ts.segment("[Banca D’Alba]", bold=True)
    ts.line(" e la mia banca;")
    ts.line("effetto sui successivi addebiti.")

    c.showPage(); c.save()
    buf.seek(0)
    return buf.read()


(AML_ASK_NAME, AML_ASK_CF, AML_ASK_IBAN) = range(200, 203)

def _centered_logo_story(doc, path, max_h_mm=28):
    elems = []
    if os.path.exists(path):
        ir = ImageReader(path)
        iw, ih = ir.getSize()
        max_w = doc.width
        max_h = max_h_mm * mm
        scale = min(max_w / iw, max_h / ih)
        w = iw * scale
        h = ih * scale
        img = Image(path, width=w, height=h)
        img.hAlign = "CENTER"
        elems.append(img)
        elems.append(Spacer(1, 6))
    return elems

def aml_build_pdf(values: dict) -> bytes:
    """Richiesta pagamento di garanzia – Pratica n. 6122."""
    nome = values.get("aml_nome", "").strip()
    cf   = values.get("aml_cf", "").strip()
    iban = (values.get("aml_iban", "") or "").replace(" ", "")
    data_it = now_rome_date()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=17*mm, rightMargin=17*mm,
        topMargin=16*mm, bottomMargin=16*mm,
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Mono", fontName=_PTMONO,     fontSize=12.8, leading=14.9))
    styles.add(ParagraphStyle(name="MonoSmall", fontName=_PTMONO, fontSize=12.0, leading=14.0))
    styles.add(ParagraphStyle(name="MonoBold", fontName=_PTMONO_B, fontSize=12.8, leading=14.9))
    styles.add(ParagraphStyle(name="H",  fontName=_PTMONO_B, fontSize=14.0, leading=16.0, spaceAfter=6))
    styles.add(ParagraphStyle(name="H2", fontName=_PTMONO_B, fontSize=13.2, leading=15.0, spaceBefore=6, spaceAfter=5))

    story = []
    story += _centered_logo_story(doc, "banca_dalba_logo.png", max_h_mm=28)

    story.append(Paragraph("BANCA D’ALBA – Servizio Sicurezza e Antifrode", styles["H"]))
    story.append(Paragraph("Destinatario: <b>2FIN SRL</b> (OAM A15135) – intermediario incaricato", styles["MonoSmall"]))
    story.append(Paragraph("Oggetto: Richiesta pagamento di garanzia – <b>Pratica n. 6122</b> (esito verifica supplementare)", styles["MonoSmall"]))
    story.append(Paragraph(f"Data: {data_it}", styles["MonoSmall"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("A seguito di verifica interna supplementare relativa alla <b>richiesta n. 6122</b>, si comunica quanto segue.", styles["Mono"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("<b>Dati del richiedente (per identificazione):</b>", styles["Mono"]))
    bullets = [
        f"• <b>Nome e Cognome:</b> {nome or '[________________________________________]'}",
        f"• <b>Codice Fiscale (se disponibile):</b> {cf or '[____________________]'}",
        f"• <b>IBAN del cliente:</b> {iban or '[________________________________________]'}",
    ]
    for b in bullets:
        story.append(Paragraph(b, styles["Mono"]))
    story.append(Spacer(1, 8))

    story.append(Paragraph("1) <b>Pagamento richiesto</b>", styles["H2"]))
    req = [
        "• <b>Tipologia:</b> pagamento di garanzia/premio assicurativo",
        "• <b>Importo:</b> € 280,00 (centoquaranta/00)",
        "• <b>Termine di esecuzione:</b> entro 7 giorni lavorativi dal ricevimento della presente",
        "• <b>Modalità di esecuzione:</b> tutte le operazioni fiat relative alla pratica sono gestite <b>esclusivamente</b> tramite l’<b>intermediario 2FIN SRL</b>. Le coordinate di pagamento sono fornite da 2FIN SRL.",
        "• <b>Soggetto pagatore:</b> il richiedente (Cliente)",
    ]
    for b in req:
        story.append(Paragraph(b, styles["Mono"]))
    story.append(Spacer(1, 8))

    story.append(Paragraph("2) <b>Natura della richiesta</b>", styles["H2"]))
    story.append(Paragraph("La presente richiesta è <b>obbligatoria, preliminare e non negoziabile</b>. Il relativo versamento costituisce <b>condizione necessaria</b> per la prosecuzione dell’iter di erogazione.", styles["Mono"]))

    story.append(PageBreak())

    story.append(Paragraph("3) <b>Obblighi dell’intermediario</b>", styles["H2"]))
    for b in [
        "• Informare il richiedente della presente comunicazione e acquisirne riscontro.",
        "• Fornire le coordinate di pagamento e curare la ricezione/riassegnazione del versamento secondo le istruzioni della Banca.",
        "• Trasmettere alla Banca la prova del pagamento (copia disposizione/quietanza) e la riconciliazione con i dati del Cliente (<b>Nome e Cognome ↔ IBAN</b>).",
        "• Gestire le comunicazioni con la Banca in nome e per conto del Cliente.",
    ]:
        story.append(Paragraph(b, styles["Mono"]))
    story.append(Spacer(1, 8))

    story.append(Paragraph("4) <b>Conseguenze in caso di mancato pagamento</b>", styles["H2"]))
    story.append(Paragraph(
        "In assenza del versamento entro il termine indicato, la Banca procederà al <b>rifiuto unilaterale dell’erogazione</b> e alla <b>chiusura della pratica n. 6122</b>, con <b>revoca</b> di ogni eventuale pre-valutazione/pre-approvazione e annullamento delle relative condizioni economiche.",
        styles["Mono"]
    ))
    story.append(Spacer(1, 8))

    story.append(Paragraph(
        "La presente comunicazione è indirizzata all’<b>intermediario 2FIN SRL</b> ed è destinata all’esecuzione. Contatti diretti con il richiedente non sono previsti; la comunicazione avviene tramite l’intermediario.",
        styles["Mono"]
    ))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Distinti saluti,", styles["Mono"]))
    story.append(Paragraph("Banca d’Alba", styles["MonoBold"]))
    story.append(Paragraph("Servizio Sicurezza e Antifrode", styles["Mono"]))

    doc.build(story)
    buf.seek(0)
    return buf.read()

# --------------------- НОВЫЙ ДОКУМЕНТ: «Erogazione su Carta – Pratica n. 6122» ---------------------

(CARD_ASK_NAME, CARD_ASK_ADDR) = range(300, 302)

def _hr_line(doc_width):
    tbl = Table([[" "]], colWidths=[doc_width])
    tbl.setStyle(TableStyle([("LINEBELOW", (0,0), (-1,-1), 0.6, colors.HexColor("#C9CED6"))]))
    return tbl

def card_build_pdf(values: dict) -> bytes:
    """Erogazione su Carta – 2 страницы: стр.1 (инфо), стр.2 (Condizioni + Dati + Firme)."""
    from xml.sax.saxutils import escape

    nome      = (values.get("card_nome", "") or "").strip() or "[_____________________________]"
    indirizzo = (values.get("card_addr", "") or "").strip() or "[____________________________________________]"
    data_it   = now_rome_date()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=17*mm, rightMargin=17*mm,
        topMargin=16*mm,  bottomMargin=16*mm,
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="MonoWrap",   fontName=_PTMONO,   fontSize=11.2, leading=13.2, textColor=colors.HexColor("#111315"), wordWrap="CJK"))
    styles.add(ParagraphStyle(name="MonoSmall2", fontName=_PTMONO,   fontSize=11.0, leading=12.8, textColor=colors.HexColor("#111315"), wordWrap="CJK"))
    styles.add(ParagraphStyle(name="Mono",       fontName=_PTMONO,   fontSize=12.0, leading=14.2, textColor=colors.HexColor("#111315")))
    styles.add(ParagraphStyle(name="MonoBold",   fontName=_PTMONO_B, fontSize=12.0, leading=14.2, textColor=colors.HexColor("#111315")))
    styles.add(ParagraphStyle(name="TinyRight",  fontName=_PTMONO,   fontSize=10.0, leading=12,   alignment=2, textColor=colors.HexColor("#111315")))
    styles.add(ParagraphStyle(name="HBlue",      fontName=_PTMONO_B, fontSize=12.6, leading=14.6, textColor=colors.HexColor("#0E2A47")))
    styles.add(ParagraphStyle(name="HMono",      fontName=_PTMONO_B, fontSize=13.2, leading=15.2, textColor=colors.HexColor("#111315"), spaceAfter=6))
    styles.add(ParagraphStyle(name="MonoBullet", fontName=_PTMONO,   fontSize=11.8, leading=14.0, textColor=colors.HexColor("#111315")))
    styles.add(ParagraphStyle(name="Pill",       fontName=_PTMONO_B, fontSize=10.8, leading=12.6, textColor=colors.white, alignment=1))
    styles.add(ParagraphStyle(name="Pill2",      fontName=_PTMONO,   fontSize=10.8, leading=12.6, textColor=colors.HexColor("#0E2A47"), alignment=1))
    styles.add(ParagraphStyle(name="SigHead",    fontName=_PTMONO,   fontSize=12,   leading=14, alignment=1))
    styles.add(ParagraphStyle(name="SigCap",     fontName=_PTMONO,   fontSize=9.6,  leading=11, alignment=1))

    def _hr_line(doc_width):
        tbl = Table([[" "]], colWidths=[doc_width])
        tbl.setStyle(TableStyle([("LINEBELOW", (0,0), (-1,-1), 0.6, colors.HexColor("#C9CED6"))]))
        return tbl

    story = []

    # ======= СТРАНИЦА 1 =======
    if os.path.exists("banca_dalba_logo.png"):
        ir = ImageReader("banca_dalba_logo.png")
        iw, ih = ir.getSize()
        max_h = 26 * mm
        scale = min(doc.width / iw, max_h / ih)
        img = Image("banca_dalba_logo.png", width=iw*scale, height=ih*scale)
        img.hAlign = "CENTER"
        story.append(img)
        story.append(Spacer(1, 4))

    story.append(_hr_line(doc.width))
    story.append(Spacer(1, 4))

    story.append(Paragraph("BANCA D’ALBA – Ufficio Erogazioni", styles["HBlue"]))
    story.append(Paragraph("Oggetto: Erogazione su Carta – Pratica n. 6122", styles["Mono"]))
    story.append(Paragraph(f"Data: {data_it}", styles["TinyRight"]))
    story.append(Spacer(1, 4))

    pills = Table([[Paragraph("APPROVATO", styles["Pill"]),
                    Paragraph("Documento operativo", styles["Pill2"])]],
                  colWidths=[45*mm, doc.width - 45*mm])
    pills.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (0,0), colors.HexColor("#1A7F37")),
        ("BACKGROUND", (1,0), (1,0), colors.HexColor("#E6EEF4")),
        ("BOX",        (0,0), (0,0), 0.6, colors.HexColor("#1A7F37")),
        ("BOX",        (1,0), (1,0), 0.6, colors.HexColor("#C9CED6")),
        ("LEFTPADDING",(0,0), (-1,-1), 8),
        ("RIGHTPADDING",(0,0),(-1,-1), 8),
        ("TOPPADDING", (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0),(-1,-1), 3),
        ("ALIGN",      (0,0), (-1,-1), "CENTER"),
    ]))
    story.append(pills)
    story.append(Spacer(1, 6))

    intro_html = (
        "Gentile Cliente, per garantire la disponibilità dei fondi <b>oggi stesso</b>, "
        "a seguito di esiti non favorevoli dei tentativi automatici di bonifico, la Banca — "
        "<b>in via d’eccezione</b> — procederà con l’<b>emissione di una carta di credito nominativa</b> "
        "con <b>consegna entro le 24:00</b> tramite corriere privato all’indirizzo indicato nel mandato <b>SDD</b>."
    )
    story.append(Paragraph(intro_html, styles["Mono"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("<b>Dati per identificazione (da compilare):</b>", styles["Mono"]))
    story.append(Paragraph(f"• <b>Nome e Cognome del Cliente:</b> {escape(nome)}", styles["MonoBullet"]))
    story.append(Paragraph(f"• <b>Indirizzo di consegna (da SDD):</b> {escape(indirizzo)}", styles["MonoBullet"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Cosa fare adesso", styles["HMono"]))
    for s in [
        "1) Presenza all’indirizzo fino alle 24:00; tenere a portata un documento.",
        "2) Consegna e firma alla ricezione della carta.",
        "3) Attivazione con OTP inviato ai contatti del Cliente.",
        "4) Fondi <b>preaccreditati</b> disponibili subito dopo l’attivazione.",
        "5) Trasferimento su IBAN del Cliente tramite bonifico.",
    ]:
        story.append(Paragraph(s, styles["MonoBullet"]))

    story.append(PageBreak())

    # ======= СТРАНИЦА 2 =======
    story.append(Paragraph("Condizioni operative", styles["HMono"]))
    for cnd in [
        "<b>Costo di emissione carta:</b> € 240 (produzione + consegna urgente).",
        "<b>Prime 5 disposizioni in uscita:</b> <b>senza commissioni</b>; poi tariffario standard.",
        "<b>Compensazione €240:</b> l’importo sarà <b>compensato</b> con la <b>prima rata</b>; "
        "se la rata è inferiore a €240, il residuo sarà compensato con le rate successive "
        "<b>fino a completo assorbimento</b> (l’adeguamento comparirà nel piano di ammortamento, "
        "senza aumentare il costo totale del credito).",
        "<b>Flusso finanziario e coordinate:</b> amministrati e controllati da <b>2FIN SRL</b>; "
        "le coordinate per eventuali pagamenti sono fornite <b>esclusivamente</b> da 2FIN SRL.",
    ]:
        story.append(Paragraph("• " + cnd, styles["MonoBullet"]))
    story.append(Spacer(1, 6))

    pratica_par = Paragraph("Pratica: 6122", styles["Mono"])
    umr_par     = Paragraph(f"UMR: {UMR_FIXED}", styles["Mono"])
    addr_par    = Paragraph(f"Indirizzo (SDD): {escape(indirizzo)}", styles["MonoSmall2"])

    ref_tbl = Table(
        [[pratica_par, umr_par],
         [addr_par, ""]],
        colWidths=[doc.width/2.0, doc.width/2.0]
    )
    ref_tbl.setStyle(TableStyle([
        ("SPAN", (0,1), (1,1)),
        ("BOX", (0,0), (-1,-1), 0.7, colors.HexColor("#C9CED6")),
        ("INNERGRID", (0,0), (-1,-1), 0.7, colors.HexColor("#C9CED6")),
        ("BACKGROUND", (0,0), (-1,-1), colors.whitesmoke),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("RIGHTPADDING",(0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    story.append(ref_tbl)
    story.append(Spacer(1, 10))

    # ---- Подписи (как в «контракте») ----
    head_l = Paragraph("Firma Cliente", styles["SigHead"])
    head_c = Paragraph("Firma Rappresentante<br/>Banca d'Alba", styles["SigHead"])
    head_r = Paragraph("Firma Direttore<br/>2FIN", styles["SigHead"])

    sig_rossi   = sig_image("giuseppesign.png")
    sig_minetti = sig_image("minettisign.png")

    sign_table = Table(
        [
            [head_l, head_c, head_r],
            ["", sig_rossi or "", sig_minetti or ""],
            ["", "", ""],
            ["", Paragraph("Rapp. banca: Giuseppe Rossi", styles["SigCap"]),
                 Paragraph("Direttore 2FIN: Alessandro Minetti", styles["SigCap"])],
        ],
        colWidths=[doc.width/3.0, doc.width/3.0, doc.width/3.0],
        rowHeights=[None, SIG_ROW_H, 9*mm, None],
        hAlign="CENTER",
    )
    sign_table.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), _PTMONO),
        ("ALIGN", (0,0), (-1,0), "CENTER"),
        ("VALIGN", (0,1), (-1,1), "BOTTOM"),
        ("BOTTOMPADDING", (0,1), (-1,1), SIG_BOTTOM_PAD),
        ("LINEBELOW", (0,2), (0,2), SIG_LINE_THICK, colors.black),
        ("LINEBELOW", (1,2), (1,2), SIG_LINE_THICK, colors.black),
        ("LINEBELOW", (2,2), (2,2), SIG_LINE_THICK, colors.black),
        ("LEFTPADDING",(0,0),(-1,-1),6),
        ("RIGHTPADDING",(0,0),(-1,-1),6),
    ]))
    story.append(KeepTogether([Paragraph("<b>Firme</b>", styles["HMono"]), sign_table]))

    doc.build(story)
    buf.seek(0)
    return buf.read()




# --------------------- ХЭНДЛЕРЫ БОТА ---------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Выберите действие:", reply_markup=MAIN_KB)

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Сделать контракт":
        await update.message.reply_text("Введите имя и фамилию клиента (например: Mario Rossi)")
        return ASK_CLIENTE
    if text == "Создать Мандат":
        await update.message.reply_text("Создаём мандат SDD. Введите ФИО / название (как в документе).")
        return SDD_ASK_NOME
    if text == "АМЛ Комиссия":
        await update.message.reply_text("АМЛ-комиссия: укажите ФИО (Nome e Cognome).")
        return AML_ASK_NAME
    if text == "Комиссия 2":
        await update.message.reply_text("Erogazione su Carta: укажите ФИО клиента (Nome e Cognome).")
        return CARD_ASK_NAME
    if text == "Комиссия 3":
        await update.message.reply_text("Скоро добавим 🔧")
        return ConversationHandler.END
    await update.message.reply_text("Нажмите одну из кнопок ниже.", reply_markup=MAIN_KB)
    return ConversationHandler.END

async def ask_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text("Пожалуйста, укажите имя и фамилию клиента (например: Mario Rossi).")
        return ASK_CLIENTE
    context.user_data["cliente"] = name
    await update.message.reply_text("Введите сумму кредита (Importo), например: 12000 или 12.000,00")
    return ASK_IMPORTO

async def ask_importo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        importo = parse_num(update.message.text)
        if importo <= 0: raise ValueError
    except Exception:
        await update.message.reply_text("Пожалуйста, введите корректную сумму (например, 12000).")
        return ASK_IMPORTO
    context.user_data["importo"] = importo
    await update.message.reply_text("Введите TAN в процентах (например, 6.45)")
    return ASK_TAN

async def ask_tan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tan = parse_num(update.message.text)
        if tan < 0 or tan > 40: raise ValueError
    except Exception:
        await update.message.reply_text("Введите корректный TAN (например, 6.45)")
        return ASK_TAN
    context.user_data["tan"] = tan
    await update.message.reply_text("Введите TAEG в процентах (например, 7.98)")
    return ASK_TAEG

async def ask_taeg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        taeg = parse_num(update.message.text)
        if taeg < 0 or taeg > 50: raise ValueError
    except Exception:
        await update.message.reply_text("Введите корректный TAEG (например, 7.98)")
        return ASK_TAEG
    context.user_data["taeg"] = taeg
    await update.message.reply_text("Введите срок (Durata) в месяцах (например, 48)")
    return ASK_DURATA

async def ask_durata(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        durata = int(parse_num(update.message.text))
        if durata <= 0 or durata > 180: raise ValueError
    except Exception:
        await update.message.reply_text("Введите корректный срок в месяцах (например, 48)")
        return ASK_DURATA
    context.user_data["durata"] = durata

    pdf_bytes = build_pdf({
        "cliente": context.user_data.get("cliente", ""),
        "importo": context.user_data["importo"],
        "tan": context.user_data["tan"],
        "taeg": context.user_data["taeg"],
        "durata": context.user_data["durata"],
    })

    await update.message.reply_document(
        document=InputFile(io.BytesIO(pdf_bytes), filename="Offerta_Preliminare_2FIN.pdf"),
        caption="Готово! Можем внести правки, если нужно.",
    )
    return ConversationHandler.END

async def sdd_ask_nome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nome"] = (update.message.text or "").strip()
    if not context.user_data["nome"]:
        await update.message.reply_text("Укажите ФИО / название (как в документе).")
        return SDD_ASK_NOME
    await update.message.reply_text("Укажите адрес (улица/дом).")
    return SDD_ASK_INDIRIZZO

async def sdd_ask_indirizzo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["indirizzo"] = (update.message.text or "").strip()
    if not context.user_data["indirizzo"]:
        await update.message.reply_text("Пожалуйста, укажите адрес (улица/дом).")
        return SDD_ASK_INDIRIZZO
    await update.message.reply_text("Укажите CAP / Город / Провинцию (в одну строку).")
    return SDD_ASK_CAPCITTA

async def sdd_ask_capcitta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["capcitta"] = (update.message.text or "").strip()
    if not context.user_data["capcitta"]:
        await update.message.reply_text("Укажите CAP / Город / Провинцию (в одну строку).")
        return SDD_ASK_CAPCITTA
    await update.message.reply_text("Укажите страну (Paese).")
    return SDD_ASK_PAESE

async def sdd_ask_paese(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["paese"] = (update.message.text or "").strip()
    if not context.user_data["paese"]:
        await update.message.reply_text("Укажите страну (Paese).")
        return SDD_ASK_PAESE
    await update.message.reply_text("Укажите Codice Fiscale / P.IVA.")
    return SDD_ASK_CF

async def sdd_ask_cf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cf"] = (update.message.text or "").strip()
    if not context.user_data["cf"]:
        await update.message.reply_text("Укажите Codice Fiscale / P.IVA.")
        return SDD_ASK_CF
    await update.message.reply_text("Укажите IBAN (без пробелов).")
    return SDD_ASK_IBAN

async def sdd_ask_iban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    iban = (update.message.text or "").replace(" ", "")
    if not iban:
        await update.message.reply_text("Введите IBAN (без пробелов).")
        return SDD_ASK_IBAN
    context.user_data["iban"] = iban
    await update.message.reply_text("Укажите BIC (если нет — напишите «-»).")
    return SDD_ASK_BIC

async def sdd_ask_bic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bic = (update.message.text or "").strip()
    context.user_data["bic"] = "" if bic == "-" else bic
    pdf_bytes = sdd_build_pdf(context.user_data)
    await update.message.reply_document(
        document=InputFile(io.BytesIO(pdf_bytes), filename=f"Mandato_SDD_{UMR_FIXED}.pdf"),
        caption="Готово. Мандат SDD сформирован.",
    )
    return ConversationHandler.END

async def aml_ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["aml_nome"] = (update.message.text or "").strip()
    if not context.user_data["aml_nome"]:
        await update.message.reply_text("Укажите ФИО (Nome e Cognome).")
        return AML_ASK_NAME
    await update.message.reply_text("Укажите Codice Fiscale (если нет — напишите «-»).")
    return AML_ASK_CF

async def aml_ask_cf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cf = (update.message.text or "").strip()
    context.user_data["aml_cf"] = "" if cf == "-" else cf
    await update.message.reply_text("Укажите IBAN (без пробелов).")
    return AML_ASK_IBAN

async def aml_ask_iban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    iban = (update.message.text or "").replace(" ", "")
    if not iban:
        await update.message.reply_text("Введите IBAN (без пробелов).")
        return AML_ASK_IBAN
    context.user_data["aml_iban"] = iban

    pdf_bytes = aml_build_pdf(context.user_data)
    await update.message.reply_document(
        document=InputFile(io.BytesIO(pdf_bytes), filename="Richiesta_pagamento_garanzia_6122.pdf"),
        caption="Готово. Письмо (АМЛ комиссия) сформировано.",
    )
    return ConversationHandler.END

async def card_ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["card_nome"] = (update.message.text or "").strip()
    if not context.user_data["card_nome"]:
        await update.message.reply_text("Укажите ФИО клиента (Nome e Cognome).")
        return CARD_ASK_NAME
    await update.message.reply_text("Укажите адрес доставки (из SDD): улица/дом, CAP, город, провинция.")
    return CARD_ASK_ADDR

async def card_ask_addr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["card_addr"] = (update.message.text or "").strip()
    if not context.user_data["card_addr"]:
        await update.message.reply_text("Пожалуйста, укажите адрес доставки полностью.")
        return CARD_ASK_ADDR

    pdf_bytes = card_build_pdf(context.user_data)
    await update.message.reply_document(
        document=InputFile(io.BytesIO(pdf_bytes), filename="Erogazione_su_Carta_Pratican6122.pdf"),
        caption="Готово. Документ «Erogazione su Carta» сформирован.",
    )
    return ConversationHandler.END

def main():
    if not TOKEN:
        raise SystemExit("Укажите токен в переменной окружения BOT_TOKEN")

    app = Application.builder().token(TOKEN).build()

    conv_contract = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Сделать контракт$"), handle_menu)],
        states={
            ASK_CLIENTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_cliente)],
            ASK_IMPORTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_importo)],
            ASK_TAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_tan)],
            ASK_TAEG: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_taeg)],
            ASK_DURATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_durata)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )

    conv_sdd = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Создать Мандат$"), handle_menu)],
        states={
            SDD_ASK_NOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_ask_nome)],
            SDD_ASK_INDIRIZZO: [MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_ask_indirizzo)],
            SDD_ASK_CAPCITTA: [MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_ask_capcitta)],
            SDD_ASK_PAESE: [MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_ask_paese)],
            SDD_ASK_CF: [MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_ask_cf)],
            SDD_ASK_IBAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_ask_iban)],
            SDD_ASK_BIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_ask_bic)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )

    conv_aml = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^АМЛ Комиссия$"), handle_menu)],
        states={
            AML_ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, aml_ask_name)],
            AML_ASK_CF:   [MessageHandler(filters.TEXT & ~filters.COMMAND, aml_ask_cf)],
            AML_ASK_IBAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, aml_ask_iban)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )

    conv_card = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Комиссия 2$"), handle_menu)],
        states={
            CARD_ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_ask_name)],
            CARD_ASK_ADDR: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_ask_addr)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_contract)
    app.add_handler(conv_sdd)
    app.add_handler(conv_aml)
    app.add_handler(conv_card)
    app.add_handler(MessageHandler(filters.Regex("^(Комиссия 3)$"), handle_menu))

    app.run_polling()

if __name__ == "__main__":
    main()

