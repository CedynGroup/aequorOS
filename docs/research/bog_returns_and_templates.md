# Bank of Ghana — Prudential Return Content & Templates (Research Dossier)

**Purpose:** Source-of-truth research for AequorOS return coverage (Liquidity/LCR/NSFR, Basel Capital/CAR/RWA, IRRBB, FX risk, FTP, Balance-sheet forecasting).
**Research date:** 2026-07-16.
**Method:** Official BoG PDFs downloaded from bog.gov.gh and read page-by-page; WP REST API enumeration of the full `reg_directives` inventory (70 posts); targeted web search for secondary confirmation.

**Confidence legend**
- **CONFIRMED** — read directly from an official BoG document (URL given).
- **REPORTED** — secondary source (news, vendor case study, industry body).
- **INFERRED** — reasonable deduction from confirmed material, flagged as such.
- **UNKNOWN** — existence known, content not publicly retrievable. **Never invent these.**

> **Template fidelity rule:** All row/column names quoted below are transcribed verbatim from the official PDFs. Anything not quoted is not publicly available and is listed in §11 (Gap list).

---

## 1. Executive summary

1. BoG shipped a **coordinated Basel-liquidity/Pillar-2 package in February 2026** (exposure drafts, comments due 30 June 2026, all effective **1 January 2027**): Liquidity Monitoring Tools Directive (LMTD), Liquidity Risk Management Directive (LRMD), IRRBB Guideline, ICAAP Guideline, Stress Testing Guideline, Recovery Planning Directive. CONFIRMED (all PDFs read; see §3–§8).
2. A **Liquidity Coverage Ratio Directive, 2026 (banks only)** is referenced by name in both the LMTD and LRMD as part of the package, but **no public PDF exists on bog.gov.gh as of 2026-07-16** (verified via WP REST API enumeration + site search). Its quantitative content (runoff rates, HQLA haircuts, minimum ratio) is **UNKNOWN**. The LMTD's "LCR by Significant Currency" template does confirm BoG will use **HQLA Level 1 / 2A / 2B** structure (§3.7).
3. The **LMTD contains 11 fully-published draft reporting templates** — the single richest public source of BoG liquidity return layout (prudential ratios, 15-column contractual maturity mismatch, concentration of funding, unencumbered assets, LCR by currency). Monthly, due **within 9 days after month end**. CONFIRMED (§3).
4. Capital: the **CRD 2018** (final, in force since Jan 2019) defines CAR = 10% min + 3% conservation buffer = **13%**, CET1 ≥ 6.5%, leverage ratio ≥ **6%** (Tier 1), CCyB currently 0, D-SIB buffer 0. Credit risk = Standardised Approach; operational risk = Standardised (8 business lines); market risk = maturity ladder/duration methods. CONFIRMED (§5). The CRD itself contains **no reporting form**; the CAR return layout is best evidenced indirectly by the Stress-Testing Guideline's capital templates (§8) — the actual ORASS CAR return form is **UNKNOWN** (not public).
5. **Large Exposures Directive (final, September 2025)**: monthly reporting in **5 published templates** (verbatim column headers captured, §6). Limits: **20% of Net Own Funds** (banks/FHCs), **15%** (S&L/finance houses); large exposure = ≥10% NOF. Effective 1 Jan 2027. Breach ⇒ CET1 deduction.
6. **IRRBB Guideline (exposure draft, Feb 2026)**: quarterly supervisory reporting **within 9 days after quarter end** + annual ICAAP inclusion; 1-year pilot with quarterly reports from publication. Full standardised framework published: **19 time buckets**, NMD core-deposit caps (90/70/50%), CPR/TDRR shock scalars, prescribed shocks **GHS ±450bp parallel** (short 500 / long 300), EVE outlier threshold **15% of Tier 1**. ΔEVE/ΔNII disclosure table published. CONFIRMED (§4).
7. **FX Net Open Position**: Revised Directive (Notice BG/FMD/2026/07, 10 Feb 2026, final): single-currency NOP **0% to −10% of NOF** (long positions prohibited in USD/GBP/EUR/Other), aggregate ≤ **20% NOF** (short-hand method); **daily** DBK returns due **10:00 a.m. next business day via ORASS** (DBK 102/300/400/700 form codes cited). CONFIRMED (§9).
8. **Reporting channel:** ORASS (Online Regulatory Analytical Surveillance System, orassportal.bog.gov.gh; Regnology/Vizor platform). Vendor case study reports BoG consolidated collections into **"42 Returns with over 250 forms"**; individual form layouts are not public. REPORTED (§10).
9. **CRR (monetary, not BSD)**: tiered/dynamic CRR (15/20/25% by loan-to-deposit) introduced April 2024 → currency-matched CRR June 2025 → **uniform 20% (all deposits, held in cedi) effective 4 June 2026**. REPORTED-multiple + BoG MPC (§10.3).
10. **NSFR:** mentioned in LMTD/LRMD preambles as Basel III context only; **no BoG NSFR directive or return exists publicly**. UNKNOWN/not yet implemented (§11).

---

## 2. Return-family inventory

| # | Return / obligation | Directive (status, date) | Frequency | Deadline | Channel | Template public? | AequorOS module |
|---|---|---|---|---|---|---|---|
| 1 | Liquidity monitoring pack (6 tools, Tables 1–11) | LMTD, 2026 (exposure draft, Feb 2026; effect 1 Jan 2027) | Monthly | ≤ 9 days after month end | BoG (ORASS INFERRED) | **CONFIRMED — 11 draft templates published** | Liquidity |
| 2 | LCR return (banks only) | LCR Directive, 2026 (referenced; **not public**) | UNKNOWN (monthly INFERRED) | UNKNOWN | UNKNOWN | **UNKNOWN** (only per-currency summary rows via LMTD Table 11) | Liquidity/LCR |
| 3 | Liquidity Adequacy Statement (LAS) + ILAAP | LRMD, 2026 (exposure draft; effect 1 Jan 2027) | Quarterly (LAS); annual (ILAAP within ICAAP) | — | — | No fixed form (narrative) | Liquidity |
| 4 | IRRBB return (ΔEVE/ΔNII, SF) | IRRBB Guideline (exposure draft, Feb 2026; effect 1 Jan 2027; 1-yr pilot from publication) | Quarterly + annual (ICAAP) | ≤ 9 days after quarter end | BoG | **CONFIRMED** — Appendix IV disclosure tables + full SF parameters | IRRBB |
| 5 | ICAAP report | ICAAP Guideline (exposure draft, Feb 2026; effect 1 Jan 2027) | Annual | ≤ 3 months after year-end (data as at 31 Dec) | BoG | Prescribed 17-section format CONFIRMED (narrative, no grid) | Capital, all |
| 6 | Stress test results + 3-yr projections | Stress Testing Guideline (exposure draft, Feb 2026; effect 1 Jan 2027) | Annual (within ICAAP) | End of March of ensuing year | BoG | **CONFIRMED** — Tables 1–6 published | Balance-sheet forecasting, Capital |
| 7 | CAR / capital adequacy return | CRD 2018 (final, in force 1 Jan 2019) | Periodic (monthly REPORTED) | UNKNOWN | ORASS | **UNKNOWN** (structure inferable from CRD + stress Tables 1–2) | Capital/CAR/RWA |
| 8 | Large exposures returns (Templates 1, 1a, 2, 3, 4) | Large Exposures Directive (final, Sept 2025; effect 1 Jan 2027) | Monthly (+5-day breach notice) | — | BoG | **CONFIRMED — 5 templates published** | Capital / concentration |
| 9 | Credit concentration metrics (HHI/Gini in ICAAP) | Guidelines on Credit Concentration Risk (final, Sept 2025; effect 1 Jan 2027) | Annual (ICAAP) + internal | Prep deliverables due 31 Jul 2026 | BoG | No grid template | Capital Pillar 2 |
| 10 | Daily Bank Returns (DBK) incl. NOP | Revised NOP Directive BG/FMD/2026/07 (final, 10 Feb 2026) | **Daily** | 10:00 a.m. next business day | **ORASS** (email only on downtime) | Form codes cited (DBK 102/300/400/700); layouts **UNKNOWN** | FX risk |
| 11 | Monthly BSD prudential pack (BS, P&L, etc.) | Act 930 s.93 + BoG prescriptions | Monthly (REPORTED) | UNKNOWN | ORASS ("42 returns, 250+ forms" REPORTED) | Balance-sheet forecasting (background) |
| 12 | CRR compliance | MPC notices (uniform 20% from 4 Jun 2026) | Daily/weekly maintenance — UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | Liquidity (context) |
| 13 | Recovery plan | Recovery Planning Directive (exposure draft, Feb 2026) | Not researched in depth | — | — | Not reviewed | (out of scope) |
| 14 | Public disclosures (liquidity, ICAAP, IRRBB) | LRMD §87–88; ICAAP §82; IRRBB App. IV | Annual | Website + AFS by **31 March** of ensuing year | Public | IRRBB tables CONFIRMED | All |

---

## 3. Liquidity Monitoring Tools Directive (LMTD), 2026

**Source (CONFIRMED, primary):** https://www.bog.gov.gh/wp-content/uploads/2026/02/Liquidity-Monitoring-Tools-Directive-Cleaned-9.2.26.pdf (32 pp., "(EXPOSURE DRAFT)", February 2026, marked PUBLIC).
Landing page: https://www.bog.gov.gh/reg_directives/liquidity-monitoring-tools-directive-exposure-draft/ (posted 2026-02-19).

### 3.1 Status, scope, legal basis
- Exposure draft; comments to bsdletters@bog.gov.gh by **30 June 2026**. CONFIRMED (notice box, p. ii).
- Citation: "Bank of Ghana Liquidity Monitoring Tools Directive (LMTD), 2026"; issued under **Sections 36(2) and 92(1) of Act 930**. CONFIRMED (¶1–2).
- Applies to **Banks, Savings and Loans, Finance House, Finance and Leasing and Financial Holding Companies** ("RFIs"). CONFIRMED (¶3).
- Read in conjunction with: **Risk Management Directive 2021, Liquidity Risk Management Directive 2026, Liquidity Coverage Ratio Directive 2026, Corporate Governance Directive 2018**. CONFIRMED (¶4). ← *the only official enumeration found of the "LCR Directive, 2026" by name.*
- Effect from **1 January 2027**; alignment by **31 December 2026**. CONFIRMED (¶8–9).
- Sanctions: s.40 and s.92(8) of Act 930. CONFIRMED (¶47).

### 3.2 The six monitoring tools (CONFIRMED, preamble p. iv)
a) BOG's Prudential Liquidity Ratios; b) Contractual Maturity Mismatch; c) Concentration of Funding; d) Available Unencumbered Assets; e) LCR by Significant Currency; f) Market-Related Monitoring Tools.

### 3.3 Reporting frequency/deadline (CONFIRMED, Part II ¶7)
> "RFIs are required to submit monthly reports to BOG, not later than 9 days after the last day of each month on each of the metrics below as per the templates provided in the Appendix – Reporting Templates."

### 3.4 Key definitions (CONFIRMED, Part I)
- **Narrow Liquid Assets**: (a) domestic & foreign currency notes/coins; (b) unencumbered balances in correspondent accounts with non-resident FIs held for operational purposes, readily withdrawable; (c) placements with non-resident FIs rated AAA; (d) balances held at BoG; (e) unencumbered GoG treasury bills and BoG bills up to 1 year; (f) unencumbered treasury securities of other sovereigns/central banks/MDBs ≤1yr, marketable, redeemable within two working days; (g) claims on other domestic banks; (h) other assets BoG may prescribe.
- **Broad Liquid Assets**: all Narrow + all GoG bonds/instruments unencumbered, maturity >1yr, marketable and freely transferable + **GSE-listed equities subject to BoG haircut, limited to 10% of total liquid assets** + other assets BoG may prescribe.
- **Volatile Liabilities**: "all demand deposits (Current and Call accounts)".
- **Short-term liabilities**: all deposit liabilities maturing within a year (current/call/savings treated as <1yr by nature); borrowings maturing within a year; cheques for clearing drawn on a bank; contingent liabilities maturing within a year; any other liability maturing within a year.
- **Eligible Collateral for Repo or Depo**: GoG treasury bills/notes incl. GoG-issued or guaranteed bonds, BoG bills, GoG Eurobonds, and corporate bonds listed on the Ghana Fixed Income Market.
- **Group of Connected Counterparties**: control relationship or economic interdependence (defined same as Large Exposures Directive per footnote 4).
- Significant counterparty: **>1% of total balance sheet** (¶27). Significant currency: **≥5% of total liabilities** (¶30, ¶41). Unencumbered-asset currency significance: ≥5% of the associated stock (¶36).

### 3.5 BOG Prudential Liquidity Ratios — formulas and thresholds (CONFIRMED ¶8–14)
Four ratios, each on Narrow and Broad bases (monitoring tools for banks; **binding prudential requirements for SDIs** — "SDIs are required to comply with these prudential ratios"; tabled thresholds shown for both SDIs and Banks):

| Ratio | SDI Narrow | SDI Broad | Bank Narrow | Bank Broad |
|---|---|---|---|---|
| Liquid Assets / Volatile Liabilities | 90% | 100% | 80% | 100% |
| Liquid Assets / Short Term Liabilities | 50% | 60% | 50% | 70% |
| Liquid Assets / Total Assets | 30% | 40% | 30% | 50% |
| Liquid Assets / Total Deposits | 60% | 70% | 60% | 80% |

### 3.6 Tool mechanics (CONFIRMED ¶15–46)
- **Contractual maturity mismatch** time bands (¶16): "overnight, 7-day, 14-day, 1, 2, 3, 6 and 9 month, 1, 2, 3, 5 and beyond 5 years buckets"; no rollover assumption; instruments without defined maturity reported in a separate column; securities flows and rehypothecable customer collateral reported (¶17–19); behavioural (going-concern) mismatch analysis encouraged where practical (¶20).
- **Concentration of funding** (¶21–31): Top 20 / Top 100 depositors as % of total deposit liabilities (net of amounts held as lien); funding from each significant counterparty as % of total liabilities; assets/liabilities by significant currency; reported for horizons **<1 month, 1–3, 3–6, 6–12, >12 months** (¶31); intra-group deposits identified specifically (¶28).
- **Available unencumbered assets** (¶32–38): amount/type/location marketable as collateral in secondary markets and separately BoG-eligible (repo-style, excluding ELA); customer collateral re-pledgeable; categorised by significant currency; **estimated haircut** and **expected monetised value** reported.
- **LCR by significant currency — banks only** (¶39–43): stock of HQ FX assets and net FX outflows "shall mirror those of the LCR for common currencies"; BoG may set minimum monitoring ratios under stress assumptions; breaches notified **within 5 days**.
- **Market-related monitoring tools** (¶44–46): BoG collects market-wide/institution-specific data; RFIs must supply on request: (i) detailed costs of unsecured and secured funding by tenor and instrument; (ii) trends in collateral flows incl. stress projections; (iii) current short-term secured/unsecured funding spreads; (iv) cash balances held at BoG.

### 3.7 APPENDIX — DRAFT REPORTING TEMPLATES (verbatim, CONFIRMED pp. 20–27)

**Table 1: BOG Prudential Ratios** — header: `Amounts in GHS '000 | Reporting Month | Previous Month`. Rows: Narrow Liquid Assets; Broad Liquid Assets; Volatile liabilities; Total Deposits; Short-term liabilities; Total Assets; *(Percentage)* Narrow Liquid Assets to Volatile Liabilities; Broad Liquid Assets to Volatile Liabilities; Narrow LA to Short Term Liabilities; Broad LA to Short Term Liabilities; Narrow LA to Total Deposits; Broad LA to Total Deposits; Narrow LA to Total Assets; Broad LA to Total Assets.

**Table 2: Reporting Template for Contractual Cash Flow Mismatch** (Amounts in GHS '000) — 15 numbered columns: `Total | Next Day | 2-7 days | 8-14 days | 15 days to 1 mth | 1-2 mths | 2-3 mths | 3-6 mths | 6-9 mths | 9 mths-1 yr | 1-2 yrs | 2-3 yrs | 3-5 yrs | >5 yrs | Non-contractual`. Rows (numbered 1–17): 1 Contractual maturity of assets (items 2 to 4); 2 Advances; 3 Trading, hedging and other investment instruments; 4 Other assets; 5 Contractual maturity of liabilities (items 6 to 9); 6 Stable deposits; 7 Volatile deposits; 8 Trading and hedging instruments; 9 Other liabilities; 10 On-balance sheet contractual mismatch (item 1 less item 5); 11 Cumulative on-balance sheet contractual mismatch; 12 Off-balance sheet exposure to liquidity risk, *of which:*; 13 Liquidity facilities provided to off-balance sheet vehicles; 14 Undrawn commitments (items 15 to 17); 15 Unutilised portion of irrevocable lending facilities; 16 Unutilised portion of irrevocable letters of credit; 17 Indemnities and guarantees.

**Table 3: Information on Investments and Items with No Contractual Maturity** — columns: `Name of Instrument/investments/Item without contractual maturity | Amount (GHS'000)`; rows Instrument 1…n, Total.

**Table 4: Customer Collateral Received** ("Customer collateral which can be re-hypothecated") — columns: `Name of Instrument | Total Amounts (A) | Amounts already hypothecated (B) | Amount Available (C = A−B)`.

**Table 5: Funding Liabilities Sourced from Significant Counterparties** — columns: `Name of Significant counterparty (1% of Total Assets) | Amount of Funding | Percentage of Total Liabilities | Intragroup or related parties (Yes or No) to be marked`. Rows: Significant counterparty 1…n; Total; **Total for Top 20 counterparties (depositors)**; **Total for Top 100 counterparties (depositors)**; Total 20 deposits as a percentage of total deposits; Total 100 deposits as a percentage of total deposits.

**Table 6: List of Assets and Liabilities by Significant Currencies** — columns: `Name of Significant Currency | Assets | Liabilities | Mismatch (4 = 2−3) | Mismatch as a Percentage of Total Liabilities`.

**Table 7: Time Buckets of Maturity of Exposures** — columns (months): `<1 | 1-3 | 3-6 | 6-12 | >12 | Total`. Row groups: A. Top 20 Depositors; B. Funding from Significant Counterparties (counterparty 1…n, Total); C. Assets by Significant Currency; D. Liabilities by Significant Currency.

**Table 8: Concentration of Deposit Funding** — columns: `Total | Next day | 2 to 7 days | 8 days to 1 month | 1 to 2 months | 2 to 3 months | 3 to 6 months | 6 to 12 months | >12 months`. Rows: Funding from associates of the reporting financial institution; Twenty largest depositors; Twenty largest financial institutions funding balances; Twenty largest government and parastatals funding balances; Negotiable paper funding instruments (*of which:* issued for a period not exceeding twelve months; *of which:* issued for a period exceeding five years).

**Table 9: Statement of Available Unencumbered Assets** — columns: `S/No. | Description | Asset Type & Nature | Location | Value in Cedi ('000) | Estimated Haircut (%) [fn: Required by Secondary Market] | Monetized Value of Collateral`. Sections: A. marketable as collateral in secondary market; B. eligible for BOG standing facilities; C. By Significant Currency (Currency 1…n).

**Table 10: Collateral received by the reporting financial institution** — columns: `Fair value of collateral received, or own debt securities issued available for encumbrance — Total | Issued by other entities of the group | BOG eligible` and `Nominal of collateral received, or own debt securities issued but not available for encumbrance`. Rows: Loans and advances; Equity instruments; Debt securities; *Government issued*; *Issued by financial institutions*; *Issued by non-financial corporations*; *Other collateral received*; Own debt securities issued; Total Assets, Collateral Received and Own Debt Securities Issued.

**Table 11: LCR by Significant Currency** — columns: `Cedi | USD | Pound | Euro | Others` ("Values in Cedi"). Rows: **Stock of HQLA — Level 1; Level 2A; Level 2B; A) Total adjusted HQLA; Net Cash Outflow — Total Cash outflow (1); Total Cash inflow (2); B) Net Cash Outflow (2-1); Liquidity Coverage Ratio (LCR) (A/B*100)**. ← confirms BoG LCR uses Basel HQLA level taxonomy; runoff/inflow rates themselves are NOT in this document (UNKNOWN, live in the unpublished LCR Directive).

---

## 4. IRRBB — Guideline on Management and Measurement of Interest Rate Risk in the Banking Book

**Source (CONFIRMED, primary):** https://www.bog.gov.gh/wp-content/uploads/2026/02/Exposure_Draft_Guidelines-on-the-Management-and-Measurement-of-Interest-Rate-Risk-in-the-Banking-Book-_February-2026.pdf (43 pp., exposure draft, Feb 2026).
Landing page: https://www.bog.gov.gh/reg_directives/guidelines-on-management-and-measurement-of-interest-rate-risk-in-the-banking-book-irrbb/ (posted 2026-02-19).

### 4.1 Status, timing, pilot
- Effect from **1 January 2027** (¶9). CONFIRMED.
- **Pilot: "RFIs shall pilot the provisions of this Guideline for a period of one (1) year following its publication and submit quarterly reports to the BOG in the prescribed templates in Appendix IV"** (¶10). After the pilot, quarterly reports due **"not later than nine (9) days after the ensuing quarter"** (¶11, ¶55). CONFIRMED.
- Annual IRRBB reporting as part of ICAAP at the same entity level as ICAAP (¶56 + fn16). Major currency = **>5% of banking-book assets or liabilities** (¶56). CONFIRMED.
- **Outlier test: RFIs with IRRBB exposures exceeding 15% of Tier 1 capital are "outliers"**, subject to supervisory review (preamble/Part V). CONFIRMED.
- Banks must use the **Standardised Framework (SF)** for supervisory reporting in the annual ICAAP; IMS permitted for Pillar 2 quantification subject to model-governance conditions; IMS must report projected NII years 1–2 baseline, Own Funds baseline, **EaR (yr 1–2) under gradual 200–500bp shifts**, and equity value changes under 200–500bp parallel shocks incl. convexity/embedded options (¶60). CONFIRMED.

### 4.2 Standardised Framework parameters (Appendix I, CONFIRMED)
- **19 predefined time buckets** (Table 1) with midpoints, e.g. Overnight (0.0028Y); O/N<t≤1M (0.0417Y); 1M<t≤3M (0.1667Y); 3M–6M (0.375Y); 6M–9M (0.625Y); 9M–1Y (0.875Y); 1–1.5Y (1.25Y); 1.5–2Y (1.75Y); 2–3Y (2.5Y); 3–4Y (3.5Y); 4–5Y (4.5Y); 5–6Y (5.5Y); 6–7Y (6.5Y); 7–8Y (7.5Y); 8–9Y (8.5Y); 9–10Y (9.5Y); 10–15Y (12.5Y); 15–20Y (17.5Y); >20Y (25Y).
- NMDs segmented retail-transactional / retail-non-transactional / wholesale; stable vs non-stable via **10 years of observed volume data** (shorter allowed with build-up plan).
- **Table 2 — Caps on Core Deposits and Average Maturity by Category:** Retail/transactional **90% / 5 yrs**; Retail/non-transactional **70% / 4.5 yrs**; Non-retail **50% / 4 yrs**.
- **Table 3 — CPR scenario multipliers (γᵢ):** Parallel up 0.8; Parallel down 1.2; Steepener 0.8; Flattener 1.2; Short rate up 0.8; Short rate down 1.2. CPRᵢ = min(1, γᵢ·CPR₀).
- **Table 4 — TDRR scalars (uᵢ):** Parallel up 1.2; Parallel down 0.8; Steepener 0.8; Flattener 1.2; Short up 1.2; Short down 0.8. TDRRᵢ = min(1, uᵢ·TDRR₀); early-redeemed term deposits slotted overnight.
- Automatic interest rate options: add-on KAOᵢ,c = Σ ΔFVAO sold − Σ ΔFVAO purchased, valued under shocked curve **plus a relative 25% implied-volatility increase**.
- EVE: continuously compounded discounting DF = exp(−R·t) (risk-free, optionally incl. commercial margins if included in cash flows); **ΔEVEᵢ,c = Σ CF₀·DF₀ − Σ CFᵢ·DFᵢ + KAOᵢ,c**; Standardised EVE Risk Measure = max over scenarios of the sum of positive currency losses.

### 4.3 Prescribed shock scenarios (Appendix II–III, CONFIRMED)
- **Two mandatory scenarios (parallel up/down)** for quarterly supervisory reporting, annual ICAAP, and Pillar 3 disclosures.
- **Table 5 — Parallel shift (bps): GHS 450; USD 200; EUR 225; GBP 275; CNY 225; Other 325.** (GHS shock "informed by historical changes in GHS and comparable EM currencies", fn25.)
- **Table 6 — Additional (optional) scenarios: Short — GHS 500; USD 300; EUR 350; GBP 425; CNY 300; Other 500. Long — GHS 300; USD 225; EUR 200; GBP 250; CNY 150; Other 300.** Shapes: short-rate shock decays via α_short(t)=e^(−t/4); steepener = −0.65·|ΔS_short| + 0.9·|ΔS_long|; flattener = +0.8·|ΔS_short| − 0.6·|ΔS_long|.

### 4.4 Disclosure/report templates (Appendix IV, verbatim, CONFIRMED)
- **Table 7 — Qualitative Disclosures** (annual, flexible format): items (a)–(g) covering IRRBB definition, management/mitigation strategies, calculation frequency and measures, shock/stress scenario descriptions, hedging + accounting treatment, key ΔEVE/ΔNII modelling assumptions, other; plus quantitative items: "Average repricing maturity assigned to NMDs"; "Longest repricing maturity assigned to NMDs".
- **Table 8 — Quantitative Disclosures** (annual, fixed format): grid `In reporting currency — ΔEVE (T, T−1) | ΔNII (T, T−1)`; rows **Parallel up; Parallel down; Maximum**; plus `Tier 1 Capital (T, T−1)`. Definitions: ΔEVE per SF; **ΔNII = change in projected NII over a forward-looking rolling 12-month period vs own best-estimate, constant balance sheet, instantaneous shock**.
- Note: ¶10 says the pilot quarterly reports use "the prescribed templates in Appendix IV" — i.e., the quarterly return is (at minimum) the Table 8 grid. INFERRED: quarterly return = Table 8 by currency; no separate quarterly grid was published.

---

## 5. Capital adequacy — Capital Requirements Directive (CRD), 2018

**Source (CONFIRMED, primary):** https://www.bog.gov.gh/wp-content/uploads/2022/05/Basel-II-BOG-CRD-Final-27-June-2018-Basel-Committee-BSD.pdf (final, 27 June 2018).
Landing page: https://www.bog.gov.gh/reg_directives/capital-requirement-directive/.

### 5.1 Framework
- Legal basis: s.29(1) BSDI Act (Act 930) + s.4(d) BoG Act 2002 (Act 612). Applies to banks licensed under Act 930. CONFIRMED.
- Implemented from 1 July 2018; compliance by **1 January 2019**. REPORTED (search synthesis) consistent with directive text; CONFIRMED in-document that transition framework ran to 1 Jan 2019 (¶82).
- **CAR calculated "on a standalone and a consolidated basis"** (¶ near intro; also ¶16: standalone licensed entity + consolidated to all subsidiaries per s.31). CONFIRMED.
- Parts: 1 Definition of Regulatory Capital; 2 Credit Risk (2A on-BS, 2B off-BS, 2C CRM); 3 Operational Risk; 4 Market Risk. CONFIRMED (ToC).

### 5.2 Minimum ratios (CONFIRMED, ¶71–91, summary table verbatim)

| # | Regulatory Capital | % RWAs |
|---|---|---|
| 1 | Minimum CET1 | 6.5 |
| 2 | Capital Conservation Buffer (CCB1) — CET1 only | 3.0 |
| 3 | CET1 Ratio plus CCB1 | 9.5 |
| 4 | Maximum AT1 | 1.5 |
| 5 | Minimum Tier 1 Capital Ratio | 8.0 |
| 6 | Maximum T2 | 2.0 |
| 7 | Minimum Capital Adequacy Ratio (CAR) | 10.0 |
| 8 | Minimum CAR plus CCB1 | 13.0 |
| 9 | Countercyclical Buffer (CCB2) | 0 |
| 10 | DSIB Buffer | 0 |
| 11 | Minimum CAR + CCB1 + CCB2 + DSIB | 13.0 |

- Capital conservation standards when CET1 deficient (¶82): CET1 >6.5–7.25% ⇒ 100% earnings retention; >7.25–8.0% ⇒ 80%; >8.0–8.75% ⇒ 60%; >8.75–9.5% ⇒ 40%; >9.5% ⇒ 0%. CONFIRMED.
- **CCB2 "is zero until other elements stated in the CRD are embedded in the industry"** (¶85). D-SIB buffer discretionary (¶86–87). CONFIRMED.
- **Leverage ratio: Tier 1 based, minimum 6% for all banks** (¶88–90). CONFIRMED.

### 5.3 RWA approaches (CONFIRMED)
- **Credit risk: Standardised Approach** (¶91). Sample risk weights: claims on GoG & BoG in **domestic currency 0%**; in **foreign currency 20%** (¶106–107); BIS/IMF/highly-rated MDBs 0%; PSE (MDA) treatment tiered; non-central-government PSEs: non-profit 50%, profit-making 100% (¶115–118). CCFs for off-balance sheet items in Appendix to Part 2.
- **Operational risk: The Standardised Approach (TSA)** — gross income by **eight business lines** with beta factors, 3-year average, negative-charge offset rules (¶287–300). CONFIRMED.
- **Market risk:** interest-rate risk on debt instruments by **Maturity Ladder** (Table 4A: 13/15 bands, zone 1–3, weights 0.00%–12.50%, vertical disallowance 10%, horizontal disallowances 40%/30%/100% pattern) or **Duration Based Method** (Table 4B). CONFIRMED (Appendix to Part 4).
- ICAAP referenced as Pillar-2 (definitions/¶296 context); the full ICAAP guideline arrived Feb 2026 (§7).

### 5.4 CAR return
- The CRD prescribes computation but contains **no return form**. Banks "submit period returns to the BoG" enabling compliance assessment. REPORTED (Lexology panoramic Ghana banking chapter: https://www.lexology.com/panoramic/tool/workareas/report/banking-regulation/chapter/ghana). The ORASS CAR return layout: **UNKNOWN**.
- Best public proxy for line structure: Stress Testing Guideline Table 2 (Regulatory Capital Projection Schedule, §8.3) which enumerates CET1/AT1/T2 components and deductions "for CAR computation" per CRD, and Table 5 (RWA by risk type). INFERRED that the ORASS CAR return follows the same CRD component taxonomy.

---

## 6. Large Exposures Directive (September 2025, FINAL)

**Sources (CONFIRMED, primary):**
- Directive: https://www.bog.gov.gh/wp-content/uploads/2025/09/Large-Exposures-Directive-September-2025.pdf (26 pp.)
- Explanatory Notes: https://www.bog.gov.gh/wp-content/uploads/2025/09/Large-Exposures-Directive-Explanatory-Notes.pdf
- Earlier exposure draft (Dec 2024): https://www.bog.gov.gh/wp-content/uploads/2024/12/Exposure-Draft-Large-Exposures-Directive.pdf
- Secondary: https://nbc.edu.gh/2025/01/10/bog-issues-large-exposures-directive-for-banks-others/ (REPORTED; describes the exposure-draft dates, superseded by final).

### 6.1 Scope, limits, dates (CONFIRMED)
- Applies to banks, S&Ls, finance houses, FHCs (scope narrowed from all SDIs — Explanatory Notes).
- **Large exposure = sum of all financial exposure values to a single person/group of connected persons ≥10% of NOF** (¶11).
- **Limit: ≤20% of NOF for banks and FHCs; ≤15% for savings and loans companies and finance houses** (¶12). Explanatory notes: Ghana's 20%-of-NOF calibration chosen "to achieve the equivalent of 25% of Tier 1 capital" in BCBS terms (LE dir. line ~¶ referencing BCBS standards). Related-party style sub-limits: single exposures ≤10% NOF; RFI's subsidiaries/affiliates/associates ≤20% (¶ context lines 376–377).
- **Effective 1 January 2027** (¶8; explanatory notes record revision from 1 July 2026 → 1 Jan 2027). Board-approved compliance plan due **31 December 2025** where non-compliant; full compliance ≤1 year from that date (¶9–10). CONFIRMED. *(Conflicting REPORTED claims of a 1 Jan 2026 effective date trace to the 2024 exposure draft — use the final.)*
- Breach handling: report to BoG **within 5 days** + written remediation plan (¶59, per Act 930 s.62(6)). Breaches deducted from **CET1** (banks/FHCs) or Tier 1 (S&L/FH) in Pillar 1 CAR computation; possible dividend/bonus restrictions and higher CAR (¶63). CONFIRMED.
- Disclosure: compliance level disclosed in audited financial statements (¶60). CONFIRMED.

### 6.2 Regulatory reporting (CONFIRMED ¶57–58): monthly, in 5 appendix templates, exposures before and after CRM

**Template 1 — "RFI's exposures with values equal to or above 10% of Net Own Funds (ie. meeting the definition of large exposure)"**
Columns (verbatim): `No | Counterparty Name | Single/Group of Connected counterparties | Counterparty TIN/ Ghana Card No. | Foreign and Domestic Currency Exposures: Drawn Down / Undrawn Facility / Other Contingent Liability / Total Exposure | Section 62(8) Collaterals and Appendix 1 ECD: Type / Amount | Net Exposure | % of Exposure Value to NOF | IFRS Staging | Classification as per BOG Prudential Norms`. Footer rows: Total; **Tier 1 Capital; Net Own Funds**. Footnote: Undrawn/Other Contingent values **after applying CCF**.

**Template 1a — "Details of Connected Counterparties"**
Columns: `No | Unique ID | Counterparty Name | Group of Connected counterparties | Basis of Connection | Counterparty TIN/Ghana Card No. | Type of Facility | FCY & DCY Exposures (Drawn Down/Undrawn/Other Contingent/Total) | Section 62(8) Collaterals & Appendix 1 ECD (Type/Amount) | Net Exposure | Other Collaterals (Type/Amount) | IFRS Staging | Classification per BOG Prudential Norms`.

**Template 2 — "RFI's Top 100 exposures to counterparties (single as well as group of connected counterparties), irrespective of the values of these exposures relative to the RFI's Net Own Funds"**
Columns: `No | Counterparty Name | Counterparty TIN/Ghana Card No. | Type of Facility | Purpose of Facility | FCY & DCY Exposures (Drawn Down/Undrawn/Other Contingent/Total) | Deductible Collaterals (Section 62(8) of Act 930) | Net Exposure | Other Collaterals | Specific loan loss provisions | IFRS Staging | Classification per BOG Prudential Norms`. Rows 1–100 + Total + Net Own Funds.

**Template 3 — "RFI's exempted exposures with values equal to or above 10% of RFIs' Net Own Funds"** — same column pattern as Template 2 (minus provisions column).

**Template 4 — "RFI's other exposures with values, measured without the effect of credit risk mitigation being taken into account, equal to or above 10% of RFI's NOF (not including exposures reported in Template 2 already)"**
Columns: `No | Counterparty Name | Counterparty TIN/Ghana Card No. | Type of Facility | Purpose of Facility | FCY & DCY Exposures (Drawn/Undrawn/Other Contingent/Total) | % of Exposure Value to NOF | IFRS Staging | Classification per BOG Prudential Norms`. Footer: Tier 1 Capital; Net Own Funds.

### 6.3 Companion: Guidelines on Credit Concentration Risk (Sept 2025, FINAL)
**Source (CONFIRMED):** https://www.bog.gov.gh/wp-content/uploads/2025/09/Guidelines-on-Measurement-and-Management-of-Credit-Concentration-Risk.pdf (+ Explanatory Notes: https://www.bog.gov.gh/wp-content/uploads/2025/09/Guidelines-on-Measurement-and-Management-of-Credit-Concentration-Risk-Explanatory-Notes.pdf).
- Effective **1 Jan 2027**; preparatory deliverables to BoG by **31 July 2026** (¶7–8). CONFIRMED.
- Measurement: model-free/heuristic methods — **HHI, concentration ratios, Gini coefficient** — feeding the ICAAP; outcomes reported within the ICAAP report. CONFIRMED. No grid return template published.

---

## 7. ICAAP Guideline (February 2026)

**Source (CONFIRMED, primary):** https://www.bog.gov.gh/wp-content/uploads/2026/02/Guideline-on-ICAAP-12-2-26-clean.pdf (37 pp.; exposure-draft cycle: https://www.bog.gov.gh/wp-content/uploads/2026/02/Guideline-on-ICAAP-Exposure-Draft.pdf; landing: https://www.bog.gov.gh/reg_directives/guideline-on-internal-capital-adequacy-assessment-process-icaap-exposure-draft/).

- Purpose includes "**establish a standard format for ICAAP submission**" (¶7c). Effective **1 Jan 2027**; alignment by 31 Dec 2026 (¶9–10). CONFIRMED.
- **Submission: annually, "no later than three months after the year-end"; the report is forward-looking "with a starting position reflecting the status as of 31st December of the preceding year"** (¶72). Accompanied by **Board resolutions and Senior Management reports** (¶71). Ad-hoc updates on material change; BoG may demand an updated ICAAP anytime (¶73–74). CONFIRMED.
- Disclosure: results published on RFI website with each update, and "submit same to BOG by **31st March** of the ensuing year" (¶82). CONFIRMED.
- **Prescribed report structure (¶49, verbatim, a–q):** Executive Summary; Structure and Operations; Governance and Management of the ICAAP; Business Model and Strategy; Risk Management Framework; Risk Appetite Statement; Risk Identification and Materiality Assessment; **Quantification of Pillar 2 Capital Requirements**; Stress Testing; Capital planning; **Liquidity Planning and Management**; Capital Allocation and Reconciliation of Internal Capital; Management Actions; Internal Audit and Review of ICAAP; Challenge and Adoption of the ICAAP; Approval, Review, and Use of ICAAP within the bank; Use of Internal Models for Capital Assessment. CONFIRMED.
- **Appendix — Categories of risks to be analysed (verbatim list):** 1 Credit Risk (incl. credit concentration); 2 Operational; 3 Market; 4 Cyber & Information Technology; 5 Legal & Compliance; 6 Liquidity; 7 **IRRBB**; 8 Business & Strategic; 9 Reputational; 10 Model; 11 Country & Transfer; 12 AML/CFT; 13 Climate-Related Financial Risk; 14 any other material emerging risks. CONFIRMED.
- Data-return companion: none published — the ICAAP is a narrative document + the stress-testing tables (§8) and IRRBB SF outputs (§4) embedded in it. CONFIRMED-by-absence (no template appendix beyond risk-category list).

---

## 8. Stress Testing Guideline (February 2026)

**Source (CONFIRMED, primary):** https://www.bog.gov.gh/wp-content/uploads/2026/02/EXPOSURE-Draft-Directive-on-Stress-Testing_FEBRUARY-2026.pdf (47 pp.). Landing: https://www.bog.gov.gh/notice/guideline-on-stress-testing-exposure-draft/ and https://www.bog.gov.gh/reg_directives/guidelines-on-stress-testing-exposure-draft/.

### 8.1 Status, cadence
- Effective **1 January 2027**; alignment by 31 Dec 2026 (¶8/¶ near line 452-470). CONFIRMED.
- **"RFIs are required to submit annual stress test results to BOG as part of the ICAAP submission in the formats highlighted in Appendix II by end of March of the ensuing year"** (¶67), plus narrative on risks/entities covered, macro conditions and justification, methodologies, impact on profitability/capital/liquidity per balance-sheet date. CONFIRMED.
- Scope of techniques: sensitivity analysis → scenario analysis → enterprise-wide → **reverse stress testing**; supervisory (BoG-prescribed) scenarios contemplated ("scenarios that are provided by BOG to RFIs to apply and report on"). CONFIRMED.
- Scenario design: severe-but-plausible; at least one severe scenario; RST for insolvency/illiquidity paths (¶34–37). CONFIRMED.
- Appendix II Table 1 footnote: **"This template supersedes the Guidance Notes on Preparation of Capital Restoration Plan."** CONFIRMED.

### 8.2 Appendix II Table 1 — "Summary Results of Stress Test Scenario" (verbatim structure, CONFIRMED)
Columns: `Amounts in GHS'000 | Current | Projection Year 1 | Year 2 | Year 3`.
Row blocks:
- *Where applicable, current Capital Gap*: Total Regulatory Capital deficit needed to meet BOG minimum CAR; Minimum Unimpaired Paid-up Capital deficit.
- *Pre-Adverse Scenario (Base Case)* [current year = most recent audited]: CET 1 Capital; Tier 1 Capital; Tier 2 Capital; Total Regulatory Capital (Tier 1 & Tier 2); Risk Weighted Assets (RWA); CET1 Capital Ratio (% of RWA); Tier 1 Capital Ratio (% of RWA); CAR (%); Unimpaired Paid-up Capital.
- *Impact of Adverse Scenario — Losses arising from adverse scenario* by exposure class (per CRD Part 2): Government of Ghana; Bank of Ghana; Other Sovereigns and Central Banks; Public sector entities; Multilateral Development Banks; Banks; Other Financial Sector and Regulated Institutions; Corporates; Retail Lending (including SMEs); Past due exposures; High risk exposures; Other exposures (specify); Total losses.
- *Post-Adverse scenario (Stress Case)*: Stressed Total RWA; Stressed CET 1; Stressed Tier 1; Stressed Total Regulatory Capital; stressed ratios; Stressed Unimpaired Paid-Up Capital.
- **"Capital required to meet BOG's minimum Total Regulatory Capital of 13%"**; **"Capital required to meet BOG's minimum unimpaired paid-up capital of GHS400 m"**.
- *Management actions*: Raising of additional capital (CET1/AT1/Tier 2); Revision of dividend policy; Change in Business Strategy; Sale of Assets; Risk Reduction; Other; Total.
- *Post Capitalisation*: capital stack + ratios; **"Additional (Residual) Capital Required to meet minimum Capital Requirements (Paid-Up Capital, CAR and leverage ratio)"**.

### 8.3 Appendix II Tables 2–4 — 3-year financial projections (base + stress), baseline = latest AFS (CONFIRMED)
- **Table 2 Regulatory Capital Projection Schedule** — columns `Current | Base Case Y1–Y3 | Stress Case Y1–Y3`. CET1 rows: Paid up Capital (Ordinary Shares); Income surplus (retained earnings); Statutory reserves; Other qualifying reserves; Minority interest; CET1 before deductions; Regulatory adjustments (breakdown): Intangibles; Investment in capital of banks/other FIs; Accumulated OCI/unrealised FV losses; Deferred tax assets; Investment in commercial entities; Others; CET1 after deductions. **AT1 (capped at 1.5% of RWA)**: perpetual non-cumulative preferred shares; others. **Tier 2 (capped at 2% of RWA)**: subordinated debt (eligible); property revaluation reserves (capped at 50%); unaudited year-to-date profit; hybrid instruments; other comprehensive income; others. Total regulatory capital; Credit Risk Reserve.
- **Table 3 Movement in Profit and Loss Schedule** — opening income surplus; Interest income; Interest expense; Net interest income; Fees and Commission income; Net Trading Income; Other Income; Non-interest expenses; Other Operating expenses; Staff Cost; Impairment Losses (incl. stress losses); Depreciation and Amortisation; Other P&L components; PBT; Income Tax; PAT; Distributions/adjustments (statutory reserve, dividends); closing income surplus; Credit Risk Reserve movement; **"Adjusted income surplus … (for CAR computation in Tables 1 and 2 above)"**.
- **Table 4 Statement of Financial Position Schedule** — Foreign Assets (FCY notes & coins; correspondent accounts in non-resident FIs; other claims on non-residents (net); others); Domestic Assets (cash & balances due from other FIs; short-term investments; financial derivatives; loans, overdrafts and other advances; long-term investments (non-equity) issued by Government; shares and other equities; PPE; other assets); Total Assets; equity items (paid-up capital, retained earnings, statutory reserves, P/L to date, other reserves); Foreign Liabilities (deposit; borrowings; others); Domestic Liabilities (demand deposits; savings deposits; time deposits; other deposits; short-term borrowings; long-term borrowings; other liabilities); Total Liabilities; Shareholders' Funds and Liabilities. ← *this is the closest public proxy for BoG's balance-sheet return taxonomy (foreign/domestic split).*
- **Table 5 Evolution of RWA and Capital Requirements** — RWA for Credit/Operational/Market Risk; Total Pillar 1 RWA; **Pillar 1 Capital Requirements (fn: "13% of Pillar 1 RWA")**; Pillar 2 rows: Credit Concentration; IRRBB; Sovereign; Country and FX; Reputational; Others; Total Pillar 2; Total (P1+P2).

### 8.4 Appendix III — Supervisory Stress Test Assumptions (CONFIRMED)
Risk drivers to consider: GDP slowdown; cedi depreciation/appreciation vs major currencies; adverse interest-rate moves; inflation spike; **decline in cocoa and gold prices and production**; unexpected liquidity outflows; funding-cost increase; reputational damage; climate physical/transition risk; sovereign-exposure factors (rating migration, debt restructure, fair-value decline).
**Table 6 Key Risk Drivers and Forecasting Assumptions** (Current + Base Y1–3 + Stress Y1–3): Average yield on GoG securities; GDP Growth Rate; Interest Rates; Unemployment Rate; FX rates (USD, GBP, EUR to GH Cedi); Inflation Rates; YoY change in GSE index; Fiscal deficit; Others. Sources to cite: BOG, Ghana Statistical Services, Bloomberg, IMF, World Bank, Reuters, Fitch Solutions, AfDB, EIU (fn29).

---

## 9. FX Net Open Position — Revised Directive on NOP Limits (Notice BG/FMD/2026/07, FINAL)

**Source (CONFIRMED, primary):** https://www.bog.gov.gh/wp-content/uploads/2026/02/BG-FMD-2026-07-Revised-Directive-on-Foreign-Exchange-FX-PositionLimits-1.pdf (signed Ag. Secretary, **10 February 2026**; revises Notice BG/GOV/SEC/2012/13). Secondary: https://www.myjoyonline.com/bog-revises-directive-on-net-open-position-limits/ ; https://nbc.edu.gh/2026/02/11/bog-revises-directive-on-net-open-position-limits/ (REPORTED).

- **Limits (CONFIRMED):** Single Currency Position **0% to −10% of Net Own Funds (NOF)** per currency — "Banks shall not hold long positions in USD, GBP, EUR and Other Currencies"; Aggregate NOP **≤ 20% of NOF** (short positions). Limits subject to change per market dynamics (fn1).
- **Computation (CONFIRMED, verbatim):** "Net Open Position = Net Assets + Net Trading Position (FX Outstanding trade contracts) − Liabilities on Contingents"; convert to GHS at BoG end-of-day published rate; divide by NOF for %; aggregate by **short-hand method** (greater of sum-of-longs vs |sum-of-shorts| ÷ NOF).
- **Contingents (CONFIRMED):** oil LCs excluded from NOP; non-oil LCs maturing within 2 days (T+1/T+2) included; other contingents (guarantees, performance bonds) included if high probability of trigger (bank-determined, evidence on request); FCY margins in same currency net against LC face value; GHS margins deductible only if contractually tied to an FX forward sale maturing on/before LC maturity; syndication — only retained share counts. All contingents incl. LCs reported in **DBK 102**.
- **Reporting (CONFIRMED):** "All banks shall continue to submit the **Daily Bank Returns (DBK)**. Reports for each working day shall be submitted **no later than 10:00 a.m. on the following business day**", **"exclusively through the ORASS platform"** (email only during ORASS downtime, with re-upload once restored). Daily NOP movement (ex-contingents) must reconcile with net FX trade reported in **DBK 400, DBK 700 and DBK 300**; unexplained changes = misreporting (sanctions per Act 930 ss.41(4), 76(1)); reporting failures sanctioned per ss.93(3), 41(4).
- **DBK form layouts (rows/columns of DBK 102/300/400/700): UNKNOWN** — not public; only the codes and purposes above are confirmed.

---

## 10. Other returns and infrastructure

### 10.1 ORASS (reporting channel)
- Portal: https://orassportal.bog.gov.gh/ (CONFIRMED existence).
- Regnology (Vizor) case study (REPORTED): BoG "consolidated all data collections across the different supervisory departments into **42 Returns with over 250 forms**"; API-based upload supported; single portal for prudential data from banks and deposit-taking institutions. https://www.regnology.net/en/knowledge-hub/case-studies/integrated-financial-supervision-system-supports-bank-of-ghana-reforms/ ; rollout news: https://www.ghanaweb.com/GhanaHomePage/business/BoG-rolls-out-online-supervisory-and-reporting-tool-to-harmonise-data-collection-1760867 (REPORTED, 2020 rollout).
- Individual ORASS return/form layouts (incl. the monthly BSD pack and CAR return): **UNKNOWN — behind the portal login.**

### 10.2 Legal basis for returns — Act 930 s.93 (CONFIRMED)
**Source:** https://www.bog.gov.gh/wp-content/uploads/2019/09/BANKS-AND-SPECIALISED-DEPOSIT-ACT-2016.pdf
- s.93(1)-(2): BoG shall require submission of information/data on assets, liabilities, income, expenditure, affairs; BoG may prescribe details, **form**, and **period** of reporting.
- s.93(3): administrative penalty up to **500 penalty units** (institution and responsible key management personnel) for non-/incomplete/delayed/inaccurate submission, **plus 50 penalty units per day** of continuing default.
- s.92(1): power to issue directives (basis of LMTD/LE etc.); s.29(1): CAR prescription power; ss.36–38: liquidity requirements powers (cited in LRMD preamble).

### 10.3 Cash Reserve Ratio (monetary policy instrument; context for liquidity engine)
- April 2024: **dynamic/tiered CRR by loan-to-deposit ratio — L/D >55% ⇒ CRR 15%; 40–55% ⇒ 20%; <40% ⇒ 25%.** REPORTED (multiple: https://citinewsroom.com/2024/04/bogs-cash-reserve-ratios-hampering-private-sector-credit-analysts-warn/ ; GAB review paper: https://gab.com.gh/assets/images/docs/Cash-Reserve-Ratio-(CRR)-Regime.pdf ).
- June 2025 (124th MPC): CRR to be maintained **in the currency of the deposit** (FCY reserves for FCY deposits), effective 5 June 2025. REPORTED (https://www.ghanaweb.com/GhanaHomePage/business/BoG-amends-dynamic-Cash-Reserve-Ratio-framework-1985369).
- May 2026 (130th MPC): tiered structure **replaced by uniform 20% CRR on all deposits (cedi and FX), held in local currency, effective 4 June 2026**; policy rate held at 14%. REPORTED (https://www.newsghana.com.gh/bank-of-ghana-sets-uniform-20-reserve-ratio/ ; https://thebftonline.com/2026/05/21/bog-tightens-liquidity-rules-holds-policy-rate-at-14-as-oil-shock-risks-rise/).
- CRR return format/maintenance-period mechanics: **UNKNOWN** (not publicly documented; presumably via daily/weekly ORASS returns).

### 10.4 Liquidity Risk Management Directive (LRMD), 2026 — qualitative companion
**Source (CONFIRMED, primary):** https://www.bog.gov.gh/wp-content/uploads/2026/02/Liquidity-Risk-Management-Directive-Cleaned-11.12.25.pdf (31 pp., exposure draft, effective 1 Jan 2027, alignment by 31 Dec 2026).
- Basis: **Sections 36–38 of Act 930**; Basel Sound Principles + Basel III LCR & monitoring tools (Jan 2013) + Intraday tools (Apr 2013). Preamble states (verbatim): "This directive is complemented by the Liquidity Monitoring Tools Directive and **Liquidity Coverage Ratio Directive (applicable to banks only)** which when implemented as a package, provides a comprehensive qualitative and quantitative perspective…". CONFIRMED.
- **Quarterly Liquidity Adequacy Statement (LAS)** from the Board to BoG, supported by an **Internal Liquidity Adequacy Assessment Process (ILAAP)**, outcome embedded in the annual ICAAP report (¶12). CONFIRMED.
- **FTP requirement (relevant to AequorOS FTP module, verbatim ¶78–79):** "An RFI shall incorporate liquidity costs, benefits and risks into its internal pricing, performance evaluation and new product approval processes…"; "RFIs shall incorporate liquidity costs and benefits into their **internal funds transfer pricing program**. This program shall charge business lines for the cost of funding all significant activities based on the liquidity consumed, while also crediting business lines that generate liquidity at a cost lower than the RFI's funding rate." Footnote 12: "This will initially apply to **Banks ONLY**. SDIs are however encouraged to build capacity…". CONFIRMED.
- 3-year Board-approved funding strategy, reviewed annually (¶80). Intraday liquidity management section applies to **banks only** (¶84–86). CFP requirements incl. BoG notification on CFP activation/de-escalation (¶74). Liquidity stress testing program with institution-specific/market-wide/combined scenarios (¶41 ff.). CONFIRMED.
- **Disclosure (¶87–88):** annual public disclosure (governance, risk appetite, quantitative measures incl. liquid-asset stock composition/size, internal ratios, limit policies, stress-test overview) on website + in Audited Financial Statements, submitted to BoG by **31 March** of ensuing year. CONFIRMED.
- No quantitative return templates in this directive (templates live in LMTD). CONFIRMED-by-absence.

### 10.5 Sectoral credit / other BSD returns (deprioritised per scope)
- Sectoral credit distribution, NPL, and related returns exist within the ORASS pack (42 returns) — layouts **UNKNOWN**. Regulatory measures on NPLs: Notice BG/GOV/SEC/2025/23 (https://www.bog.gov.gh/wp-content/uploads/2025/08/NOTICE-NO.-BG-GOV-SEC-2025-23-REGULATORY-MEASURES-TO-REDUCE-NON-PERFORMING-LOANS-IN-BANKS-SDIs-AND-NBFIs.pdf, CONFIRMED existence, not analysed).
- Recovery Planning Directive — Exposure Draft, Feb 2026: https://www.bog.gov.gh/wp-content/uploads/2026/02/Recovery-Planning-Directive-Exposure-Draft-12-2-26-clean.pdf (CONFIRMED existence, not analysed).

---

## 11. Common metadata & conventions (cross-return)

| Convention | Evidence | Confidence |
|---|---|---|
| Reporting currency/unit | Templates headed **"Amounts in GHS '000"** (LMTD Tables 1–2; Stress Tables 1–5); LMTD Table 9 "Value in Cedi ('000)"; LMTD Table 11 "Values in Cedi" | CONFIRMED |
| Sign convention | Expenses/deductions shown in parentheses `(XXX)` (Stress Tables 2–3); mismatch defined as Assets − Liabilities (LMTD Table 6: "4 = (2−3)"); NOP short positions negative (0% to −10%) | CONFIRMED |
| Comparative columns | LMTD Table 1: Reporting Month vs Previous Month; IRRBB Table 8: T vs T−1 | CONFIRMED |
| Solo vs consolidated | CRD: CAR "on a standalone and a consolidated basis" (standalone licensed entity + consolidated per Act 930 s.31); IRRBB reports at same entity level as ICAAP | CONFIRMED |
| Deadline pattern | Monthly liquidity pack ≤9 days after month end; IRRBB quarterly ≤9 days after quarter end; DBK daily by 10:00 next business day; ICAAP annual ≤3 months after year-end; disclosures by 31 March | CONFIRMED |
| Channel | ORASS exclusive for DBK (email fallback only on downtime); ORASS is BoG's consolidated prudential-return portal | CONFIRMED (DBK) / REPORTED (rest of pack) |
| Counterparty identifiers | **"Counterparty TIN/ Ghana Card No."** used as the identity key in all LE templates; "Unique ID" per connected group | CONFIRMED |
| Attestation/governance | ICAAP submitted with Board resolutions + Senior Management reports; LAS is a Board quarterly statement; Board approves monitoring-tool thresholds annually; stress results Board-challenged | CONFIRMED |
| Classification taxonomies | "IFRS Staging" + "Classification as per BOG Prudential Norms" side-by-side (LE templates); exposure classes per CRD Part 2 (stress Table 1) | CONFIRMED |
| Capital base for limits | Net Own Funds (NOF) is the LE/NOP denominator; Tier 1 reported alongside; CET1 the buffer currency | CONFIRMED |
| Institution codes in returns | ORASS institution-code scheme | UNKNOWN (not public) |
| Penalties for reporting failures | Act 930 s.93(3): ≤500 penalty units + 50/day; NOP misreporting: ss.41(4), 76(1), 93(3) | CONFIRMED |

---

## 12. Gap list — what must be simulated/approximated (clearly labelled)

| # | Gap | What is known | What to do in AequorOS |
|---|---|---|---|
| G1 | **LCR Directive, 2026 (banks only)** — full text: minimum ratio, runoff rates, inflow caps, HQLA haircuts/composition caps | Existence CONFIRMED by name in LMTD ¶4 & LRMD preamble; HQLA Level 1/2A/2B row structure CONFIRMED via LMTD Table 11; "LCR promotes resilience against a short but severe period of liquidity stress" (30-day Basel context) | Build LCR engine on Basel III January-2013 standard parameters, **flagged as Basel-default, pending BoG calibration**; keep runoff-rate table config-driven |
| G2 | **NSFR** | No BoG directive/return exists publicly; only preamble mentions | Ship Basel-default NSFR as optional module, flagged not-yet-required in Ghana |
| G3 | **ORASS CAR return form** (rows/columns) | CRD computation rules CONFIRMED; stress Tables 2 & 5 give component taxonomy | Model CAR return from CRD Part 1–4 + stress-table taxonomy; label layout as reconstructed |
| G4 | **Monthly BSD prudential pack** (balance sheet, P&L, liquidity forms) layouts | ORASS 42-returns/250-forms REPORTED; stress Table 4 foreign/domestic asset taxonomy CONFIRMED as proxy | Use stress Table 4 taxonomy as canonical BS dimensions; obtain forms from a pilot bank under NDA |
| G5 | **DBK 102/300/400/700 layouts** | Codes + purposes CONFIRMED (contingents; FX trades) | Model daily NOP return as: per-currency net asset/trading/contingent positions + NOF %; label columns as reconstructed |
| G6 | **CRR maintenance mechanics/return** | Ratios & history REPORTED/CONFIRMED (uniform 20% from 4 Jun 2026, held in cedi) | Treat as parameter of the liquidity forecast, not a return artifact |
| G7 | IRRBB **quarterly pilot return grid** beyond Table 8 | ¶10 points to Appendix IV templates | Implement Table 8 (ΔEVE/ΔNII×T/T−1 + Tier 1) per currency; extensible if final guideline adds a repricing-gap schedule (none published in the draft) |
| G8 | Market-related monitoring tool submission format (LMTD ¶46 items i–iv) | Data items CONFIRMED; no template published | Free-form data extract; keep as ad-hoc report generator |
| G9 | Institution/entity code conventions in ORASS | UNKNOWN | Configurable reporting-entity metadata |
| G10 | Final (post-consultation) versions of the Feb-2026 exposure drafts | Comments closed 30 Jun 2026; finals expected before 1 Jan 2027 effect date | Re-check bog.gov.gh reg_directives feed after Q3 2026; treat all Feb-2026 numbers as draft-subject-to-change |

---

## 13. Source register (primary documents on disk)

Downloaded and text-extracted to scratchpad during research (all fetched 2026-07-16 from bog.gov.gh over HTTPS):

| Doc | URL |
|---|---|
| LMTD 2026 (exposure draft) | https://www.bog.gov.gh/wp-content/uploads/2026/02/Liquidity-Monitoring-Tools-Directive-Cleaned-9.2.26.pdf |
| LRMD 2026 (exposure draft) | https://www.bog.gov.gh/wp-content/uploads/2026/02/Liquidity-Risk-Management-Directive-Cleaned-11.12.25.pdf |
| IRRBB Guideline (exposure draft) | https://www.bog.gov.gh/wp-content/uploads/2026/02/Exposure_Draft_Guidelines-on-the-Management-and-Measurement-of-Interest-Rate-Risk-in-the-Banking-Book-_February-2026.pdf |
| ICAAP Guideline (clean, Feb 2026) | https://www.bog.gov.gh/wp-content/uploads/2026/02/Guideline-on-ICAAP-12-2-26-clean.pdf |
| Stress Testing Guideline (exposure draft) | https://www.bog.gov.gh/wp-content/uploads/2026/02/EXPOSURE-Draft-Directive-on-Stress-Testing_FEBRUARY-2026.pdf |
| CRD 2018 (final) | https://www.bog.gov.gh/wp-content/uploads/2022/05/Basel-II-BOG-CRD-Final-27-June-2018-Basel-Committee-BSD.pdf |
| Large Exposures Directive (final, Sep 2025) | https://www.bog.gov.gh/wp-content/uploads/2025/09/Large-Exposures-Directive-September-2025.pdf |
| LE Explanatory Notes | https://www.bog.gov.gh/wp-content/uploads/2025/09/Large-Exposures-Directive-Explanatory-Notes.pdf |
| Credit Concentration Guidelines (final, Sep 2025) | https://www.bog.gov.gh/wp-content/uploads/2025/09/Guidelines-on-Measurement-and-Management-of-Credit-Concentration-Risk.pdf |
| Revised NOP Directive BG/FMD/2026/07 (final) | https://www.bog.gov.gh/wp-content/uploads/2026/02/BG-FMD-2026-07-Revised-Directive-on-Foreign-Exchange-FX-PositionLimits-1.pdf |
| Act 930 (2016) | https://www.bog.gov.gh/wp-content/uploads/2019/09/BANKS-AND-SPECIALISED-DEPOSIT-ACT-2016.pdf |
| Recovery Planning Directive (exposure draft, existence only) | https://www.bog.gov.gh/wp-content/uploads/2026/02/Recovery-Planning-Directive-Exposure-Draft-12-2-26-clean.pdf |

Full BoG `reg_directives` inventory (70 posts) enumerated via `https://www.bog.gov.gh/wp-json/wp/v2/reg_directives?per_page=100` on 2026-07-16 — **no LCR Directive, NSFR, or leverage-ratio-specific post exists** as of that date.
