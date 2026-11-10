"""Build one invoice's ground truth (what is printed) and its injected anomaly, if any."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from invoice_agent.money import iban_is_valid, iban_with_check_digits, normalize_id
from invoice_agent.schemas import InvoiceData, IssueCode, LineItem, TaxLine
from invoice_agent.synth.world import BUYER, COUNTRY, Vendor, _fill

CATALOG: dict[str, list[tuple[str, float, float, str]]] = {
    # category: (description, min unit price, max unit price, quantity kind)
    "office": [("A4 copier paper, 80gsm (box of 5 reams)", 18, 32, "units"),
               ("Ergonomic office chair, mesh back", 140, 320, "units"),
               ("Whiteboard markers, assorted (pack of 12)", 6, 14, "units"),
               ("Laser toner cartridge, black", 45, 120, "units"),
               ("Standing desk converter", 90, 260, "units"),
               ("Notebooks, A5 dotted (pack of 10)", 12, 28, "units")],
    "cloud": [("Managed Kubernetes cluster, monthly", 300, 1800, "months"),
              ("Object storage, per TB-month", 18, 40, "tb"),
              ("Dedicated GPU instance, per hour", 1.8, 4.5, "hours"),
              ("Premium support plan, monthly", 150, 900, "months"),
              ("Load balancer, monthly", 20, 60, "months"),
              ("Backup retention add-on", 40, 200, "months")],
    "consulting": [("Senior consultant, on-site", 95, 180, "hours"),
                   ("Data platform architecture review", 110, 220, "hours"),
                   ("Workshop facilitation (half day)", 600, 1400, "units"),
                   ("Project management", 70, 130, "hours"),
                   ("Travel expenses (as agreed)", 80, 600, "units")],
    "translation": [("Translation EN>DE, per 1,000 words", 90, 140, "units"),
                    ("Translation EN>FR, per 1,000 words", 90, 140, "units"),
                    ("Proofreading, per hour", 35, 60, "hours"),
                    ("Rush delivery surcharge", 50, 200, "units")],
    "marketing": [("Brand identity design", 1200, 4800, "units"),
                  ("Social media content package", 400, 1500, "months"),
                  ("Photography, half-day shoot", 450, 1100, "units"),
                  ("Copywriting", 60, 120, "hours"),
                  ("Printed brochures (500 pcs)", 180, 520, "units")],
    "logistics": [("Pallet transport, Rotterdam to Amsterdam", 120, 380, "units"),
                  ("Express courier, document envelope", 18, 45, "units"),
                  ("Warehouse storage, per pallet-week", 6, 14, "units"),
                  ("Customs clearance fee", 45, 160, "units"),
                  ("Fuel surcharge", 15, 90, "units")],
    "catering": [("Lunch buffet, per person", 14, 32, "units"),
                 ("Coffee and pastries, per person", 5, 12, "units"),
                 ("Event staff, per hour", 28, 45, "hours"),
                 ("Beverage package", 80, 400, "units")],
    "hardware": [("Laptop, 14-inch, 32 GB RAM", 1100, 2200, "units"),
                 ("27-inch 4K monitor", 280, 520, "units"),
                 ("USB-C docking station", 140, 290, "units"),
                 ("On-site installation", 65, 110, "hours"),
                 ("Extended warranty, 3 years", 90, 240, "units")],
}

SHIPPING = {"en": "Shipping and handling", "fr": "Frais de port", "de": "Versandkosten"}
DISCOUNT = {"en": "Discount {p}%", "fr": "Remise {p} %", "de": "Rabatt {p} %"}

TEMPLATES = ["classic", "two_column", "minimal", "multipage"]


@dataclass
class InvoiceSpec:
    """Everything the renderer needs. `truth` holds the values exactly as printed."""

    doc_id: str
    template: str
    locale: str
    vendor: Vendor
    truth: InvoiceData
    anomaly: IssueCode | None
    line_rates: list[float]
    vendor_address: list[str]
    vendor_bic: str | None
    order_date: date | None = None
    delivery_date: date | None = None
    payment_terms_days: int | None = None
    amount_paid: float | None = None
    original_invoice: str | None = None
    block_order_seed: int = 0
    extras: dict = field(default_factory=dict)


def _money(x: float) -> float:
    return round(x + 1e-9, 2)


def _invoice_number(rng: random.Random, vendor: Vendor, year: int) -> str:
    return vendor.number_style.format(prefix=vendor.prefix, year=year, n=rng.randint(12, 9800))


def _lines(rng: random.Random, vendor: Vendor, n: int, lang: str, rates: tuple[float, float],
           mixed: bool) -> tuple[list[LineItem], list[float]]:
    items, line_rates = [], []
    catalog = CATALOG[vendor.category]
    for _ in range(n):
        desc, lo, hi, kind = rng.choice(catalog)
        if kind == "hours":
            qty = rng.choice([0.5, 1, 1.5, 2, 3, 4, 6, 7.5, 8, 12, 16, 24, 37.5, 40])
        elif kind == "months":
            qty = rng.choice([1, 1, 1, 2, 3, 12])
        elif kind == "tb":
            qty = rng.choice([1, 2, 5, 10, 25, 40])
        else:
            qty = rng.choice([1, 1, 2, 3, 4, 5, 6, 10, 12, 20, 25, 50])
        unit = _money(rng.uniform(lo, hi))
        items.append(LineItem(description=desc, quantity=qty, unit_price=unit,
                              amount=_money(qty * unit)))
        line_rates.append(rates[1] if (mixed and rng.random() < 0.35) else rates[0])
    if mixed and len(set(line_rates)) == 1 and len(items) > 1:
        line_rates[-1] = rates[1]
    if rng.random() < 0.2:
        price = _money(rng.uniform(9, 65))
        items.append(LineItem(description=SHIPPING[lang], quantity=1, unit_price=price,
                              amount=price))
        line_rates.append(rates[0])
    if rng.random() < 0.15 and len(items) > 1:
        pct = rng.choice([5, 10, 15])
        base = sum(i.amount for i, r in zip(items, line_rates) if r == rates[0])
        disc = -_money(base * pct / 100)
        items.append(LineItem(description=DISCOUNT[lang].format(p=pct), quantity=1,
                              unit_price=disc, amount=disc))
        line_rates.append(rates[0])
    return items, line_rates


def build_invoice(rng: random.Random, doc_id: str, vendor: Vendor, anomaly: IssueCode | None,
                  ledger: list[dict]) -> InvoiceSpec:
    """Build the printed values for one document from `vendor`, injecting `anomaly` if set."""
    lang = vendor.locale[:2] if vendor.locale[:2] in ("fr", "de") else "en"
    template = rng.choices(TEMPLATES, weights=[4, 3, 2, 1.5])[0]
    standard, reduced = COUNTRY[vendor.country][2], COUNTRY[vendor.country][3]
    taxed = standard > 0
    mixed = taxed and rng.random() < 0.25
    n_lines = rng.randint(26, 38) if template == "multipage" else rng.randint(1, 6)
    items, line_rates = _lines(rng, vendor, n_lines, lang, (standard, reduced), mixed)

    issue = date(2026, 1, 5) + timedelta(days=rng.randint(0, 290))
    terms = rng.choice([14, 30, 30, 45, 60])
    due_printed = rng.random() < 0.75 or anomaly == IssueCode.DUE_BEFORE_ISSUE
    due = issue + timedelta(days=terms) if due_printed else None
    if anomaly == IssueCode.DUE_BEFORE_ISSUE:
        due = issue - timedelta(days=rng.choice([3, 10, 30, 31, 61]))

    number = _invoice_number(rng, vendor, issue.year)
    if anomaly == IssueCode.DUPLICATE_INVOICE:
        prior = [e for e in ledger if e["vendor_id"] == vendor.vendor_id]
        number = rng.choice(prior)["invoice_number"]
    else:
        taken = {e["invoice_number"] for e in ledger if e["vendor_id"] == vendor.vendor_id}
        while number in taken:
            number = _invoice_number(rng, vendor, issue.year)

    # Tax summary per rate, computed from the line items.
    bases: dict[float, float] = {}
    for item, rate in zip(items, line_rates):
        bases[rate] = _money(bases.get(rate, 0.0) + item.amount)

    if anomaly == IssueCode.LINE_SUM_MISMATCH:
        delta = rng.choice([-1, 1]) * _money(rng.choice([rng.uniform(10, 400), 90, 100, 9]))
        bases[standard] = _money(bases.get(standard, 0.0) + delta)
    subtotal = _money(sum(bases.values()))

    tax_lines: list[TaxLine] = []
    if taxed:
        for rate in sorted(bases, reverse=True):
            amount = _money(bases[rate] * rate / 100)
            tax_lines.append(TaxLine(rate_percent=rate, base=bases[rate], amount=amount))
    if anomaly == IssueCode.VAT_MISCALCULATED:
        tl = tax_lines[0]
        wrong_rate = rng.choice([r for r in (16.0, 19.0, 21.0, 25.0, 10.0) if r != tl.rate_percent])
        tax_lines[0] = TaxLine(rate_percent=tl.rate_percent, base=tl.base,
                               amount=_money(tl.base * wrong_rate / 100))
    tax_total = _money(sum(t.amount for t in tax_lines))
    total = _money(subtotal + tax_total)
    if anomaly == IssueCode.TOTAL_MISMATCH:
        delta = rng.choice([10.0, 100.0, 0.9, 1000.0, _money(rng.uniform(5, 300))])
        if delta < total / 2 and rng.random() < 0.5:
            delta = -delta
        total = _money(total + delta)

    iban = vendor.iban
    if anomaly == IssueCode.BANK_ACCOUNT_CHANGED:
        pattern = COUNTRY[vendor.country][4]
        iban = iban_with_check_digits(vendor.country, _fill(rng, pattern))
    if anomaly == IssueCode.INVALID_IBAN:
        original = normalize_id(iban)
        digit_positions = [i for i, ch in enumerate(original) if i >= 4 and ch.isdigit()]
        candidate = original
        while iban_is_valid(candidate):  # a single changed digit always breaks mod-97
            chars = list(original)
            pos = rng.choice(digit_positions)
            chars[pos] = rng.choice([d for d in "0123456789" if d != chars[pos]])
            candidate = "".join(chars)
        iban = candidate

    doc_type = "credit_note" if (rng.random() < 0.08 and anomaly is None) else "invoice"
    truth = InvoiceData(
        document_type=doc_type,
        invoice_number=number,
        invoice_date=issue.isoformat(),
        due_date=due.isoformat() if due else None,
        currency=vendor.currency,
        seller_name=vendor.name,
        seller_vat_id=vendor.vat_id,
        buyer_name=BUYER["name"],
        po_number=f"PO-{rng.randint(10000, 99999)}" if rng.random() < 0.5 else None,
        iban=iban,
        line_items=items,
        tax_lines=tax_lines,
        subtotal=subtotal,
        tax_total=tax_total,
        total=total,
    )
    amount_paid = None
    if doc_type == "invoice" and rng.random() < 0.15:
        amount_paid = _money(total * rng.choice([0.2, 0.3, 0.5]))
    return InvoiceSpec(
        doc_id=doc_id,
        template=template,
        locale=vendor.locale,
        vendor=vendor,
        truth=truth,
        anomaly=anomaly,
        line_rates=line_rates,
        vendor_address=vendor.address,
        vendor_bic=vendor.bic,
        order_date=issue - timedelta(days=rng.randint(3, 25)) if rng.random() < 0.4 else None,
        delivery_date=issue - timedelta(days=rng.randint(0, 6)) if rng.random() < 0.35 else None,
        payment_terms_days=terms,
        amount_paid=amount_paid,
        original_invoice=(_invoice_number(rng, vendor, issue.year)
                          if doc_type == "credit_note" else None),
        block_order_seed=rng.randint(0, 10_000),
    )
