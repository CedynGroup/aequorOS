# BSD7B — Current Year Consolidated Results: line / cell map

**Official workbook:** `FORM BSD7B REVISED.xls` · **Frequency:** quarterly · **Time limit:** 14 days · **Basis:** consolidated (Guide §1: subsidiaries' results only on BSD7B and BSD9) · **Unit:** ¢'Million

**Sheets (1):** `BSD7B` — 105 input cells · 24 template formulas.

Generated from `bog_forms/linemaps/bsd7b.py` + `layouts/BSD7B.json` (row table regenerated from the line map — do not hand-edit it).

## Layout

| Block | Rows | Columns | What the cells are |
|---|---|---|---|
| P&L / appropriation spine, items 1–37 (the BSD7A spine, two rows lower, same labels) | 7–52 | `C` Quarter Ended · `D` Period to date (no Domestic/Foreign split) | 34 leaf rows × 2 input cells; every total is a template formula |
| Item numbers | column `A` | — | 37 numeric template literals bound to their own value (`constant`, unscaled) |

## Sources and basis

* Same ledger mapping as BSD7A (`bsd7.pl_line`, see `bsd7a_line_map.md`): the bank's INCOME/EXPENSE GL accounts
  tagged `attributes.bsd7_line = <item>`; all currencies; **Quarter Ended** = fiscal quarter containing the
  reporting date (period-to-date at quarter end − period-to-date at the previous quarter end); **Period to
  date** = fiscal year to date. BSD7B row = BSD7A row + 2 for the whole spine.
* **Consolidation is not on the platform.** There is no subsidiary book and no inter-company elimination
  register, and the template has no separate consolidation-adjustment cells. Every P&L line therefore carries
  the **parent's solo figure** and says so in its note; the bank adds subsidiaries' results and eliminations
  before filing. Items 34–36 (reserves) are input_required as on BSD7A.

## Sheet `BSD7B` (item-number cells omitted: 37 × `constant`)

| Row | Cells | Official line | Status | Source (resolver → params) | Note |
|---|---|---|---|---|---|
| 8 | C8/D8 | (a) Overdrafts, Loans & Other Advances | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=1a; gl_classes=['INCOME'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=1a (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 9 | C9/D9 | (b) Bills (including discounts) | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=1b; gl_classes=['INCOME'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=1b (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 10 | C10/D10 | (c) Investments (including discounts) | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=1c; gl_classes=['INCOME'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=1c (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 13 | C13/D13 | -Savings | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=2a_savings; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=2a_savings (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 14 | C14/D14 | -Current | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=2a_current; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=2a_current (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 15 | C15/D15 | -Time | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=2a_time; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=2a_time (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 16 | C16/D16 | -Borrowings | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=2a_borrowings; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=2a_borrowings (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 17 | C17/D17 | (b) Other Interest paymants | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=2b; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=2b (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 19 | C19/D19 | Profit/Loss on foreign exchange dealings | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=4; gl_classes=['INCOME'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=4 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; FX dealing profits (losses go to item 26); parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 20 | C20/D20 | Income from Fees and Commissions | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=5; gl_classes=['INCOME'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=5 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 21 | C21/D21 | Dividends received | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=6; gl_classes=['INCOME'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=6 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 22 | C22/D22 | Profit/Loss on sale of Property, Plant and Equipment | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=7 | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=7 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; net gain/loss on disposal — signed per the ledger; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 23 | C23/D23 | Rent Reveivable | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=8; gl_classes=['INCOME'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=8 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 24 | C24/D24 | Gain on dealing assets | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=9; gl_classes=['INCOME'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=9 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; dealing gains (losses go to item 25); parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 25 | C25/D25 | Other Income | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=10; gl_classes=['INCOME'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=10 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 27 | C27/D27 | Operating Expense - Staff | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=12; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=12 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 28 | C28/D28 | -Training | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=13; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=13 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 29 | C29/D29 | -Emoluments | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=14; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=14 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 30 | C30/D30 | -Others | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=15; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=15 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 31 | C31/D31 | - Occupancy | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=16; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=16 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 32 | C32/D32 | - Travel | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=17; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=17 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 33 | C33/D33 | - Admin. & Other | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=18; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=18 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 35 | C35/D35 | Provisions - Depreciation | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=20; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=20 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 36 | C36/D36 | - Bad Debts | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=21; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=21 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; charge for the period, not the provision stock; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 37 | C37/D37 | - Other | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=22; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=22 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 39 | C39/D39 | Loses on sale of Investment | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=24; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=24 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 40 | C40/D40 | Loses on dealing assets | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=25; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=25 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 41 | C41/D41 | Exchange losses | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=26; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=26 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 43 | C43/D43 | Provision for Taxation | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=28; gl_classes=['EXPENSE'] | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=28 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 45 | C45/D45 | Extraordinary Items | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=30 | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=30 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; signed net; the template deducts it (charge positive); parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 47 | C47/D47 | Dividends Paid and Payable | mapped (CoA-tagged ledger) | `bsd7.pl_line` line=32 | from the bank's P&L ledger: GL accounts tagged attributes.bsd7_line=32 (or account_code_prefixes in the bank's CoA mapping); blank until tagged; dividends declared/paid in the period (appropriation); parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 49 | C49/D49 | Statutory Reserves | input_required |  | statutory reserve fund balance before this period's appropriation — bank must supply from its appropriation account; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 50 | C50/D50 | Other reserves | input_required |  | other reserves balance before this period's appropriation — bank must supply; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |
| 51 | C51/D51 | Income Surplus | input_required |  | income surplus brought forward before this period's appropriation — bank must supply; parent (solo) figure — subsidiaries' results and inter-company eliminations are not on the platform (no subsidiary book); bank must add them for the consolidated return |

**Totals:** 105 declared cells — 62 mapped from the CoA-tagged ledger (parent solo), 6 input_required (items
34–36 × 2), 37 item-number literals.

### Residual unmapped lines — data the bank must supply

- **Subsidiaries' line-by-line results and inter-company eliminations** for every line (the `subsidiaries` register carries each subsidiary's net profit YTD only — see Data-gap closure below).
- **Items 34–36** reserve balances before appropriation (see BSD7A).
- **Untagged P&L items** until the CoA mapping names them.

### Framework asks

1. **Subsidiary line-by-line P&L / consolidation entries.** The `subsidiaries` register (2026-08-16) carries
   each subsidiary's NET profit YTD only; a per-subsidiary P&L mapped to the BSD7 items (or ingested
   consolidation adjustments) is the only way BSD7B stops being parent-solo; the line maps already carry the
   parent so they would only gain the adjustment source.
2. As BSD7A: CoA mapping surface; fiscal-year start on the institution.

## Data-gap closure — subsidiaries register (2026-08-16, documented decision: no cell re-pointed)

The `subsidiaries` reference dataset (`docs/data_engine/datasets/subsidiaries.md`) now exists and carries each
subsidiary's `net_profit_ytd_ghs`. **No BSD7B cell reads it**: every input cell of this sheet is one of the 34
P&L / appropriation items and the template has no "share of subsidiaries' profit" line and no
consolidation-adjustment cell; a subsidiary's NET profit is the net of its own interest, fees, expenses,
provisions and tax, so placing it in any single item (e.g. "Other Income") would misstate that item and
double count once the bank consolidates line by line. Every P&L line therefore stays the parent's solo figure
and its note now names the register and says why (`_CONSOLIDATION_NOTE`). What closes the rest is framework
ask 1 above.

## Cross-form dependencies

- `depends_on` BSD7A (catalogue). BSD7B `D<row+2>` == BSD7A `H<row>` for the same reporting date (same
  ledger, same window) — proved in `tests/services/bog_forms/test_bsd7.py`; in the fiscal year's first quarter
  BSD7B `C` == `D`.
