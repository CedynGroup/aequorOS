# LRMD 2026 — Directive vs As-Built Gap Analysis

**Source:** Bank of Ghana, *Liquidity Risk Management Directive* (Exposure Draft,
February 2026) — full 31-page document, downloaded 2026-08-07 from bog.gov.gh
(local copy: `docs/Liquidity-Risk-Management-Directive-Cleaned-11.12.25.pdf`).
**Companion to:** `lmtd_gap_analysis.md` (the tools/templates half of the same package).
**Compared against:** the running codebase per the re-verified Phase 0 inventory in
`product.md` (2026-08-07).
**Date:** 7 August 2026.

**Method note.** The LMTD prescribes *what to report*; the LRMD prescribes *what the
bank must operate*. So this analysis maps qualitative obligations to product surfaces
rather than templates to generators. Where an obligation is on the bank as an
organisation rather than on any system, it says so — we should not claim build work
where none exists.

**Same caveat as the LMTD:** this is an **exposure draft**; comments closed
**30 June 2026**; a final version may exist and differ. Effective **1 January 2027**
(¶8); governance/frameworks/policies aligned by **31 December 2026** (¶9). Identical
dates to the LMTD — the two are one compliance programme.

---

## 1. The three-directive package is now confirmed

The preamble states this directive "is complemented by the **Liquidity Monitoring
Tools Directive** and **Liquidity Coverage Ratio Directive (applicable to banks
only)** which when implemented as a package, provides a comprehensive qualitative and
quantitative perspective."

So the **LCR Directive 2026 exists** — it is not on BoG's public directives page
(checked 2026-08-07: only the LMTD and LRMD are posted), which means it is pending
publication or circulating to RFIs directly. **Banks-only** scope confirmed. It will
carry the run-off rates and haircuts our LCR currently substitutes with Basel
defaults — obtaining it remains the single highest-value document acquisition
(open-decision register; Bernard question).

---

## 2. Two new dated regulatory obligations we carry nowhere

These are the headline findings — both are filings/publications with dates, and
neither exists anywhere in our reporting hub, coverage ledger (before today), or
obligations calendar.

**¶12 — Quarterly Liquidity Adequacy Statement (LAS).** "The Board shall, on a
quarterly basis, provide its assessment of the liquidity adequacy of the RFI through
a Liquidity Adequacy Statement (LAS) to the BOG. The assessment shall be supported by
Internal Liquidity Adequacy Assessment Process (ILAAP), and the outcome shall be
embedded in the annual Internal Capital Adequacy Assessment Process (ICAAP) report."

- A **quarterly, Board-level filing to BoG**, ILAAP-supported, feeding the annual
  ICAAP. Our ledger listed "LAS + ILAAP narrative — not built" with no cadence; it
  now has one, and it is the *Board's* statement — the attestation chain presumably
  runs above the CFO (who signs a Board statement is a Bernard question).
- No template is given in the directive. Whether BoG prescribes a LAS form is
  unknown — same posture as the CAR return.
- First LAS presumably due after Q1 2027 (interpretation — the directive gives
  cadence, not a start date).

**¶87–88 — Annual public liquidity disclosure.** RFIs must publicly disclose seven
items (governance structure incl. committee/unit responsibilities; centralisation
degree; risk appetite articulation; quantitative measures — liquid-asset stock
composition/size, internal ratios; qualitative assumptions; limit-setting policies;
stress-test overview) **on their websites**, in the **Audited Financial Statements**,
and **submit the same to BOG by 31 March of the ensuing year**.

- Our roadmap had "Pillar-3 disclosure pack → phase 7." This gives it a statutory
  date: for a directive effective 1 Jan 2027, the first submission lands
  **31 March 2028** (covering FY2027). Phase 7 may still be compatible with that
  date — but it is no longer discretionary, and ¶87(a) requires disclosing exactly
  the committee/unit structure that the pack workflow models.

---

## 3. Obligations that harden existing roadmap items (same build, stronger why)

| ¶ | Requirement | Our roadmap item | Change |
|---|---|---|---|
| 28(e)–(f) | **EWI system mandatory**, indicators "aligned with liquidity-related recovery indicators in the RFI's recovery plan"; eight indicators named (rapid asset growth on volatile funding, concentration growth, currency-mismatch increases, falling weighted-average liability maturity, repeated near-limit incidents, earnings/asset-quality deterioration, widening debt spreads, rising funding costs) | Phase 2 item 3 (EWI framework) | The configurable indicator set now has a **BoG-named starter list**; add recovery-plan alignment as a data point on each indicator |
| 70–77 | **CFP contents specified** (¶72 a–g: EWIs with CFP trigger, funding options at multiple horizons, asset/liability action plans, alternative sources, escalation/prioritisation procedures, key-relationship register, communication plans incl. BoG/media); **Board approves annually** (¶71); integrated with stress testing (¶75); consistent with BCP (¶77) | Phase 2 item 3 (CFP engine) | The CFP module's minimum field set is now dictated; Board-annual-approval is an approvals-workflow requirement (Phase 1 machinery) |
| **74** | **RFIs shall notify BOG upon initialization or de-escalation of a CFP** and provide regular updates | Phase 2 item 3 | **New event type**: CFP activation/de-escalation is a regulator notification, not just an internal state change — belongs in the submission/notification pipeline |
| 78–79 + fn 12 | **LTP mandated**: liquidity costs/benefits/risks into internal pricing, performance evaluation **and new-product approval**; explicitly "into their internal funds transfer pricing program". *Banks only initially* | Phase 2 item 11 (LTP contingent charge) | Was FRM-canonical best practice; now regulatory. The **new-product-approval** hook is additionally new (nearest home: Phase 3 deal-pricing calculator — note added there) |
| 48(b) | Stress results integrated into internal limits, strategic planning **and internal transfer pricing systems** | Phase 2 items 4/11 | The stress→FTP feedback loop is a directive expectation |
| 64–69 | **Asset encumbrance management**: distinguish encumbered/unencumbered (¶64), track **owning legal entity and physical location** of collateral (¶64), exclude pledged assets from the liquid stock (¶65), pledging policies (¶66), current + prospective encumbrance evaluation (¶67), **intraday encumbrances to clearing/settlement separately identified** (¶68) | Phase 2 item 1(a) (encumbrance flag) | The flag is now required by **both** directives. ¶64 also explains LMTD Table 9's "Location" column — the canonical addition should carry `encumbered`, `owning_entity`, `location` together |
| 29–31 | **MIS requirement**: timely, forward-looking liquidity information to Board/SM; positions **per currency, at subsidiary and consolidated level**; **more granular reports at higher frequency with reduced turnaround during stress** | The product itself | This is the sales paragraph. ¶29–31 describes AequorOS's live tier + official runs almost literally. ¶31's stress-mode reporting (higher frequency, faster turnaround) is exactly the two-tier design |
| 33 / 35(c) / 37 | FX >5% of liabilities or assets → documented FX liquidity approach; internal limits **per material currency**; FX swap-market sensitivity capacity | Phase 2 item 2 (per-currency gaps + USD stress) | The 5% materiality threshold recurs (same as LMTD significant currency); per-currency internal limits join the threshold register |
| 60–63 | Liquid-asset adequacy factors; internal **liquidity values (haircuts) per asset class**, more conservative than margin haircuts, re-assessed annually by SM, more often under stress | Phase 2 item 1(g) (Table 9 haircuts) | The haircut isn't only a Table 9 column — it's a maintained internal schedule with an annual review workflow |
| 80–83 | **Three-year funding strategy**, Board-approved, reviewed annually; diversification limits by instrument/tenor/provider/currency/geography; **max-funds-per-counterparty internal limits** (¶82) | Phase 2 item 1(e) + Phase 3 item 2 | Funding-concentration limits are not just reporting (LMTD Tables 5/7/8) but limit objects; the 3-year funding strategy is a governed document (register candidate, like the ICAAP plan) |

## 4. Obligations that challenge the roadmap's sequencing

**¶84–86 — Intraday liquidity management is mandatory for banks from 1 Jan 2027.**
Six operational elements: measure expected daily gross flows and their intraday
timing; monitor balances/credit capacity/eligible collateral in real time; acquire
intraday funding; mobilise collateral; manage outflow timing; handle disruptions.
Plus ¶40 (stress testing "including on an intraday basis") and ¶75(b) (CFP covers
intraday horizons).

Our roadmap defers intraday to **Phase 7** ("needs RTGS/payment-flow ingestion —
deliberately last"). The honest reading:

- ¶84–86 is an obligation on the **bank's operations** (treasury ops + settlement
  systems), not a mandate that its ALM vendor compute BCBS 248 metrics. Banks will
  meet it primarily through their core/payment systems.
- But sales reality changes: from 2027 every Ghanaian bank RFP will ask "intraday?"
  and our answer must be scripted — *management capability is your payment
  infrastructure; monitoring/metrics is our Phase 7 wedge; stress-on-intraday
  horizons and CFP intraday coverage arrive with our Phase 2 CFP/stress work*.
- **Recommendation: keep the Phase 7 sequencing** (the data contracts genuinely
  don't exist earlier), but add intraday stress horizons and CFP intraday coverage
  to the Phase 2 specs, and script the RFP answer. Do not silently ignore ¶84–86.

**¶50–54 — The stress maturity ladder must combine contractual and behaviourally
modified cash flows.** ¶50: "For stress testing purposes, the Maturity Ladder shall
determine, for various time buckets, the combination of normal contractual-based cash
flows and behaviorally modified cash flows under stress." ¶51–54 then require
documented behavioural run-off schedules per funding source (rollover assumptions per
liability, run-off schedules for non-maturity liabilities, nine named assumption
categories including FX convertibility and BoG ELA access).

Our behavioural GBMs + apply-as-assumptions seam exist (Phase 0), and Phase 3 item 9
plans an *advisory* LSTM overlay on the ladder. This is different: a **stress ladder
with reviewed behavioural run-off assumptions is a Phase 2 requirement**, not a
Phase 3 nicety. The engine rule stands (no ML inside regulatory engines — the
assumptions enter via the reviewed apply-as-assumptions seam, exactly what it was
built for), and the official/contractual LMTD Table 2 ladder stays purely
contractual. Two ladders, clearly labelled: contractual (LMTD filing) and stressed-
behavioural (LRMD stress framework).

**¶32–37 — Group-level liquidity monitoring** (FHCs and banks in groups, fn 7):
group-wide exposure view, transferability constraints, intragroup limits, subsidiary
limits, per-currency limits at group level. Group consolidation is Phase 7. That
stays defensible — footnote 7 scopes this to financial groups, and our beachhead
targets are mostly solo licensees — but several Ghanaian licensees are subsidiaries
of FHCs, so the Phase 7 item now has a directive citation and should not slip.

## 5. What the LRMD does NOT ask of us

Recording non-gaps so nobody builds them from a misreading:

- **No new monthly return.** The LRMD's reporting obligations to BoG are: the
  quarterly LAS (¶12), CFP activation/de-escalation notifications (¶74), the annual
  disclosure submission (¶88), and "report, as appropriate, to the Board and BOG"
  (¶15(b)). The monthly grid work all lives in the LMTD.
- **No prescribed stress scenarios.** ¶41–47 require a program with named *elements*
  (institution-specific, market-wide, combined; FX cross-border scenarios for FX-
  active RFIs ¶45; reputational/non-contractual ¶46) but no calibrations — scenario
  design remains the bank's (and our scenario workbench's) job.
- **No LCR/NSFR numbers.** Those live in the LCR Directive (banks-only, unobtained)
  and the LMTD's Table 11.

## 6. As-built scorecard against the LRMD's operable requirements

| LRMD requirement | As-built state |
|---|---|
| MIS: live liquidity info, per-currency, stress-mode granularity (¶29–31) | **STRONG** — two-tier live engine is this; per-currency split is the known Phase 2 gap |
| Maturity-ladder cash-flow projection framework (¶28(b)) | **BUILT (condensed)** — LMT ladder; re-bucketing already scheduled |
| Stress testing: scenarios exist, taxonomy doesn't (¶38–49) | **PARTIAL** — shock-scenario stress + ICAAP-STRESS built; institution-specific/market-wide/combined taxonomy, FX cross-border scenarios, intraday horizons not modelled |
| Stressed-behavioural ladder (¶50–54) | **NOT BUILT** — components exist (GBMs, assumptions seam, ladder); the combination doesn't |
| HQLA cushion view (¶55–59) | **BUILT** — HQLA buffer view, LCR stock |
| Internal liquidity values / haircut schedule (¶60–63) | **NOT BUILT** |
| Asset encumbrance (¶64–69) | **NOT BUILT** — the known flag gap, now with owning-entity + location attributes |
| EWI system (¶28(e)–(f)) | **NOT BUILT server-side** — `/liquidity/cfp` page is illustrative; Phase 2 item 3 |
| CFP module (¶70–77) | **NOT BUILT server-side** — same item; BoG-notification event is new scope |
| LTP (¶78–79) | **PARTIAL** — matched-maturity FTP built; contingent-liquidity charge and new-product hook not |
| Funding strategy register, per-counterparty funding limits (¶80–83) | **NOT BUILT** — limits engine is Phase 3; funding-strategy document register unplanned |
| Quarterly LAS (¶12) | **NOT BUILT** — new return family, no template known |
| Annual public disclosure (¶87–88) | **NOT BUILT** — was Phase 7 discretionary, now dated (31 March) |
| Intraday management (¶84–86, banks only) | **NOT BUILT** — deliberately Phase 7; positioning scripted in §4 |
| Group-level monitoring (¶32–37, groups only) | **NOT BUILT** — Phase 7, now directive-cited |
| ILAAP/ICAAP embedding (¶12, ¶24, ¶26) | **PARTIAL** — ICAAP-STRESS data companion; ILAAP narrative and LAS chain absent |

## 7. Governance confirmations (feeds rbac.md §5.1)

- **¶19**: liquidity-risk oversight sits in the risk management oversight function
  for which the **CRO is ultimately responsible**, per CGD 2018 + RMD 2021 —
  grounds persona #11.
- **¶20**: "The RFI's Assets-Liability Management Committee (ALCO) **and any other
  management committee** shall oversee liquidity risk management in line with the
  Corporate Governance Directive, 2018… responsible for managing the strategic
  direction of liquidity and funding risk" — the ALCO mandate, and "any other
  management committee" endorses the generalized-committee design over a hardcoded
  ALCO.
- **¶17**: Senior Management "shall ensure adequate **separation of responsibilities**
  in key elements of the liquidity risk management processes" — SoD is a liquidity-
  directive requirement, not just an access-control best practice.
- **¶12**: the LAS is the **Board's** statement — Flow B gains a Board-level
  attestation above the CFO/MD chain (who signs: Bernard question).
- **¶25–27**: IAF annual review + ILAAP outcomes under regular internal review,
  reported to Board — Auditor persona + examiner mode grounding, matching LMTD ¶20–21.

## 8. What I would do, in order (deltas to the existing plan — not a new plan)

1. **No change to the LMTD critical path** (product.md Phase 2 item 1 a–h). The LRMD
   reinforces it (encumbrance twice-mandated, haircut schedule feeding Table 9).
2. **Widen Phase 2 item 3 (EWI/CFP)** to the ¶72 minimum contents, the ¶28(f) starter
   indicators, Board-annual-approval, and the **¶74 BoG notification event**.
3. **Add the quarterly LAS** as a scheduled return-family gap next to the monthly BSD
   pack (template unknown — same "get the form first" discipline).
4. **Move the stressed-behavioural ladder into Phase 2** (stress framework work),
   leaving the advisory LSTM overlay in Phase 3 — they are different objects.
5. **Date the disclosure pack** (31 March cadence) and keep it Phase 7 unless a pilot
   bank's first FY2027 disclosure lands earlier than our Phase 7 reach.
6. **Script the intraday RFP answer**; add intraday horizons to Phase 2 stress/CFP
   specs; keep BCBS 248 metrics in Phase 7.

## 9. Questions for Bernard (adds to the list in `lmtd_gap_analysis.md` §11)

12. **The LAS (¶12)** — is there a prescribed quarterly Liquidity Adequacy Statement
    format, or does each bank draft its own? Who signs it — Board chair, MD, both?
13. **The LCR Directive 2026** — confirmed to exist (LRMD preamble, banks-only) but
    not on BoG's website. Does he have it, or know its publication status?
14. **CFP activation notifications (¶74)** — has any bank actually notified BoG of a
    CFP activation? What form does that take in practice?
15. **Liquidity values (¶62)** — do banks maintain their own haircut schedules today,
    or is this new to everyone (i.e., a green-field feature we can define)?
16. **The behavioural stress ladder (¶50–54)** — what run-off assumptions do Ghanaian
    banks actually document today, if any? This calibrates how much hand-holding the
    assumption workflow needs.
