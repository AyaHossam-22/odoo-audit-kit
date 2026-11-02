from __future__ import annotations

import xmlrpc.client
from typing import Dict, Optional, Tuple

from ..connection import OdooConnection
from ..models import CheckResult, Options
from ._common import company_domain, m2o_id

NAME = "trial_balance"
TITLE = "Trial balance sanity"
DESCRIPTION = (
    "Checks that total debits equal total credits, and lists accounts whose balance sits on the "
    "unusual side for their type (for example an asset with a credit balance). These are leads, "
    "not errors: advances, contra accounts and reversals can all be legitimate."
)
COLUMNS = ["account", "type", "debit", "credit", "balance", "flag"]


def abnormal_side(account_type: str, balance: float, tolerance: float) -> Optional[str]:
    """Reason when the balance is on the unusual side for this account type, else None."""
    if account_type.startswith("asset") and balance < -tolerance:
        return "asset with a credit balance"
    if account_type.startswith("liability") and balance > tolerance:
        return "liability with a debit balance"
    if account_type.startswith("equity") and balance > tolerance:
        return "equity with a debit balance"
    if account_type.startswith("income") and balance > tolerance:
        return "income with a net debit balance"
    if account_type.startswith("expense") and balance < -tolerance:
        return "expense with a net credit balance"
    return None


def sums_by_account(conn: OdooConnection, domain) -> Dict[int, Tuple[float, float]]:
    """{account_id: (debit, credit)} for posted lines, grouped on the server when possible."""
    try:
        groups = conn.call(
            "account.move.line",
            "read_group",
            [domain, ["debit:sum", "credit:sum"], ["account_id"]],
            {"lazy": False},
        )
        return {
            m2o_id(g["account_id"]): (float(g.get("debit") or 0.0), float(g.get("credit") or 0.0))
            for g in groups
            if g.get("account_id")
        }
    except xmlrpc.client.Fault:
        totals: Dict[int, Tuple[float, float]] = {}
        for line in conn.search_read_all("account.move.line", domain, ["account_id", "debit", "credit"]):
            acc = m2o_id(line["account_id"])
            d, c = totals.get(acc, (0.0, 0.0))
            totals[acc] = (d + float(line.get("debit") or 0.0), c + float(line.get("credit") or 0.0))
        return totals


def run(conn: OdooConnection, opts: Options) -> CheckResult:
    res = CheckResult(NAME, TITLE, DESCRIPTION, COLUMNS)

    domain = [("parent_state", "=", "posted")] + company_domain(conn)
    if opts.as_of:
        domain.append(("date", "<=", opts.as_of))
        res.notes.append(f"As of {opts.as_of}.")

    totals = sums_by_account(conn, domain)
    account_ids = list(totals)
    info = {
        a["id"]: a
        for a in conn.read("account.account", account_ids, ["display_name", "account_type"])
    }
    res.checked = len(account_ids)

    total_debit = round(sum(d for d, _ in totals.values()), 2)
    total_credit = round(sum(c for _, c in totals.values()), 2)
    res.notes.append(f"Total debit {total_debit:,.2f} / total credit {total_credit:,.2f}.")
    if abs(total_debit - total_credit) > opts.tolerance_amount:
        res.rows.append(
            {
                "account": "TOTAL",
                "type": "",
                "debit": total_debit,
                "credit": total_credit,
                "balance": round(total_debit - total_credit, 2),
                "flag": "debits do not equal credits",
            }
        )

    for account_id, (debit, credit) in totals.items():
        acc = info.get(account_id, {})
        balance = round(debit - credit, 2)
        reason = abnormal_side(acc.get("account_type") or "", balance, opts.tolerance_amount)
        if reason:
            res.rows.append(
                {
                    "account": acc.get("display_name", str(account_id)),
                    "type": acc.get("account_type", ""),
                    "debit": round(debit, 2),
                    "credit": round(credit, 2),
                    "balance": balance,
                    "flag": reason,
                }
            )
    res.rows.sort(key=lambda r: (r["account"] != "TOTAL", -abs(r["balance"])))
    if len(res.rows) > opts.max_rows:
        res.rows = res.rows[: opts.max_rows]
        res.truncated = True
    return res
