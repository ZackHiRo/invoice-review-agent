"""Scoring: field accuracy, routing, anomaly attribution, cost, and paired significance."""

from __future__ import annotations

import difflib
import math
import statistics
from collections import defaultdict

from invoice_agent.money import close, normalize_id
from invoice_agent.schemas import ANOMALY_CODES

HEADER_FIELDS = ["document_type", "invoice_number", "invoice_date", "due_date", "currency",
                 "seller_name", "seller_vat_id", "buyer_name", "po_number", "iban",
                 "subtotal", "tax_total", "total"]
AMOUNT_FIELDS = {"subtotal", "tax_total", "total"}
ID_FIELDS = {"invoice_number", "seller_vat_id", "iban", "po_number"}
NAME_FIELDS = {"seller_name", "buyer_name"}


def _text_eq(a, b) -> bool:
    a, b = (a or "").strip().casefold(), (b or "").strip().casefold()
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.95


def field_correct(name: str, pred, truth) -> bool:
    if name in AMOUNT_FIELDS:
        return pred is not None and close(float(pred), float(truth))
    if name in ID_FIELDS:
        return normalize_id(pred) == normalize_id(truth)
    if name in NAME_FIELDS:
        return _text_eq(pred, truth)
    return (pred or None) == (truth or None)


def lines_correct(pred: list[dict], truth: list[dict]) -> bool:
    if len(pred) != len(truth):
        return False
    for p, t in zip(pred, truth):
        if not (close(p["amount"], t["amount"]) and close(p["unit_price"], t["unit_price"])
                and abs(p["quantity"] - t["quantity"]) < 1e-6
                and _text_eq(p["description"], t["description"])):
            return False
    return True


def tax_lines_correct(pred: list[dict], truth: list[dict]) -> bool:
    # "Sales tax 0.00" extracted as one 0% line is equivalent to "no tax lines".
    pred = [t for t in pred if not (abs(t["rate_percent"]) < 1e-9 and abs(t["amount"]) < 0.005)]
    key = lambda t: t["rate_percent"]  # noqa: E731
    pred, truth = sorted(pred, key=key), sorted(truth, key=key)
    return len(pred) == len(truth) and all(
        close(p["rate_percent"], t["rate_percent"]) and close(p["base"], t["base"])
        and close(p["amount"], t["amount"]) for p, t in zip(pred, truth))


def score_extraction(pred: dict | None, truth: dict) -> dict[str, bool]:
    if pred is None:
        return {f: False for f in [*HEADER_FIELDS, "line_items", "tax_lines"]}
    out = {f: field_correct(f, pred.get(f), truth.get(f)) for f in HEADER_FIELDS}
    out["line_items"] = lines_correct(pred.get("line_items", []), truth["line_items"])
    out["tax_lines"] = tax_lines_correct(pred.get("tax_lines", []), truth["tax_lines"])
    return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar_exact(a: list[bool], b: list[bool]) -> dict:
    """Paired exact McNemar test on per-document correctness of two systems."""
    only_a = sum(1 for x, y in zip(a, b) if x and not y)
    only_b = sum(1 for x, y in zip(a, b) if y and not x)
    n = only_a + only_b
    if n == 0:
        return {"only_a": 0, "only_b": 0, "p_value": 1.0}
    k = min(only_a, only_b)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return {"only_a": only_a, "only_b": only_b, "p_value": min(1.0, 2 * p)}


def summarize(system: str, preds: list[dict], labels: dict[str, dict], price) -> dict:
    """`preds`: one dict per document with invoice, route, flags, usage, latency_s, error."""
    n = len(preds)
    field_hits: dict[str, int] = defaultdict(int)
    doc_exact, route_ok, per_doc_route, per_doc_exact = 0, 0, {}, {}
    tp = fn = fp = tn = 0
    attributed: dict[str, list[int]] = {c.value: [0, 0] for c in ANOMALY_CODES}
    errors = 0
    tokens_in = tokens_out = 0
    latencies = []
    for p in preds:
        lab = labels[p["doc_id"]]
        if p.get("error"):
            errors += 1
        scores = score_extraction(p.get("invoice"), lab["truth"])
        for f, ok in scores.items():
            field_hits[f] += ok
        exact = all(scores.values())
        doc_exact += exact
        per_doc_exact[p["doc_id"]] = exact
        expected = lab["expected_route"]
        got = p.get("route") or "error"
        # A failed run is never an auto-approval: count it as sent to a human.
        if got == "error":
            got = "human_review"
        ok = got == expected
        route_ok += ok
        per_doc_route[p["doc_id"]] = ok
        if expected == "human_review":
            tp += got == "human_review"
            fn += got != "human_review"
            code = lab["anomaly"]
            attributed[code][1] += 1
            attributed[code][0] += (got == "human_review" and code in (p.get("flags") or []))
        else:
            fp += got == "human_review"
            tn += got != "human_review"
        for u in p.get("usage", []):
            tokens_in += u.get("input_tokens", 0)
            tokens_out += u.get("output_tokens", 0)
        if p.get("latency_s") is not None:
            latencies.append(p["latency_s"])
    cost = price(tokens_in, tokens_out)
    anomalous = tp + fn
    clean = fp + tn
    return {
        "system": system,
        "n_docs": n,
        "run_errors": errors,
        "field_accuracy": {f: field_hits[f] / n for f in field_hits},
        "doc_exact_match": doc_exact / n,
        "doc_exact_match_ci95": wilson(doc_exact, n),
        "routing_accuracy": route_ok / n,
        "routing_accuracy_ci95": wilson(route_ok, n),
        "anomaly_recall": tp / anomalous if anomalous else None,
        "anomaly_recall_ci95": wilson(tp, anomalous),
        "false_review_rate": fp / clean if clean else None,
        "false_review_rate_ci95": wilson(fp, clean),
        "precision_review": tp / (tp + fp) if tp + fp else None,
        "auto_approved_clean": tn,
        "confusion": {"tp": tp, "fn": fn, "fp": fp, "tn": tn},
        "anomaly_caught_with_right_reason": {k: {"hit": v[0], "n": v[1]}
                                             for k, v in attributed.items()},
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost,
        "cost_per_doc_usd": cost / n if n else 0,
        "llm_calls_per_doc": sum(len(p.get("usage", [])) for p in preds) / n if n else 0,
        "latency_mean_s": statistics.mean(latencies) if latencies else None,
        "latency_p95_s": (sorted(latencies)[max(0, math.ceil(0.95 * len(latencies)) - 1)]
                          if latencies else None),
        "_per_doc_route": per_doc_route,
        "_per_doc_exact": per_doc_exact,
    }
