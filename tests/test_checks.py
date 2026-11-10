"""The checks are the backbone of the agent, so they are tested against every labelled
document: with a perfect extraction (the ground truth), clean invoices must raise no errors
and every anomalous invoice must raise its injected code."""

import pytest

from invoice_agent.checks import run_checks
from invoice_agent.pdf import pdf_to_text
from invoice_agent.schemas import InvoiceData, IssueCode
from tests.conftest import DATA, first


def _check(row, vendors, ledger, buyer_vat, **overrides):
    inv = InvoiceData(**(row["truth"] | overrides))
    return run_checks(inv, pdf_to_text(DATA / row["file"]), vendors, ledger, buyer_vat)


def test_oracle_routing_on_every_dev_document(labels, vendors, ledger, buyer_vat):
    for row in labels.values():
        res = _check(row, vendors, ledger, buyer_vat)
        codes = {i.code.value for i in res.errors}
        if row["anomaly"] is None:
            assert not codes, (row["doc_id"], codes)
        else:
            assert row["anomaly"] in codes, (row["doc_id"], codes)


def test_misread_total_is_ungrounded_and_inconsistent(labels, vendors, ledger, buyer_vat):
    row = first(labels, None)
    res = _check(row, vendors, ledger, buyer_vat, total=row["truth"]["total"] + 1)
    codes = {i.code for i in res.errors}
    assert IssueCode.UNGROUNDED_VALUE in codes
    assert IssueCode.TOTAL_MISMATCH in codes


def test_seller_buyer_swap_is_detected(labels, vendors, ledger, buyer_vat):
    row = first(labels, None)
    res = _check(row, vendors, ledger, buyer_vat, seller_vat_id=buyer_vat)
    assert IssueCode.SELLER_IS_BUYER in {i.code for i in res.errors}


@pytest.mark.parametrize("anomaly", ["duplicate_invoice", "bank_account_changed",
                                     "unknown_vendor"])
def test_policy_anomalies_are_policy_kind(labels, vendors, ledger, buyer_vat, anomaly):
    res = _check(first(labels, anomaly), vendors, ledger, buyer_vat)
    hit = [i for i in res.errors if i.code.value == anomaly]
    assert hit and hit[0].kind.value == "policy"


def test_vendor_matched_by_vat_id(labels, vendors, ledger, buyer_vat):
    row = first(labels, None)
    res = _check(row, vendors, ledger, buyer_vat, seller_name="Some Other Name Ltd")
    assert res.vendor_match == "vat_id"
