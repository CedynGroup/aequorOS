"""Scoped-binding enforcement for the IRRBB dashboard and compute routes."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from sqlalchemy import delete, func, select

from app.core.authorization import (
    BindingStatus,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.observability import Condition
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.models import AuthorizationBinding, Bank, BankReportingPeriod, RegulatoryRun, User
from app.services import authorization, regulatory_irr
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

BASE = f"/api/v1/banks/{SAMPLE_BANK_ID}/irr"
SIBLING_BANK_ID = "BK-IRR00002"


@pytest.fixture(autouse=True)
def _start_without_fixture_authority(db_client: TestClient) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        session.commit()
    finally:
        session.close()


def _seed_book() -> UUID:
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        period_id = session.scalar(
            select(BankReportingPeriod.id)
            .where(
                BankReportingPeriod.organization_id == ORG_1,
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            )
            .order_by(BankReportingPeriod.period_end.desc())
        )
        assert period_id is not None
        session.commit()
        return period_id
    finally:
        session.close()


def _add_sibling_bank() -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.add(
            Bank(
                id=SIBLING_BANK_ID,
                organization_id=ORG_1,
                name="IRRBB sibling bank",
                short_name="IRRBB sibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
        session.commit()
    finally:
        session.close()


def _grant(
    bundle: RoleBundle = RoleBundle.VIEWER,
    *,
    module: ModuleScope = ModuleScope.IRRBB,
    sensitivity: SensitivityScope = SensitivityScope.AGGREGATED,
    institution_scope: InstitutionScope = InstitutionScope.INSTITUTION,
    institution_id: str | None = SAMPLE_BANK_ID,
) -> tuple[UUID, int]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        binding = authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=USER_1,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                institution_scope=institution_scope,
                institution_id=institution_id,
                module_scope=module,
                sensitivity_scope=sensitivity,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "irrbb-test"),
            reason="IRRBB authorization regression",
        )
        user = session.get(User, USER_1)
        assert user is not None
        return binding.id, user.authorization_version
    finally:
        session.close()


def _auth(version: int = 1, *roles: str) -> dict[str, str]:
    return headers(
        ORG_1,
        user_id=USER_1,
        roles=roles or ("admin",),
        authorization_version=version,
    )


def _dashboard(client: TestClient, version: int = 1):
    return client.get(f"{BASE}/dashboard", headers=_auth(version))


def _capture_binding_records() -> tuple[list[dict[str, object]], int]:
    records: list[dict[str, object]] = []
    return records, logger.add(
        lambda message: records.append(dict(message.record)),
        level="DEBUG",
    )


def _binding_extras(records: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        record["extra"]  # type: ignore[index]
        for record in records
        if record["extra"].get("condition")  # type: ignore[union-attr,index]
        == Condition.AUTHORIZATION_BINDING_DECISION.value
    ]


def test_t1_exact_and_explicit_organization_bindings_allow_aggregated_view(
    db_client: TestClient,
) -> None:
    _seed_book()
    _, exact_version = _grant()
    exact = _dashboard(db_client, exact_version)
    assert exact.status_code == 200, exact.text

    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        session.commit()
    finally:
        session.close()
    _, organization_version = _grant(
        institution_scope=InstitutionScope.ORGANIZATION,
        institution_id=None,
    )
    organization = _dashboard(db_client, organization_version)
    assert organization.status_code == 200, organization.text


def test_t2_scalar_roles_cannot_open_irrbb_without_a_binding(
    db_client: TestClient,
) -> None:
    _seed_book()
    response = _dashboard(db_client)
    assert response.status_code == 403
    assert response.json()["error"]["message"] == (
        "IRRBB access requires an active scoped binding."
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"institution_id": SIBLING_BANK_ID},
        {"module_scope": ModuleScope.LIQUIDITY.value},
        {"sensitivity_scope": SensitivityScope.CONFIDENTIAL.value},
        {"role_bundle": RoleBundle.ACCOUNT_ADMIN.value},
    ],
)
def test_t3_partial_irrbb_binding_denies(
    db_client: TestClient,
    changes: dict[str, str],
) -> None:
    _seed_book()
    if changes.get("institution_id") == SIBLING_BANK_ID:
        _add_sibling_bank()
    binding_id, version = _grant()
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        binding = session.get(AuthorizationBinding, binding_id)
        assert binding is not None
        for name, value in changes.items():
            setattr(binding, name, value)
        session.commit()
    finally:
        session.close()

    assert _dashboard(db_client, version).status_code == 403


def test_t3_partial_bindings_never_compose_into_irrbb_authority(
    db_client: TestClient,
) -> None:
    _seed_book()
    _grant(module=ModuleScope.LIQUIDITY)
    _, version = _grant(sensitivity=SensitivityScope.CONFIDENTIAL)
    assert _dashboard(db_client, version).status_code == 403


@pytest.mark.parametrize(
    "changes",
    [
        {"status": BindingStatus.SUSPENDED.value},
        {
            "status": BindingStatus.REVOKED.value,
            "revoked_at": utc_now(),
            "revoked_by_type": GrantorType.SYSTEM.value,
            "revoked_by_id": "irrbb-test",
            "revoked_reason": "Exercise revoked binding denial.",
        },
        {"valid_from": utc_now() + timedelta(days=1)},
        {
            "valid_from": utc_now() - timedelta(days=2),
            "valid_until": utc_now() - timedelta(days=1),
        },
    ],
)
def test_t4_inactive_irrbb_binding_denies(
    db_client: TestClient,
    changes: dict[str, object],
) -> None:
    _seed_book()
    binding_id, version = _grant()
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        binding = session.get(AuthorizationBinding, binding_id)
        assert binding is not None
        for name, value in changes.items():
            setattr(binding, name, value)
        session.commit()
    finally:
        session.close()

    assert _dashboard(db_client, version).status_code == 403


def test_t5_cross_tenant_irrbb_probe_stays_hidden(db_client: TestClient) -> None:
    _seed_book()
    response = db_client.get(f"{BASE}/dashboard", headers=headers(ORG_2))
    assert response.status_code == 404


def test_t6_stale_authorization_version_denies_before_irrbb_evaluation(
    db_client: TestClient,
) -> None:
    _seed_book()
    _grant()
    response = _dashboard(db_client, 1)
    assert response.status_code == 401


def test_t7_evaluator_failure_denies_closed_with_telemetry(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_book()
    _, version = _grant()

    def fail_evaluation(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("evaluator unavailable")

    monkeypatch.setattr(authorization, "evaluate_permission", fail_evaluation)
    records, sink_id = _capture_binding_records()
    try:
        response = _dashboard(db_client, version)
    finally:
        logger.remove(sink_id)

    assert response.status_code == 403
    decisions = _binding_extras(records)
    assert len(decisions) == 1
    assert decisions[0]["reason"] == "binding_evaluation_failed"
    assert decisions[0]["severity"] == "error"


def test_aggregated_view_does_not_grant_confidential_compute(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    period_id = _seed_book()
    _, version = _grant()
    called = False

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("IRRBB engine must not execute before authorization")

    monkeypatch.setattr(regulatory_irr, "run_all_irr_scenarios", fail_if_called)
    session = get_sessionmaker()()
    try:
        before = session.scalar(select(func.count()).select_from(RegulatoryRun)) or 0
    finally:
        session.close()

    response = db_client.post(
        f"{BASE}/run-all-scenarios",
        headers=_auth(version),
        json={"reporting_period_id": str(period_id)},
    )

    assert response.status_code == 403
    assert called is False
    session = get_sessionmaker()()
    try:
        after = session.scalar(select(func.count()).select_from(RegulatoryRun)) or 0
    finally:
        session.close()
    assert after == before


def test_confidential_analyst_binding_allows_compute_only_analysis(
    db_client: TestClient,
) -> None:
    period_id = _seed_book()
    _, version = _grant(
        RoleBundle.ANALYST,
        sensitivity=SensitivityScope.CONFIDENTIAL,
    )
    response = db_client.get(
        f"{BASE}/ear-analysis",
        headers=_auth(version, "viewer"),
        params={
            "reporting_period_id": str(period_id),
            "horizon_months": 12,
            "delta_bp": 200,
        },
    )
    assert response.status_code == 200, response.text
    assert _dashboard(db_client, version).status_code == 403
