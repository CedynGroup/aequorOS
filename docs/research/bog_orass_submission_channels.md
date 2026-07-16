# Bank of Ghana Regulatory Submission Channels — ORASS and the Wider Return Landscape

**Research date:** 2026-07-16
**Purpose:** Ground AequorOS's Ghana submission architecture in the verifiable public record.
**Confidence legend:**
- **CONFIRMED** — official/primary source (Bank of Ghana document or website, Act of Parliament, the system vendor's own announcement, or direct observation of a BoG-operated endpoint).
- **REPORTED** — secondary source (press, third-party vendor marketing, legal commentary).
- **INFERRED** — reasoning from confirmed/reported facts; explicitly not in the public record.
- **UNKNOWN** — could not be established from public sources. **The build must simulate these parts and label the simulation.**

> **Anti-hallucination note:** No public API endpoint URLs, field names, payload schemas, or credential mechanics for ORASS exist anywhere in the public record surveyed. Nothing of that kind is stated in this document, and none should be invented in code. Where the record runs out it is marked UNKNOWN.

---

## 1. Executive summary

1. **ORASS is real, mandatory, and central.** ORASS — expanded by BoG itself as the *Online Regulatory Analytic Surveillance System* (2023 Annual Report) and elsewhere as *Online Regulatory and Analytical Surveillance Software* — is the Bank of Ghana's supervisory data-collection and licensing platform, live at `https://orassportal.bog.gov.gh/`. It was procured from Vizor Software (Ireland; acquired by Regnology) under a July 2019 contract, deployed roughly 2019–2022, and BoG "completed the onboarding of all RFIs onto ORASS" in 2023 (BoG 2023 Annual Report). [CONFIRMED]
2. **ORASS is the official channel; email is a downtime-only fallback.** BoG Notice BG/FMD/2026/07 (10 Feb 2026): "Daily Bank Returns shall be submitted exclusively through the ORASS platform which serves as the official reporting channel. Email submissions shall be accepted only in the event of ORASS system downtime… reports … uploaded to ORASS once system functionality is restored for the submission to be deemed complete." [CONFIRMED]
3. **An ORASS submission API exists but is not publicly documented.** The vendor's Phase-1 go-live comprised "Vizor Regulatory Returns (VRR) and the Vizor API Service – Submit (VAS)"; Regnology states "regulated entities can now upload their data automatically via API." Glydetek Group (Accra fintech) markets a connector ("authentication, return data submission and tracking of submission process status"). **No endpoint spec, auth model, format schema, or developer portal is public.** [Existence: CONFIRMED (vendor) / All mechanics: UNKNOWN]
4. **The consolidated return set is large:** 42 returns, 250+ forms, 400+ validation rules across BoG's supervisory departments (Regnology case study). The itemised list of the 42 returns is not public. [REPORTED / list UNKNOWN]
5. **Deadline culture is tight and codified per-directive:** daily FX/NOP returns (DBK) by 10:00 a.m. next business day; monthly liquidity-monitoring returns within 9 days of month-end; quarterly IRRBB within 9 days of quarter-end; monthly large-exposures templates; annual ICAAP within 3 months of year-end. [CONFIRMED, per directive]
6. **Penalties are statutory and quantifiable:** Act 930 s.93(3) — up to 500 penalty units on the institution *and* responsible key management personnel for non/incomplete/delayed/inaccurate submission, plus 50 units per day continuing; s.92(8) — 2,000–10,000 units for contravening a directive; one penalty unit = GH¢12. [CONFIRMED]
7. **For AequorOS:** build a clearly-labeled **ORASS simulator** behind a channel-abstraction seam (real integration requires BoG/Regnology-issued specs and credentials that are not public), a **guided email-fallback workflow** that enforces the "re-upload when restored" rule, and **artifact generation** (per-directive Excel/CSV templates reproduced from directive appendices) for manual portal upload.

---

## 2. ORASS profile

### 2.1 What it is, who runs it, since when

| Fact | Detail | Confidence | Source |
|---|---|---|---|
| Name | Online Regulatory Analytic(s) Surveillance System (BoG's own expansion); also rendered "Online Regulatory and Analytical Surveillance Software" | CONFIRMED | BoG 2023 Annual Report, abbreviations list + §on supervision — https://www.bog.gov.gh/wp-content/uploads/2024/05/Bank-of-Ghana-2023-Annual-Report-and-Financial-Statements.pdf ; Glydetek — https://glydetek.com/orass-api/ |
| Operator | Bank of Ghana; portal at `https://orassportal.bog.gov.gh/` (linked from bog.gov.gh footer as "ORASS Registration Portal") | CONFIRMED | https://orassportal.bog.gov.gh/ ; https://www.bog.gov.gh/supervision-regulation/banking-supervision/ (footer quick-links) |
| Vendor/platform | Vizor Software, contract awarded July 2019 after public tender; Vizor later acquired by Regnology — platform now branded "Regnology SupTech Platform" | CONFIRMED (vendor announcement) | https://www.regnology.net/en/resources/news/vizor-achieves-technical-go-live-with-bank-of-ghana/ ; https://www.regnology.net/en/resources/insights/integrated-financial-supervision-system-supports-bank-of-ghana-reforms/ ; contract news also at https://presswire.com/content/4305/bank-ghana-awards-vizor-contract-fully-integrated-financial-supervision-system |
| Deployment window | ~2019–2022 phased ("Technical Go Live" Phase 1 = Vizor Regulatory Returns + Vizor API Service – Submit); REGTECH AFRICA dates implementation 2019–2022 | REPORTED | Regnology go-live note (above); https://regtechafrica.com/ghana-bank-of-ghanas-suptech-advances-supervisory-capabilities/ |
| Full onboarding | "In 2023, the Bank completed the onboarding of all RFIs onto its Online Regulatory Analytic Surveillance System (ORASS)… banks, credit institutions, FinTechs and other payment service providers." | CONFIRMED | BoG 2023 Annual Report (p. section on regulation & supervision), URL above |
| Scope of data model | 42 consolidated Returns, 250+ forms, 400+ validation rules; 103 KRIs across 7 risk areas; 105 onsite control questions; 44 Power BI reports | REPORTED (vendor case study) | https://www.regnology.net/en/resources/insights/integrated-financial-supervision-system-supports-bank-of-ghana-reforms/ |
| Uses beyond returns | Licensing/authorisation workflows (e.g., Digital Credit Services Provider licence applications "shall apply to the Bank of Ghana through its Online Regulatory Analytics Surveillance System (ORASS)"), monetary-policy and FX-surveillance data | CONFIRMED (licensing) / REPORTED (other) | https://www.bog.gov.gh/wp-content/uploads/2025/09/Licensing-Requirement_Digital-Credit-Services-Provider-Updated_04_09_25-NOTICE-30.pdf ; https://www.bog.gov.gh/notice/notice-orass-training-for-the-application-of-digital-credit-services-providers-dcsp-license/ ; REGTECH AFRICA (above) |
| Who must use it | All Regulated Financial Institutions (RFIs): banks, SDIs, credit institutions, FinTechs/PSPs (onboarding statement above). Daily Bank Returns mandated exclusively via ORASS for all banks | CONFIRMED | BoG 2023 Annual Report; BG/FMD/2026/07 — https://www.bog.gov.gh/wp-content/uploads/2026/02/BG-FMD-2026-07-Revised-Directive-on-Foreign-Exchange-FX-PositionLimits-1.pdf |
| Mandate date for returns | No single public "ORASS mandate" notice was located. Practical mandate is per-directive (e.g., BG/FMD/2026/07 formalises ORASS-exclusivity for DBK) plus the 2023 completion of onboarding. Glydetek says the requirement to submit via the platform dates to its 2020 introduction | CONFIRMED (2026 directive; 2023 AR) / REPORTED (2020 introduction) / UNKNOWN (any 2020-22 mandating notice) | as above; https://glydetek.com/orass-api/ |

### 2.2 Direct observation of the portal (probed 2026-07-16)

- `GET https://orassportal.bog.gov.gh/` → HTTP 302 to `/ErrorPage.aspx?aspxerrorpath=/Default.aspx` for unauthenticated clients; ASP.NET (`.aspx`) stack; HSTS enabled; Content-Security-Policy allows `https://www.google.com/recaptcha/` (login page is reCAPTCHA-protected). [CONFIRMED — direct probe]
- Public registration form exists at `https://orassportal.bog.gov.gh/PublicForm/PublicForm.aspx` (linked from bog.gov.gh footer as "ORASS Registration Portal"); it also 302s without a session. Search-indexed title: "Public Registration - ORASS Portal - Bank of Ghana". [CONFIRMED — link + probe]
- Login-page field structure, institution-code conventions, MFA: **UNKNOWN** (page not retrievable without a session).

### 2.3 ORASS technical integration reality — confidence table

| Integration aspect | Public record | Confidence |
|---|---|---|
| API existence | Vendor Phase 1 included "Vizor API Service – Submit (VAS)"; Regnology: "regulated entities can now upload their data automatically via API… Financial institutions can integrate customized reporting applications" | CONFIRMED (vendor statements) — https://www.regnology.net/en/resources/news/vizor-achieves-technical-go-live-with-bank-of-ghana/ ; https://www.regnology.net/en/resources/insights/integrated-financial-supervision-system-supports-bank-of-ghana-reforms/ |
| Public API documentation / developer portal | None found: no docs on bog.gov.gh, no GitHub repos, no OpenAPI spec, no developer portal. openbankingtracker.com lists BoG with no published APIs of this kind | UNKNOWN (absence verified across searches) — e.g. https://www.openbankingtracker.com/provider/bank-of-ghana-gh/apis |
| Third-party connector: "Glydetek ORASS API" | Glydetek Group Ltd (Accra; info@glydetek.com, +233-501-419-780/783) "developed a connection to Bank of Ghana ORASS API endpoints allowing authentication, return data submission and tracking of submission process status"; features: "identification of total reports required to be submitted", "view institution's status with Bank of Ghana", "effortlessly generate and send due ORASS reports", "endpoint encryption provides secure data transmission". Marketing pages only; no docs, pricing, or named client banks | REPORTED — https://glydetek.com/orass-api/ ; https://glydetek.com/portfolio/orass-integration/ |
| Evidence of API use in the wild | LinkedIn post titled "Bank of Ghana ORASS API Submission" by a Ghanaian engineer (post ID timestamp decodes to ~March 2021) | REPORTED/INFERRED (date decoded from LinkedIn activity ID) — https://www.linkedin.com/posts/kwakuadade_bank-of-ghana-orass-api-submission-activity-6773451892740673536-WryA |
| Authentication model (tokens? institution codes? certs?) | Glydetek mentions "authentication" and "endpoint encryption" with zero detail. Portal login uses reCAPTCHA (observed). Everything else | **UNKNOWN — simulate** |
| Accepted file formats (Excel? XML? XBRL? web forms?) | Not published for BoG. The underlying Vizor/Regnology product line generically supports portal web-forms and machine-to-machine submission, and BoG's 400+ validation rules imply structured data, but the concrete format contract for ORASS returns is not public. No evidence of XBRL adoption in Ghana bank reporting was found | **UNKNOWN** (format) / INFERRED (structured templates with server-side validation exist) |
| Status / acknowledgement flow | Glydetek's connector claims "tracking of submission process status" and visibility of "total reports required" → implies the platform exposes an obligation calendar and per-submission status lifecycle. Names of statuses, ack artifacts (receipt IDs?), and rejection semantics | REPORTED (existence) / **UNKNOWN (semantics) — simulate** |
| Institution onboarding / credentials issuance | Public registration form exists for licence applicants; how reporting institutions receive ORASS credentials (and any API credentials) | **UNKNOWN — simulate** |
| Downtime protocol | Email accepted only during ORASS downtime; submission "deemed complete" only after re-upload to ORASS post-restoration | CONFIRMED — BG/FMD/2026/07 (URL above) |

---

## 3. Channel-by-channel matrix

| Channel | Endpoint | What flows through it | Formats | Confidence / source |
|---|---|---|---|---|
| **ORASS portal (interactive)** | https://orassportal.bog.gov.gh/ | The consolidated supervisory return set (42 returns / 250+ forms) for all RFIs; licensing applications (e.g. DCSP); Daily Bank Returns (DBK) for banks | Portal-native forms/uploads; concrete file formats UNKNOWN | CONFIRMED channel — BoG 2023 AR; BG/FMD/2026/07; DCSP Notice 30 (URLs above) |
| **ORASS API (machine-to-machine)** | Not public | Automated return submission + status tracking (vendor + Glydetek statements) | UNKNOWN | CONFIRMED existence / UNKNOWN mechanics — Regnology go-live; glydetek.com/orass-api |
| **Email — downtime fallback** | Address for DBK fallback not stated in the notice → UNKNOWN | Daily Bank Returns during ORASS downtime only; must re-upload to ORASS after restoration | Presumably the same report files (format UNKNOWN) | CONFIRMED policy — BG/FMD/2026/07 |
| **Email — consultation & correspondence (BSD)** | `bsdletters@bog.gov.gh` | Comments on exposure drafts (Liquidity Monitoring Tools Directive 2026, Recovery Planning Directive 2026, NPL notice, Non-Interest Banking Guideline, ICAAP Guideline) | Free-form / documents | CONFIRMED — e.g. LMT Directive 2026 front-matter: "All comments shall be sent … via email at bsdletters@bog.gov.gh by 30th June 2026" — https://www.bog.gov.gh/wp-content/uploads/2026/02/Liquidity-Monitoring-Tools-Directive-Cleaned-9.2.26.pdf |
| **Email — BSD general** | `bsd@bog.gov.gh` | Reported as the Banking Supervision Department address for e.g. corporate-governance annual certifications; **not found in a primary BoG PDF during this survey** | Documents | REPORTED — secondary analysis of BoG CG directives: https://studylib.net/doc/25710212/bog-directives ; search summaries. Treat as configurable, verify with BoG |
| **Email — cyber/info-security** | `information.security@bog.gov.gh` | Comments on the 2025 Cyber & Information Security Directive exposure draft (and plausibly incident correspondence) | Documents | CONFIRMED (comments use) — https://www.bog.gov.gh/wp-content/uploads/2025/09/CISD-Exposure-Draft.pdf |
| **Email — General Secretary** | `bogsecretary@bog.gov.gh` | BoG's published general contact (site-wide footer) | Correspondence | CONFIRMED — https://www.bog.gov.gh/contact-us/ |
| **PSA Returns portal (separate from ORASS)** | http://psdc.bog.gov.gh/ ("Bank of Ghana (BoG) - PSA Returns Portal") | Payment Systems and Services Act (Act 987) returns for PSPs/EMIs | UNKNOWN | CONFIRMED existence (bog.gov.gh footer quick-link "PSA Returns"; indexed title) — https://www.bog.gov.gh/supervision-regulation/banking-supervision/ ; https://psdc.bog.gov.gh/ (unreachable from research location; not probed successfully) |
| **BOG Oracle Portal** | http://ois.bog.gov.gh:8040/ | Unknown function (footer quick-link; likely internal/vendor ops) | UNKNOWN | CONFIRMED link exists / purpose UNKNOWN — bog.gov.gh footer |
| **Credit-bureau data submission (not to BoG)** | To licensed Credit Reference Bureaus | Monthly credit data incl. written-off/defaulter reporting; BoG prescribes the data formats (Business/Consumer Credit, Dishonoured Cheque, Judgment formats downloadable) | Prescribed data-format files (downloadable specs) | CONFIRMED — https://www.bog.gov.gh/supervision-regulation/fsd/credit-reporting-data-formats/ ; NPL Notice BG/GOV/SEC/2025/23 §14 — https://www.bog.gov.gh/wp-content/uploads/2025/08/NOTICE-NO.-BG-GOV-SEC-2025-23-REGULATORY-MEASURES-TO-REDUCE-NON-PERFORMING-LOANS-IN-BANKS-SDIs-AND-NBFIs.pdf |
| **Documents/letters (hard copy or unspecified)** | BoG, The Bank Square, 42 Castle Road, Ridge, Accra | ICAAP annual report + Board resolutions; Board-approved remediation plans (large-exposure breaches, NPL plans); recovery plans; group organograms (Act 930 s.42, twice yearly). Channel not specified in the directives → institution practice varies | PDF/letter | CONFIRMED obligations / channel per return UNKNOWN — ICAAP Guideline (below); Large Exposures Directive; Act 930 |

---

## 4. Return families (operational view)

### 4.1 Daily Bank Returns (DBK) — FX/treasury
- All banks submit **Daily Bank Returns (DBK)**; "Reports for each working day shall be submitted no later than 10:00 a.m. on the following business day," exclusively via ORASS. [CONFIRMED — BG/FMD/2026/07]
- Named forms in the public record: **DBK 102** (all contingents incl. oil/non-oil LCs), **DBK 300, DBK 400, DBK 700** (net FX trades that must reconcile daily NOP movement). Full DBK catalogue: UNKNOWN. [CONFIRMED names — BG/FMD/2026/07, pp.1–2]
- NOP rules: single-currency 0% to −10% of Net Own Funds (no long USD/GBP/EUR/other positions), aggregate ≤20% NOF; unexplained NOP movement = misreporting sanctioned under Act 930 ss.41(4) & 76(1). [CONFIRMED — same]

### 4.2 Prudential/BSD return set
- The pre-ORASS BSD form set was consolidated into ORASS's **42 returns / 250+ forms**; the individual return names are not public. [REPORTED — Regnology case study; itemised list UNKNOWN]
- Reporting frequencies span daily/weekly/monthly/quarterly/annual (per-directive; the monthly "within 9 days" convention below). No single public master calendar was found. [INFERRED from directives / UNKNOWN master list]
- CAR under the Capital Requirements Directive (Basel II/III, June 2018) is calculated "on a standalone and a consolidated basis"; banks must additionally submit annual 3-year capital plans, AT1/T2 issuance documentation, and the Market Risk File (MRF). [CONFIRMED — https://www.bog.gov.gh/wp-content/uploads/2022/05/Basel-II-BOG-CRD-Final-27-June-2018-Basel-Committee-BSD.pdf]

### 4.3 Liquidity package (exposure drafts, Feb 2026 — comment deadline 30 June 2026)
- **Liquidity Monitoring Tools Directive, 2026** (banks, S&Ls, finance houses, finance & leasing, FHCs): monthly reports on six liquidity-monitoring metrics **"not later than 9 days after the last day of each month"** per Appendix templates; four BoG prudential liquidity ratios (Liquid Assets/Volatile Liabilities, Liquid Assets/Short-Term Liabilities, …) are monitoring tools for banks but *binding* ratios for SDIs. Sanctions: Act 930 ss.40 & 92(8). [CONFIRMED — https://www.bog.gov.gh/wp-content/uploads/2026/02/Liquidity-Monitoring-Tools-Directive-Cleaned-9.2.26.pdf]
- **Liquidity Risk Management Directive, 2026** (qualitative framework; annual board review; website disclosure + submission to BoG; sanctions ss.40 & 92(8)). [CONFIRMED — https://www.bog.gov.gh/wp-content/uploads/2026/02/Liquidity-Risk-Management-Directive-Cleaned-11.12.25.pdf]
- **Liquidity Coverage Ratio Directive (banks only)** — referenced in the LMT preamble as part of the package; the LCR directive PDF itself was not located during this survey. [CONFIRMED existence of reference / document UNKNOWN]

### 4.4 IRRBB (exposure draft, Feb 2026)
- Guideline on Management & Measurement of IRRBB (banks, S&Ls, finance houses, finance & leasing, FHCs). Effective **1 Jan 2027**; RFIs **pilot for one year from publication, submitting quarterly reports in the Appendix IV templates**; thereafter quarterly IRRBB reports **within 9 days after the ensuing quarter**; annually, IRRBB reported as part of ICAAP (standardised ΔEVE/ΔNII/EaR scenarios; internal-model results also reported via ICAAP). Website disclosure required. [CONFIRMED — https://www.bog.gov.gh/wp-content/uploads/2026/02/Exposure_Draft_Guidelines-on-the-Management-and-Measurement-of-Interest-Rate-Risk-in-the-Banking-Book-_February-2026.pdf]

### 4.5 Large exposures
- **Large Exposures Directive** issued September 2025 (exposure draft Dec 2024) for banks, S&Ls, finance houses, FHCs; implementation from **1 January 2026**. [CONFIRMED — https://www.bog.gov.gh/wp-content/uploads/2025/09/Large-Exposures-Directive-Explanatory-Notes.pdf ; draft: https://www.bog.gov.gh/wp-content/uploads/2024/12/Exposure-Draft-Large-Exposures-Directive.pdf]
- **Monthly** regulatory reporting on five templates: Template 1 (exposures ≥10% NOF), 1a (connected-counterparty details), 2 (top-100 exposures), 3 (exempted exposures ≥10% NOF), 4 (other exposures ≥10% NOF pre-CRM). Breaches: report to BoG + Board-approved remediation plan (draft wording: plan within 30 days; breach report within 10 working days per NPL-adjacent notices). [CONFIRMED — draft PDF, Part VI]

### 4.6 ICAAP
- **Guideline on ICAAP (exposure draft, Feb 2026):** annual ICAAP **report + Board resolutions + senior-management comments** submitted **"no later than three months after the year-end"**, forward-looking from 31 Dec status; updated ICAAP on material change or on BoG demand; capital adequacy assessed at **solo and consolidated** levels; results published on the RFI's website and "submit[ted] … to BOG by 31st March of the ensuing year". Standard submission format is a stated objective of the guideline. [CONFIRMED — https://www.bog.gov.gh/wp-content/uploads/2026/02/Guideline-on-ICAAP-Exposure-Draft.pdf]
- ICAAP is a **document-style annual submission** (report + governance evidence), not a data return; its transport channel (ORASS document upload vs letter/email) is UNKNOWN.

### 4.7 NPL / credit-risk measures (Notice BG/GOV/SEC/2025/23)
- Monthly written-off-defaulter lists to BoG's **Financial Stability Department** and all Credit Reference Bureaus; RFIs with NPL >7% submit Board-approved reduction plans; breach reporting within 10 working days + Board plan within 30 days. [CONFIRMED — https://www.bog.gov.gh/wp-content/uploads/2025/08/NOTICE-NO.-BG-GOV-SEC-2025-23-REGULATORY-MEASURES-TO-REDUCE-NON-PERFORMING-LOANS-IN-BANKS-SDIs-AND-NBFIs.pdf]

### 4.8 Governance & other periodic obligations
- Corporate Governance Directive 2018: annual board certification of compliance in the annual report; separate assessments (e.g. AML/CFT, external evaluation results) "submitted to the Bank of Ghana". [CONFIRMED — https://www.bog.gov.gh/wp-content/uploads/2019/09/CGD-Corporate-Governance-Directive-2018-Final-For-PublicationV1.1.pdf]
- Group organisational structure at least **twice per year** (Act 930 s.42). [CONFIRMED — Act 930]
- DFIs (separate class): periodic returns "not exceed[ing] ten (10) calendar days after the end of the period" (Prudential Requirements for DFIs, exposure draft). [CONFIRMED — https://www.bog.gov.gh/wp-content/uploads/2022/05/Prudential-Requirements-for-DFIs-Exposure-Draft.pdf]

---

## 5. Deadlines & penalties

### 5.1 Statutory base — Banks and Specialised Deposit-Taking Institutions Act, 2016 (Act 930)
Source: https://www.bog.gov.gh/wp-content/uploads/2019/09/BANKS-AND-SPECIALISED-DEPOSIT-ACT-2016.pdf [CONFIRMED, quoted from the Act text]

- **s.93(1)–(2)** — BoG may require any information/data from a bank, SDI, financial holding company or group member, and prescribes the details, **form**, and **period** of reporting.
- **s.93(3)** — administrative penalty **up to 500 penalty units** on the institution **and on the responsible key management personnel** for **(a) non-submission; (b) incomplete submission; (c) delayed submission; or (d) inaccurate submission**, **plus 50 penalty units per day** the default continues.
- **s.92(8)** — contravening a BoG directive: **2,000–10,000 penalty units** (plus s.92(9) further remedial action).
- **s.41(4)** — NOP non-compliance: **0.5 per mille of the deficiency per day**.
- **s.76(2)** — breach of FX-business restrictions: penalty **up to 120% of the gross income** from the business.
- **s.80(1)** — annual audited financial statements **on a solo and a consolidated basis**.
- Penalty-unit value: **GH¢12.00 per unit** (Fines (Penalty Units) Act, 2000 (Act 572), value amendable by LI). So s.93(3) ≈ up to GH¢6,000 + GH¢600/day; s.92(8) ≈ GH¢24,000–120,000. [CONFIRMED unit-value — https://gra.gov.gh/domestic-tax/tax-offences-and-penalties/ ; Act 572 — http://www.commonlii.org/gh/legis/num_act/fua2000a572254.pdf]

### 5.2 Deadline table (public record)

| Return | Frequency | Deadline | Source (all CONFIRMED) |
|---|---|---|---|
| Daily Bank Returns (DBK incl. NOP) | Daily (working days) | **10:00 a.m. following business day**, via ORASS only | BG/FMD/2026/07 |
| Liquidity monitoring metrics | Monthly | **≤9 days after month-end** | LMT Directive 2026 (draft) ¶7 |
| IRRBB (Appendix IV) | Quarterly | **≤9 days after quarter-end** (post-pilot; effective 1 Jan 2027) | IRRBB Guideline 2026 (draft) ¶11, ¶55 |
| Large exposures Templates 1/1a/2/3/4 | Monthly | Monthly reporting required; exact day-count not stated in draft | LE Directive (draft Part VI) |
| ICAAP report | Annual | **≤3 months after year-end**; website disclosure + submission **by 31 March** | ICAAP Guideline 2026 (draft) ¶72, ¶82 |
| Written-off defaulters list | Monthly | To Financial Stability Dept + CRBs | Notice BG/GOV/SEC/2025/23 §14 |
| Exposure-limit breach | Event-driven | Report within **10 working days**; Board plan within **30 days** | Notice BG/GOV/SEC/2025/23 |
| Group organogram | Twice yearly | Times prescribed by BoG | Act 930 s.42 |
| Audited financial statements | Annual | Solo + consolidated | Act 930 s.80 |
| DFI periodic returns | Periodic | **≤10 calendar days after period-end** | DFI Prudential Requirements (draft) |

- Directive-level penalty clauses repeat the statutory anchors, e.g. BG/FMD/2026/07: "Any inaccurate, incomplete, delayed submissions and/or non-submission of reports shall attract sanctions as provided in Section 93(3) and Section 41(4) of Act 930"; the liquidity/IRRBB drafts cite ss.40 & 92(8). [CONFIRMED]
- A generic "monthly prudential returns by Nth working day" rule for banks (pre-ORASS folklore) could not be pinned to a public document — Act 930 s.93(2) delegates the period to BoG per return. The observable convention in current directives is **9 days** for monthly/quarterly data returns. [UNKNOWN historical rule / CONFIRMED current per-directive deadlines]

---

## 6. Solo vs consolidated reporting

- **Act 930 s.91(1):** BoG supervises "(a) banks and SDIs on a solo basis; (b) financial holding companies on a solo basis; and (c) financial groups on a consolidated basis." [CONFIRMED]
- **Act 930 ss.30/37 (headings):** capital adequacy and liquid-asset requirements applicable **on a consolidated basis** to groups; **s.80:** annual audited financials on **both** solo and consolidated bases. [CONFIRMED]
- **CRD 2018:** "CAR shall be calculated on a standalone and a consolidated basis," applied to all subsidiaries of the licensed entity. [CONFIRMED]
- **ICAAP Guideline 2026:** capital adequacy at "both the solo and consolidated levels"; foreign-subsidiary information must be obtainable for consolidation. [CONFIRMED]
- **ORASS supports both views:** vendor cites "enhanced monitoring and supervision on both a standalone and consolidated basis." [REPORTED — Regnology case study]
- Practical upshot for AequorOS: every generated return artifact needs an explicit **basis dimension (SOLO | CONSOLIDATED)** and an entity-hierarchy model (bank vs FHC vs group), since both bases are simultaneously live obligations.

---

## 7. Portal modernization & SupTech news, 2024–2026

- **BoG 2023 Annual Report:** full RFI onboarding completed; BoG shared SupTech experience with central banks of Barbados, Saudi Arabia, Zambia, Egypt, Solomon Islands. (The 2024 Annual Report contains **zero** ORASS mentions — no reversal reported, just no news.) [CONFIRMED — both annual reports]
- **Cambridge SupTech Lab collaboration:** BoG is a named project partner (with OJK, Proto, Winnow) on an AI-powered, chatbot-supported **financial consumer protection suite** — extends ORASS-era SupTech into conduct supervision. [REPORTED — https://cambridgesuptechlab.org/financial-consumer-protection-suite-with-next-generation-ai-powered-chatbot-supported-complaints/ ; https://regtechafrica.com/ghana-bank-of-ghanas-suptech-advances-supervisory-capabilities/]
- **FICSOC** (Financial Industry Command Security Operations Centre) — industry-wide cyber-resilience monitoring. [REPORTED — REGTECH AFRICA]
- **Draft Open Banking Directive** (20 Dec 2024) — API-first ecosystem direction for the industry (customer-data APIs, not supervisory returns). [CONFIRMED — https://www.bog.gov.gh/wp-content/uploads/2024/12/Draft-Open-Banking-Directive-for-Regulated-Financial-Institutions-201224.pdf]
- **Feb 2026 directive wave** (Liquidity package, IRRBB, ICAAP Guideline, Recovery Planning Directive, revised NOP directive) — actively standardising templates and codifying ORASS as the exclusive channel; consultation via bsdletters@bog.gov.gh. [CONFIRMED — bog.gov.gh PDFs cited above]
- **XBRL:** no evidence of XBRL adoption for Ghanaian bank regulatory returns was found in any source surveyed. [UNKNOWN/absent]
- **Vendor trajectory:** Vizor acquired into Regnology; BoG's platform is now maintained under the Regnology brand — any future format/API evolution likely tracks Regnology's product line. [REPORTED — regnology.net]

---

## 8. Recommended AequorOS submission architecture

Given the confirmed/unknown split, the design that maximises real-world fidelity without inventing anything:

### 8.1 Channel abstraction (the seam)
- `SubmissionChannel` enum: `ORASS_API_SIMULATED`, `ORASS_PORTAL_MANUAL`, `EMAIL_FALLBACK`, `OTHER_PORTAL` (PSA returns), `DOCUMENT_DISPATCH` (ICAAP/plans).
- Per-return routing table keyed by return family (DBK daily, liquidity monthly, IRRBB quarterly, LE monthly, ICAAP annual…), each carrying: frequency, deadline rule, basis (solo/consolidated), statutory penalty reference (for the deadline-risk UI), and channel.
- Mirror the market-data adapter discipline already in this repo (single writer, vendor-error classification, no raw vendor leakage): one `submission_runner` owns all submission-state writes.

### 8.2 Simulated ORASS client (clearly labeled)
- **Everything below the transport line is a simulator** — banner it in UI and code (`SIMULATED — no public BoG API contract exists; production integration requires BoG/Regnology-issued specifications and credentials`).
- Simulate only behaviors evidenced publicly: (a) obligation calendar ("reports required to be submitted"), (b) submit return, (c) validation verdict (accept/reject against locally-authored rules mirroring the "400+ validation rules" concept), (d) submission-status lifecycle (e.g. DRAFT → SUBMITTED → VALIDATED/REJECTED → ACKNOWLEDGED — names are ours, marked as such), (e) downtime flag to exercise the fallback path.
- Do **not** fabricate endpoint paths, auth headers, or institution-code formats in anything user-visible; keep simulator wire details obviously internal.
- Credentials seam: store future real ORASS/API credentials in the existing `EncryptedDbVault` pattern (write-only, fingerprint-surfacing), so the swap to a real client is config + transport only.

### 8.3 Guided email fallback (codifies BG/FMD/2026/07)
- Downtime-triggered workflow: package the exact return artifact + generated cover email; recipient address is **institution-configured** (BoG return-desk addresses are not public — default empty with guidance, offer `bsd@bog.gov.gh`/`bsdletters@bog.gov.gh` as *reference examples only*).
- Enforce the rule that the email submission is provisional: an open task "re-upload to ORASS when restored" blocks the return from reaching a terminal COMPLETE state until marked re-uploaded — this is the directive's "deemed complete" logic, verbatim implementable.

### 8.4 Artifacts for manual upload
- Generate per-directive templates from the published appendices (LMT Appendix reporting templates; IRRBB Appendix IV; LE Templates 1/1a/2/3/4; DBK figures for the NOP computation) as Excel/CSV + PDF cover sheets, with the solo/consolidated dimension and as-of period stamped.
- Local pre-validation engine (completeness, reconciliation identities like "ΔNOP = DBK 400+700+300 net trades", limit checks) so users catch s.93(3)-grade defects before any channel is touched.
- Deadline engine: DBK T+1 10:00 Africa/Accra; monthly/quarterly +9 days; ICAAP year-end +3 months/31 Mar; penalty-exposure estimator using GH¢12/unit statutory math (display-only, labeled).

### 8.5 What to do when the record improves
- Trigger points to replace simulator parts: BoG publishing an ORASS integration pack; Regnology publishing a BoG-specific VAS spec; or the institution obtaining its onboarding pack from BoG (the realistic path — specs appear to be distributed to regulated institutions directly, not published).

---

## 9. Source list

**Primary (Bank of Ghana / Government of Ghana):**
1. Notice BG/FMD/2026/07 — Revised Directive on Net Open Position Limits (10 Feb 2026): https://www.bog.gov.gh/wp-content/uploads/2026/02/BG-FMD-2026-07-Revised-Directive-on-Foreign-Exchange-FX-PositionLimits-1.pdf
2. Banks and Specialised Deposit-Taking Institutions Act, 2016 (Act 930): https://www.bog.gov.gh/wp-content/uploads/2019/09/BANKS-AND-SPECIALISED-DEPOSIT-ACT-2016.pdf
3. BoG 2023 Annual Report & Financial Statements: https://www.bog.gov.gh/wp-content/uploads/2024/05/Bank-of-Ghana-2023-Annual-Report-and-Financial-Statements.pdf
4. BoG 2024 Annual Report & Financial Statements: https://www.bog.gov.gh/wp-content/uploads/2025/07/2024-Annual-Report-and-Financial-Statements-1.pdf
5. Liquidity Monitoring Tools Directive, 2026 (exposure draft): https://www.bog.gov.gh/wp-content/uploads/2026/02/Liquidity-Monitoring-Tools-Directive-Cleaned-9.2.26.pdf
6. Liquidity Risk Management Directive, 2026 (exposure draft): https://www.bog.gov.gh/wp-content/uploads/2026/02/Liquidity-Risk-Management-Directive-Cleaned-11.12.25.pdf
7. Guideline on ICAAP (exposure draft, Feb 2026): https://www.bog.gov.gh/wp-content/uploads/2026/02/Guideline-on-ICAAP-Exposure-Draft.pdf
8. IRRBB Guideline (exposure draft, Feb 2026): https://www.bog.gov.gh/wp-content/uploads/2026/02/Exposure_Draft_Guidelines-on-the-Management-and-Measurement-of-Interest-Rate-Risk-in-the-Banking-Book-_February-2026.pdf
9. Large Exposures Directive — Explanatory Notes (Sept 2025): https://www.bog.gov.gh/wp-content/uploads/2025/09/Large-Exposures-Directive-Explanatory-Notes.pdf
10. Large Exposures Directive (exposure draft, Dec 2024): https://www.bog.gov.gh/wp-content/uploads/2024/12/Exposure-Draft-Large-Exposures-Directive.pdf
11. Capital Requirements Directive (June 2018): https://www.bog.gov.gh/wp-content/uploads/2022/05/Basel-II-BOG-CRD-Final-27-June-2018-Basel-Committee-BSD.pdf
12. Notice BG/GOV/SEC/2025/23 — NPL Regulatory Measures: https://www.bog.gov.gh/wp-content/uploads/2025/08/NOTICE-NO.-BG-GOV-SEC-2025-23-REGULATORY-MEASURES-TO-REDUCE-NON-PERFORMING-LOANS-IN-BANKS-SDIs-AND-NBFIs.pdf
13. DCSP Licensing Requirements (Notice 30, Sept 2025) — ORASS licensing flow: https://www.bog.gov.gh/wp-content/uploads/2025/09/Licensing-Requirement_Digital-Credit-Services-Provider-Updated_04_09_25-NOTICE-30.pdf
14. ORASS Training Notice (DCSP licence applications): https://www.bog.gov.gh/notice/notice-orass-training-for-the-application-of-digital-credit-services-providers-dcsp-license/
15. ORASS Portal: https://orassportal.bog.gov.gh/ and public registration: https://orassportal.bog.gov.gh/PublicForm/PublicForm.aspx (direct probes, 2026-07-16)
16. BoG Banking Supervision page (footer quick-links incl. "PSA Returns", "ORASS Registration Portal", "BOG Oracle Portal"): https://www.bog.gov.gh/supervision-regulation/banking-supervision/
17. Credit Reporting Data Formats: https://www.bog.gov.gh/supervision-regulation/fsd/credit-reporting-data-formats/
18. Corporate Governance Directive 2018: https://www.bog.gov.gh/wp-content/uploads/2019/09/CGD-Corporate-Governance-Directive-2018-Final-For-PublicationV1.1.pdf
19. Cyber & Information Security Directive exposure draft (2025): https://www.bog.gov.gh/wp-content/uploads/2025/09/CISD-Exposure-Draft.pdf
20. Prudential Requirements for DFIs (exposure draft): https://www.bog.gov.gh/wp-content/uploads/2022/05/Prudential-Requirements-for-DFIs-Exposure-Draft.pdf
21. Draft Open Banking Directive (Dec 2024): https://www.bog.gov.gh/wp-content/uploads/2024/12/Draft-Open-Banking-Directive-for-Regulated-Financial-Institutions-201224.pdf
22. Fines (Penalty Units) Act, 2000 (Act 572): http://www.commonlii.org/gh/legis/num_act/fua2000a572254.pdf ; GRA penalty-unit value: https://gra.gov.gh/domestic-tax/tax-offences-and-penalties/
23. BoG Contact page: https://www.bog.gov.gh/contact-us/

**Vendor / secondary:**
24. Regnology SupTech case study (BoG/ORASS): https://www.regnology.net/en/resources/insights/integrated-financial-supervision-system-supports-bank-of-ghana-reforms/
25. Regnology: "Vizor achieves Technical go-live with Bank of Ghana" (Phase 1 = VRR + Vizor API Service – Submit): https://www.regnology.net/en/resources/news/vizor-achieves-technical-go-live-with-bank-of-ghana/
26. Vizor contract award (July 2019): https://presswire.com/content/4305/bank-ghana-awards-vizor-contract-fully-integrated-financial-supervision-system
27. Glydetek ORASS API: https://glydetek.com/orass-api/ and https://glydetek.com/portfolio/orass-integration/
28. REGTECH AFRICA — BoG SupTech (deployment 2019–2022, FICSOC, Cambridge SupTech Lab): https://regtechafrica.com/ghana-bank-of-ghanas-suptech-advances-supervisory-capabilities/
29. B&FT (Dec 2023) — BoG SupTech functionalities: https://thebftonline.com/2023/12/13/bogs-suptech-enables-comprehensive-supervisory-functionalities/ (body unretrievable; headline/metadata only)
30. GhanaWeb — "BoG rolls out online supervisory and reporting tool to harmonise data collection": https://www.ghanaweb.com/GhanaHomePage/business/BoG-rolls-out-online-supervisory-and-reporting-tool-to-harmonise-data-collection-1760867 (403 on fetch; search-snippet only)
31. LinkedIn — "Bank of Ghana ORASS API Submission" (~Mar 2021 by activity-ID decode): https://www.linkedin.com/posts/kwakuadade_bank-of-ghana-orass-api-submission-activity-6773451892740673536-WryA
32. Cambridge SupTech Lab — consumer protection suite (BoG partner): https://cambridgesuptechlab.org/financial-consumer-protection-suite-with-next-generation-ai-powered-chatbot-supported-complaints/
33. Central Banking — Vizor coverage: https://www.centralbanking.com/fintech/7843991/technology-for-regulatory-compliance-vizor-software
34. Studylib — BoG CG directives analysis (source of the `bsd@bog.gov.gh` certification-submission claim; secondary): https://studylib.net/doc/25710212/bog-directives
35. PSA Returns Portal (indexed title): https://psdc.bog.gov.gh/
