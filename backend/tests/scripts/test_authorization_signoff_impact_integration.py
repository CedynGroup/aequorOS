"""Signer cutover inventory: affected officers, E2E identities and exact grants."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.authorization import (
    DataScope,
    GrantorType,
    InstitutionScope,
    Module,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.identity.service import authorization
from app.models import (
    AuthorizationBinding,
    Bank,
    Organization,
    PackageSignatureRecipient,
    RegulatoryPackage,
    User,
)
from app.policy.service.institution_types import FALLBACK_TYPE_CODE
from scripts.authorization_signoff_impact import (
    ImpactInputError,
    SignOffImpact,
    SignOffReport,
    build_signoff_report,
    write_artifacts,
)

ORG = "OR-SGNIMP01"
BANK = "BK-SGNIMP01"
OTHER_BANK = "BK-SGNIMP02"


def _tenant(db: Session) -> None:
    db.add(Organization(id=ORG, name="Synthetic Signer impact"))
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
    job_title: str | None = None,
) -> User:
    user = User(
        id=uuid4(),
        organization_id=ORG,
        email=email,
        display_name=email.split("@", maxsplit=1)[0],
        role=role,
        auth_provider=provider,
        job_title=job_title,
        is_active=True,
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
) -> AuthorizationBinding:
    return authorization.create_role_binding(
        db,
        organization_id=ORG,
        principal_user_id=user.id,
        principal_type=principal_type,
        role_bundle=bundle,
        scope=authorization.BindingScope(institution_scope, institution_id, module, sensitivity),
        grantor=authorization.GrantorRef(GrantorType.SYSTEM, "signoff-impact-test"),
        reason="Synthetic Signer impact fixture",
        commit=False,
    )


def _impact(
    report: SignOffReport, user: User, module: Module, *, bank_id: str = BANK
) -> SignOffImpact:
    return next(
        row
        for row in report.impacts
        if row.principal_id == str(user.id)
        and row.module == module.value
        and row.institution_id == bank_id
    )


def test_preparers_executives_and_e2e_identities_get_exact_grant_inventory(
    db_session: Session,
) -> None:
    """Legacy duties are lost without Signer; companion duties remain explicit."""
    _tenant(db_session)
    analyst = _user(db_session, "e2e.analyst@aequoros.example", role="analyst")
    approver = _user(db_session, "e2e.approver@aequoros.example", role="approver")
    board = _user(
        db_session,
        "e2e.board@aequoros.example",
        job_title="Chief Risk Officer",
    )
    machine = _user(
        db_session,
        "e2e.integration@service.aequoros.invalid",
        role="analyst",
        provider="service",
    )
    _grant(
        db_session,
        analyst,
        RoleBundle.ANALYST,
        institution_scope=InstitutionScope.ORGANIZATION,
        institution_id=None,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
    )
    _grant(
        db_session,
        approver,
        RoleBundle.APPROVER,
        institution_scope=InstitutionScope.ORGANIZATION,
        institution_id=None,
        module=ModuleScope.ALL,
        sensitivity=SensitivityScope.ALL,
    )
    _grant(
        db_session,
        board,
        RoleBundle.APPROVER,
        module=ModuleScope.CAPITAL,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    _grant(
        db_session,
        machine,
        RoleBundle.INTEGRATION_WRITER,
        module=ModuleScope.DATA,
        sensitivity=SensitivityScope.RESTRICTED,
        principal_type=PrincipalType.MACHINE,
    )
    db_session.commit()

    report = build_signoff_report(db_session, organization_id=ORG)

    assert not report.evaluation_failed
    assert {row.e2e_profile for row in report.principals if row.principal_type == "human"} == {
        "analyst",
        "approver",
        "board",
    }
    analyst_impact = _impact(report, analyst, Module.REGULATORY)
    assert analyst_impact.before.allowed
    assert analyst_impact.newly_denied
    assert analyst_impact.legacy_cohorts == [
        "icaap_preparer",
        "icaap_witness",
        "ordinary_preparer",
        "ordinary_witness",
    ]
    assert [grant.permission for grant in analyst_impact.required_grants] == ["sign_off"]
    assert analyst_impact.required_grants[0].role_bundle == RoleBundle.SIGNER.value
    assert analyst_impact.required_grants[0].institution_id == BANK

    approver_impact = _impact(report, approver, Module.REGULATORY)
    assert approver_impact.newly_denied
    assert "ordinary_approver_or_executive" in approver_impact.legacy_cohorts
    # An organization-wide Approver row does not meet the checker's exact-bank duty.
    assert {(grant.role_bundle, grant.permission) for grant in approver_impact.required_grants} == {
        (RoleBundle.SIGNER.value, "sign_off"),
        (RoleBundle.APPROVER.value, "approve"),
    }
    assert all(
        grant.institution_scope == InstitutionScope.INSTITUTION.value
        and grant.institution_id == BANK
        and grant.module_scope == ModuleScope.REGULATORY.value
        and grant.sensitivity_scope == SensitivityScope.RESTRICTED.value
        and grant.data_scope_kind == DataScope.ALL.value
        for grant in approver_impact.required_grants
    )

    board_impact = _impact(report, board, Module.REGULATORY)
    assert board_impact.newly_denied
    assert board_impact.legacy_cohorts == ["icaap_approver_or_executive", "icaap_witness"]
    assert {grant.permission for grant in board_impact.required_grants} == {"sign_off", "approve"}
    # The existing Capital/confidential source READ remains a separate sentence.
    assert not any(grant.permission == "view" for grant in board_impact.required_grants)

    stress_impact = _impact(report, approver, Module.RISK)
    assert stress_impact.newly_denied
    assert stress_impact.legacy_cohorts == ["enterprise_stress_board"]
    assert [grant.permission for grant in stress_impact.required_grants] == ["sign_off"]
    assert stress_impact.required_grants[0].module_scope == Module.RISK.value

    for module in (Module.REGULATORY, Module.RISK):
        machine_impact = _impact(report, machine, module)
        assert not machine_impact.before.allowed
        assert not machine_impact.after.allowed
        assert machine_impact.after.reason == "human_session_required"
        assert machine_impact.required_grants == []


def test_current_cycle_named_board_recipient_is_inventoried_without_legacy_authority(
    db_session: Session,
) -> None:
    """Routing exposes a Board gap even when no legacy scoped grant exists."""
    _tenant(db_session)
    board = _user(db_session, "journey.signer.board@aequoros.example", role="approver")
    package = RegulatoryPackage(
        organization_id=ORG,
        bank_id=BANK,
        return_family="icaap",
        return_code="ICAAP-SIGNER-IMPACT",
        reporting_date=date(2026, 3, 31),
        frequency="annual",
        basis="solo",
        status="generated",
        version=1,
        snapshot={"sections": []},
        source_runs=[],
        generated_by=board.id,
        generated_at=datetime.now(UTC),
    )
    db_session.add(package)
    db_session.flush()
    db_session.add(
        PackageSignatureRecipient(
            organization_id=ORG,
            package_id=package.id,
            attestation_cycle=package.attestation_cycle,
            signing_role="board",
            recipient_user_id=board.id,
            recipient_signer_id="synthetic-board-signer",
            routing_order=1,
            status="pending",
        )
    )
    db_session.commit()

    report = build_signoff_report(db_session, organization_id=ORG)
    impact = _impact(report, board, Module.REGULATORY)
    assert not impact.before.allowed
    assert impact.named_or_legacy_signer
    assert impact.named_roles == ["board"]
    assert impact.source_families == ["icaap"]
    assert {(row.role_bundle, row.module_scope) for row in impact.required_grants} == {
        (RoleBundle.SIGNER.value, Module.REGULATORY.value),
        (RoleBundle.VIEWER.value, Module.CAPITAL.value),
        (RoleBundle.APPROVER.value, Module.REGULATORY.value),
    }


def test_signoff_requires_one_effective_exact_bank_whole_book_binding(
    db_session: Session,
) -> None:
    """Organization-wide, wrong-bank and ineffective Signer rows never satisfy after."""
    _tenant(db_session)
    exact = _user(db_session, "exact@signoff.example")
    organization_wide = _user(db_session, "organization@signoff.example")
    wrong_bank = _user(db_session, "wrong-bank@signoff.example")
    expired = _user(db_session, "expired@signoff.example")
    for user in (exact, organization_wide, wrong_bank, expired):
        _grant(
            db_session,
            user,
            RoleBundle.ANALYST,
            module=ModuleScope.REGULATORY,
            sensitivity=SensitivityScope.RESTRICTED,
        )
    _grant(
        db_session,
        exact,
        RoleBundle.SIGNER,
        module=ModuleScope.REGULATORY,
        sensitivity=SensitivityScope.RESTRICTED,
    )
    _grant(
        db_session,
        organization_wide,
        RoleBundle.SIGNER,
        institution_scope=InstitutionScope.ORGANIZATION,
        institution_id=None,
        module=ModuleScope.REGULATORY,
        sensitivity=SensitivityScope.RESTRICTED,
    )
    _grant(
        db_session,
        wrong_bank,
        RoleBundle.SIGNER,
        institution_id=OTHER_BANK,
        module=ModuleScope.REGULATORY,
        sensitivity=SensitivityScope.RESTRICTED,
    )
    stale = _grant(
        db_session,
        expired,
        RoleBundle.SIGNER,
        module=ModuleScope.REGULATORY,
        sensitivity=SensitivityScope.RESTRICTED,
    )
    stale.valid_from = datetime.now(UTC) - timedelta(days=1)
    stale.valid_until = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()

    report = build_signoff_report(db_session, organization_id=ORG)

    exact_impact = _impact(report, exact, Module.REGULATORY)
    assert exact_impact.after.allowed
    assert not exact_impact.newly_denied
    assert exact_impact.required_grants == []
    for user, reason in (
        (organization_wide, "exact_institution_signer_required"),
        (wrong_bank, "no_active_exact_binding"),
        (expired, "no_active_exact_binding"),
    ):
        impact = _impact(report, user, Module.REGULATORY)
        assert impact.before.allowed
        assert not impact.after.allowed
        assert impact.after.reason == reason
        assert impact.newly_denied
        assert [grant.permission for grant in impact.required_grants] == ["sign_off"]
    # The other bank remains a separate resource, never an accidental match.
    assert not _impact(report, exact, Module.REGULATORY, bank_id=OTHER_BANK).after.allowed


def test_artifacts_are_private_non_overwriting_and_name_every_signing_route(
    db_session: Session, tmp_path: Path
) -> None:
    """The deliverable is before/after deny-impact plus exact operator grant rows."""
    _tenant(db_session)
    analyst = _user(db_session, "e2e.analyst@aequoros.example", role="analyst")
    _grant(
        db_session,
        analyst,
        RoleBundle.ANALYST,
        module=ModuleScope.REGULATORY,
        sensitivity=SensitivityScope.RESTRICTED,
    )
    db_session.commit()
    report = build_signoff_report(db_session, organization_id=ORG)
    output = tmp_path / "2026-11-07-signoff-impact"

    write_artifacts(report, output)

    assert {path.name for path in output.iterdir()} == {
        "signoff-before.json",
        "signoff-after.json",
        "signoff-exact-grants.json",
    }
    before = cast(dict[str, object], json.loads((output / "signoff-before.json").read_text()))
    after = cast(dict[str, object], json.loads((output / "signoff-after.json").read_text()))
    grants = cast(dict[str, object], json.loads((output / "signoff-exact-grants.json").read_text()))
    assert before["stage"] == "before" and after["stage"] == "after"
    assert before["generated_at"] == after["generated_at"] == grants["generated_at"]
    assert len(cast(list[object], before["routes"])) == 6
    outcomes = cast(list[dict[str, object]], after["outcomes"])
    assert any(row["newly_denied"] for row in outcomes)
    required = cast(list[dict[str, object]], grants["required_grants"])
    assert len(required) == 1
    assert required[0]["role_bundle"] == "signer"
    assert required[0]["permission"] == "sign_off"
    assert required[0]["approval_required"] is True
    assert required[0]["legacy_cohorts"] == ["ordinary_preparer", "ordinary_witness"]
    assert "@aequoros.example" not in json.dumps(grants)
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir())
    with pytest.raises(ImpactInputError, match="already exist"):
        write_artifacts(report, output)


def test_evaluator_failure_is_visible_and_never_becomes_a_grant_recommendation(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An incomplete inventory fails closed instead of inventing operator grants."""
    _tenant(db_session)
    analyst = _user(db_session, "analyst@signoff.example", role="analyst")
    _grant(
        db_session,
        analyst,
        RoleBundle.ANALYST,
        module=ModuleScope.REGULATORY,
        sensitivity=SensitivityScope.RESTRICTED,
    )
    db_session.commit()

    def fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("synthetic evaluator failure")

    monkeypatch.setattr(authorization, "evaluate_structural_permission", fail)
    report = build_signoff_report(db_session, organization_id=ORG)

    assert report.evaluation_failed
    impact = _impact(report, analyst, Module.REGULATORY)
    assert impact.before.reason == "evaluation_failed"
    assert impact.after.reason == "evaluation_failed"
    assert impact.required_grants == []
