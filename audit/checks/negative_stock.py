from __future__ import annotations

from ..connection import OdooConnection
from ..models import CheckResult, Options
from ._common import company_domain, m2o_name

NAME = "negative_stock"
TITLE = "Negative quantities in internal locations"
DESCRIPTION = "Stock quants with a negative on-hand quantity, usually a sign of missing receipts or wrong operations."
COLUMNS = ["product", "lot", "location", "quantity"]


def run(conn: OdooConnection, opts: Options) -> CheckResult:
    res = CheckResult(NAME, TITLE, DESCRIPTION, COLUMNS)
    if not conn.has_model("stock.quant"):
        res.skipped_reason = "Inventory app not installed."
        return res

    res.checked = int(
        conn.call("stock.quant", "search_count", [[("location_id.usage", "=", "internal")] + company_domain(conn)])
    )
    quants = conn.search_read_all(
        "stock.quant",
        [("location_id.usage", "=", "internal"), ("quantity", "<", 0)] + company_domain(conn),
        ["product_id", "lot_id", "location_id", "quantity"],
        limit=opts.max_rows + 1,
    )
    if len(quants) > opts.max_rows:
        quants = quants[: opts.max_rows]
        res.truncated = True
    for q in quants:
        res.rows.append(
            {
                "product": m2o_name(q["product_id"]),
                "lot": m2o_name(q.get("lot_id")),
                "location": m2o_name(q["location_id"]),
                "quantity": q["quantity"],
            }
        )
    return res
