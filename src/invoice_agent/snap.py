"""Deterministic post-processing of LLM extractions.

LLMs are good at finding *which* string is the IBAN and bad at copying 27 characters
without dropping or repeating a group. So the model locates, and code copies: if the
extracted IBAN is not literally in the document, it is replaced by the closest IBAN-shaped
string printed after an "IBAN" label. The checksum check still runs on the snapped value,
so a genuinely invalid IBAN on the document is still caught.
"""

from __future__ import annotations

import difflib
import re

from invoice_agent.checks import is_grounded
from invoice_agent.money import normalize_id
from invoice_agent.schemas import InvoiceData

_LABEL = re.compile(r"IBAN[:\s]*", re.IGNORECASE)
_GROUP = re.compile(r"[A-Z0-9]{1,4}")

# Fields a repair pass may not silently null out when the first read found them printed.
KEEP_IF_DROPPED = ("invoice_date", "due_date", "iban", "seller_vat_id", "po_number")


def iban_candidates(text: str) -> list[str]:
    out = []
    for m in _LABEL.finditer(text):
        tokens = re.split(r"( +)", text[m.end():].split("\n", 1)[0])
        parts: list[str] = []
        for tok in tokens:
            if tok.startswith(" "):
                if len(tok) > 1 and parts:  # a double space ends the IBAN
                    break
                continue
            if not _GROUP.fullmatch(tok):
                break
            parts.append(tok)
        cand = "".join(parts)
        if 15 <= len(cand) <= 34 and cand[:2].isalpha() and cand[2:4].isdigit():
            out.append(cand)
    return out


def snap_iban(inv: InvoiceData, text: str) -> tuple[InvoiceData, dict | None]:
    if not inv.iban or is_grounded("iban", inv.iban, text):
        return inv, None
    cands = iban_candidates(text)
    if not cands:
        return inv, None
    target = normalize_id(inv.iban)
    best = max(cands, key=lambda c: difflib.SequenceMatcher(None, target, c).ratio())
    if difflib.SequenceMatcher(None, target, best).ratio() < 0.7:
        return inv, None
    return inv.model_copy(update={"iban": best}), {"field": "iban", "from": inv.iban, "to": best}


def keep_dropped_values(new: InvoiceData, previous: InvoiceData | None, text: str
                        ) -> tuple[InvoiceData, list[str]]:
    """A repair pass must not 'fix' an inconsistency by deleting a printed value."""
    if previous is None:
        return new, []
    restored, update = [], {}
    for f in KEEP_IF_DROPPED:
        old_v, new_v = getattr(previous, f), getattr(new, f)
        if old_v and not new_v and is_grounded(f, old_v, text):
            update[f] = old_v
            restored.append(f)
    return (new.model_copy(update=update) if update else new), restored
