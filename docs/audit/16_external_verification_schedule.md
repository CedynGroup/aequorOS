# 16. External Regulatory Verification Schedule

**Prepared:** 2026-08-22 (WS-A12) · **Branch:** `eric`, working tree, nothing committed
**Re-measured the same day by the WS-A12 execution pass — the counts below have MOVED.**

> ## ⚠ RE-MEASURED 2026-08-22: 40 → **44**, and filed 14 → **18**
>
> The same command, re-run hours later on the same working tree:
>
> ```sh
> cd backend && DATABASE_URL="" uv run python -c "
> from app.domain.authority.registry import REGISTRY
> print(len(REGISTRY.all()), len(REGISTRY.requiring_external_verification()))"
> # → 82 44        (was 78 40)
> ```
>
> **`REGISTRY.all()` grew 78 → 82 and the external-verification set grew 40 → 44, of which
> filed grew 14 → 18**, still across the same three methodologies (`basel_irrbb_run`,
> `bog_fx_nop_run`, `bog_five_grade_classification`).
>
> **The cause, traced:** a sibling workstream registered four previously unbacked filed
> metrics — `asset_duration`, `liability_duration`, `ear_up_450_ghs`, `ear_down_450_ghs`
> (`backend/app/domain/authority/registry.py:1829, :1847, :1867, :1875`) — under
> `basel_irrbb_run`, which carries the sentinel. I read the four entries to check whether an
> authority had been invented; **it had not** (the duration pair inherits `duration_gap`'s own
> citation as terms of the same identity; the ±450 bp pair names BoG's IRRBB Guideline 2026
> Appendix II Table 5, corroborated at `backend/docs/bog_parameter_sources.md:832, 1142, 1314`,
> and states in the same note that the guideline is a **February 2026 exposure draft effective
> 1 January 2027**).
>
> **Read the direction correctly.** Registering those four did **not** reduce the external
> burden — it grew the count of *filed* figures standing on an unestablished basis from 14 to
> 18. What changed is that the dependence is now **visible in the registry instead of invisible
> outside it**. That is an improvement in the record, not in the legal position.
>
> **And it is the recurring lesson:** any document quoting a count is stale on arrival.
> §1 below argues 40 against 41 and is *itself* now wrong by four. The durable form is the
> command, not the number.
**Measured, not recalled.** Every row below was produced by executing
`REGISTRY.requiring_external_verification()` against the working tree, not by reading the
remediation register.

```sh
cd backend && DATABASE_URL="" uv run python -c "
from app.domain.authority.registry import REGISTRY
print(len(REGISTRY.all()), len(REGISTRY.requiring_external_verification()))"
# → 78 40
```

**Nothing in this document claims a certification.** AequorOS holds no SOC 2, no ISO
certification, and no Bank of Ghana approval, registration or accreditation. This is a
work-list of open evidence questions, not a compliance statement.

---

## 1. The measured count was 40, not 41 — and is 44 today (see the banner above)

| Source | Figure | Verdict |
|---|---:|---|
| `REGISTRY.requiring_external_verification()`, executed 2026-08-22 | **40** | **This is the number.** |
| `remediation_master_register.md` MILESTONE 1 | 41 | **Wrong**, and its own breakdown ("7 IRRBB, 5 FX, 8 forecast, 6 stress, 9 SDI/s.29, 4 credit") sums to 39 with two entries double-counted. Corrected at MILESTONE 14 but never annotated at MILESTONE 1 |
| `02_calculation_authority_registry.md` §8, `15_known_limitations.md` §2 | 40 | Correct |

`requires_external_verification` is a **substring** test over three governance fields —
`authority_reference`, `policy_resolver`, `calculation_version` — at
`backend/app/domain/authority/registry.py:356-370`. An authority is flagged when *any* one of
them still carries the sentinel, which is why a partially-established citation (Basel standard
known, BoG's own calibration not) still surfaces. That design is right and is why the count
cannot be read as "40 metrics with no authority at all".

---

## 2. What the 40 break into, by consequence

The family split is already in `02` §8 and `15` §2. The split that matters more for the
founder is **what each authority is allowed to do while unverified**:

| `advisory_designation` | n | What it means |
|---|---:|---|
| **`filed`** | **14** | The metric **can reach a Bank of Ghana return** while the authority behind it is unverified. This is the actionable half of the list. |
| `supervisory_monitoring` | 23 | Internal / supervisory-facing only. |
| `advisory_only` | 3 | Structurally barred from filing (`test_unresolved_divergences_are_never_designated_filed`, `backend/tests/domain/authority/test_registry.py:250`). |

The 14 filed authorities are **FX (5)**, **IRRBB (7)** and **bank credit classification (2)**.
Nothing has yet been filed with the Bank of Ghana — the only two `fully_certified` packages in
the database are `LCR-NSFR` returns — so no unverified authority has reached a submission. That
is a fact about elapsed time, not a control.

---

## 3. The schedule

Columns: **Value in question** is the specific quantity or calibration that is not
established from a Bank of Ghana instrument. **What would settle it** names the document,
not a person.

### 3.1 FX — 5 authorities, all `filed`

Engine `regulatory-fx-v1.0.0`, regime `crd`, methodology `bog_fx_nop_run`, institution class
`bank`. The missing field is `authority_reference` on all five.

| # | Metric | Value in question | What would settle it | Status |
|---:|---|---|---|---|
| 1 | `nop_ghs` | Aggregation rule for the net open position across currencies (gross-aggregate vs shorthand method) | The Bank of Ghana instrument setting the NOP limit and its measurement basis. **Notice BG/FMD/2026/07 is named in the dossier but is not bound to the engine.** CRD ¶310 mandates the Standardised Method and is already on disk | OPEN — instrument exists, binding not done |
| 2 | `nop_pct_tier1` | The Tier 1 denominator definition and the limit percentage | Same notice | OPEN |
| 3 | `single_ccy_max_pct` | The per-currency sub-limit | Same notice | OPEN |
| 4 | `var_99_1d_ghs` | 99% 1-day VaR: holding period, confidence, lookback, and whether BoG recognises VaR at all | **The CRD contains no VaR provision.** Closing this is not "find the citation" — it is a decision to state explicitly that VaR is an *internal* measure with no regulatory authority | OPEN — likely closes by re-labelling, not by citation |
| 5 | `stressed_var_ghs` | Stress calibration applied to the VaR | As above | OPEN — same disposition |

**Do not invent a paragraph number for VaR.** The honest close for rows 4–5 is to move them
off `filed` and label them management measures.

### 3.2 IRRBB — 7 authorities, all `filed`

Engine `regulatory-irr-v1.0.0`, regime `crd`, methodology `basel_irrbb_run`, class `bank`.
Missing field: `authority_reference` on all seven.

| # | Metric | Value in question | What would settle it | Status |
|---:|---|---|---|---|
| 6 | `eve_base_ghs` | Discount curve and cash-flow slotting convention | The final Ghana IRRBB guideline. The February 2026 text is an **exposure draft** | OPEN — blocked on an unpublished final |
| 7 | `nii_base_ghs` | Earnings horizon and repricing assumptions | Same | OPEN |
| 8 | `ear_up_200_ghs` | The **+200bp** shock — a Basel convention, not a BoG-prescribed shock | Same. BoG's prescribed shock set is not in the repository | OPEN |
| 9 | `ear_down_200_ghs` | The **−200bp** shock, and any floor applied to it | Same | OPEN |
| 10 | `worst_eve_change_pct_tier1` | The outlier threshold (BCBS d368 uses 15% of Tier 1; d578 recalibrated) and which Tier 1 basis | Same | OPEN |
| 11 | `duration_gap` | Duration convention (Macaulay vs modified) and NMD behavioural assumptions | Same | OPEN |
| 12 | `cumulative_12m_gap_ghs` | Bucket boundaries | Same | OPEN |

The Basel side is established — BCBS **d368**, recalibrated by **d578**. What is missing is
Ghana's adoption and its own shock table. Related: the dossier records that **CRD ¶90's
leverage exposure measure did not extract from the PDF text layer** and must be read off the
page; that is a distinct, cheap, non-external task.

### 3.3 Credit classification — 4 authorities, 2 of them `filed`

Methodology `bog_five_grade_classification` (bank, `crd`, **`filed`**) and
`nbfi_four_grade_classification` (SDI, `s29`, `supervisory_monitoring`). Missing field:
`calculation_version` on all four.

| # | Metric | Class | Value in question | What would settle it | Status |
|---:|---|---|---|---|---|
| 13 | `npl_ratio` | bank | The five-band day-count boundaries | The **asset-classification directive** that Act 930 ss.62(4) / 68(5) defer to, and the **Guide for Reporting Institutions** | OPEN — **neither is published anywhere reachable** |
| 14 | `total_provision_required_ghs` | bank | Provisioning rates **1 / 10 / 25 / 50 / 100 %** | As above. Partial primary support only: OLEM **10%** is VERIFIED via MPC PR 21 Mar 2022 ¶30; CRD ¶142 p.30 presupposes "no less than 25%" / "no less than 50%" **for risk weighting, not provisioning** | OPEN — best source is an audited annual report (SECONDARY; see `15` §3.2) |
| 15 | `npl_ratio` | sdi | Four-band grid, no OLEM | **No current BoG instrument gives SDIs a distinct schedule.** Notices 2020/01 and 2025/23 apply one set of norms to banks, SDIs and NBFIs jointly | OPEN |
| 16 | `total_provision_required_ghs` | sdi | Rates **0 / 20 / 50 / 100 %**, cited to *NBFI Business Rules 2000* r.19 | Those Rules were made under **PNDCL 328, repealed by Act 774 / Act 930**. A current instrument, or a decision to apply the joint bank norms | OPEN — the present citation is to a **repealed** instrument (`NEW-20`) |

⚠️ **BoG contradicts itself on the OLEM band** (`NEW-23`): *FSR 2020* p.22 item 10 describes
OLEM as "1 to 30 days in default", against the 30-to-<90-day band used elsewhere and against
Notice 2020/01 item (iv) (up to 30 days = Current). Flagged, not silently resolved.

### 3.4 SDI capital under Act 930 s.29 — 5 authorities, all `supervisory_monitoring`

Regime `s29`. Missing field: `calculation_version` on all five — there is **no engine version**
because there is no published methodology to version.

| # | Metric | Value in question | What would settle it | Status |
|---:|---|---|---|---|
| 17 | `car_pct` | Whether s.29 RWA charges for market and operational risk at all, and the risk-weight schedule | **A published BoG SDI capital directive.** The CRD excludes SDIs by its own ¶2. Act 930 **s.29(2)** does state a 10% statutory floor — that much is VERIFIED — but a floor is not a measurement methodology | OPEN — the *implicitness* is closed (`MILESTONE 18`: the SDI screen now states "credit risk only"); the *value* question is not |
| 18 | `total_rwa_ghs` | The simplified bucket weights (seeded `pending`) | Same directive | OPEN |
| 19 | `net_own_funds_ghs` | The Net Own Funds definition and its deductions | Same | OPEN |
| 20 | `paid_up_capital_ghs` | The minimum paid-up capital by licence class | Notice **BG/GOV/SEC/2026/03** (27 Jan 2026) sets Microfinance Bank 50m transitioning / 100m new and Community Bank 5m / 10m new urban. The `other_rfi` seed is `pending` | PARTIAL — a current instrument exists for the *new* classes |
| 21 | `statutory_reserve_fund_ghs` | 50% of net profit until the fund equals paid-up capital | **Act 930 s.34** — cited, and the arithmetic is statutory | NEAREST TO CLOSING — the sentinel here is the missing engine version, not a missing source |

**Do not default an SDI's CRR to the bank's 20%** (`NEW-27`). Act 930 s.36(1)–(2) expressly
permits a different requirement for SDIs and none is published; substituting the bank figure
would be a fabricated number in a filed return.

### 3.5 SDI statutory liquidity reserves — 2 authorities, `supervisory_monitoring`

Methodology `nbfi_r11_liquidity_reserve`, regime `s29`. Missing: `calculation_version`.

| # | Metric | Value in question | What would settle it | Status |
|---:|---|---|---|---|
| 22 | `primary_liquidity_reserve_pct` | **10%**, cited to *NBFI Business Rules 2000* r.11 | A current BoG SDI liquidity instrument. **None exists.** The cited Rules are repealed (`NEW-20`) | OPEN — repealed citation |
| 23 | `secondary_liquidity_reserve_pct` | **15%**, same citation | Same | OPEN — repealed citation |

These are **statutory reserve requirements, not an LCR**, and the registry says so. Never map
them onto HQLA.

### 3.6 Forecast — 8 authorities, `supervisory_monitoring`

Engine `regulatory-forecasting-v1.0.0`, methodology `bank_forecast_projection_run`, regime
`advisory_internal`. Missing: `authority_reference`.

| # | Metrics | Value in question | What would settle it | Status |
|---:|---|---|---|---|
| 24–31 | `min_car_pct`, `min_lcr_pct`, `min_nsfr_pct`, `year5_car_pct`, `year5_lcr_pct`, `year5_nsfr_pct`, `avg_roe_pct`, `cumulative_net_income` | The **projection elasticities**, which are code defaults | **No BoG instrument prescribes a projection method.** Closes with a supervisory-approved methodology *or* — more realistically — a formal statement that the projection is management's own | OPEN — closes by declaration, not by citation |

None is `filed`. The honest close here is a labelling decision, and it is cheap.

### 3.7 Stress — 6 authorities, `supervisory_monitoring`

Engines `enterprise-stress-v1.0.0` and `reverse-stress-v1.0.0`, regime `advisory_internal`.
Missing: `authority_reference`.

| # | Metrics | Value in question | What would settle it | Status |
|---:|---|---|---|---|
| 32–35 | `stressed_car_end_pct`, `car_erosion_pp`, `stressed_lcr_pct`, `lcr_erosion_pp` | The **macro scenario set** and its calibration | The *Directive on Stress Testing* — a **February 2026 exposure draft, stated effective 1 January 2027**. Not in force | OPEN — blocked on the final directive |
| 36–37 | `capital_breach_multiplier`, `liquidity_breach_multiplier` | The reverse-stress frontier search constants (`k_max = 5`, `precision = 0.05`) — **code defaults, not governed** | No BoG instrument contemplates reverse stress. Closes by governing the constants in the control plane and labelling the output as management's own | OPEN — closable in-repo |

### 3.8 The three that cannot be filed by construction — `advisory_only`

Methodology `bank_forecast_projection_path`, regime `advisory_internal`, class `bank`.

| # | Metric | Position |
|---:|---|---|
| 38 | `car_pct` | `resolution_status = unresolved_audit_finding`, designated `ADVISORY_ONLY`. The underlying numeric divergence **is** closed and proved at `Decimal(0)` tolerance in `backend/tests/equivalence/`, but the designation was deliberately left advisory: numeric equality under a tested baseline is not an enforced identity |
| 39 | `lcr_pct` | Same |
| 40 | `nsfr_pct` | Same |

These are the *correct* end state for an unresolved divergence, not a backlog item. Do not
close them by asserting equality.

---

## 4. What is genuinely external, and what only looks external

**Genuinely external — cannot be closed from this repository:**

- **Circular BSD/108/2011** (named by bank audit committees as the Credit Risk Reserve
  instrument), the **asset-classification directive** Act 930 ss.62(4)/68(5) defer to, and the
  **Guide for Reporting Institutions**. Searched: bog.gov.gh site search, WP REST `/search` and
  `/media`, the full 75-entry `reg_directives` index, and a Wayback CDX sweep of ~5,000 archived
  PDFs on the domain. None is published. These are the reason rows 13–14 cannot rise above
  SECONDARY.
- The **final** IRRBB guideline, Stress Testing directive, ICAAP guideline and Liquidity
  Monitoring Tools directive. All four are **February 2026 exposure drafts**; the Stress Testing
  and ICAAP texts state **1 January 2027**. Whether any is in force is a supervisory fact.
- A **published SDI capital directive** and risk-weight schedule (rows 17–19). BoG publishes no
  form-numbered SDI return schedule either; not inferring one is the correct call.
- **CRR for SDIs** (row 21 note).

**Only looks external — closable here, and cheaply:**

| Item | Rows | Work |
|---|---|---|
| Bind **Notice BG/FMD/2026/07** to the FX engine | 1–3 | The notice is identified; the binding is not done |
| Re-designate VaR as an internal measure | 4–5 | Removes two authorities from `filed` |
| Read **CRD ¶90**'s leverage exposure measure off the PDF page | (IRRBB note) | The text layer failed to extract; the page is on disk |
| Declare the forecast projection management's own | 24–31 | A labelling decision |
| Govern the reverse-stress frontier constants | 36–37 | A control-plane row each |
| Version the s.34 statutory reserve engine | 21 | The statute is already cited |

Six of the forty are within the team's control today. That is the actionable list.

---

## 5. Related parameter-side gaps (not authorities, same failure mode)

The registry's 40 are about *methodologies*. The parallel defect on the *values* is in
`04_policy_governance.md` §5 and `15_known_limitations.md` §3. Two are worth repeating here
because they touch numbers a bank sees:

1. **`cet1_min` 6.5%, `tier1_min` 8.0%, `leverage_min` 6% have no governed control-plane row.**
   All three are **VERIFIED against the BoG Capital Requirements Directive** — ¶73(a), ¶73(b)
   and ¶90 respectively (`backend/docs/bog_parameter_sources.md` §2.1) — so the *citation* is
   sound. What is missing is the seeded row, which leaves the tighten-only clamp **wired but
   inert**: a board register value weaker than the BoG floor passes through unclamped.
   The hermetic fixture ships `leverage_min = 3`, which is **Basel III's** figure and *below*
   BoG ¶90's 6%.
2. **The bank `car_min = 13` seed cites "Basel CRD (10% + 3% CCB)"**
   (`backend/app/services/regulatory_parameters.py:163-170`). The arithmetic is right and
   matches the in-force figure, but **the CRD is the Bank of Ghana's Capital Requirements
   Directive, not Basel's.** See `WS-A12-2` in the remediation register.

---

## 6. Reproducing this document

```sh
cd backend && DATABASE_URL="" uv run python -c "
from app.domain.authority.registry import REGISTRY
S='EXTERNAL_REGULATORY_VERIFICATION_REQUIRED'
for a in sorted(REGISTRY.requiring_external_verification(),
                key=lambda x:(x.metric_family.value, x.metric_id, x.institution_class.value)):
    miss = [f for f in ('authority_reference','policy_resolver','calculation_version')
            if S in str(getattr(a, f))]
    print(a.metric_family.value, a.metric_id, a.institution_class.value, a.regime.value,
          a.methodology_id, '+'.join(miss), a.advisory_designation.value)"
```

Any change to the count without a corresponding change to this document is a drift, and
`backend/tests/domain/authority/test_registry.py::test_entries_needing_external_verification_are_flagged_not_guessed`
is the gate that keeps the flagging honest.

---

## Addendum — measured counts moved on 2026-08-22 (WS-A4 round 2)

**Appended, not rewritten.** Everything above was correct when measured. This note
records what changed afterwards so the document does not silently become wrong.

Four IRRBB metrics were found being **sealed into filing runs with no authority at
all** and have since been registered (`asset_duration`, `liability_duration`,
`ear_up_450_ghs`, `ear_down_450_ghs`). They are outputs of `basel_irrbb_run` — the same
engine, run, methodology and citation as the seven IRRBB rows in §3.2 — so they inherit
that methodology's partially-sentinel `authority_reference` and therefore also its place
on this schedule. Re-executed against the working tree:

```sh
cd backend && DATABASE_URL="" uv run python -c "
from app.domain.authority.registry import REGISTRY
print(len(REGISTRY.all()), len(REGISTRY.requiring_external_verification()))"
# → 82 44
```

| Figure | §1 / §2 above | Now |
|---|---:|---:|
| `REGISTRY.all()` | 78 | **82** |
| `requiring_external_verification()` | **40** | **44** |
| of those, `advisory_designation == filed` | **14** | **18** |
| IRRBB rows in §3.2 | 7 | **11** |

**§3.2 needs four rows added**, all with the same "value in question" as their
siblings — the BoG-prescribed shock set — and all `OPEN`.

Two corrections to what "OPEN" means for IRRBB, both established from
`backend/docs/bog_parameter_sources.md` during that pass and both worth carrying here:

1. **The shock set is no longer unlocated.** BoG Guideline on the Management and
   Measurement of Interest Rate Risk in the Banking Book, 2026, **Appendix II ¶1 and
   Table 5** (printed page 39) sets the Ghana cedi parallel shift at **450 basis
   points** and makes it mandatory for EVE and NII. The cedi is absent from BCBS d368
   Annex 2, so no Basel table supplies it — the figure is genuinely Ghanaian.
2. **What is still open is commencement, not sourcing.** That guideline is an
   **exposure draft**: issued February 2026 under Act 930 s.92(1), effective
   1 January 2027 at ¶9, comment window closed 30 June 2026 with no final version
   published. The sentinel is therefore right for a different reason than this schedule
   records, and the registry now carries that state explicitly
   (`MetricAuthority.instrument_in_force`, **20 filed entries across
   `basel_irrbb_run`, `lmtd_table1_ratio` and `lmtd_table11_capped`**).

Full reasoning: `remediation_master_register.md` §WS-A4R2-2 and §WS-A4R2-4.
**Nothing here claims a certification.**
