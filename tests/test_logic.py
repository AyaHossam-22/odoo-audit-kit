from datetime import datetime

from audit.checks.bill_vs_po import price_issue, qty_issue
from audit.checks.expired_lots import classify
from audit.models import CheckResult
from audit.report import safe_cell, write_csv


def test_price_within_tolerance_is_ignored():
    assert price_issue(100.2, 100.0, 0.5) is None


def test_price_above_tolerance_is_reported_as_percent():
    assert price_issue(110.0, 100.0, 0.5) == 10.0
    assert price_issue(90.0, 100.0, 0.5) == -10.0


def test_zero_po_price_with_nonzero_bill_is_flagged():
    assert price_issue(5.0, 0.0, 0.5) == 100.0
    assert price_issue(0.0, 0.0, 0.5) is None


def test_qty_billed_more_than_ordered():
    assert qty_issue(11, 10) is True
    assert qty_issue(10, 10) is False


NOW = datetime(2026, 11, 1, 12, 0, 0)


def test_expiry_classification():
    assert classify("2026-10-31 00:00:00", NOW, 30) == "expired"
    assert classify("2026-11-20 00:00:00", NOW, 30) == "expiring_soon"
    assert classify("2027-03-01 00:00:00", NOW, 30) is None


def test_csv_formula_injection_is_neutralised():
    assert safe_cell("=HYPERLINK(\"http://x\")") == "'=HYPERLINK(\"http://x\")"
    assert safe_cell("-cmd") == "'-cmd"
    assert safe_cell("Normal name") == "Normal name"
    assert safe_cell(-5.0) == -5.0


def test_csv_written_with_arabic_text(tmp_path):
    res = CheckResult("demo", "Demo", "d", ["product", "quantity"], rows=[{"product": "تمر سكري", "quantity": 3}])
    path = write_csv(res, str(tmp_path))
    assert "تمر سكري" in open(path, encoding="utf-8-sig").read()
