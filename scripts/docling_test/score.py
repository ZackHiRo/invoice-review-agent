"""Score pypdf vs Docling text: does each ground-truth value appear in the extracted text?

Run with the invoice-review-agent venv (reuses its grounding helpers):
python score.py <out_dir> <labels.jsonl> <set_name>
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from invoice_agent.checks import is_grounded
from invoice_agent.money import amount_variants, squash

out, labels_path, set_name = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
labels = {json.loads(l)["doc_id"]: json.loads(l) for l in labels_path.open()}
timings = {}
for l in (out / "timings.jsonl").open():
    r = json.loads(l)
    timings.setdefault(r["doc"], {}).update(r)

TOOLS = ("pypdf", "docling")
hits = defaultdict(lambda: defaultdict(lambda: [0, 0]))   # group -> field -> [found, total] per tool key
misses = defaultdict(list)                                # (tool, field) -> docs


def present(field, value, text):
    if value is None or value == "":
        return None
    if isinstance(value, str) and field not in ("invoice_date", "due_date"):
        return squash(value) in squash(text)
    return is_grounded(field, value, text)


def row_intact(desc, amount, text):
    """One line of output holds both the line item's description and its amount (reading order kept)."""
    d, amts = squash(desc), {squash(v) for v in amount_variants(amount)}
    return any(d in squash(line) and any(a in squash(line) for a in amts) for line in text.splitlines())


docs = sorted(d for d in labels if (out / "docling" / f"{d}.txt").exists())
for doc in docs:
    lab, tm = labels[doc], timings.get(doc, {})
    texts = {t: (out / t / f"{doc}.txt").read_text() for t in TOOLS}
    pages = tm.get("pages") or lab.get("pages") or 1
    pypdf_chars = len(re.sub(r"\[page \d+\]", "", texts["pypdf"]).strip())
    group = "all" if set_name == "synthetic" else ("digital" if pypdf_chars / pages >= 200 else "scan")
    truth = lab["truth"]
    fields = {k: v for k, v in truth.items() if not isinstance(v, list) and k not in ("document_type", "currency")}
    for tool in TOOLS:
        for f, v in fields.items():
            p = present(f, v, texts[tool])
            if p is None:
                continue
            cell = hits[group][(f, tool)]
            cell[0] += p
            cell[1] += 1
            if not p:
                misses[(tool, f)].append(doc)
        for item in truth.get("line_items") or []:
            cell = hits[group][("line-item row intact", tool)]
            cell[0] += row_intact(item["description"], item["amount"], texts[tool])
            cell[1] += 1

print(f"# {set_name}: {len(docs)} documents\n")
for group, cells in hits.items():
    n = sum(1 for d in docs)  # noqa
    print(f"## {group}\n\n| field | pypdf | docling |\n|---|---|---|")
    for f in dict.fromkeys(k[0] for k in cells):
        a, b = cells.get((f, "pypdf"), [0, 0]), cells.get((f, "docling"), [0, 0])
        print(f"| {f} | {a[0]}/{a[1]} | {b[0]}/{b[1]} |")
    print()
ds = [timings[d].get("docling_s") for d in docs if timings.get(d, {}).get("docling_s") is not None]
ps = [timings[d].get("pypdf_s") for d in docs if timings.get(d, {}).get("pypdf_s") is not None]
if ds:
    print(f"Time per document: pypdf {sum(ps)/max(len(ps),1):.2f}s, docling {sum(ds)/len(ds):.1f}s (median {sorted(ds)[len(ds)//2]:.1f}s)\n")
bad = [(d, timings[d].get("docling_status"), timings[d].get("errors")) for d in docs
       if timings.get(d, {}).get("docling_status") not in (None, "ConversionStatus.SUCCESS")]
print(f"Docling non-success: {len(bad)}")
for b in bad[:10]:
    print(" ", b[0], b[1], (b[2] or [""])[0][:200])
print("\n## Values Docling missed but pypdf found")
for (tool, f), ds_ in sorted(misses.items()):
    if tool != "docling":
        continue
    only = [d for d in ds_ if d not in misses.get(("pypdf", f), [])]
    if only:
        print(f"- {f}: {len(only)} -> {', '.join(only[:8])}")
print("\n## Values pypdf missed but Docling found")
for (tool, f), ds_ in sorted(misses.items()):
    if tool != "pypdf":
        continue
    only = [d for d in ds_ if d not in misses.get(("docling", f), [])]
    if only:
        print(f"- {f}: {len(only)} -> {', '.join(only[:8])}")
