"""Document profiles: what the graph extracts and how it checks it.

The graph (load -> extract -> validate -> repair / human review -> post) is the same for
every document type. A profile supplies the schema the LLM fills, the deterministic checks,
and any post-processing of the raw extraction. The default profile is the EU/UK/US
accounts-payable invoice the synthetic eval was built for; `real/media.py` adds one for
US broadcast-advertising invoices.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from invoice_agent.checks import CheckResult, run_checks
from invoice_agent.schemas import InvoiceData
from invoice_agent.snap import keep_dropped_values, snap_iban

CheckFn = Callable[[Any, str, Any, "str | None"], CheckResult]
PostFn = Callable[[Any, Any, str], "tuple[Any, dict]"]


@dataclass(frozen=True)
class Profile:
    name: str
    schema: type[BaseModel]
    check: CheckFn                       # (record, text, deps, thread_id) -> CheckResult
    postprocess: PostFn                  # (record, previous, text) -> (record, trail extras)
    numeric_fields: frozenset[str] = field(default_factory=frozenset)
    locked_fields: frozenset[str] = field(default_factory=frozenset)  # not human-correctable


def _ap_check(inv: InvoiceData, text: str, deps, thread_id: str | None) -> CheckResult:
    return run_checks(inv, text, deps.vendors, deps.ledger, deps.buyer_vat_id, thread_id)


def _ap_post(inv: InvoiceData, previous: InvoiceData | None, text: str):
    extras: dict = {}
    inv, snapped = snap_iban(inv, text)
    inv, restored = keep_dropped_values(inv, previous, text)
    if snapped:
        extras["snapped"] = snapped
    if restored:
        extras["restored_after_repair"] = restored
    return inv, extras


AP_INVOICE = Profile(
    name="ap_invoice",
    schema=InvoiceData,
    check=_ap_check,
    postprocess=_ap_post,
    numeric_fields=frozenset({"subtotal", "tax_total", "total"}),
    locked_fields=frozenset({"line_items", "tax_lines"}),
)
