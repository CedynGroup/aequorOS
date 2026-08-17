# BSD7A — Current Year Results: line / cell map

**Official workbook:** `FORM BSD7A REVISED.xls` · **Frequency:** monthly · **Time limit:** 14 days · **Basis:** solo · **Unit:** ¢'Million (staff numbers are counts)

**Sheets (1):** `BSD7A` — 209 input cells · 147 template formulas.

Generated from `bog_forms/linemaps/bsd7a.py` + `layouts/BSD7A.json` (row tables regenerated from the line map — do not hand-edit them).

## Layout

| Block | Rows | Columns | What the cells are |
|---|---|---|---|
| P&L / appropriation spine, items 1–37 | 5–50 | `C` Month-ended Domestic · `D` Month-ended Foreign · `E` = `C+D` · `F` Period-to-date Domestic · `G` Period-to-date Foreign · `H` = `F+G` | 34 leaf rows × 4 input cells; every total (items 1, 2, 2(a), 3, 11, 19, 23, 27, 29, 31, 33, 37) is a template formula |
| Averages, items 38–42 | 54–58 | `C` Average Quarter Ended · `D` Average Period to date | 5 rows × 2 |
| Staff numbers, items 43–51 | 63–65, 68–70 | `C` / `D` (same column headings) | 6 leaf rows × 2; sub-totals and Total Staff are formulas |
| Item numbers | column `A` | — | 51 numeric template literals captured as input cells; bound to their own value (`constant`, unscaled) so the export carries the official numbering |

## Sources — what the platform holds

* **P&L lines** come from the bank's own INCOME/EXPENSE general ledger (`canonical_gl_accounts`; ingested by the
  Data Engine — Excel/CSV `gl_account`, T24 GL, API push). The platform has **no chart of accounts of its own**,
  so which accounts feed which official item is the bank's CoA mapping: an account is selected when its
  `attributes.bsd7_line` equals the item tag (`1a`, `1b`, `1c`, `2a_savings`, `2a_current`, `2a_time`,
  `2a_borrowings`, `2b`, `4` … `32` — the official item numbers) or its `account_code` starts with a declared
  prefix. A P&L account's ledger balance as at a date is **fiscal-year-to-date** (trial-balance convention: P&L
  accounts clear to reserves at year end): *Period to date* = latest generation on/before period end;
  *Month-ended* = period-to-date at month end − period-to-date at the previous month end (0 in the fiscal
  year's first month). If the previous month's generation is missing the month cell stays blank
  (input_required) rather than showing the year-to-date figure. Ledgers that deliver period movements instead
  set `balance_basis="period"` (the window sums generations). Domestic/Foreign per Guide §2 by the account's
  currency (no stated currency ⇒ base currency); a line that is tagged but has nothing in one currency reads 0
  in that column; a line with **no tagged account is blank — never a guessed split.** Sign convention: income
  and expense lines carry ledger magnitudes (the template's own arithmetic subtracts expenses); the two signed
  net lines (item 7 disposal gains/losses, item 30 extraordinary items) take the ledger's signed net.
  Fiscal year starts in January unless the mapping passes `fiscal_year_start_month`. Resolver:
  `sources_ext/bsd7.py::bsd7.pl_line`.
* **Averages** (items 38, 40) are the arithmetic mean over the reporting periods in the window (fiscal quarter
  to date / fiscal year to date; the platform holds monthly reporting periods, so these are month-end
  averages) of Σ `bank_facts`: total assets = `balance_sheet` facts with `attributes.side = asset` (the
  platform's own total-assets identity), shareholders' funds = `capital_component` facts of tier CET1 that are
  not deductions (paid-up capital + reserves). Resolver: `bsd7.average_facts`.
* On the primary database the GL panel carried ASSET/LIABILITY/EQUITY accounts only until 2026-08-16, so
  **every P&L line was blank (input_required) until a bank ingests its P&L ledger and maps it** — the honest
  state; the hermetic proof (`tests/services/bog_forms/test_bsd7.py`) inserts a tagged ledger and shows the
  full chain, and `tests/services/data_gaps/test_gl_mapping_bsd7.py` pushes the Sample Bank's P&L ledger + the
  `gl_mapping_bsd7` register through the real API (see *Data-gap closure* below).

## Sheet `BSD7A` — P&L spine, averages, staff (item-number cells omitted from the table: 51 × `constant`)

Status legend — **mapped**: fed from platform facts; **mapped (CoA-tagged ledger)**: fed from the bank's P&L
ledger once its CoA mapping tags the accounts (blank until then); **input_required**: bank must supply;
**coa-mapping**: bank-specific chart-of-accounts split required.

| Row | Cells | Official line | Status | Source (resolver → params) | Note |
|---|---|---|---|---|---|
| 6 | C6/D6/F6/G6 | (a) Overdrafts, Loans & Other Advances | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=1a; gl_classes=['INCOME'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=1a (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 7 | C7/D7/F7/G7 | (b) Bills (including discounts) | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=1b; gl_classes=['INCOME'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=1b (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 8 | C8/D8/F8/G8 | (c) Investments (including discounts) | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=1c; gl_classes=['INCOME'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=1c (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 11 | C11/D11/F11/G11 | -Savings | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=2a_savings; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=2a_savings (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 12 | C12/D12/F12/G12 | -Current | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=2a_current; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=2a_current (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 13 | C13/D13/F13/G13 | -Time | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=2a_time; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=2a_time (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 14 | C14/D14/F14/G14 | -Borrowings | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=2a_borrowings; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=2a_borrowings (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 15 | C15/D15/F15/G15 | (b) Other Interest paymants | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=2b; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=2b (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 17 | C17/D17/F17/G17 | Profit/Loss on foreign exchange dealings | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=4; gl_classes=['INCOME'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=4 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; FX dealing profits (losses go to item 26) |
| 18 | C18/D18/F18/G18 | Income from Fees and Commissions | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=5; gl_classes=['INCOME'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=5 (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 19 | C19/D19/F19/G19 | Dividends received | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=6; gl_classes=['INCOME'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=6 (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 20 | C20/D20/F20/G20 | Profit/Loss on sale of Property, Plant and Equipment | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=7 | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=7 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; net gain/loss on disposal — signed per the ledger |
| 21 | C21/D21/F21/G21 | Rent Reveivable | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=8; gl_classes=['INCOME'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=8 (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 22 | C22/D22/F22/G22 | Gain on dealing assets | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=9; gl_classes=['INCOME'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=9 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; dealing gains (losses go to item 25) |
| 23 | C23/D23/F23/G23 | Other Income | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=10; gl_classes=['INCOME'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=10 (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 25 | C25/D25/F25/G25 | Operating Expense - Staff | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=12; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=12 (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 26 | C26/D26/F26/G26 | -Training | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=13; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=13 (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 27 | C27/D27/F27/G27 | -Emoluments | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=14; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=14 (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 28 | C28/D28/F28/G28 | -Others | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=15; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=15 (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 29 | C29/D29/F29/G29 | - Occupancy | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=16; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=16 (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 30 | C30/D30/F30/G30 | - Travel | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=17; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=17 (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 31 | C31/D31/F31/G31 | - Admin. & Other | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=18; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=18 (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 33 | C33/D33/F33/G33 | Provisions - Depreciation | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=20; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=20 (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 34 | C34/D34/F34/G34 | - Bad Debts | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=21; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=21 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; charge for the period, not the provision stock |
| 35 | C35/D35/F35/G35 | - Other (specify) | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=22; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=22 (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 37 | C37/D37/F37/G37 | Loses on sale of Investment | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=24; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=24 (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 38 | C38/D38/F38/G38 | Loses on dealing assets | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=25; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=25 (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 39 | C39/D39/F39/G39 | Exchange losses | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=26; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=26 (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 41 | C41/D41/F41/G41 | Provision for Taxation | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=28; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=28 (or account_code_prefixes in the bank's CoA mapping); blank until tagged |
| 43 | C43/D43/F43/G43 | Extraordinary Items | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=30 | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=30 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; signed net; the template deducts it (charge positive) |
| 45 | C45/D45/F45/G45 | Dividends Paid and Payable | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=32 | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=32 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; dividends declared/paid in the period (appropriation) |
| 47 | C47/D47/F47/G47 | Statutory Reserves | input_required |  | statutory reserve fund balance before this period's appropriation — bank must supply from its appropriation account |
| 48 | C48/D48/F48/G48 | Other reserves | input_required |  | other reserves balance before this period's appropriation — bank must supply |
| 49 | C49/D49/F49/G49 | Income Surplus | input_required |  | income surplus brought forward before this period's appropriation — bank must supply |
| 54 | C54/D54 | Total Assets | mapped | `bsd7.average_facts` group=balance_sheet; attribute_eq={'side': 'asset'} | mean of month-end total assets (Σ balance_sheet asset facts) over the fiscal quarter / year to date |
| 55 | C55/D55 | Property, Plant and Equipment | coa-mapping |  | property, plant & equipment sits inside the platform's other_assets residual — fixed-asset register / CoA mapping required for the average |
| 56 | C56/D56 | Shareholders' Funds | mapped | `bsd7.average_facts` group=capital_component; capital_tiers=['CET1']; exclude_deductions=True | mean of month-end shareholders' funds (paid-up capital + reserves = CET1 components before regulatory deductions) over the fiscal quarter / year to date |
| 57 | C57/D57 | Assets Earning Interest or Discount | input_required |  | interest-earning classification of assets is not a platform attribute — bank must supply the average |
| 58 | C58/D58 | Liabilities Bearing Interest or Discount | input_required |  | interest-bearing classification of liabilities is not a platform attribute — bank must supply the average |
| 63 | C63/D63 | Managerial | input_required |  | headcount by grade and location — HR register required |
| 64 | C64/D64 | Clerical | input_required |  | headcount by grade and location — HR register required |
| 65 | C65/D65 | Non-Clerical | input_required |  | headcount by grade and location — HR register required |
| 68 | C68/D68 | Managerial | input_required |  | headcount by grade and location — HR register required |
| 69 | C69/D69 | Clerical | input_required |  | headcount by grade and location — HR register required |
| 70 | C70/D70 | Non-Clerical | input_required |  | headcount by grade and location — HR register required |

**Totals:** 209 declared cells — 128 mapped (124 from the CoA-tagged ledger + 4 averages), 30 input_required /
coa-mapping (3 reserve rows × 4, PPE average × 2, interest-earning / interest-bearing averages × 4, staff
numbers × 12), 51 item-number literals.

### Residual unmapped lines — data the bank must supply

- **Items 34–36 (Statutory Reserves, Other reserves, Income Surplus)** — the template sums them WITH item 33
  Retained Profit into item 37 TOTAL RESERVES, i.e. reserve balances *before* the period's appropriation. The
  platform's `capital_component` facts are period-end balances that may already include the period's profit,
  so no honest source exists; supply from the appropriation account.
- **Item 39 average PPE** — property, plant & equipment sits inside the platform's `other_assets` residual
  (fact derivation); a fixed-asset register / CoA mapping is required.
- **Items 41–42 average interest-earning assets / interest-bearing liabilities** — "interest-bearing" is not a
  platform attribute (a rule would have to be invented); supply the averages.
- **Items 43–51 staff numbers** — HR register (headcount by grade × head office / branches).
- **Every P&L item whose GL accounts are not yet tagged** — the CoA mapping is the bank's; untagged lines export
  blank and are listed on the Completion-notes sheet.

### Framework asks

1. **CoA mapping surface — BUILT 2026-08-16** as the reference dataset `gl_mapping_bsd7` (GL code/prefix →
   official item, sign, balance basis; see *Data-gap closure* below). `attributes.bsd7_line` on `gl_account`
   records remains a valid seam and takes precedence per account. Still open: a Data Engine settings screen
   over the register (today it is uploaded/pushed like any other reference dataset).
2. **Fiscal year on the institution.** `banks` carries no fiscal-year start; the resolver defaults to January
   (`fiscal_year_start_month` param). A `banks.fiscal_year_start_month` column would make it data, not a
   parameter.

## Data-gap closure — P&L ledger + CoA → item register (2026-08-16)

The 124 P&L ledger cells (31 items × Month-ended Domestic/Foreign × Period-to-date Domestic/Foreign; BSD7B's 62
Quarter/PTD cells read the same mapping) are fed by two Data-Engine datasets, both loaded for the Sample Bank on
the primary:

1. the **P&L ledger** — `gl_account` entity records with `account_class` INCOME/EXPENSE and a fiscal-year-to-date
   `balance`, one push per month-end (`backend/onboarding/sample_bank/gl_accounts_pl_<month-end>.csv`, 12
   month-ends 2025-07-31 … 2026-06-30, 57 accounts; FX-business accounts carry `currency=USD` with the cedi-
   equivalent balance → Foreign column);
2. the **`gl_mapping_bsd7` register** (`docs/data_engine/datasets/gl_mapping_bsd7.md`; schema
   `reference_schemas/gl_mapping_bsd7.py`; `backend/onboarding/sample_bank/gl_mapping_bsd7.csv`) — one row per
   `gl_account_code` or `gl_prefix` → `bsd7_item` (the tag vocabulary of this line map: `1a` … `32`), `sign`
   (`-1` for contra accounts), `balance_basis` (`ytd` | `period`).

`bsd7.pl_line` (`sources_ext/bsd7.py`) now selects accounts by precedence: the account's own `attributes.bsd7_line`
tag → the register's exact-code row → the register's longest matching prefix row → the line map's declared
`account_code_prefixes`; sign and basis apply per account. Nothing changes for a ledger tagged the old way. The
line map itself is unchanged (`pl(tag, …)` per row); the register is read at generation time (latest as-of on/
before the reporting date), so a bank changes its mapping by re-pushing the register, not the ledger. Proof:
`tests/services/data_gaps/test_gl_mapping_bsd7.py` — vocabulary == line-map tags; four month-ends of the ledger
+ the register pushed through the real API (`scripts/ingest_push.py`'s three-call `push()`) → all 124 BSD7A cells
and 62 BSD7B cells `input_required` → `mapped`, month = Δ YTD, contra sign, exact-beats-prefix, own tag beats
register, BSD7B PTD == BSD7A total.

## Cross-form dependencies

- BSD7B (quarterly, consolidated) reads the same ledger mapping: BSD7B *Period to date* == BSD7A `H` (period-to-
  date total) for the same reporting date — proved in `test_bsd7.py`.
- BSD11 (statutory return) `depends_on` BSD7A in the catalogue.
