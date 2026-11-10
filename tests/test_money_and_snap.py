from invoice_agent.money import (
    amount_variants,
    close,
    iban_is_valid,
    iban_with_check_digits,
    normalize_id,
)
from invoice_agent.schemas import InvoiceData
from invoice_agent.snap import iban_candidates, keep_dropped_values, snap_iban


def test_known_valid_iban():
    assert iban_is_valid("GB29 NWBK 6016 1331 9268 19")  # the ISO 13616 example
    assert iban_is_valid("DE89370400440532013000")


def test_single_digit_change_breaks_checksum():
    assert not iban_is_valid("GB29 NWBK 6016 1331 9268 18")


def test_generated_iban_round_trips():
    iban = iban_with_check_digits("NL", "ABNA0417164300")
    assert iban_is_valid(iban)


def test_amount_variants_cover_locales():
    v = amount_variants(1234.5)
    assert {"1,234.50", "1.234,50", "1 234,50", "1234.50"} <= v


def test_close_tolerates_rounding_only():
    assert close(10.004, 10.0)
    assert not close(10.03, 10.0)


def test_normalize_id():
    assert normalize_id("fr 31-0807") == "FR310807"


TEXT = "Bank details: IBAN FR31 0807 3702 8382 2264 6861 642  BIC MFXXFRDT\nThank you"


def _inv(**kw) -> InvoiceData:
    base = dict(document_type="invoice", invoice_number="F-1", invoice_date="2026-01-02",
                due_date=None, currency="EUR", seller_name="S", seller_vat_id=None,
                buyer_name="B", po_number=None, iban=None, line_items=[], tax_lines=[],
                subtotal=0, tax_total=0, total=0)
    return InvoiceData(**(base | kw))


def test_iban_candidates_stop_at_double_space():
    assert iban_candidates(TEXT) == ["FR3108073702838222646861642"]


def test_snap_replaces_a_garbled_copy_with_the_printed_iban():
    garbled = _inv(iban="FR310807370283822264861642")  # one group dropped by the model
    snapped, event = snap_iban(garbled, TEXT)
    assert snapped.iban == "FR3108073702838222646861642"
    assert event["from"] == "FR310807370283822264861642"


def test_snap_leaves_grounded_iban_alone():
    inv = _inv(iban="FR3108073702838222646861642")
    assert snap_iban(inv, TEXT) == (inv, None)


def test_repair_cannot_silently_drop_a_printed_value():
    text = "Invoice Date: 10/03/2026 Due Date: 01/03/2026"
    previous = _inv(invoice_date="2026-03-10", due_date="2026-03-01")
    repaired = _inv(invoice_date="2026-03-10", due_date=None)
    kept, restored = keep_dropped_values(repaired, previous, text)
    assert kept.due_date == "2026-03-01" and restored == ["due_date"]
