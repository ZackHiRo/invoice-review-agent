"""Baseline: one LLM call that extracts AND reviews, with the vendor master and ledger in
the prompt. Same model and extraction rules as the agent; no graph, no deterministic checks."""

from __future__ import annotations

import json
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from invoice_agent.extract import extraction_rules, usage_of
from invoice_agent.schemas import InvoiceData

FlagCode = Literal["line_sum_mismatch", "vat_miscalculated", "total_mismatch",
                   "due_before_issue", "invalid_iban", "unknown_vendor",
                   "bank_account_changed", "duplicate_invoice"]

REVIEW_RULES = """\
Then decide whether the invoice can be paid automatically or needs human review.
Flag every problem you find with these codes:
- line_sum_mismatch: the line item amounts do not add up to the subtotal
- vat_miscalculated: a VAT amount is not base x rate
- total_mismatch: subtotal + tax does not equal the total
- due_before_issue: the due date is before the invoice date
- invalid_iban: the IBAN fails the ISO 13616 mod-97 checksum
- unknown_vendor: the seller is not in the vendor master file below
- bank_account_changed: the IBAN differs from the vendor master file
- duplicate_invoice: this vendor's invoice number is already in the ledger below
Set needs_review to true if there is any flag, false otherwise.
"""


class BaselineOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice: InvoiceData
    flags: list[FlagCode]
    needs_review: bool
    reason: str = Field(description="One sentence explaining the decision.")


class BaselineReviewer:
    def __init__(self, vendors: list[dict], ledger_rows: list[dict],
                 model: str = "gpt-4.1-mini", buyer: dict | None = None, timeout: float = 90):
        from langchain_openai import ChatOpenAI

        self.model = model
        self.rules = extraction_rules(buyer)
        self.context = (
            "Vendor master file:\n" + json.dumps(vendors, ensure_ascii=False) +
            "\n\nLedger of already-posted invoices:\n" +
            json.dumps([{k: r[k] for k in ("vendor_id", "invoice_number", "invoice_date", "total")}
                        for r in ledger_rows])
        )
        llm = ChatOpenAI(model=model, temperature=0, timeout=timeout, max_retries=3)
        self.runnable = llm.with_structured_output(
            BaselineOutput, method="json_schema", strict=True, include_raw=True)

    def review(self, text: str) -> tuple[BaselineOutput, dict]:
        out = self.runnable.invoke([
            SystemMessage(self.rules + "\n" + REVIEW_RULES + "\n" + self.context),
            HumanMessage(f"<document>\n{text}\n</document>"),
        ])
        if out.get("parsing_error") or out.get("parsed") is None:
            raise ValueError(f"structured output failed: {out.get('parsing_error')}")
        return out["parsed"], usage_of(out["raw"], self.model)
