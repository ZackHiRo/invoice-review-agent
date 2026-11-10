"""Streamlit review console: process invoices and work the human review queue.

    streamlit run src/invoice_agent/ui.py
"""

from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path

import streamlit as st
from langgraph.types import Command

from invoice_agent.cli import _runtime, thread_id_for
from invoice_agent.config import load_settings

st.set_page_config(page_title="Invoice review agent", layout="wide")


@st.cache_resource
def runtime():
    settings = load_settings()
    return _runtime(settings, need_llm=bool(os.getenv("OPENAI_API_KEY"))), settings


(graph, deps), settings = runtime()


def cfg(tid: str) -> dict:
    return {"configurable": {"thread_id": tid}}


def badge(issue: dict) -> str:
    icon = "🔴" if issue["severity"] == "error" else "🟡"
    return f"{icon} **{issue['code']}** · _{issue['kind']}_ · {issue['message']}"


st.title("Invoice review agent")
st.caption("LangGraph pipeline: extract → deterministic checks → one repair read → "
           "human review when something is wrong. All companies in the demo data are fictional.")

with st.sidebar:
    st.header("Process an invoice")
    if deps.extractor is None:
        st.warning("Set OPENAI_API_KEY to process new invoices. The queue still works.")
    samples = sorted((settings.data_dir / "test").glob("*.pdf"))[:40]
    choice = st.selectbox("Sample from the test set", ["-"] + [p.name for p in samples])
    upload = st.file_uploader("…or upload a PDF", type=["pdf"])
    if st.button("Run", type="primary", disabled=deps.extractor is None):
        if upload is not None:
            path = Path(tempfile.gettempdir()) / upload.name
            path.write_bytes(upload.getvalue())
        elif choice != "-":
            path = settings.data_dir / "test" / choice
        else:
            path = None
        if path:
            tid = thread_id_for(path)
            if not graph.get_state(cfg(tid)).values:
                with st.spinner("Extracting and checking…"):
                    graph.invoke({"doc": str(path), "thread_id": tid}, cfg(tid), context=deps)
            st.session_state["selected"] = tid

queue_tab, ledger_tab = st.tabs(["Review queue", "Ledger"])

with queue_tab:
    pending = deps.ledger.pending()
    ids = [p["thread_id"] for p in pending]
    selected = st.session_state.get("selected")
    if selected and selected not in ids:
        snap = graph.get_state(cfg(selected))
        if snap.values:
            status = snap.values.get("status")
            st.success(f"Last run: **{snap.values.get('doc')}** → {str(status).upper()} "
                       "(no human needed)" if status == "posted" else
                       f"Last run: {snap.values.get('doc')} → {status}")
    if not pending:
        st.info("Nothing waiting for review.")
    else:
        labels = {p["thread_id"]: f"{Path(p['doc']).name} · {len(p['reasons'])} problem(s)"
                  for p in pending}
        tid = st.radio("Waiting for a human", ids, format_func=labels.get,
                       index=ids.index(selected) if selected in ids else 0)
        state = graph.get_state(cfg(tid)).values
        inv = state["invoice"]
        left, right = st.columns([3, 2])
        with left:
            st.subheader(f"{inv['seller_name']} · {inv['invoice_number']}")
            for issue in [i for i in state["issues"] if i["severity"] == "error"]:
                st.markdown(badge(issue))
            header = {k: inv[k] for k in ("invoice_date", "due_date", "currency", "seller_vat_id",
                                          "iban", "subtotal", "tax_total", "total")}
            st.table({"field": list(header), "value": [str(v) for v in header.values()]})
            st.dataframe(inv["line_items"], use_container_width=True)
            with st.expander("Audit trail"):
                st.json(state.get("trail", []))
        with right:
            pdf = Path(state["doc"])
            if pdf.exists():
                b64 = base64.b64encode(pdf.read_bytes()).decode()
                st.markdown(f'<iframe src="data:application/pdf;base64,{b64}" width="100%" '
                            'height="620"></iframe>', unsafe_allow_html=True)
            else:
                st.code(state.get("text", "")[:4000])
        st.divider()
        note = st.text_input("Note", key=f"note-{tid}")
        reviewer = st.text_input("Reviewer", value="reviewer", key=f"rev-{tid}")
        c1, c2, c3 = st.columns(3)
        decision = None
        if c1.button("Approve and post", type="primary"):
            decision = {"action": "approve"}
        if c2.button("Reject"):
            decision = {"action": "reject"}
        with c3.popover("Correct a field"):
            field = st.selectbox("Field", ["total", "subtotal", "tax_total", "invoice_date",
                                           "due_date", "invoice_number", "iban",
                                           "seller_vat_id"])
            value = st.text_input("Correct value")
            if st.button("Apply and re-check"):
                decision = {"action": "correct", "corrections": {field: value}}
        if decision:
            graph.invoke(Command(resume=decision | {"note": note, "reviewer": reviewer}),
                         cfg(tid), context=deps)
            st.session_state["selected"] = tid
            st.rerun()

with ledger_tab:
    rows = deps.ledger.invoices()
    st.dataframe(rows or [{"info": "nothing posted yet"}], use_container_width=True)
