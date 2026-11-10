"""Vendor master file and the AP ledger (SQLite). The graph only talks to these classes."""

from __future__ import annotations

import difflib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from invoice_agent.money import normalize_id

LEGAL_SUFFIXES = r"\b(ltd|limited|llp|llc|inc|gmbh|sarl|sas|sa|bv|b\.v\.|plc|co)\b\.?"


def normalize_name(name: str) -> str:
    s = name.lower().replace("&", " and ")
    s = re.sub(LEGAL_SUFFIXES, " ", s)
    s = re.sub(r"[^a-z0-9äöüßéèàçâêîôûëïœ ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class VendorMatch:
    vendor: dict
    method: str  # "vat_id" | "name"
    score: float


class VendorDirectory:
    def __init__(self, vendors: list[dict]):
        self.vendors = vendors
        self._by_vat = {normalize_id(v["vat_id"]): v for v in vendors if v.get("vat_id")}

    @classmethod
    def from_json(cls, path: str | Path) -> VendorDirectory:
        return cls(json.loads(Path(path).read_text()))

    def match(self, name: str | None, vat_id: str | None) -> VendorMatch | None:
        key = normalize_id(vat_id)
        if key and key in self._by_vat:
            return VendorMatch(self._by_vat[key], "vat_id", 1.0)
        if not name:
            return None
        target = normalize_name(name)
        best, best_score = None, 0.0
        for v in self.vendors:
            score = difflib.SequenceMatcher(None, target, normalize_name(v["name"])).ratio()
            if score > best_score:
                best, best_score = v, score
        if best and best_score >= 0.92:
            return VendorMatch(best, "name", best_score)
        return None


SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY,
    thread_id TEXT UNIQUE,
    vendor_id TEXT NOT NULL,
    invoice_number TEXT NOT NULL,
    invoice_number_norm TEXT NOT NULL,
    invoice_date TEXT,
    currency TEXT,
    total REAL,
    status TEXT NOT NULL,
    decided_by TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_invoices_vendor_number ON invoices(vendor_id, invoice_number_norm);
CREATE TABLE IF NOT EXISTS review_queue (
    thread_id TEXT PRIMARY KEY,
    doc TEXT,
    reasons TEXT,
    status TEXT NOT NULL,
    created_at TEXT,
    resolved_at TEXT,
    resolution TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Ledger:
    """Posted invoices plus the human review queue."""

    def __init__(self, path: str | Path = ":memory:"):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def seed(self, rows: list[dict]) -> None:
        for r in rows:
            self.conn.execute(
                "INSERT INTO invoices (vendor_id, invoice_number, invoice_number_norm,"
                " invoice_date, currency, total, status, decided_by, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (r["vendor_id"], r["invoice_number"], normalize_id(r["invoice_number"]),
                 r.get("invoice_date"), r.get("currency"), r.get("total"),
                 r.get("status", "posted"), "seed", _now()),
            )
        self.conn.commit()

    def find_duplicate(self, vendor_id: str, invoice_number: str,
                       exclude_thread: str | None = None) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM invoices WHERE vendor_id=? AND invoice_number_norm=? AND status='posted'"
            " AND (thread_id IS NULL OR thread_id != ?) LIMIT 1",
            (vendor_id, normalize_id(invoice_number), exclude_thread or ""),
        ).fetchone()
        return dict(row) if row else None

    def record(self, thread_id: str, vendor_id: str | None, invoice: dict, status: str,
               decided_by: str) -> None:
        """Idempotent per thread: re-running a node after a crash will not double-post."""
        self.conn.execute(
            "INSERT INTO invoices (thread_id, vendor_id, invoice_number, invoice_number_norm,"
            " invoice_date, currency, total, status, decided_by, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(thread_id) DO UPDATE SET status=excluded.status,"
            " decided_by=excluded.decided_by",
            (thread_id, vendor_id or "UNKNOWN", invoice.get("invoice_number") or "",
             normalize_id(invoice.get("invoice_number")), invoice.get("invoice_date"),
             invoice.get("currency"),
             invoice["total"] if "total" in invoice else invoice.get("net_amount_due"),
             status, decided_by, _now()),
        )
        self.conn.commit()

    def enqueue(self, thread_id: str, doc: str, reasons: list[str]) -> None:
        self.conn.execute(
            "INSERT INTO review_queue (thread_id, doc, reasons, status, created_at)"
            " VALUES (?,?,?,'pending',?) ON CONFLICT(thread_id) DO UPDATE SET"
            " reasons=excluded.reasons, status='pending'",
            (thread_id, doc, json.dumps(reasons), _now()),
        )
        self.conn.commit()

    def resolve(self, thread_id: str, resolution: str) -> None:
        self.conn.execute(
            "UPDATE review_queue SET status='resolved', resolved_at=?, resolution=?"
            " WHERE thread_id=?",
            (_now(), resolution, thread_id),
        )
        self.conn.commit()

    def pending(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM review_queue WHERE status='pending' ORDER BY created_at"
        ).fetchall()
        return [dict(r) | {"reasons": json.loads(r["reasons"])} for r in rows]

    def invoices(self, include_seed: bool = False) -> list[dict]:
        q = "SELECT * FROM invoices" + ("" if include_seed else " WHERE thread_id IS NOT NULL")
        return [dict(r) for r in self.conn.execute(q + " ORDER BY id").fetchall()]
