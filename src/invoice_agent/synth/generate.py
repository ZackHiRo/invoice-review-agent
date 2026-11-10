"""Generate the dev and test splits: PDFs, labels.jsonl, vendor master and ledger history."""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

from invoice_agent.schemas import ANOMALY_CODES, IssueCode
from invoice_agent.synth.build import _invoice_number, build_invoice
from invoice_agent.synth.render import render
from invoice_agent.synth.world import Vendor, build_unknown_vendors, build_vendors

NEEDS_IBAN = {IssueCode.INVALID_IBAN, IssueCode.BANK_ACCOUNT_CHANGED, IssueCode.VAT_MISCALCULATED}


def build_ledger(vendors: list[Vendor], seed: int) -> list[dict]:
    rng = random.Random(seed + 7)
    ledger = []
    for v in vendors:
        for _ in range(rng.randint(2, 4)):
            d = date(2025, 3, 1) + timedelta(days=rng.randint(0, 300))
            ledger.append({
                "vendor_id": v.vendor_id,
                "invoice_number": _invoice_number(rng, v, d.year),
                "invoice_date": d.isoformat(),
                "currency": v.currency,
                "total": round(rng.uniform(80, 9000), 2),
                "status": "posted",
            })
    return ledger


def generate_split(out: Path, name: str, n: int, anomaly_rate: float, seed: int,
                   vendors: list[Vendor], unknown: list[Vendor], ledger: list[dict]) -> list[dict]:
    rng = random.Random(seed)
    n_anom = round(n * anomaly_rate)
    plan: list[IssueCode | None] = [ANOMALY_CODES[i % len(ANOMALY_CODES)] for i in range(n_anom)]
    plan += [None] * (n - n_anom)
    rng.shuffle(plan)
    split_dir = out / name
    split_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    with_iban = [v for v in vendors if v.iban]
    for i, anomaly in enumerate(plan):
        doc_id = f"{name}-{i + 1:03d}"
        if anomaly == IssueCode.UNKNOWN_VENDOR:
            vendor = rng.choice(unknown)
        elif anomaly in NEEDS_IBAN:
            vendor = rng.choice(with_iban)
        else:
            vendor = rng.choice(vendors)
        spec = build_invoice(rng, doc_id, vendor, anomaly, ledger)
        pdf = split_dir / f"{doc_id}.pdf"
        render(spec, pdf)
        rows.append({
            "doc_id": doc_id,
            "file": f"{name}/{doc_id}.pdf",
            "template": spec.template,
            "locale": spec.locale,
            "vendor_id": vendor.vendor_id if not anomaly == IssueCode.UNKNOWN_VENDOR else None,
            "anomaly": anomaly.value if anomaly else None,
            "expected_route": "human_review" if anomaly else "auto_approve",
            "truth": spec.truth.model_dump(),
        })
    with open(split_dir / "labels.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rows


def generate(out: Path, n_dev: int = 40, n_test: int = 150, anomaly_rate: float = 0.4,
             seed: int = 2026) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    vendors = build_vendors(seed)
    unknown = build_unknown_vendors(seed)
    ledger = build_ledger(vendors, seed)
    (out / "vendors.json").write_text(
        json.dumps([v.public() for v in vendors], indent=2, ensure_ascii=False) + "\n")
    (out / "ledger_seed.json").write_text(json.dumps(ledger, indent=2) + "\n")
    dev = generate_split(out, "dev", n_dev, anomaly_rate, seed + 100, vendors, unknown, ledger)
    test = generate_split(out, "test", n_test, anomaly_rate, seed + 200, vendors, unknown, ledger)
    return {"dev": len(dev), "test": len(test), "vendors": len(vendors), "ledger": len(ledger)}
