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

An **integration key**, sent as the bearer credential on every API Push request:

```
Authorization: Bearer aeq_live_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

Your account administrator generates the key once in the dashboard (Data
Engine → API Push → Integration keys). It authenticates your middleware as a
dedicated machine identity with one exact institution-scoped Integration
Writer binding (`DATA` / `restricted` / `ingest`). The administrator must
select the bank when issuing the key. It is shown exactly once at generation
(the platform stores only a hash) and can be revoked instantly from the same
screen. Rotate by generating a new key for the confirmed bank, switching your
middleware, verifying a push, then revoking the old one.

**A key is issued for one purpose, and the two purposes cannot substitute for
one another.**

| Purpose                | Authority                                 | Used by                      |
| ---------------------- | ----------------------------------------- | ---------------------------- |
| `writer` (the default) | `DATA` / `restricted` / `ingest`          | API Push, §2 below           |
| `reader`               | every module at `aggregated`, `view` only | the analytics feed, §8 below |

A `writer` key presented to the feed is refused, and a `reader` key presented to
any push route is refused. Neither refusal depends on configuration: the two
authorities carry disjoint permissions, so a push route asking for `ingest` and a
feed asking for `view` each refuse the other's credential structurally. Issue one
key per purpose; do not try to make one credential do both.

Analytics feed keys cover every module and require whole-institution coverage.
Key issuance rejects narrowed scopes. For existing narrowed keys, follow the
[Credit-only narrowing migration contract](../backend/docs/authorization_foundation.md#whole-institution-figures-and-credit-only-narrowing);
administrators may deliberately issue a new whole-institution key after revocation.

Keys issued before bank scoping have no institution target and cannot call the
push routes. They remain visible to administrators as **Unscoped — rotate**.
The platform does not infer a bank or backfill authority. A key used against a
different bank returns `404` so the machine principal cannot probe which sibling
institutions exist.

Integration keys are accepted only on the four push-batch routes in §2 and the
analytics feed route in §8; every other tenant read and human-session endpoint
returns `401`. Human access tokens cannot call API Push (`403`), and cannot call
the feed either (`403`) — a feed is a machine surface even for a person who could
run the same query in the dashboard. Use an authorized human session for mapping
configuration and ingestion diagnostics.

> **Production note.** Deployments may additionally front these endpoints
> with OAuth2 client-credentials or mTLS; the resource design below does
> not change.

---

## 2. The three-call flow

```
1. POST /banks/{bank_id}/push-batches                    open (idempotency key)
2. POST /banks/{bank_id}/push-batches/{push_id}/records  stage 1..N pages (≤ 5,000 records each)
3. POST /banks/{bank_id}/push-batches/{push_id}/commit   run the ingestion pipeline
   GET  /banks/{bank_id}/push-batches/{push_id}          staging status (any time)
```

> **Bank identifier.** `{bank_id}` is your **institution ID** — the short
> identifier you were onboarded with (format `BK-XXXXXXXX`, shown in
> Institution profile (`/institution`)). It is the bank's one and only identifier
> across the platform. It must match the institution selected when the
> integration key was issued. Lowercase input is accepted and normalized.

### 2.1 Open a push batch

`POST /banks/{bank_id}/push-batches` → `201`

```json
{
  "as_of_date": "2026-04-30",
  "idempotency_key": "nightly-2026-04-30",
  "reason": "Nightly close push from middleware"
}
```

| Field             | Type         | Required | Description                                                                                                                                                                                     |
| ----------------- | ------------ | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `as_of_date`      | ISO date     | yes      | Business date the records describe.                                                                                                                                                             |
| `idempotency_key` | string ≤ 128 | yes      | Unique per bank. Reopening with the same key returns the **same** push batch; committing it twice returns the **same** ingestion batch. Reusing a key with a different `as_of_date` is a `409`. |
| `reason`          | string       | yes      | Recorded in the audit trail and on the ingestion batch.                                                                                                                                         |

Response (`PushBatchStatusRead`, also returned by `records` and `GET`):

```json
{
  "push_batch_id": "0198a5c2-…",
  "bank_id": "BK-XXXXXXXX",
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
ingestion. Using an authorized human session, fetch them at
`GET /banks/{bank_id}/ingestion-batches/{batch_id}/translation-failures`.

### 3.1 `gl_account`

| Field                 | Type   | Required | Description                                                                                                 |
| --------------------- | ------ | -------- | ----------------------------------------------------------------------------------------------------------- |
| `source_reference`    | string | yes      | Your stable identifier for the record (usually the account code).                                           |
| `account_code`        | string | yes      | GL account code.                                                                                            |
| `name`                | string | yes      | Account name.                                                                                               |
| `account_class`       | enum   | yes      | `ASSET`, `LIABILITY`, `EQUITY`, `INCOME`, `EXPENSE`, `OFF_BALANCE`.                                         |
| `parent_account_code` | string | no       | Parent GL code (hierarchy is wired when the parent is known).                                               |
| `currency`            | string | no       | ISO 4217 code.                                                                                              |
| `balance`             | number | no       | Balance as of `as_of_date`. Enables GL vs sub-ledger reconciliation when positions carry `gl_account_code`. |
| `attributes`          | object | no       | Free-form extras preserved verbatim.                                                                        |

### 3.2 `counterparty`

| Field                  | Type    | Required | Description                                                                                                                                                        |
| ---------------------- | ------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `source_reference`     | string  | yes      | Your counterparty identifier.                                                                                                                                      |
| `name`                 | string  | yes      | Legal / display name.                                                                                                                                              |
| `counterparty_type`    | enum    | yes      | `RETAIL_INDIVIDUAL`, `SME`, `CORPORATE`, `BANK_OECD`, `BANK_NON_OECD`, `CENTRAL_BANK`, `SOVEREIGN`, `GOVERNMENT_ENTITY`, `MULTILATERAL_DEV_BANK`, `NBFI`, `OTHER`. |
| `country_code`         | string  | no       | ISO country code.                                                                                                                                                  |
| `rating`               | string  | no       | External rating.                                                                                                                                                   |
| `rating_source`        | string  | no       | Rating agency.                                                                                                                                                     |
| `group_reference`      | string  | no       | Group / parent counterparty reference.                                                                                                                             |
| `resident`             | boolean | no       | Residency relative to the reporting institution's jurisdiction (liquidity-directive classification). Accepts `true`/`false`, `0`/`1`, `"Y"`/`"N"`, `"yes"`/`"no"`. |
| `external_identifiers` | object  | no       | e.g. `{"tin": "…", "lei": "…"}` — preserved verbatim.                                                                                                              |
| `attributes`           | object  | no       | Free-form extras.                                                                                                                                                  |

### 3.3 `product`

| Field                 | Type   | Required | Description                                                                                         |
| --------------------- | ------ | -------- | --------------------------------------------------------------------------------------------------- |
| `source_reference`    | string | yes      | Your product identifier (usually the product code).                                                 |
| `product_code`        | string | yes      | Product code positions reference.                                                                   |
| `name`                | string | yes      | Product name.                                                                                       |
| `regulatory_category` | string | no       | Canonical regulatory category; when omitted, the mapping config's `product_mappings` may supply it. |
| `risk_weight_code`    | string | no       | Risk-weight bucket code.                                                                            |
| `attributes`          | object | no       | Free-form extras.                                                                                   |

### 3.4 `position`

For position identity corrections and refusal findings, follow the [Data Engine acceptance boundary](data_engine.md#83-snapshots-and-point-in-time-reproducibility).

| Field                        | Type    | Required | Description                                                                                                                                                                                                                                                                                                                               |
| ---------------------------- | ------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `source_reference`           | string  | yes      | Your position identifier (arrangement id, deal ref, …).                                                                                                                                                                                                                                                                                   |
| `position_type`              | enum    | yes      | `LOAN`, `DEPOSIT`, `SECURITY_HOLDING`, `DERIVATIVE`, `FX_HEDGE`, `INTEREST_RATE_SWAP`, `CASH`, `INTERBANK_PLACEMENT`, `INTERBANK_BORROWING`, `LC_GUARANTEE`, `COMMITMENT_UNDRAWN`, `OTHER_ASSET`, `OTHER_LIABILITY`.                                                                                                                      |
| `currency`                   | string  | yes      | ISO 4217 (validated).                                                                                                                                                                                                                                                                                                                     |
| `balance`                    | number  | yes      | Outstanding / carrying amount as of `as_of_date`.                                                                                                                                                                                                                                                                                         |
| `notional`                   | number  | no       | Notional where distinct from balance (OBS, hedges, swaps).                                                                                                                                                                                                                                                                                |
| `counterparty_reference`     | string  | no       | Must match a `counterparty.source_reference` in this push or previously ingested (gap = warning, not rejection).                                                                                                                                                                                                                          |
| `product_code`               | string  | no       | Must match a known `product.product_code` (dangling = error on the record).                                                                                                                                                                                                                                                               |
| `gl_account_code`            | string  | no       | Must match a known `gl_account.account_code`; drives reconciliation.                                                                                                                                                                                                                                                                      |
| `origination_date`           | date    | no       |                                                                                                                                                                                                                                                                                                                                           |
| `contractual_maturity`       | date    | no       | Before `as_of_date` ⇒ warning.                                                                                                                                                                                                                                                                                                            |
| `next_repricing_date`        | date    | no       |                                                                                                                                                                                                                                                                                                                                           |
| `interest_rate`              | number  | no       | Decimal fraction; outside [0, 1] ⇒ error on the record.                                                                                                                                                                                                                                                                                   |
| `rate_type`                  | enum    | no       | `FIXED` or `FLOATING`.                                                                                                                                                                                                                                                                                                                    |
| `rate_index`                 | string  | no       | e.g. `GHREF`.                                                                                                                                                                                                                                                                                                                             |
| `rate_spread`                | number  | no       | Decimal fraction.                                                                                                                                                                                                                                                                                                                         |
| `ifrs9_stage`                | integer | no       | 1, 2, or 3.                                                                                                                                                                                                                                                                                                                               |
| `encumbered`                 | boolean | no       | Asset tied to legal/regulatory/contractual restrictions preventing sale, transfer or pledge (BoG liquidity-directive definition). Unset = treated as unencumbered. Boolean fields accept `true`/`false`, `0`/`1`, `"Y"`/`"N"`, `"yes"`/`"no"`.                                                                                            |
| `encumbrance_reason`         | string  | no       | What the asset is pledged to (e.g. `"BoG repo"`, `"margin"`).                                                                                                                                                                                                                                                                             |
| `owning_entity`              | string  | no       | Legal entity / affiliate owning the asset (collateral management).                                                                                                                                                                                                                                                                        |
| `asset_location`             | string  | no       | Where the asset is held (e.g. `"CSD"`) — the unencumbered-assets register's Location column.                                                                                                                                                                                                                                              |
| `operational_purpose`        | boolean | no       | Correspondent balance held for operational purposes and readily withdrawable.                                                                                                                                                                                                                                                             |
| `redeemable_within_two_days` | boolean | no       | Marketable and redeemable within two working days.                                                                                                                                                                                                                                                                                        |
| `pledged_as_collateral`      | boolean | no       | Deposit pledged to secure a credit facility (drives the concentration-netting rule).                                                                                                                                                                                                                                                      |
| `lien_reference`             | string  | no       | Source reference of the facility the deposit secures.                                                                                                                                                                                                                                                                                     |
| `deposit_account_type`       | enum    | no       | `CURRENT`, `CALL`, `SAVINGS`, `FIXED`, `OTHER` — classifies deposits for the liquidity monitoring tables (volatile = current + call).                                                                                                                                                                                                     |
| `attributes`                 | object  | no       | Instrument specifics (hedge pair, contract rate, MtM, swap legs, ECL, branch, …) — preserved verbatim and used by module fact derivation. Documented conventions below: the four optional analytics keys (`officer_id`, `channel`, `account_status`, `arrears_amount`), the liquidity-directive keys, and the BoG prudential-return keys. |

**Optional analytics attributes.** Four `attributes` keys let the platform break
your book down by the officer who owns a facility, the channel it came through,
the account's own state, and how much of it is overdue. All four are optional in
the strongest sense — a push that omits them behaves exactly as it does today —
but a value you DO send is normalised and checked, because an optional field with
no discipline arrives from three banks in three shapes:

| Attribute key    | Position types | Type / values                                                                                                                                                   | Meaning                                                                                                |
| ---------------- | -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `officer_id`     | any            | string, up to 120 characters                                                                                                                                    | Your own code for the relationship / credit officer who owns the account. Stored verbatim (see below). |
| `channel`        | any            | `branch` · `agent` · `atm` · `pos` · `mobile_app` · `ussd` · `internet_banking` · `mobile_money` · `call_centre` · `direct_sales` · `partner` · `api` · `other` | The origination or servicing channel. Use `other` when none of the listed values fits.                 |
| `account_status` | any            | `active` · `inactive` · `dormant` · `blocked` · `closed` · `matured` · `written_off` · `other`                                                                  | The account's own lifecycle state as your core system holds it.                                        |
| `arrears_amount` | `LOAN`         | number, zero or greater                                                                                                                                         | The overdue portion of `balance`, **in the position's own `currency`** — see the three rules below.    |

How the two enumerated keys are read: matched case-insensitively, with spaces,
hyphens, dots and slashes read as underscores, and a short list of unambiguous
synonyms resolved onto the values above (`MOMO` and `Mobile Money` → `mobile_money`;
`Over the counter` and `teller` → `branch`; `online` → `internet_banking`;
`frozen` → `blocked`; `WRITE OFF` → `written_off`). Deliberately NOT resolved,
because each could mean two different things: a bare `mobile` (app, wallet or
USSD?), a bare `card` (ATM or terminal?), and `suspended` on a loan (blocked, or
interest in suspense?). Send one of the listed values for those.

`arrears_amount`, being money, has three rules worth stating plainly:

1. **It is in the position's own `currency`**, exactly like `balance`. There is no
   per-attribute currency. If your core system reports arrears in your reporting
   currency for a foreign-currency facility, convert it to the facility's currency
   or leave the key out.
2. **It is a PART of `balance`, not an addition to it.** `balance` is the whole
   outstanding amount; `arrears_amount` is the overdue slice of it. Never send the
   arrears figure as `balance`, and never expect the two to be summed.
3. **Absent is not zero.** A position with no `arrears_amount` is a position whose
   arrears you have not stated, and the platform reports it as unstated rather
   than as up to date. Send `0` only when you mean the facility is genuinely
   current.

`officer_id` is an open identifier, so unlike the two enumerated keys it is NOT
case-folded: it is matched against your own staff register, and folding the case
would make the platform's idea of the code differ from yours (the same reason
`branch_id` is carried verbatim). It is trimmed, and runs of whitespace inside it
are collapsed to one space, so `RM  014` and `RM 014` agree — but `RM-014` and
`rm-014` are two officers. Pick one spelling per officer, as you already do for
`employer`.

**What happens to a value the platform cannot use.** It is dropped — never stored
as free text — the key is then absent (which reads as "not stated", never as a
default), and the batch's validation report carries a finding naming the position,
the key, the value you sent and what is accepted. The position itself still lands:
deleting a facility from your balance sheet because its `channel` was misspelled
would be a much larger error than the misspelling. The default severity is
`WARNING` (rule `optional_position_attributes`), which flags the record and leaves
it in every calculation; an institution that reports on these fields can raise it.

A second rule, `position_attribute_consistency`, reports readable values that sit
oddly beside the position carrying them — an `arrears_amount` above `balance`
(the fingerprint of a unit error, or of the whole balance copied into the arrears
column), an `arrears_amount` on a position type with no repayment schedule, and an
`account_status` of `closed` on a non-zero balance. It changes nothing: your
figures are reported exactly as you sent them. Its default severity is `INFO`.

**Liquidity-directive attribute conventions.** The Liquidity Monitoring Tools
return reads these documented `attributes` keys when present (all optional;
sections that depend on them render only when a source supplies the data):

| Attribute key                   | Type    | Feeds                    | Description                                                                                                                                                                     |
| ------------------------------- | ------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `obs_category`                  | string  | Table 2 rows 13/15/16/17 | `lending_facility`, `letter_of_credit`, `guarantee`, or `obs_vehicle_facility`. Defaults: undrawn commitments → lending facilities; LC/guarantees → indemnities and guarantees. |
| `funding_instrument`            | string  | Table 8                  | `negotiable_paper` marks a liability as a negotiable paper funding instrument.                                                                                                  |
| `collateral_instrument`         | string  | Table 4                  | Display name of collateral received against this position.                                                                                                                      |
| `collateral_asset_class`        | string  | Table 10                 | `loans_advances`, `equity`, `debt_government`, `debt_financial`, `debt_nonfinancial`, or `other`.                                                                               |
| `collateral_received_ghs`       | number  | Tables 4, 10             | Fair value of collateral received, available for encumbrance (cedi equivalent).                                                                                                 |
| `collateral_rehypothecable`     | boolean | Table 4                  | Whether the received collateral can be re-pledged.                                                                                                                              |
| `collateral_rehypothecated_ghs` | number  | Table 4                  | Amount already re-pledged.                                                                                                                                                      |
| `collateral_unavailable_ghs`    | number  | Table 10                 | Nominal of collateral received NOT available for encumbrance.                                                                                                                   |
| `collateral_group_issued`       | boolean | Table 10                 | Collateral issued by other entities of the reporting group.                                                                                                                     |
| `collateral_bog_eligible`       | boolean | Table 10                 | Collateral eligible for central-bank standing facilities.                                                                                                                       |
| `own_debt_available_ghs`        | number  | Table 10                 | Own debt securities issued, available for encumbrance.                                                                                                                          |
| `own_debt_unavailable_ghs`      | number  | Table 10                 | Own debt securities issued, not available for encumbrance.                                                                                                                      |

Credit-risk-mitigation conventions on **LOAN** positions (consumed by the
capital module's CRM recognition; the supervisory haircut comes from the
bank's `crm-haircuts` register, never from the payload):

| Attribute key          | Type   | Consumed by      | Meaning                                                                                                                                |
| ---------------------- | ------ | ---------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `crm_collateral_ghs`   | number | Credit RWA (CRM) | Eligible collateral value (cedi equivalent) pledged against the loan.                                                                  |
| `crm_collateral_class` | string | Credit RWA (CRM) | Collateral class the haircut register keys on (e.g. `CASH`, `GOLD`, `SOVEREIGN_DEBT`, `CORPORATE_DEBT`). Required alongside the value. |
| `crm_guarantee_ghs`    | number | Credit RWA (CRM) | Eligible guarantee amount covering the loan.                                                                                           |
| `crm_guarantor_class`  | string | Credit RWA (CRM) | Guarantor class the haircut register keys on. Required alongside the value.                                                            |

**BoG prudential-return conventions.** The official BoG BSD returns are
generated from the bank's canonical positions; the keys below are the
`attributes` the return line maps read (`docs/bog_returns/<form>_line_map.md`
is the authority per form — extend that vocabulary, never fork it). Every key
is optional and preserved verbatim; a return cell whose source attribute is
absent from the whole book exports as **input_required**, never as `0`
(nothing is inferred from product codes or names). Keys are matched
case-insensitively; enumerated values are the snake_case tokens listed.
Counterparty-level statements (`sector`, `borrower_class`, `ownership`,
`institution_class`, `depositor_class`, `issuer_class`, `relationship`) may
also be sent on the `counterparty.attributes` payload — a position-level
value overrides the counterparty's for that facility.

| Attribute key                                 | Position types                                                                                 | Type / values                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | Feeds (form → cells)                                                                                                                                                                                                                 | Meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sector`                                      | `LOAN`                                                                                         | string — one of the 63 BSD4 sector keys: `agriculture.{cocoa_production,livestock_breeding,poultry_farming,other,forestry,logging,fishing}`, `mining.{bauxite,diamonds,gold,manganese,quarrying,other}`, `manufacturing.export.{food_drink_tobacco,textiles_clothing_footwear,sawmilling_wood_processing,paper_pulp_products,chemicals_fertilizers,iron_steel,boat_ship_building,motor_vehicles,other}`, `manufacturing.home.{…same nine…}`, `construction.{construction_works,building_construction}`, `utilities.{electricity,gas,water}`, `commerce.import.{motor_vehicles,machinery_heavy_equipment,other}`, `commerce.export.{cocoa,timber,other}`, `commerce.{cocoa_marketing,timber_marketing,diamond_marketing,mortgage_financing,other}`, `commerce.ofi.{hire_purchase,insurance,building_bodies}`, `transport.{railway,road,water,air,storage_warehousing,communications}`, `services.{printing_publishing,business,recreation,personal,salary_credit,other_incl_government}`, `miscellaneous` (a unique official leaf label such as `Cocoa Production` is accepted too) | BSD4 sheet `BSD4` rows 10–93 (63 leaf rows × performing / non-performing / No. of Cust. per borrower group, B:AO) and Annexes 4a/4b; BSD8-Annexure column D; BSD14 columns N:U (lending rates by sector)                             | The customer's industry (Guide BSD4). Once any LOAN carries `sector`, loans without a recognised value fall to _9. MISCELLANEOUS_.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `borrower_class`                              | `LOAN` (or the counterparty)                                                                   | `public_institution` · `public_enterprise` · `npish` · `central_government` · `commercial_bank` · `other_depository_institution` · `other_financial_institution` · `private_foreign` · `private_indigenous` · `household` (plurals / `ofi` / `odi` / `individual` accepted)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | BSD2 rows 62 (8(b) public institutions) and 65 (8(c)(ii) public enterprises — other); BSD4 column groups (an explicit class always wins over the counterparty-type rule: F:I / J:M / AL:AO …)                                        | Splits `GOVERNMENT_ENTITY` borrowers into public institutions vs public enterprises (BSD2 §8 / BSD4), and names NPISH (no counterparty type expresses it).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `ownership`                                   | `LOAN` (or the counterparty)                                                                   | `foreign` · `indigenous` (default when absent)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | BSD4 groups PRIVATE CORPORATIONS — FOREIGN (Z:AC) vs INDIGENOUS (AD:AG)                                                                                                                                                              | Foreign-controlled `CORPORATE` / `SME` borrowers.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `bog_classification`                          | `LOAN`                                                                                         | `current` · `olem` (or `other loans especially mentioned`) · `substandard` · `doubtful` · `loss`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | BSD8 rows 7 (item 1, previous balance), 15 (8(b) FX), 17 (10 interest in suspense), 18 (11 allowable security), 21 (13 provisions) × columns C:G; BSD8-Annexure column N and the 50-largest ranking; BSD5A (via its BSD8 dependency) | The Guide's five-bucket loan classification. Without it the platform proxies from `ifrs9_stage` (1 → Current, 2 → OLEM, 3 → non-performing, split unknown), so Substandard / Doubtful / Loss stay input_required on stage-only feeds.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `days_past_due`                               | `LOAN`                                                                                         | integer (calendar days, ≥ 0)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | recorded verbatim; the delinquency backstop behind `bog_classification` (Guide "Notes to BSD8": 0–<30 current · 30–<90 OLEM · 90–<180 substandard · 180–<360 doubtful · ≥360 loss) — no return cell reads it directly today          | Days the oldest unpaid instalment is overdue at `as_of_date`. Send it with `bog_classification` so the classification is auditable.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `interest_in_suspense_ghs`                    | `LOAN`                                                                                         | number (cedi)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | BSD8 row 17 (item 10, cumulative interest in suspense) × C:G                                                                                                                                                                         | Cumulative interest suspended (non-accrual) on the facility.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `ecl_provision_ghs`                           | `LOAN`                                                                                         | number (cedi)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | BSD8 row 21 (item 13 provisions) × C:G and Annexure column O; BSD5A (via BSD8); the capital / ECL engines and the large-exposure return (position-level allowance)                                                                   | Impairment allowance held against the facility.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `crm_collateral_ghs` / `crm_collateral_class` | `LOAN`                                                                                         | see the CRM table above                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | BSD8 row 18 (item 11 allowable security — classes `CASH`, `SOVEREIGN_DEBT`) and Annexure columns L / M                                                                                                                               | (as documented above)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `branch_id`                                   | any                                                                                            | string                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | BSD8-Annexure column C (Branch)                                                                                                                                                                                                      | Booking branch of the facility.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `employer`                                    | `LOAN`                                                                                         | string — the employer's name, one spelling per employer                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Credit module concentration monitor (employer dimension), employer delinquency EWI                                                                                                                                                   | The borrower's employer for payroll / check-off lending. The defining risk of a payroll book is one employer failing to remit deductions, so the monitor treats employer as a first-class concentration dimension (BoG Credit Concentration Guidelines, Sept 2025, §17(c)). Loans without it are excluded from the employer view and disclosed as coverage — never grouped as "Unknown". Do NOT put the employer in the counterparty's `group_reference`: that field means BoG connected-party grouping, and an employer is not a co-obligor.                                                                                                                                                                                                                                                                                                       |
| `repayment_source`                            | `LOAN`                                                                                         | `payroll_deduction` · `cagd_checkoff` · `employer_checkoff` · `standing_order` · `direct_debit` · `cash` · `other`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Credit module segmentation                                                                                                                                                                                                           | How the facility actually repays. `cagd_checkoff` is the Controller & Accountant-General payroll deduction scheme; `employer_checkoff` a private employer's.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `employer_sector`                             | `LOAN`                                                                                         | one of the 63 BSD4 sector keys                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | Credit module (employer dimension enrichment)                                                                                                                                                                                        | The employer's own industry, when it differs from the loan's `sector` (a salary loan carries `services.salary_credit`; the employer may be a mine or a hospital). Optional.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `institution_class`                           | positions with an `NBFI` counterparty (or the counterparty)                                    | `rural_bank` · `discount_house` · `savings_and_loans` · `credit_union` · `building_society` · `other_depository` · `other_financial` · `other`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | BSD2 rows 23, 25–28, 49–51, 86–89, 105–108, 172–174, 181–183, 191; BSD1 (discount-house call money); BSD4 OTHER DEPOSITORY (R:U, the first six values) vs OTHER FINANCIAL (V:Y)                                                      | The Guide's split of non-bank financial institutions into depository vs other.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `instrument`                                  | `SECURITY_HOLDING`, `CASH`, `OTHER_ASSET`, `OTHER_LIABILITY`, `INTERBANK_BORROWING`, `DEPOSIT` | `fx_notes_coins` · `cheques_for_clearing` · `repo_receivable` · `repo_payable` · `tbill` · `tbill_other` · `gog_bond` · `gog_bond_other` · `gog_stock` · `ggilb` · `tor_bond` · `bog_bill` · `bog_bond` · `bog_bond_other` · `bog_other` · `cocoa_bill` · `grains_bill` · `cotton_bill` · `bill` · `finsap_bond` · `ssnit_educational_bond` · `term_borrowing` · `bond_issued` · `certificate_of_deposit` · `special_deposit` · `margin_against_contingent` · `tor_margin_account`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | BSD2 rows 7, 19, 33, 36–39, 41–46, 54–56, 74–78, 80–81, 143–144, 186, 190–193, 275, 277; BSD1 rows for bills / bonds / CDs / special deposits / margins; BSD5A; BSD14 column K (`certificate_of_deposit`)                            | Names the official instrument line a security / balance belongs to.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `tenor_days` / `tenor_years` / `tenor`        | `SECURITY_HOLDING`, `INTERBANK_PLACEMENT`, `INTERBANK_BORROWING`                               | integer days (`28`, `56`, `91`, `182`) / integer years (`1`, `2`, `3`) / `call`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | BSD2 rows 36–46, 76–77, 80 (bill / bond tenor) and 25, 180–183 (`tenor=call`); BSD1                                                                                                                                                  | Original tenor of the paper; `call` marks money at call.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `tenor_months`                                | `DEPOSIT` (FIXED)                                                                              | integer months                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | BSD14 columns D:J (time-deposit tenor buckets 1·2·3·6·12·24·36; else derived from the contractual dates)                                                                                                                             | Original term of a time deposit.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `hqla_level`                                  | `SECURITY_HOLDING`                                                                             | `L1` · `L2A` · `L2B`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | the LCR numerator (HQLA stock: per-level Basel haircut, then the 40% Level-2 and 15% Level-2B caps); LCR-NSFR, BSD3                                                                                                                  | **The institution's own Basel HQLA classification of the holding.** The platform establishes the level itself only where the canonical evidence settles it — domestic sovereign / central-bank paper in the reporting currency is Level 1 (BCBS 238 ¶50(d)-(e)). It will NOT guess the rest: a public-sector or multilateral issuer turns on the claim's Basel risk weight (0% ⇒ Level 1 ¶50(c), 20% ⇒ Level 2A ¶52(a)), which this schema does not carry, and foreign-currency sovereign paper additionally needs the ¶50(e) same-currency outflow test. Those holdings are **excluded from HQLA entirely**, with the reason stated on the derivation, until this attribute classifies them — send it for every security whose tier is not domestic sovereign. A value outside the three levels excludes the holding; it is never read as Level 1. |
| `long_term`                                   | `SECURITY_HOLDING`                                                                             | boolean                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | BSD2 rows 87, 100, 101                                                                                                                                                                                                               | Long-term paper (Guide's long/short split of NBFI and corporate holdings).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `leg`                                         | `FX_HEDGE`, `DERIVATIVE` (`INTEREST_RATE_SWAP`)                                                | `receivable` · `payable`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | BSD2 rows 18 (swaps receivable, Annex 2c) and 187 (swaps payable); BSD5A                                                                                                                                                             | Which leg of a swap the position row carries.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `issuer_class`                                | `SECURITY_HOLDING` with a `GOVERNMENT_ENTITY` issuer                                           | `public_institution` · `public_enterprise` (BSD1 FINSAP bonds: `soe` · `private`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | BSD2 rows 52, 56, 94–98; BSD1 FINSAP rows                                                                                                                                                                                            | Public-institution vs public-enterprise issuer.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `scheme`                                      | `LOAN`                                                                                         | `cocoa_syndicated` · `staff_advance`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | BSD2 row 64 (8(c)(i) cocoa syndicated loan; BSD4 → PUBLIC ENTERPRISES); BSD2 Annex 4 row 11 (of which staff advances)                                                                                                                | Named lending schemes the returns list separately.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `relationship`                                | `SECURITY_HOLDING` (or the counterparty)                                                       | `subsidiary_or_associate`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | BSD2 rows 104–110 (investments in subsidiaries / associates)                                                                                                                                                                         | Equity / debt holdings in group entities.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `depositor_class`                             | `DEPOSIT` with a `GOVERNMENT_ENTITY` depositor (or the counterparty)                           | `public_enterprise` · `public_institution`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | BSD2 rows 231/232, 239/240, 247/248, 255/256 (§25 deposits by depositor class × account type) and Annex 13                                                                                                                           | Public-enterprise vs public-institution depositor. The account type itself is the typed `deposit_account_type` field above.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `facility_type`                               | `LOAN`                                                                                         | `scheduled` · `unscheduled` · `overdraft` · `acceptance` · `other`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | BSD2 Annex 4 rows 8–11 × B:F                                                                                                                                                                                                         | Guide Annex 4 facility categories (the annex total ties to BSD2 `D68` only when every LOAN carries one).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `obs_category` / `obs_status`                 | `LC_GUARANTEE`, `COMMITMENT_UNDRAWN`                                                           | `obs_category` extends the LMT values above with `acceptance` · `endorsement` · `other_obligation`; `obs_status` ∈ `performing` · `non_performing`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | BSD2 Annex 16 rows 6–10 × E:H (FX / cedi × performing / non-performing); Annex 16 `I11` ties to BSD2 `D282` when every LC/guarantee carries both                                                                                     | Contingent-liability class and performance status.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `balance_ghs` / `notional_ghs`                | any foreign-currency position                                                                  | number (cedi equivalent at `as_of_date`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | every `positions.sum` line of BSD2 (Foreign column "converted into cedis"), BSD1, BSD4, BSD8, BSD14 weights, module fact derivation                                                                                                  | The bank's own cedi equivalents for the applicable measure. Off-balance-sheet rows use `notional_ghs` and do not require `balance_ghs`; governed FX quotes and generation refusals follow the rules below.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |

For prudential returns, native foreign amounts are never treated as cedis.
Reporting-currency amounts follow these rules:

- `positions.sum`, BSD1 cedi measures, BSD4, BSD6, BSD8 and BSD11 amounts, and BSD14
  weights, use the stated amount for the measure (`balance_ghs` or `notional_ghs`),
  else a governed preferred FX spot at the valuation date. BSD1 uses the cell's
  business date, including each side of a weekly change; BSD8 opening balances use
  the day before the period starts. Other measures use period end. BSD1 Annex 1
  native-unit balances are exempt from reporting-currency conversion.
- LE, BSD3 and LMT require `balance_ghs` for foreign on-balance-sheet rows, plus
  `notional_ghs` when a native undrawn notional or CCF applies.
- Foreign off-balance-sheet `LC_GUARANTEE` and `COMMITMENT_UNDRAWN` rows do not
  require `balance_ghs`: their measure is the stated `notional_ghs`, else their native
  notional converted using a governed preferred FX spot at the valuation date
  (including LE, BSD3 and LMT). The return applies CCF where its measure requires it.

Missing required conversions abort generation with HTTP 409 `foreign_amount_not_stated`,
naming the positions and the amounts to ingest. A package missing these conversion inputs
cannot reach approval or numeric roll-ups. Other existing `input_required` cells retain
their handling.

### Loan events (`loan_event`) — the loan-book movement plane

Positions and snapshots are STOCKS; loan events are the FLOWS the Bank of
Ghana's Notice BG/GOV/SEC/2025/23 reports monthly (write-offs split
wilful/non-wilful, recoveries by collateral class, restructuring activity by
measure) and BSD8's movement schedule asks for. Push them under the
`"loan_event"` entities key, or upload a sheet/file named `loan_events`
(template: `onboarding/sample_bank/loan_events_template.csv`). Events are
cumulative history: each event is its own row with its own
`source_reference`; re-pushing a reference corrects THAT event (supersession),
and history never needs re-sending wholesale.

| Field                                   | Required     | Meaning                                                                                                                                                                                                                                                                                                                                                       |
| --------------------------------------- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `source_reference`                      | yes          | The event's own stable id in the source system.                                                                                                                                                                                                                                                                                                               |
| `event_type`                            | yes          | `DISBURSEMENT` · `REPAYMENT` · `WRITE_OFF` · `RECOVERY` · `RESTRUCTURE`. An unknown type fails translation.                                                                                                                                                                                                                                                   |
| `event_subtype`                         | per type     | WRITE_OFF: `wilful` · `non_wilful` (Notice Appendix II 3a/3b). RECOVERY: `property_collateral` · `non_property_collateral` · `unsecured`. RESTRUCTURE: `interest_only` · `reduced_payment` · `moratorium` · `arrears_capitalization` · `rate_reduction` · `maturity_extension` · `assisted_sale` · `rescheduled`. An unknown subtype lands flagged `warning`. |
| `event_date`                            | yes          | The business date the movement happened.                                                                                                                                                                                                                                                                                                                      |
| `position_source_reference`             | yes          | The facility, in source-reference terms. May reference a loan from an earlier batch; an unknown reference lands flagged `warning`, never blocked (the loan file and the events file legitimately arrive separately).                                                                                                                                          |
| `amount`                                | yes          | Positive movement amount in `currency`.                                                                                                                                                                                                                                                                                                                       |
| `currency`                              | yes          | ISO 4217.                                                                                                                                                                                                                                                                                                                                                     |
| `amount_ghs`                            | no           | The bank's own reporting-unit conversion. Absent on a base-currency event it falls back to `amount`; absent on a foreign-currency one the event is excluded from reporting-unit totals — never converted at an invented rate.                                                                                                                                 |
| `attributes.bog_approval_reference`     | write-offs   | The BoG write-off approval reference (Notice ¶9 / Appendix I).                                                                                                                                                                                                                                                                                                |
| `attributes.fully_provisioned`          | write-offs   | Whether the facility was fully provisioned at write-off.                                                                                                                                                                                                                                                                                                      |
| `attributes.related_writeoff_reference` | recoveries   | The write-off event this recovery relates to.                                                                                                                                                                                                                                                                                                                 |
| `attributes.repayment_frequency`        | restructures | `monthly` · `quarterly` · `semi_annual` · `bullet` — drives the cure rule (6 consecutive payments; 4 for semi-annual; bullet cures only at settlement).                                                                                                                                                                                                       |
| `attributes.scheduled_amount_ghs`       | restructures | The revised scheduled payment.                                                                                                                                                                                                                                                                                                                                |

CSV / workbook upload path: the same keys are captured from a sheet whose
column headers are `attributes.<key>` (e.g. `attributes.sector`,
`attributes.bog_classification`) — no mapping-config change is needed; a
mapping's `attribute_columns` list keeps working for banks whose export headers
cannot be renamed. `onboarding/sample_bank/loans_template.csv` is the
header-only loans template covering the LOAN keys above; the API-push client
`scripts/ingest_push.py` folds the same headers into the `attributes` object.
Re-pushing a position (same `source_reference`, same `source_system`, same
`as_of_date`) supersedes its previous snapshot **whole** — the new snapshot
carries only what the re-push sent, so a classification re-push must resend
every field and attribute of the position, not just the new keys.

### 3.5 Reference datasets

Reference rows are preserved verbatim as payloads (values stringified, dates
ISO) under their dataset kind and consumed as-is by calculation modules.
Registered schemas are enforced at ingestion for `capital_structure`,
`business_units`, `performance_targets` and `gl_segment_balances`; other kinds
retain their documented shape without schema enforcement. Valid keys under
`"reference"`:

| Key                      | Typical row fields (from the Sample Bank dataset)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `capital_structure`      | `capital_component`, `amount_ghs`, `tier` — see the [capital register contract](#capital_structure-contract) below.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `behavioral_assumptions` | `product_code`, `assumption`, `value`, …                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `yield_curve`            | `curve_name`, `currency`, `tenor_months`, `rate`, `quote_date`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `fx_rates_current`       | `pair`, `rate`, `quote_date`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `fx_rates_historical`    | `pair`, `rate`, `quote_date`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `historical_cashflows`   | `date`, `inflow`, `outflow`, …                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `historical_financials`  | `month`, `total_assets`, `net_income`, …                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `business_units`         | `business_unit_id`, `business_unit_name` (**canonical** — the names every reader keys on); optional `region` (the bank's own region for the unit; **the only source of a branch's region** — nothing is inferred from an address, and until the field is supplied the region reads as unassigned), `parent_unit_id` (never the unit itself), `outlet_number`, `cost_centre`, `notes` — the branch / business-unit register: one row per unit, the whole register per push (latest as-of wins). A position's `attributes.branch_id` is matched against `business_unit_id`. The spelling this table used to document, `unit_id` / `name`, is accepted as an alias of the canonical pair (a row must not give both spellings with different values), but reference rows are preserved verbatim and a mapping config can only drop columns, never rename one (§4) — so send the canonical names. Schema: `app/domain/ingestion/reference_schemas/business_units.py`.                                                                                                                                                                                                                                 |
| `institution`            | `institution_id`, `name`, …                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `gl_mapping_bsd7`        | `gl_account_code` \| `gl_prefix` (one of the two), `bsd7_item` (`1a` … `32`, the official BSD7A/BSD7B item tags), `sign` (`1` \| `-1`), `balance_basis` (`ytd` \| `period`), `gl_account_name`, `notes` — the bank's chart-of-accounts → P&L item register; a register (re-push whole; latest as-of wins). Spec: `docs/data_engine/datasets/gl_mapping_bsd7.md`. Pairs with INCOME/EXPENSE `gl_account` records (§3.1) pushed once per month-end.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `interest_accruals`      | `as_of_date`, `bsd2_row` (row number of the "Accrued interest" line on the official BSD2 sheet: `20`, `29`, `32`, `141`, `145`, `151`, `156`, `161`, `166`, `177`, `195`, `204`, `211`, `218`, `225`, `234`, `242`, `250`, `258`), `side` (`asset` \| `liability`), `currency`, `accrued_interest_ghs`, `accrued_interest_native`, `gl_account_code`, `position_reference`, `counterparty_reference`, `notes` — accrual balances at the reporting date; one reporting date per push (batch `as_of_date` = that date). Spec: `docs/data_engine/datasets/interest_accruals.md`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `tariff_schedule`        | `form` (`BSD15A` \| `BSD15B`), `sheet` (`DOMESTIC` \| `RANGE` \| `INTL`), `row_key` (the official tariff row, `"<item>.<n>"` — e.g. `1.1` COT minimum, `S1.1` Savings S1 initial deposit, `17.1` account closure; full generated list in the spec), `charge_value` (**what the return prints in that cell** — text on `DOMESTIC` / `INTL`, a cedi amount on `RANGE`; never `N/A` / `Nil` / `-`, which the Data Engine reads as null — write `Free` / `Not applicable`), `label`, `charge_basis` (`flat` \| `percent` \| `per_item` \| `range`), `min_ghs`, `max_ghs`, `currency`, `effective_from`, `notes` — the bank's published tariff guide keyed by the official BSD15A/BSD15B rows; a register (re-push whole on each tariff revision; latest as-of ≤ reporting date wins). Spec: `docs/data_engine/datasets/tariff_schedule.md`.                                                                                                                                                                                                                                                                                                                                                          |
| `atm_operations`         | `month` (ISO month-end = the batch `as_of_date`), `atm_id`, `station` (Station / Branch as printed on BSD16), `cards_issued`, `min_withdrawal_ghs`, `max_withdrawal_ghs` (cedis), `region`, `branch_code`, `cards_active`, `txn_count`, `txn_value_ghs`, `cash_dispensed_ghs`, `downtime_hours`, `notes` — one row per terminal for ONE reporting month per push (BSD16 reads the latest batch on/before the period end and lists terminals in file order, first 50). Spec: `docs/data_engine/datasets/atm_operations.md`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `remittance_flows`       | `month` (ISO month-end = the batch `as_of_date`), `direction` (`inbound` \| `outbound`), `corridor_country` (ISO-3166 alpha-2), `region` (`uk` \| `usa_canada` \| `eu` \| `ecowas` \| `rest_of_africa` \| `other` — BSD17 Sheet 2 roll-up), `recipient_class` (`individual` \| `exporter` \| `service_provider` \| `ngo` \| `embassy` \| `other` — BSD17 Sheet 1), `channel` (`bank` \| `mto` \| `mobile_money` \| `other`), `currency`, `amount_fx`, `amount_usd` (the bank's own US$ equivalent — the reported figure), `amount_ghs`, `transaction_count`, `operator_name`, `notes` — monthly aggregate per (direction, corridor, recipient class, channel, currency); ONE reporting month per push. Spec: `docs/data_engine/datasets/remittance_flows.md`.                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `subsidiaries`           | `reporting_date` (ISO date = the batch `as_of_date`), `subsidiary_id` (the bank's stable id), `name`, `country_code`, `entity_type` (`bank` \| `nbfi` \| `insurance` \| `other`), `functional_currency`, `ownership_pct` (0–100), `consolidation_method` (`full` \| `equity` \| `none`), `control_via_board` (`true` \| `false`), `total_assets_ghs`, `total_liabilities_ghs`, `equity_ghs`, `net_profit_ytd_ghs`, `intercompany_receivable_ghs` (due FROM the subsidiary), `intercompany_payable_ghs` (due TO it); optional `tier1_capital_ghs`, `rwa_ghs`, `minority_interest_ghs` (required when `full` and ownership < 100 — the group's own working), `minority_interest_tier2_pref_ghs`, `investment_carrying_ghs`, `intercompany_receivable_type`, `intercompany_payable_type`, `regulator`, `licence_number`, `notes` — the subsidiary register + book, one row per subsidiary per reporting date; the whole register at one date per push (latest as-of wins). Feeds BSD9 minority interests + Annexure and BSD5B rows 3 / 18. Spec: `docs/data_engine/datasets/subsidiaries.md`.                                                                                                       |
| `capital_expenditure`    | `period_end` (ISO date = the batch `as_of_date`), `asset_class` (`land_buildings` \| `staff_land_premises` \| `furniture_equipment` \| `computers` \| `other_office_equipment` \| `motor_vehicles` \| `other_property_legal_rights`), `opening_nbv_ghs`, `additions_purchased_ghs`, `additions_finance_lease_ghs`, `additions_hire_purchase_ghs`, `disposal_proceeds_ghs`, `disposals_nbv_ghs`, `depreciation_ghs`, `closing_cost_ghs`, `accumulated_depreciation_ghs`, `closing_nbv_ghs` (= cost − accumulated depreciation, validated); optional `currency` (booking currency; blank = base ⇒ BSD2 Domestic), `capital_wip_ghs`, `wip_closing_ghs`, `contracted_not_provided_ghs`, `authorised_not_contracted_ghs`, `forecast_next_6m_ghs`, `forecast_0_3m_ghs`, `forecast_3_6m_ghs`, `budget_ghs`, `notes` — the fixed-asset / capex register, one row per (period, asset class); one period per push (half-year movements for BSD10 A–H, period-end stock for BSD2 item 12 rows 115–121 / 123). Spec: `docs/data_engine/datasets/capital_expenditure.md`.                                                                                                                                    |
| `performance_targets`    | `period` (ISO date: the **last day** of the window; must agree with `grain`), `grain` (`month` \| `quarter` \| `half_year` \| `year`), `measure_id` (the BI measure targeted — `loans.balance_rc`, `engine.car_pct.crd.official`), `time_behaviour` (`stock` \| `flow`; **required, never defaulted** — a stock target is the level to be standing at `period`, a flow target the amount to accumulate over the window `grain` names; reading one as the other is silently wrong by a period), `value` (in the measure's own unit: reporting currency for an amount, percentage points for a `_pct`; the row carries no unit), `version` (`budget` \| `reforecast`); optional `scope_dimension` / `scope_value` (which dimension the target applies to and its value, e.g. `branch.code` / `BR-001`; given together or not at all — a bank-wide target names neither) and `notes` — the bank's budget / reforecast figures, one row per (period, grain, measure, scope, version); re-push whole, latest as-of wins. Schema: `app/domain/ingestion/reference_schemas/performance_targets.py`.                                                                                                     |
| `gl_segment_balances`    | `as_of_date` (ISO date = the batch `as_of_date`), `gl_account_code` (must match an `INCOME` / `EXPENSE` `gl_account.account_code` for the same month), `branch_id` (matched against `business_units.business_unit_id`, exactly as `position.attributes.branch_id` is; `__UNALLOCATED__` is **reserved** for the remainder the platform computes and is refused), `ytd_balance` (the branch's fiscal-year-to-date balance of that account, same convention and sign as the account's own `balance`); optional `currency` (blank = the reporting currency, as on a `gl_account` record), `gl_account_name`, `branch_name`, `notes` — the bank's branch breakdown of its profit-and-loss ledger. One row per (`as_of_date`, `gl_account_code`, `branch_id`, `currency`); ONE reporting date per push, the whole breakdown in that batch, and the batch must fall in the same calendar month as the ledger balances it breaks down. The breakdown may be partial: the platform adds an explicit unallocated line so the branches sum to the institution's ledger. Spec: `docs/data_engine/datasets/gl_segment_balances.md`. Schema: `app/domain/ingestion/reference_schemas/gl_segment_balances.py`. |

#### `capital_structure` contract

Each row requires `capital_component`, `amount_ghs` and `tier`. Send
`amount_ghs` as a finite Decimal value without grouping commas; values such as
`"-20000000"` are accepted, while `"-20,000,000"`, `NaN` and `Infinity` are
refused. Amounts are preserved for Decimal parsing rather than coerced to float.

Use `CET1`, `AT1`, `T2` or their `_DEDUCTION` forms. The parser also accepts
`Common Equity Tier 1`, `Additional Tier 1` and `Tier 2`, with case, spaces,
underscores and hyphens ignored; each alias also accepts a deduction suffix.
Blank, unknown or ambiguous tiers such as `Tier 1` are refused. A negative
amount or a deduction tier marks a deduction. The BoG Credit Risk Reserve
(`credit_risk_reserve`) is excluded from the capital base regardless of tier;
it must not be submitted as a qualifying capital component.

A capital row that fails translation rejects the whole ingestion batch; no
partial register is published. Previously stored, unrecognised tiers refuse
the entire capital derivation, producing `capital_register_refused` instead of
usable capital components. Capital-dependent calculations return their named
refusal or unavailable-input state (SDIs use `data_quality_block`), with
corrective guidance. Correct the refused tiers, re-ingest the complete register
and re-derive facts before retrying. Malformed stored SDI amounts make the
credit/concentration denominator unavailable (`capital_base_unavailable`),
with guidance to correct the amounts and re-ingest the complete register.
Schema and tier parsing are owned by
`backend/app/domain/ingestion/reference_schemas/capital_structure.py` and
`backend/app/domain/ingestion/capital_tiers.py`.

---

## 4. Mapping configs (when your field names differ)

If your middleware cannot emit canonical field names, use an authorized human
session to activate a
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
   the same mapping — even under a _new_ idempotency key — returns the
   previously accepted batch with `"reused": true` instead of duplicating
   canonical state.

A rejected or failed batch is immutable history: fix the data and push again
under a **new** idempotency key.

---

## 6. Error semantics

| Status | Meaning                                                                                                                                                                                                                               |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `404`  | Unknown bank or push batch **for your tenant** (cross-tenant access is indistinguishable from not-found).                                                                                                                             |
| `409`  | State conflict: staging into a committed push batch, or reusing an idempotency key with a different `as_of_date`.                                                                                                                     |
| `413`  | Records page above 5,000 records — split it.                                                                                                                                                                                          |
| `422`  | Envelope shape validation: unknown `entities`/`reference` key, a record that is not a JSON object, an empty page, or committing with nothing staged. The `error.details` list carries JSON pointers (`loc`) to the offending element. |
| `503`  | Storage tier unavailable — retry later.                                                                                                                                                                                               |

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
  --token "$AEQ_INTEGRATION_KEY" \
  --bank-id BK-XXXXXXXX \
  --as-of 2026-04-30
```

Set `AEQ_INTEGRATION_KEY` to a key issued for the exact `--bank-id` target.

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

---

## 8. Pulling analytics out: the Stage B feed

The reverse direction. `GET /banks/{bank_id}/bi/feeds/{dataset}` serves one
**curated** analytics dataset, streamed, so a report server (Power BI Report
Server, or any HTTP-capable BI tool) can build a model on the same numbers the
platform files — without a database login, which is not offered and cannot be
made safe (`backend/docs/powerbi_stage_b.md` §1 explains why).

**Your BI team should read `backend/docs/powerbi_stage_b.md`**, not this section:
it carries the dataset columns, the loader contract, a worked Power Query
example, and the data-residency caveat your institution signs off on. What is
here is the wire contract.

### Request

```
GET /api/v1/banks/{bank_id}/bi/feeds/{dataset}?format=ndjson&cursor=<token>
Authorization: Bearer aeq_live_…        # a `reader` key for THIS bank
```

| Parameter        | Values                                            | Meaning                                                   |
| ---------------- | ------------------------------------------------- | --------------------------------------------------------- |
| `dataset` (path) | `loan_book`, `deposit_book`, `regulatory_metrics` | One of the curated datasets. Anything else is `404`.      |
| `format`         | `ndjson` (default), `csv`                         | One JSON object per line, or a header row then data rows. |
| `cursor`         | a token a previous pull returned                  | Omit it for a full synchronisation.                       |

There is no way to name a table, a column, a filter, a row limit or an as-of
date. The dataset declaration decides all of them, which is what makes the
surface safe to expose to a machine at all.

### Response

`200` with the payload as the body. Column names are AequorOS catalogue member
ids (`loans.balance_rc`); amounts are exact decimals in the institution's
reporting currency; **an empty cell or a JSON `null` means "nothing to measure",
never zero.** Provenance travels in headers, never in the payload:

| Header                                                | Meaning                                                                                                                                                                                                                                                                       |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `X-Bi-Feed-Dataset` / `X-Bi-Feed-Grain`               | Which dataset, and what one row is.                                                                                                                                                                                                                                           |
| `X-Bi-Feed-Columns`                                   | The column list, in payload order. Validate against this.                                                                                                                                                                                                                     |
| `X-Bi-Feed-Cursor` / `X-Bi-Feed-Next-Cursor`          | The cursor you sent, and the one to send next. `none` means **do not advance**.                                                                                                                                                                                               |
| `X-Bi-Feed-More-Available`                            | `true` when older reporting dates remain unserved: pull again at once.                                                                                                                                                                                                        |
| `X-Bi-Feed-Reporting-Dates` / `-Reporting-Date-Count` | Exactly which reporting dates the payload covers.                                                                                                                                                                                                                             |
| `X-Bi-Feed-Data-Scope`                                | The slice of the institution this credential covers. A change here means your dataset changed shape.                                                                                                                                                                          |
| `X-Bi-Feed-Trust`                                     | **Removed by founder decision 2026-09-29.** It carried a reconciliation verdict (`green` / `amber` / `red` / `grey`) of the feed's figures against the regulatory returns; BI carries no such verdict. A loader must not require this header. Freshness is `X-Bi-Feed-Build`. |
| `X-Bi-Feed-Build` / `X-Bi-Feed-Catalogue-Version`     | Which analytics build and which measure definitions produced the rows.                                                                                                                                                                                                        |
| `X-Bi-Feed-Unit`                                      | The reporting currency. It is deliberately not in any column name.                                                                                                                                                                                                            |

### The cursor, and the one thing your loader must do

The cursor is a position in **build** time, not a business date, because a bank's
book is restated: a correction to March arrives in September and the platform
rebuilds March. So:

> **Replace by reporting date.** For every date in `X-Bi-Feed-Reporting-Dates`,
> delete your rows for that date and insert the payload's. Never append.

That is what makes a restatement land. Because replacement is idempotent, the
feed errs towards re-sending: `X-Bi-Feed-Next-Cursor: none` means send the same
cursor again next time, and a date may legitimately arrive twice. Only reporting
dates whose analytics build fully succeeded are served — a partial date is absent,
never present with zeros.

### Refusals

| Status | When                                                                                                                                                        |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `401`  | The credential is missing, unknown or revoked.                                                                                                              |
| `403`  | A human token; a `writer` key; a `reader` key without the authority the dataset's figures need.                                                             |
| `404`  | BI is not enabled for the deployment; the institution belongs to another tenant; the key names a different institution; the dataset is not in the registry. |
| `422`  | The cursor is not a token this feed issued. It is never treated as "start from the beginning".                                                              |

**Every pull is recorded** — the credential, the dataset, the cursor in and out,
the reporting dates, the row count and whether it was allowed or refused. Ask
your AequorOS administrator for that record whenever you need it.
