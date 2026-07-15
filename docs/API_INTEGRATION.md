# AequorOS Push API — Programmatic Data Integration

This document is the public contract for pushing data into AequorOS from an
institution's middleware, instead of uploading files. A push runs the **exact
same ingestion pipeline** as a file upload — mapping-driven translation,
validation gating, cell-level lineage, canonical persistence, immutable
storage artifacts — so everything downstream (batch history, per-table
breakdowns, module activation) behaves identically regardless of how the data
arrived.

Base URL: `http://<host>:8003/api/v1` (adjust per environment).

---

## 1. Authentication

**MVP:** the platform's tenant headers, sent on every request:

| Header | Value |
| --- | --- |
| `X-Org-Id` | Your organization UUID |
| `X-User-Id` | The service-account user UUID acting for your middleware |

> **Production note.** These headers identify the tenant inside a trusted
> perimeter. Production deployments put OAuth2 client-credentials (or mTLS)
> in front of these endpoints; the resource design below does not change.
> Do not build against the headers as a security mechanism.

---

## 2. The three-call flow

```
1. POST /banks/{bank_id}/push-batches                    open (idempotency key)
2. POST /banks/{bank_id}/push-batches/{push_id}/records  stage 1..N pages (≤ 5,000 records each)
3. POST /banks/{bank_id}/push-batches/{push_id}/commit   run the ingestion pipeline
   GET  /banks/{bank_id}/push-batches/{push_id}          staging status (any time)
```

### 2.1 Open a push batch

`POST /banks/{bank_id}/push-batches` → `201`

```json
{
  "as_of_date": "2026-04-30",
  "idempotency_key": "nightly-2026-04-30",
  "reason": "Nightly close push from middleware"
}
```

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `as_of_date` | ISO date | yes | Business date the records describe. |
| `idempotency_key` | string ≤ 128 | yes | Unique per bank. Reopening with the same key returns the **same** push batch; committing it twice returns the **same** ingestion batch. Reusing a key with a different `as_of_date` is a `409`. |
| `reason` | string | yes | Recorded in the audit trail and on the ingestion batch. |

Response (`PushBatchStatusRead`, also returned by `records` and `GET`):

```json
{
  "push_batch_id": "0198a5c2-…",
  "bank_id": "77000000-…",
  "as_of_date": "2026-04-30",
  "idempotency_key": "nightly-2026-04-30",
  "status": "staging",
  "pages_staged": 0,
  "records_staged": {},
  "total_records_staged": 0,
  "committed_batch_id": null,
  "expires_note": "Staged pages live in the bank's temp storage tier; batches never committed are cleaned up by its 30-day lifecycle."
}
```

### 2.2 Stage record pages

`POST /banks/{bank_id}/push-batches/{push_id}/records` → `200` (running totals)

Body — one page, **at most 5,000 records** (sum across all lists; `413`
beyond, split into more pages):

```json
{
  "entities": {
    "gl_account":   [ { …record… } ],
    "counterparty": [ { …record… } ],
    "product":      [ { …record… } ],
    "position":     [ { …record… } ]
  },
  "reference": {
    "yield_curve":       [ { …row… } ],
    "capital_structure": [ { …row… } ]
  }
}
```

Both sections are optional per page; every listed key is optional. Push only
what you have — an absent key means "not sent this time" and is never an
error. Pages accumulate: records for the same key across pages are
concatenated in page order.

### 2.3 Commit

`POST /banks/{bank_id}/push-batches/{push_id}/commit` → `201`

No body. Assembles the staged pages into one document and runs the standard
ingestion pipeline with `source_system = "API_PUSH"`. The response is the
same `IngestionBatchStartRead` a file upload returns: the full batch row with
its validation report (summary counts, per-table breakdown, findings,
reconciliation) plus a `reused` flag.

```json
{
  "batch": {
    "id": "0198a5c8-…",
    "source_system": "API_PUSH",
    "status": "accepted",
    "records_extracted": 9,
    "records_accepted": 9,
    "validation_report": {
      "summary": { "overall_status": "ACCEPTED", "reference_rows": {"yield_curve": 2}, … },
      "tables": [
        {"source_table": "gl_account", "resolved_to": "gl_account",
         "rows_extracted": 2, "rows_accepted": 2, "rows_warning": 0,
         "rows_error": 0, "rows_blocked": 0, "suggestion": null},
        …
      ],
      "failures": []
    },
    "raw_artifact_path": "api_push/2026-04-30/0198a5c8-…/source.json",
    …
  },
  "reused": false
}
```

Batch `status` meanings (identical to file ingestion): `accepted`,
`accepted_with_warnings` (flagged records are visible in the report; ERROR
records are excluded from calculations), `rejected` (a BLOCKER — e.g. a GL /
sub-ledger reconciliation break — rejected the whole batch; nothing
persisted), `failed` (the batch never reached validation).

---

## 3. Record schemas (identity mapping)

By default field names ARE the canonical field names below — no onboarding
configuration is needed for a conformant client (an identity mapping config is
auto-provisioned on first commit). If your middleware cannot rename its
fields, see §4.

Value conventions (strict — this is a programmatic contract, unlike the
forgiving spreadsheet path):

- **Amounts** (`balance`, `notional`): JSON number or plain numeric string
  (`1500000.5` or `"1500000.50"`). No currency symbols or thousands
  separators.
- **Rates** (`interest_rate`, `rate_spread`): decimal fractions —
  `0.245` means 24.5%. Never `"24.5%"` and never bare percent numbers.
- **Dates**: ISO `"YYYY-MM-DD"` strings.
- **Nulls**: JSON `null` (or omit the field). Empty strings are treated as
  null.
- Unknown fields are ignored unless captured via `attributes` (below) or a
  mapping config's `attribute_columns`.

Records that fail these rules do not fail the request: they land in the
batch's `translation_failures` (raw record preserved, per-field error
messages) and the rest of the batch proceeds — same semantics as file
ingestion. Fetch them at
`GET /banks/{bank_id}/ingestion-batches/{batch_id}/translation-failures`.

### 3.1 `gl_account`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `source_reference` | string | yes | Your stable identifier for the record (usually the account code). |
| `account_code` | string | yes | GL account code. |
| `name` | string | yes | Account name. |
| `account_class` | enum | yes | `ASSET`, `LIABILITY`, `EQUITY`, `INCOME`, `EXPENSE`, `OFF_BALANCE`. |
| `parent_account_code` | string | no | Parent GL code (hierarchy is wired when the parent is known). |
| `currency` | string | no | ISO 4217 code. |
| `balance` | number | no | Balance as of `as_of_date`. Enables GL vs sub-ledger reconciliation when positions carry `gl_account_code`. |
| `attributes` | object | no | Free-form extras preserved verbatim. |

### 3.2 `counterparty`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `source_reference` | string | yes | Your counterparty identifier. |
| `name` | string | yes | Legal / display name. |
| `counterparty_type` | enum | yes | `RETAIL_INDIVIDUAL`, `SME`, `CORPORATE`, `BANK_OECD`, `BANK_NON_OECD`, `CENTRAL_BANK`, `SOVEREIGN`, `GOVERNMENT_ENTITY`, `MULTILATERAL_DEV_BANK`, `NBFI`, `OTHER`. |
| `country_code` | string | no | ISO country code. |
| `rating` | string | no | External rating. |
| `rating_source` | string | no | Rating agency. |
| `group_reference` | string | no | Group / parent counterparty reference. |
| `external_identifiers` | object | no | e.g. `{"tin": "…", "lei": "…"}` — preserved verbatim. |
| `attributes` | object | no | Free-form extras. |

### 3.3 `product`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `source_reference` | string | yes | Your product identifier (usually the product code). |
| `product_code` | string | yes | Product code positions reference. |
| `name` | string | yes | Product name. |
| `regulatory_category` | string | no | Canonical regulatory category; when omitted, the mapping config's `product_mappings` may supply it. |
| `risk_weight_code` | string | no | Risk-weight bucket code. |
| `attributes` | object | no | Free-form extras. |

### 3.4 `position`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `source_reference` | string | yes | Your position identifier (arrangement id, deal ref, …). |
| `position_type` | enum | yes | `LOAN`, `DEPOSIT`, `SECURITY_HOLDING`, `DERIVATIVE`, `FX_HEDGE`, `INTEREST_RATE_SWAP`, `CASH`, `INTERBANK_PLACEMENT`, `INTERBANK_BORROWING`, `LC_GUARANTEE`, `COMMITMENT_UNDRAWN`, `OTHER_ASSET`, `OTHER_LIABILITY`. |
| `currency` | string | yes | ISO 4217 (validated). |
| `balance` | number | yes | Outstanding / carrying amount as of `as_of_date`. |
| `notional` | number | no | Notional where distinct from balance (OBS, hedges, swaps). |
| `counterparty_reference` | string | no | Must match a `counterparty.source_reference` in this push or previously ingested (gap = warning, not rejection). |
| `product_code` | string | no | Must match a known `product.product_code` (dangling = error on the record). |
| `gl_account_code` | string | no | Must match a known `gl_account.account_code`; drives reconciliation. |
| `origination_date` | date | no | |
| `contractual_maturity` | date | no | Before `as_of_date` ⇒ warning. |
| `next_repricing_date` | date | no | |
| `interest_rate` | number | no | Decimal fraction; outside [0, 1] ⇒ error on the record. |
| `rate_type` | enum | no | `FIXED` or `FLOATING`. |
| `rate_index` | string | no | e.g. `GHREF`. |
| `rate_spread` | number | no | Decimal fraction. |
| `ifrs9_stage` | integer | no | 1, 2, or 3. |
| `attributes` | object | no | Instrument specifics (hedge pair, contract rate, MtM, swap legs, ECL, branch, …) — preserved verbatim and used by module fact derivation. |

### 3.5 Reference datasets

Reference rows have **no fixed schema**: each row is preserved verbatim as a
payload (values stringified, dates ISO) under its dataset kind, and consumed
as-is by the calculation modules. Valid keys under `"reference"`:

| Key | Typical row fields (from the Sample Bank dataset) |
| --- | --- |
| `capital_structure` | `item`, `amount_ghs`, `tier`, … |
| `behavioral_assumptions` | `product_code`, `assumption`, `value`, … |
| `yield_curve` | `curve_name`, `currency`, `tenor_months`, `rate`, `quote_date` |
| `fx_rates_current` | `pair`, `rate`, `quote_date` |
| `fx_rates_historical` | `pair`, `rate`, `quote_date` |
| `historical_cashflows` | `date`, `inflow`, `outflow`, … |
| `historical_financials` | `month`, `total_assets`, `net_income`, … |
| `business_units` | `unit_id`, `name`, … |
| `institution` | `institution_id`, `name`, … |

---

## 4. Mapping configs (when your field names differ)

If your middleware cannot emit canonical field names, activate a
`MappingConfig` with `source_system: "API_PUSH"` via
`POST /banks/{bank_id}/mapping-configs`. `source_table` is the payload key
(`"gl_account"`, `"position"`, a reference kind, …); `fields` maps canonical
field → your field name. `enum_mappings`, `product_mappings`, and
`attribute_columns` work exactly as they do for file ingestion.

```json
{
  "source_system": "API_PUSH",
  "name": "Middleware field aliases",
  "config": {
    "field_mappings": {
      "gl_account": {
        "source_table": "gl_account",
        "fields": {
          "source_reference": "AcctCode",
          "account_code": "AcctCode",
          "name": "AcctName",
          "account_class": "Side"
        }
      }
    },
    "enum_mappings": { "account_class": { "A": "ASSET", "L": "LIABILITY" } }
  },
  "activate": true,
  "reason": "Bank middleware cannot rename its export fields."
}
```

One mapping config is active per `(bank, source system)`; activating another
creates a new version (fully audited). When no `API_PUSH` config exists, the
identity mapping is auto-provisioned on first commit — meaning the API is
**zero-config for conformant clients** and translation stays reproducible
from the config version recorded on every batch.

---

## 5. Idempotency

Two layers, both safe to retry blindly:

1. **Push-batch identity** — `idempotency_key` (unique per bank). Reopening
   returns the same push batch; recommitting returns the same ingestion batch
   (`"reused": true`). Staging into a committed push batch is a `409` — open
   a new push batch for new data.
2. **Content identity** — the assembled document is hashed (SHA-256, part of
   the batch row). Pushing identical content for the same `as_of_date` under
   the same mapping — even under a *new* idempotency key — returns the
   previously accepted batch with `"reused": true` instead of duplicating
   canonical state.

A rejected or failed batch is immutable history: fix the data and push again
under a **new** idempotency key.

---

## 6. Error semantics

| Status | Meaning |
| --- | --- |
| `404` | Unknown bank or push batch **for your tenant** (cross-tenant access is indistinguishable from not-found). |
| `409` | State conflict: staging into a committed push batch, or reusing an idempotency key with a different `as_of_date`. |
| `413` | Records page above 5,000 records — split it. |
| `422` | Envelope shape validation: unknown `entities`/`reference` key, a record that is not a JSON object, an empty page, or committing with nothing staged. The `error.details` list carries JSON pointers (`loc`) to the offending element. |
| `503` | Storage tier unavailable — retry later. |

Error body shape (all endpoints):

```json
{"error": {"code": "validation_error", "message": "Request validation failed.",
           "request_id": "…", "details": [{"loc": ["body", "entities", "gl_account", 0], …}]}}
```

**Per-record data quality is NOT a 4xx.** Coercion and validation problems
surface in the committed batch: `translation_failures` for records that could
not be translated (with per-field messages), and the validation report's
`failures` (severity `INFO`/`WARNING`/`ERROR`/`BLOCKER`) for business-rule
findings. Interpret the report exactly as for file uploads:

- `summary.overall_status` — `ACCEPTED` / `ACCEPTED_WITH_WARNINGS` / `REJECTED`.
- `tables[]` — one row per pushed key: what it resolved to and how many rows
  were extracted/accepted/flagged. A key with `resolved_to: null` means the
  active mapping consumed nothing from it — check your mapping config.
- `failures[]` — individual findings with rule, severity, and locator
  (`source.json#position!R14` = 14th record of your `position` list).

---

## 7. Worked end-to-end example

A runnable client lives at `backend/scripts/push_api_example.py`. It reads
`data/03_gl_accounts.csv` and `data/04_products.csv`, converts them to this
contract, pushes them through the three-call flow against a local backend,
and prints the validation summary — proving file-upload/API equivalence on
the same dataset:

```bash
cd backend
.venv/bin/python scripts/push_api_example.py \
  --base-url http://127.0.0.1:8003 \
  --org-id 11111111-1111-4111-8111-111111111111 \
  --user-id aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa \
  --bank-id <bank uuid> \
  --as-of 2026-04-30
```

Condensed transcript:

```text
POST /push-batches            → 201 staging push 0198…
POST …/records (page 1/2)     → 200 staged {'gl_account': 40}
POST …/records (page 2/2)     → 200 staged {'gl_account': 40, 'product': 12}
POST …/commit                 → 201 batch 0199… accepted (reused=false)
  extracted=52 translated=52 accepted=52 warnings=0 errors=0
  tables:
    gl_account → gl_account   40 extracted / 40 accepted
    product    → product      12 extracted / 12 accepted
```
