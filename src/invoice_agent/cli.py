"""invoice-agent: process invoices, work the human review queue, run the eval."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import typer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from rich.console import Console
from rich.table import Table

from invoice_agent.config import Settings, load_settings, require_api_key
from invoice_agent.graph import Deps, build_graph
from invoice_agent.store import Ledger, VendorDirectory
from invoice_agent.synth.world import BUYER

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="LangGraph invoice review agent: extract, check, repair, ask a human.")
console = Console()


# ---------- wiring -------------------------------------------------------------------------

def _runtime(settings: Settings, need_llm: bool = True):
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = settings.state_dir / "ledger.sqlite"
    fresh = not ledger_path.exists()
    ledger = Ledger(ledger_path)
    if fresh:
        ledger.seed(json.loads((settings.data_dir / "ledger_seed.json").read_text()))
    vendors = VendorDirectory.from_json(settings.data_dir / "vendors.json")
    if need_llm:
        require_api_key()
        from invoice_agent.extract import LLMExtractor
        extractor = LLMExtractor(settings.model, buyer=BUYER)
    else:
        extractor = None
    deps = Deps(extractor, vendors, ledger, settings.buyer_vat_id)
    conn = sqlite3.connect(settings.state_dir / "checkpoints.sqlite", check_same_thread=False)
    graph = build_graph(SqliteSaver(conn))
    return graph, deps


def thread_id_for(pdf: Path) -> str:
    return "inv-" + hashlib.sha256(pdf.read_bytes()).hexdigest()[:12]


def _print_invoice(inv: dict) -> None:
    t = Table(show_header=False, box=None, pad_edge=False)
    for k in ("document_type", "invoice_number", "invoice_date", "due_date", "seller_name",
              "seller_vat_id", "iban", "currency", "subtotal", "tax_total", "total"):
        t.add_row(f"[dim]{k}[/dim]", str(inv.get(k)))
    t.add_row("[dim]line_items[/dim]", str(len(inv.get("line_items", []))))
    console.print(t)


def _print_issues(issues: list[dict]) -> None:
    for i in issues:
        colour = "red" if i["severity"] == "error" else "yellow"
        console.print(f"  [{colour}]{i['severity']:7}[/{colour}] [bold]{i['code']}[/bold] "
                      f"({i['kind']}): {i['message']}")


def _report(graph, cfg) -> None:
    snap = graph.get_state(cfg)
    s = snap.values
    console.rule(f"[bold]{s.get('doc')}[/bold]  thread={cfg['configurable']['thread_id']}")
    if s.get("invoice"):
        _print_invoice(s["invoice"])
    if s.get("issues"):
        console.print("[bold]Checks[/bold]")
        _print_issues(s["issues"])
    path = " -> ".join(
        t["node"] + (f"({t['route']})" if t.get("route") else "") +
        (f"[{t['action']}]" if t.get("action") else "") for t in s.get("trail", []))
    console.print(f"[bold]Path[/bold] {path}")
    calls = s.get("usage", [])
    console.print(f"[bold]LLM calls[/bold] {len(calls)}  "
                  f"tokens in/out {sum(c['input_tokens'] for c in calls)}/"
                  f"{sum(c['output_tokens'] for c in calls)}")
    if snap.next:
        console.print(f"[bold yellow]PAUSED for human review.[/bold yellow] Run:\n"
                      f"  invoice-agent review {cfg['configurable']['thread_id']} "
                      f"--approve | --reject | --set total=123.45")
    else:
        colour = "green" if s.get("status") == "posted" else "red"
        console.print(f"[bold {colour}]{str(s.get('status')).upper()}[/bold {colour}]")


# ---------- commands -----------------------------------------------------------------------

@app.command()
def process(pdfs: list[Path] = typer.Argument(..., exists=True, dir_okay=False)) -> None:
    """Run invoices through the graph. Re-running the same file reuses its thread."""
    settings = load_settings()
    graph, deps = _runtime(settings)
    for pdf in pdfs:
        tid = thread_id_for(pdf)
        cfg = {"configurable": {"thread_id": tid}}
        if graph.get_state(cfg).values:
            console.print(f"[dim]{pdf} already processed in thread {tid}; showing state.[/dim]")
        else:
            graph.invoke({"doc": str(pdf), "thread_id": tid}, cfg, context=deps)
        _report(graph, cfg)


@app.command()
def queue() -> None:
    """List invoices waiting for a human."""
    _, deps = _runtime(load_settings(), need_llm=False)
    rows = deps.ledger.pending()
    if not rows:
        console.print("Review queue is empty.")
        return
    t = Table("thread", "document", "reasons")
    for r in rows:
        t.add_row(r["thread_id"], r["doc"], "\n".join(r["reasons"]))
    console.print(t)


@app.command()
def show(thread_id: str) -> None:
    """Show the state, checks and audit trail of one thread."""
    graph, _ = _runtime(load_settings(), need_llm=False)
    _report(graph, {"configurable": {"thread_id": thread_id}})


@app.command()
def review(
    thread_id: str,
    approve: bool = typer.Option(False, "--approve"),
    reject: bool = typer.Option(False, "--reject"),
    set_: list[str] = typer.Option([], "--set", help="field=value, repeatable"),
    note: str = typer.Option("", "--note"),
    reviewer: str = typer.Option("cli-reviewer", "--reviewer"),
) -> None:
    """Resume a paused thread with a human decision."""
    if sum([approve, reject, bool(set_)]) != 1:
        raise typer.BadParameter("use exactly one of --approve, --reject, --set")
    settings = load_settings()
    graph, deps = _runtime(settings, need_llm=bool(set_))
    cfg = {"configurable": {"thread_id": thread_id}}
    if not graph.get_state(cfg).next:
        raise typer.BadParameter(f"thread {thread_id} is not waiting for review")
    corrections = {}
    for item in set_:
        key, _, value = item.partition("=")
        corrections[key.strip()] = value.strip()
    action = "approve" if approve else "reject" if reject else "correct"
    graph.invoke(Command(resume={"action": action, "corrections": corrections,
                                 "reviewer": reviewer, "note": note}), cfg, context=deps)
    _report(graph, cfg)


@app.command()
def ledger() -> None:
    """Invoices this agent posted or rejected."""
    _, deps = _runtime(load_settings(), need_llm=False)
    t = Table("vendor", "number", "date", "total", "status", "decided by")
    for r in deps.ledger.invoices():
        t.add_row(r["vendor_id"], r["invoice_number"], r["invoice_date"] or "",
                  f"{r['total']:.2f} {r['currency']}", r["status"], r["decided_by"])
    console.print(t)


@app.command()
def generate(out: Path = Path("data"), n_dev: int = 40, n_test: int = 150,
             seed: int = 2026) -> None:
    """Rebuild the synthetic dataset (deterministic for a given seed)."""
    from invoice_agent.synth.generate import generate as gen
    console.print(gen(out, n_dev=n_dev, n_test=n_test, seed=seed))


@app.command("eval")
def eval_(split: str = "test", model: str | None = None, out: Path | None = None,
          limit: int | None = None, workers: int = 8, price_in: float | None = None,
          price_out: float | None = None) -> None:
    """Score the agent, its no-repair ablation and the one-call baseline on a split."""
    from dataclasses import replace

    from invoice_agent.evaluation.runner import run_eval
    settings = load_settings()
    require_api_key()
    if model:
        settings = replace(settings, model=model)
    if price_in is not None:
        settings = replace(settings, price_in=price_in)
    if price_out is not None:
        settings = replace(settings, price_out=price_out)
    out = out or Path("eval/results") / f"{split}-{settings.model}"
    run_eval(settings, split, out, limit=limit, workers=workers)
    console.print((out / "report.md").read_text())


@app.command("fetch-confbench")
def fetch_confbench(out: Path = Path("data/real/confbench")) -> None:
    """Download the 75 ConfBench FCC invoice originals + labels (CC BY-NC 4.0, git-ignored)."""
    from invoice_agent.real.confbench import fetch
    console.print(fetch(out))


@app.command("eval-real")
def eval_real(root: Path = Path("data/real/confbench"), out: Path | None = None,
              limit: int | None = None, workers: int = 6) -> None:
    """Score extraction and the checks on real ConfBench invoices (digital PDFs only)."""
    from invoice_agent.real.evaluate import run_real_eval
    settings = load_settings()
    require_api_key()
    if not (root / "labels.jsonl").exists():
        raise typer.BadParameter(f"{root} has no labels.jsonl: run `invoice-agent "
                                 "fetch-confbench` first")
    out = out or Path("eval/results") / f"real-confbench-{settings.model}"
    run_real_eval(settings, root, out, limit=limit, workers=workers)
    console.print((out / "report.md").read_text())


@app.command()
def graph() -> None:
    """Print the graph as Mermaid."""
    console.print(build_graph().get_graph().draw_mermaid(), markup=False)


if __name__ == "__main__":
    app()
