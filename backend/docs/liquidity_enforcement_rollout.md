# Liquidity scoped-binding enforcement rollout

Issue #165 cuts the remaining Liquidity product surfaces over from scalar roles
to exact authorization bindings. Run this inventory against each target
deployment immediately before release. Store the dated output with the release
record. Do not copy production identities into this repository.

No migration or application startup creates compatibility grants. A principal
without one complete active row for the consumed institution, surface's module,
sensitivity, and permission is denied immediately (403 unless the object-hiding
404 contract below applies). The original #165 binding cutover required no new
migration; the later bank-local register cutover requires the migrations below.

## Affected authority

| Surface                                                                                                                                                    | Required authority                                                                    |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Liquidity dashboard, live summaries/history, analytics windows, alerts, saved-analysis summaries, and regulatory-run list rows                             | LIQ / `aggregated` / `view`                                                           |
| Full regulatory-run and saved-analysis detail                                                                                                              | LIQ / `confidential` / `view`                                                         |
| BSD-3 preview                                                                                                                                              | Whole-institution REG / `restricted` / `view`                                         |
| Monitoring Tools, EWI dashboard, CFP reads/events, forecasts/history, cash-flow window, thresholds, haircuts, SDI liquidity position, and scenario details | LIQ / `confidential` / `view`                                                         |
| Create one or all Liquidity regulatory runs; execute scenario analysis                                                                                     | LIQ / `confidential` / `run`                                                          |
| Create a CFP draft, custom scenario, or saved analysis                                                                                                     | LIQ / `confidential` / `create`                                                       |
| Update a CFP draft or custom scenario; archive a scenario; delete a saved analysis                                                                         | LIQ / `confidential` / `edit`                                                         |
| Approve, activate, or de-escalate a CFP                                                                                                                    | LIQ / `confidential` / `approve`, with maker-checker still a global veto for approval |
| Liquidity threshold, haircut, and EWI register PUTs | LIQ / `restricted` / `configure`, evaluated for the exact target bank before any mutation |

Regulatory-run lists remove unauthorized LIQ rows before total counts, offsets,
and limits. Live summaries omit unauthorized Liquidity metrics; alerts omit
Liquidity findings before counts and limits. `GET /banks/{bank_id}/analytics/window`
omits Liquidity ratio series and daily statistics without aggregated view: the
service skips Liquidity series reads/computation and excludes Liquidity daily
snapshots in SQL, preserving authorized non-Liquidity results (see the
[IRRBB rollout contract](irrbb_enforcement_rollout.md) for its shared-surface filtering).
Liquidity live-history requests
require aggregated view. Full LIQ run and saved-analysis IDs return 404 without
confidential authority.
The BSD-3 preview uses the ordinary return read policy, including when consumed
by package generation and LMT. LIQ authority alone cannot read the preview.
Denied run and CFP lifecycle requests create no run, event, audit mutation, job,
or notification.

Tenant-initiated `POST /banks/{bank_id}/official-runs` ("Mint for filing")
and `POST /banks/{bank_id}/data-activations` with `run_calculations=true`
also require LIQ/confidential/run before enqueueing or deriving facts when
`module_scope.runs_module` includes Liquidity. These mixed-module operations
retain their existing mutation gate as well. SDI plans that exclude the
Liquidity engine do not acquire this additional run requirement.

Tenant-owned bank resolution precedes the binding decision: unknown or
cross-tenant institutions return 404. The evaluator emits
`authz.binding_decision` with target, outcome, matching binding IDs, and
per-binding reasons; evaluation failure records `binding_evaluation_failed` and
denies closed. Operator impersonation
and integration keys do not satisfy the interactive human-binding gate, with one
narrow exception: the examiner evidence bundle CITES the threshold, haircut and
EWI reads, so a verified act-as-examiner session bound to the bank in the path
reads those three and no other Liquidity surface
(`app/core/examiner.py`, `examiner_evidence_enforcement_rollout.md`).

Issue #184 replaces the held Approver gate on EWI, Liquidity threshold, and
Liquidity haircut PUTs with service-enforced scoped configuration authority.
Analyst, Approver, Account Admin, and Owner bindings do not convey `configure`;
no existing principal receives a compatibility grant. A Configurator can PUT
without `view`. GET still requires a separate LIQ/confidential view grant.
The existing effective-dated history, Board evidence, audit, refresh triggers,
and response shapes are preserved. Denial precedes register reads, generation
closure, parameter writes, success audit, and job enqueue. Threshold and haircut
storage and effect scope are now the exact bank named by the request, including
all history and generation closure. No shared-register fallback exists.
Scheduled internal live
computation does not impersonate a user and receives no binding; this cutover
checks tenant-facing entry points. Scheduled official-run authority is owned by
the [FX queued-run contract](fx_enforcement_rollout.md#queued-and-scheduled-official-runs).
Capital, Credit, and RISK-owned enterprise-stress backend
enforcement remain outside this cutover.

## Bank-local register deployment

The D19 shared-write security finding is resolved by D20's bank-local storage,
not by widening a Bank A grant to Bank B. `ParamLiquidityThreshold` maps to
`bank_liquidity_threshold`; `ParamLiquidityHaircut` maps to
`bank_liquidity_haircut`. Both keys include organization, bank, jurisdiction,
parameter key and effective date (thresholds also include institution class).
Composite bank/organization FKs and FORCE RLS retain tenant isolation.
`param_liquidity_threshold` and `param_liquidity_haircut` remain immutable
historical archives. Regulatory control-plane defaults and missing-policy
refusals retain their existing contracts; an absent bank row never reads a
sibling or archive row.

Deploy in a maintenance window, using the migration-role/ownership preparation
in [tamper-evident records](tamper_evident_records.md#required-deployment-follow-up):

1. Pause tenant/staff register writes and bank provisioning/classification
   changes; retain a recoverable database backup and dated impact inventory.
   Stop **all old tenant APIs, operator APIs, workers and scheduled consumers**
   before migrating. Do not run mixed old/new binaries or rely on rolling
   restarts: old code targets archives that the migration freezes.
2. Apply `202611040109`, then `202611040110` (and the deployment's complete
   migration head). The first locks source/destination tables and bank/type
   metadata while copying **every stored generation**, including closed and
   future-effective history, to each existing matching bank. Matching uses the
   source organization/jurisdiction and, for thresholds, the institution-type
   registry's institution class. Stored values, effective windows, Board evidence,
   notes and timestamps are preserved; destination row UUIDs are new. Unmatched
   source rows remain archived; no speculative bank policy is invented.
3. Before reopening traffic, compare each bank's copied current, historical and
   future-effective values against its pre-migration effective register. Verify
   source archives unchanged, bank FKs/RLS, archive write refusal, retained-row
   protections and API/worker table privileges. Verify the migration created
   **no bindings, identities, sessions or seeded financial facts**. New banks
   do not inherit the archives; their officer must supply their own Board policy.
4. Deploy the cutover build and restart **every** consumer listed below, including
   long-lived workers and the staff operator API. Smoke-test independent A/B
   reads/history, an A-only threshold and haircut write, sibling unchanged values
   and history, denied writes without audit-success/job effects, and staff audit
   attribution. Reopen traffic only after these checks pass. Approve any new
   grants separately through normal administration; require reauthentication
   after `authv` changes.

**Rollback is not a merge policy.** `202611040109` downgrade compares the entire
bank-local multiset with the expected archive expansion (excluding only fresh
row UUIDs) under locks. Any divergence, including added/closed generations,
changed metadata/timestamps or changed bank classification, refuses a lossy
downgrade. `202611040110` only restores the prior audit-reader definitions.
Do not deploy old binaries against a diverged database, disable the refusal,
or collapse banks' Board decisions into shared rows. After divergence use a
forward fix, or an explicitly approved full backup restoration with recovery
and data-loss review. An unchanged rollback still requires stopped consumers
and maintenance-window verification.

### Reader, engine and audit cutover inventory

| Consumer | Bank-local contract |
| --- | --- |
| `liquidity/service/liquidity_thresholds.py` | GET effective registers and full history; PUT closes only that bank's generations, records Board evidence and triggers only that bank's live refresh. |
| `policy/service/params.py` and `policy/public.py` | `get_active_bank_params` uses complete organization/bank/jurisdiction/date scope. Generic active/prefetch helpers require `bank_id` for these models, including empty date sets. |
| `liquidity/service/regulatory_liquidity.py` | Currency-mismatch limits resolve the run's bank; direct and batched/trend parameter loads retain bank scope. Live, inline and official paths share this seam. |
| `reporting/service/le_generation.py` | LMT Table 1 floors and Table 9 haircut schedules read the report bank, including fallback schedules within that bank; sibling Board decisions cannot affect generated sections. |
| `operator/services/inspector_reads.py`, `inspector_fix.py` | Staff inspection reads the local model; threshold fixes resolve an explicit bank (or refuse ambiguous multi-bank selection), mutate only it and retain staff audit. Organization-wide diagnostic lists do not imply shared write authority. |
| `core/audit_attribution.py`, `operator/services/operator_views.py`, migration `202611040110` | New `bank_liquidity_threshold` / `bank_liquidity_haircut` audit subjects use the exact bank ID and reject contradictory scope details. Tenant audit readers, staff attribution, chain linkage and inspection context follow that owner. Historical `param_liquidity_*` audit types keep their original shared-history interpretation; no old audit record is rewritten. |

Stored historical runs, input hashes and results remain immutable. Focused
coverage in `tests/liquidity/api/test_bank_local_parameters.py` proves sibling
register/history, refresh, currency-policy and actual LMT result isolation;
the database migration tests cover copy fidelity, RLS/ACLs, archive immutability,
audit-reader ownership and guarded rollback. Real HTTP/browser evidence is
additional deployment evidence, not a substitute for those regressions.

## Dashboard access

Navigation, deep links, action controls, and background requests consume the
server's effective-authority projection. Sensitive query keys and invalidations
use the tenant, actor, `authv`, and institution partition defined in
[`authorization_foundation.md`](authorization_foundation.md). Grant changes
invalidate sessions; users must sign in again before using the new authority.

An aggregated-only viewer can read summaries, but the cockpit labels EWI and
CFP sections restricted and omits threshold-dependent headroom assessments
when confidential inputs are unavailable. Monitoring Tools omits KPI status
styling when the corresponding authorized metric is unavailable. Saved-analysis
workbench links use the same access decision as navigation; permission-only
gaps follow the [disabled-control convention](../../docs/rbac.md). Module scenario
navigation follows
[Treasury scenario workspaces](../dashboard/README.md#treasury-scenario-workspaces).
The SDI Liquidity landing requires confidential view because it reads the SDI position.

## Deny-impact inventory

For configuration cutover evidence, change to `backend/` from the repository
root, then run
`python -m scripts.authorization_configure_impact --organization OR-XXXXXXXX
--output-dir /outside/repo/dated-configure-impact`, as described in
[the configuration inventory contract](configurator_impact.md).
Keep dated deny-impact and exact-grant artifacts outside the repository. The
inventory covers human and machine principals; machine keys cannot use these
interactive-human configuration endpoints even with a matching binding.

Run this read-only query with a role that can see all organizations, banks,
users, and bindings. It evaluates active human and machine principals without
inferring authority from `users.role`. This query inventories engine authority;
evaluate BSD preview access separately under the
[return authority contract](authorization_foundation.md#whole-institution-figures-and-credit-only-narrowing)
using `scripts/authorization_access_impact.py --json`.

```sql
WITH active_bindings AS (
    SELECT
        b.organization_id,
        b.principal_user_id,
        b.principal_type,
        b.role_bundle,
        b.institution_scope,
        b.institution_id,
        b.module_scope,
        b.sensitivity_scope
    FROM authorization_bindings AS b
    WHERE b.status = 'active'
      AND b.revoked_at IS NULL
      AND b.valid_from <= CURRENT_TIMESTAMP
      AND (b.valid_until IS NULL OR b.valid_until > CURRENT_TIMESTAMP)
),
principal_institutions AS (
    SELECT
        u.organization_id,
        u.id AS principal_user_id,
        CASE
            WHEN u.auth_provider = 'service' THEN 'machine'
            ELSE 'human'
        END AS principal_type,
        u.email,
        u.auth_provider,
        u.role AS scalar_role,
        bank.id AS institution_id,
        bank.name AS institution_name
    FROM users AS u
    JOIN banks AS bank
      ON bank.organization_id = u.organization_id
    WHERE u.is_active IS TRUE
),
authority AS (
    SELECT
        p.*,
        COALESCE(
            bool_or(
                b.role_bundle IN ('viewer', 'auditor', 'analyst', 'approver')
                AND b.sensitivity_scope IN ('aggregated', 'all')
            ) FILTER (WHERE b.principal_type = 'human'),
            FALSE
        ) AS may_view_aggregated,
        COALESCE(
            bool_or(
                b.role_bundle IN ('viewer', 'auditor', 'analyst', 'approver')
                AND b.sensitivity_scope IN ('confidential', 'all')
            ) FILTER (WHERE b.principal_type = 'human'),
            FALSE
        ) AS may_view_confidential,
        COALESCE(
            bool_or(
                b.role_bundle = 'analyst'
                AND b.sensitivity_scope IN ('confidential', 'all')
            ) FILTER (WHERE b.principal_type = 'human'),
            FALSE
        ) AS may_run_create_edit,
        COALESCE(
            bool_or(
                b.role_bundle = 'approver'
                AND b.sensitivity_scope IN ('confidential', 'all')
            ) FILTER (WHERE b.principal_type = 'human'),
            FALSE
        ) AS may_approve,
        COALESCE(
            bool_or(
                b.role_bundle = 'configurator'
                AND b.sensitivity_scope IN ('restricted', 'all')
            ) FILTER (WHERE b.principal_type = 'human'),
            FALSE
        ) AS may_configure
    FROM principal_institutions AS p
    LEFT JOIN active_bindings AS b
      ON b.organization_id = p.organization_id
     AND b.principal_user_id = p.principal_user_id
     AND b.module_scope IN ('liq', 'all')
     AND (
          (b.institution_scope = 'organization' AND b.institution_id IS NULL)
          OR
          (b.institution_scope = 'institution' AND b.institution_id = p.institution_id)
     )
    GROUP BY
        p.organization_id,
        p.principal_user_id,
        p.principal_type,
        p.email,
        p.auth_provider,
        p.scalar_role,
        p.institution_id,
        p.institution_name
)
SELECT
    organization_id,
    institution_id,
    institution_name,
    principal_user_id,
    principal_type,
    email,
    auth_provider,
    scalar_role,
    may_view_aggregated,
    may_view_confidential,
    may_run_create_edit,
    may_approve,
    may_configure,
    CASE
        WHEN principal_type = 'machine' THEN
            'denied: interactive human binding required'
        WHEN may_view_aggregated OR may_view_confidential
             OR may_run_create_edit OR may_approve OR may_configure THEN
            'partially or fully allowed: compare flags with actual duties'
        ELSE
            'denied: no active exact LIQ binding'
    END AS cutover_result
FROM authority
ORDER BY organization_id, institution_id, principal_type, email;
```

Review each row with the institution. Record every person whose actual duty
uses an affected surface but whose matching flag is false. Record machine
principals separately; do not create a human bundle for a service account.

## Exact binding rows

Create a row only after the institution confirms the person, institution, duty,
and sensitivity. Use the authorization service so `authv` advances and refresh
families are revoked in the same transaction.

| Confirmed need                                                                                     | `principal_type` | `role_bundle`                                                   | `institution_scope` | `institution_id` | `module_scope` | `sensitivity_scope` |
| -------------------------------------------------------------------------------------------------- | ---------------- | --------------------------------------------------------------- | ------------------- | ---------------- | -------------- | ------------------- |
| Read dashboard summaries and LIQ rows in the shared run registry                                   | `human`          | `viewer` or institution-approved `auditor`/`analyst`/`approver` | `institution`       | exact `BK-*`     | `liq`          | `aggregated`        |
| Read forecasts, CFP, thresholds, haircuts, SDI positions, scenario details, and full run snapshots | `human`          | `viewer` or institution-approved `auditor`/`analyst`/`approver` | `institution`       | exact `BK-*`     | `liq`          | `confidential`      |
| Create runs and analyses; create/edit CFP drafts and scenario artifacts                            | `human`          | `analyst`                                                       | `institution`       | exact `BK-*`     | `liq`          | `confidential`      |
| Approve, activate, and de-escalate CFPs                                                            | `human`          | `approver`                                                      | `institution`       | exact `BK-*`     | `liq`          | `confidential`      |
| Configure Liquidity threshold, haircut, and EWI registers                                         | `human`          | `configurator`                                                  | `institution`       | exact `BK-*`     | `liq`          | `restricted`        |

Configurator carries only `configure`, not `view`. Add the separate
LIQ/confidential view row above only when the officer's confirmed duties also
include reading the register; a configure-only officer can PUT without GET.

If an institution explicitly approves coverage across all its banks, replace
only `institution_scope` with `organization` and `institution_id` with `NULL`.
Do not widen module or sensitivity scope to compensate for a missing row.
Create separate aggregated and confidential rows when the person's duties
consume both. The explicit `all` value remains a broad grant, not a shortcut
the rollout may infer.

For a run maker, use Analyst / LIQ / confidential at the exact institution.
Use the [grant submission contract](authorization_foundation.md#structured-grant-reasons)
for the payload, reason fields, and preview/create sequence.

Do not backfill from scalar roles, create bindings for scheduled internal
workers, combine partial rows into a synthetic permission, or reuse a grant
approved for another module.

## Release record

Attach all of the following before deployment:

1. The dated inventory output for every organization and institution.
2. The exact human and machine principals that the cutover will deny.
3. The institution-approved duty for every new binding.
4. The exact binding rows created, including grantor, reason, validity, and scope.
5. Confirmation that users whose `authv` changed signed in again.
6. Reviewer-visible browser evidence for an unbound user and an exactly
   LIQ-bound user.
