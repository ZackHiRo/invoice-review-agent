from __future__ import annotations

import json
from pathlib import Path

import pytest

from invoice_agent.extract import ExtractionResult
from invoice_agent.schemas import InvoiceData
from invoice_agent.store import Ledger, VendorDirectory
from invoice_agent.synth.world import BUYER

DATA = Path(__file__).resolve().parents[1] / "data"


class ScriptedExtractor:
    """Returns pre-baked extractions in order (last one repeats). Records every call."""

    def __init__(self, *invoices: dict):
        self.invoices = list(invoices)
        self.calls: list[dict] = []

    def extract(self, text, previous=None, issues=None):
        self.calls.append({"previous": previous, "issues": issues})
        inv = self.invoices[min(len(self.calls) - 1, len(self.invoices) - 1)]
        return ExtractionResult(InvoiceData(**inv),
                                {"model": "fake", "input_tokens": 100, "output_tokens": 50})


@pytest.fixture(scope="session")
def labels() -> dict[str, dict]:
    rows = [json.loads(line) for line in open(DATA / "dev" / "labels.jsonl")]
    return {r["doc_id"]: r for r in rows}


@pytest.fixture(scope="session")
def vendors() -> VendorDirectory:
    return VendorDirectory.from_json(DATA / "vendors.json")


@pytest.fixture()
def ledger() -> Ledger:
    led = Ledger()
    led.seed(json.loads((DATA / "ledger_seed.json").read_text()))
    return led


@pytest.fixture()
def buyer_vat() -> str:
    return BUYER["vat_id"]


def first(labels: dict, anomaly: str | None, template: str | None = None) -> dict:
    for r in labels.values():
        if r["anomaly"] == anomaly and (template is None or r["template"] == template):
            return r
    raise LookupError(anomaly)
