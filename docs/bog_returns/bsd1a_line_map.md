# BSD1A — Twenty Largest Withdrawals Over the Counter: line / cell map

**Official workbook:** `FORM BSD1A.xls` · **Frequency:** weekly · **Time limit:** 9 days · **Basis:** solo

**Sheets (official order):** `20 LARGEST WITHDRAWALS`

Generated from `bog_forms/linemaps/bsd1a.py` + `layouts/BSD1A.json` (row tables are generated — regenerate, do not hand-edit them).

Status legend — **mapped**: fed from platform data via the named resolver; **input_required**: bank must supply. The sheet's only captured input cells are the serial numbers 1…20 in column `A` (the official workbook ships the data cells blank); the ranked rows' data cells are bound explicitly (`grid_lines`).

### Source reality

The platform's canonical state is positions, GL accounts, reference rows and derived facts — there is no teller / over-the-counter withdrawal transaction dataset (no `withdrawal`, `teller` or `transaction` model exists), so the twenty largest withdrawals cannot be selected from platform data. Every data cell is `input_required` and names the dataset needed. The serial numbers are re-emitted as `constant` (unscaled) so the values-only export keeps the official 1…20 numbering. `J11…J30 = SUM(E:I)` per row and `J31 = SUM(J11:J30)` are the template's own and evaluate over the blanks.

Columns: `B` CUSTOMER · `C` BRANCH · `D` TYPE OF A/C · `E` THURSDAY · `F` FRIDAY · `G` MONDAY · `H` TUESDAY · `I` WEDNESDAY (¢ Million; the template has no Saturday/Sunday columns) · `J` TOTAL (formula).

## Sheet `20 LARGEST WITHDRAWALS` — 180 bound cells (20 captured `0`-placeholder input cells + 160 blank data cells bound explicitly) · 21 template formulas · sheet unit: millions

**Binding:** 20 mapped · 160 input_required · 0 coa-mapping (11% platform-fed).

The 20 'mapped' cells are the serial numbers (structure re-emitted); **0 data cells are platform-fed** — every customer / branch / account-type / daily-amount cell is input_required.

| Row | Official line | Cells | Status | Source (resolver → filters) | Unit | Note |
|---|---|---|---|---|---|---|
| 11 | Serial #1 | A11 | mapped | `constant` value=1 | unscaled | official serial number (template content, re-emitted) |
| 12 | Serial #2 | A12 | mapped | `constant` value=2 | unscaled | official serial number (template content, re-emitted) |
| 13 | Serial #3 | A13 | mapped | `constant` value=3 | unscaled | official serial number (template content, re-emitted) |
| 14 | Serial #4 | A14 | mapped | `constant` value=4 | unscaled | official serial number (template content, re-emitted) |
| 15 | Serial #5 | A15 | mapped | `constant` value=5 | unscaled | official serial number (template content, re-emitted) |
| 16 | Serial #6 | A16 | mapped | `constant` value=6 | unscaled | official serial number (template content, re-emitted) |
| 17 | Serial #7 | A17 | mapped | `constant` value=7 | unscaled | official serial number (template content, re-emitted) |
| 18 | Serial #8 | A18 | mapped | `constant` value=8 | unscaled | official serial number (template content, re-emitted) |
| 19 | Serial #9 | A19 | mapped | `constant` value=9 | unscaled | official serial number (template content, re-emitted) |
| 20 | Serial #10 | A20 | mapped | `constant` value=10 | unscaled | official serial number (template content, re-emitted) |
| 21 | Serial #11 | A21 | mapped | `constant` value=11 | unscaled | official serial number (template content, re-emitted) |
| 22 | Serial #12 | A22 | mapped | `constant` value=12 | unscaled | official serial number (template content, re-emitted) |
| 23 | Serial #13 | A23 | mapped | `constant` value=13 | unscaled | official serial number (template content, re-emitted) |
| 24 | Serial #14 | A24 | mapped | `constant` value=14 | unscaled | official serial number (template content, re-emitted) |
| 25 | Serial #15 | A25 | mapped | `constant` value=15 | unscaled | official serial number (template content, re-emitted) |
| 26 | Serial #16 | A26 | mapped | `constant` value=16 | unscaled | official serial number (template content, re-emitted) |
| 27 | Serial #17 | A27 | mapped | `constant` value=17 | unscaled | official serial number (template content, re-emitted) |
| 28 | Serial #18 | A28 | mapped | `constant` value=18 | unscaled | official serial number (template content, re-emitted) |
| 29 | Serial #19 | A29 | mapped | `constant` value=19 | unscaled | official serial number (template content, re-emitted) |
| 30 | Serial #20 | A30 | mapped | `constant` value=20 | unscaled | official serial number (template content, re-emitted) |
| 11 | Withdrawal #1 | B11, C11, D11, E11, F11, G11, H11, I11 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 12 | Withdrawal #2 | B12, C12, D12, E12, F12, G12, H12, I12 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 13 | Withdrawal #3 | B13, C13, D13, E13, F13, G13, H13, I13 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 14 | Withdrawal #4 | B14, C14, D14, E14, F14, G14, H14, I14 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 15 | Withdrawal #5 | B15, C15, D15, E15, F15, G15, H15, I15 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 16 | Withdrawal #6 | B16, C16, D16, E16, F16, G16, H16, I16 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 17 | Withdrawal #7 | B17, C17, D17, E17, F17, G17, H17, I17 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 18 | Withdrawal #8 | B18, C18, D18, E18, F18, G18, H18, I18 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 19 | Withdrawal #9 | B19, C19, D19, E19, F19, G19, H19, I19 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 20 | Withdrawal #10 | B20, C20, D20, E20, F20, G20, H20, I20 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 21 | Withdrawal #11 | B21, C21, D21, E21, F21, G21, H21, I21 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 22 | Withdrawal #12 | B22, C22, D22, E22, F22, G22, H22, I22 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 23 | Withdrawal #13 | B23, C23, D23, E23, F23, G23, H23, I23 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 24 | Withdrawal #14 | B24, C24, D24, E24, F24, G24, H24, I24 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 25 | Withdrawal #15 | B25, C25, D25, E25, F25, G25, H25, I25 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 26 | Withdrawal #16 | B26, C26, D26, E26, F26, G26, H26, I26 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 27 | Withdrawal #17 | B27, C27, D27, E27, F27, G27, H27, I27 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 28 | Withdrawal #18 | B28, C28, D28, E28, F28, G28, H28, I28 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 29 | Withdrawal #19 | B29, C29, D29, E29, F29, G29, H29, I29 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |
| 30 | Withdrawal #20 | B30, C30, D30, E30, F30, G30, H30, I30 | input_required |  | sheet unit | over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed |

## Residual unmapped lines — data the bank must supply

- **over-the-counter cash withdrawals by customer/branch/account type and day — no teller-withdrawal transaction dataset exists on the platform; ingestion of a cash-withdrawal transactions feed (customer, branch, account type, amount, value date) is required before this return can be fed** — `20 LARGEST WITHDRAWALS` row 11 (Withdrawal #1), `20 LARGEST WITHDRAWALS` row 12 (Withdrawal #2), `20 LARGEST WITHDRAWALS` row 13 (Withdrawal #3), `20 LARGEST WITHDRAWALS` row 14 (Withdrawal #4), `20 LARGEST WITHDRAWALS` row 15 (Withdrawal #5), `20 LARGEST WITHDRAWALS` row 16 (Withdrawal #6), … (+14 more)

## Cross-form dependencies

- None (no in-workbook or external links).

## Framework / data asks

- **Ingestion dataset:** an over-the-counter cash-withdrawal transactions feed (customer, branch, account type, amount, value date) through the Data Engine; with it a `bsd1a.rank` resolver (top-20 by weekly total, per-day split) can feed rows 11–30 — the ranking rule would then be the template's (largest first), not a new BoG rule.
