#!/usr/bin/env python3
"""Seed a LOCAL demo Odoo database with deliberate problems for odoo-audit-kit to find.

This is the only script in the repo that writes to Odoo, and it refuses to run
against anything that is not localhost. The audit toolkit itself stays read-only.

    python demo/seed_demo_data.py                 # http://localhost:8069, db "demo", admin/admin

Every step is independent: if one fails (field names differ between Odoo
versions), the error is printed and the remaining steps still run.
"""
from __future__ import annotations

import argparse
import sys
import xmlrpc.client
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
FMT = "%Y-%m-%d %H:%M:%S"


def is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in LOCAL_HOSTS


class Rpc:
    def __init__(self, url, db, user, password):
        self.db, self.password = db, password
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", allow_none=True)
        self.uid = common.authenticate(db, user, password, {})
        if not self.uid:
            raise SystemExit("Authentication failed (database name, user or password).")
        self.obj = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", allow_none=True)
        self._fields = {}

    def x(self, model, method, *args, **kw):
        return self.obj.execute_kw(self.db, self.uid, self.password, model, method, list(args), kw)

    def fields(self, model):
        if model not in self._fields:
            self._fields[model] = self.x(model, "fields_get", attributes=["type"])
        return self._fields[model]

    def has(self, model, field):
        return field in self.fields(model)

    def find(self, model, domain):
        ids = self.x(model, "search", domain, limit=1)
        return ids[0] if ids else None

    def xmlid(self, module, name):
        rec = self.x("ir.model.data", "search_read", [("module", "=", module), ("name", "=", name)], fields=["res_id"], limit=1)
        return rec[0]["res_id"] if rec else None


def step(title, fn, *a, **kw):
    try:
        result = fn(*a, **kw)
        print(f"  ok      {title}")
        return result
    except Exception as exc:  # keep going; print a short reason
        reason = getattr(exc, "faultString", str(exc)).strip().splitlines()
        print(f"  FAILED  {title}: {reason[-1][:160] if reason else exc}")
        return None


# ----------------------------------------------------------------------- setup
def enable_lots(r: Rpc):
    gid = r.xmlid("stock", "group_production_lot")
    field = "group_ids" if r.has("res.users", "group_ids") else "groups_id"
    r.x("res.users", "write", [r.uid], {field: [(4, gid)]})


def accounts(r: Rpc):
    def acc(*names):
        for n in names:
            i = r.find("account.account", [("name", "ilike", n)])
            if i:
                return i
    return {
        "val": acc("Stock Valuation"),
        "inp": acc("Stock Interim (Received)", "Stock Input"),
        "out": acc("Stock Interim (Delivered)", "Stock Output"),
        "journal": r.find("account.journal", [("code", "=", "STJ")]) or r.find("account.journal", [("name", "ilike", "Inventory Valuation")]),
    }


def make_category(r: Rpc, name, valuation_values, acc):
    vals = {
        "name": name,
        "property_cost_method": "average",
        "property_stock_valuation_account_id": acc["val"],
        "property_stock_account_input_categ_id": acc["inp"],
        "property_stock_account_output_categ_id": acc["out"],
        "property_stock_journal": acc["journal"],
    }
    last = None
    for v in valuation_values:
        try:
            return r.x("product.category", "create", dict(vals, property_valuation=v))
        except xmlrpc.client.Fault as exc:
            last = exc
    raise last


def make_product(r: Rpc, name, categ, cost, lot=False):
    vals = {"name": name, "categ_id": categ, "standard_price": cost, "list_price": cost * 1.3}
    if r.has("product.template", "is_storable"):
        vals.update(type="consu", is_storable=True)  # Odoo 18+
    else:
        vals.update(type="product")  # Odoo 17
    if lot:
        vals.update(tracking="lot", use_expiration_date=True, expiration_time=180)
    tmpl = r.x("product.template", "create", vals)
    return r.x("product.product", "search", [("product_tmpl_id", "=", tmpl)], limit=1)[0]


# ------------------------------------------------------------------ operations
def picking(r: Rpc, kind, lines):
    """kind: 'incoming' or 'outgoing'. lines: [(product_id, qty, lot_name or None)]."""
    ptype = r.x("stock.picking.type", "search_read", [("code", "=", kind)], fields=["default_location_src_id", "default_location_dest_id"], limit=1)[0]
    src = ptype["default_location_src_id"][0] if ptype["default_location_src_id"] else None
    dst = ptype["default_location_dest_id"][0] if ptype["default_location_dest_id"] else None
    if kind == "incoming":
        src = src or r.find("stock.location", [("usage", "=", "supplier")])
    else:
        dst = dst or r.find("stock.location", [("usage", "=", "customer")])
    move_field = "move_ids" if r.has("stock.picking", "move_ids") else "move_ids_without_package"
    moves = [
        (0, 0, {"name": "demo", "product_id": pid, "product_uom_qty": qty, "location_id": src, "location_dest_id": dst})
        for pid, qty, _ in lines
    ]
    pid_ = r.x("stock.picking", "create", {"picking_type_id": r.x("stock.picking.type", "search", [("code", "=", kind)], limit=1)[0],
                                          "location_id": src, "location_dest_id": dst, move_field: moves})
    r.x("stock.picking", "action_confirm", [pid_])
    qty_field = "quantity" if r.has("stock.move.line", "quantity") else "qty_done"
    lot_for = {pid: lot for pid, _, lot in lines}
    qty_for = {pid: qty for pid, qty, _ in lines}
    for mv in r.x("stock.move", "search_read", [("picking_id", "=", pid_)], fields=["id", "product_id", "move_line_ids"]):
        product = mv["product_id"][0]
        line_vals = {qty_field: qty_for[product]}
        if lot_for[product]:
            line_vals["lot_name"] = lot_for[product]
        if mv["move_line_ids"]:
            r.x("stock.move.line", "write", mv["move_line_ids"][:1], line_vals)
        else:
            r.x("stock.move.line", "create", dict(line_vals, move_id=mv["id"], picking_id=pid_, product_id=product,
                                                  location_id=src, location_dest_id=dst))
    r.x("stock.picking", "button_validate", [pid_])
    state = r.x("stock.picking", "read", [pid_], fields=["state"])[0]["state"]
    if state != "done":
        raise RuntimeError(f"picking stayed in state '{state}'")
    return pid_


def set_expiry(r: Rpc, lot_name, delta_days):
    lot = r.find("stock.lot", [("name", "=", lot_name)])
    when = (datetime.now(timezone.utc) + timedelta(days=delta_days)).strftime(FMT)
    r.x("stock.lot", "write", [lot], {"expiration_date": when})


def purchase_with_bad_bill(r: Rpc, product_id):
    vendor = r.x("res.partner", "create", {"name": "Demo Vendor"})
    when = datetime.now(timezone.utc).strftime(FMT)
    po = r.x("purchase.order", "create", {"partner_id": vendor, "order_line": [
        (0, 0, {"product_id": product_id, "name": "Demo purchase", "product_qty": 10, "price_unit": 10.0, "date_planned": when})]})
    r.x("purchase.order", "button_confirm", [po])
    r.x("purchase.order", "action_create_invoice", [po])
    bill = r.find("account.move", [("invoice_line_ids.purchase_line_id.order_id", "=", po)])
    line = r.x("account.move.line", "search", [("move_id", "=", bill), ("purchase_line_id", "!=", False)], limit=1)[0]
    # Price 25% higher than the PO and 14 billed against 10 ordered.
    r.x("account.move", "write", [bill], {"invoice_date": when[:10], "invoice_line_ids": [(1, line, {"price_unit": 12.5, "quantity": 14})]})
    r.x("account.move", "action_post", [bill])


def manual_ledger_adjustment(r: Rpc, valuation_account):
    other = r.find("account.account", [("account_type", "=", "expense")]) or r.find("account.account", [("account_type", "=", "asset_current"), ("id", "!=", valuation_account)])
    journal = r.find("account.journal", [("type", "=", "general")])
    move = r.x("account.move", "create", {"move_type": "entry", "journal_id": journal, "line_ids": [
        (0, 0, {"account_id": valuation_account, "debit": 500.0, "credit": 0.0, "name": "Manual adjustment (demo)"}),
        (0, 0, {"account_id": other, "debit": 0.0, "credit": 500.0, "name": "Manual adjustment (demo)"})]})
    r.x("account.move", "action_post", [move])


# ------------------------------------------------------------------------ main
def main() -> int:
    p = argparse.ArgumentParser(description="Seed a LOCAL demo Odoo with problems for odoo-audit-kit.")
    p.add_argument("--url", default="http://localhost:8069")
    p.add_argument("--db", default="demo")
    p.add_argument("--user", default="admin")
    p.add_argument("--password", default="admin")
    a = p.parse_args()

    if not is_local(a.url):
        print(f"Refusing to run: {a.url} is not localhost. This script writes data and is for local sandboxes only.", file=sys.stderr)
        return 2

    r = Rpc(a.url, a.db, a.user, a.password)
    print(f"Connected to {a.url} (db '{a.db}'). Seeding...\n")

    step("enable lots & serial numbers", enable_lots, r)
    acc = step("find stock accounts and journal", accounts, r) or {}
    if not all(acc.get(k) for k in ("val", "inp", "out", "journal")):
        print("  note    stock accounts/journal not all found; real-time valuation steps will likely fail.\n"
              "          Install the Accounting/Invoicing app with a chart of accounts first.")

    real = step("category: Demo Food (real-time valuation)", make_category, r, "Demo Food (real-time)", ["real_time"], acc)
    periodic = step("category: Demo Food (periodic valuation)", make_category, r, "Demo Food (periodic)", ["manual_periodic", "periodic"], acc)

    dates = step("product: Demo Dates (lot + expiry)", make_product, r, "Demo Dates", real, 10.0, True) if real else None
    honey = step("product: Demo Honey", make_product, r, "Demo Honey", real, 20.0) if real else None
    oil = step("product: Demo Olive Oil", make_product, r, "Demo Olive Oil", periodic, 15.0) if periodic else None

    if dates:
        step("receive dates in 3 lots", picking, r, "incoming", [(dates, 8, "DATES-EXPIRED")])
        step("receive dates (expiring soon)", picking, r, "incoming", [(dates, 5, "DATES-SOON")])
        step("receive dates (healthy lot)", picking, r, "incoming", [(dates, 20, "DATES-OK")])
        step("expire lot DATES-EXPIRED", set_expiry, r, "DATES-EXPIRED", -10)
        step("lot DATES-SOON expires in 7 days", set_expiry, r, "DATES-SOON", 7)
        step("lot DATES-OK expires in 120 days", set_expiry, r, "DATES-OK", 120)
    if honey:
        step("receive 10 honey", picking, r, "incoming", [(honey, 10, None)])
        step("deliver 25 honey (negative stock)", picking, r, "outgoing", [(honey, 25, None)])
        step("purchase order + bill with wrong price/qty", purchase_with_bad_bill, r, honey)
    if oil and periodic:
        step("receive olive oil under periodic valuation", picking, r, "incoming", [(oil, 12, None)])
        step("switch that category to real-time (moves keep no entries)",
             lambda: r.x("product.category", "write", [periodic], {"property_valuation": "real_time"}))
    if acc.get("val"):
        step("post a manual 500 entry on the stock valuation account", manual_ledger_adjustment, r, acc["val"])

    print("\nDone. Now run the audit against this database:\n"
          f"  ODOO_URL={a.url}  ODOO_DB={a.db}  ODOO_USER={a.user}  ODOO_API_KEY={a.password}\n"
          "  python run_audit.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
