"""Headless smoke test of the Streamlit review console: a paused invoice shows up in the
queue with its reasons, and clicking Approve resumes the graph and posts it."""

import json
import sqlite3

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("pandas")  # imported up front: the first import is slow inside AppTest
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from invoice_agent.graph import Deps, build_graph  # noqa: E402
from invoice_agent.store import Ledger, VendorDirectory  # noqa: E402
from tests.conftest import DATA, ScriptedExtractor, first  # noqa: E402

APP = str(DATA.parent / "src" / "invoice_agent" / "ui.py")


def test_queue_approve_flow(tmp_path, monkeypatch, labels):
    monkeypatch.setenv("INVOICE_AGENT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("INVOICE_AGENT_DATA_DIR", str(DATA))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr("invoice_agent.config.load_dotenv", lambda *a, **k: False)

    # Pause one invoice in the same state dir the UI will open.
    row = first(labels, "bank_account_changed")
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.seed(json.loads((DATA / "ledger_seed.json").read_text()))
    deps = Deps(ScriptedExtractor(row["truth"]), VendorDirectory.from_json(DATA / "vendors.json"),
                ledger, "NL859273641B01")
    graph = build_graph(SqliteSaver(sqlite3.connect(tmp_path / "checkpoints.sqlite",
                                                    check_same_thread=False)))
    cfg = {"configurable": {"thread_id": "ui-1"}}
    graph.invoke({"doc": str(DATA / row["file"]), "thread_id": "ui-1"}, cfg, context=deps)

    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    page = " ".join(m.value for m in at.markdown)
    assert "bank_account_changed" in page
    next(b for b in at.button if b.label == "Approve and post").click().run()
    assert not at.exception
    assert Ledger(tmp_path / "ledger.sqlite").pending() == []
    assert graph.get_state(cfg).values["status"] == "posted"
