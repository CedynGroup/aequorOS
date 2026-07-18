# Data Engine Build — Open Questions

Per the build brief ("Where to Surface Questions"): genuine unknowns the brief and the
reference docs (`data_engine.md`, `storage.md`, `market_data_adapter.md`,
`data/README.md` = the docs' `sample_bank_data/`) do not jointly resolve. Each file
states: the decision, what the docs say, my working default (I proceed on this, not
blocking), and what I need from Eric/Dela to lock it.

These are logged as I build. I am **not** blocking on them; I proceed on the documented
default and will reconcile if the answer differs.

| # | Question | Default I'm proceeding on | Blocks? |
|---|----------|---------------------------|---------|
| Q01 | Brief overrides the docs' Phase-3/4 gating of ML-ETL + DB-direct | Build now, honor docs' architecture + §12.5 model governance | No |
| Q02 | `sample_bank_data/` (named in all 3 specs) vs actual gitignored `data/` | Treat `data/` as the canonical Sample Bank package | No |
| Q03 | "Production-ready" live vendor auth is unverifiable here (no `blpapi`, no creds) | Build production-shaped code; fixture-verify; mark live-verify pending | No |
| Q04 | Canonical model already exists as SQLAlchemy, not the docs' `/schema/*.sql` DDL | Treat existing `models/canonical.py` as the realized §4 model; do not rewrite | No |
| Q05 | ML-ETL is a new shared layer vs docs' "adapters know sources, nothing else does" | Place ETL between extract→translate as source-agnostic, per brief §4 architecture | No |
| Q06 | "Delete all seed-data test paths" vs seed being the fixture behind ~25 tests | Migrate tests onto injected canonical fixtures first, delete seed paths last | No |
