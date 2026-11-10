"""The real LangGraph graph with a scripted extractor: routing, repair, human-in-the-loop,
idempotent posting, and durable resume from a SQLite checkpoint in a new process."""

import sqlite3

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import ValidationError

from invoice_agent.graph import Deps, build_graph
from tests.conftest import DATA, ScriptedExtractor, first


def _run(graph, row, deps, thread="t1"):
    cfg = {"configurable": {"thread_id": thread}}
    out = graph.invoke({"doc": str(DATA / row["file"]), "thread_id": thread}, cfg, context=deps)
    return out, cfg


def _routes(out):
    return [t["route"] for t in out["trail"] if t["node"] == "validate"]


def test_clean_invoice_is_auto_posted(labels, vendors, ledger, buyer_vat):
    row = first(labels, None)
    deps = Deps(ScriptedExtractor(row["truth"]), vendors, ledger, buyer_vat)
    out, _ = _run(build_graph(InMemorySaver()), row, deps)
    assert out["status"] == "posted" and "__interrupt__" not in out
    assert _routes(out) == ["auto_approve"]
    assert [i["status"] for i in ledger.invoices()] == ["posted"]


def test_misread_is_repaired_on_second_read(labels, vendors, ledger, buyer_vat):
    row = first(labels, None)
    misread = row["truth"] | {"total": row["truth"]["total"] + 100}
    extractor = ScriptedExtractor(misread, row["truth"])
    out, _ = _run(build_graph(InMemorySaver()), row, Deps(extractor, vendors, ledger, buyer_vat))
    assert _routes(out) == ["repair", "auto_approve"]
    assert out["attempts"] == 2 and out["status"] == "posted"
    # the repair call saw the previous extraction and the failed checks
    assert extractor.calls[1]["previous"] is not None
    assert {i.code.value for i in extractor.calls[1]["issues"]} >= {"total_mismatch"}


def test_no_repair_ablation_goes_straight_to_review(labels, vendors, ledger, buyer_vat):
    row = first(labels, None)
    misread = row["truth"] | {"total": row["truth"]["total"] + 100}
    deps = Deps(ScriptedExtractor(misread, row["truth"]), vendors, ledger, buyer_vat,
                max_attempts=1)
    out, _ = _run(build_graph(InMemorySaver()), row, deps)
    assert "__interrupt__" in out and _routes(out) == ["human_review"]


def test_anomaly_pauses_then_human_approves(labels, vendors, ledger, buyer_vat):
    row = first(labels, "bank_account_changed")
    deps = Deps(ScriptedExtractor(row["truth"]), vendors, ledger, buyer_vat)
    graph = build_graph(InMemorySaver())
    out, cfg = _run(graph, row, deps)
    payload = out["__interrupt__"][0].value
    assert [i["code"] for i in payload["issues"]] == ["bank_account_changed"]
    assert len(ledger.pending()) == 1
    out = graph.invoke(Command(resume={"action": "approve", "reviewer": "ana",
                                       "note": "confirmed new IBAN by phone"}), cfg, context=deps)
    assert out["status"] == "posted"
    assert ledger.pending() == []
    assert ledger.invoices()[0]["decided_by"] == "ana"


def test_human_reject(labels, vendors, ledger, buyer_vat):
    row = first(labels, "duplicate_invoice")
    deps = Deps(ScriptedExtractor(row["truth"]), vendors, ledger, buyer_vat)
    graph = build_graph(InMemorySaver())
    _, cfg = _run(graph, row, deps)
    out = graph.invoke(Command(resume={"action": "reject"}), cfg, context=deps)
    assert out["status"] == "rejected"
    assert ledger.invoices()[0]["status"] == "rejected"


def test_human_correction_is_revalidated(labels, vendors, ledger, buyer_vat):
    row = first(labels, None)
    wrong = row["truth"] | {"total": row["truth"]["total"] + 100}
    deps = Deps(ScriptedExtractor(wrong), vendors, ledger, buyer_vat)  # repair repeats mistake
    graph = build_graph(InMemorySaver())
    out, cfg = _run(graph, row, deps)
    assert "__interrupt__" in out
    out = graph.invoke(Command(resume={"action": "correct",
                                       "corrections": {"total": row["truth"]["total"]}}),
                       cfg, context=deps)
    assert out["status"] == "posted"
    assert _routes(out)[-1] == "auto_approve"


def test_invalid_resume_is_rejected_by_response_schema(labels, vendors, ledger, buyer_vat):
    row = first(labels, "invalid_iban")
    deps = Deps(ScriptedExtractor(row["truth"]), vendors, ledger, buyer_vat)
    graph = build_graph(InMemorySaver())
    _, cfg = _run(graph, row, deps)
    with pytest.raises(ValidationError):
        graph.invoke(Command(resume={"action": "pay it anyway"}), cfg, context=deps)


def test_paused_run_survives_a_restart(tmp_path, labels, vendors, ledger, buyer_vat):
    """Pause in one 'process', resume from the SQLite checkpoint in a fresh graph."""
    row = first(labels, "unknown_vendor")
    deps = Deps(ScriptedExtractor(row["truth"]), vendors, ledger, buyer_vat)
    db = tmp_path / "checkpoints.sqlite"
    graph1 = build_graph(SqliteSaver(sqlite3.connect(db, check_same_thread=False)))
    _, cfg = _run(graph1, row, deps, thread="durable-1")
    del graph1
    graph2 = build_graph(SqliteSaver(sqlite3.connect(db, check_same_thread=False)))
    state = graph2.get_state(cfg)
    assert state.next == ("human_review",)
    out = graph2.invoke(Command(resume={"action": "approve"}), cfg, context=deps)
    assert out["status"] == "posted"


def test_posting_is_idempotent(labels, vendors, ledger, buyer_vat):
    row = first(labels, None)
    graph = build_graph(InMemorySaver())
    for _ in range(2):
        deps = Deps(ScriptedExtractor(row["truth"]), vendors, ledger, buyer_vat)
        _run(graph, row, deps, thread="same-thread")
    assert len(ledger.invoices()) == 1
