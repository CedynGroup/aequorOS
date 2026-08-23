# BSD2A — Report on Foreign Currency Exposures: line / cell map

**Official workbook:** `FORM BSD2A REVISED.xls` · **Frequency:** monthly · **Time limit:** 14 days (Guide BSD2A ¶8: "together with the BSD2 Return of the same reporting date") · **Basis:** solo · **Unit:** ¢'Million (net worth of counterparty in USD M; `H` is a percentage — `unscaled`)

**Sheets (1):** `FOREIGN CURRENCY EXPOSURES` — 127 rows × 14 columns (`A` name of bank/institution · `B` type of exposure · `C` currency · `D` foreign currency amount · `E` cedi equivalent · `F` exchange conversion rate · `G` net worth of reporting bank · `H` %age of exposure to net worth · `I` maturity date · `J` interest rate · `K` rating of counterparty · `L` net worth of counterparty (USD M) · `M` date of audit report · `N` provision) under five section headers: A. FOREIGN ASSETS (rows 11–18) · B. DOMESTIC ASSETS (19–41) · C. FOREIGN LIABILITIES (42–51) · D. DOMESTIC LIABILITIES (52–96) · E. CONTINGENT LIABILITIES (97–105).

**Depends on:** `BSD2` (computed first for the same reporting date; Guide ¶1 — "all items … shown in the foreign column of BSD 2 Return should be analysed").

Generated from `bog_forms/linemaps/bsd2a.py` + `layouts/BSD2A.json` (do not hand-edit the table; regenerate).

## How the sheet is bound

The official template ships **blank** — no numeric placeholder anywhere — so `layouts/BSD2A.json` captured **0 input cells**; the grid is declared explicitly with `grid_lines` (framework helper for blank grids), read from the sheet's own labels:

- **Category rows** (the 24 labelled item rows: 13, 15, 17, 22, 24, 27, 30, 39, 44, 47, 49, 54, 56, 58, 60, 66, 71, 77, 80, 82, 84, 90, 99, 103) carry the category total — Guide ¶3(c) "the total group exposure … in the cedis column":
  - `E` cedi equivalent = the BSD2 FOREIGN-column cell(s) of the line the row names (`form.cell`; a heading spanning several BSD2 lines uses `bsd2a.form_cells_sum` — "(d) Securities" = BSD2 7 + 9 + 10 = `C34 + C72 + C102`; "(i) Individuals & others" demand deposits = BSD2 25(a)(i) + (vi) = `C228 + C233`);
  - `G` net worth of reporting bank = BSD2 16 Shareholders' Funds `D135` (Guide ¶5(vi));
  - `H` % of exposure to net worth = 100 × E / G (`bsd2a.form_cells_ratio_pct`, Guide ¶5(vii); `unscaled`; blank rather than 0 % when net worth is unknown/zero);
  - `N` provision: the loans row = BSD2 `C69` total debt provision, foreign column (Guide ¶3(d)); other categories `input_required`.
  - Three category rows have **no honest BSD2 counterpart** and stay `input_required` in `E`/`H`: "(ii) Inoperative" demand deposits (no dormancy flag in canonical data — the whole of 25(a)(i)+(vi) is shown on "(i)"), and both E. CONTINGENT LIABILITIES rows (Guide ¶7 restricts the report to foreign-currency commitments "certain to be called upon and likely to be irrecoverable" — a bank judgement; BSD2 line 33 foreign column is the ceiling).
- **Detail rows** (the 54 blank rows beneath the categories: 14, 16, 18, 23, 25–26, 28–29, 31–38, 40–41, 45–46, 48, 50–51, 55, 57, 59, 61, 67–70, 72–75, 78, 81, 83, 85–89, 91–96, 100–102, 104–105) are the per-counterparty / per-currency schedule (Guide ¶3(a)–(c), ¶5(i)–(xiii), ¶6): every column `A`–`N` is bound `input_required` — "populated from position-level data in a later wave".
- **Not data rows** (never bound): section headers 11 / 19 / 42 / 52 / 97 and their spacer rows 12 / 20 / 43 / 53 / 98; group headings whose figure sits on a named sub-row — 21 "(a). Claims on Central Banks" (→ 22 Bank of Ghana), 62 "(e) Deposits of non-financial institutions…", 64 "(1). Demand Deposits", 76 "(ii). Time Deposits", 79 "(iii) Savings" — and their spacers 63 / 65; rows 106–127 carry no label and are treated as outside the grid.
- **Column choice on category rows:** `C` currency, `D` foreign-currency amount, `F` rate, `I`–`M` are per-exposure attributes and live on the detail rows only (a category total spans currencies and counterparties).

**Category row ↔ BSD2 foreign column (all `C` cells; formulas are BSD2's own roll-ups):**

| BSD2A row | Category | BSD2 cell(s) | BSD2 line |
|---|---|---|---|
| 13 | (a) Foreign currency notes and coins | `C7` | A.1 |
| 15 | (b) Correspondent acc. in non-res. financial inst. | `C8` | A.2 |
| 17 | (c) Other claims on non-residents | `C9` | A.3 |
| 22 | (a) Claims on Central Banks — Bank of Ghana | `C15` | 6(b) |
| 24 | (b) Claims on Other Banks | `C21` | 6(c) claims on other depository institutions |
| 27 | (c) Loans, Overdrafts and Other Advances (gross; `N` = `C69`) | `C68` | 8 sub-total (gross), 8 provision |
| 30 | (d) Securities | `C34 + C72 + C102` | 7 bills + 9 long-term securities + 10 shares (net) |
| 39 | (e) Other Assets | `C113` | 11 |
| 44 | (a) Short-Term Borrowings | `C138` | 18 |
| 47 | (b) Long-term borrowing | `C142` | 19 |
| 49 | (c) Deposits of non-residents | `C146` | 20 |
| 54 | (a) Long-term Borrowings | `C168` | 21 |
| 56 | (b) Short-term borrowing | `C184` | 23 |
| 58 | (c) Cheques for clearing | `C178` | 22 |
| 60 | (d) Deposits of financial institutions | `C196` | 24 |
| 66 | (e)(1)(i) Demand deposits — Individuals & others | `C228 + C233` | 25(a)(i) + (vi) |
| 71 | (e)(1)(ii) Inoperative | — (input_required) | no dormancy flag |
| 77 | (e)(ii) Time deposits — Individual | `C244` | 25(c)(i) |
| 80 | (e)(iii) Savings — Individual | `C236` | 25(b)(i) |
| 82 | (f) Special deposits | `C259` | 26 |
| 84 | (g) Margins against Contingent Liab. | `C274` | 27 |
| 90 | (h) Other Liabilities | `C278` | 29 |
| 99 / 103 | E. Customers Liabilities / Bonds & Guarantee | — (input_required, Guide ¶7) | 33 is the ceiling |
| all category rows, `G` | Net worth of reporting bank | `D135` | 16 Shareholders' Funds |

BSD2 foreign-column lines with **no BSD2A row** under the official headings (documented, not invented): A.4 loans to non-residents `C10`, A.5 equity abroad `C11`, 6(a) cash on hand `C14`, 6(d) other financial institutions `C30`, 6(e) cheques for clearing drawn on banks `C33`, 12 fixed assets `C114`, 25(a)(ii)–(v) / 25(b)–(d) non-individual deposits, 28 bonds issued `C277`. Banks list them on the detail rows of the nearest category (Guide ¶3–¶6).

## Row-by-row map

852 declared cells (0 captured — the template ships blank) · 0 template formulas · **67 mapped · 785 input_required · 0 coa-mapping**

| Rows | Cols | Official label | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 13 | E | (a) Foreign currency notes and coins | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C7 | = BSD2 C7 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 13 | G | (a) Foreign currency notes and coins | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 13 | H | (a) Foreign currency notes and coins | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C7']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 13 | N | (a) Foreign currency notes and coins | input_required |  | provisions booked against exposures in this category — bank must supply |
| 14 | A–N | (detail row) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 15 | E | (b) Correspondent acc. in non-res. financial inst. | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C8 | = BSD2 C8 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 15 | G | (b) Correspondent acc. in non-res. financial inst. | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 15 | H | (b) Correspondent acc. in non-res. financial inst. | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C8']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 15 | N | (b) Correspondent acc. in non-res. financial inst. | input_required |  | provisions booked against exposures in this category — bank must supply |
| 16 | A–N | (detail row) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 17 | E | (c) Other claims on non-residents | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C9 | = BSD2 C9 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 17 | G | (c) Other claims on non-residents | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 17 | H | (c) Other claims on non-residents | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C9']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 17 | N | (c) Other claims on non-residents | input_required |  | provisions booked against exposures in this category — bank must supply |
| 18 | A–N | (detail row) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 22 | E | Bank of Ghana | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C15 | = BSD2 C15 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 22 | G | Bank of Ghana | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 22 | H | Bank of Ghana | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C15']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 22 | N | Bank of Ghana | input_required |  | provisions booked against exposures in this category — bank must supply |
| 23 | A–N | (detail row) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 24 | E | (b). Claims on Other Banks | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C21 | = BSD2 C21 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 24 | G | (b). Claims on Other Banks | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 24 | H | (b). Claims on Other Banks | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C21']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 24 | N | (b). Claims on Other Banks | input_required |  | provisions booked against exposures in this category — bank must supply |
| 25–26 | A–N | (detail rows) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 27 | E | (c) Loans, Overdrafts and Other Advances | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C68 | = BSD2 C68 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 27 | G | (c) Loans, Overdrafts and Other Advances | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 27 | H | (c) Loans, Overdrafts and Other Advances | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C68']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 27 | N | (c) Loans, Overdrafts and Other Advances | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C69 | = BSD2 C69 total debt provision (foreign column) — Guide BSD2A ¶3(d) |
| 28–29 | A–N | (detail rows) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 30 | E | (d) Securities | mapped | `bsd2a.form_cells_sum` form=BSD2; sheet=BSD2; refs=['C34', 'C72', 'C102'] | = BSD2 C34 + C72 + C102 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 30 | G | (d) Securities | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 30 | H | (d) Securities | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C34', 'C72', 'C102']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 30 | N | (d) Securities | input_required |  | provisions booked against exposures in this category — bank must supply |
| 31–38 | A–N | (detail rows) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 39 | E | (e) Other Assets | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C113 | = BSD2 C113 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 39 | G | (e) Other Assets | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 39 | H | (e) Other Assets | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C113']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 39 | N | (e) Other Assets | input_required |  | provisions booked against exposures in this category — bank must supply |
| 40–41 | A–N | (detail rows) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 44 | E | (a) Short-Term Borrowings | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C138 | = BSD2 C138 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 44 | G | (a) Short-Term Borrowings | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 44 | H | (a) Short-Term Borrowings | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C138']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 44 | N | (a) Short-Term Borrowings | input_required |  | provisions booked against exposures in this category — bank must supply |
| 45–46 | A–N | (detail rows) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 47 | E | (b) Long-term borrowing | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C142 | = BSD2 C142 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 47 | G | (b) Long-term borrowing | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 47 | H | (b) Long-term borrowing | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C142']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 47 | N | (b) Long-term borrowing | input_required |  | provisions booked against exposures in this category — bank must supply |
| 48 | A–N | (detail row) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 49 | E | (c) Deposits of non-residents | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C146 | = BSD2 C146 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 49 | G | (c) Deposits of non-residents | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 49 | H | (c) Deposits of non-residents | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C146']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 49 | N | (c) Deposits of non-residents | input_required |  | provisions booked against exposures in this category — bank must supply |
| 50–51 | A–N | (detail rows) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 54 | E | (a) Long-term Borrowings | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C168 | = BSD2 C168 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 54 | G | (a) Long-term Borrowings | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 54 | H | (a) Long-term Borrowings | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C168']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 54 | N | (a) Long-term Borrowings | input_required |  | provisions booked against exposures in this category — bank must supply |
| 55 | A–N | (detail row) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 56 | E | (b) Short-term borrowing | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C184 | = BSD2 C184 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 56 | G | (b) Short-term borrowing | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 56 | H | (b) Short-term borrowing | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C184']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 56 | N | (b) Short-term borrowing | input_required |  | provisions booked against exposures in this category — bank must supply |
| 57 | A–N | (detail row) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 58 | E | (c) Cheques for clearing | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C178 | = BSD2 C178 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 58 | G | (c) Cheques for clearing | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 58 | H | (c) Cheques for clearing | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C178']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 58 | N | (c) Cheques for clearing | input_required |  | provisions booked against exposures in this category — bank must supply |
| 59 | A–N | (detail row) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 60 | E | (d) Deposits of financial institutions | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C196 | = BSD2 C196 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 60 | G | (d) Deposits of financial institutions | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 60 | H | (d) Deposits of financial institutions | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C196']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 60 | N | (d) Deposits of financial institutions | input_required |  | provisions booked against exposures in this category — bank must supply |
| 61 | A–N | (detail row) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 66 | E | (i). Individuals & others | mapped | `bsd2a.form_cells_sum` form=BSD2; sheet=BSD2; refs=['C228', 'C233'] | = BSD2 C228 + C233 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 66 | G | (i). Individuals & others | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 66 | H | (i). Individuals & others | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C228', 'C233']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 66 | N | (i). Individuals & others | input_required |  | provisions booked against exposures in this category — bank must supply |
| 67–70 | A–N | (detail rows) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 71 | E | (ii). Inoperative | input_required |  | inoperative (dormant) demand deposits — no dormancy flag in canonical data; the whole of BSD2 25(a)(i)+(vi) is shown on (i) until one exists; bank must supply |
| 71 | G | (ii). Inoperative | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 71 | H | (ii). Inoperative | input_required |  | % of exposure to net worth — follows the cedi equivalent (input_required) |
| 71 | N | (ii). Inoperative | input_required |  | provisions booked against exposures in this category — bank must supply |
| 72–75 | A–N | (detail rows) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 77 | E | Individual | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C244 | = BSD2 C244 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 77 | G | Individual | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 77 | H | Individual | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C244']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 77 | N | Individual | input_required |  | provisions booked against exposures in this category — bank must supply |
| 78 | A–N | (detail row) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 80 | E | Individual | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C236 | = BSD2 C236 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 80 | G | Individual | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 80 | H | Individual | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C236']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 80 | N | Individual | input_required |  | provisions booked against exposures in this category — bank must supply |
| 81 | A–N | (detail row) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 82 | E | (f). Special deposits | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C259 | = BSD2 C259 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 82 | G | (f). Special deposits | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 82 | H | (f). Special deposits | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C259']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 82 | N | (f). Special deposits | input_required |  | provisions booked against exposures in this category — bank must supply |
| 83 | A–N | (detail row) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 84 | E | (g) Margins against Contingent Liab. | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C274 | = BSD2 C274 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 84 | G | (g) Margins against Contingent Liab. | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 84 | H | (g) Margins against Contingent Liab. | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C274']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 84 | N | (g) Margins against Contingent Liab. | input_required |  | provisions booked against exposures in this category — bank must supply |
| 85–89 | A–N | (detail rows) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 90 | E | (h) Other Liabilities | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=C278 | = BSD2 C278 (foreign column) — Guide BSD2A ¶1/¶3(c) |
| 90 | G | (h) Other Liabilities | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 90 | H | (h) Other Liabilities | mapped | `bsd2a.form_cells_ratio_pct` form=BSD2; sheet=BSD2; numerator=['C278']; denominator=['D135'] | 100 × cedi equivalent / BSD2 16 shareholders' funds — Guide BSD2A ¶5(vii) |
| 90 | N | (h) Other Liabilities | input_required |  | provisions booked against exposures in this category — bank must supply |
| 91–96 | A–N | (detail rows) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 99 | E | Customers Liabilities | input_required |  | customers' liabilities (contingent) — Guide BSD2A ¶7: report only foreign-currency commitments certain to be called upon and likely irrecoverable (bank judgement; BSD2 33 foreign column is the ceiling) |
| 99 | G | Customers Liabilities | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 99 | H | Customers Liabilities | input_required |  | % of exposure to net worth — follows the cedi equivalent (input_required) |
| 99 | N | Customers Liabilities | input_required |  | provisions booked against exposures in this category — bank must supply |
| 100–102 | A–N | (detail rows) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |
| 103 | E | Bonds & Guarantee | input_required |  | bonds & guarantees (contingent) — Guide BSD2A ¶7: report only foreign-currency commitments certain to be called upon and likely irrecoverable (bank judgement; BSD2 33 foreign column is the ceiling) |
| 103 | G | Bonds & Guarantee | mapped | `form.cell` form=BSD2; sheet=BSD2; ref=D135 | net worth of reporting bank = BSD2 16 Shareholders' Funds — Guide BSD2A ¶5(vi) |
| 103 | H | Bonds & Guarantee | input_required |  | % of exposure to net worth — follows the cedi equivalent (input_required) |
| 103 | N | Bonds & Guarantee | input_required |  | provisions booked against exposures in this category — bank must supply |
| 104–105 | A–N | (detail rows) | input_required |  | detail schedule row N — per-counterparty / per-currency exposure (Guide BSD2A ¶3–¶6) populated from position-level data in a later wave |

## Residual — data the bank must supply

- All 54 detail rows × 14 columns (756 cells): counterparty name, type of exposure, currency, foreign-currency amount, cedi equivalent, exchange rate used (GBA interbank mid-rate, ¶5(v)), maturity, interest rate, counterparty rating / net worth / audit date, provision — per exposure (position-level rendering, later wave).
- Category rows: provisions other than the loans row (`N`), and the three judgement rows (`E`, `H` of 71, 99, 103).

## Cross-form dependencies

- `BSD2A` ← `BSD2` (same reporting date): 21 category cedi cells (`form.cell` / `bsd2a.form_cells_sum` over BSD2 `C…`), 24 net-worth cells (`D135`), 21 ratios, 1 provision (`C69`). BSD2's own foreign column is fed by `positions.sum` / `facts.sum` under the Guide's currency rule — the tie is by construction and is asserted in `tests/services/bog_forms/test_bsd2a.py`.
- `BSD13` (NOP) depends on `BSD2A` per the registry — nothing in this map is consumed by BSD13 yet (Wave 4).

## Critical relationships proved (`tests/services/bog_forms/test_bsd2a.py`)

- `E13 = BSD2!C7`, `E27 = BSD2!C68` with `N27 = BSD2!C69`, `E30 = BSD2!C34 + C72 + C102`, `E39 = BSD2!C113`, `E66 = C228 + C233` — every category `E` equals its named BSD2 cell(s) on a book with FX cash, an FX loan and an FX cocoa bill;
- `G = BSD2!D135` on all 24 category rows; `H = 100 × E / D135` (unscaled) on the 21 mapped rows; judgement rows blank;
- status split 67 mapped / 785 input_required / 0 unmapped, no engine errors, no missing dependency; the values-only xlsx carries the ¢'Million-scaled `E`, the unscaled `H`, and lists every detail cell on the Completion-notes sheet.

## Framework asks

1. **Per-currency detail from positions.** Filling the 54 detail rows needs a *listing* resolver over `CanonicalPositionSnapshot` grouped by counterparty × currency (name, native amount, `balance_ghs`, rate, `contractual_maturity`, `interest_rate`, counterparty rating) that ranks/orders rows into the blank grid — the BSD3 `bsd3.rank` pattern generalised (`positions.list {kind, index, field}`); today `positions.sum` cannot split one row by currency.
2. **`positions.sum` cedi equivalents** — see `bsd2_line_map.md` framework ask 2: for FOREIGN columns the resolver should sum `attributes["balance_ghs"]`, not the native `balance`; BSD2A inherits BSD2's foreign column as-is.
3. **Exchange rate column `F`** could be fed from the market-data layer (`market_data.py` FX spot for the period end) once a resolver exposes it; the Guide requires the GBA interbank mid-rate the bank actually used, so the bank's own value must be able to override.
4. **Net worth denominator** — `D135` is BSD2's TOTAL column of Shareholders' Funds; if the coordinator prefers regulatory capital (BSD5A) as "net worth", switch `_NET_WORTH_REF` in one place.
