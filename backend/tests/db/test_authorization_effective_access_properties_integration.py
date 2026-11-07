"""Stateful Postgres proofs that effective access is exactly the current grants.

These machines drive the real grant-administration and reconciliation APIs over
a migrated schema with FORCE RLS. Every transition is followed by an independent
reference comparison across a complete bank/module/sensitivity/permission grid,
so partial bindings cannot compose into unrequested authority and revocation
cannot leave residual access.

Two conventions keep the proof honest and the data disposable:

* The reference oracle restates the role permission map instead of importing the
  production bundle table. A mistake in that table must disagree with the oracle
  rather than be copied by it.
* Each Hypothesis example owns a brand-new synthetic organization. The
  governance tables are append-only for the application role (no ``DELETE``
  grant), which is the production contract, so examples are isolated by tenant
  rather than by deleting rows. Nothing here reads or writes real bank data.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,  # pyright: ignore[reportUnknownVariableType] - Hypothesis factory generic
)
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.authorization import (
    InstitutionScope,
    Module,
    ModuleScope,
    Permission,
    PrincipalLocator,
    PrincipalType,
    ResourceLocator,
    RoleBundle,
    Sensitivity,
    SensitivityScope,
)
from app.core.config import get_settings
from app.db.session import get_engine, get_sessionmaker
from app.identity.service import authorization
from app.main import create_app
from app.models import (
    AuditEvent,
    AuthorizationBinding,
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    Job,
    Organization,
    ReconciliationException,
    User,
)
from tests.support.helpers import error_envelope, headers
from tests.support.postgres_migrations import (
    MigratedPostgresSchema,
    clear_database_caches,
    forward_migrated_postgres_schema,
    migrated_postgres_schema,
)

__all__ = ["forward_migrated_postgres_schema", "migrated_postgres_schema"]

pytestmark = pytest.mark.skipif(
    os.getenv("TEST_DATABASE_URL") is None,
    reason="TEST_DATABASE_URL is required for Postgres RLS authorization properties.",
)

# Per-run machine exploration depth. Local and per-PR runs keep the default;
# the scheduled RLS-depth job (`.github/workflows/rls-depth.yml`) raises it
# through ``AQS_RLS_PROPERTY_EXAMPLES``. A malformed override fails at import
# instead of silently shrinking the budget.
_PROPERTY_EXAMPLES = int(os.environ.get("AQS_RLS_PROPERTY_EXAMPLES", "10"))

_ROLE_PERMISSIONS: dict[RoleBundle, frozenset[Permission]] = {
    RoleBundle.VIEWER: frozenset({Permission.VIEW}),
    RoleBundle.AUDITOR: frozenset({Permission.VIEW}),
    RoleBundle.ANALYST: frozenset(
        {
            Permission.VIEW,
            Permission.CREATE,
            Permission.EDIT,
            Permission.RUN,
            Permission.VALIDATE,
            Permission.EXPORT,
        }
    ),
    RoleBundle.APPROVER: frozenset({Permission.VIEW, Permission.REVIEW, Permission.APPROVE}),
}
_GRANTABLE_ROLES = (RoleBundle.VIEWER, RoleBundle.ANALYST, RoleBundle.APPROVER)
#: ``FX`` and ``PUBLISHED`` are deliberately in the evaluated grid while never
#: being granted by the machines: they prove the grid denies unrequested cells
#: instead of merely confirming the ones the machine happened to create.
_GRID_MODULES = (Module.DATA, Module.LIQUIDITY, Module.CREDIT, Module.FX)
#: The modules the generated rules may grant; ``FX`` stays an ungranted column.
_GRANTABLE_MODULES = (Module.DATA, Module.LIQUIDITY, Module.CREDIT)
_GRID_SENSITIVITIES = (
    Sensitivity.PUBLISHED,
    Sensitivity.AGGREGATED,
    Sensitivity.CONFIDENTIAL,
    Sensitivity.RESTRICTED,
)
#: The sensitivities the generated rules may grant; ``PUBLISHED`` stays a control.
_GRANTABLE_SENSITIVITIES = (
    Sensitivity.AGGREGATED,
    Sensitivity.CONFIDENTIAL,
    Sensitivity.RESTRICTED,
)
#: The structural grid supplies no workflow context, so permissions gated on a
#: maker/checker or step-up condition must deny there even for a matching row.
_WORKFLOW_PERMISSIONS = frozenset({Permission.APPROVE, Permission.SIGN_OFF, Permission.SUBMIT})
_PROPOSAL = {
    "reason": "Known custody-feed gap, quantified and bounded.",
    "max_gap_fraction": "0.002",
    "effective_from": "2026-06-01",
    "effective_to": "2026-09-30",
}
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_SEQUENCE = itertools.count(1)


def _public_id(prefix: str, slot: int) -> str:
    """A well-formed synthetic ``OR-``/``BK-`` platform id, unique per example."""
    body = "".join(_CROCKFORD[(slot // 32**index) % 32] for index in range(8))
    return f"{prefix}-{body}"


def _body(response: httpx.Response | httpx2.Response) -> dict[str, object]:
    """A JSON object body with a known shape; every caller names its own keys."""
    return cast(dict[str, object], response.json())


def _listed_bank_ids(response: httpx.Response | httpx2.Response) -> list[str]:
    """The banks a caller is shown, in the order the API returns them."""
    rows = _body(response)["banks"]
    assert isinstance(rows, list)
    return [str(cast(dict[str, object], row)["id"]) for row in cast("list[object]", rows)]


@contextmanager
def _tenant_session(organization_id: str) -> Generator[Session]:
    session = get_sessionmaker()()
    session.info["organization_id"] = organization_id
    try:
        yield session
    finally:
        session.close()


@dataclass(frozen=True)
class _Tenant:
    """One synthetic institution group, fully owned by a single example."""

    organization_id: str
    bank_a: str
    bank_b: str
    owner: UUID
    target: UUID
    maker: UUID
    checker: UUID
    reader: UUID

    @property
    def banks(self) -> tuple[str, str]:
        return (self.bank_a, self.bank_b)

    @property
    def user_ids(self) -> tuple[UUID, ...]:
        return (self.owner, self.target, self.maker, self.checker, self.reader)


def _seed_tenant() -> _Tenant:
    """Create a fresh organization, two of its banks, five principals and the owner."""
    slot = next(_SEQUENCE)
    tenant = _Tenant(
        organization_id=_public_id("OR", slot),
        bank_a=_public_id("BK", slot),
        bank_b=_public_id("BK", slot + 10_000_000),
        owner=uuid4(),
        target=uuid4(),
        maker=uuid4(),
        checker=uuid4(),
        reader=uuid4(),
    )
    with _tenant_session(tenant.organization_id) as db:
        db.add(Organization(id=tenant.organization_id, name="Authorization property tenant"))
        for index, bank_id in enumerate(tenant.banks, start=1):
            db.add(
                Bank(
                    id=bank_id,
                    organization_id=tenant.organization_id,
                    name=f"Authorization property bank {index}",
                    short_name=f"Auth property {index}",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="universal_bank",
                    institution_type="universal_bank",
                )
            )
        for user_id, label in zip(
            tenant.user_ids,
            ("Owner", "Target", "Maker", "Checker", "Reader"),
            strict=True,
        ):
            db.add(
                User(
                    id=user_id,
                    organization_id=tenant.organization_id,
                    email=f"{label.lower()}-{user_id.hex[:8]}@property-auth.example",
                    display_name=f"Property {label}",
                    role="viewer",
                    auth_provider="password",
                    is_active=True,
                )
            )
        db.flush()
        authorization.create_role_binding(
            db,
            organization_id=tenant.organization_id,
            principal_user_id=tenant.owner,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.ORG_OWNER,
            scope=authorization.BindingScope(
                InstitutionScope.ORGANIZATION,
                None,
                ModuleScope.ACCOUNT,
                SensitivityScope.ALL,
            ),
            grantor=authorization.GrantorRef(
                authorization.GrantorType.SYSTEM, "authorization-property"
            ),
            reason="explicit owner authority for authorization property tests",
        )
        db.commit()
    return tenant


def _authorization_version(tenant: _Tenant, user_id: UUID) -> int:
    with _tenant_session(tenant.organization_id) as db:
        version = db.scalar(select(User.authorization_version).where(User.id == user_id))
    assert version is not None
    return version


def _auth_headers(
    tenant: _Tenant,
    user_id: UUID,
    *,
    authorization_version: int | None = None,
    roles: tuple[str, ...] = ("viewer",),
) -> dict[str, str]:
    return headers(
        org_id=tenant.organization_id,
        user_id=user_id,
        roles=roles,
        authorization_version=(
            _authorization_version(tenant, user_id)
            if authorization_version is None
            else authorization_version
        ),
    )


def _owner_headers(tenant: _Tenant) -> dict[str, str]:
    return _auth_headers(tenant, tenant.owner, roles=("account_admin",))


def _rls_guard(schema: MigratedPostgresSchema) -> None:
    with schema.app_engine.connect() as connection:
        role_state = connection.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
        rows = connection.execute(
            text(
                """
                SELECT c.relname, c.relrowsecurity AND c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema_name
                  AND c.relname = ANY(:tables)
                """
            ),
            {
                "schema_name": schema.schema_name,
                "tables": [
                    "authorization_bindings",
                    "authorization_access_requests",
                    "banks",
                    "bank_reporting_periods",
                    "bank_financial_facts",
                    "reconciliation_exceptions",
                ],
            },
        ).all()
        forced = dict(cast("Sequence[tuple[str, bool]]", rows))
    if role_state[0] or role_state[1]:
        pytest.skip("Current TEST_DATABASE_URL role bypasses RLS.")
    assert forced and all(forced.values()), f"FORCE RLS is missing: {forced}"


@dataclass(frozen=True)
class _GrantSpec:
    role: RoleBundle
    institution_scope: InstitutionScope
    institution_id: str | None
    module: Module
    sensitivity: Sensitivity

    def covers(self, bank_id: str) -> bool:
        return self.institution_scope is InstitutionScope.ORGANIZATION or (
            self.institution_id == bank_id
        )

    def matches(
        self,
        bank_id: str,
        module: Module,
        sensitivity: Sensitivity,
        permission: Permission,
    ) -> bool:
        """Structural match: runtime workflow conditions are evaluated separately."""
        return (
            self.covers(bank_id)
            and self.module is module
            and self.sensitivity is sensitivity
            and permission in _ROLE_PERMISSIONS[self.role]
        )


@dataclass(frozen=True)
class _ExceptionSpec:
    bank_id: str
    status: str = "pending"
    revoked: bool = False
    approved_by: UUID | None = None


def _resource(organization_id: str, bank_id: str, module: Module, sensitivity: Sensitivity):
    return ResourceLocator(
        organization_id, InstitutionScope.INSTITUTION, bank_id, module, sensitivity
    )


def _create_grant(
    client: TestClient,
    tenant: _Tenant,
    user_id: UUID,
    spec: _GrantSpec,
) -> tuple[int, UUID | None]:
    """Create one complete binding through the real Owner composer."""
    payload: dict[str, object] = {
        "principal_user_id": str(user_id),
        "role_bundle": spec.role.value,
        "institution_scope": spec.institution_scope.value,
        "institution_id": spec.institution_id,
        "module_scope": spec.module.value,
        "sensitivity_scope": spec.sensitivity.value,
        "reason_category": "other",
        "reason_detail": "State-machine complete scoped grant",
    }
    preview = client.post(
        "/api/v1/authorization/bindings/preview",
        headers=_owner_headers(tenant),
        json=payload,
    )
    assert preview.status_code == 200, preview.text
    sentence = _body(preview)["authority_sentence"]
    assert isinstance(sentence, str)
    payload["expected_authority_sentence"] = sentence
    response = client.post(
        "/api/v1/authorization/bindings",
        headers=_owner_headers(tenant),
        json=payload,
    )
    if response.status_code == 201:
        binding = _body(response)["binding"]
        assert isinstance(binding, dict)
        return 201, UUID(str(cast(dict[str, object], binding)["id"]))
    assert response.status_code == 409, response.text
    return 409, None


def _revoke_grant(client: TestClient, tenant: _Tenant, binding_id: UUID) -> int:
    response = client.post(
        f"/api/v1/authorization/bindings/{binding_id}/revoke",
        headers=_owner_headers(tenant),
        json={"reason": "State-machine selected revocation"},
    )
    return response.status_code


def _assert_version_bumped(
    client: TestClient,
    tenant: _Tenant,
    user_id: UUID,
    *,
    previous: int,
    expected: int,
) -> None:
    """A grant or revocation must invalidate the principal's presented authority."""
    assert _authorization_version(tenant, user_id) == expected
    stale = client.get(
        "/api/v1/auth/me",
        headers=_auth_headers(tenant, user_id, authorization_version=previous),
    )
    assert stale.status_code == 401, stale.text
    current = client.get("/api/v1/auth/me", headers=_auth_headers(tenant, user_id))
    assert current.status_code == 200, current.text


def _assert_authority_unchanged(
    client: TestClient,
    tenant: _Tenant,
    user_id: UUID,
    *,
    version: int,
) -> None:
    """A refused command must not rotate authority or invalidate the live session."""
    assert _authorization_version(tenant, user_id) == version
    live = client.get(
        "/api/v1/auth/me",
        headers=_auth_headers(tenant, user_id, authorization_version=version),
    )
    assert live.status_code == 200, live.text


def _assert_grid(
    db: Session,
    tenant: _Tenant,
    user_id: UUID,
    grants: dict[UUID, tuple[UUID, _GrantSpec]],
) -> None:
    """The evaluator's complete decision grid must equal the requested union."""
    principal = PrincipalLocator(tenant.organization_id, user_id, PrincipalType.HUMAN)
    rows = list(
        db.scalars(
            select(AuthorizationBinding).where(
                AuthorizationBinding.organization_id == tenant.organization_id,
                AuthorizationBinding.principal_user_id == user_id,
            )
        )
    )
    active = db.scalar(select(User.is_active).where(User.id == user_id))
    for bank_id in tenant.banks:
        for module in _GRID_MODULES:
            for sensitivity in _GRID_SENSITIVITIES:
                for permission in Permission:
                    expected = {
                        binding_id
                        for binding_id, (owner, spec) in grants.items()
                        if owner == user_id
                        and spec.matches(bank_id, module, sensitivity, permission)
                    }
                    decision = authorization.evaluate_prefetched_permission(
                        principal,
                        permission,
                        _resource(tenant.organization_id, bank_id, module, sensitivity),
                        rows,
                        principal_active=active is True,
                    )
                    cell = (bank_id, module.value, sensitivity.value, permission.value)
                    # The grid carries no workflow context, so a structurally
                    # matching maker/checker permission is matched but refused.
                    assert decision.allowed is (
                        bool(expected) and permission not in _WORKFLOW_PERMISSIONS
                    ), cell
                    assert set(decision.matching_binding_ids) == expected, cell


def _use_schema(schema: MigratedPostgresSchema, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the application at the migrated schema the fixture already built."""
    database_url = schema.app_engine.url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    clear_database_caches()
    get_settings.cache_clear()
    get_engine.cache_clear()


def test_scoped_grant_lifecycle_never_exceeds_current_bindings(
    migrated_postgres_schema: MigratedPostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Grant, revoke, role change, scope change and bank switch over the real API.

    Transition table (actor: the organization Owner; target: one staff user):

    | Source  | Command    | Guard                 | Destination | Effects                 |
    | ------- | ---------- | --------------------- | ----------- | ----------------------- |
    | absent  | create     | preview agrees        | active      | row; version+1          |
    | active  | revoke     | Owner session         | revoked     | evidence; version+1     |
    | revoked | revoke     | already revoked       | revoked     | 409; nothing changes    |
    | absent  | create     | duplicate dimensions  | unchanged   | 409; nothing changes    |
    | active  | revoke+new | role or scope change  | active      | old revoked, new active |

    Every destination is re-read through the effective-authority grid: an actor's
    allowed cells must equal exactly the union of their currently active bindings.
    """
    _rls_guard(migrated_postgres_schema)
    _use_schema(migrated_postgres_schema, monkeypatch)
    app = create_app()

    class EffectiveAccessMachine(RuleBasedStateMachine):
        def __init__(self, client: TestClient) -> None:
            super().__init__()
            self.client = client
            self.tenant = _seed_tenant()
            self.grants: dict[UUID, tuple[UUID, _GrantSpec]] = {}
            self.revoked: set[UUID] = set()
            self.version = _authorization_version(self.tenant, self.tenant.target)
            # One real starting grant so revocation and role/scope change always
            # have a target; every further grant is generated.
            self._create(
                _GrantSpec(
                    RoleBundle.VIEWER,
                    InstitutionScope.INSTITUTION,
                    self.tenant.bank_a,
                    Module.DATA,
                    Sensitivity.AGGREGATED,
                )
            )

        def _create(self, spec: _GrantSpec) -> None:
            previous = self.version
            status, binding_id = _create_grant(self.client, self.tenant, self.tenant.target, spec)
            if status == 201:
                assert binding_id is not None and binding_id not in self.grants
                self.grants[binding_id] = (self.tenant.target, spec)
                self.version = previous + 1
                _assert_version_bumped(
                    self.client,
                    self.tenant,
                    self.tenant.target,
                    previous=previous,
                    expected=self.version,
                )
                return
            assert any(
                owner == self.tenant.target
                and existing.role is spec.role
                and existing.institution_scope is spec.institution_scope
                and existing.institution_id == spec.institution_id
                and existing.module is spec.module
                and existing.sensitivity is spec.sensitivity
                for owner, existing in self.grants.values()
            ), "a duplicate refusal must name an already active binding"
            _assert_authority_unchanged(
                self.client, self.tenant, self.tenant.target, version=self.version
            )

        def _revoke(self, binding_id: UUID) -> None:
            previous = self.version
            assert _revoke_grant(self.client, self.tenant, binding_id) == 200
            del self.grants[binding_id]
            self.revoked.add(binding_id)
            self.version = previous + 1
            _assert_version_bumped(
                self.client,
                self.tenant,
                self.tenant.target,
                previous=previous,
                expected=self.version,
            )

        def _check_bank_surfaces(self, bank_id: str) -> None:
            target_headers = _auth_headers(self.tenant, self.tenant.target)
            covered = any(
                owner == self.tenant.target and spec.covers(bank_id)
                for owner, spec in self.grants.values()
            )
            detail = self.client.get(f"/api/v1/banks/{bank_id}", headers=target_headers)
            assert detail.status_code == (200 if covered else 404), detail.text
            for path, sensitivity in (
                ("ingestion-summary", Sensitivity.AGGREGATED),
                ("data-activations", Sensitivity.AGGREGATED),
                ("ingestion-batches", Sensitivity.CONFIDENTIAL),
                ("canonical-positions", Sensitivity.RESTRICTED),
            ):
                expected = any(
                    owner == self.tenant.target
                    and spec.matches(bank_id, Module.DATA, sensitivity, Permission.VIEW)
                    for owner, spec in self.grants.values()
                )
                response = self.client.get(
                    f"/api/v1/banks/{bank_id}/{path}", headers=target_headers
                )
                assert response.status_code == (200 if expected else 403 if covered else 404), (
                    path,
                    response.text,
                )

        @rule(
            role=st.sampled_from(_GRANTABLE_ROLES),
            bank=st.sampled_from((0, 1, None)),
            module=st.sampled_from(_GRANTABLE_MODULES),
            sensitivity=st.sampled_from(_GRANTABLE_SENSITIVITIES),
        )
        def create_one_complete_grant(
            self,
            role: RoleBundle,
            bank: int | None,
            module: Module,
            sensitivity: Sensitivity,
        ) -> None:
            bank_id = None if bank is None else self.tenant.banks[bank]
            self._create(
                _GrantSpec(
                    role,
                    (
                        InstitutionScope.ORGANIZATION
                        if bank_id is None
                        else InstitutionScope.INSTITUTION
                    ),
                    bank_id,
                    module,
                    sensitivity,
                )
            )

        def _has_grants(self) -> bool:
            return bool(self.grants)

        @precondition(_has_grants)
        @rule(slot=st.integers(min_value=0, max_value=31))
        def revoke_one_grant(self, slot: int) -> None:
            self._revoke(sorted(self.grants, key=str)[slot % len(self.grants)])

        @precondition(_has_grants)
        @rule(
            slot=st.integers(min_value=0, max_value=31),
            role=st.sampled_from(_GRANTABLE_ROLES),
        )
        def change_role(self, slot: int, role: RoleBundle) -> None:
            binding_id = sorted(self.grants, key=str)[slot % len(self.grants)]
            current = self.grants[binding_id][1]
            replacement = replace(current, role=role)
            if replacement == current or replacement in (spec for _, spec in self.grants.values()):
                return
            self._revoke(binding_id)
            self._create(replacement)

        @precondition(_has_grants)
        @rule(
            slot=st.integers(min_value=0, max_value=31),
            bank=st.sampled_from((0, 1)),
            module=st.sampled_from(_GRANTABLE_MODULES),
            sensitivity=st.sampled_from(_GRANTABLE_SENSITIVITIES),
        )
        def change_scope(
            self,
            slot: int,
            bank: int,
            module: Module,
            sensitivity: Sensitivity,
        ) -> None:
            binding_id = sorted(self.grants, key=str)[slot % len(self.grants)]
            current = self.grants[binding_id][1]
            replacement = _GrantSpec(
                current.role,
                InstitutionScope.INSTITUTION,
                self.tenant.banks[bank],
                module,
                sensitivity,
            )
            if replacement == current or replacement in (spec for _, spec in self.grants.values()):
                return
            self._revoke(binding_id)
            self._create(replacement)

        @rule(bank=st.sampled_from((0, 1)))
        def switch_institution(self, bank: int) -> None:
            self._check_bank_surfaces(self.tenant.banks[bank])

        @rule(slot=st.integers(min_value=0, max_value=31))
        def revoked_and_unknown_grants_stay_unusable(self, slot: int) -> None:
            binding_id = (
                sorted(self.revoked, key=str)[slot % len(self.revoked)] if self.revoked else uuid4()
            )
            assert _revoke_grant(self.client, self.tenant, binding_id) == 409
            _assert_authority_unchanged(
                self.client, self.tenant, self.tenant.target, version=self.version
            )

        @invariant()
        def effective_access_equals_current_grants(self) -> None:
            with _tenant_session(self.tenant.organization_id) as db:
                _assert_grid(db, self.tenant, self.tenant.target, self.grants)
            for bank_id in self.tenant.banks:
                self._check_bank_surfaces(bank_id)
            listed = self.client.get(
                "/api/v1/banks", headers=_auth_headers(self.tenant, self.tenant.target)
            )
            assert listed.status_code == 200, listed.text
            assert _listed_bank_ids(listed) == [
                bank_id
                for bank_id in self.tenant.banks
                if any(
                    owner == self.tenant.target and spec.covers(bank_id)
                    for owner, spec in self.grants.values()
                )
            ]

    with TestClient(app, raise_server_exceptions=False) as client:
        run_state_machine_as_test(
            lambda: EffectiveAccessMachine(client),
            settings=settings(
                max_examples=_PROPERTY_EXAMPLES,
                stateful_step_count=12,
                deadline=None,
                print_blob=True,
            ),
        )


def test_reconciliation_approval_never_skips_the_second_officer(
    forward_migrated_postgres_schema: MigratedPostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Maker/checker approval over real grants, including self-approval refusal.

    Transition table (maker = proposer, checker = any other officer):

    | Source   | Command | Actor         | Guard                    | Destination |
    | -------- | ------- | ------------- | ------------------------ | ----------- |
    | none     | propose | maker         | CONFIDENTIAL DATA CREATE | pending     |
    | none     | propose | ungranted     | none                     | none        |
    | pending  | approve | checker       | CONFIDENTIAL DATA APPROVE| approved    |
    | pending  | approve | the maker     | never: no self-checking  | pending     |
    | pending  | approve | ungranted     | none                     | pending     |
    | any      | approve | any actor     | sibling bank's exception | unchanged   |
    | revoked  | approve | checker       | terminal state           | revoked     |
    | approved | approve | checker       | terminal state           | approved    |
    | approved | revoke  | checker       | CONFIDENTIAL DATA APPROVE| revoked     |
    | revoked  | revoke  | checker       | terminal state is final  | revoked     |

    Each accepted command writes exactly one governance audit event and enqueues no
    job. Each refusal returns 403 for a covered bank lacking the required permission
    and an opaque 404 for an uncovered or foreign bank, the maker's own proposal or
    a revoked row, leaving the exception rows, the audit count and the job queue
    exactly as they were.

        Uses the forward-only migrated schema: pending proposals legitimately exist
    for the previous schema's ``approved_by NOT NULL`` column, so the full
    downgrade round trip is proven by the other cases in this module and in
    ``test_postgres_migrations.py`` instead.
    """
    _rls_guard(forward_migrated_postgres_schema)
    _use_schema(forward_migrated_postgres_schema, monkeypatch)
    app = create_app()

    class ApprovalMachine(RuleBasedStateMachine):
        def __init__(self, client: TestClient) -> None:
            super().__init__()
            self.client = client
            self.tenant = _seed_tenant()
            self.grants: dict[UUID, tuple[UUID, _GrantSpec]] = {}
            self.revoked: set[UUID] = set()
            self.exceptions: dict[UUID, _ExceptionSpec] = {}
            self.versions = {
                user_id: _authorization_version(self.tenant, user_id)
                for user_id in self.tenant.user_ids
            }
            # A reachable starting authority: the maker may propose on bank A and
            # a different officer may decide. Rules still remove, change and
            # re-add authority, so the seed never hides an invariant.
            self._create(
                self.tenant.maker,
                _GrantSpec(
                    RoleBundle.ANALYST,
                    InstitutionScope.INSTITUTION,
                    self.tenant.bank_a,
                    Module.DATA,
                    Sensitivity.CONFIDENTIAL,
                ),
            )
            self._create(
                self.tenant.checker,
                _GrantSpec(
                    RoleBundle.APPROVER,
                    InstitutionScope.INSTITUTION,
                    self.tenant.bank_a,
                    Module.DATA,
                    Sensitivity.CONFIDENTIAL,
                ),
            )

        def headers_for(self, user_id: UUID) -> dict[str, str]:
            return _auth_headers(
                self.tenant,
                user_id,
                authorization_version=self.versions[user_id],
                roles=("approver",),
            )

        def allowed(
            self,
            user_id: UUID,
            bank_id: str,
            sensitivity: Sensitivity,
            permission: Permission,
        ) -> bool:
            return any(
                owner == user_id and spec.matches(bank_id, Module.DATA, sensitivity, permission)
                for owner, spec in self.grants.values()
            )

        def _create(self, user_id: UUID, spec: _GrantSpec) -> None:
            previous = self.versions[user_id]
            status, binding_id = _create_grant(self.client, self.tenant, user_id, spec)
            if status == 201:
                assert binding_id is not None
                self.grants[binding_id] = (user_id, spec)
                self.versions[user_id] = previous + 1
                _assert_version_bumped(
                    self.client,
                    self.tenant,
                    user_id,
                    previous=previous,
                    expected=previous + 1,
                )
                return
            assert any(
                owner == user_id
                and existing.role is spec.role
                and existing.institution_scope is spec.institution_scope
                and existing.institution_id == spec.institution_id
                and existing.module is spec.module
                and existing.sensitivity is spec.sensitivity
                for owner, existing in self.grants.values()
            )
            _assert_authority_unchanged(
                self.client, self.tenant, user_id, version=self.versions[user_id]
            )

        def _revoke(self, binding_id: UUID) -> None:
            user_id, _spec = self.grants[binding_id]
            previous = self.versions[user_id]
            assert _revoke_grant(self.client, self.tenant, binding_id) == 200
            del self.grants[binding_id]
            self.revoked.add(binding_id)
            self.versions[user_id] = previous + 1
            _assert_version_bumped(
                self.client,
                self.tenant,
                user_id,
                previous=previous,
                expected=previous + 1,
            )

        def _evidence(self) -> tuple[tuple[tuple[str, ...], ...], int, int]:
            """Persisted governance evidence a refused command must not move."""
            with _tenant_session(self.tenant.organization_id) as db:
                rows = tuple(
                    (
                        str(row.id),
                        row.status,
                        str(row.requested_by),
                        str(row.approved_by_user_id),
                        str(row.approval_timestamp),
                        str(row.revoked_at),
                    )
                    for row in db.scalars(
                        select(ReconciliationException)
                        .where(
                            ReconciliationException.organization_id == self.tenant.organization_id
                        )
                        .order_by(ReconciliationException.id)
                    )
                )
                events = db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.organization_id == self.tenant.organization_id)
                )
                jobs = db.scalar(
                    select(func.count())
                    .select_from(Job)
                    .where(Job.organization_id == self.tenant.organization_id)
                )
                return rows, int(events or 0), int(jobs or 0)

        @rule(
            user=st.sampled_from((0, 1, 2)),
            bank=st.sampled_from((0, 1)),
            role=st.sampled_from(_GRANTABLE_ROLES),
            module=st.sampled_from((Module.DATA, Module.LIQUIDITY)),
            sensitivity=st.sampled_from((Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED)),
        )
        def grant_authority(
            self,
            user: int,
            bank: int,
            role: RoleBundle,
            module: Module,
            sensitivity: Sensitivity,
        ) -> None:
            user_id = (self.tenant.maker, self.tenant.checker, self.tenant.reader)[user]
            self._create(
                user_id,
                _GrantSpec(
                    role,
                    InstitutionScope.INSTITUTION,
                    self.tenant.banks[bank],
                    module,
                    sensitivity,
                ),
            )

        def _has_grants(self) -> bool:
            return bool(self.grants)

        def _has_revoked(self) -> bool:
            return bool(self.revoked)

        def _has_exceptions(self) -> bool:
            return bool(self.exceptions)

        @precondition(_has_grants)
        @rule(slot=st.integers(min_value=0, max_value=31))
        def revoke_authority(self, slot: int) -> None:
            self._revoke(sorted(self.grants, key=str)[slot % len(self.grants)])

        @precondition(_has_revoked)
        @rule(slot=st.integers(min_value=0, max_value=31))
        def revoke_revoked_authority(self, slot: int) -> None:
            binding_id = sorted(self.revoked, key=str)[slot % len(self.revoked)]
            before = self._evidence()
            assert _revoke_grant(self.client, self.tenant, binding_id) == 409
            assert self._evidence() == before

        @rule(bank=st.sampled_from((0, 1)))
        def propose_exception(self, bank: int) -> None:
            bank_id = self.tenant.banks[bank]
            before = self._evidence()
            response = self.client.post(
                f"/api/v1/banks/{bank_id}/reconciliation/exceptions",
                headers=self.headers_for(self.tenant.maker),
                json=_PROPOSAL,
            )
            if not self.allowed(
                self.tenant.maker, bank_id, Sensitivity.CONFIDENTIAL, Permission.CREATE
            ):
                covered = any(
                    owner == self.tenant.maker and spec.covers(bank_id)
                    for owner, spec in self.grants.values()
                )
                assert response.status_code == (403 if covered else 404), response.text
                assert self._evidence() == before
                return
            assert response.status_code == 201, response.text
            row = _body(response)
            exception_id = UUID(str(row["id"]))
            assert exception_id not in self.exceptions
            assert row["requested_by"] == str(self.tenant.maker)
            assert row["status"] == "pending"
            assert row["approved_by_user_id"] is None
            rows, events, jobs = self._evidence()
            assert len(rows) == len(before[0]) + 1
            assert (events, jobs) == (before[1] + 1, before[2])
            self.exceptions[exception_id] = _ExceptionSpec(bank_id)

        @precondition(_has_exceptions)
        @rule(
            slot=st.integers(min_value=0, max_value=31),
            actor=st.sampled_from((0, 1, 2)),
            path_bank=st.sampled_from((0, 1)),
        )
        def approve_exception(self, slot: int, actor: int, path_bank: int) -> None:
            user_id = (self.tenant.maker, self.tenant.checker, self.tenant.reader)[actor]
            bank_id = self.tenant.banks[path_bank]
            exception_id = sorted(self.exceptions, key=str)[slot % len(self.exceptions)]
            expected = self.exceptions[exception_id]
            before = self._evidence()
            response = self.client.post(
                f"/api/v1/banks/{bank_id}/reconciliation/exceptions/{exception_id}/approve",
                headers=self.headers_for(user_id),
                json={"approved_by": "Head of Finance"},
            )
            if (
                bank_id != expected.bank_id
                or user_id is self.tenant.maker
                or not self.allowed(user_id, bank_id, Sensitivity.CONFIDENTIAL, Permission.APPROVE)
            ):
                # Both "not your authority" and "not your own proposal" are opaque.
                assert response.status_code == 404, response.text
                assert self._evidence() == before
                return
            if expected.revoked or expected.status != "pending":
                assert response.status_code == 409, response.text
                assert self._evidence() == before
                return
            assert response.status_code == 200, response.text
            row = _body(response)
            assert row["status"] == "approved"
            assert row["requested_by"] == str(self.tenant.maker)
            assert row["approved_by_user_id"] == str(user_id)
            assert row["approval_timestamp"] is not None
            self.exceptions[exception_id] = replace(
                expected, status="approved", approved_by=user_id
            )

        @rule(actor=st.sampled_from((0, 1, 2)))
        def approve_unknown_exception(self, actor: int) -> None:
            user_id = (self.tenant.maker, self.tenant.checker, self.tenant.reader)[actor]
            before = self._evidence()
            response = self.client.post(
                f"/api/v1/banks/{self.tenant.bank_a}/reconciliation/exceptions/{uuid4()}/approve",
                headers=self.headers_for(user_id),
                json={"approved_by": "Head of Finance"},
            )
            assert response.status_code == 404, response.text
            assert self._evidence() == before

        @precondition(_has_exceptions)
        @rule(
            slot=st.integers(min_value=0, max_value=31),
            actor=st.sampled_from((0, 1, 2)),
            path_bank=st.sampled_from((0, 1)),
        )
        def revoke_exception(self, slot: int, actor: int, path_bank: int) -> None:
            user_id = (self.tenant.maker, self.tenant.checker, self.tenant.reader)[actor]
            bank_id = self.tenant.banks[path_bank]
            exception_id = sorted(self.exceptions, key=str)[slot % len(self.exceptions)]
            expected = self.exceptions[exception_id]
            before = self._evidence()
            response = self.client.post(
                f"/api/v1/banks/{bank_id}/reconciliation/exceptions/{exception_id}/revoke",
                headers=self.headers_for(user_id),
                json={"revoked_by": "Head of Finance", "reason": "Feed corrected."},
            )
            if (
                bank_id != expected.bank_id
                or user_id is self.tenant.maker
                or not self.allowed(user_id, bank_id, Sensitivity.CONFIDENTIAL, Permission.APPROVE)
            ):
                assert response.status_code == 404, response.text
                assert self._evidence() == before
                return
            assert response.status_code == 200, response.text
            assert _body(response)["revoked_at"] is not None
            self.exceptions[exception_id] = replace(expected, revoked=True)

        @invariant()
        def access_and_approval_history_match_current_grants(self) -> None:
            with _tenant_session(self.tenant.organization_id) as db:
                for user_id in (
                    self.tenant.maker,
                    self.tenant.checker,
                    self.tenant.reader,
                ):
                    _assert_grid(db, self.tenant, user_id, self.grants)

            for user_id in (self.tenant.maker, self.tenant.checker, self.tenant.reader):
                for bank_id in self.tenant.banks:
                    listed = self.client.get(
                        f"/api/v1/banks/{bank_id}/reconciliation/exceptions",
                        headers=self.headers_for(user_id),
                        params={"as_of": "2026-07-01"},
                    )
                    expected_read = self.allowed(
                        user_id, bank_id, Sensitivity.CONFIDENTIAL, Permission.VIEW
                    )
                    covered = any(
                        owner == user_id and spec.covers(bank_id)
                        for owner, spec in self.grants.values()
                    )
                    assert listed.status_code == (
                        200 if expected_read else 403 if covered else 404
                    ), listed.text
                    if expected_read:
                        # A pending proposal is never effective; only an
                        # unrevoked approval inside its window can be applied.
                        active = _body(listed)["active_exception_id"]
                        approved = {
                            exception_id
                            for exception_id, spec in self.exceptions.items()
                            if spec.bank_id == bank_id
                            and spec.status == "approved"
                            and not spec.revoked
                        }
                        assert (active is None) is (not approved), active
                        if active is not None:
                            assert UUID(str(active)) in approved, active

            for exception_id, expected in self.exceptions.items():
                with _tenant_session(self.tenant.organization_id) as db:
                    row = db.get(ReconciliationException, exception_id)
                assert row is not None
                assert row.bank_id == expected.bank_id
                assert row.status == expected.status
                assert (row.revoked_at is not None) is expected.revoked
                assert row.requested_by == self.tenant.maker
                assert row.approved_by_user_id == expected.approved_by
                if row.approved_by_user_id is not None:
                    # The stored checker is a different authenticated officer.
                    assert row.approved_by_user_id != row.requested_by
                if row.status == "approved":
                    assert row.approval_timestamp is not None
                    assert row.approved_by is not None
                else:
                    assert row.approval_timestamp is None
                    assert row.approved_by is None

    with TestClient(app, raise_server_exceptions=False) as client:
        run_state_machine_as_test(
            lambda: ApprovalMachine(client),
            settings=settings(
                max_examples=_PROPERTY_EXAMPLES,
                stateful_step_count=12,
                deadline=None,
                print_blob=True,
            ),
        )


def test_uncovered_institution_is_indistinguishable_from_a_missing_one(
    migrated_postgres_schema: MigratedPostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same-organization isolation: coverage, not RLS visibility, gates a bank."""
    _rls_guard(migrated_postgres_schema)
    _use_schema(migrated_postgres_schema, monkeypatch)
    tenant = _seed_tenant()
    with _tenant_session(tenant.organization_id) as db:
        period = BankReportingPeriod(
            organization_id=tenant.organization_id,
            bank_id=tenant.bank_b,
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
            label="Sibling June",
            status="open",
        )
        db.add(period)
        db.flush()
        db.add(
            BankFinancialFact(
                organization_id=tenant.organization_id,
                bank_id=tenant.bank_b,
                reporting_period_id=period.id,
                fact_group="balance_sheet",
                category="cash_vault",
                amount=Decimal("1"),
                currency="GHS",
            )
        )
        db.commit()
    sibling_period_id = period.id
    missing_bank = "BK-MISSING1"

    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        status, binding_id = _create_grant(
            client,
            tenant,
            tenant.target,
            _GrantSpec(
                RoleBundle.ANALYST,
                InstitutionScope.INSTITUTION,
                tenant.bank_a,
                Module.DATA,
                Sensitivity.RESTRICTED,
            ),
        )
        assert status == 201 and binding_id is not None

        # The sibling's rows share the tenant: RLS lets this tenant read them.
        with _tenant_session(tenant.organization_id) as db:
            assert db.get(BankReportingPeriod, sibling_period_id) is not None
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(BankFinancialFact)
                    .where(BankFinancialFact.bank_id == tenant.bank_b)
                )
                == 1
            )

        target_headers = _auth_headers(tenant, tenant.target)
        listed = client.get("/api/v1/banks", headers=target_headers)
        assert listed.status_code == 200, listed.text
        assert _listed_bank_ids(listed) == [tenant.bank_a]

        for suffix in ("", "/reporting-periods"):
            uncovered = client.get(f"/api/v1/banks/{tenant.bank_b}{suffix}", headers=target_headers)
            missing = client.get(f"/api/v1/banks/{missing_bank}{suffix}", headers=target_headers)
            assert uncovered.status_code == 404, uncovered.text
            assert missing.status_code == 404, missing.text
            assert error_envelope(uncovered) == error_envelope(missing)

        known_sibling = client.get(
            f"/api/v1/banks/{tenant.bank_a}/reporting-periods/{sibling_period_id}/facts",
            headers=target_headers,
        )
        unknown_period = client.get(
            f"/api/v1/banks/{tenant.bank_a}/reporting-periods/{uuid4()}/facts",
            headers=target_headers,
        )
        assert known_sibling.status_code == 404, known_sibling.text
        assert unknown_period.status_code == 404, unknown_period.text
        assert error_envelope(known_sibling) == error_envelope(unknown_period)

        # Restricted DATA authority on bank A never extends to bank B, and a
        # sibling identifier is not even confirmed to exist.
        for path, expected_on_a in (
            ("ingestion-summary", 403),
            ("data-activations", 403),
            ("ingestion-batches", 403),
            ("canonical-positions", 200),
        ):
            assert (
                client.get(
                    f"/api/v1/banks/{tenant.bank_a}/{path}", headers=target_headers
                ).status_code
                == expected_on_a
            ), path
            sibling = client.get(f"/api/v1/banks/{tenant.bank_b}/{path}", headers=target_headers)
            assert sibling.status_code == 404, sibling.text
            missing = client.get(f"/api/v1/banks/{missing_bank}/{path}", headers=target_headers)
            assert missing.status_code == 404, missing.text
            assert error_envelope(sibling) == error_envelope(missing)

        for resource, action, payload in (
            ("reconciliation/exceptions", "approve", {"approved_by": "Checker"}),
            ("reconciliation/exceptions", "revoke", {"revoked_by": "Checker", "reason": "Review"}),
            ("system-of-record", "approve", {"approved_by": "Checker"}),
            ("system-of-record", "revoke", {"revoked_by": "Checker", "reason": "Review"}),
            ("canonical-withdrawals", "approve", {"approved_by": "Checker"}),
            ("canonical-withdrawals", "reverse", {"reversed_by": "Checker", "reason": "Review"}),
        ):
            suffix = f"{resource}/{uuid4()}/{action}"
            sibling = client.post(
                f"/api/v1/banks/{tenant.bank_b}/{suffix}", headers=target_headers, json=payload
            )
            missing = client.post(
                f"/api/v1/banks/{missing_bank}/{suffix}", headers=target_headers, json=payload
            )
            assert sibling.status_code == missing.status_code == 404
            assert error_envelope(sibling) == error_envelope(missing)

        payload = {"as_of_date": "2026-07-01", "reason": "Refresh"}
        sibling = client.post(
            f"/api/v1/banks/{tenant.bank_b}/data-activations",
            headers=target_headers,
            json=payload,
        )
        missing = client.post(
            f"/api/v1/banks/{missing_bank}/data-activations",
            headers=target_headers,
            json=payload,
        )
        assert sibling.status_code == missing.status_code == 404
        assert error_envelope(sibling) == error_envelope(missing)
