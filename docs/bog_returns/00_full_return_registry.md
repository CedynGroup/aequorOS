# BoG Prudential Returns — Full Return Registry (Phase 1)

**Scope rule (non-negotiable):** every official template under `docs/reporting/` is in
scope for (1) line/cell map, (2) calculation/aggregation, (3) template-faithful export,
(4) tests, (5) reporting governance. No form is deferred; no annex is dropped.

**Sources of truth.** Layout/labels/units/roll-ups/annexes: the Excel templates in
`docs/reporting/` + `GUIDE FOR REPORTING INSTITUTIONS -.doc` (the "Guide"). Behaviour: the
live codebase (`backend/app/services/regulatory_reporting/`); `docs/product.md` orders the
build; **code wins on conflict.**

Discovery inventory produced 2026-08-15 by converting every `.xls` (LibreOffice headless,
formulas preserved) and extracting every cell: **24 workbooks · 76 sheets (72 populated + 4
empty placeholder `Sheet2/3` tabs) · 5,903 formula cells · 4,869 numeric input cells.**
Formula vocabulary across the entire set: `SUM` (2,368), `IF` (8), and `+ − × ÷` — nothing
else. The build therefore **evaluates the templates' own formulas** over mapped input cells
(roll-ups are BoG's by construction; AequorOS never invents a line).

---

## 1. Guide rules that apply to ALL forms

| Rule (Guide §) | Effect on the build |
|---|---|
| **Branch coverage** — returns cover all branches/agencies in Ghana **and overseas**, EXCEPT **BSD1** (Liquidity Reserve) which covers **Ghana branches only**. | BSD1 generator filters positions to `branch_country == GH`; all others take the whole book. |
| **Consolidation** — subsidiaries' liabilities/assets/results are consolidated **only on BSD7B and BSD9** (and, per template titles, the *GROUP* variants BSD3B / BSD5B / BSD6-group notes). | `basis = "consolidated"` only for BSD7B, BSD9, BSD3B, BSD5B; every other form is **solo** and must EXCLUDE subsidiary books. |
| **Fiduciary / agent funds** — only transactions on the institution's own books (as principal). | Exclude `role == fiduciary/agent` positions everywhere. |
| **Syndications** — as (co-)manager, other participants' deposits are not deposit liabilities and their loan shares are not assets. | Exclude participant shares from BSD2/4/6/8. |
| **Spot vs forward** — only spot balances after the day's entries; unmatured spot/forward excluded **except BSD6B (Annex 1)**. | Forward legs feed only BSD6B annex + BSD13/1B (NOP). |
| **Domestic vs Foreign** — Domestic = expressed *and payable* in cedis; Foreign = payable in a foreign currency (reported converted to cedis). | Every Domestic/Foreign/Total column split keys on `settlement_currency == GHS`, NOT on counterparty residence. |
| **Set-off** — netting only with formal agreement / legal right, same currency, same customer/group, Ghanaian resident; overseas balances gross. | Report gross unless a `netting_agreement` flag is present. |
| **Units** — "Enter amounts to nearest million, omitting 000,000" (¢'Million) on BSD1, BSD2, BSD6 …; other forms are unit-specific (see per-form map). | Exporter scales per template unit; the ¢'Million convention is preserved in the header. |
| **Time limits** — Weekly returns 9 days; monthly/quarterly/half-yearly 14 days after reporting date. | Registry `deadline_rule` per form (below). |

---

## 2. The registry — every template file, no omissions

**As-built (2026-08-15, end of Wave 4):** every row below is now **Implemented** — line map bound (0 unmapped official cells on any form), template-faithful export, tests, governance. The live per-form state (mapped vs input_required counts) is generated in `99_coverage_matrix.md`; per-form line/cell maps are `<form>_line_map.md`. The Status column below is preserved as the *pre-build* baseline for the record.

Legend — Status is the state **before this build** (2026-08-15): `Missing` = no code;
`Partial` = related engine exists but no BoG-form generator/exporter; `Implemented` = form
generates + exports template-faithfully with tests. Existing engine seams are named so each
form is fed from upstream where the platform already computes the figure.

| # | Form | Template file(s) | Sheets / annexes (official) | Guide frequency · limit | Basis | Depends on | Existing engine seam | Status (pre-build) |
|---|---|---|---|---|---|---|---|---|
| 1 | **BSD1** | `FORM BSD1 REVISED.xls` | `BSD1` (95×256; 2,844 formulas), `BSD1-Annex1`, `BSD1-Annex2` (←BSD1), `BSD1-Annex3` | **Weekly · 9 days** | Solo, **Ghana branches only** | — | liquidity engine (HQLA/reserves), canonical balances | Missing |
| 2 | **BSD1A** | `FORM BSD1A.xls` | `20 LARGEST WITHDRAWALS` | Weekly · 9 days | Solo | — | canonical cash-flow / teller withdrawals (input required) | Missing |
| 3 | **BSD1B** | `FORM BSD1B.XLS` | `FORM FXP`, `AFOP`, `SCHEDULE B` (daily NOP) | Weekly · 9 days | Solo | — | FX engine (`regulatory_fx`, DBK-DAILY NOP) | Partial (NOP computed; no BSD1B layout) |
| 4 | **BSD2** | `FORM BSD2 REVISED.xls` | `BSD2` (283-row A&L spine, Domestic/Foreign/Total, ▲ roll-ups), `BSD2-Summary` (←BSD2), `BSD2-Annex 1`, `2a`, `2b`, `2c`, `2d`, `3`, `4`, `5`, `6`, `7`, `8`, `9`, `10`, `11`, `12`, `13`, `14`, `15`, `16`, `17` — **19 sheets** | **Monthly · 14 days** | Solo | — (spine other returns reference) | canonical balance sheet (`canonical_positions`, `bank_facts` balance_sheet group), capital, provisions | Missing (a `BSD-MONTHLY` *template_pending* placeholder exists) |
| 5 | **BSD2A** | `FORM BSD2A REVISED.xls` | `FOREIGN CURRENCY EXPOSURES` | Monthly · 14 days | Solo | **BSD2** foreign column | FX engine + canonical FX positions by currency | Partial |
| 6 | **BSD3A** | `FORM BSD3A REVISED.xls` | `BSD3-Sheet-1` (20 largest depositors), `Sheet-2` (10 largest monetary-sector exposures), `Sheet-3` (50 largest non-monetary exposures) | Monthly · 14 days | Solo | BSD2 totals | Large-exposures engine (`LE-MONTHLY`), funding-concentration (top depositors) | Partial |
| 7 | **BSD3B** | `FORM BSD3B - REVISED GROUP.xls` | same 3 sheets, **per subsidiary** | Monthly · 14 days | **Group** (per subsidiary) | BSD3A | as BSD3A + subsidiary register | Missing |
| 8 | **BSD4** | `FORM BSD4 REVISED.xls` | `BSD4` (95×45; sector × instrument grid, 1,498 formulas), `4a Annexure`, `4b Annexure` | Monthly · 14 days | Solo | BSD2 §8 loans total | sector classification on canonical loans (`sector`, `product`, currency) | Missing |
| 9 | **BSD5A** | `FORM BSD5A REVISED.xls` | `CAR FORMAT`, `NEW RISK WEIGHTS`, `PROVISION` | Monthly · 14 days | Solo | BSD2 (capital lines), BSD8 (provisions) | **capital engine** (`regulatory_capital`: CAR, RWA, Tier 1/2) | Partial (engine real; layout is the legacy `CAR-RWA` reconstruction) |
| 10 | **BSD5B** | `FORM BSD5B REVISED.xls` | `CAR FORMAT-GROUP` (+ empty `Sheet2`) | **Quarterly** · 14 days | **Consolidated** | BSD5A | capital engine + subsidiary consolidation | Missing |
| 11 | **BSD6** | `FORM BSD6 REVISED.xls` | `BSD6A` (cedi maturity ladder; "FROM BSD2"), `BSD6B` (foreign-currency ladder; Annex 1 forwards) | Monthly · 14 days | Solo | **BSD2** line totals; forwards | liquidity ladder / contractual cash-flow window (`cashflow_window`), maturity buckets | Partial |
| 12 | **BSD7A** | `FORM BSD7A REVISED.xls` | `BSD7A` (current-year results / P&L) | Monthly · 14 days | Solo | — | P&L canonical facts (`income_statement`), FTP | Missing |
| 13 | **BSD7B** | `FORM BSD7B REVISED.xls` | `BSD7B` (consolidated results) | **Quarterly** · 14 days | **Consolidated** | BSD7A | as BSD7A + consolidation | Missing |
| 14 | **BSD8** | `FORM BSD8 REVISED.xls` | `BSD8` (adverse classification; **←BSD2**), `BSD8-Annexure` (←BSD8) | Monthly · 14 days | Solo | **BSD2** loans; classification | ECL/provisions (`capital/ecl.py`), loan classification buckets | Partial |
| 15 | **BSD9** | `FORM BSD9 REVISED.xls` | `BSD9` (consolidated balance sheet), `Annexure` (+ empty `Sheet3`) | **Quarterly** · 14 days | **Consolidated** | BSD2 | canonical balance sheet + consolidation | Missing |
| 16 | **BSD10** | `FORM BSD10 REVISED.xls` | `BSD10` (capital expenditure) | **Half-yearly** · 14 days | Solo | — | fixed-asset / capex register (input required) | Missing |
| 17 | **BSD11** | `FORM BSD11 REVISED.xls` | `BSD11-Sheet-1` … `Sheet-7`, `BSD11- Sheet 8` — **8 sheets** (statutory return) | Half-yearly · 14 days | Solo | BSD2/BSD7A figures | balance sheet + P&L + registers (input required for statutory items) | Missing |
| 18 | **BSD13** | `FORM BSD13 REVISED.xls` | `FOREX OPEN POSITION`, `SCHEDULE-A`, `SCHEDULE-B`, `SCHEDULE-C` | Monthly · 14 days | Solo | BSD2A / BSD1B | FX engine NOP (`regulatory_fx`, `FX-NOP`) | Partial (NOP computed; no BSD13 layout) |
| 19 | **BSD14** | `FORM BSD14 REVISED.xls` | `INTEREST&LENDING-RATES` | **Weekly** · 9 days | Solo | — | product rate tables / FTP base rates (input required for offered rates) | Missing |
| 20 | **BSD15A** | `FORM BSD15A REVISED.xls` | `Domestic charges of banks`, `Range of pdts i.r.o sav & cur` | Weekly · 9 days | Solo | — | tariff register (input required) | Missing |
| 21 | **BSD15B** | `FORM BSD15B REVISED.xls` | `International Banking Charges` | Weekly · 9 days | Solo | — | tariff register (input required) | Missing |
| 22 | **BSD16** | `FORM BSD16 REVISED.xls` | `MONTHLY ATM OPERATIONS` (+ empty `Sheet2`, `Sheet3`) | Monthly · 14 days | Solo | — | ATM/channel register (input required) | Missing |
| 23 | **BSD17** | `FORM BSD17 REVISED.xls` | `BSG17-SHEET 1`, `BSD17 -SHEET 2` (foreign inward remittances) | Monthly · 14 days | Solo | — | remittance data (input required; see `docs/remittance_scoping.md`) | Missing |
| — | BSD12 | *(no template file — Guide only)* | Opening/closure/relocation of branches | As necessary | — | — | `LRT-OUTLET` corporate pack already covers this event | Covered by LRT-OUTLET (event-driven) |

**Every template file in `docs/reporting/` maps to a row above.** The 4 empty placeholder
tabs (`BSD5B/Sheet2`, `BSD9/Sheet3`, `BSD16/Sheet2`, `BSD16/Sheet3`) carry no BoG lines;
they are reproduced as empty sheets in the export so the workbook's sheet set matches the
official file exactly.

---

## 3. Legacy code reconciliation (right reporting)

Before this build the registry carried two entries **mis-coded** against the official
forms, dating from before the templates were available (`fidelity=REPRESENTATIVE`):

| Legacy code | Legacy title | Official meaning of that code | Resolution |
|---|---|---|---|
| `BSD2` | Capital Adequacy Return (CAR & RWA) | Statement of Assets & Liabilities | Legacy recoded **`CAR-RWA`** (title/generator/template unchanged, still ships); the code `BSD2` is reassigned to the official form. |
| `BSD3` | Liquidity Returns (LCR & NSFR) | Large Exposures (BSD3A/3B) | Legacy recoded **`LCR-NSFR`**; `BSD3A`/`BSD3B` registered as the official forms. |

Stored `return_code` values are renamed by an idempotent data migration; the dashboard pack
cards, attestation policy defaults and e2e specs are updated in the same change. Nothing is
deleted — the CAR/RWA and LCR/NSFR reconstructions remain useful engine outputs and keep
generating; they simply stop claiming BoG form numbers they never were.

The `BSD-MONTHLY` *template_pending* placeholder ("Monthly BSD Prudential Pack") is
**retired**: it existed only because the official forms had not landed; BSD2 + BSD7A are
those forms.

---

## 4. Build architecture (shared by all 21 forms)

```
backend/app/services/regulatory_reporting/bog_forms/
  layouts/<FORM>.json    committed template layout: every sheet, every cell (label |
                         input | formula), merges, column widths, print titles —
                         regenerated by scripts/extract_bog_templates.py from
                         docs/reporting/*.xls (LibreOffice headless → openpyxl)
  catalog.py             FormSpec per form: code, workbook, sheets, unit, basis,
                         frequency, deadline, dependencies, header cells
  linemap/<form>.py      LineSpec per INPUT cell: line code, label, column
                         (domestic/foreign/total…), source resolver or
                         input_required, notes
  sources.py             named resolvers over canonical data / engine runs
                         (balance sheet, loans by sector, capital, P&L, FX, …)
  formulas.py            safe evaluator for the template formula vocabulary
                         (SUM, IF, + − × ÷, ranges, cross-sheet refs)
  engine.py              compute a form: fill inputs → evaluate the template's
                         own formulas → FormValues + per-line status
  render.py              layout-driven xlsx: rebuild official sheets from the
                         layout JSON, write header cells + values (¢'Million
                         scaling), values-only (sealed) + "Completion notes"
                         sheet listing input_required/unmapped lines
  generation.py          _generate_bog_form → snapshot for the existing package
                         pipeline (immutable run · maker-checker · lineage)
```

- **Registry:** one `ReturnDefinition` per form (`generator="bog_form"`,
  `template_id="bog-<form>-official-v1"`, frequency/deadline from the Guide, `basis`
  solo/consolidated). Weekly returns use a `weekly` frequency + `days_after(9)` rule.
- **Governance:** because each form is a registered return, it automatically participates
  in the existing package lifecycle — immutable snapshot + content hash, maker-checker
  approval, artifact versions, lineage/provenance, signing policy, submission channels.
- **Three export artifacts from one sealed run (2026-08-16):**
  `export(return_code, package_id, kind=pdf|xlsx_official|xlsx_working)` on the existing
  package-export endpoint (`POST …/regulatory-packages/{id}/export?kind=`).

  | kind | content | role |
  |---|---|---|
  | `pdf` | values only (generic sections renderer) | **the Bank of Ghana submission package** (filed via ORASS/email as the product supports) |
  | `xlsx` / `xlsx_official` | the OFFICIAL workbook layout with every cell as an evaluated NUMBER, sheets protected, run metadata + Completion notes | the immutable, hashed, maker-checked Excel twin of the PDF — governance/audit, not the filing format |
  | `xlsx_working` | the same official layout with the template's **live formulas** (SUM, Domestic+Foreign→Total, cross-sheet annex links) and inputs as values; cross-WORKBOOK links (BSD8→[1]BSD2) written as evaluated values; labelled *WORKING COPY — FOR INTERNAL REVIEW* (workbook title, print header, Completion notes) | ALM/Finance review and challenge; a distinct artifact kind that is **never filed and never signed** (`workflow` filters it out of every filing; only official BoG BSD forms have it — 409 `working_copy_unavailable` elsewhere) |

  Migration `202608160015` admits the kind. The historical sealed kind stays `xlsx`
  (`xlsx_official` is the explicit alias) so existing artifacts, signatures and the
  dashboard keep working.
- **Unmapped handling:** a line whose source data does not exist yet is emitted at its
  official cell as blank with status `input_required` (or `unmapped` if the mapping is
  bank-specific CoA), listed in the Completion-notes sheet and in the snapshot — the
  structure is never dropped.
- **Blank data grids** (templates whose data cells are empty, no `0` placeholder — BSD2A, the BSD3
  ranked rows, BSD11 registers, BSD1A, BSD8-Annexure …) are bound with `grid_lines()`; captured
  inputs with `leaf_lines()`. The renderer writes bound off-layout cells.
- **Units** are per sheet (from the sheet's own header) with per-cell overrides: a line marked
  `unscaled` (counts, percents, foreign-currency units) is never divided; formula cells get a unit
  by **unit algebra** over the template's own formula (money ÷ money → unitless, so CAR% and
  '% of total' cells export as percentages, count subtotals as counts).
- **`positions.sum`** sums the canonical cedi value (`balance_ghs`, falling back to native balance),
  compares JSON attributes typed-or-text, and supports `counterparty_types_not`.

## 5. Waves (sequence only — the assignment is complete only after Wave 4)

| Wave | Forms | Why this order |
|---|---|---|
| 1 | BSD2 (+19 sheets), BSD2A | balance-sheet spine other returns reference (BSD6/8 "FROM BSD2") |
| 2 | BSD6A/6B, BSD3A/3B, BSD4, BSD5A/5B | dependent balance-sheet / risk / capital |
| 3 | BSD7A/7B, BSD8, BSD9 | performance, adverse classification, group consolidation |
| 4 | BSD1 (+1A/1B), BSD10, BSD11, BSD13, BSD14, BSD15A/15B, BSD16, BSD17 | liquidity/weekly/statutory/tariff/channel returns |

After each wave: registry update + tests + sample package generation. After Wave 4: the
Form × (map ✓ · calc ✓ · export ✓ · test ✓ · governance ✓) matrix in
`docs/bog_returns/99_coverage_matrix.md`.
