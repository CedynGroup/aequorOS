"""``effective_data_scope``: the one authority on which slice a principal reads.

Four tracks call this function, so its refusals matter more than its successes.
The two that carry the safety are pinned first: an empty selector serves NOTHING
rather than everything, and the caller's id list is a SELECTOR rather than a
grant — a binding id from another tenant, or one since revoked or expired,
contributes nothing even though the caller named it.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.authorization import (
    BindingStatus,
    DataScope,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.base import utc_now
from app.identity.service import authorization
from app.identity.service.authorization import ALL_INSTITUTION_DATA, NO_INSTITUTION_DATA
from app.models import AuthorizationBinding, Bank, User
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.support.helpers import ORG_1, ORG_2, USER_1, USER_2

BANK_1 = "BK-DSCOPE01"
BANK_2 = "BK-DSCOPE02"


@pytest.fixture(autouse=True)
def _banks(db_session: Session) -> None:
    db_session.execute(delete(AuthorizationBinding))
    for bank_id, organization_id, name in (
        (BANK_1, ORG_1, "Data Scope Bank One"),
        (BANK_2, ORG_2, "Data Scope Bank Two"),
    ):
        db_session.add(
            Bank(
                id=bank_id,
                organization_id=organization_id,
                name=name,
                short_name=name,
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
    db_session.commit()


def _binding(  # noqa: PLR0913 - one complete row is explicit
    db: Session,
    *,
    data_scope: DataScope,
    values: list[str] | None,
    organization_id: str = ORG_1,
    institution_id: str = BANK_1,
    principal_user_id=USER_1,
    status: BindingStatus = BindingStatus.ACTIVE,
    valid_until=None,
) -> AuthorizationBinding:
    revoked = status is BindingStatus.REVOKED
    row = AuthorizationBinding(
        organization_id=organization_id,
        principal_user_id=principal_user_id,
        principal_type=PrincipalType.HUMAN.value,
        role_bundle=RoleBundle.VIEWER.value,
        institution_scope=InstitutionScope.INSTITUTION.value,
        institution_id=institution_id,
        module_scope=ModuleScope.CREDIT.value,
        sensitivity_scope=SensitivityScope.ALL.value,
        data_scope_kind=data_scope.value,
        data_scope_values=values,
        granted_by_type=GrantorType.SYSTEM.value,
        granted_by_id="effective-data-scope-test",
        grant_reason="prove the scope reduction",
        granted_at=utc_now() - timedelta(days=2),
        status=status.value,
        valid_from=utc_now() - timedelta(days=2),
        valid_until=valid_until,
        revoked_at=utc_now() if revoked else None,
        revoked_by_type=GrantorType.SYSTEM.value if revoked else None,
        revoked_by_id="effective-data-scope-test" if revoked else None,
        revoked_reason="prove a revoked row contributes nothing" if revoked else None,
    )
    db.add(row)
    db.commit()
    return row


def _scope(db: Session, *bindings: AuthorizationBinding, organization_id: str = ORG_1):
    return authorization.effective_data_scope(
        db,
        organization_id=organization_id,
        binding_ids=[binding.id for binding in bindings],
    )


def test_an_empty_selector_serves_nothing_never_the_whole_institution(
    db_session: Session,
) -> None:
    scope = authorization.effective_data_scope(db_session, organization_id=ORG_1, binding_ids=[])

    assert scope is NO_INSTITUTION_DATA
    assert scope.serves_nothing
    assert not scope.whole_institution


def test_one_whole_institution_binding_reads_the_whole_institution(db_session: Session) -> None:
    binding = _binding(db_session, data_scope=DataScope.ALL, values=None)

    assert _scope(db_session, binding) == ALL_INSTITUTION_DATA


def test_one_branch_binding_reads_exactly_its_branches(db_session: Session) -> None:
    binding = _binding(db_session, data_scope=DataScope.BRANCH, values=["ACC-001"])

    scope = _scope(db_session, binding)

    assert scope.kind == "branch"
    assert scope.branches == ("ACC-001",)
    assert scope.regions == ()


def test_two_branch_bindings_union(db_session: Session) -> None:
    first = _binding(db_session, data_scope=DataScope.BRANCH, values=["TEM-002"])
    second = _binding(db_session, data_scope=DataScope.BRANCH, values=["ACC-001", "KUM-003"])

    scope = _scope(db_session, first, second)

    assert scope.kind == "branch"
    assert scope.branches == ("ACC-001", "KUM-003", "TEM-002")


def test_a_branch_binding_and_a_region_binding_are_mixed(db_session: Session) -> None:
    branch = _binding(db_session, data_scope=DataScope.BRANCH, values=["ACC-001"])
    region = _binding(db_session, data_scope=DataScope.REGION, values=["Ashanti"])

    scope = _scope(db_session, branch, region)

    assert scope.kind == "mixed"
    assert scope.branches == ("ACC-001",)
    assert scope.regions == ("Ashanti",)


def test_a_whole_institution_binding_beside_a_branch_one_wins(db_session: Session) -> None:
    """Bindings OR, so narrowing here would revoke granted authority."""
    whole = _binding(db_session, data_scope=DataScope.ALL, values=None)
    branch = _binding(db_session, data_scope=DataScope.BRANCH, values=["ACC-001"])

    assert _scope(db_session, whole, branch) == ALL_INSTITUTION_DATA


def test_a_revoked_binding_contributes_nothing(db_session: Session) -> None:
    live = _binding(db_session, data_scope=DataScope.BRANCH, values=["ACC-001"])
    revoked = _binding(
        db_session,
        data_scope=DataScope.ALL,
        values=None,
        status=BindingStatus.REVOKED,
    )

    scope = _scope(db_session, live, revoked)

    assert scope.kind == "branch", "a revoked whole-institution row must not widen the read"
    assert scope.branches == ("ACC-001",)


def test_an_expired_binding_contributes_nothing(db_session: Session) -> None:
    live = _binding(db_session, data_scope=DataScope.BRANCH, values=["ACC-001"])
    expired = _binding(
        db_session,
        data_scope=DataScope.ALL,
        values=None,
        valid_until=utc_now() - timedelta(days=1),
    )

    scope = _scope(db_session, live, expired)

    assert scope.kind == "branch"
    assert scope.branches == ("ACC-001",)


def test_a_suspended_binding_contributes_nothing(db_session: Session) -> None:
    live = _binding(db_session, data_scope=DataScope.BRANCH, values=["ACC-001"])
    suspended = _binding(
        db_session,
        data_scope=DataScope.ALL,
        values=None,
        status=BindingStatus.SUSPENDED,
    )

    scope = _scope(db_session, live, suspended)

    assert scope.kind == "branch"
    assert scope.branches == ("ACC-001",)


def test_a_foreign_tenants_binding_id_contributes_nothing(db_session: Session) -> None:
    """The caller's id list is a selector, not authority."""
    assert db_session.get(User, USER_2) is not None, "the fixture's second tenant identity"
    foreign = _binding(
        db_session,
        data_scope=DataScope.ALL,
        values=None,
        organization_id=ORG_2,
        institution_id=BANK_2,
        principal_user_id=USER_2,
    )
    home = _binding(db_session, data_scope=DataScope.BRANCH, values=["ACC-001"])

    scope = _scope(db_session, home, foreign)

    assert scope.kind == "branch", (
        "naming another tenant's whole-institution binding must not widen this tenant's read"
    )
    assert scope.branches == ("ACC-001",)


def test_a_selector_whose_every_id_falls_away_serves_nothing(db_session: Session) -> None:
    """Indistinguishable from an empty list, which is the safe direction."""
    revoked = _binding(
        db_session,
        data_scope=DataScope.BRANCH,
        values=["ACC-001"],
        status=BindingStatus.REVOKED,
    )

    assert _scope(db_session, revoked) is NO_INSTITUTION_DATA


def test_an_unknown_binding_id_serves_nothing(db_session: Session) -> None:
    scope = authorization.effective_data_scope(
        db_session, organization_id=ORG_1, binding_ids=[uuid4()]
    )

    assert scope is NO_INSTITUTION_DATA


def test_a_narrow_scope_on_an_ORGANIZATION_wide_binding_is_refused_by_the_SERVICE(
    db_session: Session,
) -> None:
    """Audit A10-05: the shape was storable by any caller that skipped the schema.

    A branch code belongs to one institution's core banking system, so two sibling
    banks of one organization can share a code meaning two different books. An
    organization-wide binding naming ``B1`` therefore describes no slice anybody
    can resolve, and the request schema already refuses it with a sentence — but a
    schema guards only the one route that uses it. ``_validate_scope`` returned
    early for organization-wide coverage and never looked at the data scope, so
    every other service caller could write it.

    The database CHECK still permits the shape on purpose, so an organization-wide
    REGION grant can be designed later without a migration. Until then the service
    is the refusal, and this is the test that says so.
    """

    for kind, values in ((DataScope.BRANCH, ("B1",)), (DataScope.REGION, ("North",))):
        with pytest.raises(authorization.AuthorizationInvariantError) as refused:
            authorization.create_role_binding(
                db_session,
                organization_id=ORG_1,
                principal_user_id=USER_1,
                principal_type=PrincipalType.HUMAN,
                role_bundle=RoleBundle.VIEWER,
                scope=authorization.BindingScope(
                    InstitutionScope.ORGANIZATION,
                    None,
                    ModuleScope.CREDIT,
                    SensitivityScope.ALL,
                    kind,
                    values,
                ),
                grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
                reason="A narrow slice with no institution to resolve it against.",
            )
        assert "exact institution coverage" in str(refused.value), str(refused.value)

    # The positive half: the same sentence at institution scope is accepted, and
    # organization-wide coverage is still accepted when the slice is the whole book.
    # Without these, a change refusing every binding would pass.
    authorization.create_role_binding(
        db_session,
        organization_id=ORG_1,
        principal_user_id=USER_1,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization.BindingScope(
            InstitutionScope.INSTITUTION,
            BANK_1,
            ModuleScope.CREDIT,
            SensitivityScope.ALL,
            DataScope.BRANCH,
            ("B1",),
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="A branch slice of one exact institution.",
    )
    authorization.create_role_binding(
        db_session,
        organization_id=ORG_1,
        principal_user_id=USER_1,
        principal_type=PrincipalType.HUMAN,
        role_bundle=RoleBundle.VIEWER,
        scope=authorization.BindingScope(
            InstitutionScope.ORGANIZATION,
            None,
            ModuleScope.ALL,
            SensitivityScope.ALL,
            DataScope.ALL,
        ),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
        reason="The whole book of every institution.",
    )
