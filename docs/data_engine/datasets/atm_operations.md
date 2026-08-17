# `atm_operations` — monthly ATM / card operations per terminal

**Kind:** `atm_operations` (reference dataset; `REFERENCE_DATASET_KINDS`, migration `202608160014`)
· **Feeds:** BSD16 `MONTHLY ATM OPERATIONS` (50 station rows × Station / Branch · No. of Cards
Issued · Minimum withdrawal made ¢ · Maximum withdrawal made ¢, plus the TOTAL row) · **Schema:**
`backend/app/domain/ingestion/reference_schemas/atm_operations.py` · **Sample Bank:**
`backend/onboarding/sample_bank/atm_operations.csv` (12 months × 44 terminals) +
`atm_operations_YYYY-MM.csv` (one push unit per month) + `…_template.csv` · **Loader:**
`backend/scripts/ingest_push.py --as-of <month-end> --reference atm_operations=<month csv>`

## What it is

The bank's ATM estate for one reporting month: one row per terminal with the four figures BoG's
monthly ATM return asks for, plus the operational columns a channel team already keeps (active
cards, transaction count / value, cash dispensed, downtime). No ATM, card or channel entity
exists on the platform (searched `app/models` / `app/domain`), so this register *is* the source.

## Grain — **one reporting month per push**

One row per `(month, atm_id)`. The batch's `as_of_date` **is the reporting month-end** and BSD16
for a period reads the latest batch on/before the period end, so **a push must carry exactly one
month** — a multi-month file would be read as one month's roster (12 × the estate on the sheet).
The Sample Bank set is therefore shipped as twelve `atm_operations_YYYY-MM.csv` push units (the
combined `atm_operations.csv` is the reference copy).

Order matters: BSD16 lists terminals **in file order** (row 7 = first row of the file, row 56 =
50th). Put the terminals in the order you want them on the return. The official grid has 50
station rows: an estate of fewer than 50 leaves the tail rows blank (listed in the Completion
notes as `input_required` — not an error); an estate of more than 50 shows its first 50 while the
TOTAL row still sums the whole register.

## Fields

| Field | Type | Required | Unit / values | BSD16 column | Notes |
|---|---|---|---|---|---|
| `month` | date | yes | ISO month-end (`2026-06-30`) | — | must equal the batch `as_of_date` |
| `atm_id` | text | yes | | — | the bank's terminal id, unique within the month |
| `station` | text | yes | | B Station / Branch | text as it should print on the return |
| `cards_issued` | integer | yes | count | C No. of Cards Issued | cards issued at the station in the month (unscaled) |
| `min_withdrawal_ghs` | number | yes | cedis | D Minimum withdrawal made ¢ | smallest single withdrawal in the month |
| `max_withdrawal_ghs` | number | yes | cedis | E Maximum withdrawal made ¢ | largest single withdrawal in the month |
| `region` | text | no | Ghana region | — | e.g. `Greater Accra`, `Ashanti` |
| `branch_code` | text | no | | — | hosting branch / outlet code |
| `cards_active` | integer | no | count | — | active cards linked to the station |
| `txn_count` | integer | no | count | — | withdrawals in the month |
| `txn_value_ghs` | number | no | cedis | — | value withdrawn |
| `cash_dispensed_ghs` | number | no | cedis | — | cash dispensed (may differ from value: reversals) |
| `downtime_hours` | number | no | hours | — | out-of-service hours |
| `notes` | text | no | | — | |

Amounts are **cedis**; the sheet's `(¢'Million)` header is applied at export (a GHS 10 minimum
exports as `0.00001`). Counts are unscaled.

## Validation rules

- required: `month`, `atm_id`, `station`, `cards_issued`, `min_withdrawal_ghs`,
  `max_withdrawal_ghs`; all numeric fields numeric; `min ≤ max` (sanity, not enforced by the
  schema).
- Every row in a push must carry the same `month` (= `as_of_date`).
- Do not write `N/A` / `-` / `none` in text fields — the Data Engine reads them as null.

## How BSD16 reads it

| Cell(s) | Binding |
|---|---|
| B7…B56 | `refs.field {kind: atm_operations, index: row−7, field: station}` |
| C7…C56 | `refs.field {…, field: cards_issued, numeric: true}` (unscaled) |
| D7…D56 / E7…E56 | `refs.field {…, field: min_withdrawal_ghs / max_withdrawal_ghs, numeric: true}` |
| F7…F56, F57 | BoG's template formulas `=D+E`, `=SUM(F7:F56)` — evaluated, never bound |
| D57 / E57 | `refs.sum {kind: atm_operations, value_field: min_withdrawal_ghs / max_withdrawal_ghs}` — the register's own column totals (the official total row carries no formula) |

`refs.field` with no `order_by` keeps ingestion order; `index` beyond the estate → blank.

## Example

```csv
month,atm_id,station,region,branch_code,cards_issued,cards_active,min_withdrawal_ghs,max_withdrawal_ghs,txn_count,txn_value_ghs,cash_dispensed_ghs,downtime_hours,notes
2026-06-30,ATM-001,Head Office — Independence Avenue,Greater Accra,BR001,109,5043,50,2000,7253,2758201.24,2737902.15,0.0,
2026-06-30,ATM-019,Kumasi Adum,Ashanti,BR020,128,5751,10,2000,6912,2439107.56,2433515.56,6.2,
```

Push: `{"reference": {"atm_operations": [ ...rows of ONE month... ]}}` with
`as_of_date = <that month-end>` and an idempotency key per month (e.g. `atm-2026-06-30`).

## Sample Bank dataset

44 terminals across eleven regions (Greater Accra 17, Ashanti 9, Western 4, Central 3, Eastern 3,
Volta 2, Northern 2, Bono / Bono East / Upper East / Upper West 1 each), twelve months
2025-07 … 2026-06, seasonal December peak; illustrative, deterministic, not real bank data.
Loaded to the primary (`BK-0PMD7Z5M`) as twelve pushes, `as_of_date` = each month-end.
