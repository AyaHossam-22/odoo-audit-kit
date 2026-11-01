from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..connection import OdooConnection
from ..models import CheckResult, Options
from ._common import chunks, company_domain, m2o_name, m2o_id, realtime_categories


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


NAME = "moves_without_entries"
TITLE = "Done stock moves without an accounting entry"
DESCRIPTION = (
    "Valued stock moves (in/out of internal locations) in real-time valued categories "
    "that are done but have no journal entry linked."
)
COLUMNS = ["move", "date", "product", "quantity", "from", "to", "unit_cost", "note"]

# One side internal, the other not: these are the moves that change company stock value.
VALUED_MOVE_DOMAIN = [
    "|",
    "&", ("location_id.usage", "!=", "internal"), ("location_dest_id.usage", "=", "internal"),
    "&", ("location_id.usage", "=", "internal"), ("location_dest_id.usage", "!=", "internal"),
]


def run(conn: OdooConnection, opts: Options) -> CheckResult:
    res = CheckResult(NAME, TITLE, DESCRIPTION, COLUMNS)

    if not conn.has_field("stock.move", "account_move_ids"):
        res.skipped_reason = "stock.move has no 'account_move_ids' field on this version/setup."
        return res
    categories = realtime_categories(conn)
    if not categories:
        res.skipped_reason = "No product category uses real-time (automated) valuation."
        return res

    since = (_utcnow() - timedelta(days=opts.since_days)).strftime("%Y-%m-%d %H:%M:%S")
    qty_field = "quantity" if conn.has_field("stock.move", "quantity") else "quantity_done"

    base = [
        ("state", "=", "done"),
        ("date", ">=", since),
        ("product_id.categ_id", "in", list(categories)),
    ] + VALUED_MOVE_DOMAIN + company_domain(conn)

    res.checked = int(conn.call("stock.move", "search_count", [base]))
    moves = conn.search_read_all(
        "stock.move",
        base + [("account_move_ids", "=", False)],
        ["reference", "date", "product_id", qty_field, "location_id", "location_dest_id"],
        limit=opts.max_rows + 1,
    )
    if len(moves) > opts.max_rows:
        moves = moves[: opts.max_rows]
        res.truncated = True

    product_ids = list({m2o_id(m["product_id"]) for m in moves if m.get("product_id")})
    costs = {}
    for part in chunks(product_ids, 500):
        for p in conn.read("product.product", part, ["standard_price"]):
            costs[p["id"]] = float(p.get("standard_price") or 0.0)

    for m in moves:
        cost = costs.get(m2o_id(m["product_id"]), 0.0)
        res.rows.append(
            {
                "move": m.get("reference") or m["id"],
                "date": m.get("date"),
                "product": m2o_name(m["product_id"]),
                "quantity": m.get(qty_field),
                "from": m2o_name(m["location_id"]),
                "to": m2o_name(m["location_dest_id"]),
                "unit_cost": cost,
                "note": "zero cost: no entry expected" if cost == 0 else "",
            }
        )
    return res
