# Stress governance scoped-authorization rollout

This cutover completes the scenario, management-action and sign-off preparation
boundaries tracked by #178. Reverse-stress Forecasting authority and existing
engine-specific scenario workbench permissions are unchanged. Final stress
attestation remains a separate Signer cutover (#185).

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
  in place. The workbench additionally requires RISK/confidential VIEW before
  issuing its scenario, management-action or run queries.
- Sign-off preparation requires RISK/confidential CREATE or EDIT. Reads require
  VIEW. The final attestation ceremony is not completed by this change.

Lists filter authorized scopes before path/item counts and returned totals.
Unauthorized object IDs return 404. Evaluator failures deny closed. Refused
mutations cannot create scenarios, action items, sign-offs or successful audit
events. No production bindings are created or backfilled.

## Deployment gate

Before enabling this code, run `backend/scripts/authorization_access_impact.py`
against the target deployment and retain dated output outside this repository.
Inventory active human and machine principals and the exact replacement grants:
authors need Analyst RISK/confidential; checkers need Approver RISK/confidential;
readers need Viewer RISK/confidential. Explicitly choose organization-wide versus
one exact institution for each grant. Translation readers also need the matching
source-module view. Machine credentials gain no interactive authority.

Release acceptance requires real-binding allowed/denied browser journeys,
permission-disabled action explanations, no forbidden companion queries and
cross-bank checks. Test source alone is not evidence of a passing browser run.
