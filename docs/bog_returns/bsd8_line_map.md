# BSD8 — Advances Subject to Adverse Classification: line / cell map

**Official workbook:** `FORM BSD8 REVISED.xls` · **Frequency:** monthly · **Time limit:** 14 days (Guide: "completed as at the end of each month … within FOURTEEN (14) CALENDAR DAYS") · **Basis:** solo · **Unit:** ¢'Million ("Enter amounts to nearest million, omitting 000,000") on both sheets · **Legal basis:** Banking Act 2004 (Act 673) s.53(1)

**Sheets (2, official order):** `BSD8` (summary: 79 input cells · 26 template formulas), `BSD8-Annexure` (50 largest adversely classified advances: 50 captured input cells + a blank 50 × 13 detail grid · 106 template formulas)

Generated from `bog_forms/linemaps/bsd8.py` + `layouts/BSD8.json` (do not hand-edit; regenerate — the table below is emitted by iterating `line_maps_for("BSD8")`, see the snippet at the end).

## Guide rules that shape this form

- **Five categories** (Guide, FORM BSD8): Current · Other loans especially mentioned (OLEM) · Substandard · Doubtful · Loss — the delinquency backstops in "Notes to BSD8" are 0–<30 · 30–<90 · 90–<180 · 180–<360 · ≥360 days, with the qualitative tests in the text (OLEM = "potentially weak … undue credit risk"; substandard = "well-defined credit weaknesses"; …).
- **Minimum provisions**: 1 % of current balances; 10 / 25 / 50 / 100 % of the **net unsecured** balance of OLEM / substandard / doubtful / loss ("net unsecured = principal outstanding less the value of readily realisable security held").
- **Security** that counts: "cash, government stocks, other readily realisable securities and liens against deposit accounts. Property and guarantees are not currently regarded as sufficient security."
- **Interest** on substandard / doubtful / loss advances is non-accrual — posted to an interest-suspense account (item 10).
- **Annexure**: "The 50 largest advances which are subject to adverse classification should be detailed on BSD 8 Annexure."
- **BSD7A cross-check** (Guide, BSD7A notes): the P&L charge for new provisions "should be the same but not less than the figure arrived at in BSD 8 item 13 total".

## Platform sources — what the classification is read from

The canonical model has **one** typed classification attribute on a LOAN snapshot: `ifrs9_stage` (1 / 2 / 3). It has **no** days-past-due, no BoG bucket, no loan-movement flows. `bsd8.bucket` (`sources_ext/bsd8.py`) therefore reads, per LOAN position (snapshot attributes overlaid on counterparty attributes):

| Priority | Attribute | Values | Bucket |
|---|---|---|---|
| 1 | `bog_classification` (documented convention read here — see *Framework asks*) | current · olem / "other loans especially mentioned" · substandard / sub-standard · doubtful · loss (case/punctuation-insensitive) | as stated |
| 2 | `ifrs9_stage` (typed column) — **proxy** at the Guide's delinquency backstops | 1 → **Current** (0–<30 d) · 2 → **OLEM** (30-day SICR backstop / watch-list ≙ "undue credit risk") · 3 → **non-performing** (≥ 90 d) | stage 3 is Substandard ∪ Doubtful ∪ Loss — the 90/180/360-day split **cannot** be stated from the stage |
| — | neither | | **unclassified** |

**Decidability rule (never guess, never silently drop):** a bucket value is emitted only when EVERY loan's membership in that bucket is decidable. An unclassified loan makes all five buckets `input_required`; a stage-3-only loan makes Substandard / Doubtful / Loss `input_required` while Current / OLEM still resolve (a stage-3 loan is definitely neither). Once the source states `bog_classification` (or the feed classifies every NPL) all five columns fill.

Other conventions read (all optional; a measure is `input_required`, never zero, when **no** loan in the book carries the attribute):

| Measure | Attribute | Feeds |
|---|---|---|
| provisions held | `ecl_provision_ghs` (the position-level allowance the capital / ECL engines consume) | item 13, annexure "Provision made" |
| interest in suspense | `interest_in_suspense_ghs` (documented here — see *Framework asks*) | item 10 |
| allowable security | `crm_collateral_ghs` where `crm_collateral_class` ∈ {CASH, SOVEREIGN_DEBT} (Guide: cash, government stocks; other "readily realisable" classes need the bank's confirmation) | item 11, annexure "Value / Type of Security" |
| OBS liability | Σ notional of `LC_GUARANTEE` + `COMMITMENT_UNDRAWN` positions of the same counterparty | annexure "Off Balance Sheet Liability" (shown once, on the customer's first row) |
| branch · sector · facility · expiry | `branch_id` · `sector` (the BSD4 convention) · product name · `contractual_maturity` | annexure text columns |

Amounts are cedi equivalents (`balance_ghs` → raw balance for base-currency loans → preferred FX spot at period end → raw balance), the BSD4 convention. Scope: own books, current generation of snapshots, latest as-of on/before the cut-off per position; `as_of=previous` cuts off the day before the reporting period starts (item 1). Column keys of the line map are the bucket names, so one declaration fills Current … Loss.

## Sheet `BSD8` — 79 input cells (13 leaf rows × Current C / Olem D / Substandard E / Doubtful F / Loss G = 65, + 14 printed item numbers in column A) · 26 template formulas (Total column H = SUM(C:G); item 8 = SUM(items 1–7); item 12 = 8 − 9 − 10 − 11; H22 = `[1]BSD2!D38+[1]BSD2!D39`)

Status legend — **mapped**: fed from platform data via the named resolver; **input_required**: bank must supply (no canonical source). The item numbers in column A were captured as numeric input cells; they are bound to the template's own printed literal (`constant`, unscaled) so the export reproduces the form.

| Row | Item | Official line | Cells | Status | Source (resolver → params) | Note |
|---|---|---|---|---|---|---|
| 7 | 1 | Previous balance (Gross) | C–G + A | mapped | `bsd8.bucket` measure=balance; as_of=previous | gross cedi balance of LOAN positions as at the previous month-end (latest snapshot on/before the day before the reporting period starts), by classification: source `bog_classification` attribute, else IFRS 9 stage proxy (1→Current, 2→Olem, 3→non-performing; the Substandard/Doubtful/Loss split of a stage-3-only book is input_required) |
| 8 | 2 | Add: New advances made during the month | C–G + A | input_required |  | new advances made during the month — disbursement flow; the canonical model holds positions/snapshots, not loan-book movements. Bank must supply. |
| 9 | 3 | Add: Interest charged (for the month) | C–G + A | input_required |  | interest charged for the month by classification — interest/accruals sub-ledger required. Bank must supply. |
| 10 | 4 | Add: Revaluation gains/loss (for the month) | C–G + A | input_required |  | revaluation gains/loss for the month on foreign-currency advances — bank must supply. |
| 11 | 5 | Less: Amount recovered | C–G + A | input_required |  | amounts recovered during the month — repayment/recovery flow; bank must supply. |
| 12 | 6 | Less: Amount written  off | C–G + A | input_required |  | amounts written off during the month — write-off register; bank must supply. |
| 13 | 7 | (+ / -) Changes in classification from previous month | C–G + A | input_required |  | (+/-) net reclassification between buckets during the month — cannot be separated from items 2–6 without the movement schedule; bank must supply. |
| 14 | 8 | Current  balance (Gross) | A (item number) | mapped | `constant` value=8 (unscaled) | template item number (printed literal); C–G / H are template formulas |
| 15 | 8 (b) | 8 (b) Of  which foreign Currency (of item 8) | C–G | mapped | `bsd8.bucket` measure=balance; currency=FX | cedi equivalent of foreign-currency LOAN positions as at the reporting date, by classification: source `bog_classification` attribute, else IFRS 9 stage proxy (1→Current, 2→Olem, 3→non-performing; the Substandard/Doubtful/Loss split of a stage-3-only book is input_required) |
| 16 | 9 | Revaluation gains (on NPL, cum. balance) | C–G + A | input_required |  | cumulative revaluation gains on non-performing FX advances — bank must supply (same gap as BSD2 line 8 'Revaluation gains on non-performing loans'). |
| 17 | 10 | Interest in suspense (cumulative balance) | C–G + A | mapped | `bsd8.bucket` measure=interest_in_suspense | Σ `interest_in_suspense_ghs` attribute of LOAN positions; input_required when the source carries no such attribute, by classification: source `bog_classification` attribute, else IFRS 9 stage proxy (1→Current, 2→Olem, 3→non-performing; the Substandard/Doubtful/Loss split of a stage-3-only book is input_required) |
| 18 | 11 | Allowable Security (Cash & Near Cash Instruments) | C–G + A | mapped | `bsd8.bucket` measure=security; collateral_classes=['CASH', 'SOVEREIGN_DEBT'] | Σ `crm_collateral_ghs` where `crm_collateral_class` ∈ CASH / SOVEREIGN_DEBT (Guide 'Security': cash and government stocks); other readily-realisable classes need the bank's confirmation; input_required when the source carries no collateral attributes, by classification: source `bog_classification` attribute, else IFRS 9 stage proxy (1→Current, 2→Olem, 3→non-performing; the Substandard/Doubtful/Loss split of a stage-3-only book is input_required) |
| 19 | 12 | Net current balance (8-9-10-11) | A (item number) | mapped | `constant` value=12 (unscaled) | template item number (printed literal); C–G / H are template formulas |
| 20 | 12 (b) | 12 (b) Of  which foreign Currency (of item 12) | C–G | input_required |  | foreign-currency share of the NET balance (item 12) — needs the FX split of items 9–11 (revaluation, suspense, security) which the source does not carry; bank must supply. |
| 21 | 13 | Provisions required (on 12) | C–G + A | mapped | `bsd8.bucket` measure=provision | Σ `ecl_provision_ghs` (position-level allowance held) by classification; the Guide's minimum (1/10/25/50/100% of the net unsecured balance) is not recomputed here — bank confirms/overrides; input_required when the source carries no provision attribute, by classification: source `bog_classification` attribute, else IFRS 9 stage proxy (1→Current, 2→Olem, 3→non-performing; the Substandard/Doubtful/Loss split of a stage-3-only book is input_required) |
| 22 | 14 | Amounts provided in BSD 2 | A (item number) | mapped | `constant` value=14 (unscaled) | template item number (printed literal); C–G / H are template formulas |

**Totals (sheet `BSD8`):** 79 / 79 input cells bound — **39 mapped** (5 rows × 5 buckets from `bsd8.bucket` = 25, + 14 item numbers) · **40 input_required** (8 rows × 5 buckets: the six movement rows, cumulative revaluation gains, FX share of the net balance) · 0 unmapped. Data-cell share: 25 of 65 mapped (38 %); the 30 movement cells are the schedule the template is designed around and the platform does not hold loan-book flows.

**How the movement schedule closes.** Item 1 (previous balance) is the platform's opening position by bucket; items 2–7 are the bank's month flows; item 8 = SUM(1–7) is BoG's formula, so it equals the opening balance until the bank supplies the movements — the form never invents a reclassification. Items 8(b), 10, 11 and 13 are as-at figures from the current book and fill independently.

**Template formulas evaluated (26):** `H7:H13`, `H15:H18`, `H20:H21` = `SUM(Cn:Gn)`; `C14:G14` = `SUM(x7:x13)`, `H14` = `SUM(C14:G14)`; `C19:G19` = `x14-x16-x17-x18`, `H19` = `SUM(C19:G19)`; `H22` = `[1]BSD2!D38+[1]BSD2!D39` (external link — see *Cross-form dependencies*).

## Sheet `BSD8-Annexure` — "ADVANCES SUBJECT TO ADVERSE CLASSIFICATION (50 LARGEST)" — 50 captured input cells (serial numbers A5:A54) + blank detail grid B:P × rows 5–54 · 106 template formulas

The detail grid is BLANK in the official template (no `0` placeholders), so it is bound with `grid_lines` (`_common.py`); the exporter writes bound off-layout cells. One facility per row, largest cedi balance first (`bsd8.annexure`, `rank` = row − 4, field = the column key). Rows past the end of the list are positively empty (the platform knows there is no such advance) and export blank as **mapped**; a listed advance whose source lacks a detail (interest due, comments, branch, sector, security …) has that cell `input_required` with the note below.

| Column | Official header | Field key | Status | Source |
|---|---|---|---|---|
| A | (serial 1–50) | `no` | mapped | `constant` value = printed literal (unscaled) |
| B | Name of Customer | `name` | mapped | counterparty name |
| C | Branch | `branch` | mapped / input_required | `branch_id` attribute (else bank supplies) |
| D | Sector | `sector` | mapped / input_required | `sector` attribute — the BSD4 convention (else bank supplies) |
| E | Facility | `facility` | mapped | product name (else product code) |
| F | Expiry Date | `expiry_date` | mapped | `contractual_maturity` (ISO date) |
| G | Amount Due — Capital | `capital` | mapped | cedi balance of the facility |
| H | Amount Due — Interest | `interest` | input_required | interest due — accruals sub-ledger; the canonical balance is the outstanding carrying amount |
| I | Total Funded Credits | — | template formula `=G+H` | not bound |
| J | Off Balance Sheet Liability | `obs` | mapped | Σ notional of LC/guarantee + undrawn commitments of the customer, on the customer's first-listed facility (0 on later rows; input_required when the facility has no counterparty) |
| K | Total Exposure | — | template formula `=I+J` | not bound |
| L | Value of Security | `security_value` | mapped / input_required | `crm_collateral_ghs` |
| M | Type of Security | `security_type` | mapped / input_required | `crm_collateral_class` |
| N | Classifi-cation | `classification` | mapped / input_required | bucket caption (Olem / Substandard / Doubtful / Loss); a stage-3-only loan → input_required (bank states the split) |
| O | Provision made | `provision` | mapped / input_required | `ecl_provision_ghs` |
| P | Comments on action | `comments` | input_required | bank supplies |
| row 55 | TOTAL | — | template formulas `SUM(G5:G54)` … `K55=I55+J55` | not bound |
| row 56 | % of 50 Largest Funded Exposures to Total Funded Credit | — | template formula `=K55/BSD8!H19*100` | not bound |

Line specs: 50 detail rows × 13 fields = 650 grid cells + 50 serial numbers = 700 bound cells (all declared with a source); the mapped / input_required split of the grid is data-dependent (per listed advance: interest and comments are always input_required; branch / sector / security / classification depend on the source).

## Residual unmapped lines — data the bank must supply

- **Items 2–7 (movement schedule)**: new advances, interest charged, revaluation gains/loss for the month, amounts recovered, amounts written off, net changes in classification — a loan-book movement / write-off / recovery schedule by bucket. The canonical model holds positions and snapshots, not flows.
- **Item 9** cumulative revaluation gains on non-performing FX advances (same gap as BSD2 line 8).
- **Item 12(b)** FX share of the net balance (needs the FX split of items 9–11).
- **Substandard / Doubtful / Loss** columns whenever the source classifies only by IFRS 9 stage (stage 3 cannot be split) — supply `bog_classification` on the LOAN feed.
- **Item 10 / 11 / 13** when the source carries no `interest_in_suspense_ghs` / `crm_collateral_*` / `ecl_provision_ghs` attribute at all.
- **Item 13 caveat**: the value is the position-level provision HELD; the Guide's minimum (1/10/25/50/100 % of the net unsecured balance of item 12) is not recomputed by the platform because item 12 depends on the bank-supplied movements — the bank confirms or overrides.
- **Annexure**: interest due (H) and comments on action (P) for every listed advance; branch / sector / security detail where the feed lacks them.

## Cross-form dependencies

- `BSD8!H22` "Amounts provided in BSD 2" = `[1]BSD2!D38+[1]BSD2!D39` — BoG's external workbook link. `FormSpec.depends_on = ("BSD2",)`, so external index `[1]` resolves to the BSD2 computed for the same reporting date (`engine.compute_form → external()`); the test proves `H22 == BSD2!D38 + BSD2!D39` from a BSD2 package generated the same way. Note (recorded, not resolved by guesswork): in the REVISED BSD2 layout rows 38/39 are "(iii) 1Year Bond/Stock" and "(iv) Other Bills" of section 7(a) — the link predates the current BSD2 revision, where the provision lines are rows 69–71. The template's own link is reproduced verbatim.
- `BSD8-Annexure!C56` = `K55/BSD8!H19*100` (in-workbook link to the summary sheet's net-balance total).
- `BSD5A` depends on BSD8 (provisions) — see that form's map.
- Guide BSD7A: new-provision charge ≥ BSD8 item 13 total (a review check, not a formula).

## Tests

`backend/tests/services/bog_forms/test_bsd8.py` — every input cell of both sheets bound and the counts above; the classification rule (explicit → stage proxy → unclassified); end-to-end generation through `POST /api/v1/banks/{bank}/regulatory-packages` on a LOAN book inserted with `bog_classification` / `ifrs9_stage` / provision / suspense / collateral attributes: item 1 buckets and `H7 = Σ C7:G7`, items 2–7 input_required, `item 8 = Σ items 1–7`, item 8(b) FX, items 10/11/13, `item 12 = 8 − 9 − 10 − 11` per bucket, the annexure ranking / per-row `I = G+H`, `K = I+J`, TOTAL row, `C56 = K55/H19×100`, positively-empty tail rows, and `BSD8!H22 = BSD2!D38 + BSD2!D39` (non-zero, from securities inserted to feed BSD2 rows 38/39); a stage-3-only book leaves the NPL split input_required while Current/OLEM resolve.

## Framework asks

1. **Attribute conventions to document in `docs/API_INTEGRATION.md` §3.4** (read here, preserved verbatim by ingestion like `obs_category` / `crm_collateral_*`): `bog_classification` (current | olem | substandard | doubtful | loss) and `interest_in_suspense_ghs` (number, cedi). Until banks supply `bog_classification`, the Substandard / Doubtful / Loss columns stay input_required on stage-only feeds.
2. **Percentage formula cells are scaled on export**: `BSD8-Annexure!C56` (`=K55/BSD8!H19*100`) evaluates correctly in base units but the renderer divides every formula cell by the sheet's ¢'Million divisor, so the export shows `0.000112` for 111.9 %. Formula cells need a per-cell unit override (e.g. a catalog list of unscaled formula cells per sheet, or detect `*100` / `%` formulas).
3. **`positions.sum` sums raw `balance` with no cedi conversion** (a USD 1,000,000 loan contributes 1 to BSD2's Foreign column, not its cedi equivalent) and does not filter `validation_status` (the derivation slice is accepted/warning). BSD8 (like BSD4) converts via `balance_ghs` → spot; a BSD8-vs-BSD2 loan reconciliation will differ on FX positions until `positions.sum` converts.

## Regenerating the table

```python
from app.services.regulatory_reporting.bog_forms.linemaps import line_maps_for
from app.services.regulatory_reporting.bog_forms.layout import load_layout
maps, layout = line_maps_for("BSD8"), load_layout("BSD8")
for line in maps["BSD8"]:                      # one row per LineSpec: code · label · cells · source · params · notes
    print(line.code, line.label, line.cells, line.source, line.params, line.notes, line.unscaled)
```
