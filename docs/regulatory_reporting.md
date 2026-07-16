# AequorOS Regulatory Reporting & Submission Hub — Architecture

**Status:** v1.0 (build spec) · **Regulator:** Bank of Ghana first, regulator-extensible
**Companions:** `docs/research/bog_orass_submission_channels.md`, `docs/research/bog_returns_and_templates.md` (research inputs; template fidelity labels come from there)

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
| liquidity | LCR return, NSFR return, Liquidity Monitoring Tools set | `get_bsd3_preview` + liquidity runs + maturity/funding analytics | monthly (LMT by day 9 per 2026 directive — confirm from research) |
| capital | CAR/RWA return (BSD-2 style), leverage, buffers | `get_bsd2_preview` + capital runs | monthly/quarterly per research |
| irrbb | IRRBB pilot return (repricing gap, ΔEVE/ΔNII by shock) | IRR dashboard/run payloads | quarterly (pilot) |
| icaap_stress | ICAAP data companion + stress summary | forecast + stress runs | annual / per research |
| fx | Net Open Position return | FX dashboard/runs | per research |

Registry entries: code, title, directive citation, frequency, deadline rule (callable:
reporting_date → due_date), generator, template id + fidelity grade, channel default.

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
