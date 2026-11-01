from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from ..connection import OdooConnection


def m2o_id(value: Any) -> Optional[int]:
    return value[0] if isinstance(value, (list, tuple)) and value else None


def m2o_name(value: Any) -> str:
    return value[1] if isinstance(value, (list, tuple)) and len(value) > 1 else ""


def chunks(seq: List[Any], size: int) -> Iterable[List[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def company_domain(conn: OdooConnection, field: str = "company_id") -> List[Any]:
    return [(field, "in", [conn.company_id])] if conn.company_id else []


def realtime_categories(conn: OdooConnection) -> Optional[Dict[int, int]]:
    """Map product-category id -> valuation account id, for real-time valued categories.

    Returns None when the accounting-for-stock fields are not available
    (stock_account not installed).
    """
    needed = ("property_valuation", "property_stock_valuation_account_id")
    if not all(conn.has_field("product.category", f) for f in needed):
        return None
    cats = conn.search_read_all("product.category", [], list(needed))
    mapping: Dict[int, int] = {}
    for cat in cats:
        account_id = m2o_id(cat.get("property_stock_valuation_account_id"))
        if cat.get("property_valuation") == "real_time" and account_id:
            mapping[cat["id"]] = account_id
    return mapping
