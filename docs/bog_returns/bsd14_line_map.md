# BSD14 — Weekly Return on Interest Rates: line / cell map

**Official workbook:** `FORM BSD14 REVISED.xls` · **Frequency:** weekly · **Time limit:** 9 days · **Basis:** solo · **Unit:** percent (all cells)

**Sheets (1, official order):** `INTEREST&LENDING-RATES`

**Depends on:** —

Generated from `bog_forms/linemaps/bsd14.py` + `layouts/BSD14.json` (tables are generated — do not hand-edit them; regenerate).


## What the form asks and where each figure comes from

FORM BSD14 (weekly, 9 days) is a single rate grid — currency rows × product
columns, all percentages — plus the bank's BASE RATE. The template ships the
grid empty; the 100 official rate cells (rows 17 Cedis · 28 USD · 29 GBP · 30
DEM · 31 All other currencies × columns B..U) are named from the header labels
(`grid_lines`). The tenor headers D14:J14 (1 · 2 · 3 · 6 · 12 · 24 · 36) are the
template's only captured input cells and are kept as constants. Spare
unlabelled rows (18–25, 32–43) are the form's "add other items" space, not
official lines.

**Source — `bsd14.rate`** (see `sources_ext/bsd14.py`): the platform holds no
"offered rate" table, but every DEPOSIT / LOAN position carries its contractual
`interest_rate`. Each rate cell is the **balance-weighted average** of the
matching positions' rates in that currency (× 100), or `min` / `max` via the
`statistic` param:

| Column | Product filter |
|---|---|
| B Demand deposit | DEPOSIT, `deposit_account_type` CURRENT |
| C Savings deposit | DEPOSIT, SAVINGS |
| D..J Fixed/Time deposit 1·2·3·6·12·24·36 months | DEPOSIT, FIXED, tenor bucket = **nearest** official tenor (`attributes.tenor_months`, else original term `contractual_maturity − origination_date`, else remaining term from period end) |
| K Certificate of deposits | DEPOSIT, `attributes.instrument` certificate_of_deposit / cd |
| L Call deposit | DEPOSIT, CALL |
| M Any other | DEPOSIT, OTHER |
| N..U Lending rates by sector | LOAN, `sector` attribute (BSD4 vocabulary — snapshot attribute, else counterparty attribute) grouped: agriculture.* → Agriculture; manufacturing.export.* + commerce.export.* → Exports; mining.* → Mining/Quarrying; manufacturing.home.* / manufacturing → Manufacturing; construction.* → Construction; commerce.import.* → Imports; other commerce.* → Commerce; transport.*/services.*/miscellaneous and unclassified → Others |

Honesty rules: a cell with no matching position, or whose matches carry no
`interest_rate`, is **input_required** ("product rate table required") — never
0; sector cells are input_required when the currency's loan book carries no
`sector` attribute at all (nothing is guessed — BSD4's rule); the Cedis row
binds the bank's base currency at resolve time; the *All other currencies* row
weights by `balance_ghs` when present. The **BASE RATE** (B9) is a declared
benchmark, not derivable from positions → input_required.

## Sheet `INTEREST&LENDING-RATES`

Status legend — **mapped**: fed from platform data via the named resolver (status resolves per cell at generation: a resolver returning nothing yields `input_required` for that cell); **input_required**: bank must supply (no canonical source); **constant**: the template's own shipped value, kept verbatim.

Cells bound: **108** — mapped 100 · input_required 1 · constant 7.

| Row | Cells | Official line | Status | Source (resolver → params) | Unit | Note |
|---|---|---|---|---|---|---|
| 9 | base_rate→B9 | BASE RATE: | input_required |  | base → sheet unit | the bank's published BASE RATE (a declared benchmark, not derivable from positions) — bank must supply |
| 14 | td_1→D14, td_2→E14, td_3→F14, td_6→G14, td_12→H14, td_24→I14, td_36→J14 | Deposit | constant | `bsd14.column_constant` values={'td_1': 1, 'td_2': 2, 'td_3': 3, 'td_6': 6, 'td_12': 12, 'td_24': 24, 'td_36': 36} | unscaled (sheet unit already) | template tenor header (months) |
| 17 | demand→B17, savings→C17, td_1→D17, td_2→E17, td_3→F17, td_6→G17, td_12→H17, td_24→I17, td_36→J17, cd→K17, call→L17, other_deposit→M17, agriculture→N17, exports→O17, mining→P17, manufacturing→Q17, construction→R17, imports→S17, commerce→T17, others→U17 | Cedis | mapped | `bsd14.rate` currency='GHS' | unscaled (sheet unit already) | balance-weighted average contractual interest_rate of matching positions (percent); input_required where no position carries a rate — product rate table required |
| 28 | demand→B28, savings→C28, td_1→D28, td_2→E28, td_3→F28, td_6→G28, td_12→H28, td_24→I28, td_36→J28, cd→K28, call→L28, other_deposit→M28, agriculture→N28, exports→O28, mining→P28, manufacturing→Q28, construction→R28, imports→S28, commerce→T28, others→U28 | USD | mapped | `bsd14.rate` currency='USD' | unscaled (sheet unit already) | balance-weighted average contractual interest_rate of matching positions (percent); input_required where no position carries a rate — product rate table required |
| 29 | demand→B29, savings→C29, td_1→D29, td_2→E29, td_3→F29, td_6→G29, td_12→H29, td_24→I29, td_36→J29, cd→K29, call→L29, other_deposit→M29, agriculture→N29, exports→O29, mining→P29, manufacturing→Q29, construction→R29, imports→S29, commerce→T29, others→U29 | GBP | mapped | `bsd14.rate` currency='GBP' | unscaled (sheet unit already) | balance-weighted average contractual interest_rate of matching positions (percent); input_required where no position carries a rate — product rate table required |
| 30 | demand→B30, savings→C30, td_1→D30, td_2→E30, td_3→F30, td_6→G30, td_12→H30, td_24→I30, td_36→J30, cd→K30, call→L30, other_deposit→M30, agriculture→N30, exports→O30, mining→P30, manufacturing→Q30, construction→R30, imports→S30, commerce→T30, others→U30 | DEM | mapped | `bsd14.rate` currency='DEM' | unscaled (sheet unit already) | balance-weighted average contractual interest_rate of matching positions (percent); input_required where no position carries a rate — product rate table required |
| 31 | demand→B31, savings→C31, td_1→D31, td_2→E31, td_3→F31, td_6→G31, td_12→H31, td_24→I31, td_36→J31, cd→K31, call→L31, other_deposit→M31, agriculture→N31, exports→O31, mining→P31, manufacturing→Q31, construction→R31, imports→S31, commerce→T31, others→U31 | All other Currencies | mapped | `bsd14.rate` currency='other' | unscaled (sheet unit already) | balance-weighted average contractual interest_rate of matching positions (percent); input_required where no position carries a rate — product rate table required |


## Residual unmapped lines — data the bank must supply

* B9 BASE RATE — the bank's published base rate.
* Any product × currency cell without a rated position in the book (e.g. certificates of deposit, tenors the bank does not offer): the bank's offered-rate (product rate) table.

## Cross-form dependencies

None (`depends_on = ()`).

## Framework asks

* A **product rate table** (offered rates by product/tenor/currency) is the natural source for BSD14 and BSD15A's *Range of products* sheet; today rates are inferred from booked positions only. If the Data Engine gains a `product_rates` reference dataset, `bsd14.rate` can prefer it over positions.
