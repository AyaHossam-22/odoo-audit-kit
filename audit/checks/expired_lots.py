from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..connection import OdooConnection
from ..models import CheckResult, Options
from ._common import chunks, company_domain, m2o_id, m2o_name


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


NAME = "expired_lots"
TITLE = "Expired or soon-to-expire lots still in stock"
DESCRIPTION = "Lots past (or within the warning window of) their expiration date that still have stock on hand."
COLUMNS = ["status", "lot", "product", "expiration_date", "days_left", "location", "quantity"]

DT_FORMAT = "%Y-%m-%d %H:%M:%S"


def classify(expiration: str, now: datetime, warn_days: int) -> Optional[str]:
    """'expired', 'expiring_soon', or None when the lot is outside the warning window."""
    expires = datetime.strptime(expiration[:19], DT_FORMAT)
    if expires < now:
        return "expired"
    if expires <= now + timedelta(days=warn_days):
        return "expiring_soon"
    return None


def run(conn: OdooConnection, opts: Options) -> CheckResult:
    res = CheckResult(NAME, TITLE, DESCRIPTION, COLUMNS)

    if not conn.has_field("stock.lot", "expiration_date"):
        res.skipped_reason = "Expiration dates not enabled (product_expiry module not installed)."
        return res

    now = _utcnow()
    horizon = (now + timedelta(days=opts.warn_days)).strftime(DT_FORMAT)
    lots = conn.search_read_all(
        "stock.lot",
        [("expiration_date", "!=", False), ("expiration_date", "<=", horizon)] + company_domain(conn),
        ["name", "product_id", "expiration_date"],
    )
    lot_info = {l["id"]: l for l in lots}
    res.checked = len(lots)

    for part in chunks(list(lot_info), 1000):
        quants = conn.search_read_all(
            "stock.quant",
            [("lot_id", "in", part), ("location_id.usage", "=", "internal"), ("quantity", ">", 0)],
            ["lot_id", "location_id", "quantity"],
        )
        for q in quants:
            lot = lot_info[m2o_id(q["lot_id"])]
            status = classify(lot["expiration_date"], now, opts.warn_days)
            if status:
                expires = datetime.strptime(lot["expiration_date"][:19], DT_FORMAT)
                res.rows.append(
                    {
                        "status": status,
                        "lot": lot["name"],
                        "product": m2o_name(lot["product_id"]),
                        "expiration_date": lot["expiration_date"],
                        "days_left": math.ceil((expires - now).total_seconds() / 86400),
                        "location": m2o_name(q["location_id"]),
                        "quantity": q["quantity"],
                    }
                )
    res.rows.sort(key=lambda r: r["days_left"])
    if len(res.rows) > opts.max_rows:
        res.rows = res.rows[: opts.max_rows]
        res.truncated = True
    return res
