"""Notifications/jobs cutover inventory: users losing visibility, exact grants."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

import scripts.authorization_notification_impact as notification_impact_reporter
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    Module,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    Sensitivity,
    SensitivityScope,
)
from app.identity.service import authorization
from app.models import Bank, Job, Notification, Organization, User
from app.policy.service.institution_types import FALLBACK_TYPE_CODE
from app.reporting.service.registry import REGISTRY
from scripts.authorization_notification_impact import (
    ImpactInputError,
    NotifImpactReport,
    VisibilityImpact,
    build_notification_impact_report,
    write_artifacts,
)

ORG = "OR-NOTIMP01"
BANK = "BK-NOTIMP01"
OTHER_BANK = "BK-NOTIMP02"


def _tenant(db: Session) -> None:
    db.add(Organization(id=ORG, name="Synthetic Notification impact"))
    db.add_all(
        Bank(
            id=bank_id,
            organization_id=ORG,
            name=f"Synthetic {bank_id}",
            short_name=bank_id,
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal_bank",
            institution_type=FALLBACK_TYPE_CODE,
        )
        for bank_id in (BANK, OTHER_BANK)
    )
    db.flush()


def _user(
    db: Session,
    email: str,
    *,
    role: str = "viewer",
    provider: str = "password",
    is_active: bool = True,
) -> User:
    user = User(
        id=uuid4(),
        organization_id=ORG,
        email=email,
        display_name=email.split("@", maxsplit=1)[0],
        role=role,
        auth_provider=provider,
        is_active=is_active,
    )
    db.add(user)
    db.flush()
    return user


def _grant(  # noqa: PLR0913 - one indivisible binding sentence
    db: Session,
    user: User,
    bundle: RoleBundle,
    *,
    institution_scope: InstitutionScope = InstitutionScope.INSTITUTION,
    institution_id: str | None = BANK,
    module: ModuleScope,
    sensitivity: SensitivityScope,
    principal_type: PrincipalType = PrincipalType.HUMAN,
) -> object:
    return authorization.create_role_binding(
        db,
        organization_id=ORG,
        principal_user_id=user.id,
        principal_type=principal_type,
        role_bundle=bundle,
        scope=authorization.BindingScope(institution_scope, institution_id, module, sensitivity),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "notification-impact-test"),
        reason="Synthetic notification impact fixture",
        commit=False,
    )


def _notification(  # noqa: PLR0913 - one fixture notification with its full locator
    db: Session,
    *,
    scope: str | None,
    bank: str | None,
    module: str | None,
    sensitivity: str | None,
    recipient: User | None = None,
    type: str = "synthetic.event",  # noqa: A002 - mirrors the column name
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> None:
    db.add(
        Notification(
            organization_id=ORG,
            recipient_user_id=None if recipient is None else recipient.id,
            type=type,
            severity="info",
            title="Synthetic notification",
            body="Synthetic body",
            entity_type=entity_type,
            entity_id=entity_id,
            institution_scope=scope,
            bank_id=bank,
            module=module,
            sensitivity=sensitivity,
        )
    )
    db.flush()


def _job(  # noqa: PLR0913 - one fixture job with its full locator and provenance
    db: Session,
    *,
    scope: str | None,
    bank: str | None,
    module: str | None,
    sensitivity: str | None,
    principal: str | None,
    job_type: str = "synthetic",
    payload: dict[str, object] | None = None,
) -> None:
    db.add(
        Job(
            organization_id=ORG,
            job_type=job_type,
            status="queued",
            payload=payload or {},
            institution_scope=scope,
            bank_id=bank,
            module=module,
            sensitivity=sensitivity,
            enqueued_by_principal=principal,
            enqueued_action="synthetic.action",
        )
    )
    db.flush()


def _impact(  # noqa: PLR0913 - one row is selected by its complete locator
    report: NotifImpactReport,
    user: User,
    surface: str,
    *,
    scope: str | None,
    bank: str | None,
    module: str | None,
    sensitivity: str | None,
) -> VisibilityImpact:
    return next(
        row
        for row in report.impacts
        if row.principal_id == str(user.id)
        and row.surface == surface
        and row.institution_scope == scope
        and row.bank_id == bank
        and row.module == module
        and row.sensitivity == sensitivity
    )


def test_view_authority_decides_notification_and_job_visibility(
    db_session: Session,
) -> None:
    """Tenants saw every row before; only current view grants keep rows visible."""
    _tenant(db_session)
    bare = _user(db_session, "bare@notification-impact.example")
    org_viewer = _user(db_session, "org@notification-impact.example")
    bank_viewer = _user(db_session, "bank@notification-impact.example")
    _grant(
        db_session,
        org_viewer,
        RoleBundle.VIEWER,
        institution_scope=InstitutionScope.ORGANIZATION,
        institution_id=None,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
    )
    _grant(
        db_session,
        bank_viewer,
        RoleBundle.VIEWER,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
    )
    # One org-wide notification, one bank-scoped notification, one job.
    _notification(
        db_session,
        scope=InstitutionScope.ORGANIZATION.value,
        bank=None,
        module=Module.REGULATORY.value,
        sensitivity=Sensitivity.RESTRICTED.value,
    )
    _notification(
        db_session,
        scope=InstitutionScope.INSTITUTION.value,
        bank=BANK,
        module=Module.DATA.value,
        sensitivity=Sensitivity.CONFIDENTIAL.value,
    )
    _job(
        db_session,
        scope=InstitutionScope.INSTITUTION.value,
        bank=BANK,
        module=Module.LIQUIDITY.value,
        sensitivity=Sensitivity.RESTRICTED.value,
        principal=f"human:{uuid4()}",
    )
    # A worker-provenance job shares the bank surface but never becomes visible.
    _job(
        db_session,
        scope=InstitutionScope.INSTITUTION.value,
        bank=BANK,
        module=Module.DATA.value,
        sensitivity=Sensitivity.CONFIDENTIAL.value,
        principal="worker:scheduler",
    )
    db_session.commit()

    report = build_notification_impact_report(db_session, organization_id=ORG)

    assert not report.evaluation_failed
    org_row = _impact(
        report,
        bare,
        "notifications",
        scope=InstitutionScope.ORGANIZATION.value,
        bank=None,
        module=Module.REGULATORY.value,
        sensitivity=Sensitivity.RESTRICTED.value,
    )
    assert org_row.before_count == 1
    assert org_row.after_count == 0
    assert org_row.newly_hidden
    assert [
        (grant.role_bundle, grant.permission, grant.institution_scope, grant.institution_id)
        for grant in org_row.required_grants
    ] == [(RoleBundle.VIEWER.value, "view", InstitutionScope.ORGANIZATION.value, None)]

    bank_row = _impact(
        report,
        bare,
        "notifications",
        scope=InstitutionScope.INSTITUTION.value,
        bank=BANK,
        module=Module.DATA.value,
        sensitivity=Sensitivity.CONFIDENTIAL.value,
    )
    assert bank_row.newly_hidden
    assert [
        (grant.module_scope, grant.sensitivity_scope, grant.institution_id)
        for grant in bank_row.required_grants
    ] == [(Module.DATA.value, Sensitivity.CONFIDENTIAL.value, BANK)]

    job_row = _impact(
        report,
        bare,
        "jobs",
        scope=InstitutionScope.INSTITUTION.value,
        bank=BANK,
        module=Module.LIQUIDITY.value,
        sensitivity=Sensitivity.RESTRICTED.value,
    )
    assert job_row.before_count == 1
    assert job_row.after_count == 0
    assert job_row.newly_hidden
    assert [(grant.module_scope, grant.institution_id) for grant in job_row.required_grants] == [
        (Module.LIQUIDITY.value, BANK)
    ]

    # The organization-wide viewer keeps every grantable row visible; only the
    # worker-provenance job stays hidden from every principal.
    org_viewer_impacts = [row for row in report.impacts if row.principal_id == str(org_viewer.id)]
    for row in org_viewer_impacts:
        assert row.after_count == row.before_count or row.module == Module.DATA.value
    assert all(
        row.after_count == 0
        for row in org_viewer_impacts
        if row.surface == "jobs" and row.module == Module.DATA.value
    )
    assert all(row.required_grants == [] for row in org_viewer_impacts)

    # An exact-bank viewer keeps the bank rows but loses the organization-wide one;
    # a grant cannot restore the worker-provenance job, so no candidate is offered.
    bank_org_row = _impact(
        report,
        bank_viewer,
        "notifications",
        scope=InstitutionScope.ORGANIZATION.value,
        bank=None,
        module=Module.REGULATORY.value,
        sensitivity=Sensitivity.RESTRICTED.value,
    )
    assert bank_org_row.newly_hidden
    assert bank_org_row.required_grants[0].institution_scope == InstitutionScope.ORGANIZATION.value
    bank_job_row = _impact(
        report,
        bank_viewer,
        "jobs",
        scope=InstitutionScope.INSTITUTION.value,
        bank=BANK,
        module=Module.LIQUIDITY.value,
        sensitivity=Sensitivity.RESTRICTED.value,
    )
    assert not bank_job_row.newly_hidden
    bank_worker_row = _impact(
        report,
        bank_viewer,
        "jobs",
        scope=InstitutionScope.INSTITUTION.value,
        bank=BANK,
        module=Module.DATA.value,
        sensitivity=Sensitivity.CONFIDENTIAL.value,
    )
    assert bank_worker_row.before_count == 1
    assert bank_worker_row.after_count == 0
    assert bank_worker_row.newly_hidden
    assert bank_worker_row.required_grants == []


def test_family_hidden_filing_notifications_are_never_newly_hidden(
    db_session: Session,
) -> None:
    """A return-family-hidden row counts as hidden before AND after the cutover.

    An SDI licence class has no ICAAP surface (C-27): the live API hides an
    ICAAP-family filing notification from every principal of that bank,
    whatever the locator-view filter would admit. The report must apply the
    same family factor to its before and after counts — no deny impact, and
    no grant candidate, because no grant restores family authority.
    """
    _tenant(db_session)
    sdi_bank = "BK-NOTIMP03"
    db_session.add(
        Bank(
            id=sdi_bank,
            organization_id=ORG,
            name="Synthetic SDI bank",
            short_name=sdi_bank,
            currency="GHS",
            jurisdiction_code="GH",
            license_type="savings_and_loans",
            institution_type="savings_and_loans",
        )
    )
    sdi_viewer = _user(db_session, "sdi-reg@notification-impact.example")
    _grant(
        db_session,
        sdi_viewer,
        RoleBundle.VIEWER,
        institution_id=sdi_bank,
        module=ModuleScope.REGULATORY,
        sensitivity=SensitivityScope.RESTRICTED,
    )
    bare = _user(db_session, "sdi-bare@notification-impact.example")
    code = next(code for code, definition in REGISTRY.items() if definition.family == "icaap")
    _notification(
        db_session,
        scope=InstitutionScope.INSTITUTION.value,
        bank=sdi_bank,
        module=Module.REGULATORY.value,
        sensitivity=Sensitivity.RESTRICTED.value,
        type=f"reporting.deadline.due_soon_7:{code}:2026-03-31",
        entity_type="bank",
        entity_id=sdi_bank,
    )
    db_session.commit()

    report = build_notification_impact_report(db_session, organization_id=ORG)

    assert not report.evaluation_failed
    for user in (sdi_viewer, bare):
        row = _impact(
            report,
            user,
            "notifications",
            scope=InstitutionScope.INSTITUTION.value,
            bank=sdi_bank,
            module=Module.REGULATORY.value,
            sensitivity=Sensitivity.RESTRICTED.value,
        )
        assert row.before_count == 0, "the family factor hid this row before the cutover"
        assert row.after_count == 0
        assert not row.newly_hidden
        assert row.required_grants == [], "no grant restores return-family authority"


def test_family_visible_locator_hidden_rows_get_restoring_candidates(
    db_session: Session,
) -> None:
    """A visible return's notification is a true deny impact a grant can restore.

    The requester holds whole-institution REGULATORY/restricted view, so the
    return family is visible and the row was genuinely seen before the
    cutover; the persisted locator names a module no grant covers, so the
    locator-view filter refuses it after. The least-scope candidate for that
    locator must be recommended, and simulating it must restore the row.
    """
    _tenant(db_session)
    reg_viewer = _user(db_session, "reg@notification-impact.example")
    _grant(
        db_session,
        reg_viewer,
        RoleBundle.VIEWER,
        module=ModuleScope.REGULATORY,
        sensitivity=SensitivityScope.RESTRICTED,
    )
    code = next(code for code, definition in REGISTRY.items() if definition.family == "bsd")
    _notification(
        db_session,
        scope=InstitutionScope.INSTITUTION.value,
        bank=BANK,
        module=Module.LIQUIDITY.value,
        sensitivity=Sensitivity.RESTRICTED.value,
        type=f"reporting.deadline.due_soon_7:{code}:2026-03-31",
        entity_type="bank",
        entity_id=BANK,
    )
    db_session.commit()

    report = build_notification_impact_report(db_session, organization_id=ORG)

    row = _impact(
        report,
        reg_viewer,
        "notifications",
        scope=InstitutionScope.INSTITUTION.value,
        bank=BANK,
        module=Module.LIQUIDITY.value,
        sensitivity=Sensitivity.RESTRICTED.value,
    )
    assert row.before_count == 1
    assert row.after_count == 0
    assert row.newly_hidden
    assert [
        (
            grant.role_bundle,
            grant.permission,
            grant.institution_scope,
            grant.institution_id,
            grant.module_scope,
            grant.sensitivity_scope,
        )
        for grant in row.required_grants
    ] == [
        (
            RoleBundle.VIEWER.value,
            "view",
            InstitutionScope.INSTITUTION.value,
            BANK,
            ModuleScope.LIQUIDITY.value,
            SensitivityScope.RESTRICTED.value,
        )
    ]


def test_recipient_scoped_rows_impact_only_their_recipient(
    db_session: Session,
) -> None:
    """A recipient-targeted notification is one user's visibility loss, not the org's."""
    _tenant(db_session)
    recipient = _user(db_session, "recipient@notification-impact.example")
    bystander = _user(db_session, "bystander@notification-impact.example")
    _notification(
        db_session,
        scope=InstitutionScope.INSTITUTION.value,
        bank=OTHER_BANK,
        module=Module.RISK.value,
        sensitivity=Sensitivity.RESTRICTED.value,
        recipient=recipient,
    )
    db_session.commit()

    report = build_notification_impact_report(db_session, organization_id=ORG)

    recipient_row = _impact(
        report,
        recipient,
        "notifications",
        scope=InstitutionScope.INSTITUTION.value,
        bank=OTHER_BANK,
        module=Module.RISK.value,
        sensitivity=Sensitivity.RESTRICTED.value,
    )
    assert recipient_row.before_count == 1
    assert recipient_row.after_count == 0
    assert recipient_row.newly_hidden
    assert [
        (grant.institution_scope, grant.institution_id, grant.module_scope)
        for grant in recipient_row.required_grants
    ] == [(InstitutionScope.INSTITUTION.value, OTHER_BANK, Module.RISK.value)]

    bystander_row = _impact(
        report,
        bystander,
        "notifications",
        scope=InstitutionScope.INSTITUTION.value,
        bank=OTHER_BANK,
        module=Module.RISK.value,
        sensitivity=Sensitivity.RESTRICTED.value,
    )
    assert bystander_row.before_count == 0
    assert bystander_row.after_count == 0
    assert not bystander_row.newly_hidden
    assert bystander_row.required_grants == []


def test_legacy_and_machine_rows_never_receive_grant_candidates(
    db_session: Session,
) -> None:
    """Locator-less rows and machines hide without any grant able to restore them."""
    _tenant(db_session)
    bare = _user(db_session, "legacy@notification-impact.example")
    machine = _user(
        db_session,
        "e2e.integration@service.aequoros.invalid",
        role="viewer",
        provider="service",
    )
    inactive = _user(db_session, "inactive@notification-impact.example", is_active=False)
    _notification(db_session, scope=None, bank=None, module=None, sensitivity=None)
    _job(db_session, scope=None, bank=None, module=None, sensitivity=None, principal=None)
    db_session.commit()

    report = build_notification_impact_report(db_session, organization_id=ORG)

    legacy_notification = _impact(
        report,
        bare,
        "notifications",
        scope=None,
        bank=None,
        module=None,
        sensitivity=None,
    )
    assert legacy_notification.before_count == 1
    assert legacy_notification.after_count == 0
    assert legacy_notification.newly_hidden
    assert legacy_notification.required_grants == []

    legacy_job = _impact(report, bare, "jobs", scope=None, bank=None, module=None, sensitivity=None)
    assert legacy_job.before_count == 1
    assert legacy_job.after_count == 0
    assert legacy_job.newly_hidden
    assert legacy_job.required_grants == []

    machine_row = _impact(
        report,
        machine,
        "notifications",
        scope=None,
        bank=None,
        module=None,
        sensitivity=None,
    )
    assert machine_row.newly_hidden
    assert machine_row.required_grants == []
    inactive_row = _impact(
        report,
        inactive,
        "notifications",
        scope=None,
        bank=None,
        module=None,
        sensitivity=None,
    )
    assert inactive_row.newly_hidden
    assert inactive_row.required_grants == []


def test_a_private_bi_question_impacts_only_its_requester(db_session: Session) -> None:
    """The report reproduces the API's requester visibility, per principal.

    A queued BI question is one reader's: the report must count it visible
    for the requester alone, never recommend a grant to a colleague whose
    simulated recount still could not restore another reader's question
    (the composition root's registration is what the standalone script must
    reproduce — an unregistered predicate overcounts, and a registered one
    without the assessed user's id hides even the requester's own rows).
    """
    _tenant(db_session)
    requester = _user(db_session, "requester@notification-impact.example")
    colleague = _user(db_session, "colleague@notification-impact.example")
    for user in (requester, colleague):
        _grant(
            db_session,
            user,
            RoleBundle.VIEWER,
            institution_scope=InstitutionScope.INSTITUTION,
            institution_id=BANK,
            module=ModuleScope.DATA,
            sensitivity=SensitivityScope.AGGREGATED,
        )
    _job(
        db_session,
        scope=InstitutionScope.INSTITUTION.value,
        bank=BANK,
        module=Module.DATA.value,
        sensitivity=Sensitivity.AGGREGATED.value,
        principal=f"human:{requester.id}",
        job_type="bi_nlq_translate",
        payload={"principal_user_id": str(requester.id)},
    )
    db_session.commit()

    report = build_notification_impact_report(db_session, organization_id=ORG)

    requester_row = _impact(
        report,
        requester,
        "jobs",
        scope=InstitutionScope.INSTITUTION.value,
        bank=BANK,
        module=Module.DATA.value,
        sensitivity=Sensitivity.AGGREGATED.value,
    )
    assert requester_row.after_count == 1
    assert not requester_row.newly_hidden
    assert requester_row.required_grants == []

    colleague_row = _impact(
        report,
        colleague,
        "jobs",
        scope=InstitutionScope.INSTITUTION.value,
        bank=BANK,
        module=Module.DATA.value,
        sensitivity=Sensitivity.AGGREGATED.value,
    )
    assert colleague_row.after_count == 0
    # A grant cannot make a colleague's question the colleague's own, so the
    # recommendation engine must stay silent rather than promise restoration.
    assert colleague_row.required_grants == []


def test_artifacts_are_private_non_overwriting_and_name_every_tenant_surface(
    db_session: Session, tmp_path: Path
) -> None:
    """The deliverable is before/after deny-impact plus exact operator grant rows."""
    _tenant(db_session)
    bare = _user(db_session, "e2e.analyst@aequoros.example")
    assert bare.is_active
    _notification(
        db_session,
        scope=InstitutionScope.ORGANIZATION.value,
        bank=None,
        module=Module.REGULATORY.value,
        sensitivity=Sensitivity.RESTRICTED.value,
    )
    db_session.commit()
    report = build_notification_impact_report(db_session, organization_id=ORG)
    output = tmp_path / "2026-11-08-notification-impact"

    write_artifacts(report, output)

    assert {path.name for path in output.iterdir()} == {
        "notif-before.json",
        "notif-after.json",
        "notif-exact-grants.json",
    }
    before = cast("dict[str, object]", json.loads((output / "notif-before.json").read_text()))
    after = cast("dict[str, object]", json.loads((output / "notif-after.json").read_text()))
    grants = cast("dict[str, object]", json.loads((output / "notif-exact-grants.json").read_text()))
    assert before["stage"] == "before" and after["stage"] == "after"
    assert before["generated_at"] == after["generated_at"] == grants["generated_at"]
    routes = [
        f'{row["method"]} {row["path"]}'
        for row in cast("list[dict[str, str]]", before["routes"])
    ]
    assert routes == [
        "GET /api/v1/notifications",
        "POST /api/v1/notifications/{notification_id}/read",
        "POST /api/v1/notifications/read-all",
        "GET /api/v1/jobs",
        "GET /api/v1/jobs/{job_id}",
    ]
    outcomes = cast("list[dict[str, object]]", after["outcomes"])
    assert any(row["newly_hidden"] for row in outcomes)
    required = cast("list[dict[str, object]]", grants["required_grants"])
    assert len(required) >= 1
    assert all(row["role_bundle"] == "viewer" and row["permission"] == "view" for row in required)
    assert all(row["approval_required"] is True for row in required)
    assert "@aequoros.example" not in json.dumps(grants)
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir())
    with pytest.raises(ImpactInputError, match="already exist"):
        write_artifacts(report, output)


def test_evaluation_failure_is_visible_and_never_becomes_a_grant_recommendation(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An incomplete inventory fails closed instead of inventing operator grants."""
    _tenant(db_session)
    bare = _user(db_session, "failed@notification-impact.example")
    _notification(
        db_session,
        scope=InstitutionScope.ORGANIZATION.value,
        bank=None,
        module=Module.REGULATORY.value,
        sensitivity=Sensitivity.RESTRICTED.value,
    )
    db_session.commit()

    def fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("synthetic evaluation failure")

    monkeypatch.setattr(notification_impact_reporter, "load_permission_grants", fail)
    report = build_notification_impact_report(db_session, organization_id=ORG)

    assert report.evaluation_failed
    impact = _impact(
        report,
        bare,
        "notifications",
        scope=InstitutionScope.ORGANIZATION.value,
        bank=None,
        module=Module.REGULATORY.value,
        sensitivity=Sensitivity.RESTRICTED.value,
    )
    assert impact.assessment_error == "evaluation_failed"
    assert impact.required_grants == []
