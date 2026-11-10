"""Deterministic checks on an extracted invoice. No LLM calls in here.

Each check returns Issues tagged with a kind, which drives routing in the graph:
EXTRACTION / CONSISTENCY errors earn one targeted re-read; DOCUMENT / POLICY errors (and
anything still wrong after the re-read) go to a human.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from invoice_agent.money import amount_variants, close, iban_is_valid, normalize_id, squash
from invoice_agent.schemas import InvoiceData, Issue, IssueCode, IssueKind, Severity
from invoice_agent.store import Ledger, VendorDirectory

MONTHS_EN = ["january", "february", "march", "april", "may", "june", "july", "august",
             "september", "october", "november", "december"]
MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
             "septembre", "octobre", "novembre", "décembre"]


@dataclass
class CheckResult:
    issues: list[Issue] = field(default_factory=list)
    vendor_id: str | None = None
    vendor_match: str | None = None

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]


def _err(code: IssueCode, kind: IssueKind, msg: str, field: str | None = None) -> Issue:
    return Issue(code=code, kind=kind, severity=Severity.ERROR, message=msg, field=field)


def _warn(code: IssueCode, kind: IssueKind, msg: str, field: str | None = None) -> Issue:
    return Issue(code=code, kind=kind, severity=Severity.WARNING, message=msg, field=field)


def date_variants(iso: str) -> set[str]:
    d = date.fromisoformat(iso)
    dd, mm, yyyy = f"{d.day:02d}", f"{d.month:02d}", str(d.year)
    return {
        f"{dd}/{mm}/{yyyy}", f"{mm}/{dd}/{yyyy}", f"{dd}.{mm}.{yyyy}", f"{dd}-{mm}-{yyyy}",
        iso, f"{d.day}{MONTHS_EN[d.month - 1]}{yyyy}", f"{MONTHS_EN[d.month - 1]}{d.day},{yyyy}",
        f"{d.day}{MONTHS_FR[d.month - 1]}{yyyy}",
    }


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def check_fields(inv: InvoiceData) -> list[Issue]:
    issues = []
    for name in ("invoice_number", "seller_name", "currency"):
        if not getattr(inv, name).strip():
            issues.append(_err(IssueCode.MISSING_FIELD, IssueKind.EXTRACTION,
                               f"{name} is empty", name))
    if _parse_date(inv.invoice_date) is None:
        issues.append(_err(IssueCode.MISSING_FIELD, IssueKind.EXTRACTION,
                           f"invoice_date {inv.invoice_date!r} is not a YYYY-MM-DD date",
                           "invoice_date"))
    if inv.due_date and _parse_date(inv.due_date) is None:
        issues.append(_err(IssueCode.MISSING_FIELD, IssueKind.EXTRACTION,
                           f"due_date {inv.due_date!r} is not a YYYY-MM-DD date", "due_date"))
    if not inv.line_items:
        issues.append(_err(IssueCode.MISSING_FIELD, IssueKind.EXTRACTION,
                           "no line items extracted", "line_items"))
    return issues


def value_variants(field_name: str, value) -> set[str]:
    if field_name in ("subtotal", "tax_total", "total", "amount"):
        return amount_variants(float(value))
    if field_name in ("invoice_date", "due_date"):
        return date_variants(value) if _parse_date(value) else {str(value)}
    return {str(value)}


def is_grounded(field_name: str, value, text: str) -> bool:
    """Does `value` literally appear in the document (modulo whitespace and number format)?"""
    if value is None:
        return False
    hay = squash(text)
    return any(squash(v) in hay for v in value_variants(field_name, value))


def check_grounding(inv: InvoiceData, text: str, buyer_vat_id: str | None) -> list[Issue]:
    """Every key value must literally appear in the document text (catches misreads)."""
    hay = squash(text)
    issues = []

    def missing(field_name: str, value, variants: set[str]) -> None:
        if not any(squash(v) in hay for v in variants):
            issues.append(_err(IssueCode.UNGROUNDED_VALUE, IssueKind.EXTRACTION,
                               f"{field_name}={value!r} does not appear in the document",
                               field_name))

    missing("invoice_number", inv.invoice_number, {inv.invoice_number})
    for name in ("seller_vat_id", "iban", "po_number"):
        value = getattr(inv, name)
        if value:
            missing(name, value, {value})
    for name in ("subtotal", "tax_total", "total"):
        missing(name, getattr(inv, name), amount_variants(getattr(inv, name)))
    for name in ("invoice_date", "due_date"):
        value = getattr(inv, name)
        if value and _parse_date(value):
            missing(name, value, date_variants(value))
    bad_lines = [i for i, li in enumerate(inv.line_items)
                 if not any(squash(v) in hay for v in amount_variants(li.amount))]
    if bad_lines:
        issues.append(_err(IssueCode.UNGROUNDED_VALUE, IssueKind.EXTRACTION,
                           f"line item amounts at positions {bad_lines} do not appear in the "
                           "document", "line_items"))
    if buyer_vat_id and normalize_id(inv.seller_vat_id) == normalize_id(buyer_vat_id):
        issues.append(_err(IssueCode.SELLER_IS_BUYER, IssueKind.EXTRACTION,
                           "seller_vat_id is the buyer's (our own) VAT ID", "seller_vat_id"))
    return issues


def check_arithmetic(inv: InvoiceData) -> list[Issue]:
    issues = []
    bad = [i for i, li in enumerate(inv.line_items)
           if not close(li.quantity * li.unit_price, li.amount)]
    if bad:
        issues.append(_err(IssueCode.LINE_MATH, IssueKind.CONSISTENCY,
                           f"quantity x unit price != amount on line items {bad}", "line_items"))
    line_sum = sum(li.amount for li in inv.line_items)
    if not close(line_sum, inv.subtotal):
        issues.append(_err(IssueCode.LINE_SUM_MISMATCH, IssueKind.CONSISTENCY,
                           f"line items sum to {line_sum:.2f} but subtotal is {inv.subtotal:.2f}",
                           "subtotal"))
    if inv.tax_lines:
        bases = sum(t.base for t in inv.tax_lines)
        taxes = sum(t.amount for t in inv.tax_lines)
        if len(inv.tax_lines) > 1 and not close(bases, inv.subtotal):
            issues.append(_err(IssueCode.TAX_LINES_MISMATCH, IssueKind.CONSISTENCY,
                               f"tax bases sum to {bases:.2f} but subtotal is {inv.subtotal:.2f}",
                               "tax_lines"))
        if not close(taxes, inv.tax_total):
            issues.append(_err(IssueCode.TAX_LINES_MISMATCH, IssueKind.CONSISTENCY,
                               f"tax lines sum to {taxes:.2f} but tax_total is "
                               f"{inv.tax_total:.2f}", "tax_total"))
        for t in inv.tax_lines:
            base = t.base if len(inv.tax_lines) > 1 else inv.subtotal
            expected = base * t.rate_percent / 100
            if not close(expected, t.amount, tol=0.02):
                issues.append(_err(IssueCode.VAT_MISCALCULATED, IssueKind.CONSISTENCY,
                                   f"{t.rate_percent:g}% of {base:.2f} is {expected:.2f}, "
                                   f"invoice says {t.amount:.2f}", "tax_lines"))
    elif abs(inv.tax_total) > 0.005:
        issues.append(_err(IssueCode.TAX_LINES_MISMATCH, IssueKind.CONSISTENCY,
                           "tax_total is non-zero but no tax lines were extracted", "tax_lines"))
    if not close(inv.subtotal + inv.tax_total, inv.total):
        issues.append(_err(IssueCode.TOTAL_MISMATCH, IssueKind.CONSISTENCY,
                           f"subtotal + tax = {inv.subtotal + inv.tax_total:.2f} but total is "
                           f"{inv.total:.2f}", "total"))
    issued, due = _parse_date(inv.invoice_date), _parse_date(inv.due_date)
    if issued and due and due < issued:
        issues.append(_err(IssueCode.DUE_BEFORE_ISSUE, IssueKind.CONSISTENCY,
                           f"due date {due} is before the invoice date {issued}", "due_date"))
    return issues


def check_document(inv: InvoiceData) -> list[Issue]:
    if inv.iban and not iban_is_valid(inv.iban):
        return [_err(IssueCode.INVALID_IBAN, IssueKind.DOCUMENT,
                     f"IBAN {inv.iban} fails the ISO 13616 checksum", "iban")]
    return []


def check_policy(inv: InvoiceData, vendors: VendorDirectory, ledger: Ledger,
                 thread_id: str | None) -> tuple[list[Issue], str | None, str | None]:
    issues = []
    match = vendors.match(inv.seller_name, inv.seller_vat_id)
    if match is None:
        issues.append(_err(IssueCode.UNKNOWN_VENDOR, IssueKind.POLICY,
                           f"seller {inv.seller_name!r} ({inv.seller_vat_id}) is not in the "
                           "vendor master file", "seller_name"))
        return issues, None, None
    v = match.vendor
    if v.get("iban") and inv.iban and normalize_id(inv.iban) != normalize_id(v["iban"]):
        issues.append(_err(IssueCode.BANK_ACCOUNT_CHANGED, IssueKind.POLICY,
                           f"IBAN {inv.iban} differs from the one on file for {v['name']} "
                           "(possible payment fraud: verify by phone)", "iban"))
    if v.get("currency") and inv.currency and inv.currency.upper() != v["currency"]:
        issues.append(_warn(IssueCode.CURRENCY_MISMATCH, IssueKind.POLICY,
                            f"invoice currency {inv.currency} differs from vendor default "
                            f"{v['currency']}", "currency"))
    dup = ledger.find_duplicate(v["vendor_id"], inv.invoice_number, exclude_thread=thread_id)
    if dup:
        issues.append(_err(IssueCode.DUPLICATE_INVOICE, IssueKind.POLICY,
                           f"{v['name']} invoice {inv.invoice_number} was already posted on "
                           f"{dup.get('created_at') or dup.get('invoice_date')}",
                           "invoice_number"))
    return issues, v["vendor_id"], match.method


def run_checks(inv: InvoiceData, text: str, vendors: VendorDirectory, ledger: Ledger,
               buyer_vat_id: str | None = None, thread_id: str | None = None) -> CheckResult:
    issues = check_fields(inv)
    issues += check_grounding(inv, text, buyer_vat_id)
    issues += check_arithmetic(inv)
    issues += check_document(inv)
    policy, vendor_id, method = check_policy(inv, vendors, ledger, thread_id)
    issues += policy
    return CheckResult(issues=issues, vendor_id=vendor_id, vendor_match=method)
