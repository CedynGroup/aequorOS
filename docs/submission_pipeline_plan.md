# Submission Pipeline — Enterprise-Grade Completion Plan

**Status:** v1.0 approved-for-build plan · **Date:** 2026-07-24
**Trigger:** thorough review of the as-built module against the official *ORASS LRT Portal
User Guide v1.0* (BoG BSD/OFISD, Sept 2020 draft — primary source, upgrades several
research UNKNOWNs to DOCUMENTED) and an enterprise-grade bar. The submission pipeline is
the core product; every workstream below is in scope for the MVP — nothing deferred.
**Companions:** `docs/regulatory_reporting.md` (v1 build spec — superseded where this
plan says otherwise), `docs/research/bog_orass_submission_channels.md`,
`docs/research/bog_returns_and_templates.md`.

---

## 1. As-built verdict (evidence-based)

What exists and is genuinely strong (verified in code, not aspiration):

- Immutable versioned packages with supersession + partial unique index; full lineage
  (`source_runs` = module/run_id/input_hash/engine_version; provenance in every artifact).
- Lifecycle state machine with audit events on every transition; identity-based
  maker-checker; validation pipeline whose ERROR findings block approval.
- Six registered return families with honest fidelity grades; deterministic
  xlsx/csv/pdf exports with checksums into tiered object storage.
- ORASS sandbox (labeled, deterministic, config-driven behaviors) + email fallback that
  faithfully implements the BG/FMD/2026/07 "deemed complete only after re-upload" rule;
  write-only vaulted channel credentials.
- Deadline calendar with RAG + Act 930 s.93(3) penalty estimator.
- Deep backend test coverage (4 suites: API, workflow, channels, exports).

What is missing or below enterprise grade — the gap register in §2.

## 2. Gap register

### A. Regulator-decision fidelity (source: ORASS LRT guide)
- A1. No **Declined** (final refusal) distinct from **Rejected** (returned for
  correction). ORASS has both; we only have `rejected`.
- A2. No **supervisor comments** on rejection (ORASS "View Comments"); sandbox reject
  carries a message but the package stores/displays nothing.
- A3. No **Request Resubmission** flow (reason → granted/denied → new revision). Our
  regeneration exists but there is no resubmission-request entity or revision semantics
  (ORASS: 1.0 → 1.1) on the submission side.
- A4. Reference IDs are `SANDBOX-ORASS-...` blobs, not ORASS-style form-set-prefixed
  sequences (`PS01390`).
- A5. Per-revision audit log exists in data (events + approvals) but History does not
  render a revision-centric audit view.

### B. Roles & control (sources: ORASS guide roles + rbac.md)
- B1. `decide-approval` is not role-gated: any analyst+ non-generator can approve.
  Enterprise bar + ORASS Principal/Secondary split demand approver-class gating.
- B2. `submit` is not separately gated; ORASS reality: only the Principal user submits.
- B3. No ORASS Principal/Secondary user mapping anywhere in config.
- B4. UI still ships hardcoded `DEMO_OFFICERS` and a misleading "Acting as" selector;
  officer names not resolved from real users.

### C. Notifications & scheduling
- C1. Zero real notifications — no in-app feed, no email, nothing on state changes,
  approval queues, regulator decisions, or deadlines. NotificationDrawer is a hardcoded
  mock with inert buttons.
- C2. Scheduler never scans reporting obligations; overdue/due-soon states exist only
  when a user opens the Calendar page.

### D. Pipeline integrity & API completeness
- D1. No artifact-list endpoint; the UI's artifact panel is a session-local cache
  ("No artifacts exported this session" after reload).
- D2. Generated `downloadRegulatoryArtifact` op unused; download re-implemented via raw
  fetch.
- D3. No `(package_id, kind)` unique constraint on artifacts (app-code-only upsert).
- D4. No content hash of the package snapshot itself (`snapshot_sha256`).
- D5. History filtering is client-side over a 100-row page; no server filters/pagination.
- D6. Email fallback bundle is preview-only; no downloadable `.eml`/send artifact.

### E. Institution & corporate profile (source: ORASS guide §7/§12 — the master-data half)
- E1. Institution identity is a single thin `banks` row. No ORASS institution code on
  the profile (exports fall back to short_name; email bundle prints
  `INSTITUTION-CODE-UNSET`).
- E2. No corporate profile: institution type, legal entity structure, authorisation
  date, approved capital, TIN/registration numbers, incorporation date, ownership
  split (local/foreign %), stock-exchange membership/ISIN, licenses, name history.
- E3. No related-party register: shareholders (share classes, rights, %), directors
  (roles, appointment, allowances), key management personnel, external auditor (ICAG),
  ultimate beneficial owners (individuals only, linkable to corporate shareholders),
  outsourced service providers, liquidators — with CDD/EDD/personality-notes checklists.
- E4. No outlet register (branches/agencies, open/relocate/close lifecycle), no
  products/services register (BoG product-approval obligation).
- E5. No LRT return generation: outlet packs, capital-injection packs, product-approval
  packs, related-party packs, M&A/MTN/winding-up, ad-hoc submission covers. The LRT
  guide gives us the official form structures — these can now be built at
  CONFIRMED-per-guide fidelity.

### F. Return-family completeness (flagged in code as TODO/placeholder)
- F1. DBK daily family absent (FX-NOP monthly is an explicit placeholder); calendar
  cannot express daily obligations or time-of-day deadlines (T+1 10:00 Africa/Accra).
- F2. Large Exposures monthly Templates 1/1a/2/3/4 absent (published draft appendix —
  buildable now).
- F3. LMT Tables 1–10 unfilled (`TODO(RR-6)`); only the LCR-by-currency subset renders.
- F4. IRRBB shocks are Basel ±200bp, not the BoG GHS ±450bp appendix parameterization.
- F5. No T−1 comparative columns (BoG Reporting-vs-Previous-Month convention).
- F6. No SOLO|CONSOLIDATED basis dimension on packages (Act 930 runs both bases live);
  basis is only a channel-config string today.
- F7. Placeholder deadlines (BSD2 day-14, FX-NOP day-10) are not overridable per bank;
  the honest fix is bank-level deadline overrides sourced from the BoG onboarding pack.

### G. Verification
- G1. Dashboard has zero test tooling; no e2e coverage of any submission journey.

## 3. Workstreams (all in scope — build order §4)

### W1 — Regulator-decision fidelity (closes A1–A5)
1. Add `declined` package status (terminal, regulator final-no). Transitions:
   `submitted → {acknowledged, rejected, declined, submitted}`; `declined → {}`.
   Sandbox behavior `decline` added alongside ack/reject/slow.
2. `regulator_comments` captured on rejection/decline events (sandbox supplies labeled
   simulated comments); Returns + History render a "Supervisor comments" panel.
3. New `regulatory_resubmission_requests` table (package_id, reason, status
   requested|granted|denied, decided_at, detail JSON) + endpoints
   (requestResubmission, decideResubmission — sandbox auto-decides per config).
   Granting mints the next package version (existing supersession machinery) and
   stamps `submission_revision` (1.0 → 1.1) on the new version.
4. Sandbox reference IDs become form-set-prefixed sequences per family
   (`BSD3-000123`-style, per-bank monotonic), still SANDBOX-labeled in detail payloads.
5. History gains a revision-centric audit view (revision × status × actor × timestamps
   — same rendering ORASS's View Audit Log shows).

### W2 — Roles, maker-checker hardening, ORASS user mapping (closes B1–B4)
1. Role gates (roles already ride the JWT): `decide-approval` requires `approver`+;
   `submit`/`poll` require `approver`+ (configurable down to analyst via org setting
   later — default strict). Generate/validate/export stay analyst+. This is the
   rbac.md §15 Phase 0 ROLE_PERMISSIONS slice for this module, implemented now.
2. Channel config gains `principal_user_name/email` and `secondary_users[]` (ORASS
   portal user mapping); submit UI surfaces "ORASS Principal: X" beside the button.
3. Kill `DEMO_OFFICERS`: new `listOrganizationUsers` lookup (id → display name, role);
   approvals/history resolve real names. Remove the "Acting as" selector.

### W3 — Notifications + deadline engine (closes C1–C2)
1. `notifications` table (org, recipient_user_id nullable = org-wide, type, severity,
   title, body, entity kind/id, read_at) + API: list (cursor), markRead, markAllRead.
2. Emission points (service-level, transactional with the triggering commit):
   pending_approval → approver-role users; approved → submitter roles;
   acknowledged/rejected/declined/resubmission-decision → maker + approver;
   downtime email submission → daily reminder until re-upload clears.
3. Worker job `reporting_deadline_scan` (daily, plus hourly within due-soon window):
   emits due-soon (T−7/T−3/T−1), overdue, and escalating-overdue notifications from
   `list_obligations`; idempotent per (obligation, threshold).
4. Dashboard NotificationDrawer becomes real: feed from API, unread badge, working
   mark-read/mark-all; deep links to the obligation/package.
5. Optional SMTP mirror (new `SMTP_*` settings, default off; when configured, sends
   the same notification content by email). In-app is always on.

### W4 — Corporate profile & related-party register (closes E1–E4)
New tenant-scoped, RLS-forced tables (one migration):
- `institution_profiles` (1:1 bank): institution_type, legal_entity_structure,
  authorisation_date, approved_capital, incorporation_date, tin, registration_number,
  orass_institution_code, traded_on_exchange, exchange_name, isin,
  ownership_local_pct, ownership_foreign_pct, parent_country_code (FK jurisdictions).
- `related_parties`: party_type individual|legal_entity, names, contact JSON,
  regulated-elsewhere flag/jurisdiction.
- `related_party_roles`: party FK, role enum (shareholder, director, board_chairman,
  board_secretary, kmp_* officer roles, external_auditor, ultimate_beneficial_owner,
  outsourced_company, outsourced_individual, liquidator, other), appointed_on, term,
  allowances/fees fields (directors), other_responsibilities text.
- `shareholdings`: party FK, share_type/subtype, rights, number, pct, ubo_party FK.
- `outlets`: type branch|agency|head_office, name, number, address JSON, status
  active|closed, opened_on, relocated_from, closure_reason.
- `bank_products`: name, type, status proposed|approved|withdrawn, approval_reference.
- `bank_licenses` + `bank_name_history`.
Rules: every mutation requires a non-empty reason + audit event (canonical-mutation
convention); no seeding — rows enter by manual entry (master data) or Data Engine CSV
templates (new `related_parties.csv`, `outlets.csv` templates in lib/templates).
Exports/envelope: institution block enriched (orass_institution_code, basis, preparer/
approver names); email bundle drops `INSTITUTION-CODE-UNSET` when profile is set.
UI: Governance → **Institution Profile** (tabs: Profile · Related parties · Outlets ·
Products & licences · History). Settings institution card links to it.

### W5 — LRT corporate return packs (closes E5; depends on W4)
New return family `corporate` (regulator BOG, event-driven — not periodic):
- Packs, each a normal package through the SAME lifecycle (generate → validate →
  approve → export → record submission): Update Corporate Profile; Outlet Opening /
  Relocation / Closure (incl. projected-investment + 3-year operating-results tables
  and required-documents checklist); Related-Party Addition (per role, with CDD/EDD/
  personality-notes checklists); Capital Injection (existing / new shareholder /
  retained-earnings transfer); Product/Service Approval (Declaration, MOU, Resolution,
  Submission-of-Payments checklist); Ad-hoc Submission cover.
- Generators pre-fill from W4 master data; artifacts = PDF pack in ORASS form order +
  XLSX data sheet + document checklist. Fidelity: CONFIRMED-per-LRT-guide (v1.0 draft
  caveat stated in the template notes).
- Calendar: event-driven obligation type (created with the pack, due per configured
  SLA) rendered alongside periodic obligations.

### W6 — Return-family completeness + calendar engine (closes F1–F7)
1. Daily frequency support: obligations at business-day grain with datetime deadlines
   (T+1 10:00 Africa/Accra); DBK family (DBK 102/300/400/700 representative layouts
   from the NOP directive facts) generated from derived FX/NOP facts.
2. Large Exposures family: Templates 1/1a/2/3/4 from the draft appendix; generator from
   canonical loan exposures + counterparty groups (concentration derivation added to
   fact pipeline where needed).
3. LMT Tables 1–10 filled from forecasting/maturity analytics (retires TODO(RR-6)).
4. IRRBB BoG shock set (±450bp GHS et al.) as parameters (param table row, effective-
   dated), Basel set retained as fallback; template notes updated.
5. T−1 comparatives: resolver pulls the prior-period package snapshot (or prior period
   runs) and renders the BoG Reporting-vs-Previous-Month columns; never fabricates —
   blank + note when no prior exists.
6. `basis` column (solo|consolidated) joins package identity (unique index becomes
   (org, bank, return_code, reporting_date, basis) WHERE status != 'superseded');
   UI basis selector; config default per bank.
7. Bank-level deadline overrides (`reporting_deadline_overrides` on channel/reporting
   config): placeholders stay labeled until the bank enters its onboarding-pack values.

### W7 — Pipeline hardening, UI completion, e2e (closes A5-UI, D1–D6, G1)
1. `listPackageArtifacts` endpoint; UI artifact panels persist across sessions; use the
   generated download client op (single download path).
2. Migration: `snapshot_sha256` on packages (computed at generation, verified at
   export); unique `(package_id, kind)` on artifacts.
3. Server-side package filters + pagination (family, status, date range); History uses
   them.
4. Email fallback: downloadable `.eml` artifact (kind `eml`) alongside instructions.
5. Playwright e2e suite in the dashboard (new tooling): journey 1 happy path
   (generate→validate→approve[second user]→export→submit→ack), journey 2 downtime →
   email fallback → re-upload clears, journey 3 rejection → comments → resubmission →
   revision 1.1, journey 4 corporate profile → LRT outlet pack, journey 5 role gates
   (viewer read-only, analyst cannot approve).
6. Docs: `regulatory_reporting.md` → v2 reflecting all of the above; LRT guide filed as
   primary source in `docs/research/` with the upgraded confidence entries.

## 4. Build order & effort

| order | workstream | prereqs | est. effort |
|---|---|---|---|
| 1 | W2 roles + W7.1–.3 hardening | — | 1 session |
| 2 | W1 decision fidelity | W2 | 1 session |
| 3 | W3 notifications + scans | W2 | 1–1.5 sessions |
| 4 | W4 corporate profile | — (parallel to 2–3) | 1.5–2 sessions |
| 5 | W5 LRT packs | W4 | 1.5 sessions |
| 6 | W6 families + calendar | — (parallel to 4–5) | 2–2.5 sessions |
| 7 | W7.4–.6 e2e + docs | all | 1 session |

Gates per landing: backend pytest (full), basedpyright, ruff, client regen + package
tests, dashboard typecheck + prod build, new Playwright suite, live-data invariants
untouched. Merge per workstream (squash), demo checkpoint after order 3 and order 5.

## 5. Explicit non-inventions

Unchanged honesty rules: no fabricated ORASS endpoints/credentials/field mechanics for
the prudential module (still UNKNOWN publicly); the sandbox stays labeled; the LRT packs
cite the guide (v1.0 draft) as their structural source; unknown deadlines stay labeled
until the bank supplies its onboarding-pack values.
