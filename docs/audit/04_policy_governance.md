# 04. Policy and Parameter Governance

**Prepared:** 2026-08-22
**Primary modules:** `backend/app/domain/policy/resolver.py`,
`backend/app/services/regulatory_parameters.py`,
`backend/app/operator/services/regulatory_parameters.py`,
`backend/app/models/regulatory_parameter.py`

Every regulatory number the platform applies — a minimum ratio, a risk weight, a
provisioning rate, a haircut — is data with a citation, an effective date, a confirmation
status and a four-eyes approval trail. This document describes the mechanism and then states
honestly which values do not yet meet the standard it enforces.

---

## 1. The resolution order

```
global control plane      regulatory_parameter  (approved rows only, effective-dated)
        ↓
tenant board register     param_capital_threshold / param_liquidity_threshold /
                          param_risk_weight / param_lcr_runoff_rate / param_nsfr_weight /
                          param_stress_shock / param_liquidity_haircut /
                          param_ecl_assumption / param_crm_haircut
        ↓
tightening clamp          app/domain/policy/resolver.py::clamp_overrides
        ↓
engine
```

The resolver is **pure** — no SQLAlchemy, no FastAPI, no `app.services` import (606 lines).
`app/services/regulatory_parameters.py` is a thin database adapter over it (`ARCH-2`), and
the purity is enforced by
`tests/architecture/test_dependency_boundaries.py::test_the_pure_domain_layer_imports_no_application_state`.

The resolver keys on jurisdiction → regulator → institution type → regime → return family →
parameter set → effective date. `resolve_class_value` **requires** a jurisdiction and is
threaded through `app/services/banks.py`, so institution-type detail is keyed
`(type_code, jurisdiction)` — a Nigerian tenant resolves its own limits, not Ghana's.

---

## 2. Citation discipline is a database constraint, not a convention

`app/models/regulatory_parameter.py`:

| Column | Constraint |
|---|---|
| `source_citation` | `String(240)`, **`nullable=False`** — a value physically cannot enter without a citation |
| `confirmation_status` | `String(12)`, **default `"pending"`**, CHECK `IN ('confirmed','pending')` |
| `status` | default `"draft"`, CHECK `IN ('draft','approved')` |
| `effective_from` | `Date`, not null |
| `effective_to` | nullable — set to the successor's `effective_from` on supersession |
| `proposed_by` / `approved_by` | `approved_by` nullable until the checker acts |
| value | CHECK `value_numeric IS NOT NULL OR value_json IS NOT NULL` |
| uniqueness | one generation per `(scope, code, jurisdiction, effective_from)` |

The tenant-side resolver reads **only approved rows**
(`app/services/regulatory_parameters.py:797`, filtering on `effective_from <= as_of` and
`effective_to` null or later).

## 3. Four-eyes on parameter change

`app/operator/services/regulatory_parameters.py`:

- `propose(...)` writes a `draft` row with `proposed_by`.
- `approve(...)` refuses when `approved_by.strip().lower() == row.proposed_by.strip().lower()`
  (`:144-149`) — *"four-eyes / dual control"*. The refusal is server-side; the console
  surfaces it but never grants it.
- Approval supersedes the prior open row by setting its `effective_to`.
- Endpoints: `/operator/v1/regulatory-parameters` (list / propose / approve), mounted only
  on the operator application.
- Every operator mutation lands in append-only `operator_audit_log`.

### Console hardening

The console page (`console/app/(shell)/(admin)/admin/regulatory-parameters/page.tsx` plus
`RegulatoryParametersView.tsx`) shipped with several defects that the parameter mechanism
exists to prevent. All were corrected:

| Defect found in committed code | Why it mattered |
|---|---|
| `placeholder="10"` on the value field | A suggested regulatory number in the one form whose purpose is that numbers come from a cited source |
| Supersession prefilled the outgoing value **and** citation | The mechanism by which a stale value gets re-approved unchanged — the most dangerous defect on the page |
| `jurisdiction_code: 'GH'` hardcoded as the new-proposal default | Jurisdiction is a decision, not a default |
| `Number.isFinite(Number(value))` validation | Coerced a regulatory decimal through a float; also admitted `1e3` and `0x1f` |
| `pending BoG` chip, `hint="e.g. pct, ratio, GHS_m."` | Hardcoded regulator and currency |
| Raw enum values on screen (`institution_class (bank / sdi)`, `pending`, `(Track: maker)`) | Internal vocabulary in operator-facing copy |

Identity fields still carry over on supersession; **value and citation are now blank.** The
proposer's own row renders no approve button ("Needs a second operator"); enforcement stays
server-side.

Verified: `tsc --noEmit` clean, `npm run test` 27 pass / 0 fail, `next build` compiled.
**Not verified:** ESLint never ran (the console workspace has no ESLint dependency or
config, which is why `next build` prints "Skipping linting"); no real propose/approve POST
was submitted, so the server's four-eyes refusal is unverified *from the console*; there are
no DOM or component tests.

### Open API gaps on this surface

| ID | Gap |
|---|---|
| `L-2` | **Structured values cannot be proposed.** `RegulatoryParameterProposeRequest` has no `value_json` and `propose()` hardcodes `None` (`:115`), so the SDI risk-weight bucket maps are readable but unreachable from any UI. Needs a backend change. |
| `L-3` | No reject/withdraw transition — a mistaken proposal sits in the queue permanently. |
| `L-4` | No jurisdiction filter and no pagination on list. |
| `L-5` | No `/operator/v1/me`, so four-eyes pre-emption degrades to "unknown viewer" under a dev-token session. |

---

## 4. The tightening clamp

A tenant's board may adopt a stricter limit than the regulator's, never a looser one.
`PARAMETER_DIRECTION` (`app/domain/policy/resolver.py:466`) declares the direction of each
governed code, and `clamp_overrides` applies it to a whole register in one pass. Every clamp
produces a `ClampRecord` that is logged and returned as evidence — it is never applied
silently.

Measured:

```
$ cd backend && uv run python -c "from app.domain.policy.resolver import PARAMETER_DIRECTION; ..."
governed codes: 25   (22 floor, 3 ceiling)
governed codes with a seeded control value: 20
governed but UNSEEDED — clamp wired but INERT:
    cet1_min, tier1_min, leverage_min, lcr_min, nsfr_min
```

Before this programme, only **9** codes were clamped (`car_min` plus the eight LMTD floors)
in two hand-written call sites; `regulatory_forecasting._load_active_params` was
**unclamped**, so the same code yielded two answers in two modules.

### `NEW-29` — the five inert codes are a live governance gap

`cet1_min`, `tier1_min`, `leverage_min`, `lcr_min` and `nsfr_min` have **no control-plane
value**, so a board register can set them to anything and nothing constrains it. `lcr_min = 100`
in use today is a **board value, not a verified Bank of Ghana figure.** The citation dossier
supplies bank values with paragraph locators for the first three (6.5%, 8.0%, 6% — CRD); for
`lcr_min` and `nsfr_min` **no Bank of Ghana source exists at all**, and the correct governed
state is an explicit absence with a Basel-default label, not a number.

### `NEW-39` — one code was governed but undirected — **RESOLVED 2026-08-22**

`balance_identity_tolerance_pct` was seeded, and documented in the seed catalogue as
tighten-only, but was **not** in `PARAMETER_DIRECTION` (measured at the time:
`'balance_identity_tolerance_pct' in PARAMETER_DIRECTION → False`). It was therefore the one
governed code a board override could *widen* — on the single control standing between a
broken book and a filed return.

It is now declared **`ceiling`**: the tolerance is a percent of total assets, so a larger
number admits a more broken book and tightening means smaller. The governed set is pinned at
26 codes (`test_the_governed_code_set_is_pinned`), and two tests prove the clamp — one pure
(`tests/domain/policy/test_policy_resolver.py::test_a_board_override_cannot_widen_the_balance_identity_tolerance`)
and one through the seeded control plane for both institution classes
(`tests/services/test_policy_chain.py::test_a_board_override_cannot_widen_the_filing_tolerance`):
a board asking for 5.00% against the governed 0.10% is clamped to 0.10% and the attempt is
recorded as a `ClampRecord`; a board holding itself to 0.02% keeps 0.02%.

### One financial output changed as a result of clamping — reported, not hidden

For the hermetic sample bank the board register carries `car_min = 10%`, below the BoG floor
of 13%. `regulatory_capital` already clamped to 13; `regulatory_forecasting` used the raw 10.
Now both use 13.

- The CAR **value** is untouched (`total_capital / total_rwa × 100`).
- `car_min_pct` feeds only the RAG classification, the `TRIGGER_BREACH` threshold,
  `OptimizerConstraints.car_min_pct`, and the filed validation `year5_car_above_minimum`,
  whose `threshold_min` moves **10 → 13**.
- For a real bank whose projected CAR lands between 10% and 13%, that validation will now
  correctly **fail**.
- **No existing test's expected value was changed to accommodate this.** Two behaviour tests
  were deliberately inverted — a policy change, not a number change — and both were reported.

---

## 5. Seed catalogue census — measured

```
$ cd backend && uv run python -c "from app.services.regulatory_parameters import SEED_PARAMETERS, SEED_EFFECTIVE_FROM; ..."
SEED_PARAMETERS: 70   (54 literal rows + 16 generated LMTD floors)
confirmation_status: 56 confirmed, 14 pending
distinct param codes: 40
SEED_EFFECTIVE_FROM: 2020-01-01     — a single date for the entire catalogue
effective_to:        never set on any seed
scope_type: 63 institution_class, 7 institution_type
```

> **Reconciling the counts.** A textual search finds 57 `ParamSpec(` occurrences — 54 data
> rows, 2 constructions inside the `_lmtd_specs()` loop, and 1 class statement. The citation
> dossier's "54 parameters, 40 confirmed / 14 pending" counts the literal rows only. The
> runtime catalogue that actually seeds the database is **70 rows, 56 confirmed / 14
> pending**. All three figures reconcile; the runtime figure is the one used here.

### `S-4` — a single effective date across four regime changes

`SEED_EFFECTIVE_FROM = date(2020, 1, 1)` for every row, with no `effective_to`. The minimum
CAR alone changed four times in that window:

| Date | Minimum CAR | Instrument |
|---|---|---|
| pre-Mar 2020 | 13% | CRD ¶71 minimum 10% **plus** the ¶75 CCB1 of 3% |
| 20 Mar 2020 | 11.5% | MPC PR 18 Mar 2020 ¶9(ii) + Notice BG/GOV/SEC/2020/01 |
| 1 Apr 2022 | 13% | MPC PR 21 Mar 2022 ¶30 |
| Dec 2022 (DDEP) | 10% | CCB1 set to zero; the CRD text was never amended |
| Jan 2026 | 13% | Relief expired; MPR March 2026 §6.3.2 p.33, MPR May 2026 §6.3.2 p.36 |

**The mechanism matters more than the number:** BoG never amended the CRD. CAR must be
modelled as *minimum + buffer*, not as one undated scalar. A single-dated seed cannot be
correct.

### Findings withdrawn during the audit, recorded

Two seed findings raised early in the programme were later disproved and are recorded here
so neither is acted on:

- **`S-2` ("there is no `car_min` seeded for `institution_class / bank`") is withdrawn.**
  The row exists: `ParamSpec("institution_class","bank","car_min","13","percent","Basel CRD (10% + 3% CCB)","confirmed")`.
  The original finding came from a regex requiring ≥7 quoted strings terminated by `),\n`,
  which silently dropped multi-line calls with trailing-comma formatting. A balanced-paren
  re-parse found it immediately. Its real defect is a missing date and paragraph locator,
  not a missing row.
- **`S-1` is partly withdrawn.** The hypothesis that "Act 930 s.29 is *enabling only*" is
  refuted — **s.29(2) does state a 10% statutory floor.** The surviving concern is narrower:
  it is *a floor standing in for a prescription that does not exist*, because the CRD
  excludes SDIs by its own ¶2.

---

## 6. Where `confirmed` is not currently justified

The recurring defect found across four independent research streams is **not a wrong number
— it is a wrong `confirmation_status`.** Values are seeded `confirmed` while resting on an
*enabling* statute, a **repealed** instrument, a **secondary** source, or an **exposure
draft**. The `pending` state exists precisely for these and is under-used.

### 6.1 Twelve rows cite a repealed instrument (`NEW-20`) — all marked `confirmed`

```
$ uv run python -c "... [s for s in SEED_PARAMETERS if 'NBFI' in s.source_citation]"
rows citing an NBFI instrument: 12    all confirmed? {'confirmed'}
```

| Rows | Citation |
|---|---|
| `statutory_reserve_fund_pct` (bank, sdi) | "Act 930 s.34; NBFI r.7" / "NBFI r.7; Act 930 s.34" |
| `primary_liquidity_reserve_pct` 10, `secondary_liquidity_reserve_pct` 15 (sdi) | "NBFI Business Rules 2000 r.11" |
| `prov_standard` 0, `prov_substandard` 20, `prov_doubtful` 50, `prov_loss` 100 (sdi) | "NBFI Rules 2000 r.19" |
| `npl_dpd_threshold` 90, `dpd_substandard_min` 90, `dpd_doubtful_min` 180, `dpd_loss_min` 360 (sdi) | "NBFI Rules 2000 rr.17-19" |

The *Non-Bank Financial Institutions (Bank of Ghana) Business Rules, 2000* were made under
**PNDCL 328** and repealed by **Act 774** and subsequently **Act 930**. They are no longer
hosted on bog.gov.gh, appear in neither the 70-entry directives register nor the 917-entry
notices register, and return nothing from the site's own search. Act 930 s.157(2) saves
instruments made under the Banking Act 2004 and its amendment — **not** instruments made
under PNDCL 328. Two independent research streams reached the same verdict: *historical
context only.*

**There is nothing to substitute.** No current BoG instrument gives SDIs a distinct
five-band classification and provisioning schedule. The two current instruments —
Notice BG/GOV/SEC/2020/01 and Notice BG/GOV/SEC/2025/23 — apply **one** set of norms to
banks, SDIs and NBFIs jointly. The correct action is to move these rows to `pending` and
leave them there.

The `statutory_reserve_fund_pct` rows carry a second, independent defect: Act 930 s.34(1) is
a **three-band sliding scale**, and 50% is correct only in the first band.

### 6.2 The bank five-band table is secondary-sourced but marked `confirmed` (`NEW-21`)

`prov_standard` 1 / `prov_olem` 10 / `prov_substandard` 25 / `prov_doubtful` 50 /
`prov_loss` 100, cited *"BoG loan classification (5-grade)"* — a citation naming no
document. No BoG-published instrument sets out the table; the best available source is an
audited annual report. Partial primary support exists: **OLEM 10%** is verified via MPC PR
21 Mar 2022 ¶30, and **CRD ¶142 p.30** presupposes *"no less than 25%"* / *"no less than
50%"* for past-due **risk weighting** — which is not a provisioning rate.

`NEW-22`: OLEM is also time-varying — 10% → 5% (Notice BG/GOV/SEC/2020/01, 20 Mar 2020,
p.1 item (iii)) → 10% (MPC PR 21 Mar 2022 ¶30, effective 1 Apr 2022).

`NEW-23`: **BoG contradicts itself on the OLEM band.** *FSR 2020* p.22 item 10 describes
OLEM as *"1 to 30 days in default"*, contradicting the 30-to-<90-day band used elsewhere
**and** Notice 2020/01 item (iv) (up to 30 days = Current). Flagged, not silently resolved.

### 6.3 Eight LMTD floors are an exposure draft presented as a floor (`NEW-24`)

`app/services/liquidity_thresholds.py:47` holds `BANK_MINIMUM_PCT` = exactly the Liquidity
Monitoring Tools Directive's bank column (80/100/50/70/60/80/30/50), and the surrounding
comment calls them *"LMTD Table 1 published minimums for BANKS"*. The **Liquidity Monitoring
Tools Directive (February 2026) is an exposure draft that does not take effect until
1 January 2027.**

Measured: `grep -rn "exposure draft" backend/app/` returns **10 hits — none in
`liquidity_thresholds.py`.** The draft status *is* disclosed in
`app/services/regulatory_reporting/templates.py` and `registry.py`; it is **not** disclosed
where the numbers are used as the fallback regulatory floor. Mitigating: a board row
overrides, and the board register is the primary mechanism — so the defect is the fallback
and the framing, not every computed ratio.

Three further February 2026 instruments the platform builds against are likewise exposure
drafts, all stated effective 1 January 2027: the Liquidity Risk Management Directive, the
Directive on Stress Testing, and the ICAAP guideline (`NEW-28`). Comment windows closed
30 June 2026; the register shows nothing newer than the 19 February 2026 postings.

### 6.4 Other rows that cannot honestly stay `confirmed`

| Row | Stored citation | Defect |
|---|---|---|
| `universal_bank` / `financial_holding_company` paid-up 400 | "BoG minimum capital (banks)" | Names no instrument. Correct citation is Notice BG/GOV/SEC/2017/19, 11 Sep 2017 — re-confirmable. The row also applies the bank figure to holding companies, for which Act 930 s.28(4) requires a separate prescription that was **not found**. |
| `savings_and_loans` / `finance_house` paid-up 15 | "SDI Subsector ToR" | An internal terms-of-reference document is not a regulatory source. **Downgrade.** |
| `microfinance_bank` paid-up 2 | "MFI Framework 2026" | The citation names the 2026 framework but the value is the 2015 figure. Notice BG/GOV/SEC/2026/03 §3.1.3 sets 50,000,000 transitioning / 100,000,000 for new entrants. **Value and citation disagree with each other.** |
| `rural_community_bank` paid-up 1 | "SDI Subsector ToR" | Value correct per Notice BG/GOV/SEC/2015/08 ¶1.1, but the class becomes Community Bank at 5,000,000 from 31 Dec 2026. |
| bank `car_min` 13 | "Basel CRD (10% + 3% CCB)" | **WRONG ISSUER, not just an undated citation.** Re-read at HEAD 2026-08-22 (`backend/app/services/regulatory_parameters.py:171`, inside the `ParamSpec` at `:165-173`): the string is still `"Basel CRD (10% + 3% CCB)"`. The CRD is the **Bank of Ghana**'s Capital Requirements Directive (June 2018) — ¶71's 10% minimum plus the ¶75 CCB1 — **not Basel's**. The arithmetic is right and matches the in-force figure; the attribution tells staff a Ghanaian requirement is an international one, which is the mirror image of the dashboard defect MILESTONE 13 corrected in the other direction. It is also undated across four regime changes (13 → 11.5 → 13 → 10 → 13) and cites no paragraph, and `ParamSpec` (`:109-118`) carries **no `effective_from` field at all**, so the row cannot be dated without a schema change. Ships `confirmed`. Raised as `WS-A12-2`; **still open at the close of the WS-A12 execution pass.** |
| sdi `car_min` 10 | "Act 930 s.29" | Cite **s.29(2)** and record it as a statutory floor standing in for an absent SDI directive. |
| `large_exposure_limit_pct` 20 / 15 | "Large Exposures Directive Sept 2025" | Values correct per ¶12, but the directive is **not in force until 1 Jan 2027**, and the 15% reaches only savings-and-loans and finance houses. |
| `large_exposure_id_threshold_pct` 10 | "BoG LE return (BSD) 10% identification" | Correct value, **wrong authority** — it is LED ¶11 and Act 930 s.156, not a return. |
| `aggregate_large_exposure_cap` 8 (both classes) | "LED aggregate cap (×NOF; value pending BoG)" | **Contradicted.** ¶13 gives 6× for banks, ¶14 gives 4× for S&L/finance houses. Neither class is 8. Correctly held `pending`. |
| `related_party_limit_pct` 25 (both classes) | "Act 930 related-party (value pending BoG)" | **Contradicted.** Act 930 gives four figures for four relationships: s.67(2) 10%, s.67(3) 5% unsecured, s.67(5) 20% aggregate, s.64(2) 25% to affiliates. One code cannot carry four scopes. |
| six sdi `risk_weight_*` | "SDI simplified risk weights (value pending BoG)" | No SDI schedule exists. The stored set matches the **superseded** Form BSD 5A bank column, so its resemblance to an official template is a trap. Correctly `pending`; **should stay `pending`.** |
| `single_obligor_limit_pct` 25 | "Act 930 s.62(1)" | Correct and precisely located — one of the few rows that fully meets the bar. Needs an end date of 31 Dec 2026. |
| `hqla_*` haircuts and caps | BCBS 238 ¶50/¶52/¶47 | Honest and precisely located. They are **Basel defaults filling the gap left by the unpublished BoG LCR directive** and should be labelled that way on bank-facing surfaces. |
| `hqla_l2b_haircut_pct` 50 | declared modelling choice | Ships **`pending`** deliberately: Basel sets L2B by sub-class (25% RMBS ¶54(a), 50% corporate/equities ¶54(b),(c)) and the canonical fact model carries only an HQLA *level*, so 50% is a conservative bound, not a BoG number. |
| `balance_identity_tolerance_pct` | internal data-integrity control | Not a regulatory parameter; correctly `pending`. |

### 6.5 The single highest-value corrective action

A **re-status pass over all 56 `confirmed` seeds**, not a re-computation. The values are
mostly right; the statuses are not.

---

## 7. Two migrations requested and deliberately not created

Both were deferred to integration because the Alembic head moved during the programme and a
duplicate-revision collision had already occurred once:

1. `ALTER TABLE temenos_connections ALTER COLUMN default_currency DROP DEFAULT` — drops the
   `'GHS'` server default from `202607170007`. (Partly addressed by `202608220033`.)
2. `ADD CONSTRAINT fk_regulatory_parameter_jurisdiction FOREIGN KEY (jurisdiction_code)
   REFERENCES jurisdictions(code)` — **the control plane's jurisdiction is unconstrained
   today**, so a typo creates an unreachable parameter scope.

Per the repository's own operating rules, any data step runs under `WORKER_DATABASE_URL`,
and the primary must be reconciled to head before deploy.

---

## 8. Jurisdiction defaults

Jurisdiction and currency are decisions, not defaults. Seventeen sites were corrected — the
nine the audit identified plus eight it missed:

- `RegulatoryParameterMixin.jurisdiction_code` (propagating to **9 parameter tables**)
- `bank_financial_facts.currency`, `regulatory_parameter.jurisdiction_code`
- two operator/desk schemas, `enterprise_stress.py:658`, `regulatory_parameters.py:745`
- `loan_classification.py:176,223`, `liquidity_thresholds.py:75`,
  `le_generation.py:328,1207,1933,2543`, `bog_forms/sources_ext/bsd11.py:301`

All are ORM-side; no migration was needed. The guard suite is
`backend/tests/services/test_jurisdiction_neutrality.py`.

Deliberate exceptions, kept literal because they are Ghana-factual content: the BoG return
artifacts (BSD templates and registry), ORASS/DBK rules, notice citations, and the GHS '000
unit convention on BoG forms.
