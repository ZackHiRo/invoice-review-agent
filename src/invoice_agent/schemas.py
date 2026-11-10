"""Typed contracts shared by the extractor, the checks, the graph and the eval.

The extraction models are sent to OpenAI as a strict JSON schema, so every field is
required (optional values are nullable, not defaulted).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(description="Line description exactly as printed.")
    quantity: float = Field(description="Quantity as printed (hours, units...).")
    unit_price: float = Field(description="Net unit price as printed.")
    amount: float = Field(
        description="Net line amount as printed. Discounts are negative amounts."
    )


class TaxLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rate_percent: float = Field(description="VAT/tax rate in percent, e.g. 20 for 20%.")
    base: float = Field(description="Net amount this rate applies to, as printed.")
    amount: float = Field(description="Tax amount for this rate, as printed.")


class InvoiceData(BaseModel):
    """What the extractor returns. Values are copied from the document, never recomputed."""

    model_config = ConfigDict(extra="forbid")

    document_type: Literal["invoice", "credit_note"]
    invoice_number: str = Field(
        description="The number of THIS document (for a credit note: the credit note number, "
        "not the original invoice it refers to)."
    )
    invoice_date: str = Field(description="Issue date as YYYY-MM-DD.")
    due_date: str | None = Field(
        description="Payment due date as YYYY-MM-DD, only if a date is printed. "
        "Null if only terms like 'Net 30' are given."
    )
    currency: str = Field(description="ISO 4217 code, e.g. EUR, GBP, USD.")
    seller_name: str = Field(description="Legal name of the company issuing the invoice.")
    seller_vat_id: str | None = Field(
        description="The SELLER's VAT or tax ID (an EIN for US companies) as printed, without "
        "its label. Not the buyer's."
    )
    buyer_name: str = Field(description="Name of the customer being billed.")
    po_number: str | None = Field(description="Customer purchase order number, if printed.")
    iban: str | None = Field(description="Seller's IBAN for payment, if printed, without spaces.")
    line_items: list[LineItem]
    tax_lines: list[TaxLine] = Field(
        description="One entry per tax rate shown in the tax summary. Empty if no tax."
    )
    subtotal: float = Field(description="Net total before tax, as printed.")
    tax_total: float = Field(description="Total tax, as printed.")
    total: float = Field(
        description="Gross invoice total including tax, as printed. Not 'balance due' "
        "after payments."
    )


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class IssueKind(StrEnum):
    # Probably our own extraction mistake: worth a second, targeted read.
    EXTRACTION = "extraction"
    # The numbers on the document do not add up. Could be a misread or a bad invoice.
    CONSISTENCY = "consistency"
    # The document is wrong in a way re-reading cannot fix (bad IBAN checksum, dates).
    DOCUMENT = "document"
    # Business rules against the vendor master and the ledger (fraud and duplicates).
    POLICY = "policy"


class IssueCode(StrEnum):
    MISSING_FIELD = "missing_field"
    UNGROUNDED_VALUE = "ungrounded_value"
    SELLER_IS_BUYER = "seller_is_buyer"
    LINE_MATH = "line_math"
    LINE_SUM_MISMATCH = "line_sum_mismatch"
    TAX_LINES_MISMATCH = "tax_lines_mismatch"
    VAT_MISCALCULATED = "vat_miscalculated"
    TOTAL_MISMATCH = "total_mismatch"
    DUE_BEFORE_ISSUE = "due_before_issue"
    INVALID_IBAN = "invalid_iban"
    UNKNOWN_VENDOR = "unknown_vendor"
    BANK_ACCOUNT_CHANGED = "bank_account_changed"
    DUPLICATE_INVOICE = "duplicate_invoice"
    CURRENCY_MISMATCH = "currency_mismatch"


class Issue(BaseModel):
    code: IssueCode
    kind: IssueKind
    severity: Severity
    message: str
    field: str | None = None


# The anomaly codes the synthetic data injects and the eval scores on.
ANOMALY_CODES: tuple[IssueCode, ...] = (
    IssueCode.LINE_SUM_MISMATCH,
    IssueCode.VAT_MISCALCULATED,
    IssueCode.TOTAL_MISMATCH,
    IssueCode.DUE_BEFORE_ISSUE,
    IssueCode.INVALID_IBAN,
    IssueCode.UNKNOWN_VENDOR,
    IssueCode.BANK_ACCOUNT_CHANGED,
    IssueCode.DUPLICATE_INVOICE,
)


class ReviewDecision(BaseModel):
    """What a human reviewer sends back to resume a paused run."""

    action: Literal["approve", "reject", "correct"]
    corrections: dict[str, str | float | None] = Field(
        default_factory=dict,
        description="Header field -> corrected value, used with action='correct'.",
    )
    reviewer: str = "reviewer"
    note: str = ""


Route = Literal["auto_approve", "human_review", "repair"]
Status = Literal["processing", "posted", "rejected", "pending_review"]
