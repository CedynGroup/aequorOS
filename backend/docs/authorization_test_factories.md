# Authorization test factories

These opt-in factories build synthetic identities and complete scopes with the
existing production models, grant service and credential mechanisms. They do
not define permissions or expected decisions. The policy and rollout contracts
remain in [authorization_foundation.md](authorization_foundation.md).

## Start without ambient authority

Import `tests.support.factories.role_scope` explicitly. No plugin, autouse
fixture, global client header, seeded financial book or module activation is
added. Existing shared fixtures are unchanged.

**The root `db_client` / `db_session` setup still gives its two legacy demo users
organization-wide Viewer/all/all bindings.** Use `human` with an explicit
organization and scalar role for a new actor with no bindings. Do not use a
legacy demo user to prove a no-authority denial unless the test deliberately
removes its bootstrap grants. Factories never remove another actor's bindings.

```python
from app.core.authorization import (
    DataScope, GrantorType, ModuleScope, PrincipalLocator, PrincipalType,
    RoleBundle, SensitivityScope,
)
from app.identity.service.authorization import GrantorRef
from tests.support.factories import role_scope as actors

user = actors.human(db_session, organization_id=bank.organization_id, legacy_role="viewer")
grants = actors.RoleBindingFactory(
    db_session, GrantorRef(GrantorType.SYSTEM, "my-feature-test")
)
grants.create(
    principal=PrincipalLocator(bank.organization_id, user.id, PrincipalType.HUMAN),
    bundle=RoleBundle.VIEWER,
    scope=actors.institution_scope(
        bank_id=bank.id,
        module=ModuleScope.LIQUIDITY,
        sensitivity=SensitivityScope.CONFIDENTIAL,
        data_scope=DataScope.ALL,
    ),
    reason="Read only this synthetic bank's confidential Liquidity data",
)
db_session.commit()
session = actors.tenant_session(db_session, user=user)
response = db_client.get(
    f"/api/v1/banks/{bank.id}", headers=actors.bearer(session.access_token)
)
assert response.status_code == 200
```

Every bundle, module, sensitivity, institution and grantor is explicit.
`organization_scope` deliberately selects organization-wide, whole-institution
coverage; it is never inferred from a missing bank. `institution_scope` requires
the data-scope kind; branch/region values pass unchanged to production validation.
Credit-only narrowing remains Credit-only. Explicit `ALL` values are available
but never chosen for the caller.

## Transactions and credentials

- `organization`, `bank`, `human`, `machine`, `staff` and
  `RoleBindingFactory.create` flush without committing. Callers own the
  transaction. `bank` uses the supplied jurisdiction registry row's currency
  and adds no modules, parameters or financial data. Both principal factories
  require an explicit legacy scalar role, which does not create a binding.
- Grant creation uses `authorization.create_role_binding(commit=False)`.
  Principal/type, tenant, institution and scope checks and authorization-version
  advancement remain real. It never restores `authv=1`, revives refresh tokens,
  converts Approvers to Validators, adds Owner read grants or infers EXPORT.
- `tenant_session` calls `authentication.issue_tokens`, which **commits** and
  records a real refresh family at the current authorization version. It is a
  token-issuance helper, not a password/SSO-login or activation fixture. Capture
  an old session explicitly to test stale-token refusal, then issue a separate
  session after changing grants. The helper does not renew old credentials.
- `machine` creates a real `auth_provider="service"` row, not a human preset.
  `tenant_session` refuses it. Machine credential tests must use the existing
  integration-key issuance service/API and its required administration authority;
  no key or machine grant is created automatically.
- `inspections.staff_headers` uses the dedicated operator JWT secret and actual
  staff-token minting primitive. It requires an explicit persisted staff actor,
  not the legacy super-admin dev token. It does not exercise password login.
- `inspections.examiner_assignment` takes the real `InspectorSessionCreate`
  schema and opens a session through the operator API. The caller must name the
  bank. `examiner_headers` uses its actual act-token endpoint, including
  ownership, active-session, expiry and separate-signing-key checks. Neither
  helper invents a tenant examiner binding or supplies a tenant-bank fallback.
- Pass bearer headers per request to the existing TestClient. There is no new
  application fixture or client wrapper; shared headers/cookies are not mutated.

`RoleBindingFactory` is the **low-level** service boundary, not grant
administration. Delegation, public-bundle restrictions, baseline membership,
Owner designation, assignment SoD and grant audit tests must invoke their owning
services/APIs. For validity windows, suspension/revocation, invalid stored rows,
forged claims, missing bank claims or unavailable signing secrets, use the real
service/model/credential primitives directly. Do not add permissive presets.

## Exact executable contracts and pilot

Run from the repository root:

```sh
uv run --frozen --project backend pytest \
  backend/tests/identity/service/test_role_scope_factories_integration.py \
  backend/tests/identity/service/test_authorization_integration.py \
  backend/tests/operator/test_role_scope_factories_integration.py \
  backend/tests/operator/test_inspector_api_integration.py -q
```

The new identity contracts execute no-grant discovery/detail refusal, exact
module/sensitivity/VIEW-versus-EXPORT behavior, incomplete-row non-composition,
cross-bank/organization refusal, Credit branch/region preservation, unsupported
narrowing refusal, machine type/bundle checks, caller rollback, stale access
tokens and revoked refresh families. Expected outcomes are stated independently,
not calculated by these factories.

The existing identity pilot retains the assertions in
`test_binding_creation_bumps_version_and_revokes_refresh_families_atomically`,
`test_institution_binding_allows_one_institution_and_denies_its_sibling` and
`test_explicit_organization_binding_covers_sibling_institutions`.
The operator pilot retains `test_session_is_bound_to_one_examined_bank`.
The new operator contracts exercise a real staff assignment/mint, independent
credential families, exact-bank cited-register reads, sibling/foreign refusal,
mutation refusal, developer break-glass refusal and caller/ended-session checks.

These are test-infrastructure characterizations, not a policy-matrix rollout or
proof of every route's authorization. Regulated examiner/register access is
exercised read-only; no calculation, filing, migration, schema, key custody or
application runtime changes. The separate Signer/configuration/notification
missions remain independent. There is no new read-only regulatory exporter:
the existing Viewer/Validator EXPORT gap is not resolved by a fixture.

This pilot stays separate from the code-organization audit's P01 domain-purity,
P02 public-interface and P17 script/documentation slices. It moves no tests or
fixtures and changes no suite ownership, so the later P06 curve-test move can
retain existing collection and fixture resolution.
