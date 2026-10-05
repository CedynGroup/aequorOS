"""What the database refuses about a data scope, on whichever dialect it is.

Every case below runs twice in CI: once on SQLite in the hermetic suite, where
the schema comes from ``Base.metadata.create_all``, and once on Postgres in the
schema job. Both matter for a different reason. The hermetic run is the one a
developer sees, so the model's ``__table_args__`` has to carry the constraint at
all; the Postgres run is the one a deployment sees, and ``json_array_length`` is
a different function in each dialect rather than a shared one.

The safety case is the empty list. Without the second half of
``ck_authorization_bindings_data_scope_values`` a ``branch`` binding could store
``[]`` or NULL, and the natural way to write the reader (``if values: inject a
filter``) would then serve that principal THE WHOLE BOOK. A scope meaning "no
branches" has to be unstorable rather than merely discouraged.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.authorization import (
    BindingStatus,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.base import utc_now
from app.models import AuthorizationBinding, User
from tests.api.helpers import ORG_1, USER_1


def _row(
    *,
    kind: str,
    values: object,
    role_bundle: str = RoleBundle.VIEWER.value,
    principal_type: str = PrincipalType.HUMAN.value,
    principal_user_id=USER_1,
) -> AuthorizationBinding:
    return AuthorizationBinding(
        organization_id=ORG_1,
        principal_user_id=principal_user_id,
        principal_type=principal_type,
        role_bundle=role_bundle,
        institution_scope=InstitutionScope.ORGANIZATION.value,
        institution_id=None,
        module_scope=ModuleScope.CREDIT.value,
        sensitivity_scope=SensitivityScope.ALL.value,
        data_scope_kind=kind,
        data_scope_values=values,
        granted_by_type=GrantorType.SYSTEM.value,
        granted_by_id="data-scope-check-test",
        grant_reason="exercise the data-scope constraints",
        granted_at=utc_now(),
        status=BindingStatus.ACTIVE.value,
        valid_from=utc_now(),
    )


def _refused(db: Session, row: AuthorizationBinding) -> None:
    db.add(row)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def _admitted(db: Session, row: AuthorizationBinding) -> None:
    db.add(row)
    db.flush()
    assert row.id is not None
    db.rollback()


# --- refused shapes -----------------------------------------------------------


def test_whole_institution_with_a_value_list_is_refused(db_session: Session) -> None:
    """A list a row does not apply is a list a future reader could start honouring."""
    _refused(db_session, _row(kind="all", values=["ACC-001"]))


def test_whole_institution_with_an_empty_list_is_refused(db_session: Session) -> None:
    _refused(db_session, _row(kind="all", values=[]))


def test_a_branch_scope_with_no_list_is_refused(db_session: Session) -> None:
    _refused(db_session, _row(kind="branch", values=None))


def test_a_branch_scope_with_an_empty_list_is_refused(db_session: Session) -> None:
    """THE safety case: an empty list read as "no filter" serves the whole book."""
    _refused(db_session, _row(kind="branch", values=[]))


def test_a_region_scope_with_an_empty_list_is_refused(db_session: Session) -> None:
    _refused(db_session, _row(kind="region", values=[]))


def test_an_unknown_kind_is_refused(db_session: Session) -> None:
    _refused(db_session, _row(kind="mixed", values=["ACC-001"]))


def test_the_derived_none_kind_is_refused(db_session: Session) -> None:
    """``none`` describes a reduction over no bindings, never a stored row."""
    _refused(db_session, _row(kind="none", values=None))


@pytest.mark.parametrize("kind", ["branch", "region"])
@pytest.mark.parametrize("status", list(BindingStatus))
def test_non_credit_narrowing_is_refused_in_every_lifecycle_state(
    db_session: Session, kind: str, status: BindingStatus
) -> None:
    row = _row(kind=kind, values=["ACC-001"])
    row.module_scope = ModuleScope.LIQUIDITY.value
    row.status = status.value
    if status == BindingStatus.REVOKED:
        row.revoked_at = utc_now()
        row.revoked_by_type = GrantorType.SYSTEM.value
        row.revoked_by_id = "data-scope-check-test"
        row.revoked_reason = "exercise the revoked scope constraint"
    db_session.add(row)
    with pytest.raises(IntegrityError, match="ck_authorization_bindings_narrowed_module"):
        db_session.flush()
    db_session.rollback()


def test_a_human_holding_the_machine_feed_bundle_is_refused(db_session: Session) -> None:
    _refused(
        db_session,
        _row(kind="all", values=None, role_bundle=RoleBundle.BI_READER.value),
    )


def test_a_machine_holding_a_human_bundle_is_refused(db_session: Session) -> None:
    machine = _machine(db_session)
    _refused(
        db_session,
        _row(
            kind="all",
            values=None,
            role_bundle=RoleBundle.VIEWER.value,
            principal_type=PrincipalType.MACHINE.value,
            principal_user_id=machine.id,
        ),
    )


# --- admitted shapes ----------------------------------------------------------


def test_whole_institution_with_no_list_is_admitted(db_session: Session) -> None:
    _admitted(db_session, _row(kind="all", values=None))


def test_a_branch_scope_with_two_codes_is_admitted(db_session: Session) -> None:
    _admitted(db_session, _row(kind="branch", values=["ACC-001", "TEM-002"]))


def test_a_region_scope_with_one_name_is_admitted(db_session: Session) -> None:
    _admitted(db_session, _row(kind="region", values=["Greater Accra"]))


def test_a_machine_holding_the_feed_bundle_is_admitted(db_session: Session) -> None:
    machine = _machine(db_session)
    _admitted(
        db_session,
        _row(
            kind="all",
            values=None,
            role_bundle=RoleBundle.BI_READER.value,
            principal_type=PrincipalType.MACHINE.value,
            principal_user_id=machine.id,
        ),
    )


def _machine(db: Session) -> User:
    """A service identity, created inside the test's own rolled-back savepoint."""
    machine = User(
        organization_id=ORG_1,
        email="feed@service.aequoros.invalid",
        role="viewer",
        auth_provider="service",
    )
    db.add(machine)
    db.flush()
    return machine
