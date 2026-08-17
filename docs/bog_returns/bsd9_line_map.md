# BSD9 — Consolidated Balance Sheet: line / cell map

**Official workbook:** `FORM BSD9 REVISED.xls` · **Frequency:** quarterly · **Time limit:** 14 days · **Basis:** consolidated (Guide §1) · **Unit:** ¢'Million

**Sheets (3, official order):** `BSD9` (62 input cells · 43 formulas), `Annexure` (Details of inter-company transactions — a blank DATE / TYPE / SUBSIDIARY / AMOUNT grid, rows 11–28, no captured input cells), `Sheet3` (empty placeholder tab, reproduced empty).

Generated from `bog_forms/linemaps/bsd9.py` + `layouts/BSD9.json` (row tables regenerated from the line map — do not hand-edit them).

## Sources and basis

* **BSD9 is the condensed BSD2.** Every BSD9 line is one BSD2 line or a roll-up of several, in the same
  DOMESTIC (`B`) / FOREIGN (`C`) columns (TOTAL `D` = `B+C` is a template formula). Each row binds
  `bsd9.bsd2_lines` (`sources_ext/bsd9.py`) over the corresponding BSD2 rows: BSD2 is computed first
  (`depends_on`) and the resolver picks the column's BSD2 letter and sums the rows — BoG's BSD2 arithmetic
  (sub-totals, TOTAL column) is what lands here, never a second implementation. A BSD9 line whose every BSD2
  source cell is blank stays blank (input_required) instead of reading as a zero.
* Reconciliations held **by construction** (proved in `tests/services/bog_forms/test_bsd9.py` through the real
  package pipeline): BSD9 item 7 Total Assets == BSD2 item 13; BSD9 Shareholders' funds == BSD2 item 16; BSD9
  item 9 net loans == BSD2 item 8 (net); item 6 == BSD2 6(a)+(b); item 7 == BSD2 6(c)+(d)+(e); items 10–13 ==
  BSD2 9–12; item 21 Total Liabilities == BSD2 item 31 + item 17 (Other amounts allowed as capital, which BSD2
  leaves outside its own total but BSD9 includes); items 22–23 == BSD2 33–34.
* Roll-up choices that are not one-to-one (documented so an examiner can follow them): BSD2 item 8's
  "Revaluation gains on non-performing loans" (a deduction from gross alongside provisions) has no BSD9 line
  and is carried in BSD9 "Provisions" so net loans reconcile; BSD2 item 20 "Deposits of non-residents" (the
  only deposits BSD2 carries outside items 24–26) lands in BSD9 "17. Other deposits"; long- and short-term
  borrowings are the foreign + domestic BSD2 items (19+21, 18+23).
* **Consolidation of the balance-sheet lines is not on the platform.** No subsidiary line-by-line book, no
  elimination register; the template has no separate adjustment cells. Every balance-sheet line carries the
  **parent's solo BSD2 figure** and says so in its note; the bank adds subsidiaries' balances and eliminations
  before filing. **Data-gap closure (2026-08-16):** the `subsidiaries` reference dataset — the subsidiary
  register + book (`docs/data_engine/datasets/subsidiaries.md`; schema `reference_schemas/subsidiaries.py`),
  ingested through the Data Engine — now feeds the two consolidation-only blocks: "10. Minority interests"
  (`refs.sum` of the group's own `minority_interest_ghs` workings over fully consolidated subsidiaries,
  Domestic/Foreign by `functional_currency`) and the Annexure (per-subsidiary inter-company receivables and
  payables, `refs.field` ranked by amount). Proof: `tests/services/data_gaps/test_subsidiaries.py`.

## Sheet `BSD9`

Status legend — **mapped**: fed from the computed BSD2 (parent solo); **input_required**: bank must supply.

| Row | Cells | Official line | Status | Source (resolver → params) | Note |
|---|---|---|---|---|---|
| 8 | B8/C8 | 1.   Foreign currency notes and coins | mapped | `bsd9.bsd2_lines` rows=[7] | BSD2 row 7; parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 9 | B9/C9 | 2.   Correspondent acc. in non-res. financial inst. | mapped | `bsd9.bsd2_lines` rows=[8] | BSD2 row 8; parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 10 | B10/C10 | 3.   Other claims on non-residents | mapped | `bsd9.bsd2_lines` rows=[9] | BSD2 row 9; parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 11 | B11/C11 | 4. Loans and advances to non-resident | mapped | `bsd9.bsd2_lines` rows=[10] | BSD2 row 10; parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 12 | B12/C12 | 5. Equity and Other non-liquid investments abroad | mapped | `bsd9.bsd2_lines` rows=[11] | BSD2 row 11; parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 14 | B14/C14 | 6. Cash and balances with Bank of Ghana | mapped | `bsd9.bsd2_lines` rows=[14, 15] | BSD2 row 14+15; cash on hand + claims on Bank of Ghana (BSD2 6(a)+(b)); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 15 | B15/C15 | 7. Balances due from banks and other financial institutions | mapped | `bsd9.bsd2_lines` rows=[21, 30, 33] | BSD2 row 21+30+33; claims on other depository institutions + other financial institutions + cheques for clearing drawn on banks (BSD2 6(c)+(d)+(e)); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 16 | B16/C16 | 8. Bills | mapped | `bsd9.bsd2_lines` rows=[34] | BSD2 row 34; bills / short-term investments (BSD2 item 7); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 18 | B18/C18 | - Gross | mapped | `bsd9.bsd2_lines` rows=[68] | BSD2 row 68; loans, overdrafts and other advances — gross sub-total (BSD2 item 8); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 19 | B19/C19 | - Provisions | mapped | `bsd9.bsd2_lines` rows=[69, 71] | BSD2 row 69+71; total debt provision + revaluation gains on non-performing loans (both BSD2 item-8 deductions from gross; BSD9 has no separate line for the latter); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 20 | B20/C20 | - Interest  suspense | mapped | `bsd9.bsd2_lines` rows=[70] | BSD2 row 70; interest in suspense (BSD2 item 8); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 21 | B21/C21 | 10. Securities (Long term investment) | mapped | `bsd9.bsd2_lines` rows=[72] | BSD2 row 72; securities other than shares — long-term investments (BSD2 item 9); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 22 | B22/C22 | 11. Shares and other equities(Net) | mapped | `bsd9.bsd2_lines` rows=[102] | BSD2 row 102; shares and other equities, net of impairment (BSD2 item 10); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 23 | B23/C23 | 12. Other Assets | mapped | `bsd9.bsd2_lines` rows=[113] | BSD2 row 113; other assets (BSD2 item 11); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 24 | B24/C24 | 13. Property, Plant & Equipment (Net) | mapped | `bsd9.bsd2_lines` rows=[114] | BSD2 row 114; property, plant & equipment net of depreciation (BSD2 item 12); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 27 | B27/C27 | 8. Paid-Up Capital | mapped | `bsd9.bsd2_lines` rows=[128] | BSD2 row 128; paid-up capital (BSD2 item 14); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 28 | B28/C28 | 9. Reserves | mapped | `bsd9.bsd2_lines` rows=[129] | BSD2 row 129; reserves (BSD2 item 15); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 30 | B30/C30 | 10. Minority  interests | mapped | `refs.sum` kind=subsidiaries; value_field=minority_interest_ghs; filters={consolidation_method: full}; currency_field=functional_currency | Σ minority_interest_ghs (the group's own consolidation workings — non-controlling share of each FULLY consolidated subsidiary's equity) from the subsidiaries register at the latest reporting date on/before the period end; Domestic = subsidiaries whose functional currency is the bank's base currency, Foreign = any other (Guide §2); blank until the register is ingested |
| 31 | B31/C31 | 11. Other Amounts allowed as Capital | mapped | `bsd9.bsd2_lines` rows=[136] | BSD2 row 136; other amounts allowed as capital (BSD2 item 17); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 32 | B32/C32 | 12. Long term Borrowings | mapped | `bsd9.bsd2_lines` rows=[142, 168] | BSD2 row 142+168; long-term borrowings, foreign (BSD2 19) + domestic (BSD2 21); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 33 | B33/C33 | 13. Cheques for Clearing | mapped | `bsd9.bsd2_lines` rows=[178] | BSD2 row 178; cheques for clearing (BSD2 item 22); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 34 | B34/C34 | 14. Short term borrowings | mapped | `bsd9.bsd2_lines` rows=[138, 184] | BSD2 row 138+184; short-term borrowings, foreign (BSD2 18) + domestic (BSD2 23); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 35 | B35/C35 | 15. Deposits of Fin. Institution | mapped | `bsd9.bsd2_lines` rows=[196] | BSD2 row 196; deposits of financial institutions (BSD2 item 24); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 36 | B36/C36 | 16. Dep of Non-Fin. Inst. the Pub. & Govt | mapped | `bsd9.bsd2_lines` rows=[226] | BSD2 row 226; deposits of non-financial institutions, public and govt (BSD2 item 25); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 37 | B37/C37 | 17. Other deposits | mapped | `bsd9.bsd2_lines` rows=[146] | BSD2 row 146; deposits of non-residents (BSD2 item 20) — the only deposits BSD2 carries outside items 24–26; parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 38 | B38/C38 | 17. Special deposits | mapped | `bsd9.bsd2_lines` rows=[259] | BSD2 row 259; special deposits (BSD2 item 26); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 39 | B39/C39 | 18. Margins against Contingent Liab. | mapped | `bsd9.bsd2_lines` rows=[274] | BSD2 row 274; margins against contingent liabilities (BSD2 item 27); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 40 | B40/C40 | 19. Bonds issued | mapped | `bsd9.bsd2_lines` rows=[277] | BSD2 row 277; bonds issued (BSD2 item 28); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 41 | B41/C41 | 20. Other Liabilities excl. cont. Liab. | mapped | `bsd9.bsd2_lines` rows=[278] | BSD2 row 278; other liabilities (BSD2 item 29); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 43 | B43/C43 | 22. Total Contingent Liabilities | mapped | `bsd9.bsd2_lines` rows=[282] | BSD2 row 282; contingent liabilities (BSD2 item 33); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |
| 44 | B44/C44 | 23. Managed Funds | mapped | `bsd9.bsd2_lines` rows=[283] | BSD2 row 283; managed funds, contra (BSD2 item 34); parent (solo) figure from BSD2 — subsidiaries' balances and inter-company eliminations are not on the platform (no subsidiary book) |

**Totals:** 62 declared cells — 60 mapped from BSD2, 2 mapped from the `subsidiaries` register (minority interests; data-gap closure 2026-08-16).

## Sheet `Annexure` — Details of inter-company transactions

Blank data grid (`grid_lines`): rows 11–28 × `A` DATE / `B` TYPE / `C` SUBSIDIARY / `D` AMOUNT (¢'Million).
**Data-gap closure (2026-08-16):** all 72 cells read the `subsidiaries` register through `refs.field` — one
`LineSpec` per cell (each column reads a different register field), `code` `BSD9.ANNEX.<block>.<column>.R<row>`.
Rows **11–19** list the subsidiaries ranked by `intercompany_receivable_ghs` (amount due FROM the subsidiary,
largest first): A = `reporting_date`, B = `intercompany_receivable_type` (the bank's own text), C = `name`,
D = `intercompany_receivable_ghs`; rows **20–28** the same ranked by `intercompany_payable_ghs` (amount due TO
the subsidiary) with the payable fields. Nine slots per block — a bank with fewer subsidiaries leaves the
rest blank (`input_required`), a bank with more lists the nine largest. Blank until the register is ingested.

| Rows | Cells | Block | Status | Source (resolver → params) | Note |
|---|---|---|---|---|---|
| 11–19 | A/B/C/D | inter-company RECEIVABLE, rank = row − 10 | mapped | `refs.field` kind=subsidiaries; order_by=intercompany_receivable_ghs; desc; index=rank−1; field = reporting_date / intercompany_receivable_type / name / intercompany_receivable_ghs (numeric) | inter-company balance due FROM the subsidiary (receivable), subsidiary ranked N by that balance in the subsidiaries register (latest reporting date on/before the period end); blank until the register is ingested or when the bank has fewer subsidiaries than slots (type text is the bank's own — blank if the register omits it) |
| 20–28 | A/B/C/D | inter-company PAYABLE, rank = row − 19 | mapped | `refs.field` kind=subsidiaries; order_by=intercompany_payable_ghs; desc; index=rank−1; field = reporting_date / intercompany_payable_type / name / intercompany_payable_ghs (numeric) | inter-company balance due TO the subsidiary (payable), subsidiary ranked N by that balance … (as above) |

## Sheet `Sheet3`

Empty placeholder tab in the official workbook — reproduced empty; nothing to bind.

### Residual unmapped lines — data the bank must supply

- **Subsidiaries' balances and inter-company eliminations** for every balance-sheet line (parent solo today;
  the register carries each subsidiary's totals, not its lines).
- **Annexure TYPE** text when the register omits `intercompany_*_type`; Annexure slots beyond nine per block.
- Everything BSD2 itself leaves input_required flows through blank (see `bsd2_line_map.md`).

### Framework asks

1. **Subsidiary line-by-line book / consolidation entries** (shared with BSD7B, BSD3B, BSD5B): the
   `subsidiaries` register now populates minority interests and the Annexure; consolidating the 31
   balance-sheet lines needs per-subsidiary BSD2-shaped feeds (or ingested consolidation adjustments) and an
   elimination register — the parent side is already wired.
1b. **Per-column `RowSource`** — the Annexure needs 72 `LineSpec`s (one per cell) because each column reads a
   different register field; a `column_params` overlay on `RowSource` (or a `refs.field` that reads the field
   from `rc.column`) would bind a grid row in one declaration (same ask as BSD10).
2. **Column-aware `form.cell`.** `form_cell` takes one fixed ref; a Domestic/Foreign row that mirrors another
   form's Domestic/Foreign row needs a per-column ref — `bsd9.bsd2_lines` does this for BSD2 rows; a generic
   `form.cells {form, sheet, rows, column_letters}` in `sources.py` would serve BSD6/BSD8/BSD11 too.

## Cross-form dependencies

- `depends_on` BSD2 (catalogue): every mapped BSD9 cell reads the computed BSD2 of the same reporting date.
