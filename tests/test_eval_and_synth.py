import json
import random

from invoice_agent.evaluation.metrics import mcnemar_exact, score_extraction, wilson
from invoice_agent.synth.build import build_invoice
from invoice_agent.synth.generate import build_ledger
from invoice_agent.synth.world import build_vendors
from tests.conftest import first


def test_perfect_prediction_scores_all_fields(labels):
    row = first(labels, None)
    assert all(score_extraction(row["truth"], row["truth"]).values())


def test_zero_rate_tax_line_counts_as_no_tax(labels):
    row = next(r for r in labels.values() if not r["truth"]["tax_lines"])
    pred = row["truth"] | {"tax_lines": [{"rate_percent": 0, "base": row["truth"]["subtotal"],
                                          "amount": 0}]}
    assert score_extraction(pred, row["truth"])["tax_lines"]


def test_wrong_amount_is_scored_wrong(labels):
    row = first(labels, None)
    pred = row["truth"] | {"subtotal": row["truth"]["subtotal"] + 0.05}
    assert not score_extraction(pred, row["truth"])["subtotal"]


def test_mcnemar_exact():
    assert mcnemar_exact([True] * 10, [True] * 10)["p_value"] == 1.0
    res = mcnemar_exact([True] * 12, [False] * 12)
    assert res["only_a"] == 12 and res["p_value"] < 0.001


def test_wilson_interval_contains_estimate():
    lo, hi = wilson(45, 50)
    assert lo < 0.9 < hi


def test_generator_is_deterministic():
    vendors = build_vendors(2026)
    ledger = build_ledger(vendors, 2026)
    a = build_invoice(random.Random(5), "x", vendors[0], None, ledger)
    b = build_invoice(random.Random(5), "x", vendors[0], None, ledger)
    assert a.truth == b.truth


def test_committed_dataset_is_balanced():
    from tests.conftest import DATA
    rows = [json.loads(line) for line in open(DATA / "test" / "labels.jsonl")]
    anomalous = [r for r in rows if r["anomaly"]]
    assert len(rows) == 150 and len(anomalous) == 60
    assert len({r["anomaly"] for r in anomalous}) == 8
