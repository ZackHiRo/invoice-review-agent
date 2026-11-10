"""Render an InvoiceSpec to PDF with reportlab.

Four layouts x three label languages. Blocks are drawn in a shuffled order on some
layouts, so the PDF's text stream (what pypdf reads) is not in visual reading order:
this is common with real invoicing software and makes extraction harder.
"""

from __future__ import annotations

import random
from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from invoice_agent.money import format_iban
from invoice_agent.synth.build import InvoiceSpec
from invoice_agent.synth.world import BUYER

W, H = A4

LABELS = {
    "en": dict(invoice="INVOICE", credit_note="CREDIT NOTE", number="Invoice No.",
               cn_number="Credit Note No.", date="Invoice Date", due="Due Date",
               order_date="Order Date", delivery="Delivery Date", po="PO Number",
               bill_to="Bill To", description="Description", qty="Qty", unit="Unit Price",
               amount="Amount", vat_col="VAT %", subtotal="Subtotal", vat="VAT",
               vat_total="Total VAT",
               on="on", total="Total", paid="Amount Paid", balance="Balance Due",
               bank="Bank details", vat_id="VAT No.", terms="Payment terms",
               net_days="Net {d} days", page="Page {p} of {n}", carried="Carried forward",
               brought="Brought forward", ref="Refers to invoice",
               thanks="Thank you for your business.", amounts_in="All amounts in {c}"),
    "fr": dict(invoice="FACTURE", credit_note="AVOIR", number="Facture n°",
               cn_number="Avoir n°", date="Date de facture", due="Date d'échéance",
               order_date="Date de commande", delivery="Date de livraison",
               po="Bon de commande", bill_to="Facturé à", description="Désignation",
               qty="Qté", unit="Prix unitaire HT", amount="Montant HT", vat_col="TVA %",
               subtotal="Total HT", vat="TVA", vat_total="Total TVA", on="sur", total="Total TTC",
               paid="Acompte versé", balance="Reste à payer", bank="Coordonnées bancaires",
               vat_id="N° TVA intracom.", terms="Conditions de paiement",
               net_days="{d} jours net", page="Page {p}/{n}", carried="À reporter",
               brought="Report", ref="Facture d'origine",
               thanks="Merci pour votre confiance.", amounts_in="Montants en {c}"),
    "de": dict(invoice="RECHNUNG", credit_note="GUTSCHRIFT", number="Rechnungsnr.",
               cn_number="Gutschriftnr.", date="Rechnungsdatum", due="Fällig am",
               order_date="Bestelldatum", delivery="Lieferdatum", po="Bestellnummer",
               bill_to="Rechnungsempfänger", description="Bezeichnung", qty="Menge",
               unit="Einzelpreis", amount="Betrag", vat_col="USt %", subtotal="Nettobetrag",
               vat="USt.", vat_total="USt. gesamt", on="auf", total="Gesamtbetrag",
               paid="Bereits bezahlt",
               balance="Offener Betrag", bank="Bankverbindung", vat_id="USt-IdNr.",
               terms="Zahlungsbedingungen", net_days="{d} Tage netto",
               page="Seite {p} von {n}", carried="Übertrag", brought="Übertrag",
               ref="Bezug: Rechnung", thanks="Vielen Dank für Ihren Auftrag.",
               amounts_in="Beträge in {c}"),
}

MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December"]
MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre"]
SYMBOL = {"EUR": "€", "GBP": "£", "USD": "$"}


class Style:
    """Per-document formatting choices (number format, date format, currency display)."""

    def __init__(self, spec: InvoiceSpec, rng: random.Random):
        self.locale = spec.locale
        self.lang = spec.locale[:2] if spec.locale[:2] in ("fr", "de") else "en"
        self.L = LABELS[self.lang]
        self.currency = spec.truth.currency
        self.money_mode = rng.choice(["symbol", "symbol", "code_header"])
        self.date_mode = rng.randint(0, 1)
        if self.locale == "nl_NL":
            self.sep = rng.choice(["de", "en"])
        elif self.lang in ("fr", "de"):
            self.sep = self.lang
        else:
            self.sep = "en"

    def num(self, x: float, decimals: int = 2) -> str:
        s = f"{abs(x):,.{decimals}f}"
        if self.sep == "de":
            s = s.replace(",", "X").replace(".", ",").replace("X", ".")
        elif self.sep == "fr":
            s = s.replace(",", " ").replace(".", ",")
        return ("-" if x < 0 else "") + s

    def qty(self, q: float) -> str:
        if float(q).is_integer():
            return str(int(q))
        s = f"{q:g}"
        return s.replace(".", ",") if self.sep in ("fr", "de") else s

    def money(self, x: float) -> str:
        n = self.num(x)
        if self.money_mode == "code_header":
            return n
        sym = SYMBOL[self.currency]
        if self.lang in ("fr", "de") or (self.locale == "nl_NL" and self.sep == "de"):
            return f"{n} {sym}"
        return f"-{sym}{n[1:]}" if n.startswith("-") else f"{sym}{n}"

    def date(self, d: date | str | None) -> str:
        if d is None:
            return ""
        if isinstance(d, str):
            d = date.fromisoformat(d)
        if self.locale == "en_US":
            long = f"{MONTHS_EN[d.month - 1]} {d.day}, {d.year}"
            return d.strftime("%m/%d/%Y") if self.date_mode else long
        if self.locale == "en_GB":
            long = f"{d.day} {MONTHS_EN[d.month - 1]} {d.year}"
            return d.strftime("%d/%m/%Y") if self.date_mode else long
        if self.lang == "fr":
            long = f"{d.day} {MONTHS_FR[d.month - 1]} {d.year}"
            return d.strftime("%d/%m/%Y") if self.date_mode else long
        if self.lang == "de":
            return d.strftime("%d.%m.%Y")
        if self.locale == "nl_NL":
            return d.strftime("%d-%m-%Y")
        return d.strftime("%d/%m/%Y")


class Pen:
    def __init__(self, c: Canvas):
        self.c = c

    def text(self, x: float, y: float, s: str, size: float = 9, bold: bool = False,
             align: str = "left") -> None:
        self.c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        if align == "right":
            self.c.drawRightString(x, y, s)
        elif align == "center":
            self.c.drawCentredString(x, y, s)
        else:
            self.c.drawString(x, y, s)

    def rule(self, y: float, x0: float = 50, x1: float = W - 50) -> None:
        self.c.setLineWidth(0.5)
        self.c.line(x0, y, x1, y)


# ---------- blocks -------------------------------------------------------------------------

def _seller_lines(spec: InvoiceSpec, st: Style) -> list[str]:
    t = spec.truth
    lines = [t.seller_name, *spec.vendor_address]
    if t.seller_vat_id:
        label = "EIN" if spec.locale == "en_US" else st.L["vat_id"]
        lines.append(f"{label}: {t.seller_vat_id}")
    return lines


def _buyer_lines(st: Style) -> list[str]:
    return [st.L["bill_to"] + ":", BUYER["name"], *BUYER["address"],
            f"{st.L['vat_id']}: {BUYER['vat_id']}"]


def _meta_lines(spec: InvoiceSpec, st: Style) -> list[tuple[str, str]]:
    t, L = spec.truth, st.L
    number_label = L["cn_number"] if t.document_type == "credit_note" else L["number"]
    rows = [(number_label, t.invoice_number),
            (L["date"], st.date(t.invoice_date))]
    if spec.order_date:
        rows.append((L["order_date"], st.date(spec.order_date)))
    if spec.delivery_date:
        rows.append((L["delivery"], st.date(spec.delivery_date)))
    if t.due_date:
        rows.append((L["due"], st.date(t.due_date)))
    if t.po_number:
        rows.append((L["po"], t.po_number))
    if spec.original_invoice:
        rows.append((L["ref"], spec.original_invoice))
    return rows


def _totals_rows(spec: InvoiceSpec, st: Style) -> list[tuple[str, str, bool]]:
    t, L = spec.truth, st.L
    rows = [(L["subtotal"], st.money(t.subtotal), False)]
    for tl in t.tax_lines:
        rate = st.qty(tl.rate_percent)
        if len(t.tax_lines) > 1:
            label = f"{L['vat']} {rate}% {L['on']} {st.num(tl.base)}"
        else:
            label = f"{L['vat']} {rate}%"
        rows.append((label, st.money(tl.amount), False))
    if len(t.tax_lines) > 1:
        rows.append((L["vat_total"], st.money(t.tax_total), False))
    if not t.tax_lines:
        rows.append(("Sales Tax" if spec.locale == "en_US" else L["vat"], st.money(0), False))
    rows.append((L["total"], st.money(t.total), True))
    if spec.amount_paid:
        rows.append((L["paid"], st.money(spec.amount_paid), False))
        rows.append((L["balance"], st.money(t.total - spec.amount_paid), True))
    return rows


def _bank_lines(spec: InvoiceSpec, st: Style) -> list[str]:
    t, L = spec.truth, st.L
    lines = []
    if spec.payment_terms_days and not t.due_date:
        lines.append(f"{L['terms']}: {L['net_days'].format(d=spec.payment_terms_days)}")
    if t.iban:
        lines.append(f"{L['bank']}: IBAN {format_iban(t.iban)}" +
                     (f"  BIC {spec.vendor_bic}" if spec.vendor_bic else ""))
    elif spec.locale == "en_US":
        lines.append("Payment by ACH. Remittance details on request.")
    lines.append(L["thanks"])
    return lines


def _draw_items(pen: Pen, spec: InvoiceSpec, st: Style, items, rates, y: float) -> float:
    L = st.L
    mixed = len(spec.truth.tax_lines) > 1
    pen.text(50, y, L["description"], bold=True)
    pen.text(360, y, L["qty"], bold=True, align="right")
    pen.text(440, y, L["unit"], bold=True, align="right")
    if mixed:
        pen.text(485, y, L["vat_col"], bold=True, align="right")
    pen.text(545, y, L["amount"], bold=True, align="right")
    y -= 5
    pen.rule(y)
    y -= 13
    for item, rate in zip(items, rates):
        pen.text(50, y, item.description)
        pen.text(360, y, st.qty(item.quantity), align="right")
        pen.text(440, y, st.num(item.unit_price), align="right")
        if mixed:
            pen.text(485, y, st.qty(rate), align="right")
        pen.text(545, y, st.num(item.amount), align="right")
        y -= 14
    pen.rule(y + 6)
    return y


def _draw_totals(pen: Pen, spec: InvoiceSpec, st: Style, y: float, x_label: float = 330) -> float:
    for label, value, bold in _totals_rows(spec, st):
        pen.text(x_label, y, label, bold=bold)
        pen.text(545, y, value, bold=bold, align="right")
        y -= 14
    return y


def _title(spec: InvoiceSpec, st: Style) -> str:
    return st.L["credit_note"] if spec.truth.document_type == "credit_note" else st.L["invoice"]


# ---------- layouts ------------------------------------------------------------------------

def _classic(c: Canvas, spec: InvoiceSpec, st: Style, rng: random.Random) -> None:
    pen = Pen(c)
    t = spec.truth

    def header():
        pen.text(W - 50, H - 60, _title(spec, st), size=18, bold=True, align="right")
        y = H - 85
        for k, v in _meta_lines(spec, st):
            pen.text(400, y, f"{k}:")
            pen.text(W - 50, y, v, align="right")
            y -= 13
        if st.money_mode == "code_header":
            pen.text(W - 50, y - 4, st.L["amounts_in"].format(c=t.currency), size=8, align="right")

    def seller():
        y = H - 60
        for i, line in enumerate(_seller_lines(spec, st)):
            pen.text(50, y, line, size=12 if i == 0 else 9, bold=i == 0)
            y -= 14 if i == 0 else 12

    def buyer():
        y = H - 200
        for i, line in enumerate(_buyer_lines(st)):
            pen.text(50, y, line, bold=i == 0)
            y -= 12

    def body():
        y = _draw_items(pen, spec, st, t.line_items, spec.line_rates, H - 300)
        _draw_totals(pen, spec, st, y - 12)

    def footer():
        y = 90
        for line in _bank_lines(spec, st):
            pen.text(50, y, line, size=8)
            y -= 11

    blocks = [header, seller, buyer, body, footer]
    rng.shuffle(blocks)
    for b in blocks:
        b()


def _two_column(c: Canvas, spec: InvoiceSpec, st: Style, rng: random.Random) -> None:
    """Seller and buyer side by side, drawn row by row: text extraction merges them per line."""
    pen = Pen(c)
    t = spec.truth
    pen.text(50, H - 55, _title(spec, st), size=20, bold=True)
    left = ["", *_seller_lines(spec, st)]
    right = _buyer_lines(st)
    y = H - 85
    for i in range(max(len(left), len(right))):
        if i < len(left) and left[i]:
            pen.text(50, y, left[i], bold=i == 1)
        if i < len(right):
            pen.text(320, y, right[i], bold=i == 0)
        y -= 12
    y -= 10
    meta = _meta_lines(spec, st)
    for i in range(0, len(meta), 2):
        k, v = meta[i]
        pen.text(50, y, f"{k}: {v}")
        if i + 1 < len(meta):
            k2, v2 = meta[i + 1]
            pen.text(320, y, f"{k2}: {v2}")
        y -= 13
    if st.money_mode == "code_header":
        pen.text(50, y, st.L["amounts_in"].format(c=t.currency), size=8)
        y -= 13
    y = _draw_items(pen, spec, st, t.line_items, spec.line_rates, y - 15)
    _draw_totals(pen, spec, st, y - 12)
    y = 80
    for line in _bank_lines(spec, st):
        pen.text(50, y, line, size=8)
        y -= 11


def _minimal(c: Canvas, spec: InvoiceSpec, st: Style, rng: random.Random) -> None:
    """Freelancer-style plain text, no table. Totals come before the line details."""
    pen = Pen(c)
    t = spec.truth
    y = H - 60
    for line in _seller_lines(spec, st):
        pen.text(50, y, line)
        y -= 12
    y -= 10
    pen.text(50, y, f"{_title(spec, st)} {t.invoice_number}", size=14, bold=True)
    y -= 20
    for k, v in _meta_lines(spec, st)[1:]:
        pen.text(50, y, f"{k}: {v}")
        y -= 12
    y -= 8
    for line in _buyer_lines(st):
        pen.text(50, y, line)
        y -= 12
    y -= 10
    if st.money_mode == "code_header":
        pen.text(50, y, st.L["amounts_in"].format(c=t.currency))
        y -= 14
    for label, value, _ in _totals_rows(spec, st):
        pen.text(50, y, f"{label}: {value}", bold=label == st.L["total"])
        y -= 12
    y -= 10
    for item in t.line_items:
        pen.text(50, y, f"- {item.description}: {st.qty(item.quantity)} x "
                        f"{st.num(item.unit_price)} = {st.num(item.amount)}")
        y -= 12
    if len(t.tax_lines) > 1:
        y -= 6
        for item, rate in zip(t.line_items, spec.line_rates):
            if rate != t.tax_lines[0].rate_percent:
                pen.text(50, y, f"* {item.description}: {st.L['vat']} {st.qty(rate)}%", size=8)
                y -= 10
    y -= 10
    for line in _bank_lines(spec, st):
        pen.text(50, y, line, size=8)
        y -= 11


def _multipage(c: Canvas, spec: InvoiceSpec, st: Style, rng: random.Random) -> None:
    pen = Pen(c)
    t = spec.truth
    split = rng.randint(17, 21)
    first, rest = t.line_items[:split], t.line_items[split:]
    r1, r2 = spec.line_rates[:split], spec.line_rates[split:]
    carried = round(sum(i.amount for i in first), 2)
    # page 1
    y = H - 60
    for i, line in enumerate(_seller_lines(spec, st)):
        pen.text(50, y, line, size=11 if i == 0 else 9, bold=i == 0)
        y -= 12
    pen.text(W - 50, H - 60, _title(spec, st), size=16, bold=True, align="right")
    yy = H - 82
    for k, v in _meta_lines(spec, st):
        pen.text(W - 50, yy, f"{k}: {v}", align="right")
        yy -= 12
    y = min(y, yy) - 12
    for line in _buyer_lines(st):
        pen.text(50, y, line)
        y -= 12
    if st.money_mode == "code_header":
        pen.text(50, y - 4, st.L["amounts_in"].format(c=t.currency), size=8)
    y = _draw_items(pen, spec, st, first, r1, y - 25)
    pen.text(330, y - 8, st.L["carried"], bold=True)
    pen.text(545, y - 8, st.money(carried), align="right")
    pen.text(W / 2, 30, st.L["page"].format(p=1, n=2), size=8, align="center")
    c.showPage()
    # page 2
    pen.text(50, H - 50, f"{t.seller_name} - {_title(spec, st)} {t.invoice_number}", size=8)
    pen.text(330, H - 75, st.L["brought"], bold=True)
    pen.text(545, H - 75, st.money(carried), align="right")
    y = _draw_items(pen, spec, st, rest, r2, H - 100)
    _draw_totals(pen, spec, st, y - 12)
    y = 90
    for line in _bank_lines(spec, st):
        pen.text(50, y, line, size=8)
        y -= 11
    pen.text(W / 2, 30, st.L["page"].format(p=2, n=2), size=8, align="center")


LAYOUTS = {"classic": _classic, "two_column": _two_column, "minimal": _minimal,
           "multipage": _multipage}


def render(spec: InvoiceSpec, path: Path) -> None:
    rng = random.Random(spec.block_order_seed)
    st = Style(spec, rng)
    c = Canvas(str(path), pagesize=A4, invariant=1)
    c.setTitle(f"{spec.doc_id}")
    c.setAuthor("synthetic")
    LAYOUTS[spec.template](c, spec, st, rng)
    c.showPage()
    c.save()
