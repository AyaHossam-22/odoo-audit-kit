# Demo sandbox (experimental)

A throwaway local Odoo with deliberately broken data, so you can see the audit work and take a clean screenshot without touching client data.

> **This folder is the only place in the repo that writes to Odoo.** `seed_demo_data.py` refuses to run against anything but `localhost`. The audit toolkit itself stays read-only.

Status: written but **not yet verified end to end** on every Odoo version. Each seeding step is independent and reports `ok` / `FAILED`, so a failing step does not stop the rest.

## 1. Start Odoo

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/). From the `demo/` folder:

```bash
# Create the database with demo data and the needed apps (takes a few minutes)
docker compose run --rm odoo odoo -d demo -i base,stock,purchase,account,stock_account,purchase_stock,product_expiry --stop-after-init

# Start Odoo
docker compose up -d
```

Odoo is now at http://localhost:8069 (login `admin`, password `admin`). To use another version: `ODOO_VERSION=17.0 docker compose ...`.

## 2. Seed the problems

From the repo root:

```bash
python demo/seed_demo_data.py
```

It creates, in a real-time valued category and a periodic one:

| Planted problem | Caught by |
|---|---|
| A lot already expired and one expiring in 7 days, both in stock | `expired_lots` |
| 25 units delivered with 10 in stock | `negative_stock` |
| A vendor bill 25% above the PO price, billing 14 against 10 ordered | `bill_vs_po` |
| Receipts done under periodic valuation, then the category switched to real-time | `moves_without_entries` |
| A manual 500 entry on the stock valuation account | `stock_vs_ledger` |

## 3. Run the audit

```bash
# .env
ODOO_URL=http://localhost:8069
ODOO_DB=demo
ODOO_USER=admin
ODOO_API_KEY=admin

python run_audit.py
```

Open `audit_output/demo_<timestamp>/report.html`.

## Clean up

```bash
docker compose down -v   # removes the sandbox and its data
```
