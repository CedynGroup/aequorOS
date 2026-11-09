# Design note: the CREDIT Board registers move to bank-local storage

Decision D55 (codex P1 on PR #849): the four CREDIT register tables
(`param_ecl_assumption`, `param_crm_haircut`, `param_concentration_limit`,
`param_credit_threshold`) are keyed by organization + jurisdiction with no
`bank_id`, while `credit_params._require_configure` authorizes one exact
institution. A Configurator for bank A can therefore change values sibling
bank B reads — the same CWE-863 shape D19 found for the LIQ registers.

## The D20 precedent this reuses

D20 resolved the identical LIQ finding (PR #781, VAL-CFG-018) with bank-local
storage. Its mechanism, reused here unchanged:

1. **New bank-local tables**, not in-place edits. Each shared register gets a
   successor table with `bank_id` and a composite foreign key
   (`bank_id`, `organization_id`) → `banks(id, organization_id)`, so a row can
   only ever belong to a bank of its own tenant. The unique scope gains
   `bank_id`.
2. **A copy migration in its own commit.** Every shared generation is copied
   once per existing bank of the same organization and jurisdiction, with fresh
   UUIDs, preserving values and approval evidence. Source rows are retained
   unchanged as frozen read-only archives (statement-level append-only guards,
   INSERT/UPDATE revoked even from owners), so no approval trail is destroyed
   and no seeded data is created. The copy runs under an ACCESS EXCLUSIVE lock
   on the sources and banks with FORCE RLS suspended for the migration only.
   The new tables carry tenant FORCE RLS, sealed/retention/no-truncate guards
   and the standard runtime grants. Downgrade verifies bank-local values
   against the archives and refuses a lossy rollback after divergence.
3. **Runtime cutover with no shared-row fallback.** Readers and engines resolve
   only the configured bank's rows (`get_active_bank_params` requires
   `bank_id`; a bank-local model without one raises). Writers supersede and
   create rows scoped to the bank in the path, record audit events under the
   bank-local entity types (direct ownership, no shared-register fan-out), and
   enqueue a bank-scoped live refresh instead of an organization-wide one.
4. **Audit attribution follows.** A second migration rebuilds the bank audit
   reader policies: the new bank-local entity types attribute directly to the
   bank in the path; the historical shared types keep their shared redaction.

## What changes for CREDIT

- New tables `bank_ecl_assumption`, `bank_crm_haircut`,
  `bank_concentration_limit`, `bank_credit_threshold`; the `param_*` tables
  become frozen archives (ORM `Legacy*` classes, SELECT-only).
- Cutover of every writer (`credit_params` mutators) and every reader/engine
  path: capital ECL/CRM, concentration, credit thresholds, stress, ICAAP
  granularity, forecasting, the examiner-cited register GETs (they read the
  same service), live refresh triggers and the audit-log shared-register
  ownership mapping.
- Tests: a migration regression proving per-bank copy, isolation, archive
  freeze and guarded rollback, plus a bank A/sibling B isolation suite proving
  a bank A configure never changes bank B's registers, history or engine
  outputs (mirroring `tests/liquidity/api/test_bank_local_parameters_integration.py`).
- The route-authorization catalogue marks the four PUTs `bank_local` with their
  storage tables; the deny-impact and exact-grant artifacts are refreshed
  outside the repo. No API schema changes, so the generated client is
  untouched.

Authority is unchanged: the PUTs keep requiring one complete active
CREDIT/`restricted` `configure` binding for the exact institution, and
configure-only callers keep receiving write-only acknowledgements (D27/D34).
Bank-local storage closes the gap between what a grant authorizes and what a
write can affect; it widens nothing.
