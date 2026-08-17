# `capital_expenditure` — fixed-asset / capital-expenditure register by asset class

**Feeds:** every cell of **BSD10** *Capital Expenditure* (half-yearly; ten Guide items × five asset-class
columns; `H = SUM(C:G)` and row 18 are BoG's own formulas) and the fixed-asset block of **BSD2** *Statement
of Assets and Liabilities* — item 12 rows 115–121 (property, plant and equipment **at cost** by class, and
work-in-progress) and row 123 (accumulated depreciation), Domestic `B` / Foreign `C` (`D = B + C`, the
sub-total row 122 and item 12 itself `= 122 − 123` are template formulas). **Kind:** reference dataset
`capital_expenditure` (constants `REFERENCE_DATASET_KINDS`; migration `202608160014`). **Schema module:**
`backend/app/domain/ingestion/reference_schemas/capital_expenditure.py`. **Readers:** `refs.sum` —
`bog_forms/linemaps/bsd10.py` (one `LineSpec` per column × row, `filters={"asset_class": [...]}`),
`bog_forms/linemaps/bsd2.py::fixed_assets` (`currency_field="currency"`).

## Why it exists

The Guide defines every BSD10 figure as a half-year **cash flow** by asset class — expenditure incurred
(A purchased / B finance-lease / C hire-purchase), capital WIP (D), commitments (E), authorisations (F),
the six-month forecast (G, split 0–3 / 3–6 months) and disposal receipts (H) — "without deductions for
depreciation, amortisation or obsolescence". BSD2 item 12 asks for the **stock**: PPE at cost by class less
accumulated depreciation. The platform held fixed assets only as one GL `ASSET` balance with no asset-class
attribute (which is why BSD2 rows 115–121 were `BANK_COA_MAPPING` and all 50 BSD10 cells `input_required`).
The bank's fixed-asset sub-ledger already produces both views; this register is that sub-ledger, one row
per asset class per period.

## Grain and fields

One row per `(period_end, asset_class)`. Amounts in **cedis** (the platform applies the sheets' ¢'Million
convention on export). Movement fields are the **half-year's** (Guide: as at end June / end December); stock
fields are **as at `period_end`**.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `period_end` | ISO date | yes | The half-year end (BSD10) / reporting date (BSD2) the row describes. Equal to the push batch's `as_of_date`. |
| `asset_class` | enum (below) | yes | Register asset class — the union of the BSD10 columns and the BSD2 item-12 lines. |
| `currency` | ISO 4217 | no | Booking currency of the class's assets. Blank or the bank's base currency ⇒ BSD2 **Domestic** column; any other ⇒ **Foreign** (Guide §2). BSD10 has no currency split. |
| `opening_nbv_ghs` | number | yes | Net book value at the start of the half-year (completed assets, excl. WIP). |
| `additions_purchased_ghs` | number | yes | **BSD10 A. Purchased** — cash expenditure on assets purchased in the half-year (Guide: charged to a capital account; excludes transfers from WIP brought forward). |
| `additions_finance_lease_ghs` | number | yes | **BSD10 B. On finance-lease** — assets acquired under finance leases (Guide definition). |
| `additions_hire_purchase_ghs` | number | yes | **BSD10 C. On Hire-Purchase** — assets acquired on hire-purchase / conditional sale, excluding finance charges. |
| `capital_wip_ghs` | number | no | **BSD10 D. Capital WIP** — half-year expenditure carried as work-in-progress for the class (Guide (d): additions temporarily carried under other headings). Blank = nil. |
| `disposal_proceeds_ghs` | number | yes | **BSD10 H. Disposal proceeds** — amount due (incl. sums still receivable) on disposals completed in the half-year. |
| `disposals_nbv_ghs` | number | yes | Net book value derecognised on those disposals (the sub-ledger roll-forward). |
| `depreciation_ghs` | number | yes | Depreciation charge for the half-year (informational for the roll-forward; BSD7 carries the P&L charge). |
| `closing_cost_ghs` | number | yes | **BSD2 rows 115–120** — cost of the class's COMPLETED assets at `period_end` (excludes WIP). |
| `accumulated_depreciation_ghs` | number | yes | **BSD2 row 123** — accumulated depreciation on the class at `period_end`. |
| `closing_nbv_ghs` | number | yes | Net book value at `period_end` = `closing_cost_ghs − accumulated_depreciation_ghs` (validated). |
| `wip_closing_ghs` | number | no | **BSD2 row 121** — the WIP balance attributable to the class at `period_end`. Blank = nil. |
| `contracted_not_provided_ghs` | number | no | **BSD10 E. Contracted but not provided** — PPE ordered / contracted, not yet received. Blank = nil. |
| `authorised_not_contracted_ghs` | number | no | **BSD10 F. Authorised but not contracted** — Board-authorised, no order placed. Blank = nil. |
| `forecast_next_6m_ghs` | number | no | **BSD10 G. Forecast to be acquired in next six months** (purchase + lease + HP). |
| `forecast_0_3m_ghs` | number | no | **BSD10 row 16** — the part of G expected in the next quarter. |
| `forecast_3_6m_ghs` | number | no | **BSD10 row 17** — the part of G expected in the quarter after. `G = row 16 + row 17` (validated when all three are given). |
| `budget_ghs` | number | no | The class's approved capex budget for the half-year (informational). |
| `notes` | string | no | Free text. |

### `asset_class` vocabulary and how the two forms roll it up

| `asset_class` | BSD10 column (`bog_forms/linemaps/bsd10.py`) | BSD2 item-12 row |
|---|---|---|
| `land_buildings` | `C` Land and Buildings | 115 (a) Bank land and premises |
| `staff_land_premises` | `C` Land and Buildings | 116 (b) Land and premises for staff and staff amenities |
| `computers` | `E` Computers | 117 (c) Computers |
| `furniture_equipment` | `D` Furniture and Equipment | 118 (d) Furniture, fixtures and equipment |
| `other_office_equipment` | `F` Other Office Equipment | 118 (d) Furniture, fixtures and equipment |
| `motor_vehicles` | `G` Motor Vehicles | 119 (e) Motor vehicles |
| `other_property_legal_rights` | — (Guide excludes intangibles from BSD10) | 120 (f) Other property acquired by legal rights |

Work-in-progress is a **per-class attribute**, not a class: BSD10 row D "Capital WIP" reads `capital_wip_ghs`
under each column; BSD2 row 121 "(g) Work-in-progress" reads Σ `wip_closing_ghs` over all classes.
`closing_cost_ghs` therefore excludes WIP so BoG's sub-total (`=SUM(B115:B121)`) does not double count.
(`reference_schemas.capital_expenditure.BSD10_COLUMN_CLASSES` / `BSD2_ROW_CLASSES` carry the same tables.)

## Reading rule

Both forms read the **latest** `capital_expenditure` batch with `as_of_date` on/before the reporting date.
BSD10 cell (row, column) = Σ `<row field>` over the rows whose `asset_class` is in the column's classes.
BSD2 rows 115–120 = Σ `closing_cost_ghs` per class (118 sums two classes), 121 = Σ `wip_closing_ghs`,
123 = Σ `accumulated_depreciation_ghs` — each placed in Domestic or Foreign by the row's `currency`; item 12
= sub-total − 123 is BoG's arithmetic and equals the register's Σ NBV + Σ WIP by construction. Blank
(`input_required`) until the register is ingested at all; `0` for a class the register carries no row for
and for an omitted optional field. **One period per push** — a batch carries the whole register at that
`period_end`. A bank that wants BSD2's monthly PPE block to move monthly pushes the stock monthly (any
`period_end` is valid; the BSD10 flow fields are then the half-year-to-date figures the bank states).

## Validation

`reference_schemas.capital_expenditure.SCHEMA.validate_row` / `validate_capex_row`: the twelve required
fields; `asset_class` in the vocabulary; every `*_ghs` field numeric; `closing_nbv_ghs = closing_cost_ghs −
accumulated_depreciation_ghs` (to the cedi); `forecast_next_6m_ghs = forecast_0_3m_ghs + forecast_3_6m_ghs`
when the split is given. Rows are preserved verbatim at the storage layer.

## Sample Bank onboarding dataset

`backend/onboarding/sample_bank/capital_expenditure.csv` — two half-years (`2025-12-31` H2 2025, `2026-06-30`
H1 2026) × the seven classes = 14 rows; the per-period push units are `capital_expenditure_2025-12-31.csv`
and `capital_expenditure_2026-06-30.csv`; header-only template `capital_expenditure_template.csv`. Figures
are illustrative for a universal bank of the Sample Bank's size (PPE net ≈ GHS 101m at 2026-06-30 incl.
GHS 8.2m WIP; H1 2026 purchases GHS 8.6m, finance-lease GHS 1.1m, hire-purchase GHS 0.6m, disposal proceeds
GHS 0.58m; forecast for the next six months GHS 10.0m). H1 opens where H2 closed class by class and the WIP
balance rolls forward by the half-year's WIP expenditure.

Push (from `backend/`):

```
uv run python scripts/ingest_push.py --base-url http://localhost:8001 --token "$AEQ_TOKEN" \
  --bank BK-0PMD7Z5M --as-of 2025-12-31 --reason "Sample Bank onboarding: capex register H2 2025" \
  --reference capital_expenditure=onboarding/sample_bank/capital_expenditure_2025-12-31.csv
uv run python scripts/ingest_push.py --base-url http://localhost:8001 --token "$AEQ_TOKEN" \
  --bank BK-0PMD7Z5M --as-of 2026-06-30 --reason "Sample Bank onboarding: capex register H1 2026" \
  --reference capital_expenditure=onboarding/sample_bank/capital_expenditure_2026-06-30.csv
```

## Proof

`backend/tests/services/data_gaps/test_capital_expenditure.py`: the file validates and its arithmetic
holds; the line maps bind every BSD10 cell and BSD2 rows 115–121 / 123; a half-year pushed through the real
API lands with lineage, BSD10 carries the register's figures per class with BoG's totals over them, BSD2
item 12 fills at cost / WIP / accumulated depreciation so `SUM(115:121) − 123` equals the register's NBV +
WIP; an earlier period pushed later does not disturb the later one; without a register the cells stay
blank; malformed rows are refused.
