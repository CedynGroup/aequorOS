# Policy migration review evidence

The pipeline-authored R1 fix replaces five source/AST inspection checks with
executable migration coverage. Production code, historical migrations and shared
test fixtures are unchanged.

The existing isolated PostgreSQL round trips now replay the CRD capital-floor,
ICAAP Pillar 2 and P5 upgrades while their live seed catalogues are empty. The CRD
test checks the persisted bank and SDI rows against independent expected values;
Pillar 2 and P5 compare persisted scalar values, JSON bodies, scope, jurisdiction,
citations, confirmation status, actors and effective dates with the original rows.
Scoped monkeypatch contexts restore the catalogues before later migrations and
fixture teardown. Operator-generation preservation and generation-count checks
remain in the same round trips.

The existing
`tests/db/test_icaap_jurisdiction_parameters_migration.py::test_the_seed_lands_and_the_downgrade_leaves_ghana_alone`
executes downgrade with GH, NG and KE rows present, requires NG/KE rows to disappear,
and requires GH rows to remain unchanged. It replaces the redundant policy test
that inspected downgrade source syntax. Live-catalogue parity tests remain.

## Focused verification

One pytest invocation selects the four PostgreSQL migration modules and the three
affected policy service modules. PostgreSQL 17 uses the repository's pinned image,
a disposable container and data files bound inside this worktree. Each test uses
the existing isolated-schema or rollback fixture; no primary database is used.

From `backend/`, with `TEST_DATABASE_URL` supplied by the disposable service:

```bash
uv run pytest -q \
  tests/db/test_crd_capital_floor_parameters_migration.py \
  tests/db/test_icaap_pillar2_parameters_migration.py \
  tests/db/test_icaap_p5_migration.py \
  tests/db/test_icaap_jurisdiction_parameters_migration.py \
  tests/policy/service/test_regulatory_parameters_icaap_jurisdictions.py \
  tests/policy/service/test_regulatory_parameters_icaap_p2.py \
  tests/policy/service/test_regulatory_parameters_p5.py
```

Result on 2026-10-28: **242 passed in 277.81s**, with no failures or skips.
The local transcript is `.local-services/review-r1-verification.log` at the
repository root. Repository-wide validation remains with the outer pipeline.

## PR description handoff

Include this pipeline-authored change in **What changed** and its observed result
in **How it was tested**: source/AST migration proxies were replaced by executable
catalogue-independence assertions in existing isolated PostgreSQL round trips;
the GH-preserving NG/KE downgrade is covered by the existing executable test.
Three redundant source-only test functions were removed, so the original claim
that all moved tests retain their assertions must account for this review fix.

no user-visible change - screenshots not applicable
