from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from ..connection import OdooConnection
from ..models import CheckResult, Options
from ._common import chunks, company_domain, m2o_id, m2o_name


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


NAME = "bill_vs_po"
TITLE = "Vendor bills that differ from their purchase order"
DESCRIPTION = (
    "Posted vendor bill lines whose unit price differs from the purchase order line, "
    "and PO lines billed for more than the ordered quantity."
)
COLUMNS = ["issue", "bill", "purchase_order", "product", "bill_price", "po_price", "diff_pct", "qty_ordered", "qty_billed"]

EPS = 1e-6


def price_issue(bill_price: float, po_price: float, tolerance_pct: float) -> Optional[float]:
    """Return the % difference if it exceeds the tolerance, else None."""
    if po_price == 0:
        return 100.0 if abs(bill_price) > EPS else None
    diff_pct = (bill_price - po_price) / abs(po_price) * 100.0
    return round(diff_pct, 2) if abs(diff_pct) > tolerance_pct else None


def qty_issue(qty_billed: float, qty_ordered: float) -> bool:
    return qty_billed - qty_ordered > EPS


def run(conn: OdooConnection, opts: Options) -> CheckResult:
    res = CheckResult(NAME, TITLE, DESCRIPTION, COLUMNS)

    if not conn.has_field("account.move.line", "purchase_line_id"):
        res.skipped_reason = "Purchase module not installed (no purchase_line_id on bill lines)."
        return res

    since = (_utcnow() - timedelta(days=opts.since_days)).strftime("%Y-%m-%d")
    bill_lines = conn.search_read_all(
        "account.move.line",
        [
            ("purchase_line_id", "!=", False),
            ("parent_state", "=", "posted"),
            ("move_id.move_type", "=", "in_invoice"),
            ("date", ">=", since),
        ]
        + company_domain(conn),
        ["move_id", "product_id", "price_unit", "purchase_line_id", "currency_id"],
    )
    res.checked = len(bill_lines)

    po_ids = list({m2o_id(l["purchase_line_id"]) for l in bill_lines})
    po_lines = {}
    for part in chunks(po_ids, 500):
        for pl in conn.read(
            "purchase.order.line",
            part,
            ["order_id", "product_id", "price_unit", "product_qty", "qty_invoiced", "currency_id"],
        ):
            po_lines[pl["id"]] = pl

    skipped_currency = 0
    for line in bill_lines:
        po = po_lines.get(m2o_id(line["purchase_line_id"]))
        if not po:
            continue
        if m2o_id(line.get("currency_id")) != m2o_id(po.get("currency_id")):
            skipped_currency += 1
            continue
        diff = price_issue(float(line["price_unit"]), float(po["price_unit"]), opts.tolerance_pct)
        if diff is not None:
            res.rows.append(
                {
                    "issue": "price_differs",
                    "bill": m2o_name(line["move_id"]),
                    "purchase_order": m2o_name(po["order_id"]),
                    "product": m2o_name(line.get("product_id")),
                    "bill_price": line["price_unit"],
                    "po_price": po["price_unit"],
                    "diff_pct": diff,
                    "qty_ordered": po["product_qty"],
                    "qty_billed": po["qty_invoiced"],
                }
            )

    seen = set()
    for line in bill_lines:
        pl_id = m2o_id(line["purchase_line_id"])
        po = po_lines.get(pl_id)
        if po and pl_id not in seen and qty_issue(float(po["qty_invoiced"]), float(po["product_qty"])):
            seen.add(pl_id)
            res.rows.append(
                {
                    "issue": "billed_more_than_ordered",
                    "bill": "",
                    "purchase_order": m2o_name(po["order_id"]),
                    "product": m2o_name(po.get("product_id")),
                    "bill_price": "",
                    "po_price": po["price_unit"],
                    "diff_pct": "",
                    "qty_ordered": po["product_qty"],
                    "qty_billed": po["qty_invoiced"],
                }
            )

    if skipped_currency:
        res.notes.append(f"{skipped_currency} bill line(s) skipped: bill and PO currencies differ.")
    res.notes.append("Prices are compared before discounts; a bill discount can show up as a price difference.")
    if len(res.rows) > opts.max_rows:
        res.rows = res.rows[: opts.max_rows]
        res.truncated = True
    return res
