"""Every return needs whole-institution scoped authority.

Regulatory Reporting/restricted governs ordinary returns; ICAAP keeps
Capital/confidential. Missing visibility hides the return, while visible returns
require their own action permission. Scalar roles cannot satisfy either gate.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.api.deps import TenantContext
from app.core.authorization import (
    DataScope,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models import (
    AuthorizationBinding,
    Bank,
    PackageSignatureRecipient,
    RegulatoryPackage,
    User,
)
from app.services import authorization
from app.services.institution_types import FALLBACK_TYPE_CODE
from app.services.regulatory_reporting import version_chain
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.support.helpers import (
    ORG_1,
    ORG_2,
    USER_1,
    error_envelope,
    headers,
    integration_key_headers,
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
    data_scope: DataScope = DataScope.ALL,
    data_scope_values: tuple[str, ...] = (),
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
                data_scope,
                data_scope_values,
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


def _assign_signature(package_id: UUID, *, user_id: UUID = USER_1) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.add(
            PackageSignatureRecipient(
                organization_id=ORG_1,
                package_id=package_id,
                attestation_cycle=1,
                signing_role="approver",
                recipient_user_id=user_id,
                recipient_signer_id=f"fixture-{str(user_id)[:8]}",
                routing_order=1,
            )
        )
        session.commit()
    finally:
        session.close()


def _add_user() -> UUID:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        user = User(
            organization_id=ORG_1,
            email=f"package-maker-{uuid4()}@example.test",
            display_name="Package Maker",
            role="analyst",
        )
        session.add(user)
        session.commit()
        return user.id
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
    authv = _grant(
        module_scope=ModuleScope.REGULATORY, sensitivity_scope=SensitivityScope.RESTRICTED
    )
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages",
        headers=headers(roles=("analyst",), authorization_version=authv),
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


# --- ordinary families require Regulatory Reporting ------------------------------------


def test_ordinary_returns_require_regulatory_authority(db_client: TestClient) -> None:
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}"
    assert db_client.get(base, headers=headers(roles=("approver",))).status_code == 404
    version = _grant(
        role_bundle=RoleBundle.VIEWER,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
    viewer = headers(roles=("approver",), authorization_version=version)
    assert db_client.get(base, headers=viewer).status_code == 200
    assert db_client.post(f"{base}/validate", headers=viewer).status_code == 403
    version = _grant(
        module_scope=ModuleScope.REGULATORY, sensitivity_scope=SensitivityScope.RESTRICTED
    )
    assert (
        db_client.post(
            f"{base}/validate", headers=headers(roles=("viewer",), authorization_version=version)
        ).status_code
        == 200
    )


def test_regulatory_viewer_cannot_send_back_but_approver_can(
    db_client: TestClient,
) -> None:
    maker_id = _add_user()
    package_id = _package(
        family="liquidity",
        return_code="LCR-NSFR",
        generated_by=maker_id,
        status="pending_approval",
    )
    _pin_chain(package_id, current_stage_seq=2)
    endpoint = (
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}/attestation/send-back"
    )
    viewer_version = _grant(
        role_bundle=RoleBundle.VIEWER,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
    refused = db_client.post(
        endpoint,
        headers=headers(roles=("viewer",), authorization_version=viewer_version),
        json={"reason": "Viewer must not change the return state."},
    )
    assert refused.status_code == 403, refused.text
    with get_sessionmaker()() as session:
        assert session.get(RegulatoryPackage, package_id).status == "pending_approval"

    approver_version = _grant(
        role_bundle=RoleBundle.APPROVER,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
    sent_back = db_client.post(
        endpoint,
        headers=headers(roles=("viewer",), authorization_version=approver_version),
        json={"reason": "Correct the return before another review."},
    )
    assert sent_back.status_code == 200, sent_back.text
    with get_sessionmaker()() as session:
        assert session.get(RegulatoryPackage, package_id).status == "generated"


def test_binding_only_signer_queue_filters_by_whole_institution_family(
    db_client: TestClient,
) -> None:
    regulatory = _package(family="liquidity", return_code="LCR-NSFR")
    icaap = _package(family="icaap", return_code="ICAAP-REPORT")
    _assign_signature(regulatory)
    _assign_signature(icaap)
    version = _grant(
        role_bundle=RoleBundle.APPROVER,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )

    response = db_client.get(
        "/api/v1/attestation/awaiting-my-signature",
        headers=headers(roles=("viewer",), authorization_version=version),
    )
    assert response.status_code == 200, response.text
    assert [row["package_id"] for row in response.json()["items"]] == [str(regulatory)]


@pytest.mark.parametrize("role", ["viewer", "account_admin"])
def test_binding_only_signer_can_set_up_only_their_own_signature(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    monkeypatch.setenv("SIGNER_ID_PEPPER", "test-signer-setup-pepper")
    get_settings.cache_clear()
    with get_sessionmaker()() as session:
        user = session.get(User, USER_1)
        assert user is not None
        user.role = role
        session.commit()
    version = _grant(
        role_bundle=RoleBundle.APPROVER,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
    auth = headers(roles=(role,), authorization_version=version)
    identity_url = "/api/v1/attestation/signer-identity"
    appearance_url = "/api/v1/attestation/my-signature-appearance"

    identity = db_client.get(identity_url, headers=auth)
    assert identity.status_code == 200, identity.text
    signer_id = identity.json()["signer_id"]
    assert identity.json()["user_id"] == str(USER_1)
    assert db_client.get(identity_url, headers=auth).json()["signer_id"] == signer_id
    empty = db_client.get(appearance_url, headers=auth)
    assert empty.status_code == 200, empty.text
    assert empty.json()["adopted"] is False
    assert empty.json()["signer_id"] == signer_id

    for name in ("Ama Mensah", "Ama A. Mensah"):
        adopted = db_client.put(
            appearance_url,
            headers=auth,
            json={"kind": "typed", "typed_name": name, "typed_font": "times_italic"},
        )
        assert adopted.status_code == 200, adopted.text
        assert adopted.json()["adopted"] is True
        assert adopted.json()["signer_id"] == signer_id
        assert adopted.json()["typed_name"] == name
        reread = db_client.get(appearance_url, headers=auth)
        assert reread.status_code == 200, reread.text
        stored = reread.json()
        assert stored["adopted"] is True
        assert stored["signer_id"] == signer_id
        assert stored["kind"] == "typed"
        assert stored["typed_name"] == name
        assert stored["typed_font"] == "times_italic"

    other_user = _add_user()
    other_auth = headers(user_id=other_user, roles=("viewer",))
    other = db_client.get(appearance_url, headers=other_auth)
    assert other.status_code == 200, other.text
    assert other.json()["adopted"] is False
    assert other.json()["signer_id"] != signer_id
    assert other.json()["typed_name"] is None


@pytest.mark.parametrize(
    ("method", "endpoint"),
    [
        ("GET", "signer-identity"),
        ("GET", "my-signature-appearance"),
        ("PUT", "my-signature-appearance"),
    ],
)
@pytest.mark.parametrize("principal", ["impersonation", "machine"])
def test_signer_setup_refuses_noninteractive_principals(
    db_client: TestClient, method: str, endpoint: str, principal: str
) -> None:
    auth = (
        _impersonation_headers()
        if principal == "impersonation"
        else integration_key_headers(SAMPLE_BANK_ID)
    )
    response = db_client.request(
        method,
        f"/api/v1/attestation/{endpoint}",
        headers=auth,
        json={"kind": "typed", "typed_name": "Examiner", "typed_font": "times_italic"}
        if method == "PUT"
        else None,
    )
    assert response.status_code == (401 if principal == "machine" else 403), response.text


@pytest.mark.parametrize("authority", ["narrowed", "unbound"])
def test_narrowed_or_unbound_assignee_has_an_empty_signer_queue(
    db_client: TestClient, authority: str
) -> None:
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    _assign_signature(package_id)
    version = 1
    if authority == "narrowed":
        version = _grant(
            role_bundle=RoleBundle.APPROVER,
            module_scope=ModuleScope.CREDIT,
            sensitivity_scope=SensitivityScope.ALL,
            data_scope=DataScope.BRANCH,
            data_scope_values=("BR-001",),
        )

    response = db_client.get(
        "/api/v1/attestation/awaiting-my-signature",
        headers=headers(roles=("viewer",), authorization_version=version),
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"items": []}


def test_an_unknown_package_never_discloses_figures(db_client: TestClient) -> None:
    for role in ("viewer", "analyst", "approver"):
        response = db_client.post(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{uuid4()}/validate",
            headers=headers(roles=(role,)),
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
    assert response.status_code == 404


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
# Both approval routes now require the complete scoped binding.


def _approver_grant(user_id: UUID = USER_1, *, sensitivity: SensitivityScope) -> int:
    return _grant(
        role_bundle=RoleBundle.APPROVER,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=sensitivity,
        user_id=user_id,
    )


def _decide_approval(client: TestClient, package_id: UUID, *, request_headers: dict[str, str]):  # noqa: ANN202 - concise route-test helper
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
    package_id = _package(family="liquidity", return_code="LCR-NSFR", status="pending_approval")
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


@pytest.mark.parametrize("route", ["stage", "legacy"])
def test_icaap_capital_approver_ignores_regulatory_near_miss(
    db_client: TestClient, route: str
) -> None:
    maker_id = _add_user()
    package_id = _package(status="pending_approval", generated_by=maker_id)
    _pin_chain(package_id, current_stage_seq=2)
    _grant(role_bundle=RoleBundle.APPROVER)
    authv = _grant(
        role_bundle=RoleBundle.APPROVER,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    request_headers = headers(roles=("account_admin",), authorization_version=authv)

    if route == "stage":
        digest = _chain_digest(db_client, package_id, request_headers)
        response = _decide_stage(
            db_client, package_id, request_headers=request_headers, digest=digest
        )
        assert response.status_code == 200, response.text
    else:
        response = _decide_approval(db_client, package_id, request_headers=request_headers)
        assert response.status_code == 200, response.text


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
    _grant(
        role_bundle=RoleBundle.VIEWER,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
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
    _grant(
        role_bundle=RoleBundle.VIEWER,
        module_scope=ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.RESTRICTED,
    )
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


def test_a_caller_with_no_binding_cannot_read_or_approve_a_return(db_client: TestClient) -> None:
    package_id = _package(family="liquidity", return_code="LCR-NSFR")
    _pin_chain(package_id, current_stage_seq=2)
    request_headers = headers(roles=("account_admin", "approver"))
    base = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}"
    assert db_client.get(f"{base}/workflow", headers=request_headers).status_code == 404
    response = _decide_stage(
        db_client, package_id, request_headers=request_headers, digest="0" * 64
    )
    assert response.status_code == 404


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
        f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{package_id}/workflow/decisions",
        headers=request_headers,
        json={"decision": "approved", "round": 1, "review_digest": "0" * 64},
    )
    assert response.status_code == 404


def test_the_scalar_approver_ladder_never_approves(db_client: TestClient) -> None:
    """A scalar Approver without a reporting grant has no return authority."""
    package_id = _package(family="liquidity", return_code="LCR-NSFR", status="pending_approval")
    _pin_chain(package_id, current_stage_seq=2, status="pending_approval")
    response = _decide_approval(db_client, package_id, request_headers=headers(roles=("approver",)))
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("visible_family", ["icaap", "liquidity"])
def test_comparison_hides_unauthorized_targets_in_both_family_directions(
    db_client: TestClient, visible_family: str
) -> None:
    packages = {
        "icaap": _package(),
        "liquidity": _package(family="liquidity", return_code="LCR-NSFR"),
    }
    authorities = {
        "icaap": (ModuleScope.CAPITAL, SensitivityScope.CONFIDENTIAL),
        "liquidity": (ModuleScope.REGULATORY, SensitivityScope.RESTRICTED),
    }
    hidden_family = "liquidity" if visible_family == "icaap" else "icaap"
    module, sensitivity = authorities[visible_family]
    version = _grant(
        role_bundle=RoleBundle.VIEWER, module_scope=module, sensitivity_scope=sensitivity
    )
    auth = headers(roles=("viewer",), authorization_version=version)
    base, target = packages[visible_family], packages[hidden_family]
    endpoint = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{base}/comparison"
    hidden = db_client.get(endpoint, headers=auth, params={"against": str(target)})
    unknown = db_client.get(endpoint, headers=auth, params={"against": str(uuid4())})
    assert hidden.status_code == unknown.status_code == 404
    assert error_envelope(hidden) == error_envelope(unknown)
    with get_sessionmaker()() as db:
        ctx = TenantContext(
            organization_id=ORG_1, actor_user_id=USER_1, authorization_version=version
        )
        for left, right in ((base, target), (target, base)):
            with pytest.raises(HTTPException) as excinfo:
                version_chain.compare_versions(db, ctx, SAMPLE_BANK_ID, left, right)
            assert excinfo.value.status_code == 404

    module, sensitivity = authorities[hidden_family]
    version = _grant(
        role_bundle=RoleBundle.VIEWER, module_scope=module, sensitivity_scope=sensitivity
    )
    for left, right in ((base, target), (target, base)):
        visible = db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{left}/comparison",
            headers=headers(roles=("viewer",), authorization_version=version),
            params={"against": str(right)},
        )
        assert visible.status_code == 409, visible.text
        details = visible.json()["error"]["details"]
        assert details["error_code"] == "comparison_return_mismatch"
        assert "ICAAP-REPORT" in details["message"]
        assert "LCR-NSFR" in details["message"]


@pytest.mark.parametrize("family", ["icaap", "liquidity"])
@pytest.mark.parametrize("difference", ["version", "reporting_date", "basis"])
def test_scoped_package_comparison_preserves_visible_figures_and_direction(
    db_client: TestClient, family: str, difference: str
) -> None:
    code = "ICAAP-REPORT" if family == "icaap" else "LCR-NSFR"
    base = _package(family=family, return_code=code, status="superseded")
    target = _package(family=family, return_code=code)
    with get_sessionmaker()() as db:
        for package_id, amount in ((base, "100"), (target, "125")):
            row = db.get(RegulatoryPackage, package_id)
            assert row is not None
            row.snapshot = {
                "sections": [
                    {
                        "code": "position",
                        "title": "Position",
                        "rows": [{"code": "total", "description": "Total", "value": amount}],
                    }
                ]
            }
        row = db.get(RegulatoryPackage, target)
        assert row is not None
        if difference == "version":
            row.version = 2
        elif difference == "reporting_date":
            row.reporting_date = date(2026, 6, 30)
        else:
            row.basis = "consolidated"
        db.commit()
    version = _grant(
        role_bundle=RoleBundle.VIEWER,
        module_scope=ModuleScope.CAPITAL if family == "icaap" else ModuleScope.REGULATORY,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL
        if family == "icaap"
        else SensitivityScope.RESTRICTED,
    )
    for left, right, delta in ((base, target, "25"), (target, base, "-25"), (base, base, None)):
        response = db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{left}/comparison",
            headers=headers(roles=("viewer",), authorization_version=version),
            params={"against": str(right)},
        )
        assert response.status_code == 200, response.text
        comparison = response.json()
        assert comparison["base"]["package_id"] == str(left)
        assert comparison["target"]["package_id"] == str(right)
        assert comparison["identical"] is (left == right)
        if delta is None:
            assert comparison["sections"] == []
        else:
            assert comparison["changed_count"] == 1
            assert comparison["sections"][0]["lines"][0]["delta"] == delta


@pytest.mark.parametrize("other_org", [ORG_1, ORG_2])
def test_comparison_target_is_scoped_to_the_path_bank(
    db_client: TestClient, other_org: str
) -> None:
    base = _package()
    _add_bank(OTHER_ORG_BANK_ID, organization_id=other_org)
    target = _package(organization_id=other_org, bank_id=OTHER_ORG_BANK_ID)
    version = _grant(role_bundle=RoleBundle.VIEWER, institution_id=None)
    endpoint = f"/api/v1/banks/{SAMPLE_BANK_ID}/regulatory-packages/{base}/comparison"
    auth = headers(roles=("viewer",), authorization_version=version)
    foreign = db_client.get(endpoint, headers=auth, params={"against": str(target)})
    unknown = db_client.get(endpoint, headers=auth, params={"against": str(uuid4())})
    assert foreign.status_code == unknown.status_code == 404
    assert error_envelope(foreign) == error_envelope(unknown)
