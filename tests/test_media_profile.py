"""Media-invoice profile: same graph, different schema and checks. Uses an invented
invoice text (the real ConfBench PDFs are downloaded, not committed)."""

from langgraph.checkpoint.memory import InMemorySaver

from invoice_agent.extract import ExtractionResult
from invoice_agent.graph import Deps, build_graph
from invoice_agent.real.evaluate import name_match, score, summarize, terms_match
from invoice_agent.real.media import MEDIA_INVOICE, MediaInvoice, media_checks
from invoice_agent.schemas import IssueCode
from invoice_agent.store import Ledger, VendorDirectory

TEXT = """WXYZ-TV  INVOICE  Invoice 445566  Invoice Date 10/31/2016
AGENCY  Example Media Buying LLC   ADVERTISER  Friends of Example (88231)
PAYMENT TERMS  NET 30
AIRTIME TOTAL : $ 2,000.00
Agency Commission : $ 300.00
Rep Commission : $ 100.00
NET TOTAL : $ 1,600.00"""

GOOD = dict(station="WXYZ-TV", invoice_number="445566", invoice_date="2016-10-31",
            agency="Example Media Buying LLC", advertiser="Friends of Example",
            gross_total=2000.0, agency_commission=300.0,
            other_deductions=[{"label": "Rep Commission", "amount": 100.0}],
            net_amount_due=1600.0, payment_terms="NET 30")


class Scripted:
    def __init__(self, *records):
        self.records, self.calls = list(records), 0

    def extract(self, text, previous=None, issues=None):
        self.calls += 1
        rec = self.records[min(self.calls - 1, len(self.records) - 1)]
        return ExtractionResult(MediaInvoice(**rec),
                                {"model": "fake", "input_tokens": 1, "output_tokens": 1})


def _codes(rec):
    return {i.code for i in media_checks(MediaInvoice(**rec), TEXT).errors}


def test_clean_media_invoice_has_no_errors():
    assert _codes(GOOD) == set()


def test_missing_rep_commission_breaks_the_arithmetic():
    assert IssueCode.TOTAL_MISMATCH in _codes(GOOD | {"other_deductions": []})


def test_invented_amount_is_ungrounded():
    assert IssueCode.UNGROUNDED_VALUE in _codes(GOOD | {"gross_total": 2100.0})


def test_unusual_commission_is_only_a_warning():
    rec = GOOD | {"agency_commission": 400.0, "other_deductions": []}
    res = media_checks(MediaInvoice(**rec), TEXT.replace("300.00", "400.00"))
    assert IssueCode.UNUSUAL_COMMISSION in {i.code for i in res.issues}
    assert IssueCode.UNUSUAL_COMMISSION not in {i.code for i in res.errors}


def test_same_graph_runs_the_media_profile_with_repair():
    missed = GOOD | {"other_deductions": []}
    extractor = Scripted(missed, GOOD)
    deps = Deps(extractor, VendorDirectory([]), Ledger(), None, profile=MEDIA_INVOICE)
    cfg = {"configurable": {"thread_id": "m1"}}
    out = build_graph(InMemorySaver()).invoke(
        {"doc": "x.pdf", "text": TEXT, "thread_id": "m1", "attempts": 0}, cfg, context=deps)
    routes = [t["route"] for t in out["trail"] if t["node"] == "validate"]
    assert routes == ["repair", "auto_approve"] and out["status"] == "posted"
    assert extractor.calls == 2


def test_label_matching_is_lenient_on_format_only():
    assert name_match("Ackerman McQueen (2852)", "Ackerman McQueen")
    assert not name_match("OnMedia", "Ackerman McQueen")
    assert terms_match("Net 30", "30 Days")
    assert terms_match("CASH IN ADVANCE", "Cash In Advance")
    assert not terms_match("Net 30", "Net 45")


def test_detection_counts():
    truth = {k: GOOD[k] for k in ("agency", "advertiser", "gross_total", "agency_commission",
                                  "net_amount_due", "payment_terms")}
    labels = {"a": {"truth": truth, "file": "a.pdf"}, "b": {"truth": truth, "file": "b.pdf"}}
    assert all(score(GOOD, truth).values())

    class S:
        def cost(self, i, o):
            return 0.0

    preds = [{"doc_id": "a", "first_read": GOOD, "final": GOOD, "route": "auto_approve"},
             {"doc_id": "b", "first_read": GOOD, "final": GOOD, "route": "human_review"}]
    r = summarize(preds, labels, S(), root=None)
    assert r["agent_routing"] == {"caught": 0, "false_alarm": 1, "silent_error": 0,
                                  "clean_auto": 1}
    assert r["silent_errors"] == {"single_call": 0, "agent": 0}
