"""Profile for US broadcast-advertising invoices (TV/cable stations billing ad agencies).

Same graph, different schema and checks: these invoices have no VAT or IBAN, but they have
their own arithmetic (gross airtime - agency commission - other deductions = net due).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from invoice_agent.checks import CheckResult, is_grounded
from invoice_agent.money import close
from invoice_agent.profiles import Profile
from invoice_agent.schemas import Issue, IssueCode, IssueKind, Severity
from invoice_agent.snap import keep_dropped_values


class Deduction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="The deduction's label as printed.")
    amount: float = Field(description="Deducted amount as a positive number.")


class MediaInvoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    station: str | None = Field(
        description="Station, network or media company that issued the invoice.")
    invoice_number: str | None = Field(description="Invoice number as printed.")
    invoice_date: str | None = Field(description="Invoice date as YYYY-MM-DD.")
    agency: str | None = Field(
        description="Advertising agency billed on behalf of the advertiser. Not the station. "
        "Null if the advertiser is billed directly.")
    advertiser: str | None = Field(
        description="Advertiser, client or campaign whose ads ran (e.g. a candidate committee).")
    gross_total: float | None = Field(
        description="Gross amount billed for the airtime on this invoice, before commissions.")
    agency_commission: float | None = Field(
        description="Agency commission deducted, as a positive number. Null if none is shown.")
    other_deductions: list[Deduction] = Field(
        description="Any other deductions between gross and net (rep firm commission, "
        "discounts). Empty if none.")
    net_amount_due: float | None = Field(
        description="Net amount due after commissions and deductions, as printed.")
    payment_terms: str | None = Field(description="Payment terms as printed, e.g. 'Net 30'.")


MEDIA_RULES = """\
You extract US broadcast-advertising invoices (TV, radio and cable stations billing for
airtime) into the schema.

Rules:
- Copy amounts and identifiers exactly as printed. Never compute or "fix" a number: if the
  printed numbers do not add up, keep them as printed.
- Amounts are plain positive numbers ("($3,369.90)" is 3369.90).
- If the document has per-page or per-week subtotals and an invoice total, use the invoice
  totals. Ignore spot-level line rates.
- The station issues the invoice. The agency is the advertising agency it bills; the
  advertiser is the agency's client. Station IDs and numeric codes in parentheses are not
  part of names.
- Dates are YYYY-MM-DD; US documents write MM/DD/YYYY.
- The text comes from a PDF and may be out of reading order.
"""

AMOUNT_FIELDS = ("gross_total", "agency_commission", "net_amount_due")


def _err(code, kind, msg, field=None) -> Issue:
    return Issue(code=code, kind=kind, severity=Severity.ERROR, message=msg, field=field)


def media_checks(inv: MediaInvoice, text: str, deps=None, thread_id=None) -> CheckResult:
    issues: list[Issue] = []
    if inv.gross_total is None and inv.net_amount_due is None:
        issues.append(_err(IssueCode.MISSING_FIELD, IssueKind.EXTRACTION,
                           "neither a gross total nor a net amount due was extracted"))
    for name in AMOUNT_FIELDS:
        value = getattr(inv, name)
        if value is not None and not is_grounded(name, value, text):
            issues.append(_err(IssueCode.UNGROUNDED_VALUE, IssueKind.EXTRACTION,
                               f"{name}={value} does not appear in the document", name))
    for d in inv.other_deductions:
        if not is_grounded("amount", d.amount, text):
            issues.append(_err(IssueCode.UNGROUNDED_VALUE, IssueKind.EXTRACTION,
                               f"deduction {d.label!r}={d.amount} does not appear in the "
                               "document", "other_deductions"))
    if inv.invoice_number and not is_grounded("invoice_number", inv.invoice_number, text):
        issues.append(_err(IssueCode.UNGROUNDED_VALUE, IssueKind.EXTRACTION,
                           f"invoice_number={inv.invoice_number!r} does not appear in the "
                           "document", "invoice_number"))
    if inv.invoice_date:
        try:
            date.fromisoformat(inv.invoice_date)
        except ValueError:
            issues.append(_err(IssueCode.MISSING_FIELD, IssueKind.EXTRACTION,
                               f"invoice_date {inv.invoice_date!r} is not YYYY-MM-DD",
                               "invoice_date"))
    if inv.gross_total is not None and inv.net_amount_due is not None:
        deducted = (inv.agency_commission or 0) + sum(d.amount for d in inv.other_deductions)
        if not close(inv.gross_total - deducted, inv.net_amount_due, tol=0.02):
            issues.append(_err(IssueCode.TOTAL_MISMATCH, IssueKind.CONSISTENCY,
                               f"gross {inv.gross_total:.2f} - deductions {deducted:.2f} != "
                               f"net {inv.net_amount_due:.2f}", "net_amount_due"))
    if inv.gross_total and inv.agency_commission is not None:
        rate = inv.agency_commission / inv.gross_total
        if abs(rate - 0.15) > 0.006:
            issues.append(Issue(code=IssueCode.UNUSUAL_COMMISSION, kind=IssueKind.POLICY,
                                severity=Severity.WARNING,
                                message=f"agency commission is {rate:.1%} of gross "
                                "(industry standard is 15%)", field="agency_commission"))
    return CheckResult(issues=issues)


def _media_post(inv: MediaInvoice, previous: MediaInvoice | None, text: str):
    inv, restored = keep_dropped_values(inv, previous, text, fields=AMOUNT_FIELDS)
    return inv, ({"restored_after_repair": restored} if restored else {})


MEDIA_INVOICE = Profile(
    name="media_invoice",
    schema=MediaInvoice,
    check=media_checks,
    postprocess=_media_post,
    numeric_fields=frozenset(AMOUNT_FIELDS),
    locked_fields=frozenset({"other_deductions"}),
)
