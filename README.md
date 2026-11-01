# odoo-audit-kit

Read-only Python toolkit that audits an Odoo database over XML-RPC and reports inventory, costing, and accounting anomalies.

Instead of eyeballing reports, you point the toolkit at a **staging copy** of an Odoo database, it asks the system a fixed set of questions that no single report answers, and it hands you back **only the anomalies**: one CSV per check plus a single HTML summary.

- **Read-only by design.** The connection layer whitelists `search`, `search_read`, `read`, `search_count`, `fields_get` and `read_group`. Any other method (`write`, `create`, `unlink`, `action_*`...) raises an error *before* a request is sent. This is covered by tests.
- **Standard library only.** No dependencies to install (Python 3.9+).
- **Version-aware.** Fields and models are detected at runtime with `fields_get`, so a check that does not apply (module not installed, field renamed) is reported as *skipped* instead of crashing.

## What it checks

| Check | Question it answers |
|---|---|
| `stock_vs_ledger` | Does the inventory value of real-time valued categories match the posted balance of their stock valuation accounts? |
| `moves_without_entries` | Which done, valued stock moves have no accounting entry? |
| `bill_vs_po` | Which posted vendor bills have a unit price different from the purchase order, or bill more than was ordered? |
| `expired_lots` | Which lots are expired (or expiring within N days) and still have stock on hand? |
| `negative_stock` | Which internal-location quants have a negative quantity? |

The first check reconciles at **valuation-account level** (that is the granularity the ledger has). To find the product behind a gap, start from the account, then use `moves_without_entries` and `bill_vs_po` to trace the operation that broke the match.

## Quick start

```bash
git clone https://github.com/AyaHossam-22/odoo-audit-kit.git
cd odoo-audit-kit

cp .env.example .env      # then fill in URL, DB, user and API key
python run_audit.py
```

Open `audit_output/<db>_<timestamp>/report.html`.

### Options

```bash
python run_audit.py --list                              # list checks
python run_audit.py --checks expired_lots negative_stock
python run_audit.py --since-days 180 --warn-days 45
python run_audit.py --tolerance-pct 1 --tolerance-amount 5
```

| Option | Default | Meaning |
|---|---|---|
| `--since-days` | 365 | Look-back window for stock moves and bills |
| `--warn-days` | 30 | "Expiring soon" horizon for lots |
| `--tolerance-pct` | 0.5 | Allowed % gap between bill price and PO price |
| `--tolerance-amount` | 1.0 | Allowed gap (currency units) between inventory value and ledger |
| `--max-rows` | 5000 | Cap on anomaly rows per check |
| `--out` | `audit_output` | Output directory |

Multi-company database? Set `ODOO_COMPANY_ID` in `.env`.

## Safety

- **Run it on staging, or on a read-only user.** Create an Odoo user with read access only and an API key for it.
- Reports can contain client data. `audit_output/` and `.env` are in `.gitignore`; keep it that way.
- CSV cells that start with `=`, `+`, `-` or `@` are prefixed with `'` so opening a report in Excel cannot execute a formula coming from the database.

## Compatibility

Designed for Odoo **17, 18 and 19** (Community and Enterprise). Notes on version differences:

- Inventory valuation uses `stock.valuation.layer` when the model exists, then `product.total_value` (Odoo 19). Where neither exists, it falls back to *quantity x current cost*, which is exact for average cost but only an approximation for FIFO. The report says which source was used.
- The "no accounting entry" check depends on `stock.move.account_move_ids`; if it is missing, the check is skipped and says so.
- Expiry checks need the `product_expiry` module; bill checks need `purchase`.

Tested against:

| Odoo version | Edition | Status |
|---|---|---|
| 17 | | not yet recorded |
| 18 | | not yet recorded |
| 19 | Enterprise (19.0+e) | Connects and runs. 3 of 5 checks ran on a real database; `moves_without_entries` is skipped (no `stock.move.account_move_ids` on 19, support planned) and `expired_lots` is skipped when `product_expiry` is not installed |

Ran it on a version and hit an error? Open an issue with the Odoo version and the message.

## Limitations

- Bill vs PO compares unit prices before discounts, and skips lines where the bill and PO currencies differ.
- Categories using periodic (manual) valuation are excluded from the ledger checks, since they do not create entries per move by design.
- Moves between two internal locations are not valued moves and are excluded.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

## License

MIT
