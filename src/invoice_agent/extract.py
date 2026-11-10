"""LLM extraction with OpenAI strict structured outputs (via langchain-openai)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from invoice_agent.schemas import InvoiceData, Issue

EXTRACTION_RULES = """\
You are an accounts-payable data entry specialist. Extract the document into the schema.

Rules:
- Copy values exactly as printed. Never compute, correct or "fix" numbers, even when they do
  not add up: downstream checks need the printed values to catch bad invoices.
- Amounts are plain numbers with "." as decimal separator ("1.234,56" and "1 234,56" are
  1234.56). Discounts printed with a minus sign are negative.
- Dates are YYYY-MM-DD. Read day/month order from the vendor's country: US vendors write
  MM/DD/YYYY, UK and EU vendors write DD/MM/YYYY.
- The seller issues the document. The buyer is under "Bill To", "Facturé à" or
  "Rechnungsempfänger". Do not swap their names or VAT IDs. {buyer_hint}
- invoice_date is the issue date, not the order or delivery date. due_date only if a due
  date is printed; payment terms alone ("Net 30") mean null.
- total is the gross document total, not a "balance due" after payments. Page subtotals
  ("carried forward", "brought forward", "À reporter", "Übertrag") are not line items.
- The text comes from a PDF and may be out of reading order; table cells may sit on
  separate lines. Use labels and layout cues to reassemble it.
"""

REPAIR_INSTRUCTIONS = """\
Your previous extraction of this document failed automated checks. Re-read the document
carefully and return a corrected extraction.
- Fix any field you misread, missed or swapped (check every line item and the totals block).
- If the document itself is inconsistent (its printed numbers or dates do not add up), keep
  the printed values. Do not make them consistent and do not drop printed values: the
  inconsistency must reach a human.
"""


def extraction_rules(buyer: dict | None = None) -> str:
    hint = ""
    if buyer:
        hint = (f"We are the buyer: {buyer['name']} (VAT ID {buyer['vat_id']}); the seller is "
                "the other company.")
    return EXTRACTION_RULES.replace("{buyer_hint}", hint)


@dataclass
class ExtractionResult:
    invoice: InvoiceData
    usage: dict = field(default_factory=dict)


class Extractor(Protocol):
    def extract(self, text: str, previous: InvoiceData | None = None,
                issues: list[Issue] | None = None) -> ExtractionResult: ...


def build_messages(text: str, previous, issues: list[Issue] | None,
                   buyer: dict | None = None, rules: str | None = None):
    messages = [SystemMessage(rules or extraction_rules(buyer)),
                HumanMessage(f"<document>\n{text}\n</document>")]
    if previous is not None:
        problems = "\n".join(f"- [{i.code.value}] {i.message}" for i in issues or [])
        messages.append(HumanMessage(
            f"{REPAIR_INSTRUCTIONS}\nPrevious extraction:\n"
            f"{json.dumps(previous.model_dump(), ensure_ascii=False)}\n\n"
            f"Check failures:\n{problems}"))
    return messages


def usage_of(raw_message, model: str) -> dict:
    meta = getattr(raw_message, "usage_metadata", None) or {}
    return {"model": model, "input_tokens": meta.get("input_tokens", 0),
            "output_tokens": meta.get("output_tokens", 0)}


class LLMExtractor:
    def __init__(self, model: str = "gpt-4.1-mini", buyer: dict | None = None,
                 timeout: float = 90, schema: type = InvoiceData, rules: str | None = None):
        from langchain_openai import ChatOpenAI

        self.model = model
        self.buyer = buyer
        self.rules = rules
        llm = ChatOpenAI(model=model, temperature=0, timeout=timeout, max_retries=3)
        self.runnable = llm.with_structured_output(
            schema, method="json_schema", strict=True, include_raw=True)

    def extract(self, text: str, previous: InvoiceData | None = None,
                issues: list[Issue] | None = None) -> ExtractionResult:
        out = self.runnable.invoke(build_messages(text, previous, issues, self.buyer,
                                                  self.rules))
        if out.get("parsing_error") or out.get("parsed") is None:
            raise ValueError(f"structured output failed: {out.get('parsing_error')}")
        return ExtractionResult(invoice=out["parsed"], usage=usage_of(out["raw"], self.model))
