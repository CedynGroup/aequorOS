## Summary

aequorOS calculates Bank of Ghana credit risk on the corrected exposure basis, including government foreign-currency claims, public-sector paper, SME loans and bank placements.

## What changed

- Added a separate net capital credit exposure basis on the live and official fact planes. Accounting loans, IFRS 9 EAD and expected-loss stress EAD stay gross; booked IFRS allowance remains the capital figure of record.
- Shared CRD issuer/counterparty classification, original bank maturity treatment and specific deductions between fact derivation and bottom-up stress. Applied GoG/BoG FX 20%, PSE institution 50% and enterprise 100%, SME 100% plus the FX add-on, and bank ERG weights.
- Removed arbitrary issuer-class sovereignty. Unestablished preferential classifications and invalid ERG grades refuse capital.
- Netted specific provisions and suspended interest once. Residual capital assets exclude covered loan contra accounts, including suspended interest outside the loan code block, while retaining unrelated asset impairment and uncovered GL loan allowances.
- Carried the corrected basis through forecasts and both enterprise projection legs, including growth and the year-one securities haircut. Bottom-up migration/FX RWA uses net exposures while expected loss uses gross EAD. The projection uplift is measured against total capital credit RWA, so constant assets are not assigned the migrating book's percentage increase.
- Fresh official/live capital, workbench and reverse-stress calculations, official/live forecasts, optimizer, what-if and enterprise stress refuse stale facts without the credit exposure group. Re-derivation supplies that basis; existing stored run outputs remain readable.
- Preserved established government/central-bank issuer labels and the existing governed collateral pool across split weights and bank counterparty reclassification.
- Widened both financial-fact CHECK constraints; the migration creates no bank financial data.

## Why

BoG CRD (June 2018), in force, Part 2 requires net exposures (¶98), GoG/BoG FX 20% (¶107), PSE weights (¶117–119 and Annex 2D), bank weights and original term (¶123–124), and SME 100% (¶139). HQLA eligibility does not establish sovereign capital treatment. Forecast and stress capital must measure the same classified net book as standalone capital.

Primary authority: [BoG Capital Requirements Directive, June 2018](https://www.bog.gov.gh/wp-content/uploads/2022/05/Basel-II-BOG-CRD-Final-27-June-2018-Basel-Committee-BSD.pdf).

## Changed figures

Amounts are regression fixtures, not customer data. Unless stated otherwise, amounts are RWA for a reporting-currency exposure of 1,000. No customer CAR is invented.

| Figure | Before → after | Why |
| --- | --- | --- |
| GoG foreign-currency claim | 0 → 200 | ¶107: 20% |
| TOR enterprise paper | 0 → 1,000 | ¶118, Annex 2D: 100% |
| Cocobod institution paper | 0 → 500 | ¶117, Annex 2D: 50% |
| Foreign-currency PSE institution | 0 → 700 | ¶119: 50% plus 20 percentage points |
| Unestablished public-sector/MDB/foreign-sovereign classification | 0 → refusal | Preferential treatment requires evidence under ¶109, ¶112–118 |
| Unconverted claim in the ECL coverage fixture | CAR with omitted claim → refusal | ¶98: missing reporting-currency conversion cannot remove a claim |
| Private/arbitrary issuer_class paper | 0 → 1,000 | A label confers no sovereign exemption |
| FINSAP, grains or cotton label without established issuer evidence | 0 → 1,000 | ¶148: instrument label alone establishes no preferential issuer classification |
| Same label: public-debt / other-assets / HQLA balance | 1,000 / 0 / 1,000 → 0 / 1,000 / 0 | Unsupported sovereign signal removed; accounting assets remain 1,000 |
| SME claim, either accepted SME category | 750 → 1,000 | ¶139: 100% |
| Foreign-currency SME claim | 750 → 1,200 | ¶139: 100% plus 20 percentage points |
| Foreign-currency central-bank loan / placement | 1,000 → 200 | ¶107 counterparty treatment overrides corporate product label |
| Corporate-labelled loan to unrated bank | 1,000 → 500 | ¶123 bank treatment |
| Nonbank counterparty tagged as placement | 1,000 → refusal | ¶123 preference cannot cover a nonbank |
| Unrated bank, long or unknown original term | 1,000 → 500 | ¶123: 50% |
| Unrated domestic bank, original term ≤3 months | 1,000 → 200 | ¶124: 20% |
| Unrated FX bank, original term ≤3 months | 1,000 → 500 | ¶124 preference requires domestic currency |
| Long-term bank ERG 1 / 2 / 6 | 1,000 → 200 / 500 / 1,500 | ¶123 rating table |
| Domestic and FX SME 1,000 each, family collateral 1,500 | 375 → 600 | Corrected weights; existing collateral pool used once |
| Unrated bank loan 1,000, recognised collateral 200 | 800 → 400 | Bank classification retains collateral recognition |
| NPL, specific provision 200 and suspended interest 50 | 1,500 → 1,125 | ¶98: net 750 ×150% |
| Same NPL capital exposure | 1,000 → 750 | Accounting and ECL EAD stay 1,000 |
| NPL, provision 200 and matching GL allowance -200 | 1,300 → 1,200 | Net 800 ×150%; eliminate duplicate GL deduction |
| Mixed GoG FX, TOR, unrated bank and SME, 1,000 each | 1,750 → 2,700 | Apply corrected weights once |
| Forecast year-0 credit RWA, millions | 1,402.5 → 1,466.5 | Net corporate reduction 60; FX-sovereign RWA 124 |
| Forecast year-1 credit RWA, millions | 1,638.75 → 1,711.79 | Roll corrected basis with accounting growth |
| Forecast CAR year 0 | 15.832363% → 15.374180% | Corrected net credit basis |
| Forecast CAR year 1 | 16.334767% → 15.822633% | Carry corrected basis |
| Forecast CAR year 2 | 16.767780% → 16.204785% | Carry corrected basis |
| Forecast CAR year 3 | 17.008853% → 16.408176% | Carry corrected basis |
| Forecast CAR year 4 | 16.051029% → 15.498456% | Carry corrected basis |
| Forecast CAR year 5 | 15.215367% → 14.705671% | Carry corrected basis |
| Enterprise base/stress: net corporate 1,000, annual loan growth 10%, year 1 | 1,000 → 1,100 | R1: net exposure grows on both legs with accounting loans |
| Same enterprise exposure, years 2 / 3 | 1,000 / 1,000 → 1,210 / 1,331 | R1: repeat the annual roll-forward |
| Enterprise GoG FX paper 1,000, securities growth 20%, year 1 base / stress with 17.5% haircut | 200 / 200 → 240 / 198 | R1: public-debt exposure grows and takes the one-off stress haircut |
| Same public-debt exposure, years 2 / 3 base | 200 / 200 → 288 / 345.6 | R1: subsequent growth continues |
| Enterprise regression base-leg credit RWA, millions, years 1 / 2 / 3 | 1,551.5 / 1,568 / 1,586.15 → 1,693.55 / 1,868.785 / 2,064.5195 | R1: grow net loans and sovereign credit; keep residual assets constant |
| Same regression stress leg with 17.5% securities haircut, millions, years 1 / 2 / 3 | 1,551.5 / 1,568 / 1,586.15 → 1,667.51 / 1,837.537 / 2,027.0219 | R1: securities take a one-off haircut before subsequent growth |
| Same public-debt exposure, years 2 / 3 stress | 200 / 200 → 237.6 / 285.12 | R1: haircut occurs once; subsequent growth continues |
| Bottom-up domestic/FX SME 1,000 each: baseline RWA | 1,500 → 2,200 | R2: replace source RW75 with domestic 100% and FX 120% |
| Same SME book, FX-only 10% shock: bottom-up RWA | 1,575 → 2,320 | R2: FX net exposure ×120% |
| Same SME book: overlay applied to corrected 2,200 capital RWA | 2,310 → 2,320 | R2: uplift 1.05 → 2,320/2,200; retain precision through final rounding |
| FX central-bank claim: bottom-up baseline / 10% FX stress | 750 / 825 → 200 / 220 | R2 fixture source RW75 replaced by ¶107 20% |
| FX unrated bank loan: bottom-up baseline / 10% FX stress | 750 / 825 → 500 / 550 | R2 fixture source RW75 replaced by ¶123 50% |
| FX ERG-6 placement: bottom-up baseline / 10% FX stress | 750 / 825 → 1,500 / 1,650 | R2: shared ERG classification |
| FX PSE institution loan: bottom-up baseline / 10% FX stress | 750 / 825 → 700 / 770 | R2: shared issuer classification and FX add-on |
| FX SME 1,000, provision 200, suspended interest 50: bottom-up baseline / FX stress | 750 / 825 → 900 / 990 | R2: net 750 at120%; expected-loss EAD stays gross 1,000 |
| Same SME: baseline / stressed expected loss | 9 / 9.9 → 9 / 9.9 | Preserve gross EAD ×2% PD ×45% LGD |
| Corrected credit RWA 3,200 containing the two-SME 2,200 book and 1,000 constant residual assets, FX-only shock | 3,360 → 3,320 | R2: add migrating-book delta 120 to total RWA rather than applying its percentage to residual assets |
| Fresh official/live capital, forecast and enterprise stress using pre-migration facts | Legacy CAR/RWA computation → refusal | R3: credit_exposure_basis_missing; re-derive facts; stored historical outputs remain intact |
| Equipment 1,000 with unrelated ASSET impairment -200: residual RWA | 1,000 → 800 | R4: do not strip non-loan impairment |
| Corporate loan 1,000, suspended interest 50 and GL counterpart -50 outside loan block | 900 → 950 | R4: deduct suspense once on the exposure; remove GL counterpart |
| Same loan, suspended-interest GL inside loan block | 950 → 950 | R4: retain single-counting on both chart representations |
| Uncovered GL loan 1,000 and allowance -200 | 1,000 → 800 | R4: without loan positions, residual assets retain their allowance |

## How it was tested

The author supplied prior validation results in the captured verification plan; those are historical evidence, not independently repeated in this review phase. The outer executor owns repository test, lint, push, PR and CI phases.

This review adds cited behavioral regressions for both projection legs, securities haircuts, corrected loan/placement stress classifications, gross expected-loss versus net-RWA measurement, official/live stale-basis refusal with preserved history, and GL deduction counterparts. Final focused verification: 155 passed across enterprise projection, bottom-up credit stress, CRD exposure derivation, stale-basis/history preservation, enterprise controls and forecast/capital parity. The same selected regressions executed against starting HEAD 6fc56f2705374db672c61ae82124bc99e2e40193 produced 12 behavioral failures and two expected passes for already-correct in-block suspended-interest accounts. These failures demonstrate R1–R4 before repair; the final repaired suite passes.

no user-visible change - screenshots not applicable

Scope: C3 only. C1/C2 caps, operational-risk method/labels, NOP and draft return/calendar rules remain separate. This does not resolve retail qualification, past-due grading or CCF findings.

Task status note: figure changes — the additional R1–R4 enterprise growth/haircut, net bottom-up stress, stale-basis refusal and residual GL figures were recorded before verification. Final focused verification passed 155 cases; all 12 pre-fix behavioral failures are corrected. Accounting and expected-loss EAD remain gross, and booked IFRS allowance remains separate from net CRD credit exposure. The outer executor owns subsequent repository checks and eventual PR publication.
