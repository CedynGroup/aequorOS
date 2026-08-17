# Closing the BoG data gaps — through the Data Engine, never seeded

**Order (founder, re-affirmed 2026-08-16):** every data point enters through the Data Engine —
CSV/xlsx upload in the app, core-banking adapters, or the API push. No seeding, no direct inserts,
no seed scripts. That is what makes each gap valuable: the ingestion path we build is the path a
real bank's IT uses at onboarding.

## What already exists (use it, do not rebuild)

- **Reference datasets** — `REFERENCE_DATASET_KINDS` (`app/domain/ingestion/constants.py`); rows
  land verbatim (values stringified, dates ISO) on `canonical_reference_rows(dataset_kind, as_of_date,
  row_index, payload, lineage_id)` with full batch lineage. The eight gap kinds are ALREADY
  registered and the DB CHECK widened (migration `202608160014`, applied to the primary):
  `gl_mapping_bsd7`, `subsidiaries`, `tariff_schedule`, `capital_expenditure`, `atm_operations`,
  `remittance_flows`, `teller_withdrawals`, `interest_accruals`.
- **Ingestion entry points:** app upload `POST /banks/{id}/ingestion-uploads` (multipart) → `POST
  /banks/{id}/ingestion-batches` with a mapping config whose `references: [ReferenceMapping(source_table,
  dataset_kind, fields)]` names the sheet; API push three-call flow (`docs/API_INTEGRATION.md` §2):
  `push-batches` → `records` pages (`{"reference": {"<kind>": [rows]}}`, plus `positions`/`counterparties`/…)
  → `commit`. Auth: admin JWT or an `aeq_live_…` integration key.
- **Position attributes** ride verbatim (`API_INTEGRATION.md` §3.4 documents conventions); the
  bog_forms line maps already read `sector`, `bog_classification`, `interest_in_suspense_ghs`,
  `ecl_provision_ghs`, `institution_class`, `instrument`, `borrower_class`, `facility_type`,
  `obs_category`, `balance_ghs`, … — check the form's `<form>_line_map.md` for the exact key/value
  vocabulary before inventing one; **extend the vocabulary docs, do not fork them.**
- **Readers for bog_forms:** `sources.reference_rows(rc, kind)`, resolvers `refs.sum {kind,
  value_field, filters, sign}`, `refs.count {kind, filters}`, `refs.field {kind, filters, order_by,
  desc, index, field, numeric}` — latest as_of ≤ period end, memoised, `None` when never ingested
  (→ `input_required`), `0` when ingested with no match.
- **Load target for the realistic Sample Bank data:** the local tenant API on `:8001` (already
  running, pointed at the primary — `OR-QVXE0FQV` / `BK-0PMD7Z5M`) via the push API. A generic
  CSV→push client lives at `backend/scripts/ingest_push.py` (build it once; every domain reuses it —
  it is an ingestion client any bank could run, NOT a seed script: it carries no data).

## Deliverables per domain (only these files; nothing else)

| Deliverable | Path |
|---|---|
| Dataset spec (schema, one row per …, field table with types/units, validation rules, examples) | `docs/data_engine/datasets/<kind>.md` |
| Validation + translation hints (required fields, enums, numeric fields) | `backend/app/domain/ingestion/reference_schemas/<kind>.py` (create the package; register in its `__init__`) — the batch validator surfaces missing required fields as record warnings/errors |
| CSV template + Sample Bank realistic dataset (12 months where periodic) | `backend/onboarding/sample_bank/<kind>.csv` (+ `<kind>_template.csv` header-only) |
| bog_forms line-map re-pointing: replace the relevant `INPUT_REQUIRED` rows with `refs.*` sources | the form's `linemaps/<form>.py` — ONLY the rows your dataset feeds (coordinate: each form's map has one owner per this wave, listed in the launch brief) |
| Tests | `backend/tests/services/data_gaps/test_<kind>.py`: (a) hermetic upload/push of your CSV through the REAL API into the hermetic DB → rows land under the kind with lineage; (b) the affected BSD form generates and the previously-`input_required` cells are now `mapped` with the expected values; (c) validation rejects a malformed row honestly |
| Loaded to the primary | via `scripts/ingest_push.py` against `:8001` — report the batch ids; verify with a read query (worker URL) that rows landed with `dataset_kind` and lineage |

Loan-attribute gaps (sector, `bog_classification`, IIS/provisions, accruals-on-positions) are
**position attributes**, not reference datasets: extend `API_INTEGRATION.md` §3.4 attribute
conventions (append a table row per key), add the columns to the loans CSV template + a mapping
that carries extra columns into `attributes` (verify how the excel adapter maps unmapped columns —
if it drops them, add an `attributes_from_columns` mapping seam), and load Sample Bank's loans
re-push with those attributes.

## Ground rules

1. Never seed. Never write to `canonical_*` directly. Data enters only through the two ingestion
   entry points; tests prove it through the API.
2. Never invent a BoG line: you are supplying inputs to official cells that already exist.
3. Realistic Sample Bank data must be plausible for a Ghanaian universal bank of its size (read
   `docs/reporting/GUIDE…` for definitions; use the bank's real currency/periods from the primary);
   mark it clearly as the Sample Bank onboarding dataset in the CSV header comment/doc.
4. RLS: reads for verification use the worker URL; the running API handles tenant scoping.
5. Gates: ruff + basedpyright clean on your files; `DATABASE_URL="" uv run pytest
   tests/services/test_bog_forms_framework.py tests/services/bog_forms/ tests/services/data_gaps/
   -q -p no:cacheprovider` green. **Do not commit.**

Report: files, rows loaded per kind (batch ids), which BSD cells flipped input_required→mapped
(counts per form), gates, and any framework asks.
