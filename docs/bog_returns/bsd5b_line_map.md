# BSD5B — Consolidated Capital Adequacy Return (group): line / cell map

**Official workbook:** `FORM BSD5B REVISED.xls` · **Frequency:** quarterly (as at each quarter end) · **Time limit:** 14 days · **Basis:** **consolidated** (Guide General Notes §1 + the template title "GROUP CAPITAL ADEQUACY RATIOS") · **Unit:** ¢'Million · **Depends on:** BSD5A (computed first for the same reporting date; every shared line links to its cell).

**Sheets (2, official order):** `CAR FORMAT-GROUP` (item No column B · amount column D; 94 captured input cells + **4 official data cells the template leaves blank** — D8 Paid-up Ordinary Share Capital, D24 Undisclosed Reserves, D71 50% of NOP, D72 3-yr average gross income — bound through `grid_lines`; 14 BoG formulas) · `Sheet2` (empty placeholder — no cells, reproduced so the exported workbook's sheet set matches the official file).

Generated from `bog_forms/linemaps/bsd5b.py` + `layouts/BSD5B.json` (do not hand-edit the table; regenerate). Resolvers: `bog_forms/sources_ext/bsd5.py` (shared with BSD5A). Tests: `backend/tests/services/bog_forms/test_bsd5.py`.

## Basis and how the group form is fed

The platform holds **no subsidiary book** (consolidation exists only on BSD7B/BSD9 in the Guide, and no engine consolidates entities). Therefore:

- every line that also exists on the solo return is `form.cell` → the computed **BSD5A `CAR FORMAT`** cell of the same reporting date: **the group figure equals the solo figure until subsidiary consolidation data exists**, and the note on every such line says so (a BSD5A input_required cell propagates as input_required here);
- lines that exist **only on consolidation** — row 3 Minority Interests, row 18 Minority Interests in Tier 2 Preferred Shares — read the **`subsidiaries` reference dataset** (data-gap closure 2026-08-16, see below): `refs.sum` of the group's own minority-interest workings over fully consolidated subsidiaries; blank until the register is ingested;
- lines the group form splits **more finely** than the solo form bind their own `capital_component` names directly (Capitalised Revaluation Reserves — Tier 1 deduction row 10 and Tier 2 add-back row 13; Revaluation Reserves row 14 = fixed-asset names only; Latent Revaluation Reserves row 15; Cumulative Preference Shares row 17; Hybrid Capital row 19 = hybrids excluding cumulative prefs) so the group Tier 2 total equals BSD5A's Tier 2 (E24) — the test proves `D29 = E24`.

**Template quirks reproduced, never corrected** (formula cells are BoG's): asset-side rows 29–33 are formulas that COPY the Tier 1 deductions (`D46=D14`, `D47=D16`, `D48=D17`, `D49=D19`) and `D50` "80% of claims on Discount Houses" is `=D20` — **Net Tier 1 Capital** (a template error: the group asset base subtracts Net Tier 1); rows 50–51 (50% of NOP, 100% of 3-yr average gross income) have **no data cell** and `D73 =D55+D65-D67-D68` does not add them — their values are still emitted at D71/D72 for completeness of the official structure; `D74 =D30/D73%` is the ratio in percent (BSD5A's E70 is a fraction) and `D75 =D30-(D73*10%)` tests at 10% (BSD5A tests at 6%). An unlabeled cell `E15` carries `0` in the official workbook (column E has no header) — reproduced as `constant 0`, not a BoG line.

## Guide rules applied

Same as BSD5A (Explanatory Notes + Composition of Capital), plus the group items: 1.iii Minority Interest = "that part of the net result of operations and net assets of the subsidiary attributable to interests not owned … by the bank" (row 3, and Tier 2 preferred-share minorities row 18); Investments in Subsidiaries (row 8) — on consolidation the investment in a CONSOLIDATED subsidiary is eliminated, only unconsolidated holdings remain deducted (the solo figure links until then). "Only cleared balances with BoG"; own books only; the four risk-weight notes — as on BSD5A.

## Sheet `CAR FORMAT-GROUP` — 94 captured input cells · 14 template formulas · 98 cells declared

### Column `amount` — 43 cells

| Row | Cell | Official line | Status | Source (resolver → filters) | Note |
|---|---|---|---|---|---|
| 8 | D8 | Paid-up  Ordinary Share Capital | mapped | `form.cell` BSD5A!`CAR FORMAT`!E7 | solo figure from BSD5A (the platform holds no subsidiary book — add the paid-up ordinary share capital of consolidated subsidiaries when a group book exists) |
| 9 | D9 | Disclosed Reserves | mapped | `form.cell` BSD5A!`CAR FORMAT`!E8 | solo figure from BSD5A (the platform holds no subsidiary book — add the disclosed reserves of consolidated subsidiaries when a group book exists) |
| 10 | D10 | Minority Interests | mapped | `refs.sum` kind=subsidiaries; value_field=minority_interest_ghs; filters={consolidation_method: full} | Σ minority_interest_ghs over fully consolidated subsidiaries in the subsidiaries register (latest reporting date on/before the period end): minority interests (Guide 1.iii) — the group's own consolidation workings: the non-controlling share of each FULLY consolidated subsidiary's equity; blank until the register is ingested |
| 11 | D11 | Paid-up Permanent Non-Cumulative Preference Shares | mapped | `form.cell` BSD5A!`CAR FORMAT`!E9 | solo figure from BSD5A (the platform holds no subsidiary book — add the permanent non-cumulative preference shares of consolidated subsidiaries when a group book exists) |
| 14 | D14 | Goodwill/Intangibles | mapped | `form.cell` BSD5A!`CAR FORMAT`!E12 | solo figure from BSD5A (the platform holds no subsidiary book — add the goodwill/intangibles of consolidated subsidiaries when a group book exists) |
| 15 | D15 | Losses not Provided For | mapped | `form.cell` BSD5A!`CAR FORMAT`!E13 | solo figure from BSD5A (the platform holds no subsidiary book — add the losses not provided for of consolidated subsidiaries when a group book exists) |
| 16 | D16 | Investments in  Subsidiaries | mapped | `form.cell` BSD5A!`CAR FORMAT`!E14 | solo figure from BSD5A (the platform holds no subsidiary book — add the investments in subsidiaries of consolidated subsidiaries when a group book exists) — on consolidation the investment in a CONSOLIDATED subsidiary is eliminated; only unconsolidated holdings remain deducted |
| 17 | D17 | Invest in the Capital of Other Banks & Fin. Institutions | mapped | `form.cell` BSD5A!`CAR FORMAT`!E15 | solo figure from BSD5A (the platform holds no subsidiary book — add the investments in the capital of other banks and financial institutions of consolidated subsidiaries when a group book exists) |
| 18 | D18 | Capitalised Revaluation Reserves | mapped | `bsd5.capital_facts` categories=capitalised_revaluation_reserve, capitalised_revaluation_reserves, capitalized_revaluation_reserve, capitalized_revaluation_reserves; deduction=True | revaluation reserves capitalised into share capital — deducted from Tier 1 here and added back in Tier 2 (row 13) |
| 19 | D19 | Connected Lending of Long Term Nature | mapped | `form.cell` BSD5A!`CAR FORMAT`!E16 | solo figure from BSD5A (the platform holds no subsidiary book — add the connected lending of a long-term nature of consolidated subsidiaries when a group book exists) |
| 21 | D21 | Capitalised Revaluation Reserves | mapped | `bsd5.capital_facts` categories=capitalised_revaluation_reserve, capitalised_revaluation_reserves, capitalized_revaluation_reserve, capitalized_revaluation_reserves | the capitalised revaluation reserves deducted at row 10, admitted in Tier 2 |
| 22 | D22 | Revaluation Reserves | mapped | `bsd5.capital_facts` categories=revaluation_reserve, revaluation_reserves, fixed_asset_revaluation_reserve, property_revaluation_reserve | fixed-asset (premises) revaluation reserves |
| 23 | D23 | Latent Revaluation Reserves | mapped | `bsd5.capital_facts` categories=latent_revaluation_reserve, latent_revaluation_reserves, unrealised_revaluation_reserve | latent revaluation of long-term equity securities carried at historical cost |
| 24 | D24 | Undisclosed Reserves | mapped | `form.cell` BSD5A!`CAR FORMAT`!E20 | solo figure from BSD5A (the platform holds no subsidiary book — add the undisclosed reserves (current-year profit/loss) of consolidated subsidiaries when a group book exists) |
| 25 | D25 | Cumulative Preference Shares | mapped | `bsd5.capital_facts` categories=cumulative_preference_shares, cumulative_preference_share_capital | cumulative preference shares (Guide 2.v) |
| 26 | D26 | Minority Interests in Tier 2 Prefered Shares | mapped | `refs.sum` kind=subsidiaries; value_field=minority_interest_tier2_pref_ghs; filters={consolidation_method: full} | Σ minority_interest_tier2_pref_ghs over fully consolidated subsidiaries in the subsidiaries register (latest reporting date on/before the period end): minority interests in Tier 2 preferred shares (Guide 2) — the non-controlling share of each FULLY consolidated subsidiary's qualifying preferred shares, per the group's own workings (nil for most groups); blank until the register is ingested |
| 27 | D27 | Hybrid Capital | mapped | `bsd5.capital_facts` categories=hybrid_capital, hybrid_instruments, hybrid_capital_instruments, hybrid_debt_equity_instruments | hybrid debt/equity instruments (cumulative preference shares are on row 17) |
| 28 | D28 | Subordinated Term Debt (Limited to 50% of 5) | mapped | `form.cell` BSD5A!`CAR FORMAT`!E22 | solo figure from BSD5A (the platform holds no subsidiary book — add the subordinated term debt of consolidated subsidiaries when a group book exists) |
| 32 | D32 | TOTAL ASSETS (less Contra Items) | mapped | `form.cell` BSD5A!`CAR FORMAT`!E27 | solo figure from BSD5A (the platform holds no subsidiary book — add the total assets of consolidated subsidiaries when a group book exists) |
| 34 | D34 | Cash on Hand (Cedis) | mapped | `form.cell` BSD5A!`CAR FORMAT`!E29 | solo figure from BSD5A (the platform holds no subsidiary book — add the cedi cash on hand of consolidated subsidiaries when a group book exists) |
| 35 | D35 | Cash on Hand (Forex) | mapped | `form.cell` BSD5A!`CAR FORMAT`!E30 | solo figure from BSD5A (the platform holds no subsidiary book — add the forex cash on hand of consolidated subsidiaries when a group book exists) |
| 37 | D37 | i.   Cedi Clearing Account Balance | mapped | `form.cell` BSD5A!`CAR FORMAT`!E32 | solo figure from BSD5A (the platform holds no subsidiary book — add the cedi clearing account balance with BoG of consolidated subsidiaries when a group book exists) |
| 38 | D38 | ii.   Forex Account Balance | mapped | `form.cell` BSD5A!`CAR FORMAT`!E33 | solo figure from BSD5A (the platform holds no subsidiary book — add the forex account balance with BoG of consolidated subsidiaries when a group book exists) |
| 39 | D39 | iii.  Funds under SWAPS | mapped | `form.cell` BSD5A!`CAR FORMAT`!E34 | solo figure from BSD5A (the platform holds no subsidiary book — add the funds under swaps with BoG of consolidated subsidiaries when a group book exists) |
| 40 | D40 | iv.  Bills and Bonds | mapped | `form.cell` BSD5A!`CAR FORMAT`!E35 | solo figure from BSD5A (the platform holds no subsidiary book — add the BoG bills and bonds of consolidated subsidiaries when a group book exists) |
| 43 | D43 | i)  Treasury Securities (Bills and Bonds) | mapped | `form.cell` BSD5A!`CAR FORMAT`!E39 | solo figure from BSD5A (the platform holds no subsidiary book — add the treasury securities of consolidated subsidiaries when a group book exists) |
| 44 | D44 | ii)  Stocks | mapped | `form.cell` BSD5A!`CAR FORMAT`!E40 | solo figure from BSD5A (the platform holds no subsidiary book — add the government stocks of consolidated subsidiaries when a group book exists) |
| 45 | D45 | 80% of Cheques drawn on other banks | mapped | `form.cell` BSD5A!`CAR FORMAT`!E41 | solo figure from BSD5A (the platform holds no subsidiary book — add the 80% of cheques drawn on other banks of consolidated subsidiaries when a group book exists) |
| 51 | D51 | 80% of claims on Other Banks (Cedis / Forex) | mapped | `form.cell` BSD5A!`CAR FORMAT`!E47 | solo figure from BSD5A (the platform holds no subsidiary book — add the 80% of claims on other banks of consolidated subsidiaries when a group book exists) |
| 52 | D52 | 50% of Residential Mortgage Loans | mapped | `form.cell` BSD5A!`CAR FORMAT`!E51 | solo figure from BSD5A (the platform holds no subsidiary book — add the 50% of residential mortgage loans of consolidated subsidiaries when a group book exists) |
| 53 | D53 | 50% of Export Financing Loans | mapped | `form.cell` BSD5A!`CAR FORMAT`!E52 | solo figure from BSD5A (the platform holds no subsidiary book — add the 50% of export financing loans of consolidated subsidiaries when a group book exists) |
| 54 | D54 | 80% of loans guaranteed by multilateral banks | mapped | `form.cell` BSD5A!`CAR FORMAT`!E50 | solo figure from BSD5A (the platform holds no subsidiary book — add the 80% of loans guaranteed by multilateral banks of consolidated subsidiaries when a group book exists) |
| 58 | D58 | Commercial Letters of Credit Outstanding | mapped | `form.cell` BSD5A!`CAR FORMAT`!E55 | solo figure from BSD5A (the platform holds no subsidiary book — add the commercial letters of credit outstanding of consolidated subsidiaries when a group book exists) |
| 59 | D59 | Guarantees / Indemnities | mapped | `form.cell` BSD5A!`CAR FORMAT`!E56 | solo figure from BSD5A (the platform holds no subsidiary book — add the guarantees / indemnities of consolidated subsidiaries when a group book exists) |
| 60 | D60 | Acceptances | mapped | `form.cell` BSD5A!`CAR FORMAT`!E57 | solo figure from BSD5A (the platform holds no subsidiary book — add the acceptances of consolidated subsidiaries when a group book exists) |
| 61 | D61 | Endorsements | mapped | `form.cell` BSD5A!`CAR FORMAT`!E58 | solo figure from BSD5A (the platform holds no subsidiary book — add the endorsements of consolidated subsidiaries when a group book exists) |
| 62 | D62 | Revolving Underwriting Facilities | mapped | `form.cell` BSD5A!`CAR FORMAT`!E59 | solo figure from BSD5A (the platform holds no subsidiary book — add the revolving underwriting facilities of consolidated subsidiaries when a group book exists) |
| 63 | D63 | Note Issuance Facilities | mapped | `form.cell` BSD5A!`CAR FORMAT`!E60 | solo figure from BSD5A (the platform holds no subsidiary book — add the note issuance facilities of consolidated subsidiaries when a group book exists) |
| 64 | D64 | Standby Letters of Credit to Other Banks | mapped | `form.cell` BSD5A!`CAR FORMAT`!E61 | solo figure from BSD5A (the platform holds no subsidiary book — add the standby letters of credit to other banks of consolidated subsidiaries when a group book exists) |
| 67 | D67 | 50% of class 1 risk weighted off-balance sheet items | mapped | `form.cell` BSD5A!`CAR FORMAT`!E63 | solo figure from BSD5A (the platform holds no subsidiary book — add the 50% of class-1 risk-weighted off-balance-sheet items of consolidated subsidiaries when a group book exists) |
| 68 | D68 | 80% of class 2 risk weighted off-balance sheet items | mapped | `form.cell` BSD5A!`CAR FORMAT`!E64 | solo figure from BSD5A (the platform holds no subsidiary book — add the 80% of class-2 risk-weighted off-balance-sheet items of consolidated subsidiaries when a group book exists) |
| 71 | D71 | 50% of NOP | mapped | `form.cell` BSD5A!`CAR FORMAT`!E67 | solo figure from BSD5A (the platform holds no subsidiary book — add the 50% of NOP of consolidated subsidiaries when a group book exists) — informational: BoG's D73 formula omits this row |
| 72 | D72 | 100% of 3yrs Average Annual Gross Income | mapped | `form.cell` BSD5A!`CAR FORMAT`!E68 | solo figure from BSD5A (the platform holds no subsidiary book — add the 100% of the 3-year average annual gross income of consolidated subsidiaries when a group book exists) — informational: BoG's D73 formula omits this row |

**amount:** 43 cells — 41 mapped, 2 input_required.

### Column `no` — 54 cells, all `constant` = the template's printed value (unscaled): B8=1, B9=2, B10=3, B11=4, B12=5, B14=6, B15=7, B16=8, B17=9, B18=10, B19=11, B20=12, B21=13, B22=14, B23=15, B24=16, B25=17, B26=18, B27=19, B28=20, B29=21, B30=22, B32=23, B34=24, B35=25, B36=26, B42=27, B45=28, B46=29, B47=30, B48=31, B49=32, B50=33, B51=34, B52=35, B53=36, B54=37, B55=38, B58=39, B59=40, B60=41, B61=42, B62=43, B63=44, B64=45, B65=46, B67=47, B68=48, B69=49, B71=50, B72=51, B73=52, B74=53, B75=54

### Column `stray` — 1 cells, all `constant` = the template's printed value (unscaled): E15=0

Template formulas (BoG's, evaluated — never bound): `D12` `=SUM(D8:D11)`; `D20` `=D12-SUM(D14:D19)`; `D29` `=SUM(D21:D28)`; `D30` `=D20+D29`; `D46` `=D14`; `D47` `=D16`; `D48` `=D17`; `D49` `=D19`; `D50` `=D20`; `D55` `=D32-SUM(D34:D54)`; `D65` `=SUM(D58:D64)`; `D73` `=D55+D65-D67-D68`; `D74` `=D30/D73%`; `D75` `=D30-(D73*10%)`

**CAR FORMAT-GROUP totals:** 98 cells declared (94 captured + 4 blank data cells) — by declaration 98 bound / 0 input_required (rows 3, 18 read the `subsidiaries` register since 2026-08-16 and are blank at runtime until it is ingested); at runtime the 12 links to BSD5A input_required cells (D35, D38, D44, D53, D54, D60–D64, D67, D68) resolve blank → on the hermetic book **84 mapped / 14 input_required / 0 unmapped**. Values (¢'Million, after a capital baseline run): D8 150.0 · D9 150.0 · D11 20.0 · D12 320.0 · D14 25.0 · D20 295.0 · D28 45.0 · D29 45.0 · **D30 340.0** (= BSD5A E25) · D32 2,400.0 · D50 295.0 (BoG's `=D20`) · D55 1,070.0 · D65 320.0 · **D73 1,390.0** · D74 24.46 · D75 201.0.

## Residual unmapped lines — data the bank must supply

- **Row 3 Minority Interests; row 18 Minority Interests in Tier 2 Preferred Shares** — the group's minority workings per fully consolidated subsidiary, supplied through the `subsidiaries` register (blank until pushed).
- **Every consolidated adjustment** — subsidiaries' capital, assets and contingents, elimination of intra-group balances and of the investment in consolidated subsidiaries (row 8): until a group book exists the linked solo figures ARE the group figures; the bank adjusts.
- **Inherited from BSD5A** — forex cash / forex BoG account (rows 25, 26.ii), Government stocks (27.ii), export financing (36), multilateral-guaranteed loans (37), acceptances / endorsements / RUF / NIF / standby LCs (41–45), class-1/2 off-balance-sheet split (47/48). Note the group form has **no** rows for claims on public-sector FIs, government-guaranteed loans, repos with BoG or special deposits, and its rows 29–33 are BoG's formula copies rather than inputs.
- **Capital components with no line** — as BSD5A (DTA-type CET1 deductions, general provisions).

## Cross-form dependencies

- `BSD5B` ← **BSD5A** (`FormSpec.depends_on = ("BSD5A",)`; `form.cell` links on 35 cells incl. the four blank data cells). BSD5A in turn depends on BSD2 and BSD8, so generating BSD5B computes BSD2 → BSD8 → BSD5A → BSD5B for the same reporting date.
- The BSD5A ↔ capital-run reconciliation (E25 = the run's mapped capital-component lines) carries over: `D30 = E25`.

## Tests

`backend/tests/services/bog_forms/test_bsd5.py::test_bsd5b_group_return_links_to_bsd5a_and_reproduces_bogs_group_formulas` (+ the resolver test's BSD5B tail): every captured input cell plus D8/D24/D71/D72 bound, `Sheet2` empty, group-only rows input_required, group = solo cell for cell (18 pairs incl. the blank cells), `D12 = D8+D9+D11 = E10`, `D20 = E17`, `D29 = E24` (finer Tier 2 rows, same total — proven with latent revaluation / cumulative preference / hybrid probe facts), **`D30 = D20+D29 = E25 = 340.0`**, the D46–D50 copies and the `D50=D20` quirk verbatim, `D55`, `D65 = E54`, `D73` excludes rows 50–51, `D74 = D30/D73×100`, `D75 = D30 − 10%×D73`, `E15 = 0`.

## Framework asks

- `D74` (`=D30/D73%`) is a percent formula cell on a ¢'Million sheet — scaled on export like BSD5A E70 / BSD8 C56 (same ask: per-cell unit override for formula cells).
- Consolidation of the solo-linked lines: the `subsidiaries` register now carries each subsidiary's `total_assets_ghs`, `tier1_capital_ghs`, `rwa_ghs` and `investment_carrying_ghs`, but adding them to a `form.cell` figure (and eliminating the investment in a consolidated subsidiary at row 8) needs a **combining resolver** (`form.cell` + `refs.sum` in one declaration, e.g. `sum_of: [{source, params}, …]`) — until then the linked solo figures ARE the group figures and the bank adjusts.

## Data-gap closure — minority interests (2026-08-16)

Rows 3 (`D10`) and 18 (`D26`) — the two consolidation-only lines — now read the **`subsidiaries`** reference dataset (the subsidiary register + book, one row per subsidiary per reporting date; `docs/data_engine/datasets/subsidiaries.md`; schema `reference_schemas/subsidiaries.py`; ingested through the Data Engine, `docs/API_INTEGRATION.md` §3.5). Binding: `linemaps/bsd5b.py::minority(field, …)` → `refs.sum {kind: subsidiaries, value_field: minority_interest_ghs | minority_interest_tier2_pref_ghs, filters: {consolidation_method: full}}` over the latest register on/before the reporting date — the figures are the group's own consolidation workings (the non-controlling share of a fully consolidated subsidiary's equity / qualifying Tier 2 preferred shares), never derived by the platform. Blank until the register is ingested; `0` when it exists and no fully consolidated subsidiary carries the field. BSD9 row 30 reads the same `minority_interest_ghs` (Domestic/Foreign by functional currency). Proof: `tests/services/data_gaps/test_subsidiaries.py`. Sample Bank onboarding data: `backend/onboarding/sample_bank/subsidiaries.csv`.

## Regenerating the table

```python
from app.services.regulatory_reporting.bog_forms.linemaps import line_maps_for
from app.services.regulatory_reporting.bog_forms.layout import load_layout
maps, layout = line_maps_for("BSD5B"), load_layout("BSD5B")
for line in maps["CAR FORMAT-GROUP"]:      # one row per LineSpec: code · label · cells · source · params · notes · unscaled
    print(line.code, line.label, line.cells, line.source, line.params, line.notes, line.unscaled)
```
