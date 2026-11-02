from __future__ import annotations

import csv
import html
import os
from datetime import datetime
from typing import Any, Dict, List

from .models import CheckResult

_FORMULA_PREFIXES = ("=", "+", "-", "@")


def safe_cell(value: Any) -> Any:
    """Neutralise spreadsheet formula injection for text coming from the database."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def write_csv(result: CheckResult, out_dir: str) -> str:
    path = os.path.join(out_dir, f"{result.name}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:  # utf-8-sig: Excel opens Arabic correctly
        writer = csv.DictWriter(fh, fieldnames=result.columns, extrasaction="ignore")
        writer.writeheader()
        for row in result.rows:
            writer.writerow({k: safe_cell(v) for k, v in row.items()})
    return path


_CSS = """
body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:2rem auto;max-width:1100px;padding:0 1rem;color:#1c1e21}
h1{margin-bottom:.2rem} .meta{color:#555;margin-bottom:1.5rem}
section{border:1px solid #ddd;border-radius:8px;padding:1rem 1.2rem;margin:1rem 0}
.badge{display:inline-block;padding:.1rem .6rem;border-radius:999px;font-size:.8rem;font-weight:600}
.clean{background:#d9f2e0;color:#14532d}.anomalies{background:#fde2e1;color:#7f1d1d}
.skipped{background:#e5e7eb;color:#374151}.listing{background:#dbeafe;color:#1e3a8a}.error{background:#fef3c7;color:#78350f}
table{border-collapse:collapse;width:100%;font-size:.88rem;margin-top:.6rem}
th,td{border-bottom:1px solid #eee;padding:.35rem .5rem;text-align:left}th{background:#f6f7f9}
.note{color:#555;font-size:.85rem}
"""


def write_html(results: List[CheckResult], meta: Dict[str, str], out_dir: str) -> str:
    esc = html.escape
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'><title>Odoo audit report</title>",
        f"<style>{_CSS}</style></head><body>",
        "<h1>Odoo audit report</h1>",
        "<div class='meta'>" + " &middot; ".join(f"{esc(k)}: {esc(v)}" for k, v in meta.items()) + "</div>",
    ]
    for r in results:
        detail = f"{len(r.rows)} anomalies / {r.checked} checked" if r.status in ("clean", "anomalies") else (f"{len(r.rows)} lines" if r.status == "listing" else "")
        parts.append(
            f"<section><h2>{esc(r.title)} <span class='badge {r.status}'>{esc(r.status)}</span></h2>"
            f"<div class='note'>{esc(r.description)} {esc(detail)}</div>"
        )
        if r.skipped_reason:
            parts.append(f"<p class='note'>Skipped: {esc(r.skipped_reason)}</p>")
        if r.error:
            parts.append(f"<p class='note'>Error: {esc(r.error)}</p>")
        for note in r.notes:
            parts.append(f"<p class='note'>Note: {esc(note)}</p>")
        if r.truncated:
            parts.append("<p class='note'>Output truncated to the row cap; see --max-rows.</p>")
        if r.rows:
            parts.append("<table><tr>" + "".join(f"<th>{esc(c)}</th>" for c in r.columns) + "</tr>")
            for row in r.rows[:200]:
                parts.append("<tr>" + "".join(f"<td>{esc(str(row.get(c, '')))}</td>" for c in r.columns) + "</tr>")
            parts.append("</table>")
            if len(r.rows) > 200:
                parts.append(f"<p class='note'>Showing 200 of {len(r.rows)} rows; the CSV has all of them.</p>")
        parts.append("</section>")
    parts.append("</body></html>")
    path = os.path.join(out_dir, "report.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(parts))
    return path


def make_out_dir(base: str, db: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_db = "".join(c if c.isalnum() or c in "-_" else "_" for c in db)
    path = os.path.join(base, f"{safe_db}_{stamp}")
    os.makedirs(path, exist_ok=True)
    return path
