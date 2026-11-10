"""Run the baseline and the agent over a labelled split and write a report.

The "agent without repair" ablation re-scores the agent's FIRST extraction with the same
checks (max one read). That is exactly what the graph does with max_attempts=1, and it
pairs perfectly with the full agent because both start from the same LLM output.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from rich.progress import Progress

from invoice_agent.baseline import BaselineReviewer
from invoice_agent.checks import run_checks
from invoice_agent.config import Settings
from invoice_agent.evaluation.metrics import HEADER_FIELDS, mcnemar_exact, summarize
from invoice_agent.extract import ExtractionResult, Extractor, LLMExtractor
from invoice_agent.graph import Deps, build_graph
from invoice_agent.pdf import pdf_to_text
from invoice_agent.schemas import Severity
from invoice_agent.snap import snap_iban
from invoice_agent.store import Ledger, VendorDirectory
from invoice_agent.synth.world import BUYER

SYSTEMS = ("baseline", "agent", "agent_no_repair")


class RecordingExtractor:
    def __init__(self, inner: Extractor):
        self.inner = inner
        self.results: list[ExtractionResult] = []

    def extract(self, text, previous=None, issues=None):
        res = self.inner.extract(text, previous=previous, issues=issues)
        self.results.append(res)
        return res


def load_split(data_dir: Path, split: str) -> list[dict]:
    with open(data_dir / split / "labels.jsonl") as f:
        return [json.loads(line) for line in f]


def _fresh_ledger(seed_rows: list[dict]) -> Ledger:
    ledger = Ledger()
    ledger.seed(seed_rows)
    return ledger


def run_agent_doc(row: dict, data_dir: Path, extractor: Extractor, vendors: VendorDirectory,
                  seed_rows: list[dict], buyer_vat_id: str) -> list[dict]:
    text = pdf_to_text(data_dir / row["file"])
    rec = RecordingExtractor(extractor)
    graph = build_graph(InMemorySaver())
    deps = Deps(rec, vendors, _fresh_ledger(seed_rows), buyer_vat_id, max_attempts=2)
    cfg = {"configurable": {"thread_id": row["doc_id"]}}
    t0 = time.perf_counter()
    try:
        out = graph.invoke({"doc": row["file"], "text": text, "thread_id": row["doc_id"],
                            "attempts": 0, "status": "processing"}, cfg, context=deps)
    except Exception as exc:  # noqa: BLE001 - recorded and scored as a failure
        err = {"doc_id": row["doc_id"], "invoice": None, "route": "error", "flags": [],
               "usage": [r.usage for r in rec.results], "error": repr(exc)}
        return [err | {"system": "agent"}, err | {"system": "agent_no_repair"}]
    latency = time.perf_counter() - t0
    route = "human_review" if out.get("__interrupt__") else "auto_approve"
    flags = [i["code"] for i in out.get("issues", []) if i["severity"] == Severity.ERROR]
    agent = {"system": "agent", "doc_id": row["doc_id"], "invoice": out["invoice"],
             "route": route, "flags": flags, "usage": out.get("usage", []),
             "attempts": out.get("attempts"), "latency_s": latency,
             "trail": out.get("trail", [])}
    first = rec.results[0]
    first_invoice, _ = snap_iban(first.invoice, text)
    first_check = run_checks(first_invoice, text, vendors, _fresh_ledger(seed_rows),
                             buyer_vat_id, row["doc_id"])
    no_repair = {"system": "agent_no_repair", "doc_id": row["doc_id"],
                 "invoice": first_invoice.model_dump(),
                 "route": "human_review" if first_check.errors else "auto_approve",
                 "flags": [i.code.value for i in first_check.errors],
                 "usage": [first.usage], "latency_s": None}
    return [agent, no_repair]


def run_baseline_doc(row: dict, data_dir: Path, reviewer: BaselineReviewer) -> list[dict]:
    text = pdf_to_text(data_dir / row["file"])
    t0 = time.perf_counter()
    try:
        out, usage = reviewer.review(text)
    except Exception as exc:  # noqa: BLE001
        return [{"system": "baseline", "doc_id": row["doc_id"], "invoice": None,
                 "route": "error", "flags": [], "usage": [], "error": repr(exc)}]
    return [{"system": "baseline", "doc_id": row["doc_id"], "invoice": out.invoice.model_dump(),
             "route": "human_review" if (out.needs_review or out.flags) else "auto_approve",
             "flags": list(out.flags), "reason": out.reason, "usage": [usage],
             "latency_s": time.perf_counter() - t0}]


def run_eval(settings: Settings, split: str, out_dir: Path, limit: int | None = None,
             workers: int = 8) -> dict:
    data_dir = settings.data_dir
    rows = load_split(data_dir, split)[:limit]
    vendors_raw = json.loads((data_dir / "vendors.json").read_text())
    seed_rows = json.loads((data_dir / "ledger_seed.json").read_text())
    vendors = VendorDirectory(vendors_raw)
    extractor = LLMExtractor(settings.model, buyer=BUYER)
    reviewer = BaselineReviewer(vendors_raw, seed_rows, settings.model, buyer=BUYER)

    preds: list[dict] = []
    started = datetime.now(UTC)
    with Progress() as progress, ThreadPoolExecutor(max_workers=workers) as pool:
        task = progress.add_task(f"{split}: {len(rows)} docs x 2 runs", total=len(rows) * 2)
        futures = []
        for row in rows:
            futures.append(pool.submit(run_agent_doc, row, data_dir, extractor, vendors,
                                       seed_rows, settings.buyer_vat_id))
            futures.append(pool.submit(run_baseline_doc, row, data_dir, reviewer))
        for fut in as_completed(futures):
            preds.extend(fut.result())
            progress.advance(task)

    labels = {r["doc_id"]: r for r in rows}
    summaries = {s: summarize(s, [p for p in preds if p["system"] == s], labels, settings.cost)
                 for s in SYSTEMS}
    comparisons = {}
    for a, b in (("agent", "baseline"), ("agent", "agent_no_repair")):
        ids = sorted(labels)
        for metric, key in (("routing", "_per_doc_route"), ("doc_exact", "_per_doc_exact")):
            comparisons[f"{a}_vs_{b}_{metric}"] = mcnemar_exact(
                [summaries[a][key][i] for i in ids], [summaries[b][key][i] for i in ids])
    for s in summaries.values():
        s.pop("_per_doc_route")
        s.pop("_per_doc_exact")

    result = {
        "split": split,
        "n_docs": len(rows),
        "model": settings.model,
        "price_per_1m_tokens_usd": {"input": settings.price_in, "output": settings.price_out},
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "systems": summaries,
        "paired_tests": comparisons,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    with open(out_dir / "predictions.jsonl", "w") as f:
        for p in sorted(preds, key=lambda p: (p["doc_id"], p["system"])):
            f.write(json.dumps(p, ensure_ascii=False, default=str) + "\n")
    (out_dir / "report.md").write_text(render_report(result))
    return result


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _ci(ci) -> str:
    return f"[{100 * ci[0]:.1f}, {100 * ci[1]:.1f}]"


def render_report(r: dict) -> str:
    S = r["systems"]
    names = {"baseline": "Baseline (1 LLM call)", "agent_no_repair": "Agent, no repair loop",
             "agent": "Agent (full graph)"}
    order = ["baseline", "agent_no_repair", "agent"]
    lines = [f"# Eval: `{r['split']}` split, {r['n_docs']} documents, `{r['model']}`", "",
             f"Run {r['started_at']} -> {r['finished_at']} (UTC). Prices: "
             f"${r['price_per_1m_tokens_usd']['input']}/1M input, "
             f"${r['price_per_1m_tokens_usd']['output']}/1M output tokens.", "",
             "## Headline", "",
             "| System | Routing accuracy | Anomaly recall | False-review rate | "
             "Docs fully correct | LLM calls/doc | Cost/doc | Mean latency |",
             "|---|---|---|---|---|---|---|---|"]
    for s in order:
        x = S[s]
        lat = "n/a" if x["latency_mean_s"] is None else f"{x['latency_mean_s']:.1f}s"
        lines.append(
            f"| {names[s]} | {_pct(x['routing_accuracy'])} {_ci(x['routing_accuracy_ci95'])} | "
            f"{_pct(x['anomaly_recall'])} {_ci(x['anomaly_recall_ci95'])} | "
            f"{_pct(x['false_review_rate'])} {_ci(x['false_review_rate_ci95'])} | "
            f"{_pct(x['doc_exact_match'])} | {x['llm_calls_per_doc']:.2f} | "
            f"${x['cost_per_doc_usd']:.4f} | {lat} |")
    lines += ["", "Brackets are 95% Wilson intervals. Run errors: " +
              ", ".join(f"{names[s]}: {S[s]['run_errors']}" for s in order) + ".", "",
              "## Paired tests (exact McNemar)", "",
              "| Comparison | Only first correct | Only second correct | p-value |",
              "|---|---|---|---|"]
    for k, v in r["paired_tests"].items():
        lines.append(f"| `{k}` | {v['only_a']} | {v['only_b']} | {v['p_value']:.4f} |")
    lines += ["", "## Field-level extraction accuracy", "",
              "| Field | " + " | ".join(names[s] for s in order) + " |",
              "|---|" + "---|" * len(order)]
    for f in [*HEADER_FIELDS, "line_items", "tax_lines"]:
        lines.append(f"| {f} | " + " | ".join(_pct(S[s]['field_accuracy'][f]) for s in order)
                     + " |")
    lines += ["", "## Anomalies caught with the right reason", "",
              "Routed to review AND the matching code was flagged.", "",
              "| Anomaly | " + " | ".join(names[s] for s in order) + " |",
              "|---|" + "---|" * len(order)]
    for code in S["agent"]["anomaly_caught_with_right_reason"]:
        cells = []
        for s in order:
            v = S[s]["anomaly_caught_with_right_reason"][code]
            cells.append(f"{v['hit']}/{v['n']}")
        lines.append(f"| {code} | " + " | ".join(cells) + " |")
    lines += ["", "## Confusion (human_review = positive)", "",
              "| System | TP | FN | FP | TN | Tokens in | Tokens out | Total cost |",
              "|---|---|---|---|---|---|---|---|"]
    for s in order:
        c = S[s]["confusion"]
        lines.append(f"| {names[s]} | {c['tp']} | {c['fn']} | {c['fp']} | {c['tn']} | "
                     f"{S[s]['tokens_in']:,} | {S[s]['tokens_out']:,} | ${S[s]['cost_usd']:.3f} |")
    lines.append("")
    return "\n".join(lines)
