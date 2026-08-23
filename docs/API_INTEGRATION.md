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

An **integration key**, sent as the bearer credential on every request:

```
Authorization: Bearer aeq_live_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

Your administrator generates the key once in the dashboard (Data Engine →
API Push → Integration keys). It authenticates your middleware as a
dedicated service account with data-push rights only, is shown exactly once
at generation (the platform stores only a hash), and can be revoked
instantly from the same screen. Rotate by generating a new key, switching
your middleware, then revoking the old one.

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
> Settings → Institution profile). It is the bank's one and only identifier
> across the platform. Lowercase input is accepted and normalized.

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
| `resident` | boolean | no | Residency relative to the reporting institution's jurisdiction (liquidity-directive classification). Accepts `true`/`false`, `0`/`1`, `"Y"`/`"N"`, `"yes"`/`"no"`. |
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
| `encumbered` | boolean | no | Asset tied to legal/regulatory/contractual restrictions preventing sale, transfer or pledge (BoG liquidity-directive definition). Unset = treated as unencumbered. Boolean fields accept `true`/`false`, `0`/`1`, `"Y"`/`"N"`, `"yes"`/`"no"`. |
| `encumbrance_reason` | string | no | What the asset is pledged to (e.g. `"BoG repo"`, `"margin"`). |
| `owning_entity` | string | no | Legal entity / affiliate owning the asset (collateral management). |
| `asset_location` | string | no | Where the asset is held (e.g. `"CSD"`) — the unencumbered-assets register's Location column. |
| `operational_purpose` | boolean | no | Correspondent balance held for operational purposes and readily withdrawable. |
| `redeemable_within_two_days` | boolean | no | Marketable and redeemable within two working days. |
| `pledged_as_collateral` | boolean | no | Deposit pledged to secure a credit facility (drives the concentration-netting rule). |
| `lien_reference` | string | no | Source reference of the facility the deposit secures. |
| `deposit_account_type` | enum | no | `CURRENT`, `CALL`, `SAVINGS`, `FIXED`, `OTHER` — classifies deposits for the liquidity monitoring tables (volatile = current + call). |
| `attributes` | object | no | Instrument specifics (hedge pair, contract rate, MtM, swap legs, ECL, branch, …) — preserved verbatim and used by module fact derivation. Documented liquidity-directive conventions below. |

**Liquidity-directive attribute conventions.** The Liquidity Monitoring Tools
return reads these documented `attributes` keys when present (all optional;
sections that depend on them render only when a source supplies the data):

| Attribute key | Type | Feeds | Description |
| --- | --- | --- | --- |
| `obs_category` | string | Table 2 rows 13/15/16/17 | `lending_facility`, `letter_of_credit`, `guarantee`, or `obs_vehicle_facility`. Defaults: undrawn commitments → lending facilities; LC/guarantees → indemnities and guarantees. |
| `funding_instrument` | string | Table 8 | `negotiable_paper` marks a liability as a negotiable paper funding instrument. |
| `collateral_instrument` | string | Table 4 | Display name of collateral received against this position. |
| `collateral_asset_class` | string | Table 10 | `loans_advances`, `equity`, `debt_government`, `debt_financial`, `debt_nonfinancial`, or `other`. |
| `collateral_received_ghs` | number | Tables 4, 10 | Fair value of collateral received, available for encumbrance (cedi equivalent). |
| `collateral_rehypothecable` | boolean | Table 4 | Whether the received collateral can be re-pledged. |
| `collateral_rehypothecated_ghs` | number | Table 4 | Amount already re-pledged. |
| `collateral_unavailable_ghs` | number | Table 10 | Nominal of collateral received NOT available for encumbrance. |
| `collateral_group_issued` | boolean | Table 10 | Collateral issued by other entities of the reporting group. |
| `collateral_bog_eligible` | boolean | Table 10 | Collateral eligible for central-bank standing facilities. |
| `own_debt_available_ghs` | number | Table 10 | Own debt securities issued, available for encumbrance. |
| `own_debt_unavailable_ghs` | number | Table 10 | Own debt securities issued, not available for encumbrance. |

Credit-risk-mitigation conventions on **LOAN** positions (consumed by the
capital module's CRM recognition; the supervisory haircut comes from the
bank's `crm-haircuts` register, never from the payload):

| Attribute key | Type | Consumed by | Meaning |
|---|---|---|---|
| `crm_collateral_ghs` | number | Credit RWA (CRM) | Eligible collateral value (cedi equivalent) pledged against the loan. |
| `crm_collateral_class` | string | Credit RWA (CRM) | Collateral class the haircut register keys on (e.g. `CASH`, `GOLD`, `SOVEREIGN_DEBT`, `CORPORATE_DEBT`). Required alongside the value. |
| `crm_guarantee_ghs` | number | Credit RWA (CRM) | Eligible guarantee amount covering the loan. |
| `crm_guarantor_class` | string | Credit RWA (CRM) | Guarantor class the haircut register keys on. Required alongside the value. |

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

| Attribute key | Position types | Type / values | Feeds (form → cells) | Meaning |
|---|---|---|---|---|
| `sector` | `LOAN` | string — one of the 63 BSD4 sector keys: `agriculture.{cocoa_production,livestock_breeding,poultry_farming,other,forestry,logging,fishing}`, `mining.{bauxite,diamonds,gold,manganese,quarrying,other}`, `manufacturing.export.{food_drink_tobacco,textiles_clothing_footwear,sawmilling_wood_processing,paper_pulp_products,chemicals_fertilizers,iron_steel,boat_ship_building,motor_vehicles,other}`, `manufacturing.home.{…same nine…}`, `construction.{construction_works,building_construction}`, `utilities.{electricity,gas,water}`, `commerce.import.{motor_vehicles,machinery_heavy_equipment,other}`, `commerce.export.{cocoa,timber,other}`, `commerce.{cocoa_marketing,timber_marketing,diamond_marketing,mortgage_financing,other}`, `commerce.ofi.{hire_purchase,insurance,building_bodies}`, `transport.{railway,road,water,air,storage_warehousing,communications}`, `services.{printing_publishing,business,recreation,personal,salary_credit,other_incl_government}`, `miscellaneous` (a unique official leaf label such as `Cocoa Production` is accepted too) | BSD4 sheet `BSD4` rows 10–93 (63 leaf rows × performing / non-performing / No. of Cust. per borrower group, B:AO) and Annexes 4a/4b; BSD8-Annexure column D; BSD14 columns N:U (lending rates by sector) | The customer's industry (Guide BSD4). Once any LOAN carries `sector`, loans without a recognised value fall to *9. MISCELLANEOUS*. |
| `borrower_class` | `LOAN` (or the counterparty) | `public_institution` · `public_enterprise` · `npish` · `central_government` · `commercial_bank` · `other_depository_institution` · `other_financial_institution` · `private_foreign` · `private_indigenous` · `household` (plurals / `ofi` / `odi` / `individual` accepted) | BSD2 rows 62 (8(b) public institutions) and 65 (8(c)(ii) public enterprises — other); BSD4 column groups (an explicit class always wins over the counterparty-type rule: F:I / J:M / AL:AO …) | Splits `GOVERNMENT_ENTITY` borrowers into public institutions vs public enterprises (BSD2 §8 / BSD4), and names NPISH (no counterparty type expresses it). |
| `ownership` | `LOAN` (or the counterparty) | `foreign` · `indigenous` (default when absent) | BSD4 groups PRIVATE CORPORATIONS — FOREIGN (Z:AC) vs INDIGENOUS (AD:AG) | Foreign-controlled `CORPORATE` / `SME` borrowers. |
| `bog_classification` | `LOAN` | `current` · `olem` (or `other loans especially mentioned`) · `substandard` · `doubtful` · `loss` | BSD8 rows 7 (item 1, previous balance), 15 (8(b) FX), 17 (10 interest in suspense), 18 (11 allowable security), 21 (13 provisions) × columns C:G; BSD8-Annexure column N and the 50-largest ranking; BSD5A (via its BSD8 dependency) | The Guide's five-bucket loan classification. Without it the platform proxies from `ifrs9_stage` (1 → Current, 2 → OLEM, 3 → non-performing, split unknown), so Substandard / Doubtful / Loss stay input_required on stage-only feeds. |
| `days_past_due` | `LOAN` | integer (calendar days, ≥ 0) | recorded verbatim; the delinquency backstop behind `bog_classification` (Guide "Notes to BSD8": 0–<30 current · 30–<90 OLEM · 90–<180 substandard · 180–<360 doubtful · ≥360 loss) — no return cell reads it directly today | Days the oldest unpaid instalment is overdue at `as_of_date`. Send it with `bog_classification` so the classification is auditable. |
| `interest_in_suspense_ghs` | `LOAN` | number (cedi) | BSD8 row 17 (item 10, cumulative interest in suspense) × C:G | Cumulative interest suspended (non-accrual) on the facility. |
| `ecl_provision_ghs` | `LOAN` | number (cedi) | BSD8 row 21 (item 13 provisions) × C:G and Annexure column O; BSD5A (via BSD8); the capital / ECL engines and the large-exposure return (position-level allowance) | Impairment allowance held against the facility. |
| `crm_collateral_ghs` / `crm_collateral_class` | `LOAN` | see the CRM table above | BSD8 row 18 (item 11 allowable security — classes `CASH`, `SOVEREIGN_DEBT`) and Annexure columns L / M | (as documented above) |
| `branch_id` | any | string | BSD8-Annexure column C (Branch) | Booking branch of the facility. |
| `institution_class` | positions with an `NBFI` counterparty (or the counterparty) | `rural_bank` · `discount_house` · `savings_and_loans` · `credit_union` · `building_society` · `other_depository` · `other_financial` · `other` | BSD2 rows 23, 25–28, 49–51, 86–89, 105–108, 172–174, 181–183, 191; BSD1 (discount-house call money); BSD4 OTHER DEPOSITORY (R:U, the first six values) vs OTHER FINANCIAL (V:Y) | The Guide's split of non-bank financial institutions into depository vs other. |
| `instrument` | `SECURITY_HOLDING`, `CASH`, `OTHER_ASSET`, `OTHER_LIABILITY`, `INTERBANK_BORROWING`, `DEPOSIT` | `fx_notes_coins` · `cheques_for_clearing` · `repo_receivable` · `repo_payable` · `tbill` · `tbill_other` · `gog_bond` · `gog_bond_other` · `gog_stock` · `ggilb` · `tor_bond` · `bog_bill` · `bog_bond` · `bog_bond_other` · `bog_other` · `cocoa_bill` · `grains_bill` · `cotton_bill` · `bill` · `finsap_bond` · `ssnit_educational_bond` · `term_borrowing` · `bond_issued` · `certificate_of_deposit` · `special_deposit` · `margin_against_contingent` · `tor_margin_account` | BSD2 rows 7, 19, 33, 36–39, 41–46, 54–56, 74–78, 80–81, 143–144, 186, 190–193, 275, 277; BSD1 rows for bills / bonds / CDs / special deposits / margins; BSD5A; BSD14 column K (`certificate_of_deposit`) | Names the official instrument line a security / balance belongs to. |
| `tenor_days` / `tenor_years` / `tenor` | `SECURITY_HOLDING`, `INTERBANK_PLACEMENT`, `INTERBANK_BORROWING` | integer days (`28`, `56`, `91`, `182`) / integer years (`1`, `2`, `3`) / `call` | BSD2 rows 36–46, 76–77, 80 (bill / bond tenor) and 25, 180–183 (`tenor=call`); BSD1 | Original tenor of the paper; `call` marks money at call. |
| `tenor_months` | `DEPOSIT` (FIXED) | integer months | BSD14 columns D:J (time-deposit tenor buckets 1·2·3·6·12·24·36; else derived from the contractual dates) | Original term of a time deposit. |
| `hqla_level` | `SECURITY_HOLDING` | `L1` · `L2A` · `L2B` | the LCR numerator (HQLA stock: per-level Basel haircut, then the 40% Level-2 and 15% Level-2B caps); LCR-NSFR, BSD3 | **The institution's own Basel HQLA classification of the holding.** The platform establishes the level itself only where the canonical evidence settles it — domestic sovereign / central-bank paper in the reporting currency is Level 1 (BCBS 238 ¶50(d)-(e)). It will NOT guess the rest: a public-sector or multilateral issuer turns on the claim's Basel risk weight (0% ⇒ Level 1 ¶50(c), 20% ⇒ Level 2A ¶52(a)), which this schema does not carry, and foreign-currency sovereign paper additionally needs the ¶50(e) same-currency outflow test. Those holdings are **excluded from HQLA entirely**, with the reason stated on the derivation, until this attribute classifies them — send it for every security whose tier is not domestic sovereign. A value outside the three levels excludes the holding; it is never read as Level 1. |
| `long_term` | `SECURITY_HOLDING` | boolean | BSD2 rows 87, 100, 101 | Long-term paper (Guide's long/short split of NBFI and corporate holdings). |
| `leg` | `FX_HEDGE`, `DERIVATIVE` (`INTEREST_RATE_SWAP`) | `receivable` · `payable` | BSD2 rows 18 (swaps receivable, Annex 2c) and 187 (swaps payable); BSD5A | Which leg of a swap the position row carries. |
| `issuer_class` | `SECURITY_HOLDING` with a `GOVERNMENT_ENTITY` issuer | `public_institution` · `public_enterprise` (BSD1 FINSAP bonds: `soe` · `private`) | BSD2 rows 52, 56, 94–98; BSD1 FINSAP rows | Public-institution vs public-enterprise issuer. |
| `scheme` | `LOAN` | `cocoa_syndicated` · `staff_advance` | BSD2 row 64 (8(c)(i) cocoa syndicated loan; BSD4 → PUBLIC ENTERPRISES); BSD2 Annex 4 row 11 (of which staff advances) | Named lending schemes the returns list separately. |
| `relationship` | `SECURITY_HOLDING` (or the counterparty) | `subsidiary_or_associate` | BSD2 rows 104–110 (investments in subsidiaries / associates) | Equity / debt holdings in group entities. |
| `depositor_class` | `DEPOSIT` with a `GOVERNMENT_ENTITY` depositor (or the counterparty) | `public_enterprise` · `public_institution` | BSD2 rows 231/232, 239/240, 247/248, 255/256 (§25 deposits by depositor class × account type) and Annex 13 | Public-enterprise vs public-institution depositor. The account type itself is the typed `deposit_account_type` field above. |
| `facility_type` | `LOAN` | `scheduled` · `unscheduled` · `overdraft` · `acceptance` · `other` | BSD2 Annex 4 rows 8–11 × B:F | Guide Annex 4 facility categories (the annex total ties to BSD2 `D68` only when every LOAN carries one). |
| `obs_category` / `obs_status` | `LC_GUARANTEE`, `COMMITMENT_UNDRAWN` | `obs_category` extends the LMT values above with `acceptance` · `endorsement` · `other_obligation`; `obs_status` ∈ `performing` · `non_performing` | BSD2 Annex 16 rows 6–10 × E:H (FX / cedi × performing / non-performing); Annex 16 `I11` ties to BSD2 `D282` when every LC/guarantee carries both | Contingent-liability class and performance status. |
| `balance_ghs` / `notional_ghs` | any foreign-currency position | number (cedi equivalent at `as_of_date`) | every `positions.sum` line of BSD2 (Foreign column "converted into cedis"), BSD1, BSD4, BSD8, BSD14 weights, module fact derivation | The bank's own cedi equivalent; when absent the platform converts at its preferred period-end spot (raw balance if none). Send it for every FX position. |

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
| `gl_mapping_bsd7` | `gl_account_code` \| `gl_prefix` (one of the two), `bsd7_item` (`1a` … `32`, the official BSD7A/BSD7B item tags), `sign` (`1` \| `-1`), `balance_basis` (`ytd` \| `period`), `gl_account_name`, `notes` — the bank's chart-of-accounts → P&L item register; a register (re-push whole; latest as-of wins). Spec: `docs/data_engine/datasets/gl_mapping_bsd7.md`. Pairs with INCOME/EXPENSE `gl_account` records (§3.1) pushed once per month-end. |
| `interest_accruals` | `as_of_date`, `bsd2_row` (row number of the "Accrued interest" line on the official BSD2 sheet: `20`, `29`, `32`, `141`, `145`, `151`, `156`, `161`, `166`, `177`, `195`, `204`, `211`, `218`, `225`, `234`, `242`, `250`, `258`), `side` (`asset` \| `liability`), `currency`, `accrued_interest_ghs`, `accrued_interest_native`, `gl_account_code`, `position_reference`, `counterparty_reference`, `notes` — accrual balances at the reporting date; one reporting date per push (batch `as_of_date` = that date). Spec: `docs/data_engine/datasets/interest_accruals.md`. |
| `tariff_schedule` | `form` (`BSD15A` \| `BSD15B`), `sheet` (`DOMESTIC` \| `RANGE` \| `INTL`), `row_key` (the official tariff row, `"<item>.<n>"` — e.g. `1.1` COT minimum, `S1.1` Savings S1 initial deposit, `17.1` account closure; full generated list in the spec), `charge_value` (**what the return prints in that cell** — text on `DOMESTIC` / `INTL`, a cedi amount on `RANGE`; never `N/A` / `Nil` / `-`, which the Data Engine reads as null — write `Free` / `Not applicable`), `label`, `charge_basis` (`flat` \| `percent` \| `per_item` \| `range`), `min_ghs`, `max_ghs`, `currency`, `effective_from`, `notes` — the bank's published tariff guide keyed by the official BSD15A/BSD15B rows; a register (re-push whole on each tariff revision; latest as-of ≤ reporting date wins). Spec: `docs/data_engine/datasets/tariff_schedule.md`. |
| `atm_operations` | `month` (ISO month-end = the batch `as_of_date`), `atm_id`, `station` (Station / Branch as printed on BSD16), `cards_issued`, `min_withdrawal_ghs`, `max_withdrawal_ghs` (cedis), `region`, `branch_code`, `cards_active`, `txn_count`, `txn_value_ghs`, `cash_dispensed_ghs`, `downtime_hours`, `notes` — one row per terminal for ONE reporting month per push (BSD16 reads the latest batch on/before the period end and lists terminals in file order, first 50). Spec: `docs/data_engine/datasets/atm_operations.md`. |
| `remittance_flows` | `month` (ISO month-end = the batch `as_of_date`), `direction` (`inbound` \| `outbound`), `corridor_country` (ISO-3166 alpha-2), `region` (`uk` \| `usa_canada` \| `eu` \| `ecowas` \| `rest_of_africa` \| `other` — BSD17 Sheet 2 roll-up), `recipient_class` (`individual` \| `exporter` \| `service_provider` \| `ngo` \| `embassy` \| `other` — BSD17 Sheet 1), `channel` (`bank` \| `mto` \| `mobile_money` \| `other`), `currency`, `amount_fx`, `amount_usd` (the bank's own US$ equivalent — the reported figure), `amount_ghs`, `transaction_count`, `operator_name`, `notes` — monthly aggregate per (direction, corridor, recipient class, channel, currency); ONE reporting month per push. Spec: `docs/data_engine/datasets/remittance_flows.md`. |
| `subsidiaries` | `reporting_date` (ISO date = the batch `as_of_date`), `subsidiary_id` (the bank's stable id), `name`, `country_code`, `entity_type` (`bank` \| `nbfi` \| `insurance` \| `other`), `functional_currency`, `ownership_pct` (0–100), `consolidation_method` (`full` \| `equity` \| `none`), `control_via_board` (`true` \| `false`), `total_assets_ghs`, `total_liabilities_ghs`, `equity_ghs`, `net_profit_ytd_ghs`, `intercompany_receivable_ghs` (due FROM the subsidiary), `intercompany_payable_ghs` (due TO it); optional `tier1_capital_ghs`, `rwa_ghs`, `minority_interest_ghs` (required when `full` and ownership < 100 — the group's own working), `minority_interest_tier2_pref_ghs`, `investment_carrying_ghs`, `intercompany_receivable_type`, `intercompany_payable_type`, `regulator`, `licence_number`, `notes` — the subsidiary register + book, one row per subsidiary per reporting date; the whole register at one date per push (latest as-of wins). Feeds BSD9 minority interests + Annexure and BSD5B rows 3 / 18. Spec: `docs/data_engine/datasets/subsidiaries.md`. |
| `capital_expenditure` | `period_end` (ISO date = the batch `as_of_date`), `asset_class` (`land_buildings` \| `staff_land_premises` \| `furniture_equipment` \| `computers` \| `other_office_equipment` \| `motor_vehicles` \| `other_property_legal_rights`), `opening_nbv_ghs`, `additions_purchased_ghs`, `additions_finance_lease_ghs`, `additions_hire_purchase_ghs`, `disposal_proceeds_ghs`, `disposals_nbv_ghs`, `depreciation_ghs`, `closing_cost_ghs`, `accumulated_depreciation_ghs`, `closing_nbv_ghs` (= cost − accumulated depreciation, validated); optional `currency` (booking currency; blank = base ⇒ BSD2 Domestic), `capital_wip_ghs`, `wip_closing_ghs`, `contracted_not_provided_ghs`, `authorised_not_contracted_ghs`, `forecast_next_6m_ghs`, `forecast_0_3m_ghs`, `forecast_3_6m_ghs`, `budget_ghs`, `notes` — the fixed-asset / capex register, one row per (period, asset class); one period per push (half-year movements for BSD10 A–H, period-end stock for BSD2 item 12 rows 115–121 / 123). Spec: `docs/data_engine/datasets/capital_expenditure.md`. |

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
