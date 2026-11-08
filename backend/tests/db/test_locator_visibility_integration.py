"""The SQL locator predicate must answer exactly what the evaluator answers.

``core.locator_visibility.locator_visibility`` exists so tenant read paths can
filter rows by current authority in SQL. Its only correctness claim is that it
is the evaluator's match rules expressed as a row predicate: for any grant set
and any locator, a row is visible exactly when some grant would allow
``evaluate_permission`` on a resource with that locator. These tests pin that
correspondence row by row, including every way a binding can fail to count.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, String, Table, insert, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import (
    BindingGrant,
    BindingStatus,
    DataScope,
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
    evaluate_permission,
)
from app.core.locator_visibility import locator_visibility
from app.identity.public import load_permission_grants

#: A stand-in for any table carrying the typed locator columns (#186). The
#: organization column is included because tenant isolation is a separate WHERE
#: factor the caller applies (notifications and jobs scope by organization_id
#: first); the probe keeps it so the correspondence with the evaluator is
#: total, evaluator tenant-match included. It stays OUT of ``Base.metadata``:
#: the IDOR property sweep digests every metadata table against the database,
#: and the probe must never enter that inventory.
_PROBE_METADATA = MetaData()
_LOCATOR_TABLE = Table(
    "locator_probe",
    _PROBE_METADATA,
    Column("id", String(40), primary_key=True),
    Column("organization_id", String(16), nullable=False),
    Column("institution_scope", String(16), nullable=True),
    Column("bank_id", String(16), nullable=True),
    Column("module", String(32), nullable=True),
    Column("sensitivity", String(16), nullable=True),
)

ORG = "OR-LOCAT001"
OTHER_ORG = "OR-LOCAT002"
BANK_1 = "BK-LOCAT001"
BANK_2 = "BK-LOCAT002"
USER = uuid4()

#: Probe rows spanning every locator shape: (id, institution_scope, bank, module, sensitivity).
ROWS: tuple[tuple[str, str | None, str | None, str | None, str | None], ...] = (
    ("org-data-conf", InstitutionScope.ORGANIZATION.value, None, Module.DATA.value, "confidential"),
    (
        "inst-bank1-data-conf",
        InstitutionScope.INSTITUTION.value,
        BANK_1,
        Module.DATA.value,
        Sensitivity.CONFIDENTIAL.value,
    ),
    (
        "inst-bank1-reg-restricted",
        InstitutionScope.INSTITUTION.value,
        BANK_1,
        Module.REGULATORY.value,
        Sensitivity.RESTRICTED.value,
    ),
    (
        "inst-bank2-data-conf",
        InstitutionScope.INSTITUTION.value,
        BANK_2,
        Module.DATA.value,
        Sensitivity.CONFIDENTIAL.value,
    ),
    # Legacy row (all locator columns NULL) and the bank-only shape: never
    # visible, whatever the caller holds.
    ("legacy", None, None, None, None),
    ("orphan-bank-only", None, BANK_1, None, None),
)

HUMAN = PrincipalLocator(ORG, USER, PrincipalType.HUMAN)


def _grant(  # noqa: PLR0913 - every binding dimension is an input to the match
    *,
    principal_type: PrincipalType = PrincipalType.HUMAN,
    bundle: RoleBundle = RoleBundle.VIEWER,
    institution_scope: InstitutionScope = InstitutionScope.ORGANIZATION,
    institution_id: str | None = None,
    module_scope: ModuleScope = ModuleScope.ALL,
    sensitivity_scope: SensitivityScope = SensitivityScope.ALL,
    status: BindingStatus = BindingStatus.ACTIVE,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    revoked_at: datetime | None = None,
) -> BindingGrant:
    return BindingGrant(
        binding_id=uuid4(),
        organization_id=ORG,
        principal_id=USER,
        principal_type=principal_type,
        role_bundle=bundle,
        institution_scope=institution_scope,
        institution_id=institution_id,
        module_scope=module_scope,
        sensitivity_scope=sensitivity_scope,
        status=status,
        valid_from=valid_from or datetime.now(UTC) - timedelta(days=1),
        valid_until=valid_until,
        revoked_at=revoked_at,
        data_scope=DataScope.ALL,
    )


def _visible(db: Session, grants: list[BindingGrant], row_id: str) -> bool:
    """Evaluate the predicate plus the caller's tenant factor against one probe row."""
    table = _LOCATOR_TABLE.c
    predicate = locator_visibility(
        grants,
        Permission.VIEW,
        institution_scope=table.institution_scope,
        bank_id=table.bank_id,
        module=table.module,
        sensitivity=table.sensitivity,
    )
    return (
        db.scalar(
            select(_LOCATOR_TABLE.c.id).where(
                _LOCATOR_TABLE.c.id == row_id,
                _LOCATOR_TABLE.c.organization_id == ORG,
                predicate,
            )
        )
        is not None
    )


def _evaluator_says(grants: list[BindingGrant], locator: tuple[str | None, ...]) -> bool:
    """What ``evaluate_permission`` answers for a resource with this locator."""
    scope, bank, module, sensitivity = locator
    if scope is None or module is None or sensitivity is None:
        # A locator-less row is no resource at all: the evaluator is never
        # asked, and the read paths hide it outright.
        return False
    assert scope in (InstitutionScope.ORGANIZATION.value, InstitutionScope.INSTITUTION.value)
    resource = ResourceLocator(
        organization_id=ORG,
        institution_scope=InstitutionScope(scope),
        institution_id=bank if scope == InstitutionScope.INSTITUTION.value else None,
        module=Module(module),
        sensitivity=Sensitivity(sensitivity),
    )
    return any(
        evaluate_permission(HUMAN, Permission.VIEW, resource, [grant]).allowed for grant in grants
    )


@pytest.fixture
def probe_db(db_session: Session) -> Session:
    _PROBE_METADATA.create_all(db_session.get_bind())
    db_session.execute(
        insert(_LOCATOR_TABLE),
        [
            {
                "id": row_id,
                "organization_id": ORG,
                "institution_scope": scope,
                "bank_id": bank,
                "module": module,
                "sensitivity": sens,
            }
            for row_id, scope, bank, module, sens in ROWS
        ],
    )
    return db_session


@pytest.mark.parametrize(
    "grants",
    [
        [],
        [_grant()],
        [_grant(institution_scope=InstitutionScope.INSTITUTION, institution_id=BANK_1)],
        [
            _grant(module_scope=ModuleScope.DATA, sensitivity_scope=SensitivityScope.CONFIDENTIAL),
            _grant(
                institution_scope=InstitutionScope.INSTITUTION,
                institution_id=BANK_1,
                module_scope=ModuleScope.REGULATORY,
                sensitivity_scope=SensitivityScope.RESTRICTED,
            ),
        ],
        [_grant(bundle=RoleBundle.ANALYST)],
        [_grant(bundle=RoleBundle.CONFIGURATOR)],
        [_grant(status=BindingStatus.SUSPENDED)],
        [_grant(revoked_at=datetime.now(UTC))],
        [_grant(valid_from=datetime.now(UTC) + timedelta(days=1))],
        [_grant(valid_until=datetime.now(UTC))],
        [_grant(principal_type=PrincipalType.MACHINE, bundle=RoleBundle.VIEWER)],
    ],
)
def test_predicate_matches_the_evaluator_row_for_row(
    probe_db: Session, grants: list[BindingGrant]
) -> None:
    for row_id, *locator in ROWS:
        assert _visible(probe_db, grants, row_id) is _evaluator_says(grants, tuple(locator)), (
            f"grant set {[g.binding_id for g in grants]} disagrees on row {row_id}"
        )


def test_the_grants_loader_never_hands_over_a_foreign_binding(db_session: Session) -> None:
    """Tenant isolation: bindings load only from the principal's own organization.

    The predicate itself is row-local; the tenant factor is the caller's WHERE
    clause and the loader's organization filter together, so a foreign binding
    cannot even reach a read path's grant list.
    """
    ctx = TenantContext(organization_id=OTHER_ORG, actor_user_id=USER, authorization_version=1)
    assert load_permission_grants(db_session, ctx, Permission.VIEW) == ()
