from __future__ import annotations

from collections import Counter
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
COLUMNS = ["move", "date", "product", "quantity", "from", "to", "operation", "unit_cost", "note"]

# One side internal, the other not: these are the moves that change company stock value.
VALUED_MOVE_DOMAIN = [
    "|",
    "&", ("location_id.usage", "!=", "internal"), ("location_dest_id.usage", "=", "internal"),
    "&", ("location_id.usage", "=", "internal"), ("location_dest_id.usage", "!=", "internal"),
]

# Field linking a stock move to its journal entry(ies): one2many up to Odoo 18, may differ on 19.
LINK_FIELDS = ("account_move_ids", "account_move_id")


def find_link_field(conn: OdooConnection):
    return next((f for f in LINK_FIELDS if conn.is_searchable("stock.move", f)), None)


def run(conn: OdooConnection, opts: Options) -> CheckResult:
    res = CheckResult(NAME, TITLE, DESCRIPTION, COLUMNS)

    link_field = find_link_field(conn)
    if not link_field:
        hints = sorted(f for f in conn.fields("stock.move") if "account" in f or "valu" in f)
        res.skipped_reason = (
            "stock.move has none of " + ", ".join(LINK_FIELDS) + " on this version. "
            "Possibly related fields: " + (", ".join(hints) if hints else "none found")
        )
        return res
    categories = realtime_categories(conn)
    if not categories:
        res.skipped_reason = "No product category uses real-time (automated) valuation."
        return res

    since = (_utcnow() - timedelta(days=opts.since_days)).strftime("%Y-%m-%d %H:%M:%S")
    qty_field = "quantity" if conn.has_field("stock.move", "quantity") else "quantity_done"

    # Which moves count as valued. Prefer stored flags (Odoo 19); older versions: infer from locations.
    if conn.is_searchable("stock.move", "is_valued"):
        valued = [("is_valued", "=", True)]
    elif conn.is_searchable("stock.move", "is_in") and conn.is_searchable("stock.move", "is_out"):
        valued = ["|", ("is_in", "=", True), ("is_out", "=", True)]
    else:
        valued = VALUED_MOVE_DOMAIN

    base = [
        ("state", "=", "done"),
        ("date", ">=", since),
        ("product_id.categ_id", "in", list(categories)),
    ] + valued + company_domain(conn)

    res.checked = int(conn.call("stock.move", "search_count", [base]))
    moves = conn.search_read_all(
        "stock.move",
        base + [(link_field, "=", False)],
        ["reference", "date", "product_id", qty_field, "location_id", "location_dest_id", "picking_type_id"],
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
                "operation": m2o_name(m.get("picking_type_id")),
                "unit_cost": cost,
                "note": "zero cost: no entry expected" if cost == 0 else "",
            }
        )
    if res.rows:
        routes = Counter((r["from"], r["to"]) for r in res.rows).most_common(5)
        res.notes.append(
            "Most frequent routes: " + "; ".join(f"{a} -> {b}: {n}" for (a, b), n in routes) + ". "
            "Moves from point-of-sale and similar sources often post one consolidated entry per session, "
            "so they can show up here without being a real gap; check the operation column."
        )
    return res
