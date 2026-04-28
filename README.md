# Amazon FBA Sourcing System

A Python pipeline for finding profitable products to sell on Amazon UK via FBA and FBM. Operated by a UK-based, non-VAT-registered seller. Uses real money, so accuracy matters more than cleverness.

## What this does

Two strategies, one engine:

- **`supplier_pricelist`** — given a supplier's price list, find which products in their catalogue make money on Amazon
- **`keepa_niche`** — given a niche (e.g. kids-toys), find what's worth selling on Amazon (then hunt for a supplier separately)

Both produce CSV/Excel/Markdown outputs with SHORTLIST / REVIEW / REJECT verdicts per product.

## Quick start

```bash
# Run the supplier pricelist strategy
python run.py --supplier connect-beauty

# With explicit Keepa data
python run.py --supplier abgee \
    --market-data fba_engine/data/pricelists/abgee/raw/keepa_combined.csv
```

Output goes to `fba_engine/data/pricelists/<supplier>/results/<timestamp>/`.

## Repo layout

```
.
├── docs/             # SPEC.md, architecture.md, strategies/, archive/
├── shared/           # config (single source of truth), niches, libraries
├── fba_engine/       # adapters, data, legacy keepa pipeline (temp), tests
├── services/         # MCP servers (SP-API)
└── orchestration/    # Cowork-facing workflow definitions (placeholder)
```

For the full breakdown, see `docs/architecture.md`.

## Key documents

- **`docs/SPEC.md`** — business logic, decision rules, the truth
- **`docs/architecture.md`** — how the system is laid out
- **`docs/strategies/`** — per-strategy documentation
- **`AGENTS.md`** — agent behaviour rules (what not to do)
- **`CLAUDE.md`** — agent quick-start

## Tests

```bash
# Shared library + canonical engine
cd shared/lib/python && pytest tests/ sourcing_engine/tests/ && cd ../../..

# Per-supplier adapter tests (run from supplier data folder)
for s in abgee connect-beauty shure zappies; do
  cd fba_engine/data/pricelists/$s
  pytest ../../../adapters/$s/tests/
  cd ../../../..
done
```

Expected: all canonical and adapter tests pass for abgee/connect-beauty (49 + 12 + 15 = 76 tests). Shure and zappies have 6 pre-existing test failures from copy-pasted abgee tests — not regressions, see `docs/SPEC.md` for context.

## Configuration

The single tunable is `target_roi` in `shared/config/decision_thresholds.yaml` (default 30%). All other thresholds derive from this or are absolute floors.

To onboard a new supplier:

1. Create `fba_engine/adapters/<new-supplier>/` with `ingest.py` and `normalise.py`
2. Create `fba_engine/data/pricelists/<new-supplier>/{raw,results}/`
3. Run `python run.py --supplier <new-supplier>`

No engine changes required.

## License & disclaimer

Personal sourcing tooling. Not licensed for redistribution. Use at your own risk — the engine produces decisions based on data; final purchase judgement is the operator's responsibility.
