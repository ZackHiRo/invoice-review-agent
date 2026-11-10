"""Evaluate on real ConfBench invoices (digital PDFs only; there is no OCR path).

There are no injected anomalies here: these are genuine invoices. So the questions are:
1. How accurate is extraction on real documents, before and after the repair read?
2. When the checks pass, is the extraction actually right (silent errors), and when they
   fire, was something actually wrong (false alarms)?
The "single call" system is the agent's first read with no checks: it auto-approves all.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from rich.progress import Progress

from invoice_agent.checks import is_grounded
from invoice_agent.config import Settings
from invoice_agent.evaluation.metrics import wilson
from invoice_agent.evaluation.runner import RecordingExtractor
from invoice_agent.extract import Extractor
from invoice_agent.graph import Deps, build_graph
from invoice_agent.money import close
from invoice_agent.pdf import pdf_to_text
from invoice_agent.real.media import AMOUNT_FIELDS, MEDIA_INVOICE, MEDIA_RULES, MediaInvoice
from invoice_agent.store import Ledger, VendorDirectory

NAME_FIELDS = ("agency", "advertiser")
FIELDS = (*NAME_FIELDS, *AMOUNT_FIELDS, "payment_terms")
MIN_CHARS_PER_PAGE = 200   # below this the PDF is a scan without a text layer


def is_digital(text: str, pages: int) -> bool:
    return len(text) / max(pages, 1) >= MIN_CHARS_PER_PAGE


def _norm_name(s: str) -> str:
    s = re.sub(r"\([^)]*\)", " ", s.lower())
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def name_match(pred: str | None, truth: str | None) -> bool:
    if not pred or not truth:
        return not pred and not truth
    a, b = _norm_name(pred), _norm_name(truth)
    if a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.85:
        return True
    return min(len(a), len(b)) >= 5 and (a in b or b in a)


def terms_match(pred: str | None, truth: str | None) -> bool:
    if not pred or not truth:
        return not pred and not truth
    a, b = pred.lower(), truth.lower()
    na, nb = re.findall(r"\d+", a), re.findall(r"\d+", b)
    if na or nb:
        return set(na) == set(nb)
    a, b = _norm_name(a), _norm_name(b)
    return a == b or a in b or b in a or difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def amount_match(pred, truth) -> bool:
    if pred is None or truth is None:
        return pred is None and truth is None
    return close(float(pred), float(truth))


def score(pred: dict | None, truth: dict) -> dict[str, bool]:
    if pred is None:
        return {f: False for f in FIELDS}
    out = {f: name_match(pred.get(f), truth.get(f)) for f in NAME_FIELDS}
    out |= {f: amount_match(pred.get(f), truth.get(f)) for f in AMOUNT_FIELDS}
    out["payment_terms"] = terms_match(pred.get("payment_terms"), truth.get("payment_terms"))
    return out


def run_doc(row: dict, root: Path, extractor: Extractor) -> dict:
    text = pdf_to_text(root / row["file"])
    rec = RecordingExtractor(extractor)
    deps = Deps(rec, VendorDirectory([]), Ledger(), None, max_attempts=2,
                profile=MEDIA_INVOICE)
    cfg = {"configurable": {"thread_id": row["doc_id"]}}
    t0 = time.perf_counter()
    try:
        out = build_graph(InMemorySaver()).invoke(
            {"doc": row["file"], "text": text, "thread_id": row["doc_id"], "attempts": 0,
             "status": "processing"}, cfg, context=deps)
    except Exception as exc:  # noqa: BLE001 - scored as a failure
        return {"doc_id": row["doc_id"], "error": repr(exc),
                "usage": [r.usage for r in rec.results]}
    return {
        "doc_id": row["doc_id"],
        "first_read": rec.results[0].invoice.model_dump(),
        "final": out["invoice"],
        "route": "human_review" if out.get("__interrupt__") else "auto_approve",
        "errors": [i["code"] for i in out.get("issues", []) if i["severity"] == "error"],
        "warnings": [i["code"] for i in out.get("issues", []) if i["severity"] == "warning"],
        "attempts": out.get("attempts"),
        "usage": out.get("usage", []),
        "latency_s": time.perf_counter() - t0,
        "trail": out.get("trail", []),
    }


def run_real_eval(settings: Settings, root: Path, out_dir: Path, limit: int | None = None,
                  workers: int = 6) -> dict:
    from invoice_agent.extract import LLMExtractor

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    rows = [json.loads(line) for line in open(root / "labels.jsonl")]
    digital, scanned = [], []
    for r in rows:
        (digital if is_digital(pdf_to_text(root / r["file"]), r["pages"]) else scanned).append(r)
    digital = digital[:limit]
    extractor = LLMExtractor(settings.model, schema=MediaInvoice, rules=MEDIA_RULES)
    started = datetime.now(UTC)
    preds = []
    with Progress() as progress, ThreadPoolExecutor(max_workers=workers) as pool:
        task = progress.add_task(f"ConfBench: {len(digital)} digital invoices",
                                 total=len(digital))
        futures = [pool.submit(run_doc, r, root, extractor) for r in digital]
        for fut in as_completed(futures):
            preds.append(fut.result())
            progress.advance(task)
    labels = {r["doc_id"]: r for r in digital}
    result = summarize(preds, labels, settings, root)
    result |= {"model": settings.model, "n_documents": len(rows),
               "n_digital": len(digital), "n_scanned_excluded": len(scanned),
               "started_at": started.isoformat(timespec="seconds"),
               "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
               "price_per_1m_tokens_usd": {"input": settings.price_in,
                                           "output": settings.price_out}}
    out_dir.mkdir(parents=True, exist_ok=True)
    disagreements = result.pop("disagreements")
    (out_dir / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    with open(out_dir / "predictions.jsonl", "w") as f:
        for p in sorted(preds, key=lambda p: p["doc_id"]):
            f.write(json.dumps(p, ensure_ascii=False, default=str) + "\n")
    (out_dir / "report.md").write_text(render(result, disagreements))
    return result


def summarize(preds: list[dict], labels: dict[str, dict], settings: Settings,
              root: Path) -> dict:
    n = len(preds)
    acc = {"single_call": dict.fromkeys(FIELDS, 0), "agent": dict.fromkeys(FIELDS, 0)}
    amounts_ok = {"single_call": 0, "agent": 0}
    det = {"caught": 0, "false_alarm": 0, "silent_error": 0, "clean_auto": 0}
    errors = tokens_in = tokens_out = 0
    disagreements = []
    for p in preds:
        truth = labels[p["doc_id"]]["truth"]
        for u in p.get("usage", []):
            tokens_in += u.get("input_tokens", 0)
            tokens_out += u.get("output_tokens", 0)
        if p.get("error"):
            errors += 1
        for system, key in (("single_call", "first_read"), ("agent", "final")):
            s = score(p.get(key), truth)
            for f, ok in s.items():
                acc[system][f] += ok
            amounts_ok[system] += all(s[f] for f in AMOUNT_FIELDS)
        s = score(p.get("final"), truth)
        right = all(s[f] for f in AMOUNT_FIELDS)
        flagged = p.get("route") != "auto_approve"   # errors count as sent to a human
        det["caught" if flagged and not right else "false_alarm" if flagged
            else "silent_error" if not right else "clean_auto"] += 1
        if p.get("final") and not right:
            text = pdf_to_text(root / labels[p["doc_id"]]["file"])
            for f in AMOUNT_FIELDS:
                if not s[f]:
                    pv, tv = p["final"].get(f), truth.get(f)
                    disagreements.append({
                        "doc_id": p["doc_id"], "field": f, "predicted": pv, "label": tv,
                        "predicted_in_doc": pv is not None and is_grounded(f, pv, text),
                        "label_in_doc": tv is not None and is_grounded(f, tv, text),
                        "route": p.get("route")})
    wrong = det["caught"] + det["silent_error"]
    single_wrong = n - amounts_ok["single_call"]
    return {
        "n_scored": n,
        "run_errors": errors,
        "field_accuracy": {s: {f: v / n for f, v in a.items()} for s, a in acc.items()},
        "amounts_all_correct": {s: v / n for s, v in amounts_ok.items()},
        "agent_routing": det,
        "review_rate": (det["caught"] + det["false_alarm"]) / n,
        "error_detection_recall": det["caught"] / wrong if wrong else None,
        "error_detection_recall_ci95": wilson(det["caught"], wrong),
        "silent_errors": {"single_call": single_wrong, "agent": det["silent_error"]},
        "repairs": sum(1 for p in preds if (p.get("attempts") or 0) > 1),
        "tokens_in": tokens_in, "tokens_out": tokens_out,
        "cost_usd": settings.cost(tokens_in, tokens_out),
        "disagreements": disagreements,
    }


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def render(r: dict, disagreements: list[dict]) -> str:
    fa, d = r["field_accuracy"], r["agent_routing"]
    lines = [
        f"# Real invoices: ConfBench FCC originals, `{r['model']}`", "",
        f"{r['n_documents']} documents; {r['n_scanned_excluded']} are scans without a text "
        f"layer and are excluded (no OCR path); {r['n_scored']} digital invoices scored. "
        f"Run errors: {r['run_errors']}. Run {r['started_at']} -> {r['finished_at']} (UTC).",
        "", "## Field accuracy against the published labels", "",
        "| Field | Single call (first read, no checks) | Agent (after checks + repair) |",
        "|---|---|---|"]
    for f in FIELDS:
        lines.append(f"| {f} | {_pct(fa['single_call'][f])} | {_pct(fa['agent'][f])} |")
    lines.append(f"| **all three amounts** | {_pct(r['amounts_all_correct']['single_call'])} | "
                 f"{_pct(r['amounts_all_correct']['agent'])} |")
    lo, hi = r["error_detection_recall_ci95"]
    lines += [
        "", "## Do the checks catch the wrong ones?", "",
        "An invoice counts as wrong if any of gross, commission or net disagrees with its "
        "label.", "",
        "| | Amounts right | Amounts wrong |", "|---|---|---|",
        f"| Sent to a human | {d['false_alarm']} (false alarm) | {d['caught']} (caught) |",
        f"| Auto-approved | {d['clean_auto']} | **{d['silent_error']} (silent error)** |", "",
        f"- Review rate: {_pct(r['review_rate'])}. Repairs: {r['repairs']}.",
        f"- Wrong invoices caught: {_pct(r['error_detection_recall'])} "
        f"[{100 * lo:.1f}, {100 * hi:.1f}].",
        f"- Silent errors (wrong amounts, auto-approved): single call "
        f"{r['silent_errors']['single_call']}, agent {r['silent_errors']['agent']}.",
        f"- Tokens in/out {r['tokens_in']:,} / {r['tokens_out']:,}; cost "
        f"${r['cost_usd']:.3f}.", "",
        "## Every amount disagreement with the labels", "",
        "`in doc` = the value appears literally in the document text.", "",
        "| Document | Field | Agent | Label | Agent in doc | Label in doc | Route |",
        "|---|---|---|---|---|---|---|"]
    for x in sorted(disagreements, key=lambda x: (x["doc_id"], x["field"])):
        lines.append(f"| `{x['doc_id'][:10]}` | {x['field']} | {x['predicted']} | {x['label']} "
                     f"| {'yes' if x['predicted_in_doc'] else 'no'} | "
                     f"{'yes' if x['label_in_doc'] else 'no'} | {x['route']} |")
    lines.append("")
    return "\n".join(lines)
