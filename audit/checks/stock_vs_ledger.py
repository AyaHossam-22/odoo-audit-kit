from __future__ import annotations

from collections import defaultdict
from typing import Dict

from ..connection import OdooConnection
from ..models import CheckResult, Options
from ._common import company_domain, m2o_id, realtime_categories

NAME = "stock_vs_ledger"
TITLE = "Inventory valuation vs. general ledger"
DESCRIPTION = (
    "Compares the inventory value of real-time valued product categories with the "
    "posted balance of their stock valuation accounts."
)
COLUMNS = ["account", "inventory_value", "ledger_balance", "difference", "valuation_source"]


def run(conn: OdooConnection, opts: Options) -> CheckResult:
    res = CheckResult(NAME, TITLE, DESCRIPTION, COLUMNS)

    categories = realtime_categories(conn)
    if categories is None:
        res.skipped_reason = "stock_account fields not found (Inventory valuation not installed)."
        return res
    if not categories:
        res.skipped_reason = "No product category uses real-time (automated) valuation."
        return res

    cat_ids = list(categories)
    has_total_value = conn.has_field("product.product", "total_value")  # Odoo 19+
    products = conn.search_read_all(
        "product.product",
        [("categ_id", "in", cat_ids)],
        ["categ_id", "standard_price"] + (["total_value"] if has_total_value else []),
        context={"active_test": False},
    )
    product_account: Dict[int, int] = {}
    product_cost: Dict[int, float] = {}
    product_value: Dict[int, float] = {}
    for p in products:
        product_account[p["id"]] = categories[m2o_id(p["categ_id"])]
        product_cost[p["id"]] = float(p.get("standard_price") or 0.0)
        product_value[p["id"]] = float(p.get("total_value") or 0.0)

    inventory: Dict[int, float] = defaultdict(float)
    if conn.has_model("stock.valuation.layer") and conn.has_field("stock.valuation.layer", "value"):
        source = "stock.valuation.layer"
        layers = conn.search_read_all(
            "stock.valuation.layer",
            [("product_id", "in", list(product_account))] + company_domain(conn),
            ["product_id", "value"],
        )
        for layer in layers:
            account_id = product_account.get(m2o_id(layer["product_id"]))
            if account_id:
                inventory[account_id] += float(layer.get("value") or 0.0)
    elif has_total_value:
        source = "product.total_value"
        for pid, value in product_value.items():
            inventory[product_account[pid]] += value
    else:
        source = "quantity x standard_price (approximation)"
        res.notes.append(
            "No valuation layers on this version: inventory value is quantity x current cost, "
            "which is exact for average cost but only approximate for FIFO."
        )
        quants = conn.search_read_all(
            "stock.quant",
            [("location_id.usage", "=", "internal"), ("product_id", "in", list(product_account))]
            + company_domain(conn),
            ["product_id", "quantity"],
        )
        for quant in quants:
            pid = m2o_id(quant["product_id"])
            inventory[product_account[pid]] += float(quant.get("quantity") or 0.0) * product_cost.get(pid, 0.0)

    account_ids = sorted(set(categories.values()))
    ledger: Dict[int, float] = defaultdict(float)
    lines = conn.search_read_all(
        "account.move.line",
        [("account_id", "in", account_ids), ("parent_state", "=", "posted")] + company_domain(conn),
        ["account_id", "balance"],
    )
    for line in lines:
        ledger[m2o_id(line["account_id"])] += float(line.get("balance") or 0.0)

    names = {a["id"]: a["display_name"] for a in conn.read("account.account", account_ids, ["display_name"])}

    res.checked = len(account_ids)
    for account_id in account_ids:
        inv = round(inventory.get(account_id, 0.0), 2)
        led = round(ledger.get(account_id, 0.0), 2)
        diff = round(inv - led, 2)
        if abs(diff) > opts.tolerance_amount:
            res.rows.append(
                {
                    "account": names.get(account_id, str(account_id)),
                    "inventory_value": inv,
                    "ledger_balance": led,
                    "difference": diff,
                    "valuation_source": source,
                }
            )
    return res
