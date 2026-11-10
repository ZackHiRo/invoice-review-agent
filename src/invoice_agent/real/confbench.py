"""Download the 75 clean originals of Amazon ConfBench (FCC broadcast-advertising invoices).

Source: https://huggingface.co/datasets/amazon/ConfBench (CC BY-NC 4.0). The underlying
invoices are public records filed with the US FCC; the labels are RealKIE-FCC-Verified
annotations. Files are downloaded to data/real/confbench/ (git-ignored): this repository
does not redistribute them.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

REPO = "https://huggingface.co/datasets/amazon/ConfBench/resolve/main"
LABELS = f"{REPO}/data/test-00000-of-00001.parquet"
FIELD_MAP = {  # ConfBench field -> our MediaInvoice field
    "Agency": "agency",
    "Advertiser": "advertiser",
    "GrossTotal": "gross_total",
    "AgencyCommission": "agency_commission",
    "NetAmountDue": "net_amount_due",
    "PaymentTerms": "payment_terms",
}


def _get(url: str, dest: Path) -> int:
    resp = httpx.get(url, follow_redirects=True, timeout=120,
                     headers={"User-Agent": "invoice-review-agent-eval"})
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return len(resp.content)


def fetch(out: Path = Path("data/real/confbench"), force: bool = False) -> dict:
    import pyarrow.parquet as pq  # installed with streamlit / the `ui` extra

    out.mkdir(parents=True, exist_ok=True)
    parquet = out / "labels.parquet"
    if force or not parquet.exists():
        _get(LABELS, parquet)
    rows = [r for r in pq.read_table(parquet).to_pylist() if r["noise_variant"] == "original"]
    labels, downloaded, total_bytes = [], 0, 0
    for r in sorted(rows, key=lambda r: r["id"]):
        doc_id = r["id"].split("__")[0]
        pdf = out / f"{doc_id}.pdf"
        if force or not pdf.exists():
            total_bytes += _get(f"{REPO}/pdfs/{r['id']}", pdf)
            downloaded += 1
        raw = r["json_response"]
        raw = json.loads(raw) if isinstance(raw, str) else raw
        raw = raw.get("inference_result", raw)
        labels.append({
            "doc_id": doc_id,
            "file": pdf.name,
            "pages": r["page_count"],
            "truth": {ours: raw.get(theirs) for theirs, ours in FIELD_MAP.items()},
            "n_line_items": len(raw.get("LineItems") or []),
        })
    with open(out / "labels.jsonl", "w") as f:
        for row in labels:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"documents": len(labels), "downloaded": downloaded, "bytes": total_bytes,
            "dir": str(out)}
