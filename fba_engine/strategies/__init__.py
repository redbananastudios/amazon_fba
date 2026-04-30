"""Strategy YAML compositions + the runner that executes them.

Each strategy is a YAML file describing an ordered chain of pipeline steps
(modules under `fba_engine.steps.*`). The runner loads the YAML, applies
context variable substitution (e.g. `{niche}`, `{base}`), and pipes the
DataFrame through each step's `run_step(df, config) -> df` contract.

See `runner.py` for the API and `keepa_niche.yaml` for the canonical
example.
"""
