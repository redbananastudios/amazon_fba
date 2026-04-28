# Strategy: `supplier_pricelist`

**Type:** Supplier-feed-first reseller sourcing
**Status:** Production
**Implementation:** `shared/lib/python/sourcing_engine/`

---

## What this strategy does

Take a supplier's price list. For each row, find the matching Amazon ASIN
by EAN. Calculate profitability at conservative pricing. Emit
SHORTLIST / REVIEW / REJECT verdicts. Output CSV, Excel, and Markdown
reports.

This is the most direct workflow for a reseller with established trade
accounts. You already have the supplier; the question is which products in
their catalogue make money on Amazon.

---

## When to use it

- You have a supplier login and a downloaded price list
- You want to know which lines in that price list to actually order
- You're running known-supplier sourcing, not Amazon-first discovery

For Amazon-first discovery (find good products, then hunt for suppliers),
see the `keepa_niche` strategy.

---

## Inputs

- **Supplier price lists** at `fba_engine/data/pricelists/<supplier>/raw/`
  (CSV, XLSX, XLS, PDF, or HTML — adapter handles the format)
- **Optional Keepa export** with the same ASINs/EANs at the same path,
  passed via `--market-data <file>`. Without this, every row REJECTs as "no
  match."

---

## Run

```bash
# Minimal — uses default paths under fba_engine/data/pricelists/<supplier>/
python run.py --supplier connect-beauty

# Full
python run.py \
    --supplier abgee \
    --input  fba_engine/data/pricelists/abgee/raw/ \
    --output fba_engine/data/pricelists/abgee/results/ \
    --market-data fba_engine/data/pricelists/abgee/raw/keepa_combined.csv
```

---

## Outputs

In `fba_engine/data/pricelists/<supplier>/results/<timestamp>/`:

- `shortlist_<ts>.csv` — every row, every decision (audit trail)
- `shortlist_<ts>.xlsx` — SHORTLIST + REVIEW only, colour-coded
- `report_<ts>.md` — per-supplier markdown tables

---

## Decision logic

See `docs/SPEC.md` sections 3-4. In short:

- SHORTLIST: profitable at conservative price, ROI ≥ target, sales ≥ 20/mo, no hard blockers
- REVIEW: profitable but flagged, OR conservative-only profitable, OR low sales (10-19), OR gating UNKNOWN
- REJECT: hard blocks (invalid EAN, no match, gating Y for some flags, sales < 10, unprofitable both prices)

---

## Configuration knobs

In `shared/config/decision_thresholds.yaml`:

- `target_roi` — single tunable for SHORTLIST gate (default 30%)
- `min_profit_absolute` — absolute profit floor (default £2.50)
- `min_sales_shortlist` / `min_sales_review` — velocity gates

In `shared/config/business_rules.yaml`:

- `vat_rate`, `marketplace_id` — fixed for this seller
- `price_range.min` / `price_range.max` — broad working range

Per-supplier:

- The adapter at `fba_engine/adapters/<supplier>/` defines how the supplier's
  files are parsed; new suppliers add an adapter folder

---

## Known limitations

- Match is strict-EAN only. Fuzzy matching is intentionally not implemented —
  too risky for live money.
- Keepa data must be supplied as a separate CSV; live API integration is a
  future addition.
- FBM shipping/packaging defaults are placeholders (£3.50 / £0.50). Set them
  to your real costs in `shared/config/decision_thresholds.yaml` before
  trusting FBM SHORTLIST results.
- Storage fee modelling is approximate (volume × rate / sales) — not a
  replacement for SP-API's exact storage data.
