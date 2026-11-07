# Stress governance scoped-authorization rollout

This cutover completes the scenario, management-action and sign-off preparation
boundaries tracked by #178. Final stress attestation is the Signer cutover
tracked by #185. Reverse-stress Forecasting authority and existing
engine-specific scenario workbench permissions are unchanged.

The [authorization foundation](authorization_foundation.md#standing-rules-at-a-glance)
owns complete binding matching, invalidation, source-module access and the
executable verification contract.

## Required authority

- Macro scenarios and management-action plans require RISK/confidential VIEW,
  CREATE, EDIT or APPROVE for the requested action. Submission and archiving
  require EDIT. Existing maker-checker and immutable-state conditions still apply.
- A bank-specific row requires authority on its exact bank. A row with no bank
  is an explicit organization resource, including system scenario definitions.
  An exact-bank binding never grants organization-wide access.
- Translation additionally requires confidential VIEW on the translated module,
  against the same resource scope. A Risk grant alone cannot expose a translation.
- Enterprise-stress execution requires RISK/confidential RUN, retains each
  engine's RUN check and its Risk response-view boundary, and no longer
  depends on the scalar Analyst role.
- The existing IRRBB/confidential entry gate on `/irr/enterprise-stress` remains
  in place. The workbench additionally requires RISK/confidential VIEW on the
  active bank before mounting its query-owning content. A grant on another bank
  does not permit scenario, management-action or run queries for this workbench.
- Organization scenario controls consume organization capabilities from the
  shared effective-authority projection; exact-bank capabilities never substitute
  for them. Approval eligibility remains contextual: the server checks the maker
  against the checker. Risk capabilities do not confer Account administration.
- Sign-off preparation requires RISK/confidential CREATE for a new sign-off and
  EDIT to update, submit or withdraw it. Reads require VIEW. Submission names an
  eligible exact-bank signer other than the preparer and freezes the recipient
  and value-based signing digest.
- Final attestation requires one active institution-scoped exact-bank
  RISK/restricted SIGN_OFF binding, independent confidential source READ, the
  frozen named recipient, unchanged signing digest, maker-checker separation and
  genuine fresh password/OIDC step-up. Approver or organization-wide Signer
  authority alone is insufficient. The shared one-shot token is bound to signer,
  sign-off, `board` role and digest, and replay is refused.

Disabled controls explain the missing permission on hover and keyboard focus.
These boundaries require whole-institution data coverage. Lists filter authorized
scopes before path/item counts and returned totals. For callers without VIEW,
existing and missing object IDs return an identical 404 response. Evaluator
failures deny closed. Refused mutations cannot create scenarios, action items,
sign-offs or successful audit events. No production bindings are created or backfilled.

## Deployment gate

Before enabling this code, run `backend/scripts/authorization_access_impact.py`
against the target deployment and retain dated output outside this repository.
Inventory active human and machine principals and the exact replacement grants:
authors need Analyst RISK/confidential; checkers need Approver RISK/confidential;
readers need Viewer RISK/confidential. Each final stress signer additionally needs
one independent Viewer RISK/confidential binding and one institution-scoped
Signer RISK/restricted binding naming that exact bank. Do not substitute an
organization-wide Signer grant. Translation readers also need the matching
source-module view. Machine credentials gain no interactive authority.

Migration `202611070113` adds nullable recipient, digest and consumed-authorization
fields without backfilling identity or authority. Existing pending rows retain
NULL ceremony fields and cannot be attested; a new governed run and sign-off are
required because the one-sign-off-per-run record remains immutable.

Release acceptance requires real-binding allowed/denied browser journeys,
permission-disabled action explanations, no forbidden companion queries and
cross-bank checks. Test source alone is not evidence of a passing browser run.
