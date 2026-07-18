# Q06 — "Delete all seed-data test paths" vs the seed being the fixture behind ~25 tests

**Deciding:** how to execute the brief's step 7 ("all downstream tests run against injected
canonical data; seed-data test paths are deleted") without red-lining CI mid-build.

**What the brief says:** retire seed-based testing; every test operates on canonical data
produced by running the Excel/CSV adapter end-to-end on the Sample Bank raw sources; cache
that canonical output as fixtures; regenerate on demand; delete seed paths.

**What actually exists:** `backend/app/services/sample_bank_seed.py` is imported by
`services/banks.py` (the demo-seed path) **and is the deterministic fixture behind ~25
test files** — every golden engine/API test (`test_regulatory_liquidity/capital/
forecasting/fx/irr/ftp`, `test_fact_derivation`, `test_pipeline`, `test_live_engine`,
`test_data_activation`, …). `reset_uploaded_data.py` also treats the seed as the synthetic
baseline that uploads layer on top of. Deleting it outright red-lines the suite.

**Default I'm proceeding on (sequenced, non-breaking):**
1. Build the `test_data_pipeline` that runs the Excel/CSV adapter on `data/` end-to-end
   (extract → ETL → translate → validate → enrich → persist) and **caches canonical
   snapshots as fixtures** (byte-anchored to seed `20260521`).
2. Add the multi-source variants (Excel / Oracle export / Refinitiv fixture) and assert
   byte-identical canonical output — the source-agnostic acid test — **before** touching
   any downstream test.
3. Re-point downstream tests onto the injected canonical fixtures.
4. **Only then** delete the seed-based test paths, in one reviewed change, with CI green
   at each step. Per the brief: do not proceed past a step whose contract tests fail.

**Need from Eric/Dela to lock:** confirm the seed may be fully removed *after* the injected
fixtures are green (vs retained as a minimal fast unit fixture). Note: this also depends on
Q02 (the sources must be reproducible in CI, or the injected tests can't run on a clean
checkout).
