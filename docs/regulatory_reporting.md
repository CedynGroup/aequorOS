# AequorOS Regulatory Reporting & Submission Hub — Architecture

**Status:** v2.0 (as-built through plan W1–W7) · **Regulator:** Bank of Ghana first, regulator-extensible
**Companions:** `docs/submission_pipeline_plan.md` (the enterprise-grade completion plan), `docs/research/bog_orass_submission_channels.md`, `docs/research/bog_returns_and_templates.md`, `docs/research/bog_orass_lrt_portal_guide.md` (primary-source lifecycle notes; template fidelity labels come from these)

## 0. As-built delta since v1.0 (submission-pipeline hardening, 2026-07-24)

v1.0 below is the original build spec. The pipeline was then taken to enterprise
grade against the documented ORASS lifecycle (`bog_orass_lrt_portal_guide.md`).
What changed, by workstream (`docs/submission_pipeline_plan.md`):

- **W1 — decision fidelity.** Package lifecycle gains `declined` (regulator final
  refusal, terminal) distinct from correctable `rejected`; a value-based
  `snapshot_sha256` content seal; `submission_revision` (ORASS 1.0/1.1 stamped at
  submit); `regulator_comments` sealed from reject/decline responses (ORASS "View
  Comments"). New `regulatory_resubmission_requests` entity: reason → grant/deny →
  one authorized superseding regeneration (acknowledged packages can only be
  reworked under a granted request). ORASS-style form-set reference IDs. **New
  production channel `orass_api`** (real httpx transport/auth/TLS; provisional
  wire contract concentrated in one block; connectivity failures route to the
  BG/FMD/2026/07 email fallback) alongside the labeled `orass_sandbox` simulator.
- **W2 — control.** `decide-approval`, `submit`, `poll`, and resubmission
  decisions require the `approver` role (ORASS Principal-only-submit mirror); a
  real `listOrganizationUsers` directory replaces demo officers; channel config
  carries the ORASS principal/secondary user identities.
- **W3 — notifications.** `notifications` table + emission on approval-request /
  approval-decision / regulator-decision; a daily `reporting_deadline_scan` worker
  job (due-soon 7/3/1, overdue, re-upload-pending, idempotent per threshold); a
  live in-app drawer (unread badge, mark-read/all).
- **W4 — institution master data.** `institution_profiles` + related-party
  register (`related_parties`/`related_party_roles`/`shareholdings`/`outlets`/
  `bank_products`/`bank_licenses`/`bank_name_history`), every mutation
  reason-required + audited; exports/email now carry the real ORASS institution
  code.
- **W5 — LRT packs.** New event-driven `corporate` family: five packs (profile,
  outlet, party, capital, product) generated from W4 master data, CONFIRMED-per-
  guide fidelity, riding the same lifecycle; event-driven returns are excluded
  from the periodic calendar.
- **W6 — family completeness.** New `large_exposures` family (Templates
  1/1a/2/3/4 from canonical exposures vs a capital-run NOF proxy); LMT Tables
  filled (maturity ladder, funding concentration, unencumbered assets); IRRBB
  BoG ±450bp shock parameters — and (gap wave) the ENGINE now computes ±450 as
  informational scenarios excluded from the Basel outlier test.
- **Gap wave (same day):** DBK daily family (5-business-day calendar window,
  T+1 10:00 Africa/Accra due_time, Fri→Mon roll); T−1 comparative columns on
  BSD3/BSD2 (prior-period package values, never fabricated); SOLO|CONSOLIDATED
  ``basis`` on package identity (independent version chains; unique index
  includes basis); per-bank deadline overrides
  (``regulatory_reporting_settings``, get/putReportingSettings) — the honest
  correction path for the BSD2/FX placeholder deadlines; Institution Profile
  register UI (Governance → Institution Profile: profile, related parties +
  roles + shareholdings + UBO links, outlets, products/licences, name history —
  reason-required mutations, CSV bulk import via the reasoned endpoints);
  History server-side filters + pagination + per-revision audit-log view;
  ``.eml`` download buttons; SMTP notification mirror (outbox on
  ``notifications.emailed_at``, default OFF, ``SMTP_*`` settings).
- **W7 — hardening + verification.** `listPackageArtifacts` (artifact lists are
  persisted, not session-local); `snapshot_sha256`; schema-level
  `(org,package,kind)` artifact uniqueness; server-side family/date-range package
  filters; downloadable `.eml` downtime bundle; a Playwright e2e suite (hermetic
  backend + minted NextAuth cookies) covering authenticated navigation, live
  package generation, the deadline board, and role gates.

Migrations `202607240018`–`202607240023` are applied to the primary DB.
The status vocabulary, channel set, and family set in the sections below are
superseded by this delta where they differ.

## 1. Principles

1. **One home.** All official reporting lives under Governance → Regulatory Reporting. Module
   sub-navs (Liquidity → Submission, Basel → Submissions) are removed; modules link out.
2. **Packages are immutable.** Generating a return for a reporting date mints a versioned,
   immutable package snapshot carrying the input hashes of every source calculation run.
   Regeneration supersedes; it never mutates.
3. **Every number traces.** Package → source runs (input_hash, engine version) → facts →
   canonical rows → ingestion batch → raw file. Reuses the existing lineage substrate.
4. **Fidelity is labeled.** Each template in the registry carries a fidelity grade from the
   research: `CONFIRMED` (official appendix structure), `PARTIAL` (directive-described,
   appendix not public), `REPRESENTATIVE` (professional reconstruction, awaiting official).
   The UI shows the grade; nothing invented is passed off as official.
5. **Channels are honest.** ORASS integration ships as a clearly-labeled **sandbox simulator**
   (public API details are not published); the email/manual fallback produces real, complete
   artifacts + guided instructions. Real ORASS onboarding is a config swap behind the channel
   interface.
6. **Maker–checker.** No package reaches a submission channel without approval by a different
   user than its generator. All transitions audit-logged.

## 2. Package lifecycle

```
draft → generated → validated → pending_approval → approved → submitted
                                      ↓ rejected(approval)          ↓
                                   generated (rework)      acknowledged | rejected(BoG)
                                                                    ↓ rejected → resubmitted (new version, supersedes)
any regeneration for the same (family, reporting_date) ⇒ new version, prior → superseded
```

## 3. Data model (migration 202607170009, all RLS + tenant-scoped)

- `regulatory_packages`: organization_id, bank_id, return_family, return_code, reporting_date,
  frequency, status (CHECK per lifecycle), version, supersedes_id, snapshot JSON (the full
  generated return content — rows, totals, metadata), source_runs JSON
  ([{module, run_id, input_hash, engine_version}]), validation_report JSON, generated_by,
  generated_at, notes. Unique current-version per (org, bank, return_code, reporting_date)
  WHERE status != 'superseded'.
- `regulatory_package_artifacts`: package_id, kind (xlsx|csv|pdf), object_path (outputs tier:
  `bog_returns/{reporting_date}/{package_id}/{return_code}.{ext}`), checksum_sha256, size_bytes.
- `regulatory_package_approvals`: package_id, action (requested|approved|rejected), actor_user_id,
  reason, occurred_at. Checker ≠ maker enforced in service.
- `regulatory_submission_events`: package_id, channel (orass_sandbox|email|manual),
  event (submitted|status_poll|acknowledged|rejected), external_ref, detail JSON, occurred_at.
- `regulatory_channel_configs`: org, bank, channel, config JSON (institution_code, contacts,
  solo/consolidated), credential_ciphertext (EncryptedDbVault pattern — write-only at API).

## 4. Return-family registry (`app/services/regulatory_reporting/registry.py`)

| family | return_code(s) | source | frequency/deadline (per research) |
|---|---|---|---|
| liquidity | LCR/NSFR return (`LCR-NSFR`; registered as `BSD3` before the official templates were available — official BSD3 is Large Exposures, recoded 2026-08-15), Liquidity Monitoring Tools set | `get_bsd3_preview` + liquidity runs + maturity/funding analytics | monthly (LMT by day 9 per 2026 directive — confirm from research) |
| capital | CAR/RWA reconstruction (`CAR-RWA`; was mis-coded `BSD2` — the official Capital Adequacy Return is **BSD5A**, now generated on its official layout from this same engine) | `get_bsd2_preview` + capital runs | monthly/quarterly per research |
| irrbb | IRRBB pilot return (repricing gap, ΔEVE/ΔNII by shock) | IRR dashboard/run payloads | quarterly (pilot) |
| icaap_stress | ICAAP data companion + stress summary | forecast + stress runs | annual / per research |
| fx | Net Open Position return | FX dashboard/runs | per research |
| **bsd** (2026-08-15) | **Every official Bank of Ghana BSD prudential return** — BSD1, 1A, 1B, 2, 2A, 3A, 3B, 4, 5A, 5B, 6 (6A/6B), 7A, 7B, 8, 9, 10, 11, 13, 14, 15A, 15B, 16, 17 (24 workbooks / 76 sheets under `docs/reporting/`) | `bog_form` — `bog_forms/` computes each form by filling the official INPUT cells from named platform sources and **evaluating the templates' own formulas** (5,903 formula cells, 100% covered), then exports the **official workbook layout** values-only (sealed) | frequency + time limit from the Guide's List of Prudential Returns (weekly 9 days; monthly/quarterly/half-yearly 14 days); basis solo, consolidated only on BSD7B/BSD9 (+ GROUP variants BSD3B/BSD5B). Registry doc: `docs/bog_returns/00_full_return_registry.md`; per-form line maps `docs/bog_returns/<form>_line_map.md`; coverage matrix `docs/bog_returns/99_coverage_matrix.md` |

Registry entries: code, title, directive citation, frequency, deadline rule (callable:
reporting_date → due_date), generator, template id + fidelity grade, channel default.

### 4a. Export artifacts of a package (2026-08-16)

`POST /banks/{bank}/regulatory-packages/{id}/export?kind=` — `pdf` (values only; **the BoG
submission package**), `xlsx` / `xlsx_official` (sealed values-only Excel, sheets protected — the
governance twin of the PDF), `xlsx_working` (official BoG BSD forms only: the same official
layout with the template's live formulas for ALM/Finance review; labelled WORKING COPY; a
distinct artifact kind that is never filed and never signed), `csv`. Rendering:
`bog_forms/render.py` (`mode="official"|"working"`); kinds admitted by migration `202608160015`.

## 5. Services (`app/services/regulatory_reporting/`)

- `generation.py` — generators pull ONLY from existing services/run snapshots (no recomputation);
  snapshot embeds every value that will be exported; records source_runs with input hashes.
- `validation.py` — completeness (all template cells sourced), internal consistency (totals,
  cross-foots), prior-period movement checks (>X% swings flagged), status → validation_report.
- `exports/xlsx.py` (openpyxl: metadata block — institution code, reporting date, solo/consol,
  preparer/approver; GHS + FCY columns; number formats), `exports/csv.py`, `exports/pdf.py`
  (**reportlab** — new dependency: cover page, attestation/signature block, section tables,
  provenance appendix listing source run hashes). Artifacts → outputs tier with lineage metadata.
- `channels/base.py` (SubmissionChannel protocol: submit(package, artifacts) → external_ref;
  poll(external_ref) → status), `channels/orass_sandbox.py` (simulator: deterministic ack/reject
  fixtures, latency simulation, explicit SANDBOX labeling in every response),
  `channels/email_fallback.py` (builds the send-ready package: artifact bundle + guided
  instructions with the research-confirmed addresses; records the event; no actual SMTP in MVP).
- `workflow.py` — state machine + maker-checker + audit events (`record_event`).
- `calendar.py` — obligations for the next N months per registry + bank config; RAG staleness.

## 6. API (`app/features/manage_regulatory_reporting.py`)

listReportingObligations (calendar), listRegulatoryPackages, createRegulatoryPackage (generate),
getRegulatoryPackage, validateRegulatoryPackage, requestPackageApproval, decidePackageApproval,
exportRegulatoryPackage (kind → artifact download), submitRegulatoryPackage (channel),
listSubmissionEvents, listReturnTemplates (registry + fidelity), get/putChannelConfig
(credentials write-only). Conventions: manage_live_engine.py patterns, tenant 404s, audit events.

## 7. UI (Governance → Regulatory Reporting, route `/submissions` retained)

Tabs: **Calendar** (deadline board, RAG, next obligations) · **Returns** (family workspaces:
generate → preview vs prior period → validate → approvals → export xlsx/csv/pdf → submit) ·
**Approvals** (checker queue) · **History** (packages + submission events, filters, downloads) ·
**Templates** (registry with fidelity grades + preview) · **Channel settings** (ORASS sandbox
config, institution codes, contacts). Liquidity/Basel submission tabs removed → module pages
link "Official returns →". Sidebar Governance: Reports · Regulatory Reporting · Settings.

## 8. Extensibility

`regulator` field on registry entries (BOG now; CBN/CBK/SARB later); channels and templates are
per-regulator plugins; no BoG-specific logic outside the registry, templates, and channels.
