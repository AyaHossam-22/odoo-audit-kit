import xmlrpc.client

import pytest

from audit.account_review import find_account, run_account_review, running_balances
from audit.checks import trial_balance as tb
from audit.connection import OdooConnection
from audit.models import Options


def test_abnormal_side_rules():
    assert tb.abnormal_side("asset_current", -50, 1) == "asset with a credit balance"
    assert tb.abnormal_side("asset_current", -0.5, 1) is None  # inside tolerance
    assert tb.abnormal_side("liability_payable", 10, 1) == "liability with a debit balance"
    assert tb.abnormal_side("income", 10, 1) == "income with a net debit balance"
    assert tb.abnormal_side("expense", -10, 1) == "expense with a net credit balance"
    assert tb.abnormal_side("equity", 10, 1) == "equity with a debit balance"
    assert tb.abnormal_side("asset_current", 500, 1) is None
    assert tb.abnormal_side("off_balance", 500, 1) is None


def test_running_balance_includes_opening():
    lines = [{"debit": 100, "credit": 0}, {"debit": 0, "credit": 30}, {"debit": 5.5, "credit": 0}]
    assert running_balances(1000, lines) == [1100, 1070, 1075.5]
    assert running_balances(0, []) == []


class Fake(OdooConnection):
    def __init__(self, data, group_fails=False):
        super().__init__("http://x", "d", "u", "k")
        self.uid = 1
        self.data, self.group_fails, self.calls = data, group_fails, []

    def call(self, model, method, args, kwargs=None):
        self.calls.append((model, method))
        if method == "read_group":
            if self.group_fails:
                raise xmlrpc.client.Fault(1, "read_group removed")
            return self.data["groups"]
        if method == "read":
            return [r for r in self.data.get(model, []) if r["id"] in args[0]]
        if method == "search_read" and model == "account.account":
            return self.data["account.account"]
        if method == "search_read" and model == "account.move.line":
            domain = args[0]
            if any(isinstance(t, tuple) and t[0] == "id" and t[2] > 0 for t in domain):
                return []  # second page: nothing left
            return self.data["lines_for"](domain)
        return []


ACCOUNTS = [
    {"id": 1, "display_name": "110100 Cash", "account_type": "asset_cash"},
    {"id": 2, "display_name": "210100 Payable", "account_type": "liability_payable"},
    {"id": 3, "display_name": "400000 Sales", "account_type": "income"},
]


def test_trial_balance_flags_unbalanced_total_and_odd_sides():
    groups = [
        {"account_id": [1, "110100 Cash"], "debit": 100.0, "credit": 250.0},   # asset with credit balance
        {"account_id": [2, "210100 Payable"], "debit": 0.0, "credit": 40.0},   # normal
        {"account_id": [3, "400000 Sales"], "debit": 0.0, "credit": 500.0},    # normal
    ]
    conn = Fake({"groups": groups, "account.account": ACCOUNTS})
    res = tb.run(conn, Options())
    flags = [(r["account"], r["flag"]) for r in res.rows]
    assert ("TOTAL", "debits do not equal credits") in flags
    assert ("110100 Cash", "asset with a credit balance") in flags
    assert not any(a == "210100 Payable" for a, _ in flags)
    assert res.rows[0]["account"] == "TOTAL"  # the total always comes first


def test_trial_balance_clean_when_balanced():
    groups = [
        {"account_id": [1, "110100 Cash"], "debit": 100.0, "credit": 0.0},
        {"account_id": [3, "400000 Sales"], "debit": 0.0, "credit": 100.0},
    ]
    res = tb.run(Fake({"groups": groups, "account.account": ACCOUNTS}), Options())
    assert res.status == "clean"


def test_trial_balance_falls_back_when_read_group_fails():
    lines = lambda domain: [
        {"id": 1, "account_id": [1, "c"], "debit": 100.0, "credit": 0.0},
        {"id": 2, "account_id": [3, "s"], "debit": 0.0, "credit": 100.0},
    ]
    conn = Fake({"account.account": ACCOUNTS, "lines_for": lines}, group_fails=True)
    res = tb.run(conn, Options())
    assert res.status == "clean"
    assert ("account.move.line", "search_read") in conn.calls


def test_as_of_is_applied_to_the_domain():
    seen = {}
    conn = Fake({"groups": [], "account.account": []})
    original = conn.call

    def spy(model, method, args, kwargs=None):
        if method == "read_group":
            seen["domain"] = args[0]
        return original(model, method, args, kwargs)

    conn.call = spy
    tb.run(conn, Options(as_of="2026-06-30"))
    assert ("date", "<=", "2026-06-30") in seen["domain"]


def test_account_review_with_opening_balance_and_manual_entries():
    def lines_for(domain):
        before = any(isinstance(t, tuple) and t[0] == "date" and t[1] == "<" for t in domain)
        if before:
            return [{"id": 1, "debit": 1000.0, "credit": 0.0}]
        return [
            {"id": 3, "date": "2026-02-02", "move_id": [11, "JE/2"], "journal_id": [1, "Misc"], "partner_id": False,
             "name": "adj", "debit": 0.0, "credit": 30.0},
            {"id": 2, "date": "2026-02-01", "move_id": [10, "BILL/1"], "journal_id": [2, "Bills"], "partner_id": [7, "Vendor"],
             "name": "bill", "debit": 100.0, "credit": 0.0},
        ]

    data = {"account.account": [ACCOUNTS[0]], "lines_for": lines_for,
            "account.move": [{"id": 10, "move_type": "in_invoice"}, {"id": 11, "move_type": "entry"}]}
    res = run_account_review(Fake(data), Options(), "110100", "2026-02-01", "2026-12-31")
    assert res.status == "listing"
    assert res.rows[0]["entry"] == "OPENING BALANCE" and res.rows[0]["running_balance"] == 1000.0
    assert [r["entry"] for r in res.rows[1:]] == ["BILL/1", "JE/2"]  # sorted by date, not by id
    assert [r["running_balance"] for r in res.rows[1:]] == [1100.0, 1070.0]
    assert any("plain journal entries" in n and "system-generated" in n for n in res.notes)


def test_unknown_account_code_gives_a_clear_error():
    conn = Fake({"account.account": []})
    with pytest.raises(ValueError, match="No account with code"):
        find_account(conn, "999999")
