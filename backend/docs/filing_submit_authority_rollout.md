# Filing submit authority enforcement rollout

Step 1 of [the filing workflow redesign](filing_workflow_redesign.md) (§1 finding 3,
§3.3, §6 step 1) separates **approving** a regulatory return from **transmitting** it
to the regulator. Run this inventory against each target deployment immediately
before release and store the dated output with the deployment record. Do not copy
production identities into this repository.

## What was wrong

`require_package_submit` required `Permission.APPROVE` — the *same permission* as
`require_package_approve`. Two consequences, both live:

- an officer holding approval authority for a return could also file it to the Bank
  of Ghana alone; and
- on an **ungated** family (every BSD, liquidity, capital and FX return — i.e. every
  return that actually reaches ORASS) the dependency fell back to the scalar ladder,
  so the scalar `approver` (or `admin`) role was sufficient on its own, with no
  binding at all.

The bank's process has a separate **Validator** who is the only officer that
transmits. One permission cannot express two authorities.

## What the cutover changes

`Permission.SUBMIT` already existed in the vocabulary as a reserved action name
carried by no bundle. It is now carried by exactly one bundle and enforced on one
surface.

- **`RoleBundle.VALIDATOR`** (`validator`) grants `view` and `submit`, and nothing
  else. It deliberately does **not** carry `approve` — a bundle that both approves
  and files would reinstate the defect under a new name — and it deliberately does
  not carry `validate`, which is the machine rules check the Preparer re-runs.
- **`require_package_submit`** takes one path for **every** return family:
  `get_scoped_mutation_tenant_context` (an interactive human, no impersonation,
  current `authv`, and no scalar role check whatsoever), then one complete active
  binding carrying `submit` over the transmission gate below. The family dispatch
  that gives ICAAP its own read authority does not apply to filing: transmission is
  the same act whatever the return is, and two transmission authorities would be a
  seam (D-069).
- **Transmission gate:** module `reg` (Regulatory Reporting), sensitivity
  `restricted`, institution scope exact or explicitly organization-wide. Covering one
  bank never covers its sibling.
- **Visibility is decided first and is unchanged.** A gated (ICAAP) family the caller
  cannot see still answers `404` — the refusal must not disclose that the package
  exists. Once it is visible, a missing filing authority is an honest `403` that
  names what is missing.
- **Maker/checker is required context.** `Permission.SUBMIT` is registered in
  `services/authorization._REQUIRED_RUNTIME_CONDITIONS`, so an evaluation that does
  not receive a maker/checker verdict denies. The officer who generated the return
  cannot file it, on a BSD return as much as on an ICAAP. A future route that grants
  `submit` without establishing who prepared the return is refused, not allowed.

### Affected routes

Both routes that declare `PackageSubmit`:

- `POST /api/v1/banks/{bank_id}/regulatory-packages/{package_id}/submit`
- `POST /api/v1/banks/{bank_id}/regulatory-packages/{package_id}/poll`

Polling moves with submission on purpose: the channel conversation belongs to the
officer who opened it, and the poll route records the regulator's decision.

`decide-approval` and `decide-resubmission` keep `require_package_approve` and
`Permission.APPROVE`. Nothing else about the approval side changes.

### What did NOT change

- The lifecycle. `approved -> submitted` and every channel precondition are
  untouched; a principal who passes the new gate still meets the same `409`s.
- **Rehearsal packages remain unfilable (D-068).** The rehearsal refusal lives in
  `workflow._ensure_channel_submittable`, before the transition check, and is
  independent of authorization. Pinned by
  `tests/api/test_package_authorization.py::test_a_rehearsal_is_unfilable_even_with_transmission_authority`,
  which holds *both* the Capital read grant and the filing grant and is still refused.
- **Existing sealed and submitted packages.** No migration touches
  `regulatory_packages`, `regulatory_package_approvals`, signatures or artifacts.
- The examiner branch. Impersonation is read-only and never files.
- Cross-tenant behaviour: a package outside the caller's tenant is `404`.

### Reconciling with docs/rbac.md §7.2

`rbac.md` describes `reg:submit` as a **preset add-on** on top of the Approver
bundle (CFO, MD, Head of Reg), SoD-gated under §7.4, never blanket to every
Approver. That intent is preserved; the mechanism could not be. v1 has no mutable
permission catalogue and no per-user add-ons — bundles are code — so putting
`submit` on Approver gives it to every Approver, which is the hole. Its own bundle
is the only way to express "some Approvers file, most do not" in the model that
exists, and it satisfies rbac.md's own C4 and C6 (`run != approve != submit`,
producer != approver of numbers) more strictly than an add-on would have. The
Approver row of the §7.2 tables has been corrected and carries a dated note so the
permission is not folded back in. `sign_off` remains reserved and unbundled.

## Grants: who could submit, before and after

**Before:** any active human whose scalar role satisfied `approver` — that is, scalar
`admin` or `approver` — could transmit any ungated return, with no binding. For the
gated `icaap` family, any holder of an `approve`-carrying binding covering
Capital/confidential for the institution could transmit.

**After:** only the holder of an active `submit` binding over Regulatory Reporting /
restricted for that exact institution. **Nothing is backfilled.**

### No migration, by design

No binding migration ships with this cutover, and that is the deliberate choice, not
an omission:

- Granting transmission authority to everyone who held approval authority would
  encode the very defect being removed. The whole point of Bernard's review is that
  the Validator is a *different person* from the Approver.
- The precedent is `202608250044` (the foundation migration backfills no binding) and
  `202608280046` (assign nothing where the intent is ambiguous, and leave the state
  queryable). Here the pre-cutover state remains queryable *without* a control table,
  because this cutover changes no `users` row: the scalar role that used to authorize
  filing is still on the user and is reported in the `role` column of the gate below.
- An organization therefore has **no filing authority at all** until its Org Owner
  writes one. That is a deliberate, stated stop — an organization that cannot say who
  its Validator is must not keep filing on the old, conflated authority.

### Granting it

One indivisible binding per Validator, through Access → Members (the Org Owner
gate) or `POST /api/v1/authorization/bindings`:

```
role_bundle        validator
institution_scope  institution   (one exact BK-*)     or organization
module_scope       reg
sensitivity_scope  restricted
```

The grant service advances the grantee's `authorization_version` and revokes their
refresh families in the same transaction, so the Validator signs in again and their
projected authority is current on the next request.

**Separation of duties is enforced at assignment.** An identity holding an effective
`approver` binding is refused a `validator` grant and vice versa
(`approval_and_transmission_separation_required`, a **block**, not a warning). The
check is scope-independent, unlike the Analyst/Approver warning, because one
Validator grant files every return family. It is a block because the per-object
condition that would catch approve-then-file at action time belongs to the stage
engine and does not exist yet (redesign §3.3 layer 3). Relax it to a warning only
when that condition is live — never to make an assignment pass.

C9 still applies: an Org Owner or Account Administrator cannot hold `validator`
either, because it is an operational maker/checker bundle.

## Inventory

Run the evaluator-backed gate against the target deployment. It uses the same
projection the API serves, and its `filing` column is the post-cutover answer while
the `role` column beside it is the pre-cutover one:

```
cd backend && uv run python scripts/authorization_access_impact.py --organization OR-XXXXXXXX
```

The read-only SQL below is the deployment inventory. Run it as a role that can see
all tenant users and bindings.

```sql
-- BEFORE: every identity the old scalar gate would have let file.
SELECT organization_id, id, email, role
FROM users
WHERE is_active
  AND auth_provider <> 'service'
  AND role IN ('admin', 'approver')
ORDER BY organization_id, email;

-- BEFORE (gated ICAAP family only): approval bindings that also reached a channel.
SELECT organization_id, principal_user_id, institution_scope, institution_id,
       module_scope, sensitivity_scope
FROM authorization_bindings
WHERE status = 'active'
  AND revoked_at IS NULL
  AND role_bundle = 'approver'
  AND module_scope IN ('cap', 'all')
  AND sensitivity_scope IN ('confidential', 'all')
ORDER BY organization_id;

-- AFTER: every identity that can file, which is empty until grants are written.
SELECT organization_id, principal_user_id, institution_scope, institution_id
FROM authorization_bindings
WHERE status = 'active'
  AND revoked_at IS NULL
  AND role_bundle = 'validator'
  AND module_scope IN ('reg', 'all')
  AND sensitivity_scope IN ('restricted', 'all')
ORDER BY organization_id;

-- In-flight exposure: packages that are past approval and not yet filed are the
-- ones whose filing stops until a Validator exists.
SELECT organization_id, status, count(*)
FROM regulatory_packages
GROUP BY 1, 2
ORDER BY 1, 2;
```

## Measured impact (primary, 2026-09-20)

Read-only run of the gate and the inventory above against the primary database,
before and after the code change. The two runs are identical except for the added
`filing` column: this cutover changes no `view` authority, so no one loses a module,
a bank or a page.

```
organization  email                     role           grants  account     product view      filing  flags
------------  ------------------------  -------------  ------  ----------  ----------------  ------  ----------------------------------
OR-ADGE1DPV   admin@…                   account_admin  3       administer  BK-7CF5N6KS: all  -       -
OR-Q4RGR5EW   eric@…                    account_admin  3       administer  BK-XREAZES1: all  -       -
OR-QVXE0FQV   accessbank.evaluation@…   admin          5       administer  BK-0PMD7Z5M: all  -       -
OR-QVXE0FQV   admin@…                   account_admin  1       -           -                 -       no_product_view
OR-QVXE0FQV   dela@…                    account_admin  2       administer  -                 -       no_product_view,account_plane_only
OR-QVXE0FQV   eric@…                    account_admin  3       administer  BK-0PMD7Z5M: all  -       -
OR-QVXE0FQV   icaap.preparer@…          analyst        1       -           BK-0PMD7Z5M: all  -       -
OR-QVXE0FQV   lawrenceaddo@…            account_admin  1       -           -                 -       no_product_view

8 active human user(s); 3 with no product view or no bindings.
0 can transmit a return to the regulator.
```

- **Before:** exactly **one** active human could transmit an ungated return — the
  single scalar `admin` in `OR-QVXE0FQV`. `account_admin` was already outside the
  ladder and could not. For the gated ICAAP family, one organization-wide `approver`
  binding in `OR-QVXE0FQV` also reached a channel.
- **After:** **zero**, in all three organizations, until an Owner grants Validator
  authority.
- **Nothing is stranded.** Every package on the primary is `generated` or
  `superseded`; there is no `approved`, `submitted` or `acknowledged` row anywhere,
  so no return is mid-filing at the moment of the cutover.

## Deployment order

1. Run the gate and the inventory; keep the dated output.
2. Deploy the backend.
3. The Org Owner of each tenant grants the Validator sentence to the officer who
   actually files, and to nobody who approves.
4. Re-run the gate. A tenant that files returns must show a non-empty `filing`
   column, or filing is stopped for that tenant.

### Dashboard

No route path, method, request body or response shape changes, so **nothing 422s
and no dashboard call site is broken by this contract**. Two follow-ups, neither
blocking the backend deploy:

1. **Client regeneration, then one label.** `GrantableRoleBundle` gains
   `validator`. Regenerate `packages/risk-service-api` centrally with the other
   contract-changing work, then add `["validator", "Validator"]` to `ROLE_OPTIONS`
   in `backend/dashboard/lib/api/grants.ts`. Until that lands, the Members composer
   simply does not offer the role (every value it does send remains valid), and an
   existing Validator grant renders with the raw `validator` string instead of a
   label.
2. **The Submit control is offered to people the server now refuses.**
   `app/(app)/submissions/returns/page.tsx` computes `canSubmit = status ===
   'approved'` — pure lifecycle, no authority — so a Preparer or Approver looking
   at an approved return still sees an enabled Submit button and receives a clean
   403 naming Validator on click. That is the visible half of the same problem the
   redesign describes (§4b.1: absent when the surface is not this role's, disabled
   **with the reason** when it is) and it is fixed by driving the control from the
   projected capability in redesign steps 2 and 5 — not by a call-site edit here.

## Executable verification

- `tests/core/test_authorization.py::test_approving_a_return_is_not_authority_to_file_it`
  and `::test_no_bundle_other_than_validator_carries_submit` — the bundle split,
  stated twice and independently.
- `tests/core/test_authorization_properties.py` — the generative oracle carries the
  new bundle, so the evaluator is compared against an independent restatement of it.
- `tests/api/test_package_authorization.py`, section *transmission to the regulator is
  its own authority* — a complete Approver binding over the filing scope is refused
  with a named reason; a scalar `approver`/`admin` with no binding is refused; a
  Validator binding passes authorization; the preparer cannot file their own return;
  a gated family still hides before it refuses; a rehearsal is refused past both
  grants; an impersonated examiner is refused; cross-tenant is `404`; `poll` carries
  the same authority.
- `tests/api/test_grant_administration.py::test_approving_and_filing_cannot_land_on_one_identity`
  and `::test_a_validator_grant_is_accepted_for_an_identity_that_does_not_approve`.
- `tests/scripts/test_authorization_access_impact.py::test_the_report_names_who_may_transmit_a_return`.
- `tests/api/test_regulatory_reporting.py` (opt-in, `REAL_DATA_DATABASE_URL`) — the
  channel journeys now run three officers. `test_control_actions_require_approver_role`
  was **renamed and rewritten**, not deleted: its old sentence ("channel submissions
  and polls need the `approver` role") was the defect written down as an assertion.
