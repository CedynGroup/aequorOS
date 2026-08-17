# BSD3A — Large Exposures: Advances and Deposits: line / cell map

**Official workbook:** `FORM BSD3A REVISED.xls` · **Frequency:** monthly · **Time limit:** 14 days · **Basis:** solo (own books; group variant = BSD3B) · **Unit:** ¢'Million on every sheet (template header `(¢'Million)`; the `ALL FIGURES IN GHC` banner is the template's own wording) — the depositor count (Sheet 1 row 25) is an unscaled count · **Depends on (catalogue):** BSD2 (no cell link — a reconciliation dependency, see §6) · **Statutory basis:** Section 53(1) of the Banking Act, 2004 (Act 673) (Guide, Form BSD3).

**Sheets (3, official order):** `BSD3-Sheet-1` (TWENTY LARGEST DEPOSITORS), `BSD3-Sheet-2` (TEN LARGEST EXPOSURES TO MONETARY SECTOR), `BSD3-Sheet-3` (FIFTY LARGEST NON-MONETARY SECTOR EXPOSURES). The workbook's sheet tabs say "(Sheet 1 of 5)…(3 of 5)"; the official file carries only these three.

Generated from `bog_forms/linemaps/bsd3a.py` + `sources_ext/bsd3.py` + `layouts/BSD3A.json` (row tables in §3 are produced by the snippet in §8 — do not hand-edit; regenerate).

## 1. What the template is, and how it is bound

All three sheets are **ranked rosters whose data cells are BLANK in the official file** (no `0`
placeholder): the extractor therefore captured only the row-total / grand-total FORMULAS and — on
Sheet 1 — the four numeric row numbers `A27:A30` (22–25) as "input" cells. Every roster cell is
bound explicitly with `_common.grid_lines` (rows × official columns, formula cells skipped), so the
export writes names and amounts into the exact official cells and BoG's own formulas roll them up:

| Sheet | Rank rows | Official data columns bound | Template formulas (never bound) |
|---|---|---|---|
| `BSD3-Sheet-1` | 6–25 = ranks 1–20 | `B` CUSTOMER · `C` TYPE OF ACCOUNT · `D` FINAL MATURITY DATE · `E` CURRENCY FOREIGN-GHC (cedi equivalent) · `F` CURRENCY CEDIS-GHC · `H` REMARKS; row 27 `E27`/`F27` accrued interest; row 30 `G30` total no. of depositors; `A27:A30` row numbers 22–25 | `G6:G25 = E+F` (row totals) · `G26 = SUM(G6:G25)` (item 21 Total) · `G27 = E27+F27` · `G28 = G26+G27` (item 23) · `G29 = G26/G28` (item 24, %) |
| `BSD3-Sheet-2` | 6–15 = ranks 1–10 | `B` CUSTOMER · `C` FINAL MATURITY DATE · `D` CURRENCY FOREIGN-GHC · `E` CURRENCY CEDIS-GHC · `G` OF WHICH ON BALANCE SHEET · `H` VALUE OF SECURITY · `I` REMARKS | `F6:F15 = D+E` (TOTAL EXPOSURE) |
| `BSD3-Sheet-3` | 5–54 = ranks 1–50 | `B` Customer · `C` Type of Exposure · `D` Drawn Down · `E` Undrawn Facility · `F` Other Contingent Liability · `H` Value of Security · `I` Type of Security · `J` REMARKS | `G5:G54 = D+E+F` (Total Exposures) |

**Sources.** `bsd3.rank {kind, rank}` returns one attribute of the N-th largest entity — the bound
column key IS the field (`name`, `account_type`, `maturity`, `foreign`, `cedi`, `on_balance`,
`drawn`, `undrawn`, `contingent`, `exposure_type`; also `amount`/`total`, `currency`);
`bsd3.count {kind}` returns the number of distinct counterparties in a population. Both live in
`sources_ext/bsd3.py` and REUSE the Large-Exposures / LMT engine (`le_generation`):
`_load_canonical_rows` (the current-generation, accepted/warning snapshot slice **at the period
end**, `balance_ghs` cedi equivalents exactly as `fact_derivation` reads them) and
`_entity_identity` (connected-counterparty grouping: `group_reference` first, then the single
counterparty, then the `issuer` attribute for counterparty-less securities). BSD3A therefore
reconciles to LE-MONTHLY / LMT by construction.

## 2. Guide rules applied and documented decisions

| Guide BSD3 item | Rule | How the map applies it |
|---|---|---|
| 1 | Twenty largest depositors; "all placements from the same depositor and/or from different depositors within the same group should be aggregated" | population `depositor` = **DEPOSIT** positions, aggregated per counterparty / connected group; sorted by total, ties by name. INTERBANK_BORROWING is deliberately NOT a "depositor" (it is BSD2 §21–25 balances due to financial institutions) — documented decision, reversible in one line. |
| item 22 | accrued interest on the 20 largest depositors | `E27`/`F27` **input_required** — no accruals sub-ledger in the canonical model (same gap as BSD2's accrued-interest lines). `G27`, `G28`, `G29` are template formulas (blank accrued → 0, so `G29` = 100% until supplied). |
| 2 | Total exposures = local currency + cedi equivalent of forex components | every amount is a cedi (base-unit) figure; the row totals are BoG's `E+F` / `D+E` / `D+E+F`. |
| 3 | Ten largest exposures to the monetary sector | population `monetary_exposure` = exposure positions whose counterparty type ∈ {BANK_OECD, BANK_NON_OECD, CENTRAL_BANK, NBFI} (Guide: "Banks, Discount Houses, Building Societies and other financial institutions participating in the money market"; the central bank is a money-market participant). |
| 4 | Fifty largest non-monetary exposures; exposures = equity investments, loans/overdrafts/advances, bills, leases, acceptances, undrawn commitments, guarantees and other contingents; facilities to one borrower aggregated, group companies as one entity; total = drawn + undrawn + other contingent | population `non_monetary_exposure` = every other (or unattributed-type) counterparty. **Exposure position types:** drawn = LOAN, INTERBANK_PLACEMENT, SECURITY_HOLDING (balances); undrawn = COMMITMENT_UNDRAWN; other contingent = LC_GUARANTEE (off-balance amounts take `notional_ghs`, else the balance — the LMT Table 2 convention). Ranking key = drawn + undrawn + contingent, i.e. exactly the template's Total column. **Sovereign / government holdings rank as non-monetary exposures** (they are exposures; the LE engine's exemption is a limits concept, not a population rule) — documented, reversible by filter. |
| 4 | Value of security = ORIGINAL valuation at grant (a later valuation only if professional); type = the asset charged (fixed charge, floating charge, guarantee) | **input_required** on both sheets. The canonical model carries only `crm_collateral_ghs` / `crm_collateral_class` (the CRM-eligible value, a different figure); reporting it as "value of security" would mis-state the line. |
| 5 | Remarks elaborate on unclear items | `H` / `I` / `J` remarks **input_required** (free text). |
| 6 | Foreign currency = the cedi equivalent of the foreign component | `foreign` / `cedi` split on the POSITION currency vs the bank's base currency (`banks.currency`), amounts already cedi-equivalent (`balance_ghs`). |
| 7 | BSD3-GROUP for each subsidiary | BSD3B (`bsd3b_line_map.md`) — same map in `subsidiary` scope. |
| General §1–3 | own books only, no fiduciary; syndication participants excluded; gross unless set-off right | inherited from the canonical slice the LE engine reads (positions the bank booked as principal); nothing here nets. |

Other decisions:

- **Final maturity date** = the LATEST `contractual_maturity` among the entity's aggregated positions (ISO date); blank when every position is undated (demand-natured deposits) — never fabricated.
- **Type of account** = the set of `deposit_account_type` values (`CURRENT, FIXED`, …); **Type of exposure** = the canonical position types in Guide vocabulary (`Loan / advance`, `Placement`, `Investment`, `Undrawn commitment`, `Guarantee / contingent`).
- **Names**: the counterparty's canonical name; a connected group reports under its `group_reference` (the LE engine's convention), so the roster line is the group entity the Guide asks for.
- **Unattributed positions** (no counterparty and no issuer — e.g. pooled retail deposits ingested without counterparties) cannot rank and are excluded, exactly as in LE-MONTHLY / LMT (`lmt.unattributed_funding`); their money is still in BSD2. The depositor count (`G30`) counts distinct counterparties holding DEPOSIT positions (all depositors, not only the twenty listed).
- **Rows beyond the bank's population** stay blank; the framework has no "not applicable" status, so those cells appear as `input_required` in the Completion-notes sheet with the note "blank when the bank has fewer than N ranked counterparties" (framework ask §7).
- **As-of slice**: exactly the period end (`as_of_date == period_end`, the LE-MONTHLY slice), whereas `positions.sum` (BSD2) takes the latest snapshot on/before period end — identical on a book with a period-end snapshot; recorded as a reconciling item.
- `A27:A30` (22–25) are the template's OWN row numbers, captured as inputs only because they are numeric; bound to their constant (`unscaled`) so the official numbering survives the values-only export.

## 3. Row-by-row map (generated)

Status legend — **mapped**: fed from platform data via the named resolver (a rank beyond the bank's population still exports blank); **input_required**: bank must supply.

### Sheet `BSD3-Sheet-1` — 127 cells bound (105 mapped · 22 input_required) · 24 template formulas · 4 numeric cells captured by the extractor

| Row | Official label | Cells (column → cell) | Status | Source (resolver → params) | Note |
|---|---|---|---|---|---|
| 6 | 1. | name→B6, account_type→C6, maturity→D6, foreign→E6, cedi→F6 | mapped | `bsd3.rank` kind=depositor; rank=1 | rank 1 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 1 ranked counterparties |
| 6 | 1. | remarks→H6 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 7 | 2. | name→B7, account_type→C7, maturity→D7, foreign→E7, cedi→F7 | mapped | `bsd3.rank` kind=depositor; rank=2 | rank 2 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 2 ranked counterparties |
| 7 | 2. | remarks→H7 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 8 | 3. | name→B8, account_type→C8, maturity→D8, foreign→E8, cedi→F8 | mapped | `bsd3.rank` kind=depositor; rank=3 | rank 3 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 3 ranked counterparties |
| 8 | 3. | remarks→H8 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 9 | 4. | name→B9, account_type→C9, maturity→D9, foreign→E9, cedi→F9 | mapped | `bsd3.rank` kind=depositor; rank=4 | rank 4 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 4 ranked counterparties |
| 9 | 4. | remarks→H9 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 10 | 5. | name→B10, account_type→C10, maturity→D10, foreign→E10, cedi→F10 | mapped | `bsd3.rank` kind=depositor; rank=5 | rank 5 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 5 ranked counterparties |
| 10 | 5. | remarks→H10 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 11 | 6. | name→B11, account_type→C11, maturity→D11, foreign→E11, cedi→F11 | mapped | `bsd3.rank` kind=depositor; rank=6 | rank 6 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 6 ranked counterparties |
| 11 | 6. | remarks→H11 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 12 | 7. | name→B12, account_type→C12, maturity→D12, foreign→E12, cedi→F12 | mapped | `bsd3.rank` kind=depositor; rank=7 | rank 7 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 7 ranked counterparties |
| 12 | 7. | remarks→H12 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 13 | 8. | name→B13, account_type→C13, maturity→D13, foreign→E13, cedi→F13 | mapped | `bsd3.rank` kind=depositor; rank=8 | rank 8 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 8 ranked counterparties |
| 13 | 8. | remarks→H13 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 14 | 9. | name→B14, account_type→C14, maturity→D14, foreign→E14, cedi→F14 | mapped | `bsd3.rank` kind=depositor; rank=9 | rank 9 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 9 ranked counterparties |
| 14 | 9. | remarks→H14 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 15 | 10. | name→B15, account_type→C15, maturity→D15, foreign→E15, cedi→F15 | mapped | `bsd3.rank` kind=depositor; rank=10 | rank 10 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 10 ranked counterparties |
| 15 | 10. | remarks→H15 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 16 | 11. | name→B16, account_type→C16, maturity→D16, foreign→E16, cedi→F16 | mapped | `bsd3.rank` kind=depositor; rank=11 | rank 11 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 11 ranked counterparties |
| 16 | 11. | remarks→H16 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 17 | 12. | name→B17, account_type→C17, maturity→D17, foreign→E17, cedi→F17 | mapped | `bsd3.rank` kind=depositor; rank=12 | rank 12 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 12 ranked counterparties |
| 17 | 12. | remarks→H17 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 18 | 13. | name→B18, account_type→C18, maturity→D18, foreign→E18, cedi→F18 | mapped | `bsd3.rank` kind=depositor; rank=13 | rank 13 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 13 ranked counterparties |
| 18 | 13. | remarks→H18 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 19 | 14. | name→B19, account_type→C19, maturity→D19, foreign→E19, cedi→F19 | mapped | `bsd3.rank` kind=depositor; rank=14 | rank 14 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 14 ranked counterparties |
| 19 | 14. | remarks→H19 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 20 | 15. | name→B20, account_type→C20, maturity→D20, foreign→E20, cedi→F20 | mapped | `bsd3.rank` kind=depositor; rank=15 | rank 15 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 15 ranked counterparties |
| 20 | 15. | remarks→H20 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 21 | 16. | name→B21, account_type→C21, maturity→D21, foreign→E21, cedi→F21 | mapped | `bsd3.rank` kind=depositor; rank=16 | rank 16 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 16 ranked counterparties |
| 21 | 16. | remarks→H21 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 22 | 17. | name→B22, account_type→C22, maturity→D22, foreign→E22, cedi→F22 | mapped | `bsd3.rank` kind=depositor; rank=17 | rank 17 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 17 ranked counterparties |
| 22 | 17. | remarks→H22 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 23 | 18. | name→B23, account_type→C23, maturity→D23, foreign→E23, cedi→F23 | mapped | `bsd3.rank` kind=depositor; rank=18 | rank 18 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 18 ranked counterparties |
| 23 | 18. | remarks→H23 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 24 | 19. | name→B24, account_type→C24, maturity→D24, foreign→E24, cedi→F24 | mapped | `bsd3.rank` kind=depositor; rank=19 | rank 19 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 19 ranked counterparties |
| 24 | 19. | remarks→H24 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 25 | 20. | name→B25, account_type→C25, maturity→D25, foreign→E25, cedi→F25 | mapped | `bsd3.rank` kind=depositor; rank=20 | rank 20 depositor — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 20 ranked counterparties |
| 25 | 20. | remarks→H25 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 27 | Accrued Interest | foreign→E27, cedi→F27 | input_required |  | accrued interest on the twenty largest depositors (Guide item 22) — accruals sub-ledger required (same gap as BSD2's accrued-interest lines) |
| 27 | Accrued Interest | number→A27 | mapped | `constant` value=22 | template row number (a numeric label the extractor captured as an input) — constant keeps the official numbering in the values-only export |
| 28 | Total Deposits(Including accrued Interest) | number→A28 | mapped | `constant` value=23 | template row number (a numeric label the extractor captured as an input) — constant keeps the official numbering in the values-only export |
| 29 | 21 as a percentage of 22 | number→A29 | mapped | `constant` value=24 | template row number (a numeric label the extractor captured as an input) — constant keeps the official numbering in the values-only export |
| 30 | Total no. of depositors. | count→G30 | mapped | `bsd3.count` kind=depositor | number of distinct counterparties holding DEPOSIT positions (all depositors, not only the twenty listed) — a count, unscaled |
| 30 | Total no. of depositors. | number→A30 | mapped | `constant` value=25 | template row number (a numeric label the extractor captured as an input) — constant keeps the official numbering in the values-only export |

Template formulas on this sheet (24 cells; evaluated by the engine, never bound): G6 `=E6+F6`; G26 `=SUM(G6:G25)`; G28 `=G26+G27`; G29 `=G26/G28`.

### Sheet `BSD3-Sheet-2` — 70 cells bound (50 mapped · 20 input_required) · 10 template formulas · 0 numeric cells captured by the extractor

| Row | Official label | Cells (column → cell) | Status | Source (resolver → params) | Note |
|---|---|---|---|---|---|
| 6 | 1. | name→B6, maturity→C6, foreign→D6, cedi→E6, on_balance→G6 | mapped | `bsd3.rank` kind=monetary_exposure; rank=1 | rank 1 monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 1 ranked counterparties |
| 6 | 1. | security_value→H6 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 6 | 1. | remarks→I6 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 7 | 2. | name→B7, maturity→C7, foreign→D7, cedi→E7, on_balance→G7 | mapped | `bsd3.rank` kind=monetary_exposure; rank=2 | rank 2 monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 2 ranked counterparties |
| 7 | 2. | security_value→H7 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 7 | 2. | remarks→I7 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 8 | 3. | name→B8, maturity→C8, foreign→D8, cedi→E8, on_balance→G8 | mapped | `bsd3.rank` kind=monetary_exposure; rank=3 | rank 3 monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 3 ranked counterparties |
| 8 | 3. | security_value→H8 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 8 | 3. | remarks→I8 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 9 | 4. | name→B9, maturity→C9, foreign→D9, cedi→E9, on_balance→G9 | mapped | `bsd3.rank` kind=monetary_exposure; rank=4 | rank 4 monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 4 ranked counterparties |
| 9 | 4. | security_value→H9 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 9 | 4. | remarks→I9 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 10 | 5. | name→B10, maturity→C10, foreign→D10, cedi→E10, on_balance→G10 | mapped | `bsd3.rank` kind=monetary_exposure; rank=5 | rank 5 monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 5 ranked counterparties |
| 10 | 5. | security_value→H10 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 10 | 5. | remarks→I10 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 11 | 6. | name→B11, maturity→C11, foreign→D11, cedi→E11, on_balance→G11 | mapped | `bsd3.rank` kind=monetary_exposure; rank=6 | rank 6 monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 6 ranked counterparties |
| 11 | 6. | security_value→H11 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 11 | 6. | remarks→I11 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 12 | 7. | name→B12, maturity→C12, foreign→D12, cedi→E12, on_balance→G12 | mapped | `bsd3.rank` kind=monetary_exposure; rank=7 | rank 7 monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 7 ranked counterparties |
| 12 | 7. | security_value→H12 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 12 | 7. | remarks→I12 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 13 | 8. | name→B13, maturity→C13, foreign→D13, cedi→E13, on_balance→G13 | mapped | `bsd3.rank` kind=monetary_exposure; rank=8 | rank 8 monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 8 ranked counterparties |
| 13 | 8. | security_value→H13 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 13 | 8. | remarks→I13 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 14 | 9. | name→B14, maturity→C14, foreign→D14, cedi→E14, on_balance→G14 | mapped | `bsd3.rank` kind=monetary_exposure; rank=9 | rank 9 monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 9 ranked counterparties |
| 14 | 9. | security_value→H14 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 14 | 9. | remarks→I14 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 15 | 10. | name→B15, maturity→C15, foreign→D15, cedi→E15, on_balance→G15 | mapped | `bsd3.rank` kind=monetary_exposure; rank=10 | rank 10 monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 10 ranked counterparties |
| 15 | 10. | security_value→H15 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 15 | 10. | remarks→I15 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |

Template formulas on this sheet (10 cells; evaluated by the engine, never bound): F6 `=D6+E6`.

### Sheet `BSD3-Sheet-3` — 400 cells bound (250 mapped · 150 input_required) · 50 template formulas · 0 numeric cells captured by the extractor

| Row | Official label | Cells (column → cell) | Status | Source (resolver → params) | Note |
|---|---|---|---|---|---|
| 5 | 1. | name→B5, exposure_type→C5, drawn→D5, undrawn→E5, contingent→F5 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=1 | rank 1 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 1 ranked counterparties |
| 5 | 1. | security_value→H5, security_type→I5 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 5 | 1. | remarks→J5 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 6 | 2. | name→B6, exposure_type→C6, drawn→D6, undrawn→E6, contingent→F6 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=2 | rank 2 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 2 ranked counterparties |
| 6 | 2. | security_value→H6, security_type→I6 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 6 | 2. | remarks→J6 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 7 | 3. | name→B7, exposure_type→C7, drawn→D7, undrawn→E7, contingent→F7 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=3 | rank 3 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 3 ranked counterparties |
| 7 | 3. | security_value→H7, security_type→I7 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 7 | 3. | remarks→J7 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 8 | 4. | name→B8, exposure_type→C8, drawn→D8, undrawn→E8, contingent→F8 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=4 | rank 4 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 4 ranked counterparties |
| 8 | 4. | security_value→H8, security_type→I8 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 8 | 4. | remarks→J8 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 9 | 5. | name→B9, exposure_type→C9, drawn→D9, undrawn→E9, contingent→F9 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=5 | rank 5 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 5 ranked counterparties |
| 9 | 5. | security_value→H9, security_type→I9 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 9 | 5. | remarks→J9 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 10 | 6. | name→B10, exposure_type→C10, drawn→D10, undrawn→E10, contingent→F10 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=6 | rank 6 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 6 ranked counterparties |
| 10 | 6. | security_value→H10, security_type→I10 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 10 | 6. | remarks→J10 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 11 | 7. | name→B11, exposure_type→C11, drawn→D11, undrawn→E11, contingent→F11 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=7 | rank 7 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 7 ranked counterparties |
| 11 | 7. | security_value→H11, security_type→I11 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 11 | 7. | remarks→J11 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 12 | 8. | name→B12, exposure_type→C12, drawn→D12, undrawn→E12, contingent→F12 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=8 | rank 8 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 8 ranked counterparties |
| 12 | 8. | security_value→H12, security_type→I12 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 12 | 8. | remarks→J12 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 13 | 9. | name→B13, exposure_type→C13, drawn→D13, undrawn→E13, contingent→F13 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=9 | rank 9 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 9 ranked counterparties |
| 13 | 9. | security_value→H13, security_type→I13 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 13 | 9. | remarks→J13 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 14 | 10. | name→B14, exposure_type→C14, drawn→D14, undrawn→E14, contingent→F14 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=10 | rank 10 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 10 ranked counterparties |
| 14 | 10. | security_value→H14, security_type→I14 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 14 | 10. | remarks→J14 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 15 | 11. | name→B15, exposure_type→C15, drawn→D15, undrawn→E15, contingent→F15 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=11 | rank 11 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 11 ranked counterparties |
| 15 | 11. | security_value→H15, security_type→I15 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 15 | 11. | remarks→J15 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 16 | 12. | name→B16, exposure_type→C16, drawn→D16, undrawn→E16, contingent→F16 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=12 | rank 12 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 12 ranked counterparties |
| 16 | 12. | security_value→H16, security_type→I16 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 16 | 12. | remarks→J16 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 17 | 13. | name→B17, exposure_type→C17, drawn→D17, undrawn→E17, contingent→F17 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=13 | rank 13 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 13 ranked counterparties |
| 17 | 13. | security_value→H17, security_type→I17 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 17 | 13. | remarks→J17 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 18 | 14. | name→B18, exposure_type→C18, drawn→D18, undrawn→E18, contingent→F18 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=14 | rank 14 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 14 ranked counterparties |
| 18 | 14. | security_value→H18, security_type→I18 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 18 | 14. | remarks→J18 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 19 | 15. | name→B19, exposure_type→C19, drawn→D19, undrawn→E19, contingent→F19 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=15 | rank 15 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 15 ranked counterparties |
| 19 | 15. | security_value→H19, security_type→I19 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 19 | 15. | remarks→J19 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 20 | 16. | name→B20, exposure_type→C20, drawn→D20, undrawn→E20, contingent→F20 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=16 | rank 16 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 16 ranked counterparties |
| 20 | 16. | security_value→H20, security_type→I20 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 20 | 16. | remarks→J20 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 21 | 17. | name→B21, exposure_type→C21, drawn→D21, undrawn→E21, contingent→F21 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=17 | rank 17 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 17 ranked counterparties |
| 21 | 17. | security_value→H21, security_type→I21 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 21 | 17. | remarks→J21 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 22 | 18. | name→B22, exposure_type→C22, drawn→D22, undrawn→E22, contingent→F22 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=18 | rank 18 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 18 ranked counterparties |
| 22 | 18. | security_value→H22, security_type→I22 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 22 | 18. | remarks→J22 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 23 | 19. | name→B23, exposure_type→C23, drawn→D23, undrawn→E23, contingent→F23 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=19 | rank 19 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 19 ranked counterparties |
| 23 | 19. | security_value→H23, security_type→I23 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 23 | 19. | remarks→J23 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 24 | 20. | name→B24, exposure_type→C24, drawn→D24, undrawn→E24, contingent→F24 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=20 | rank 20 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 20 ranked counterparties |
| 24 | 20. | security_value→H24, security_type→I24 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 24 | 20. | remarks→J24 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 25 | 21. | name→B25, exposure_type→C25, drawn→D25, undrawn→E25, contingent→F25 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=21 | rank 21 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 21 ranked counterparties |
| 25 | 21. | security_value→H25, security_type→I25 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 25 | 21. | remarks→J25 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 26 | 22. | name→B26, exposure_type→C26, drawn→D26, undrawn→E26, contingent→F26 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=22 | rank 22 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 22 ranked counterparties |
| 26 | 22. | security_value→H26, security_type→I26 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 26 | 22. | remarks→J26 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 27 | 23. | name→B27, exposure_type→C27, drawn→D27, undrawn→E27, contingent→F27 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=23 | rank 23 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 23 ranked counterparties |
| 27 | 23. | security_value→H27, security_type→I27 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 27 | 23. | remarks→J27 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 28 | 24. | name→B28, exposure_type→C28, drawn→D28, undrawn→E28, contingent→F28 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=24 | rank 24 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 24 ranked counterparties |
| 28 | 24. | security_value→H28, security_type→I28 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 28 | 24. | remarks→J28 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 29 | 25. | name→B29, exposure_type→C29, drawn→D29, undrawn→E29, contingent→F29 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=25 | rank 25 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 25 ranked counterparties |
| 29 | 25. | security_value→H29, security_type→I29 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 29 | 25. | remarks→J29 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 30 | 26. | name→B30, exposure_type→C30, drawn→D30, undrawn→E30, contingent→F30 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=26 | rank 26 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 26 ranked counterparties |
| 30 | 26. | security_value→H30, security_type→I30 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 30 | 26. | remarks→J30 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 31 | 27. | name→B31, exposure_type→C31, drawn→D31, undrawn→E31, contingent→F31 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=27 | rank 27 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 27 ranked counterparties |
| 31 | 27. | security_value→H31, security_type→I31 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 31 | 27. | remarks→J31 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 32 | 28. | name→B32, exposure_type→C32, drawn→D32, undrawn→E32, contingent→F32 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=28 | rank 28 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 28 ranked counterparties |
| 32 | 28. | security_value→H32, security_type→I32 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 32 | 28. | remarks→J32 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 33 | 29. | name→B33, exposure_type→C33, drawn→D33, undrawn→E33, contingent→F33 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=29 | rank 29 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 29 ranked counterparties |
| 33 | 29. | security_value→H33, security_type→I33 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 33 | 29. | remarks→J33 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 34 | 30. | name→B34, exposure_type→C34, drawn→D34, undrawn→E34, contingent→F34 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=30 | rank 30 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 30 ranked counterparties |
| 34 | 30. | security_value→H34, security_type→I34 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 34 | 30. | remarks→J34 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 35 | 31. | name→B35, exposure_type→C35, drawn→D35, undrawn→E35, contingent→F35 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=31 | rank 31 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 31 ranked counterparties |
| 35 | 31. | security_value→H35, security_type→I35 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 35 | 31. | remarks→J35 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 36 | 32. | name→B36, exposure_type→C36, drawn→D36, undrawn→E36, contingent→F36 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=32 | rank 32 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 32 ranked counterparties |
| 36 | 32. | security_value→H36, security_type→I36 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 36 | 32. | remarks→J36 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 37 | 33. | name→B37, exposure_type→C37, drawn→D37, undrawn→E37, contingent→F37 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=33 | rank 33 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 33 ranked counterparties |
| 37 | 33. | security_value→H37, security_type→I37 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 37 | 33. | remarks→J37 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 38 | 34. | name→B38, exposure_type→C38, drawn→D38, undrawn→E38, contingent→F38 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=34 | rank 34 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 34 ranked counterparties |
| 38 | 34. | security_value→H38, security_type→I38 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 38 | 34. | remarks→J38 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 39 | 35. | name→B39, exposure_type→C39, drawn→D39, undrawn→E39, contingent→F39 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=35 | rank 35 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 35 ranked counterparties |
| 39 | 35. | security_value→H39, security_type→I39 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 39 | 35. | remarks→J39 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 40 | 36. | name→B40, exposure_type→C40, drawn→D40, undrawn→E40, contingent→F40 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=36 | rank 36 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 36 ranked counterparties |
| 40 | 36. | security_value→H40, security_type→I40 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 40 | 36. | remarks→J40 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 41 | 37. | name→B41, exposure_type→C41, drawn→D41, undrawn→E41, contingent→F41 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=37 | rank 37 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 37 ranked counterparties |
| 41 | 37. | security_value→H41, security_type→I41 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 41 | 37. | remarks→J41 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 42 | 38. | name→B42, exposure_type→C42, drawn→D42, undrawn→E42, contingent→F42 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=38 | rank 38 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 38 ranked counterparties |
| 42 | 38. | security_value→H42, security_type→I42 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 42 | 38. | remarks→J42 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 43 | 39. | name→B43, exposure_type→C43, drawn→D43, undrawn→E43, contingent→F43 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=39 | rank 39 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 39 ranked counterparties |
| 43 | 39. | security_value→H43, security_type→I43 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 43 | 39. | remarks→J43 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 44 | 40. | name→B44, exposure_type→C44, drawn→D44, undrawn→E44, contingent→F44 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=40 | rank 40 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 40 ranked counterparties |
| 44 | 40. | security_value→H44, security_type→I44 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 44 | 40. | remarks→J44 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 45 | 41. | name→B45, exposure_type→C45, drawn→D45, undrawn→E45, contingent→F45 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=41 | rank 41 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 41 ranked counterparties |
| 45 | 41. | security_value→H45, security_type→I45 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 45 | 41. | remarks→J45 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 46 | 42. | name→B46, exposure_type→C46, drawn→D46, undrawn→E46, contingent→F46 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=42 | rank 42 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 42 ranked counterparties |
| 46 | 42. | security_value→H46, security_type→I46 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 46 | 42. | remarks→J46 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 47 | 43. | name→B47, exposure_type→C47, drawn→D47, undrawn→E47, contingent→F47 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=43 | rank 43 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 43 ranked counterparties |
| 47 | 43. | security_value→H47, security_type→I47 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 47 | 43. | remarks→J47 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 48 | 44. | name→B48, exposure_type→C48, drawn→D48, undrawn→E48, contingent→F48 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=44 | rank 44 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 44 ranked counterparties |
| 48 | 44. | security_value→H48, security_type→I48 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 48 | 44. | remarks→J48 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 49 | 45. | name→B49, exposure_type→C49, drawn→D49, undrawn→E49, contingent→F49 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=45 | rank 45 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 45 ranked counterparties |
| 49 | 45. | security_value→H49, security_type→I49 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 49 | 45. | remarks→J49 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 50 | 46. | name→B50, exposure_type→C50, drawn→D50, undrawn→E50, contingent→F50 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=46 | rank 46 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 46 ranked counterparties |
| 50 | 46. | security_value→H50, security_type→I50 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 50 | 46. | remarks→J50 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 51 | 47. | name→B51, exposure_type→C51, drawn→D51, undrawn→E51, contingent→F51 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=47 | rank 47 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 47 ranked counterparties |
| 51 | 47. | security_value→H51, security_type→I51 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 51 | 47. | remarks→J51 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 52 | 48. | name→B52, exposure_type→C52, drawn→D52, undrawn→E52, contingent→F52 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=48 | rank 48 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 48 ranked counterparties |
| 52 | 48. | security_value→H52, security_type→I52 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 52 | 48. | remarks→J52 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 53 | 49. | name→B53, exposure_type→C53, drawn→D53, undrawn→E53, contingent→F53 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=49 | rank 49 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 49 ranked counterparties |
| 53 | 49. | security_value→H53, security_type→I53 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 53 | 49. | remarks→J53 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |
| 54 | 50. | name→B54, exposure_type→C54, drawn→D54, undrawn→E54, contingent→F54 | mapped | `bsd3.rank` kind=non_monetary_exposure; rank=50 | rank 50 non monetary exposure — canonical positions aggregated per counterparty / connected group (LE-MONTHLY slice); blank when the bank has fewer than 50 ranked counterparties |
| 54 | 50. | security_value→H54, security_type→I54 | input_required |  | value / type of security — the Guide asks for the ORIGINAL valuation at grant and the asset charged (fixed charge, floating charge, guarantee); the canonical model holds only the CRM-eligible collateral value (crm_collateral_ghs), a different figure — bank must supply |
| 54 | 50. | remarks→J54 | input_required |  | remarks — free text; bank elaborates on items not clear from the figures |

Template formulas on this sheet (50 cells; evaluated by the engine, never bound): G5 `=D5+E5+F5`.

**Totals:** 597 official cells bound — 405 mapped (`bsd3.rank` 400 · `bsd3.count` 1 · `constant` 4), 192 input_required (remarks 80 · value/type of security 110 · accrued interest 2). Every row total, the Sheet-1 total, the total-including-accrued and the percentage are the template's own formulas over these inputs. Unmapped cells: 0.

## 4. Residual unmapped lines — data the bank must supply

| Where | What | Why the platform cannot supply it |
|---|---|---|
| Sheet 1 `E27`, `F27` | Accrued interest on the twenty largest depositors (item 22), foreign / cedi | no accruals sub-ledger in the canonical model (BSD2 has the same gap) |
| Sheet 1 `H6:H25`, Sheet 2 `I6:I15`, Sheet 3 `J5:J54` | Remarks | free text (Guide item 5) |
| Sheet 2 `H6:H15`; Sheet 3 `H5:H54`, `I5:I54` | Value of security (original valuation) and type of security | not in the canonical model — `crm_collateral_ghs` is the CRM-eligible value, not the original valuation; no charge-type register |
| Sheet 1–3 ranks beyond the population | (nothing) — blank by construction | rows exist only for the entities the book has |

Data quality prerequisites for a full roster: DEPOSIT / LOAN / … positions ingested **with counterparty references** (`counterparty_reference`; pooled positions cannot rank), `group_reference` on connected counterparties, `deposit_account_type`, `contractual_maturity`, and `balance_ghs` (else `notional_ghs`) on foreign-currency positions.

## 5. Critical totals proven by `tests/services/bog_forms/test_bsd3.py`

Generated through `POST /api/v1/banks/{bank}/regulatory-packages` on the hermetic book plus an inserted canonical slice (9 counterparties incl. a connected group, a pooled counterparty-less deposit, a prior-month snapshot; distinct balances):

1. every official cell of the three sheets is declared (`status_counts.unmapped == 0`, no engine errors); the mapped / input_required split per sheet is 105/22 · 50/20 · 250/150;
2. rank ordering on every sheet: rank 1 ≥ rank 2 ≥ … (blank rows evaluate to 0 in BoG's row-total formulas and sit last);
3. `G26 = SUM(G6:G25)` = Σ listed depositor amounts (22.7m) and every `G = E+F` / `F = D+E` / `G = D+E+F` row total; `G28 = G26+G27`, `G29 = G26/G28`;
4. reconciliation to positions: Σ ranked = Σ counterparty-attributed DEPOSIT balances (pooled deposit excluded), Σ monetary and Σ non-monetary exposures independently recomputed from the canonical rows; the prior-month snapshot never ranks; the FX/cedi split (USD fixed deposit → `E`, cedi current → `F`); the connected group aggregates under its `group_reference`; the depositor count = 5;
5. the values-only xlsx: names in `B`, ¢'Million-scaled amounts, `G30` unscaled, `A27:A30` = 22–25, blank ranks blank;
6. the resolvers unit-tested against the rows (field defaults to the bound column, explicit `field` override, `None` beyond the population, unknown field / kind raise).

## 6. Cross-form dependencies

- No cell-level link (the official workbook has no external references). Catalogue dependency BSD2 is a **reconciliation** relationship: Sheet 1 `G26` ≤ BSD2 total deposits (a subset — the twenty largest, counterparty-attributed); Sheet 2 + Sheet 3 exposures ⊂ BSD2 loans, placements, investments and contingents. Both are checked in review, not by formula (the Guide gives no BSD3↔BSD2 tie-out).
- LE-MONTHLY (`le_generation.generate_large_exposures`) and LMT (funding concentration, top-20 depositors) read the SAME slice and grouping — BSD3A's roster is that engine's population re-cut by the Guide's three populations.
- BSD3B ← BSD3A (`bsd3b_line_map.md`).

## 7. Framework asks

1. **A "blank by construction" line status.** Roster rows beyond the bank's population (rank 15 of 20 when the bank has 12 depositors) are correctly blank but can only be reported as `input_required`; a `not_applicable`/`blank` `LineStatus` (or a resolver-signalled "no such row") would keep the Completion-notes sheet honest and short. Today: 80–150 spurious notes per BSD3 run on a small book.
2. **Promote the LE canonical slice.** `sources_ext/bsd3.py` imports `le_generation._load_canonical_rows` / `_entity_identity` (private names, lazily to avoid the `engine → sources_ext → le_generation → generation → bog_forms.generation → engine` cycle). A public `canonical_slice` module (rows + connected-counterparty identity) shared by LE / LMT / BoG forms would remove both smells.
3. **Row labels for grids.** `grid_lines` labels a roster row by its left-most label (`"1."`, `"2."`); an optional label template (`"rank {n} depositor"`) would make the Completion notes self-explanatory.
4. **BSD3B per subsidiary** — see `bsd3b_line_map.md` (subsidiary register + one workbook per subsidiary).

## 8. Regenerating the row tables

Run from `backend/` (`PYTHONPATH=. uv run python gen.py BSD3A`; `BSD3B` for the group form) and paste the output into §3:

```python
"""Generate the row-by-row tables for docs/bog_returns/bsd3{a,b}_line_map.md
from the line maps + layouts (run from backend/ with `uv run python`)."""

from __future__ import annotations

import re
import sys
from collections import defaultdict

from app.services.regulatory_reporting.bog_forms.layout import load_layout
from app.services.regulatory_reporting.bog_forms.linemaps import line_maps_for

form = sys.argv[1]
layout = load_layout(form)
maps = line_maps_for(form)


def status(line) -> str:
    return "mapped" if line.source else "input_required"


def source(line) -> str:
    if not line.source:
        return ""
    params = "; ".join(f"{k}={v}" for k, v in line.params.items())
    return f"`{line.source}` {params}".strip()


for sheet in layout.sheets:
    lines = maps.get(sheet.name, ())
    n_cells = sum(len(ln.cells) for ln in lines)
    n_mapped = sum(len(ln.cells) for ln in lines if ln.source)
    print(
        f"\n### Sheet `{sheet.name}` — {n_cells} cells bound "
        f"({n_mapped} mapped · {n_cells - n_mapped} input_required) · "
        f"{len(sheet.formula_cells)} template formulas · "
        f"{len(sheet.input_cells)} numeric cells captured by the extractor\n"
    )
    print("| Row | Official label | Cells (column → cell) | Status | Source (resolver → params) | Note |")
    print("|---|---|---|---|---|---|")
    by_row: dict[int, list] = defaultdict(list)
    for ln in lines:
        row = int(next(iter(ln.cells.values()))[1:].lstrip("ABCDEFGHIJ"))
        by_row[row].append(ln)
    for row in sorted(by_row):
        for ln in by_row[row]:
            cells = ", ".join(f"{k}→{ref}" for k, ref in ln.cells.items())
            label = (ln.label or "").replace("|", "/")
            note = ln.notes.replace("|", "/")
            print(f"| {row} | {label} | {cells} | {status(ln)} | {source(ln)} | {note} |")
    shapes: dict[str, str] = {}
    for c in sorted(sheet.formula_cells, key=lambda c: (c.row, c.col)):
        shape = re.sub(r"(?<=[A-Z])\d+", "n", c.formula or "")
        shapes.setdefault(shape, f"{c.ref} `{c.formula}`")
    print(
        f"\nTemplate formulas on this sheet ({len(sheet.formula_cells)} cells; evaluated by the "
        "engine, never bound): " + "; ".join(shapes.values()) + "."
    )
```
