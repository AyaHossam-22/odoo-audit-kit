from audit.checks import moves_without_entries as m
from audit.connection import OdooConnection
from audit.models import Options


class FakeConn(OdooConnection):
    """Minimal fake: fields_get answers come from FIELDS, everything else is empty."""

    FIELDS = {}
    UNSEARCHABLE = ()

    def __init__(self):
        super().__init__("http://x", "db", "u", "k")
        self.uid = 1
        self.calls = []

    def call(self, model, method, args, kwargs=None):
        self.calls.append((model, method, args))
        if method == "fields_get":
            return {f: {"searchable": f not in self.UNSEARCHABLE} for f in self.FIELDS.get(model, [])}
        if model == "product.category":
            return [{"id": 1, "property_valuation": "real_time", "property_stock_valuation_account_id": [10, "Stock"]}]
        if method == "search_count":
            return 0
        return []


def test_skipped_with_hints_when_no_link_field():
    conn = FakeConn()
    conn.FIELDS = {"stock.move": ["is_valued", "value", "state"], "product.category": ["property_valuation", "property_stock_valuation_account_id"]}
    res = m.run(conn, Options())
    assert res.status == "skipped"
    assert "is_valued" in res.skipped_reason and "value" in res.skipped_reason


def test_runs_with_account_move_id_and_is_valued():
    conn = FakeConn()
    conn.FIELDS = {"stock.move": ["account_move_id", "is_valued", "quantity"], "product.category": ["property_valuation", "property_stock_valuation_account_id"]}
    res = m.run(conn, Options())
    assert res.status == "clean"
    domains = [c[2][0] for c in conn.calls if c[0] == "stock.move" and c[1] == "search_read"]
    assert any(("account_move_id", "=", False) in d for d in domains)
    assert any(("is_valued", "=", True) in d for d in domains)


def test_old_versions_keep_location_based_domain():
    conn = FakeConn()
    conn.FIELDS = {"stock.move": ["account_move_ids", "quantity"], "product.category": ["property_valuation", "property_stock_valuation_account_id"]}
    m.run(conn, Options())
    domain = next(c[2][0] for c in conn.calls if c[0] == "stock.move" and c[1] == "search_count")
    assert ("location_id.usage", "!=", "internal") in domain


def test_unstored_is_valued_falls_back_to_is_in_is_out():
    """Regression: on Odoo 19 stock.move.is_valued is not stored and cannot be searched."""
    conn = FakeConn()
    conn.FIELDS = {"stock.move": ["account_move_id", "is_valued", "is_in", "is_out", "quantity"], "product.category": ["property_valuation", "property_stock_valuation_account_id"]}
    conn.UNSEARCHABLE = ("is_valued",)
    res = m.run(conn, Options())
    assert res.status == "clean"
    domain = next(c[2][0] for c in conn.calls if c[0] == "stock.move" and c[1] == "search_count")
    assert not any(isinstance(t, tuple) and t[0] == "is_valued" for t in domain)
    assert ("is_in", "=", True) in domain and ("is_out", "=", True) in domain
