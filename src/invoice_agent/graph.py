"""The LangGraph workflow.

    load -> extract -> validate --(misread? one more targeted read)--> extract
                          |
                          +--(clean)--> post
                          +--(problems)--> human_review --approve--> post
                                               |-------reject---> reject
                                               +-------correct--> validate

Dependencies (extractor, vendor master, ledger) are injected per run through the
LangGraph runtime context, so tests run the real graph with a fake extractor.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, RetryPolicy, interrupt

from invoice_agent.extract import Extractor
from invoice_agent.pdf import pdf_to_text
from invoice_agent.profiles import AP_INVOICE, Profile
from invoice_agent.schemas import Issue, IssueKind, ReviewDecision, Severity
from invoice_agent.store import Ledger, VendorDirectory

REPAIRABLE = {IssueKind.EXTRACTION, IssueKind.CONSISTENCY}


class InvoiceState(TypedDict, total=False):
    thread_id: str
    doc: str                 # source path or label
    text: str
    invoice: dict | None     # InvoiceData.model_dump()
    issues: list[dict]       # Issue.model_dump() from the latest validation
    vendor_id: str | None
    attempts: int
    human_corrected: bool
    route: str
    status: str              # processing | posted | rejected
    decision: dict | None    # ReviewDecision.model_dump()
    trail: Annotated[list[dict], operator.add]   # audit log, append-only
    usage: Annotated[list[dict], operator.add]   # one entry per LLM call


@dataclass
class Deps:
    extractor: Extractor
    vendors: VendorDirectory
    ledger: Ledger
    buyer_vat_id: str | None = None
    max_attempts: int = 2   # 1 = no repair loop (used as an ablation in the eval)
    extras: dict[str, Any] = field(default_factory=dict)
    profile: Profile = AP_INVOICE   # what to extract and how to check it


def _issues(state: InvoiceState) -> list[Issue]:
    return [Issue(**i) for i in state.get("issues", [])]


# ---------- nodes --------------------------------------------------------------------------

def load(state: InvoiceState) -> dict:
    if state.get("text"):
        return {"trail": [{"node": "load", "chars": len(state["text"])}]}
    text = pdf_to_text(state["doc"])
    return {"text": text, "attempts": 0, "status": "processing",
            "trail": [{"node": "load", "chars": len(text)}]}


def extract(state: InvoiceState, runtime: Runtime[Deps]) -> dict:
    profile = runtime.context.profile
    attempt = state.get("attempts", 0) + 1
    previous = profile.schema(**state["invoice"]) if state.get("invoice") else None
    res = runtime.context.extractor.extract(
        state["text"], previous=previous, issues=_issues(state) if previous else None)
    invoice, extras = profile.postprocess(res.invoice, previous, state["text"])
    event = {"node": "extract", "attempt": attempt,
             "mode": "repair" if previous else "first_read"} | extras
    return {
        "invoice": invoice.model_dump(),
        "attempts": attempt,
        "usage": [res.usage | {"node": "extract", "attempt": attempt}],
        "trail": [event],
    }


def validate(state: InvoiceState, runtime: Runtime[Deps]) -> dict:
    deps = runtime.context
    profile = deps.profile
    result = profile.check(profile.schema(**state["invoice"]), state["text"], deps,
                           state.get("thread_id"))
    errors = result.errors
    repairable = [i for i in errors if i.kind in REPAIRABLE]
    if repairable and state.get("attempts", 0) < deps.max_attempts \
            and not state.get("human_corrected"):
        route = "repair"
    elif errors:
        route = "human_review"
    else:
        route = "auto_approve"
    return {
        "issues": [i.model_dump(mode="json") for i in result.issues],
        "vendor_id": result.vendor_id,
        "route": route,
        "trail": [{"node": "validate", "route": route,
                   "errors": [i.code.value for i in errors],
                   "warnings": [i.code.value for i in result.issues
                                if i.severity == Severity.WARNING]}],
    }


def route_after_validate(state: InvoiceState) -> Literal["extract", "post", "human_review"]:
    return {"repair": "extract", "auto_approve": "post"}.get(state["route"], "human_review")


def human_review(state: InvoiceState, runtime: Runtime[Deps]
                 ) -> Command[Literal["post", "reject", "validate"]]:
    errors = [i for i in _issues(state) if i.severity == Severity.ERROR]
    # Runs again on resume, so it must be idempotent (it is an upsert).
    runtime.context.ledger.enqueue(state.get("thread_id", ""), state.get("doc", ""),
                                   [i.message for i in errors])
    raw = interrupt({
        "thread_id": state.get("thread_id"),
        "doc": state.get("doc"),
        "vendor_id": state.get("vendor_id"),
        "invoice": state["invoice"],
        "issues": [i.model_dump(mode="json") for i in errors],
        "question": "Approve, reject or correct this invoice?",
    }, response_schema=ReviewDecision)  # resume value is validated into a ReviewDecision
    decision = raw if isinstance(raw, ReviewDecision) else ReviewDecision.model_validate(raw)
    event = {"node": "human_review", "action": decision.action, "reviewer": decision.reviewer,
             "note": decision.note}
    if decision.action == "approve":
        return Command(goto="post", update={"decision": decision.model_dump(),
                                            "trail": [event]})
    if decision.action == "reject":
        return Command(goto="reject", update={"decision": decision.model_dump(),
                                              "trail": [event]})
    profile = runtime.context.profile
    invoice = dict(state["invoice"])
    for key, value in decision.corrections.items():
        if key not in profile.schema.model_fields or key in profile.locked_fields:
            raise ValueError(f"cannot correct field {key!r}")
        numeric = key in profile.numeric_fields and value not in (None, "")
        invoice[key] = float(value) if numeric else value
    profile.schema(**invoice)  # validate the corrected record
    return Command(goto="validate", update={
        "invoice": invoice, "human_corrected": True, "decision": decision.model_dump(),
        "trail": [event | {"corrections": decision.corrections}]})


def post(state: InvoiceState, runtime: Runtime[Deps]) -> dict:
    by = (state.get("decision") or {}).get("reviewer") or "auto"
    runtime.context.ledger.record(state.get("thread_id", ""), state.get("vendor_id"),
                                  state["invoice"], "posted", by)
    if state.get("decision"):
        runtime.context.ledger.resolve(state.get("thread_id", ""), "approved")
    return {"status": "posted", "trail": [{"node": "post", "by": by}]}


def reject(state: InvoiceState, runtime: Runtime[Deps]) -> dict:
    by = (state.get("decision") or {}).get("reviewer") or "reviewer"
    runtime.context.ledger.record(state.get("thread_id", ""), state.get("vendor_id"),
                                  state["invoice"], "rejected", by)
    runtime.context.ledger.resolve(state.get("thread_id", ""), "rejected")
    return {"status": "rejected", "trail": [{"node": "reject", "by": by}]}


def _transient(exc: Exception) -> bool:
    name = type(exc).__name__
    return name in {"APIConnectionError", "APITimeoutError", "RateLimitError",
                    "InternalServerError", "ReadTimeout"}


def build_graph(checkpointer=None):
    g = StateGraph(InvoiceState, context_schema=Deps)
    g.add_node("load", load)
    g.add_node("extract", extract,
               retry_policy=RetryPolicy(max_attempts=3, initial_interval=2.0,
                                        retry_on=_transient))
    g.add_node("validate", validate)
    g.add_node("human_review", human_review)
    g.add_node("post", post)
    g.add_node("reject", reject)
    g.add_edge(START, "load")
    g.add_edge("load", "extract")
    g.add_edge("extract", "validate")
    g.add_conditional_edges("validate", route_after_validate,
                            ["extract", "post", "human_review"])
    g.add_edge("post", END)
    g.add_edge("reject", END)
    return g.compile(checkpointer=checkpointer)
