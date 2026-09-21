"""The cutover gate reports exactly what the evaluator projects — nothing inferred."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.authorization import (
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.models import Bank, Organization, User
from app.services import authorization
from app.services.institution_types import FALLBACK_TYPE_CODE
from scripts.authorization_access_impact import (
    INSTITUTION_MODULES,
    build_report,
    render_table,
)

ORG = "OR-IMPCT001"
BANK = "BK-IMPCT001"


def _user(db: Session, email: str, *, role: str = "viewer", **overrides) -> User:
    user = User(
        id=uuid4(),
        organization_id=ORG,
        email=email,
        display_name=email.split("@", maxsplit=1)[0].replace(".", " ").title(),
        role=role,
        auth_provider="password",
        is_active=True,
    )
    for key, value in overrides.items():
        setattr(user, key, value)
    db.add(user)
    return user


def _grant(  # noqa: PLR0913 - one indivisible sentence per call
    db: Session,
    user: User,
    bundle: RoleBundle,
    *,
    institution_scope: InstitutionScope = InstitutionScope.ORGANIZATION,
    institution_id: str | None = None,
    module_scope: ModuleScope = ModuleScope.ALL,
    sensitivity_scope: SensitivityScope = SensitivityScope.ALL,
) -> None:
    authorization.create_role_binding(
        db,
        organization_id=ORG,
        principal_user_id=user.id,
        principal_type=PrincipalType.HUMAN,
        role_bundle=bundle,
        scope=authorization.BindingScope(
            institution_scope, institution_id, module_scope, sensitivity_scope
        ),
        grantor=authorization.GrantorRef(authorization.GrantorType.SYSTEM, "test:impact"),
        reason="impact report fixture",
    )


def test_report_flags_users_the_dashboard_would_show_nothing(db_session: Session) -> None:
    db_session.add(Organization(id=ORG, name="Impact proof"))
    db_session.add(
        Bank(
            id=BANK,
            organization_id=ORG,
            name="Impact Bank",
            short_name="Impact",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal_bank",
            institution_type=FALLBACK_TYPE_CODE,
        )
    )
    owner = _user(db_session, "owner@impact.example", role="account_admin")
    reader = _user(db_session, "reader@impact.example")
    liquidity_only = _user(db_session, "liquidity@impact.example", role="analyst")
    _user(db_session, "nobody@impact.example")
    _user(db_session, "gone@impact.example", is_active=False)
    _user(db_session, "feed@impact.example", role="analyst", auth_provider="service")
    db_session.commit()

    _grant(db_session, owner, RoleBundle.ORG_OWNER, module_scope=ModuleScope.ACCOUNT)
    _grant(db_session, reader, RoleBundle.VIEWER)
    _grant(
        db_session,
        liquidity_only,
        RoleBundle.ANALYST,
        institution_scope=InstitutionScope.INSTITUTION,
        institution_id=BANK,
        module_scope=ModuleScope.LIQUIDITY,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    db_session.commit()

    rows = {row.email: row for row in build_report(db_session, organization_id=ORG)}

    # Inactive and machine principals are not sign-in identities: excluded.
    assert set(rows) == {
        "owner@impact.example",
        "reader@impact.example",
        "liquidity@impact.example",
        "nobody@impact.example",
    }

    owner_row = rows["owner@impact.example"]
    assert owner_row.account_administer is True
    assert owner_row.institutions == {}
    assert owner_row.flags == ["no_product_view", "account_plane_only"]

    reader_row = rows["reader@impact.example"]
    assert reader_row.account_administer is False
    assert reader_row.institutions == {BANK: sorted(INSTITUTION_MODULES)}
    assert reader_row.flags == []

    liquidity_row = rows["liquidity@impact.example"]
    assert liquidity_row.institutions == {BANK: ["liq"]}
    assert liquidity_row.flags == []

    nobody_row = rows["nobody@impact.example"]
    assert nobody_row.active_bindings == 0
    assert nobody_row.flags == ["no_bindings", "no_product_view"]
    # The scalar role is reported for context only; it never produces access.
    assert nobody_row.scalar_role == "viewer"

    # Nobody in this fixture can file: the Validator bundle is the only carrier
    # of `submit`, and no migration or scalar role produces it.
    assert all(row.filing_institutions == [] for row in rows.values())

    table = render_table(list(rows.values()))
    assert "account_plane_only" in table
    assert f"{BANK}: all" in table
    assert f"{BANK}: liq" in table
    assert "no_bindings,no_product_view" in table


def test_the_report_names_who_may_transmit_a_return(db_session: Session) -> None:
    """The filing cutover's gate: the `filing` column is the after-picture.

    Before 2026-09-20 the answer was "every scalar admin or approver", which is
    the `role` column beside it. After, it is exactly the holders of a
    Regulatory Reporting `submit` binding — and an Approver, however complete
    the grant, is not one of them.
    """
    db_session.add(Organization(id=ORG, name="Filing impact"))
    db_session.add(
        Bank(
            id=BANK,
            organization_id=ORG,
            name="Impact Bank",
            short_name="Impact",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal_bank",
            institution_type=FALLBACK_TYPE_CODE,
        )
    )
    approver = _user(db_session, "checker@impact.example", role="approver")
    validator = _user(db_session, "filer@impact.example")
    db_session.commit()

    _grant(
        db_session,
        approver,
        RoleBundle.APPROVER,
        institution_scope=InstitutionScope.INSTITUTION,
        institution_id=BANK,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
    _grant(
        db_session,
        validator,
        RoleBundle.VALIDATOR,
        institution_scope=InstitutionScope.INSTITUTION,
        institution_id=BANK,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
    db_session.commit()

    rows = {row.email: row for row in build_report(db_session, organization_id=ORG)}
    assert rows["checker@impact.example"].filing_institutions == []
    assert rows["filer@impact.example"].filing_institutions == [BANK]
    assert BANK in render_table(list(rows.values()))
