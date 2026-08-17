# `interest_accruals` — accrued-interest sub-ledger

**Feeds:** the 19 "Accrued interest" lines of **BSD2** *Statement of Assets and Liabilities* (Domestic
`B` / Foreign `C`; the Total column and every subtotal are BoG's own template formulas), and through
BSD2's totals the "FROM BSD2" links of BSD6 / BSD8. **Kind:** reference dataset `interest_accruals`
(constants `REFERENCE_DATASET_KINDS`; migration `202608160014`). **Schema module:**
`backend/app/domain/ingestion/reference_schemas/interest_accruals.py`. **Reader:** `refs.sum` with
`filters={"bsd2_row": <row>}` and `currency_field="currency"` (`bog_forms/linemaps/bsd2.py::accrued_interest`).

## Why it exists

BSD2 asks for accrued interest as its own line under each balance block. Positions on the platform are
principal balances — no canonical entity carries an accrual — so these 19 official cells were
`input_required`. The bank's accruals sub-ledger closes them: it states, at each reporting date, the
accrued-interest **balances** (stocks, not the period's interest flow) tagged to the official line each
belongs to.

## Grain and fields

One row per accrual balance at the reporting date: `(as_of_date, bsd2_row, side, currency,
gl_account_code | position_reference)`. A bank may post one summary row per line and currency, or one
row per position — the resolver sums whatever it is given.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `as_of_date` | ISO date | yes | The reporting date the balance is struck at. Equal to the push batch's `as_of_date` (BSD2 reads the latest batch on/before the period end). |
| `bsd2_row` | enum (below) | yes | The **row number of the "Accrued interest" line on the official `BSD2` sheet** of *FORM BSD2 REVISED.xls* — the number printed down the left of the template the bank already files. |
| `side` | enum `asset` \| `liability` | yes | Which side of the balance sheet the accrual sits on; must agree with the row (rows 20/29/32 are assets, the rest liabilities). |
| `currency` | ISO 4217 | yes | Currency of the underlying claim/obligation. Guide §2: the bank's base currency ⇒ **Domestic** column; any other ⇒ **Foreign** column. |
| `accrued_interest_ghs` | number | yes | Accrued interest balance in **cedis** (Foreign rows converted at the bank's closing rate). Positive; a nil accrual is `0`. |
| `accrued_interest_native` | number | no | The native-currency amount of a foreign-currency accrual (informational). |
| `gl_account_code` | string | no | The bank's GL account holding the accrual (e.g. Accrued Interest Receivable / Payable). |
| `position_reference` | string | no | `position.source_reference` of the underlying position when the row is per position. |
| `counterparty_reference` | string | no | `counterparty.source_reference` when known. |
| `notes` | string | no | Free text. |

### `bsd2_row` vocabulary — the official BSD2 rows labelled "Accrued interest"

The bank tags an accrual by the **row it would type it into on the official form**; nothing else about
the platform's line map needs to be known.

| `bsd2_row` | `side` | Section on the official BSD2 sheet | Line |
|---|---|---|---|
| `20` | asset | B. DOMESTIC ASSETS · 6(b) Claims on Bank of Ghana | (v) Accrued interest |
| `29` | asset | 6(c) Claims on other depository institutions | (vi) Accrued interest |
| `32` | asset | 6(d) Claims on other financial institutions | (ii) Accrued interest |
| `141` | liability | C. FOREIGN LIABILITIES · 18. Short-term borrowings (non-resident) | (c) Accrued interest |
| `145` | liability | 19. Long-term borrowing (non-resident) | (c) Accrued interest |
| `151` | liability | 20. Deposits of non-residents · (a) Demand deposits | (iv) Accrued interest |
| `156` | liability | 20(b) Savings accounts | (iv) Accrued interest |
| `161` | liability | 20(c) Time deposits | (iv) Accrued interest |
| `166` | liability | 20(d) Certificates of deposit | (iv) Accrued interest |
| `177` | liability | D. DOMESTIC LIABILITIES · 21. Long-term borrowings | (f) Accrued interest |
| `195` | liability | 23. Short-term borrowing | (f) Accrued interest |
| `204` | liability | 24. Deposits of financial institutions · (a) Demand deposits | (iv) Accrued interest |
| `211` | liability | 24(b) Savings accounts | (iv) Accrued interest |
| `218` | liability | 24(c) Time deposits | (iv) Accrued interest |
| `225` | liability | 24(d) Certificates of deposit | (iv) Accrued interest |
| `234` | liability | 25. Deposits of non-financial institutions, public and govt · (a) Demand deposits | (vii) Accrued interest |
| `242` | liability | 25(b) Savings accounts | (vii) Accrued interest |
| `250` | liability | 25(c) Time deposits | (vii) Accrued interest |
| `258` | liability | 25(d) Certificates of deposit | (vii) Accrued interest |

(`reference_schemas.interest_accruals.BSD2_ROW_LABELS` carries the same table for tooling.) Accrued
interest on loans and on investment securities is not a separate BSD2 line (those balances are reported
inside the loan / investment lines per the Guide) and is therefore not in this vocabulary.

## Reading rule

For each of the 19 rows the resolver sums `accrued_interest_ghs` over the rows tagged with that
`bsd2_row` from the **latest** `interest_accruals` batch on/before the period end (latest as-of date and, within it, the most recently ingested batch — a corrected re-push for the same date replaces the earlier one), placing each row in
the Domestic or Foreign column by its `currency`. Blank (`input_required`) until the sub-ledger has been
ingested at all; `0` for a line the sub-ledger carries no row for. **One reporting date per push** —
a batch must carry that date's full sub-ledger; an omitted line reads `0`, not blank.

## Validation

`reference_schemas.interest_accruals.SCHEMA.validate_row` / `validate_accrual_row`: the five required
fields; `bsd2_row` in the vocabulary; `side` in {`asset`,`liability`} and consistent with the row;
`accrued_interest_ghs` / `accrued_interest_native` numeric. Rows are preserved verbatim at the storage
layer.

## Loading

```bash
cd backend
uv run python scripts/ingest_push.py --base-url http://localhost:8001 --token "$AEQ_TOKEN" \
  --bank BK-0PMD7Z5M --as-of 2026-06-30 --reason "Sample Bank onboarding: accruals sub-ledger 2026-06-30" \
  --reference interest_accruals=onboarding/sample_bank/interest_accruals.csv
```

Repeat monthly with the new month-end's file and `--as-of`. App path: upload the sheet and map it with
`ReferenceMapping(source_table=<sheet>, dataset_kind="interest_accruals")`.

## Sample Bank onboarding dataset

`backend/onboarding/sample_bank/interest_accruals.csv` — 22 rows as at 2026-06-30 (GHS, USD rows at
15.10), one summary row per line × currency referencing the bank's GL `1404` (Other Assets, receivable
side) and `2201` (Accrued Interest Payable): placements ≈ 2.1m receivable; time deposits ≈ 11.6m,
savings ≈ 3.1m, subordinated debt ≈ 4.0m, interbank/DFI borrowings ≈ 3.1m payable — sized to the bank's
placements (0.17bn), deposits (3.88bn) and borrowings (0.35bn) at Ghanaian 2026 rates; nil-accrual
lines (BoG balances, CDs) carried explicitly as `0`. Template: `interest_accruals_template.csv`.
Realistic, not real: the Sample Bank is the platform's onboarding sandbox.
