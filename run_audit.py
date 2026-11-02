#!/usr/bin/env python3
"""Run the read-only Odoo audit.

    python run_audit.py                      # all checks
    python run_audit.py --checks expired_lots negative_stock
    python run_audit.py --list
"""
from __future__ import annotations

import argparse
import os
import sys

from audit import __version__
from audit.checks import ALL_CHECKS
from audit.connection import OdooConnection, load_env
from audit.models import CheckResult, Options
from audit.report import make_out_dir, write_csv, write_html


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Read-only audit of an Odoo database over XML-RPC.")
    p.add_argument("--checks", nargs="+", choices=sorted(ALL_CHECKS), help="Run only these checks.")
    p.add_argument("--list", action="store_true", help="List available checks and exit.")
    p.add_argument("--since-days", type=int, default=365, help="Look-back window for moves and bills (default 365).")
    p.add_argument("--warn-days", type=int, default=30, help="'Expiring soon' horizon in days (default 30).")
    p.add_argument("--tolerance-pct", type=float, default=0.5, help="Bill vs PO price tolerance in %% (default 0.5).")
    p.add_argument("--tolerance-amount", type=float, default=1.0, help="Stock vs ledger tolerance in currency units (default 1.0).")
    p.add_argument("--as-of", metavar="YYYY-MM-DD", help="Cut-off date for the trial balance check.")
    p.add_argument("--account", metavar="CODE", help="Review one account: list its ledger lines with a running balance.")
    p.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", help="Account review: start date (opening balance is computed before it).")
    p.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", help="Account review: end date.")
    p.add_argument("--max-rows", type=int, default=5000, help="Cap on anomaly rows per check (default 5000).")
    p.add_argument("--out", default="audit_output", help="Output base directory (default audit_output).")
    p.add_argument("--env-file", default=".env", help="Path to the .env file (default .env).")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.list:
        for name, module in ALL_CHECKS.items():
            print(f"{name:24s} {module.TITLE}")
        return 0

    load_env(args.env_file)
    missing = [k for k in ("ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY") if not os.environ.get(k)]
    if missing:
        print(f"Missing settings: {', '.join(missing)}. Copy .env.example to .env and fill it in.", file=sys.stderr)
        return 2

    company = os.environ.get("ODOO_COMPANY_ID")
    conn = OdooConnection(
        os.environ["ODOO_URL"],
        os.environ["ODOO_DB"],
        os.environ["ODOO_USER"],
        os.environ["ODOO_API_KEY"],
        company_id=int(company) if company else None,
    )
    try:
        conn.connect()
    except Exception as exc:  # network, auth, wrong URL...
        print(f"Could not connect: {exc}", file=sys.stderr)
        return 1

    opts = Options(
        since_days=args.since_days,
        warn_days=args.warn_days,
        tolerance_pct=args.tolerance_pct,
        tolerance_amount=args.tolerance_amount,
        max_rows=args.max_rows,
        as_of=args.as_of,
    )
    if args.account:
        from audit.account_review import run_account_review

        try:
            review = run_account_review(conn, opts, args.account, args.date_from, args.date_to)
        except Exception as exc:
            print(f"Account review failed: {exc}", file=sys.stderr)
            return 1
        out_dir = make_out_dir(args.out, conn.db)
        write_csv(review, out_dir)
        report = write_html([review], {"Odoo": conn.version, "Database": conn.db, "Toolkit": __version__}, out_dir)
        print(f"Connected to Odoo {conn.version} (read-only). {review.title}: {review.checked} line(s)")
        for note in review.notes:
            print(f"  {note}")
        print(f"\nLedger CSV: {os.path.join(out_dir, review.name + '.csv')}\nReport: {report}")
        return 0

    selected = args.checks or list(ALL_CHECKS)
    print(f"Connected to Odoo {conn.version} (read-only). Running {len(selected)} check(s)...\n")

    out_dir = make_out_dir(args.out, conn.db)
    results = []
    for name in selected:
        module = ALL_CHECKS[name]
        try:
            result = module.run(conn, opts)
        except Exception as exc:  # keep going: one failing check must not hide the others
            result = CheckResult(module.NAME, module.TITLE, module.DESCRIPTION, module.COLUMNS, error=str(exc))
        if result.status == "anomalies":
            write_csv(result, out_dir)
        results.append(result)
        detail = f"{len(result.rows)}/{result.checked}" if result.status in ("clean", "anomalies") else (
            result.skipped_reason or result.error or ""
        )
        print(f"  [{result.status.upper():9s}] {result.title}  {detail}")

    meta = {"Odoo": conn.version, "Database": conn.db, "Toolkit": __version__}
    report = write_html(results, meta, out_dir)
    print(f"\nReport: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
