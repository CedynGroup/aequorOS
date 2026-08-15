# Gap Analysis + Remediation Plan: Market Research Desk — Weekly Rates Workflow

**Status:** Gap analysis complete. Product decisions locked (see §11). Remediation plan ready for approval. **No code until plan approved.**  
**Scope:** Rates-first weekly determination and publishing (lending, BoG, interbank, GRR, competitor APR). Curves secondary until rates flow is solid.  
**Primary references:** `docs/internal/AequorOS_Market_Data_and_Curve_Platform.md`, `console/` Markets Desk, `backend/app/services/market_desk/`, `backend/app/models/market_desk.py`, client `dashboard/.../markets/`.

---

## 0. Executive verdict (blunt)

A **substantial backend spine already exists** and is more serious than a prototype: global desk tables, Tier-1 Ghana source adapters, nightly capture job, methodology register (Track 1 / Track 2), bitemporal determinations, maker-checker, publication through `aequor_desk` → `pull_runner`, client Markets consumption, and (by design) full curve construction including synthetic AGD.

What does **not** exist is the product you described as non-negotiable:

> A guided weekly **rates** desk: new weekly capture waiting for review → review raw/cleaned → research adjustments → Review & Confirm → Supervisor approval → formal publish.

What exists instead is closer to:

> A **state-machine operator console** around an automated **nightly capture → auto-compute → auto-submit to pending_review** pipeline, with a single determination detail page whose primary intellectual center of gravity is **curves + QA gates**, not a rates research workflow.

**How the system treats this feature set today:** enterprise-shaped data model and governance vocabulary, but **incomplete as a professional research desk experience**. It is not a toy admin CRUD screen — but it is still **pipeline-and-inventory oriented**, not a guided, rates-first Market Research Desk. The as-built header proudly claims Stages 0–2 (including multi-curve and dual-curve engine adoption). That is **ahead of your stated priority order** (Rates solid and production-ready **before** forward curves / OIS).

---

## 1. What currently exists

### 1.1 Platform placement (correct architectural choice)

| Layer | Location | Role |
|---|---|---|
| Staff control plane | `console/` (separate app, operator auth) | Markets Desk publishing surface |
| Desk domain | `backend/app/services/market_desk/`, `backend/app/models/market_desk.py` | Global golden-copy production (not RLS-tenant) |
| Operator API | `backend/app/operator/features/desk.py` | `/operator/v1/desk/*` |
| Quant | `backend/app/domain/curves/` | Bootstrap, NSS, forwards, meeting-date OIS step, cointegration diagnostic |
| Distribution | `app/adapters/market_data/aequor_desk/` + `pull_runner` | Publish into every bank as vendor `aequor_desk` |
| Client consumption | `dashboard/app/(app)/markets/` | Rates board, curve board, FX, overlays |

This matches the industry control-plane pattern (staff console ≠ bank product). That part is right.

### 1.2 Data model (built, serious)

From `backend/app/models/market_desk.py`:

- **`desk_source_captures`** — silver layer, raw as-is, SHA-256, status `captured|parsed|failed`
- **`desk_observations`** — cleaned series points; append-only supersession; manual or capture-linked provenance
- **`desk_methodologies`** — versioned parameter register (`draft|approved|retired`)
- **`desk_determinations`** — COB valid-time, `published_at` transaction-time, input snapshot + digest, derived_values, qa_results
- **`desk_publications`** — per-bank fan-out results (`complete|partial|failed`)

Determination statuses: `draft → pending_review → approved → published`, plus `rejected`, plus supersede → new draft.

### 1.3 Ingestion / capture (built, multi-source)

- **16 Tier-1 sources** registered (BoG wpDataTables, auction PDFs, APR PDF, SEFD, GFIM XLSX/PDF, GSS PxWeb)
- **Nightly job** `desk_capture` (`capture_job.py`), gated by `DESK_CAPTURE_ENABLED`
- Cadence-aware: daily / weekly / monthly / per_event; Friday **auction_pass** re-pull for T-bill/BoG bill sources
- On success path: capture → observations → **auto open draft → compute → auto submit to `pending_review`**
- Job identity: `desk-capture-job@aequoros.system` as preparer
- Job **never publishes** (correct governance boundary)

### 1.4 Calculation pipeline (built — rates *and* curves)

`calculation.py` implements a full §5-style pipeline:

**Rates emitted (subset of product):**

- Pass-through: MPR, GRR, USD/GHS mid/ref (where present)
- Windowed: interbank ON
- Derived: T-bill 91/182/364 true yields (ACT/364)
- Derived: `GHS.LENDING.INDICATOR` from per-bank APR set
- Derived: GRR-consistent base
- APR series as pass-through family

**Curves (built, prominent):**

- `AEQ.GHS.SOV.ZERO`, `AEQ.GHS.SOV.FWD`, `AEQ.GHS.OIS` (synthetic AGD)
- Forward smoothness QA hard gate
- Cointegration as **diagnostic only** (calibrated rejection on Ghana data — honest)
- NSS fallback when thin

**Reproducibility:** value-based input digest; pure `run_pipeline` from snapshot × methodology parameters.

### 1.5 Maker-checker & Track 2 (built at state-machine level)

- Track 1: submit / approve / reject / publish / supersede APIs
- Four-eyes: reviewer ≠ preparer (enforced)
- Hard QA gate blocks approve when `qa_passed=false`
- Track 2: methodology propose version + approve version; console deliberately heavier UI
- Operator mutations go through `record_operator_action` (audit rows exist)

### 1.6 Console UI (built as inventory + single detail surface)

Tabs under Markets Desk:

1. **Determinations** — list + “New determination” by COB date  
2. **Observations** — ledger + manual entry fallback  
3. **Methodology** — Track 2 register  
4. **Sources** — static registry cards + capture history (no pull-from-UI)  
5. **Publications** — fan-out history  

Determination detail (`determinations/[id]/page.tsx`): lifecycle rail; action buttons (Compute / Submit / Approve / Reject / Publish); QA panel; **curve cards first**; rates table grouped by treatment; collapsible input snapshot.

### 1.7 Client Markets (built consumption)

- Rates board groups policy/GRR, money market, lending/APR
- Curve board multi-curve aware
- Overlay drawer (per-bank private spreads)
- Attribution + freshness chips

### 1.8 Documented as-built claim (2026-08-11)

Spec header states Stages 0–2 built, first real determination published 2026-08-09 (~447k observations, 593 series), dual-curve engine adoption live. Still open: entitlement tiers, Stage 3 credit, Stage 4 true OIS, expansion markets.

---

## 2. Exact weekly workflow required (your non-negotiable)

| Step | Actor | Required product behavior |
|---|---|---|
| 1 | System | Automated **weekly** scrape/ingestion of Tier-1 rates data |
| 2 | Analyst | Opens desk; **newly scraped weekly data clearly waiting** (“New weekly capture”) |
| 3 | Analyst | Reviews raw/cleaned data → **Next** |
| 4 | Analyst | Implements **research adjustments** (spreads, overrides, research assumptions) → **Review & Confirm** |
| 5 | System | Package pushed to **Supervisor** for review/approval |
| 6 | Supervisor | Approves → **formal publish** to client banks |

Must feel like a professional multi-step research desk with distinct states; Track 1 (weekly determination) visibly separate from Track 2 (methodology change). Rates first; curves not the product focus until rates are solid.

---

## 3. Workflow divergence matrix (required vs as-built)

| Required step | As-built behavior | Verdict |
|---|---|---|
| **1. Weekly automated scrape** | Nightly `desk_capture` with weekly/monthly cadence gates + Friday auction pass | **Partial.** Ingestion is real and multi-source. Product framing is **nightly staging**, not a **weekly rates package** concept. |
| **2. “New weekly capture” waiting for review** | Determinations list / Sources capture history. No desk home, no inbox, no “this week’s capture package” object, no unread/new badge for analysts | **Missing.** |
| **3. Analyst reviews raw/cleaned → Next** | Observations is a separate tab (filterable ledger). Determination shows input snapshot collapsed at bottom *after* compute. No guided review step; no field-level confirm of parser extraction vs source snippet | **Missing as guided step.** Review is optional browsing, not a gate. |
| **4. Research adjustments (spreads, overrides, assumptions)** | Track 1 applies fixed methodology parameters. “Research assumptions” live as **methodology JSON** (Track 2). No determination-scoped adjustment model, no override editor, no spread entry UI on the weekly path | **Missing / philosophical conflict** (see §5). |
| **5. Review & Confirm → Supervisor** | “Submit for review” button on same page. Capture job often **already submitted** with robot as preparer — human analyst never did maker review/adjust | **Broken relative to your roles.** Auto-submit skips the human Analyst maker. |
| **6. Supervisor approve → publish** | Approve + deliberate Publish with arming. Four-eyes works when preparer ≠ reviewer. Robot preparer means any human can approve (SoD against the job, not Analyst→Supervisor dual control of judgment) | **Partial.** Mechanical maker-checker exists; **role model does not match Analyst → Supervisor of researched package**. |
| Guided multi-step UX | Single detail page; buttons flip status | **Missing.** Inventory console, not a wizard/rail of panes. |
| Rates-first | Rates table secondary; curves + AGD QA dominate detail UX and pipeline | **Inverted priority.** |
| Track 1 vs Track 2 separation | Separate nav tabs; Track 2 heavier ceremony | **Present and good.** |

---

## 4. Gaps by surface

### 4.1 UI gaps (console Markets Desk)

| Gap | Detail |
|---|---|
| **No Research Desk home / work queue** | No “this week” package, no pending captures, no “awaiting analyst review” vs “awaiting supervisor” columns as first-class inbox. |
| **No multi-step determination wizard** | Missing panes: (1) Capture & provenance, (2) Cleaned rates review, (3) Adjustments, (4) Package summary & confirm, (5) Supervisor decision. Today: one scrollable page + buttons. |
| **No “New weekly capture” affordance** | Capture job stages quietly into `pending_review`. Analyst is not pulled into a review. |
| **No field-level source review** | Spec §11a calls for proposed value + source snippet before write. Console never shows PDF/HTML excerpt next to extracted value for confirm/correct. |
| **No research adjustment screen** | No spreads / overrides / assumption editors on Track 1. Cannot implement weekly judgment in product language you specified. |
| **Rates under-emphasized** | Curve cards and forward QA occupy primary real estate. Rates are a lower section. Competitor APR is not a dedicated weekly review board. |
| **No week-over-week package comparison** | No delta view vs last published rates package (critical for a real desk). |
| **No supervisor-specific queue** | Same list for everyone; no role filter, no “items needing my approval,” no SLA/age. |
| **No package checklist / readiness** | No explicit “all Tier-1 weekly series present / missing / stale / failed capture” dashboard before publish. |
| **Sources page is read-only status** | Deliberately no on-demand pull. Fine for automation purity; weak for “desk re-pull this source now and review.” |
| **Observations decoupled from determination flow** | Manual fix lives on another tab; determination snapshot is frozen at create/compute — workflow for “fix observation then recompute package” is tribal knowledge, not guided. |
| **Client Markets is consumption-only** | Correct for banks. Gap is not here; gap is the staff desk experience that *feeds* it. |

### 4.2 Backend gaps

| Gap | Detail |
|---|---|
| **No weekly package / cycle entity** | Only COB-dated determinations. No `weekly_rates_cycle` (week-of, expected series set, capture completeness, analyst sign-off timestamps). |
| **Capture job collapses Analyst steps** | `_stage_determination` does create + compute + `submit_for_review` in one shot. **This actively prevents the required Analyst workflow.** |
| **No determination-scoped adjustment model** | No table/API for overrides, additive bps, expert-judgment notes bound to a determination and included in input digest / audit. |
| **No intermediate states for guided flow** | Only draft/pending_review/approved/rejected/published. Missing e.g. `awaiting_capture_review`, `adjusting`, `ready_for_submit` if product wants true step gates. |
| **Submit does not require compute** | `submit_for_review` only checks status=`draft`. Empty derived_values can be submitted (publish later refuses empty; still a governance hole for review content). |
| **Rates not a separable publishable product** | One methodology/pipeline produces rates **and** curves. Cannot publish a **rates-only** package without curve build success (forward QA can block the whole determination). |
| **Hard QA coupling rates publish to curve quality** | If sovereign forward oscillation fails, rates (MPR, GRR, T-bills, APR) that are already valid may be stuck. Wrong for rates-first product discipline. |
| **Weak series completeness contract for “weekly rates”** | Snapshot builds what exists; missing series may be skipped rather than blocking a weekly rates package with an explicit incompleteness state. |
| **No structured audit event model for research judgment** | Operator action rows exist; no first-class “override applied / rationale / previous→new / who / when” evidence object banks’ model risk would expect. |
| **Publication is sync in-process fan-out** | Works, but not desk-UX related; fine for now. |
| **Entitlements (§10)** | Still open (as-built). Not rates-workflow critical for first customer set, but not enterprise-complete. |
| **Field-level validation API for parser proposals** | No “proposed observation from capture awaiting steward confirm” state — parse goes straight to observations. |

### 4.3 Product / experience characterization

| Dimension | Assessment |
|---|---|
| Too light / startup-y? | **Not in data model.** Global desk tables, digests, dual control, publication lineage are serious. |
| Reporting-oriented? | **Partly.** Console reads like status/ops inventory (lists, badges, JSON-ish trees) more than a research decision surface. |
| Incomplete desk experience? | **Yes.** Missing inbox, guided steps, weekly package framing, adjustment judgment, rates primacy, source-snippet review. |
| Priority inverted? | **Yes.** Engineering energy went into curve construction, AGD calibration, dual-curve engine adoption **before** the weekly rates determination UX is production-grade for a research desk. |
| Governance vocabulary vs operating model | Vocabulary (Track 1/2, maker-checker) is present; **operating model** (Analyst researches weekly package → Supervisor approves) is not fully embodied because the robot is the preparer and adjustments don’t exist on Track 1. |

---

## 5. Critical product tension (must resolve before remediation)

### Spec §5 principle (as implemented)

> **Governed methodology, not weekly judgment.** Track 1 confirms *correct application* of fixed parameters. Changing assumptions is Track 2 (rare, heavier).

### Your required workflow language

> Analyst **implements research adjustments** (spreads, overrides, research assumptions) every week before Review & Confirm.

These are **not the same product**.

| Option | Meaning | Implication |
|---|---|---|
| **A. Strict IOSCO Track-1** | Weekly run = application only; any spread/assumption change is Track 2 | Your step 4 must be reinterpreted as *confirm methodology application + optional documented expert-judgment only where methodology allows* |
| **B. Weekly expert-judgment layer** | Determination carries auditable, bounded adjustments (override value, additive bps, rationale, who/when) that enter the digest and publish set | Matches your written workflow; still must **not** silently rewrite methodology register |
| **C. Hybrid (recommended industry pattern)** | Methodology defines *which* fields may be expert-judged weekly, allowed ranges, mandatory rationale; Track 2 changes formulas/parameters; Track 1 records judgment within rails | Closest to benchmark administration practice (expert judgment disclosed, methodology fixed) |

**Gap analysis cannot pretend the system already supports B/C.** It implements **A**, with methodology parameters as the only place “research assumptions” live (`liquidity_premium_bps_by_tenor`, overnight window, GRR weights, etc.).

**Decision required before implementation:** which of A/B/C is the product truth for AequorOS rates.

---

## 6. Rates-first vs curves-first (priority gap)

| Area | Status | Rates-first compliance |
|---|---|---|
| BoG T-bill / bill rates ingest | Built | Good |
| Interbank ON / WAVG | Built | Good |
| MPR | Built | Good |
| GRR + reconstruction check | Built | Good |
| Competitor APR (BoG public) | Built | Good (publish/consume); weak in weekly **desk review** UX |
| Lending indicator / GRR base | Built | Good math; weak desk presentation |
| Sovereign zero/forward construction | Built + golden tests | **Ahead of mandate** |
| Synthetic AGD / dual-curve engines | Built + engine adoption | **Ahead of mandate** |
| Weekly rates package UX | Incomplete | **Fails mandate** |
| Rates-only publish path | Missing | **Fails mandate** |

Blunt: the quant layer for curves is further along than the research-desk product for weekly rates. That is the wrong order relative to your priority statement.

---

## 7. What “good” would look like (acceptance bar — not a plan yet)

For the weekly rates flow to be production-ready as a professional desk:

1. **Weekly rates package** is a first-class object (or determination mode) with expected series set, capture completeness, and explicit missing/failed sources.
2. Morning queue: **“New weekly capture — awaiting analyst”** is unmistakable.
3. Analyst is forced through **Review inputs → Adjustments (per agreed A/B/C) → Confirm** before Supervisor sees it.
4. Capture job **must not** auto-submit past human Analyst review (or must stage only into a pre-review state).
5. Supervisor queue and four-eyes are **Analyst vs Supervisor**, not **robot vs any human**.
6. Publish is deliberate; published rates appear on client Markets with provenance/freshness.
7. Full audit: who saw what, what was overridden, digests, methodology version, publish event.
8. Track 2 remains a visibly different, heavier path.
9. Curve build failures **do not block** publishing of pass-through official rates unless product explicitly chooses that coupling (default for rates-first: decouple).

---

## 8. Security note (out of band but urgent)

The chat message included a full **production** `backend/.env.production` with live DB, MinIO, Redis, vault, JWT, and SSO secrets. Those credentials should be treated as **compromised for operational hygiene purposes** and rotated in the secrets manager / Coolify env — regardless of this feature work. Do not commit that file; it is correctly gitignored.

---

## 9. Locked product decisions (2026-08-11)

| Decision | Choice | Consequence |
|---|---|---|
| Weekly research judgment | **B — Full weekly adjustments** | Determination-scoped overrides, spreads, research assumptions with rationale; enter digest + audit; methodology register unchanged unless Track 2 |
| Curve QA vs rates publish | **Rates-only publish allowed** | Pass-through/derived rates publish even when curve build/QA fails; curves secondary |
| Capture job handoff | **Draft awaiting Analyst** | Capture (+ optional pre-compute) stops in draft; Analyst must Review → Adjust → Confirm → Submit; Supervisor then approves |

These **supersede** the as-built capture-job auto-submit behavior and the pure “methodology application only” Track-1 stance for rates adjustments. Track 2 remains the path for methodology/parameter **definition** changes. Track 1 may now carry **per-determination research adjustments** without rewriting the register.

---

## 10. Remediation plan (concrete, rates-first)

### Guiding principles

1. **Enterprise research desk, not admin CRUD** — guided multi-step, rates-primary, auditable judgment.
2. **Build on existing spine** — do not rewrite desk tables, sources, or `aequor_desk` publication; extend them.
3. **Rates package is the product unit** — curves may still compute and display, but must not block rates publish.
4. **Robot prepares material; humans research and govern** — capture stages drafts; never auto-submits.
5. **Track 1 vs Track 2 stay visibly separate** — adjustments are determination-scoped; methodology edits stay on Methodology.

---

### Phase R1 — Backend operating model (unblocks the real weekly cycle)

**R1.1 Capture job: stop at draft**

- Change `_stage_determination` in `capture_job.py`:
  - create draft (robot preparer OK for materialization)
  - optionally pre-compute so Analyst opens a package with numbers already proposed
  - **remove** `submit_for_review`
- Job progress records `status: "draft_ready"` (or equivalent), never `pending_review`.
- Tests: update `test_desk_capture_job.py` expectations; add assertion that job never leaves `pending_review`.

**R1.2 Determination-scoped research adjustments (Option B)**

New persistence (prefer column/JSON on `desk_determinations` first; promote to child table only if multi-row editing complexity demands it):

```text
research_adjustments: [
  {
    series_code: "GHS.LENDING.INDICATOR",  // or curve tenor later
    kind: "override" | "additive_bps" | "assumption_note",
    value: "...",           // decimal string
    rationale: "...",       // required non-empty for override/additive
    applied_by: email,
    applied_at: iso8601
  },
  ...
]
```

- Writable only while `status == draft`.
- Included in a **package digest** (snapshot + adjustments + methodology version) so published values remain reproducible.
- API:
  - `PUT /desk/determinations/{id}/adjustments` (replace or upsert set)
  - recompute applies adjustments **after** methodology-derived rates (order must be documented and tested)
- Audit: every adjustment write → `record_operator_action` with before/after.

**R1.3 Rates package vs curve soft-gate**

- Pipeline still may build curves for display/diagnostic.
- Split QA outcomes:
  - `rates_qa_passed` — required for approve/publish of rates
  - `curves_qa_passed` — advisory for rates publish; may remain hard for a future curves-only package
- `ensure_approvable` / publish path gate on **rates** readiness, not forward-oscillation alone.
- Publication extraction: rates scopes always eligible when rates QA passes; curve scopes only when curves QA passes (partial publish OK; record in publication results).

**R1.4 Submit / approve integrity**

- `submit_for_review` requires:
  - non-empty derived rates
  - `rates_qa_passed` (or explicit Analyst acknowledgment of soft flags if we allow steward override — default: hard for rates completeness, soft for curve)
  - Analyst is a human operator (reject submit by robot identity if any path still tries it)
- Completeness API: expected weekly rates series checklist vs present/stale/missing/failed captures.

**R1.5 Expected weekly rates series set (v1 checklist)**

Minimum must-have for a weekly rates package (GHS beachhead):

| Series family | Codes (illustrative) | Source |
|---|---|---|
| Policy | `GHS.MPR` | BoG MPR table |
| Interbank | `GHS.INTERBANK.ON` (+ WAVG if available) | BoG interbank |
| T-bills | 91/182/364 discount and/or yield | BoG tender + auction |
| BoG bills | as available | BoG bill rates |
| GRR | `GHS.GRR` | GSS / BoG path |
| FX anchor | `GHS.USDGHS.MID` / ref | BoG FX |
| Competitor APR | `GHS.APR.*` (monthly — show last available) | BoG APR PDF |
| Derived | lending indicator, GRR-consistent base | pipeline + adjustments |

Missing must-haves surface as package incompleteness flags; Analyst can still adjust/override with rationale where Option B allows, but cannot hide missing source captures.

**Deliverables R1:** migrations/schemas as needed, service + API + tests, capture job behavior change, rates soft-gate, adjustment model.

---

### Phase R2 — Console Research Desk UI (enterprise)

**R2.1 Desk home / work queue (new default landing)**

Replace bare determinations list primacy with a **Research Desk home**:

- Banner: **New weekly capture** when draft(s) exist for current/latest COB with robot-prepared material
- Columns/queues:
  - Awaiting analyst (draft)
  - Awaiting supervisor (`pending_review`)
  - Published this week
  - Failed / incomplete captures
- Clear status chips; age since capture; rates QA / curves QA badges

**R2.2 Guided multi-step determination (rates-first)**

On `/desk/determinations/[id]`, replace single-page button soup with a **step rail**:

| Step | Name | Analyst actions |
|---|---|---|
| 1 | **Capture & inputs** | Review completeness, failed sources, raw→cleaned observations; link to captures; fix via manual entry + recompute |
| 2 | **Rates review** | Primary board: policy, money market, GRR, lending/APR; week-over-week deltas vs last published; staleness flags |
| 3 | **Research adjustments** | Apply overrides / additive bps / assumption notes with mandatory rationale; live recompute preview |
| 4 | **Review & Confirm** | Package summary, digests, methodology chip, QA; **Submit for supervisor** |
| 5 | **Supervisor** | Read-only package + adjustments audit; Approve / Reject (reason); then Publish (armed) |

- Curves live under a secondary tab/panel (“Curves & diagnostics”), never step 1.
- Track 2 remains only under Methodology tab (unchanged ceremony).

**R2.3 Provenance**

- Per rate: series code, as-of, source capture id / manual flag, quality flags, treatment, adjustment applied (if any).
- Where payload allows, surface parser attributes; full HTML/PDF snippet viewer is stretch (R3) if storage_path/payload available.

**R2.4 Supervisor surface**

- Distinct visual mode when status is `pending_review` (not the same as Analyst edit mode).
- Emphasize: adjustments made, rationales, deltas vs prior publish, four-eyes identity.
- Publish remains deliberate two-click arming.

**Deliverables R2:** console routes/components, API client updates in `console/lib/api.ts`, no client-bank dashboard changes required beyond existing rates board consumption.

---

### Phase R3 — Governance hardening & polish

- Optional operator role tags (analyst vs supervisor) if four-eyes alone is insufficient; otherwise document that any second operator is Supervisor for v1.
- Richer audit trail UI (timeline of capture → draft → adjustments → submit → approve → publish).
- Week-over-week package comparison polish.
- On-demand “re-capture this source” (audited) from Sources — optional, after core flow works.
- Field-level source snippet viewer if captures store usable payloads.
- Update `docs/internal/AequorOS_Market_Data_and_Curve_Platform.md` as-built header + §5 Track-1 language to reflect Option B + rates soft-gate (doc must not contradict product).

---

### Phase R4 — Explicitly later (do not start now)

- Curve-first research UX, multi-curve editor depth
- Stage 3 credit curve, Stage 4 true OIS
- Expansion markets
- Entitlement tiers (§10)
- Async publication job type

---

### Implementation order (PR-sized)

1. **PR1:** Capture job → draft only + tests  
2. **PR2:** Rates soft-gate (`rates_qa_passed` / publish rates without curve hard fail) + tests  
3. **PR3:** Research adjustments model + API + compute application + digest/audit + tests  
4. **PR4:** Completeness checklist API + submit guards  
5. **PR5:** Console work queue + multi-step wizard (Review → Adjust → Confirm)  
6. **PR6:** Supervisor mode + publish arming polish + week-over-week deltas  
7. **PR7:** Doc as-built + AGENTS.md desk note update  

Parallelization: PR1 and PR2 can run in parallel; PR3 depends on neither; PR5 needs PR3 API.

---

### Acceptance criteria (definition of done for rates flow)

1. After weekly/nightly capture, Analyst opens console and sees **“New weekly capture” / draft awaiting analyst** — not auto-pending_review.  
2. Analyst walks Review → Adjustments → Confirm; cannot submit without rates package readiness.  
3. Adjustments require rationale; appear in package digest and supervisor view.  
4. Supervisor ≠ preparer; approve then deliberate publish.  
5. Client Markets shows published rates with attribution/freshness.  
6. Curve QA failure does **not** block rates publish.  
7. Track 2 methodology change remains a separate, heavier path.  
8. Automated tests cover capture staging, adjustments digest, rates soft-gate, four-eyes, and submit guards.  
9. No new curve/OIS feature work claimed as part of this delivery.

---

## 11. Summary table

| Question | Answer |
|---|---|
| Does a Markets Desk exist? | **Yes** — console + backend + publication + client consumption. |
| Is it a full professional weekly rates research desk? | **No** — inventory/state-machine console, not guided research desk. |
| Maker-checker present? | **Mechanically yes; operationally incomplete** (robot preparer + no adjustments). |
| Track 1 / Track 2 separated? | **Yes** in nav and APIs. |
| Weekly capture automation? | **Yes** (nightly job, weekly cadence); not framed as weekly package. |
| Research adjustments on weekly path? | **No today; Option B locked for remediation.** |
| Rates-first? | **No today; remediation inverts UI and publish gates.** |
| Biggest single product break | **Capture job auto-computes and auto-submits, skipping Analyst.** |
| Locked decisions | **B adjustments · rates-only publish · draft for Analyst** |

---

## 12. Next step

Approve this remediation plan (phases R1–R3, PR sequence, acceptance criteria). On approval, implementation starts with **PR1 (capture job → draft only)** and **PR2 (rates soft-gate)** in parallel. No code before approval.

