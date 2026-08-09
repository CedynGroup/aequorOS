# AequorOS Developer Console — Spec (CP-1 + CP-2 ops slice)

Scope: the internal console for AequorOS developers/IT staff. Covers bank
onboarding (tenant provisioning), cross-tenant operations visibility
(activities, jobs, data engines), and the operator plumbing they require.
Explicitly OUT of scope for this phase: the market-data desk and curve
construction (CP-3, staff_UI.md), impersonation writes, offboarding/
crypto-shred (CP-4 — blocked on storage KES; must land before the first
paying tenant, tracked separately).

Architecture follows docs/internal/staff_UI.md: a SEPARATE app on its own
subdomain behind allowlist/VPN, workforce login, all privileged calls
through one operator BFF. Never a page inside bank.aequoros.com.

## 1. What exists vs what this builds

| Need | As-built today | This spec builds |
|---|---|---|
| Create org + bank | NOTHING outside `sample_bank_seed` (test-only, flag-gated) | The provisioning service + UI |
| Platform IDs | `app/services/public_ids.py` generates OR-/BK- codes via model defaults | Reused as-is |
| Jurisdiction/currency | `jurisdictions` global registry (GH/NG/KE/ZA seeded); `banks.currency`+`jurisdiction_code` REQUIRED, no defaults | Console form makes both mandatory picks |
| SSO connection | `sso_connections` per org, admin-managed in customer Settings | Console pre-creates the stub (issuer/domains empty until bank IT fills them) |
| First admin | Users are pre-provisioned rows (`auth_provider='oidc'` or password) | Console creates the first admin (password reset flow or OIDC pre-provision) |
| Storage buckets | `app/storage/provisioning.py::provision_institution` — the ONLY sanctioned path: four tier buckets per institution, versioning on retained tiers, temp lifecycle, idempotent | Saga calls it, plus the AWS-specific work in §2a |
| Cross-tenant reads | ONLY the worker, via BYPASSRLS role (`WORKER_DATABASE_URL`) | Operator BFF gets its own DB role on the same precedent |
| Audit | `audit_events` append-only (DB trigger) | Every operator action lands there with `operator_context` |
| Activity data | `jobs`, ingestion batches, `live_metrics`, `live_metric_snapshots`, freshness | Read-only cross-tenant views over them |
| Ops health checks | `tests/live_data/` invariant suite (read-only, primary DB) | Console surfaces the same invariants as a dashboard |

## 2. Backend: the provisioning service (the substance of CP-1)

New service `app/services/tenant_provisioning.py` + operator-only router.
NOT mounted on the tenant API app — see §4 topology.

`provision_tenant(payload) -> ProvisioningResultRead`, executed as a saga
with explicit step results (partial failure never leaves a half-tenant
silently):

1. **Create organization** (name; OR- id from the generator).
2. **Create bank** (name, license_type, REQUIRED `jurisdiction_code` from
   the jurisdictions registry, REQUIRED `currency` — the console form
   derives the currency default from the chosen jurisdiction but the
   operator must confirm; never auto-couple silently, that's the exact bug
   the no-defaults rule exists to prevent).
3. **Storage buckets (AWS)** — call `provision_institution` (never raw
   bucket creation; the module is the sanctioned path and safely retries),
   then the §2a AWS additions; probe with a write+GET+delete cycle
   (the current MinIO's Cloudflare WAF blocks HEAD — GET-probe works on
   both targets).
4. **SSO connection stub** — disabled row in `sso_connections`; the bank's
   IT completes it later via customer Settings → Authentication per
   docs/sso-onboarding.md. Console shows the two redirect URIs to hand the
   bank (sign-in + attestation step-up — registering only the first is the
   documented foot-gun).
5. **First admin user** — email + role admin; `auth_provider` per choice
   (password-with-reset or oidc pre-provision).
6. **Readiness probe** — run the same checks `/health/ready` and the
   live-data invariants use, scoped to the new tenant (empty-but-wired is
   the expected state: zero periods, zero facts, bucket reachable).

Result object records every step: `succeeded | failed | rolled_back` with
error detail. Rollback deletes created rows in reverse order; the bucket is
deleted only if empty (it always is at this point).

Validation: jurisdiction must exist in the registry; currency must be a
real ISO code; org/bank names non-empty; duplicate-name warning (not
block). NO seeding of financial data ever — the bank comes alive when its
first ingestion lands (standing order, 2026-07-21).

Tests: hermetic saga tests (full success; bucket-failure rollback; the
no-defaults rule enforced — payload without currency/jurisdiction is a 422
at the schema layer, not a silent 'GHS').

## 2a. Storage on AWS — per-institution buckets, keys, and secrets

The founder's requirement: onboarding from the portal creates the
institution's buckets ON AWS with the relevant secrets, and it just works.
The design that does this with the smallest secret surface:

- **Reuse `provision_institution` against AWS S3.** It is S3-protocol
  already. One code fix required first: it always sends
  `CreateBucketConfiguration.LocationConstraint` (a MinIO requirement),
  which real AWS REJECTS for us-east-1 — branch on endpoint/region.
- **One platform IAM principal, not per-tenant access keys.** The app
  already consolidates on a single S3_* credential set. Keep that: the
  provisioning saga (running in the control plane with a privileged AWS
  principal) creates the buckets and attaches bucket policies scoping the
  platform principal to them. No new static credentials are minted per
  tenant, so there are no per-tenant secrets to rotate, leak, or
  distribute. (Alternative, if contractual isolation demands it:
  per-tenant IAM access keys held in EncryptedDbVault — supported custody
  path, but adopt only when a bank's diligence requires it.)
- **Per-tenant KMS key — this is the secret that matters.** The saga
  creates a KMS key per institution, sets SSE-KMS with that key as the
  default encryption on all four tier buckets, and registers the key ARN
  against the org (a small `tenant_storage` registry row; the ARN is
  config, not a secret — access is IAM-governed). Consequences:
  - **CP-4 (offboarding/crypto-shred) is SOLVED for AWS-provisioned
    tenants**: offboarding schedules KMS key deletion and every object,
    including backups, becomes undecryptable — the exact recipe
    staff_UI.md demands and the KES-less MinIO cannot deliver.
  - Per-tenant encryption isolation without per-tenant credentials.
- **AWS principals live only in the control plane.** The operator/
  provisioning service holds the AWS credentials capable of CreateBucket/
  CreateKey/PutBucketPolicy; the tenant-facing app keeps only the runtime
  S3_* read/write principal. Tenant-plane code can never provision.
- **Migration note:** existing MinIO institutions (the sample bank) stay
  where they are; new tenants provision to AWS from day one, which aligns
  with the MinIO retirement date (2027-01-14) — the migration is then
  "move two dev-era tenants", not a fleet.

## 3. Backend: operator read APIs (CP-2 ops slice)

All read-only in this phase, all cross-tenant, all through the operator
BFF's own DB role:

- `GET /operator/tenants` — orgs + banks with: platform IDs, jurisdiction,
  currency, created, period-spine summary (first/last period, count),
  live-engine freshness per module, SSO state (stub/enabled), last
  ingestion batch outcome.
- `GET /operator/tenants/{org}/activity` — unified feed: ingestion batches
  (with the 4-state outcomes), jobs (queued/running/succeeded/failed —
  including which worker generation claimed them, once the job row carries
  it; today claimant identity is a gap worth one added column, see §6),
  official runs minted, packages generated/submitted, audit_events tail.
- `GET /operator/data-engines` — cross-tenant connection health: every
  Database-Direct/T24/market-data connection's lifecycle status, last sync,
  credential expiry (metadata only — the vault never returns material),
  using the same read paths the tenant Data Engine health panel uses.
- `GET /operator/invariants` — the live-data invariant suite's checks
  (provenance, spine contiguity, fact coverage, live-metrics presence) as
  API results per tenant.

## 4. Topology, identity, audit

- **Separate FastAPI app** (`backend/operator/` or a second app instance)
  mounting ONLY operator routers; deployed as its own Coolify app on
  `console.aequoros.com` (or internal DNS), source-IP allowlisted via the
  existing CEDYN-RESTRICT pattern. Coolify compose rules apply (no `${}`
  interpolation; config inside the compose; no repo bind-mounts).
- **DB role**: `aequoros_operator` — like the worker's role, BYPASSRLS for
  the read views; the provisioning saga runs with write grants on exactly
  the tables it creates. Never the tenant app role.
- **Workforce login**: reuse `verify_oidc_id_token` against a Google
  Workspace (or Okta) issuer with an @aequoros.com domain allow-list and a
  small `operator_users` table (pre-provisioned, role: `developer` |
  `operator_admin`). MFA enforced at the IdP. No password path.
- **Audit**: every BFF request writes an `audit_events` row with
  `operator_context` (operator email, request id); provisioning steps each
  write their own. The append-only trigger already guarantees immutability.
- **The customer app is untouched**: no operator routes, no operator UI,
  no shared session anything.

## 5. Console UI (thin, deliberately)

Next.js app (same design system — the DS tokens/components are a package
copy away) or Appsmith over the BFF if speed wins; either way ONLY over
BFF endpoints:

1. **Tenants** — list with health chips; detail page = activity feed +
   data-engine connections + invariant results.
2. **Onboard bank** — the provisioning form (org, bank, jurisdiction picker
   from the registry, currency confirm, license type, first-admin email),
   a review step showing exactly what will be created, then the saga's
   live step-by-step result (each step's status rendered as it lands),
   ending with the handoff pack: platform IDs, the two OIDC redirect URIs,
   the first-admin credential flow, and "bank goes live on first
   ingestion".
3. **Operations** — cross-tenant jobs/batches board (the shared-jobs-table
   race becomes VISIBLE here: which runtime claimed each job), data-engine
   connection wall, invariant dashboard.

## 6. Known gaps this spec does NOT close (tracked, ordered)

- **Job claimant identity**: jobs don't record which worker/runtime claimed
  them — one column + worker stamp makes the prod/local race observable in
  the console. Small backend change, high ops value; do it with CP-2.
- **Impersonation** (CP-2b): read-only first, examiner-role shaped, dual
  identity `act` token, banner, separate audit stream — per staff_UI.md §5.
  Not in this phase.
- **Market-data desk + curve engines** (CP-3): staff_UI.md gap register.
- **CP-4 (crypto-shred)**: SOLVED for AWS-provisioned tenants by §2a's
  per-tenant KMS design; remains open only for anything left on the
  KES-less MinIO at offboarding time.
- **Billing**: nothing exists; out of scope.

## 7. Sequencing

0. `provision_institution` AWS-compat fix (LocationConstraint branch) +
   the per-tenant KMS/SSE-KMS/bucket-policy additions + `tenant_storage`
   registry — testable against LocalStack/moto hermetically.
1. Provisioning service + saga tests (backend only — usable via one CLI
   call before any UI exists; this alone "covers the gap").
2. Operator BFF skeleton + workforce login + audit context.
3. Read APIs + Tenants/Operations screens.
4. Onboard-bank screen over the saga.
5. Job-claimant column + race visibility.
