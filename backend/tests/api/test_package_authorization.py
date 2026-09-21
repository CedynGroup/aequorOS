"""Who may reach a regulatory package, per return FAMILY (audit C-4 / D-012).

Two answers live side by side and must both stay true:

* an UNGATED family (every BSD, liquidity, capital, FX return) keeps the scalar
  ladder it has always had. A change that quietly tightened those routes would
  lock analysts out of returns they have filed for months, so the control rows
  here are as important as the ICAAP ones;
* the ICAAP family is GATED on an exact CAPITAL/CONFIDENTIAL binding for the
  institution. A principal without it gets **404** — the existence of an ICAAP
  package for a date is itself a disclosure — and a principal with VIEW but
  without the action permission gets an honest 403.

The third property is the one that is easiest to lose: a scalar role must never
satisfy a scoped surface. A scalar ``approver`` with no binding is refused.

TRANSMISSION is the fourth, and it cuts across both answers. Filing a return to
the regulator requires ``Permission.SUBMIT`` over Regulatory Reporting /
restricted for the exact institution, for EVERY family — it used to require the
same ``APPROVE`` permission as the approval decision, and on an ungated family
the scalar ``approver`` role alone (``docs/filing_workflow_redesign.md`` §1
finding 3). Those tests live in their own section at the bottom.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.session import get_sessionmaker
from app.models import AuthorizationBinding, Bank, RegulatoryPackage, User
from app.services import authorization
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

OTHER_ORG_BANK_ID = "BK-PKOTH001"
REPORTING_DATE = date(2026, 3, 31)


@pytest.fixture(autouse=True)
def _seed(db_client: TestClient) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        materialize_canonical_test_book(session)
        session.commit()
    finally:
        session.close()


def _grant(  # noqa: PLR0913 - every binding dimension is an enforcement input
    *,
    role_bundle: RoleBundle = RoleBundle.ANALYST,
    module_scope: ModuleScope = ModuleScope.CAPITAL,
    sensitivity_scope: SensitivityScope = SensitivityScope.CONFIDENTIAL,
    institution_id: str | None = SAMPLE_BANK_ID,
    organization_id: str = ORG_1,
    user_id: UUID = USER_1,
) -> int:
    session = get_sessionmaker()()
    session.info["organization_id"] = organization_id
    try:
        authorization.create_role_binding(
            session,
            organization_id=organization_id,
            principal_user_id=user_id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=role_bundle,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION if institution_id else InstitutionScope.ORGANIZATION,
                institution_id,
                module_scope,
                sensitivity_scope,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="Exercise package-plane scoped enforcement.",
        )
        user = session.get(User, user_id)
        assert user is not None
        session.refresh(user)
        return user.authorization_version
    finally:
        session.close()


def _package(  # noqa: PLR0913 - one package row, spelled out
    *,
    organization_id: str = ORG_1,
    bank_id: str = SAMPLE_BANK_ID,
    family: str = "icaap",
    return_code: str = "ICAAP-REPORT",
    generated_by: UUID | None = None,
    status: str = "generated",
    is_rehearsal: bool = False,
) -> UUID:
    session = get_sessionmaker()()
    session.info["organization_id"] = organization_id
    try:
        row = RegulatoryPackage(
            organization_id=organization_id,
            bank_id=bank_id,
            return_family=family,
            return_code=return_code,
            reporting_date=REPORTING_DATE,
            frequency="annual",
            basis="solo",
            status=status,
            is_rehearsal=is_rehearsal,
            version=1,
            snapshot={"sections": []},
            source_runs=[],
            generated_by=generated_by or uuid4(),
            generated_at=datetime.now(UTC),
        )
        session.add(row)
        session.commit()
        return row.id
    finally:
        session.close()


def _add_bank(bank_id: str, *, organization_id: str) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = organization_id
    try:
        session.add(
            Bank(
                id=bank_id,
                organization_id=organization_id,
                name=f"Fixture {bank_id}",
                short_name="Fixture",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
        session.commit()
    finally:
        session.close()


def _read_routes(package_id: UUID, bank_id: str = SAMPLE_BANK_ID) -> list[str]:
    base = f"/api/v1/banks/{bank_id}/regulatory-packages/{package_id}"
    return [
        base,
        f"{base}/artifact-versions",
        f"{base}/version-chain",
        f"{base}/artifacts",
        f"{base}/submission-events",
        f"{base}/resubmission-requests",
        f"{base}/attachments",
    ]


_MUTATIONS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "validate", None),
    ("POST", "request-approval", {"reason": "please approve"}),
    ("POST", "request-resubmission", {"reason": "correction needed"}),
]


# --- the gated family -------------------------------------------------------


def test_without_a_capital_binding_an_icaap_package_does_not_exist(
    db_client: TestClient,
) -> None:
    """404, not 403. That a bank has an ICAAP for a date is itself disclosure."""
    package_id = _package()
    for url in _read_routes(package_id):
        response = db_client.get(url, headers=headers(roles=("analyst",)))
        assert response.status_code == 404, url


def test_a_scalar_approver_with_no_binding_is_refused(db_client: TestClient) -> None:
    """A scalar role must never satisfy a scoped surface."""
    package_id = _package()
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}/validate",
        headers=headers(roles=("admin", "approver", "analyst")),
    )
    assert response.status_code == 404


def test_a_capital_confidential_binding_opens_the_reads(db_client: TestClient) -> None:
    package_id = _package()
    authv = _grant(role_bundle=RoleBundle.ANALYST)
    for url in _read_routes(package_id):
        response = db_client.get(
            url, headers=headers(roles=("viewer",), authorization_version=authv)
        )
        assert response.status_code == 200, (url, response.text)


def test_a_viewer_binding_may_read_but_not_validate(db_client: TestClient) -> None:
    """VIEW held, action permission missing: an honest 403, not a hiding 404."""
    package_id = _package()
    authv = _grant(role_bundle=RoleBundle.VIEWER)
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}"
    assert (
        db_client.get(
            base, headers=headers(roles=("viewer",), authorization_version=authv)
        ).status_code
        == 200
    )
    response = db_client.post(
        f"{base}/validate", headers=headers(roles=("analyst",), authorization_version=authv)
    )
    assert response.status_code == 403


def test_a_binding_for_another_module_does_not_open_icaap(db_client: TestClient) -> None:
    package_id = _package()
    authv = _grant(module_scope=ModuleScope.LIQUIDITY)
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}",
        headers=headers(roles=("analyst",), authorization_version=authv),
    )
    assert response.status_code == 404


def test_a_binding_for_a_lower_sensitivity_does_not_open_icaap(
    db_client: TestClient,
) -> None:
    package_id = _package()
    authv = _grant(sensitivity_scope=SensitivityScope.AGGREGATED)
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}",
        headers=headers(roles=("analyst",), authorization_version=authv),
    )
    assert response.status_code == 404


def test_a_package_in_another_tenant_is_not_found(db_client: TestClient) -> None:
    """Cross-tenant: the binding is for OUR bank, the package is not."""
    _add_bank(OTHER_ORG_BANK_ID, organization_id=ORG_2)
    foreign = _package(organization_id=ORG_2, bank_id=OTHER_ORG_BANK_ID)
    authv = _grant(role_bundle=RoleBundle.ANALYST)
    for url in _read_routes(foreign, bank_id=OTHER_ORG_BANK_ID):
        assert (
            db_client.get(
                url, headers=headers(roles=("analyst",), authorization_version=authv)
            ).status_code
            == 404
        )
    # ...and naming our own bank with their package id does not reach it either.
    for url in _read_routes(foreign):
        assert (
            db_client.get(
                url, headers=headers(roles=("analyst",), authorization_version=authv)
            ).status_code
            == 404
        )


def test_an_icaap_package_is_hidden_from_the_package_list(db_client: TestClient) -> None:
    _package()
    _package(family="liquidity", return_code="LCR-NSFR")
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages",
        headers=headers(roles=("analyst",)),
    )
    assert response.status_code == 200
    families = {row["return_family"] for row in response.json()["packages"]}
    assert "icaap" not in families
    assert "liquidity" in families


def test_the_list_shows_icaap_to_a_binding_holder(db_client: TestClient) -> None:
    _package()
    authv = _grant(role_bundle=RoleBundle.ANALYST)
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages",
        headers=headers(roles=("viewer",), authorization_version=authv),
    )
    assert response.status_code == 200
    assert "icaap" in {row["return_family"] for row in response.json()["packages"]}


# --- the ungated families are untouched ------------------------------------


def test_an_ordinary_return_keeps_the_scalar_ladder(db_client: TestClient) -> None:
    """The control rows. A viewer reads; a viewer does not validate."""
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}"
    assert db_client.get(base, headers=headers(roles=("viewer",))).status_code == 200
    assert db_client.post(f"{base}/validate", headers=headers(roles=("viewer",))).status_code == 403
    assert (
        db_client.post(f"{base}/validate", headers=headers(roles=("analyst",))).status_code == 200
    )


def test_a_viewer_hitting_an_unknown_package_still_gets_the_write_refusal(
    db_client: TestClient,
) -> None:
    """403 before 404: a viewer must not learn from a 404 that they got past
    the write gate. This is today's behaviour and it is deliberately kept."""
    unknown = uuid4()
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{unknown}/validate",
        headers=headers(roles=("viewer",)),
    )
    assert response.status_code == 403
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{unknown}/validate",
        headers=headers(roles=("analyst",)),
    )
    assert response.status_code == 404


# --- the examiner branch ----------------------------------------------------


def _impersonation_headers(org_id: str = ORG_1) -> dict[str, str]:
    """An act-as-examiner session, minted exactly as the operator plane does."""
    import datetime as dt  # noqa: PLC0415

    from app.core import security  # noqa: PLC0415
    from app.core.config import get_settings  # noqa: PLC0415
    from app.db.base import utc_now  # noqa: PLC0415

    secret = get_settings().auth.impersonation_jwt_secret
    assert secret, "the impersonation secret must be configured for this suite"
    now = utc_now()
    token = security.mint_impersonation_token(
        organization_id=org_id,
        act_operator="ops@aequoros.com",
        session_id="11111111-2222-4333-8444-555555555555",
        secret=secret,
        issued_at=now,
        expires_at=now + dt.timedelta(minutes=15),
    )
    return {"Authorization": f"Bearer {token}"}


def test_a_supervisor_may_read_a_filed_icaap_package(db_client: TestClient) -> None:
    """Packages exist only post-freeze, so everything served is sealed (C-11).

    The examiner branch reads NO binding: a supervisor is not a tenant
    principal and never holds one. It is bounded structurally instead — by
    impersonation being read-only everywhere.
    """
    package_id = _package()
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}",
        headers=_impersonation_headers(),
    )
    assert response.status_code == 200
    assert response.json()["return_family"] == "icaap"


def test_a_supervisor_may_not_mutate_an_icaap_package(db_client: TestClient) -> None:
    package_id = _package()
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}/validate",
        headers=_impersonation_headers(),
    )
    assert response.status_code == 403


def test_a_supervisor_sees_icaap_in_the_package_list(db_client: TestClient) -> None:
    _package()
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages",
        headers=_impersonation_headers(),
    )
    assert response.status_code == 200
    assert "icaap" in {row["return_family"] for row in response.json()["packages"]}


# --- transmission to the regulator is its own authority ---------------------
#
# Until 2026-09-20 ``require_package_submit`` required ``Permission.APPROVE`` —
# the same permission as ``require_package_approve`` — so whoever could approve
# a return could also file it to the Bank of Ghana, and on an ungated family the
# scalar ``approver`` role was enough on its own. The bank's process has a
# separate Validator who is the only officer that transmits.
#
# The positive cases assert that AUTHORIZATION opened, not that a channel ran:
# past the gate the request meets the lifecycle, which refuses a `generated`
# package with 409. A 403 would mean the gate is still shut, a 404 that the
# package is hidden — both are the failures these tests exist to catch.


def _submit(  # noqa: PLR0913 - the complete request is explicit
    client: TestClient,
    package_id: UUID,
    *,
    request_headers: dict[str, str],
    bank_id: str = SAMPLE_BANK_ID,
    channel: str | None = None,
):  # noqa: ANN202 - concise route-test helper
    body: dict[str, Any] = {} if channel is None else {"channel": channel}
    return client.post(
        f"/api/v1/banks/{bank_id}/regulatory-packages/{package_id}/submit",
        headers=request_headers,
        json=body,
    )


def _filing_grant(user_id: UUID = USER_1) -> int:
    """The Validator sentence: REG / restricted / this institution."""
    return _grant(
        role_bundle=RoleBundle.VALIDATOR,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
        user_id=user_id,
    )


def test_approve_authority_is_not_transmission_authority(db_client: TestClient) -> None:
    """The defect, inverted into an assertion.

    A complete Approver binding over the filing scope — the strongest approval
    authority there is for this return — does not file it. The refusal has to
    say what is missing, because "403" alone sends an officer to the wrong
    person.
    """
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    authv = _grant(
        role_bundle=RoleBundle.APPROVER,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
    response = _submit(
        db_client,
        package_id,
        request_headers=headers(roles=("approver",), authorization_version=authv),
    )
    assert response.status_code == 403
    message = response.json()["error"]["message"]
    assert "Validator" in message
    assert "submit" in message
    assert "Approval authority is not transmission authority." in message


def test_a_scalar_approver_with_no_binding_cannot_transmit(db_client: TestClient) -> None:
    """A scalar role must never satisfy a scoped surface — filing included.

    This is the case that was live: an ungated BSD/liquidity return, an officer
    holding the scalar ``approver`` (or ``admin``) role and no binding at all,
    and the regulator one POST away.
    """
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    response = _submit(
        db_client, package_id, request_headers=headers(roles=("admin", "approver", "analyst"))
    )
    assert response.status_code == 403
    assert "Validator" in response.json()["error"]["message"]


def test_a_validator_binding_transmits(db_client: TestClient) -> None:
    """The positive case: the gate opens for SUBMIT and only for SUBMIT."""
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    authv = _filing_grant()
    response = _submit(
        db_client,
        package_id,
        request_headers=headers(roles=("viewer",), authorization_version=authv),
    )
    # Past authorization; the lifecycle then refuses a `generated` package.
    assert response.status_code == 409, response.text
    assert "approved" in response.text or "generated" in response.text


def test_the_officer_who_generated_the_return_cannot_file_it(db_client: TestClient) -> None:
    """Four eyes on the regulator's copy, on an ungated family too.

    ``Permission.SUBMIT`` declares the maker/checker condition required, so this
    refusal comes from the evaluator's condition veto rather than from a missing
    binding — the holder's Validator grant is complete.
    """
    package_id = _package(family="liquidity", return_code="LCR-NSFR", generated_by=USER_1)
    authv = _filing_grant()
    response = _submit(
        db_client,
        package_id,
        request_headers=headers(roles=("viewer",), authorization_version=authv),
    )
    assert response.status_code == 403
    assert "Validator" in response.json()["error"]["message"]


def test_a_gated_family_still_hides_before_it_refuses(db_client: TestClient) -> None:
    """Transmission authority does not disclose that an ICAAP exists.

    VIEW is decided first and unchanged: a Validator with no Capital binding is
    told the package is not there, exactly as any other principal without it.
    """
    package_id = _package()
    authv = _filing_grant()
    response = _submit(
        db_client,
        package_id,
        request_headers=headers(roles=("viewer",), authorization_version=authv),
    )
    assert response.status_code == 404


def test_a_rehearsal_is_unfilable_even_with_transmission_authority(
    db_client: TestClient,
) -> None:
    """D-068: a dry run never reaches a regulator, whoever holds the authority.

    Two grants, because they answer two questions — Capital/confidential VIEW to
    know the ICAAP exists, REG/restricted SUBMIT to file it — and the refusal is
    still the rehearsal's, from the service, past both.
    """
    package_id = _package(is_rehearsal=True, status="approved")
    _grant(role_bundle=RoleBundle.VIEWER)
    authv = _filing_grant()
    response = _submit(
        db_client,
        package_id,
        request_headers=headers(roles=("viewer",), authorization_version=authv),
        channel="orass_sandbox",
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["error_code"] == "rehearsal_channel_not_permitted"


def test_an_impersonated_examiner_cannot_transmit(db_client: TestClient) -> None:
    """The examiner branch reads; it never files."""
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    response = _submit(db_client, package_id, request_headers=_impersonation_headers())
    assert response.status_code == 403


def test_transmission_does_not_cross_a_tenant_boundary(db_client: TestClient) -> None:
    """A complete Validator grant for OUR bank never reaches a neighbour's."""
    _add_bank(OTHER_ORG_BANK_ID, organization_id=ORG_2)
    foreign = _package(
        organization_id=ORG_2,
        bank_id=OTHER_ORG_BANK_ID,
        family="liquidity",
        return_code="LCR-NSFR",
    )
    authv = _filing_grant()
    request_headers = headers(roles=("viewer",), authorization_version=authv)
    assert (
        _submit(
            db_client,
            foreign,
            request_headers=request_headers,
            bank_id=OTHER_ORG_BANK_ID,
        ).status_code
        == 404
    )
    # ...and naming our own bank with their package id does not reach it either.
    assert _submit(db_client, foreign, request_headers=request_headers).status_code == 404


def test_polling_the_regulator_is_transmission_authority_too(db_client: TestClient) -> None:
    """The channel surface belongs to the officer who files, not to the approver."""
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    authv = _grant(
        role_bundle=RoleBundle.APPROVER,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}/poll",
        headers=headers(roles=("approver",), authorization_version=authv),
    )
    assert response.status_code == 403
    assert "Validator" in response.json()["error"]["message"]


# --- the stage decision names the authority ---------------------------------
#
# The filing chain (``docs/filing_workflow_redesign.md`` §3.2, §6 steps 3-4)
# gives the package plane a Preparer -> Approver -> Validator chain. The stage a
# return waits at decides which permission its decision takes, which is what
# makes the three roles three AUTHORITIES rather than three labels: the
# ``validator`` bundle carries ``submit`` and never ``approve``, the approver
# bundle the reverse, so neither can take the other's stage.


def _pin_chain(
    package_id: UUID, *, current_stage_seq: int, status: str = "pending_approval"
) -> None:
    """Put a package at a named stage of the platform-default chain."""
    from app.domain.filing import workflow as filing_domain  # noqa: PLC0415
    from app.models import PackageWorkflowStage  # noqa: PLC0415

    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        package = session.get(RegulatoryPackage, package_id)
        assert package is not None
        for stage in filing_domain.default_stages():
            session.add(
                PackageWorkflowStage(
                    organization_id=package.organization_id,
                    bank_id=package.bank_id,
                    package_id=package.id,
                    seq=stage.seq,
                    stage_key=stage.stage_key,
                    title=stage.title,
                    decision_kind=stage.decision_kind,
                    officer_titles=[],
                    transmit_on_approve=stage.transmit_on_approve,
                    source="platform_default",
                )
            )
        package.current_stage_seq = current_stage_seq
        package.checks_passed = True
        package.status = status
        session.commit()
    finally:
        session.close()


def _decide_stage(
    client: TestClient, package_id: UUID, *, request_headers: dict[str, str], digest: str
):  # noqa: ANN202 - concise route-test helper
    return client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}/workflow/decisions",
        headers=request_headers,
        json={"decision": "approved", "round": 1, "review_digest": digest},
    )


def _chain_digest(client: TestClient, package_id: UUID, request_headers: dict[str, str]) -> str:
    response = client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}/workflow",
        headers=request_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["review_digest"]


def test_the_validators_stage_is_not_open_to_approval_authority(db_client: TestClient) -> None:
    """An Approver holding the strongest approval binding cannot take stage 3.

    This is the per-object half of the same sentence the submit gate already
    carries: approving is not filing. Here it is the STAGE that refuses, because
    the transmitting stage's decision takes ``Permission.SUBMIT``.
    """
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    _pin_chain(package_id, current_stage_seq=3)
    authv = _grant(
        role_bundle=RoleBundle.APPROVER,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
    request_headers = headers(roles=("approver",), authorization_version=authv)
    digest = _chain_digest(db_client, package_id, request_headers)
    response = _decide_stage(db_client, package_id, request_headers=request_headers, digest=digest)
    assert response.status_code == 403
    assert "Validator" in response.json()["error"]["message"]


def test_a_validator_binding_takes_the_validators_stage(db_client: TestClient) -> None:
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    _pin_chain(package_id, current_stage_seq=3)
    authv = _filing_grant()
    request_headers = headers(roles=("viewer",), authorization_version=authv)
    digest = _chain_digest(db_client, package_id, request_headers)
    response = _decide_stage(db_client, package_id, request_headers=request_headers, digest=digest)
    assert response.status_code == 200, response.text
    body = response.json()
    validator_stage = next(stage for stage in body["stages"] if stage["transmit_on_approve"])
    assert [decision["decision"] for decision in validator_stage["decisions"]] == ["approved"]
    # The chain is still incomplete: this fixture never had the Approver decide,
    # and the transmission gate reads every reviewing stage, not the last one.
    assert body["complete"] is False


def test_a_validator_binding_does_not_open_the_approvers_stage(db_client: TestClient) -> None:
    """The split cuts both ways: ``validator`` carries no ``approve``."""
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    _pin_chain(package_id, current_stage_seq=2)
    authv = _filing_grant()
    request_headers = headers(roles=("viewer",), authorization_version=authv)
    digest = _chain_digest(db_client, package_id, request_headers)
    response = _decide_stage(db_client, package_id, request_headers=request_headers, digest=digest)
    assert response.status_code == 403


def test_the_officer_who_generated_the_return_cannot_take_a_review_stage(
    db_client: TestClient,
) -> None:
    """§3.3 layer 3, at the gate: the Preparer is a maker on every stage."""
    package_id = _package(family="liquidity", return_code="LCR-NSFR", generated_by=USER_1)
    _pin_chain(package_id, current_stage_seq=3)
    authv = _filing_grant()
    request_headers = headers(roles=("viewer",), authorization_version=authv)
    digest = _chain_digest(db_client, package_id, request_headers)
    response = _decide_stage(db_client, package_id, request_headers=request_headers, digest=digest)
    assert response.status_code == 403


def test_a_gated_family_hides_its_chain_before_it_refuses(db_client: TestClient) -> None:
    """Reading the chain of an ICAAP package is still a disclosure."""
    package_id = _package()
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}/workflow",
        headers=headers(roles=("approver",)),
    )
    assert response.status_code == 404


# --- an Approver GRANT actually approves ------------------------------------
#
# Until 2026-09-20 the approve-side dependency dispatched on the family and, for
# an UNGATED family — every BSD, liquidity, capital and FX return, i.e. all of
# them but ICAAP — returned with the scalar ladder alone. A complete Approver
# binding was therefore never read on the returns banks actually file, and an
# Account Administrator holding one was refused with "requires the 'analyst'
# role or higher" while the dashboard, which projects the control from the same
# binding, offered them the button. Fail-open screen over a fail-closed server.
#
# The binding is now asked FIRST on both approve routes. It is additive: the
# scalar ladder stays behind it, so nobody who could approve yesterday is
# refused today. Removing the ladder is a separate cutover.


def _approver_grant(user_id: UUID = USER_1, *, sensitivity: SensitivityScope) -> int:
    return _grant(
        role_bundle=RoleBundle.APPROVER,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=sensitivity,
        user_id=user_id,
    )


def _decide_approval(
    client: TestClient, package_id: UUID, *, request_headers: dict[str, str]
):  # noqa: ANN202 - concise route-test helper
    return client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}/decide-approval",
        headers=request_headers,
        json={"action": "approved"},
    )


def test_an_approver_grant_approves_an_ungated_return_without_a_scalar_role(
    db_client: TestClient,
) -> None:
    """The founder's case, inverted into an assertion.

    A complete Approver binding over Regulatory Reporting for this exact
    institution, held by an identity whose scalar role is OUTSIDE the
    analyst/approver ladder. It used to 403 at the ladder before the binding was
    ever read.
    """
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    _pin_chain(package_id, current_stage_seq=2)
    authv = _approver_grant(sensitivity=SensitivityScope.RESTRICTED)
    request_headers = headers(roles=("account_admin",), authorization_version=authv)
    digest = _chain_digest(db_client, package_id, request_headers)
    response = _decide_stage(db_client, package_id, request_headers=request_headers, digest=digest)
    assert response.status_code == 200, response.text
    approver_stage = next(stage for stage in response.json()["stages"] if stage["seq"] == 2)
    assert [decision["decision"] for decision in approver_stage["decisions"]] == ["approved"]


def test_the_legacy_approval_route_reads_the_same_binding(db_client: TestClient) -> None:
    """One act, one authority. Settings -> Approvals posts here, not to the chain
    route, and a second rule for the same decision is the seam D-069 describes."""
    package_id = _package(
        family="liquidity", return_code="LCR-NSFR", status="pending_approval"
    )
    _pin_chain(package_id, current_stage_seq=2, status="pending_approval")
    authv = _approver_grant(sensitivity=SensitivityScope.RESTRICTED)
    response = _decide_approval(
        db_client,
        package_id,
        request_headers=headers(roles=("account_admin",), authorization_version=authv),
    )
    # Past authorization. The lifecycle then refuses, because this return's
    # policy requires signatures and approving it is the signing act — a 403
    # would mean the gate is still shut, which is what this test exists to catch.
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["error_code"] == "approval_requires_signature"


def test_a_grant_at_the_wrong_classification_does_not_approve(db_client: TestClient) -> None:
    """Reporting resources are RESTRICTED, and sensitivity is exact-or-all.

    A grant naming ``confidential`` names a classification no Regulatory
    Reporting resource carries, so it does not authorise a reporting act — the
    same rule that governs transmission. Stated as a test because the refusal is
    otherwise indistinguishable from "no grant at all", and the remedy is to
    re-issue the sentence rather than to widen the gate.
    """
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    _pin_chain(package_id, current_stage_seq=2)
    authv = _approver_grant(sensitivity=SensitivityScope.CONFIDENTIAL)
    request_headers = headers(roles=("account_admin",), authorization_version=authv)
    digest = _chain_digest(db_client, package_id, request_headers)
    response = _decide_stage(db_client, package_id, request_headers=request_headers, digest=digest)
    assert response.status_code == 403
    # And it says WHICH dimension missed. The first version of this fix left the
    # caller reading "This action requires the 'analyst' role or higher" — a
    # sentence about a scalar role they will never hold, while the evaluator's
    # own trace already said 'sensitivity_mismatch'. That cost a live debugging
    # round, so the message is pinned, not just the status.
    message = response.json()["error"]["message"]
    assert "sensitivity" in message
    assert "Restricted" in message
    assert "Approver" in message
    assert "analyst" not in message


def test_a_grant_for_another_module_names_the_module_that_missed(
    db_client: TestClient,
) -> None:
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    _pin_chain(package_id, current_stage_seq=2)
    authv = _grant(
        role_bundle=RoleBundle.APPROVER,
        module_scope=ModuleScope.LIQUIDITY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
    request_headers = headers(roles=("account_admin",), authorization_version=authv)
    digest = _chain_digest(db_client, package_id, request_headers)
    response = _decide_stage(db_client, package_id, request_headers=request_headers, digest=digest)
    assert response.status_code == 403
    assert "Regulatory Reporting" in response.json()["error"]["message"]


def test_a_caller_with_no_binding_at_all_still_reads_the_scalar_refusal(
    db_client: TestClient,
) -> None:
    """A near-miss message is for a near miss. Somebody with no grant is told
    what they have always been told, and learns nothing new about the tenant."""
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    _pin_chain(package_id, current_stage_seq=2)
    request_headers = headers(roles=("account_admin",))
    digest = _chain_digest(db_client, package_id, request_headers)
    response = _decide_stage(db_client, package_id, request_headers=request_headers, digest=digest)
    assert response.status_code == 403
    message = response.json()["error"]["message"]
    assert "analyst" in message
    assert "grant" not in message


def test_a_near_miss_never_discloses_a_gated_package(db_client: TestClient) -> None:
    """The better message must not become a disclosure.

    A caller holding a Regulatory Reporting grant but no Capital one must not
    learn from a helpful refusal that an ICAAP package exists for a date. The
    gated-family check runs before any evaluation, so there is no trace and no
    near miss — the answer stays 404.
    """
    package_id = _package()
    authv = _approver_grant(sensitivity=SensitivityScope.CONFIDENTIAL)
    request_headers = headers(roles=("account_admin",), authorization_version=authv)
    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}"
        "/workflow/decisions",
        headers=request_headers,
        json={"decision": "approved", "round": 1, "review_digest": "0" * 64},
    )
    assert response.status_code == 404


def test_the_scalar_approver_ladder_still_approves(db_client: TestClient) -> None:
    """Additive, not a cutover: nobody who could approve yesterday is refused."""
    package_id = _package(
        family="liquidity", return_code="LCR-NSFR", status="pending_approval"
    )
    _pin_chain(package_id, current_stage_seq=2, status="pending_approval")
    response = _decide_approval(
        db_client, package_id, request_headers=headers(roles=("approver",))
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["error_code"] == "approval_requires_signature"
