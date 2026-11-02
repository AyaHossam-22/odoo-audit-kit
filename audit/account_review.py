"""General-ledger review of a single account (read-only).

    python run_audit.py --account 112300 --from 2026-01-01 --to 2026-10-31

Lists every posted line with a running balance, starting from the opening balance
before --from, so the account can be walked line by line or filtered in Excel.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from .connection import OdooConnection
from .checks._common import chunks, company_domain, m2o_id, m2o_name
from .models import CheckResult, Options

COLUMNS = ["date", "entry", "journal", "entry_type", "partner", "label", "debit", "credit", "running_balance"]


def running_balances(opening: float, lines: List[Dict[str, Any]]) -> List[float]:
    """Running balance after each line, in the order given."""
    out, balance = [], opening
    for line in lines:
        balance = round(balance + float(line.get("debit") or 0.0) - float(line.get("credit") or 0.0), 2)
        out.append(balance)
    return out


def find_account(conn: OdooConnection, code: str) -> Tuple[int, str, str]:
    """(id, display_name, account_type) for an exact account code."""
    found = conn.call(
        "account.account",
        "search_read",
        [[("code", "=", code)]],
        {"fields": ["display_name", "account_type"], "limit": 2},
    )
    if not found:
        raise ValueError(f"No account with code '{code}'. Use the exact code shown in the chart of accounts.")
    return found[0]["id"], found[0]["display_name"], found[0].get("account_type") or ""


def run_account_review(
    conn: OdooConnection,
    opts: Options,
    code: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> CheckResult:
    account_id, display_name, account_type = find_account(conn, code)
    safe_code = re.sub(r"[^A-Za-z0-9_-]", "_", code)
    res = CheckResult(
        f"account_{safe_code}",
        f"Account review: {display_name}",
        "Posted journal items of one account with a running balance.",
        COLUMNS,
        listing=True,
    )
    base = [("account_id", "=", account_id), ("parent_state", "=", "posted")] + company_domain(conn)

    opening = 0.0
    if date_from:
        for line in conn.search_read_all("account.move.line", base + [("date", "<", date_from)], ["debit", "credit"]):
            opening += float(line.get("debit") or 0.0) - float(line.get("credit") or 0.0)
        opening = round(opening, 2)

    domain = list(base)
    if date_from:
        domain.append(("date", ">=", date_from))
    if date_to:
        domain.append(("date", "<=", date_to))

    lines = conn.search_read_all(
        "account.move.line",
        domain,
        ["date", "move_id", "journal_id", "partner_id", "name", "debit", "credit"],
        limit=opts.max_rows + 1,
    )
    if len(lines) > opts.max_rows:
        lines = lines[: opts.max_rows]
        res.truncated = True
    lines.sort(key=lambda l: (l["date"], l["id"]))
    res.checked = len(lines)

    move_ids = list({m2o_id(l["move_id"]) for l in lines if l.get("move_id")})
    move_type: Dict[int, str] = {}
    for part in chunks(move_ids, 500):
        for m in conn.read("account.move", part, ["move_type"]):
            move_type[m["id"]] = m.get("move_type") or ""

    balances = running_balances(opening, lines)
    if date_from:
        res.rows.append(
            {"date": date_from, "entry": "OPENING BALANCE", "journal": "", "entry_type": "", "partner": "",
             "label": "", "debit": "", "credit": "", "running_balance": opening}
        )
    for line, balance in zip(lines, balances):
        res.rows.append(
            {
                "date": line["date"],
                "entry": m2o_name(line["move_id"]),
                "journal": m2o_name(line["journal_id"]),
                "entry_type": move_type.get(m2o_id(line["move_id"]), ""),
                "partner": m2o_name(line.get("partner_id")),
                "label": line.get("name") or "",
                "debit": line["debit"],
                "credit": line["credit"],
                "running_balance": balance,
            }
        )

    total_debit = round(sum(float(l.get("debit") or 0.0) for l in lines), 2)
    total_credit = round(sum(float(l.get("credit") or 0.0) for l in lines), 2)
    closing = balances[-1] if balances else opening
    period = f"{date_from or 'start'} to {date_to or 'today'}"
    res.notes.append(f"Account type: {account_type}. Period: {period}.")
    res.notes.append(
        f"Opening {opening:,.2f}, debits {total_debit:,.2f}, credits {total_credit:,.2f}, closing {closing:,.2f}."
    )
    kinds = Counter(move_type.get(m2o_id(l["move_id"]), "?") for l in lines)
    if kinds:
        res.notes.append("Lines by entry type: " + ", ".join(f"{k}: {n}" for k, n in kinds.most_common()))
        if kinds.get("entry"):
            res.notes.append(
                f"{kinds['entry']} line(s) come from plain journal entries (entry_type = entry). These are manual "
                "entries but also system-generated ones such as stock valuation and point-of-sale postings, "
                "so check the journal and label columns before treating them as manual."
            )
    return res
