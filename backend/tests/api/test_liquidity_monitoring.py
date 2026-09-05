from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from loguru import logger
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
from app.core.observability import Condition
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.models import AuthorizationBinding, Bank, User
from app.services import authorization, grant_administration
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, ORG_2, USER_1, USER_2, headers
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

URL = f"/api/v1/banks/{SAMPLE_BANK_ID}/liquidity-monitoring"
SIBLING_BANK_ID = "BK-LIQM0002"
OTHER_BANK_ID = "BK-LIQM0003"


def _seed_liquidity_book() -> None:
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.flush()
        seed_canonical_fixture(session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
        session.commit()
    finally:
        session.close()


def _add_bank(session: Session, organization_id: str, bank_id: str) -> None:
    session.add(
        Bank(
            id=bank_id,
            organization_id=organization_id,
            name=f"Liquidity bank {bank_id}",
            short_name=bank_id,
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal_bank",
            institution_type=FALLBACK_TYPE_CODE,
        )
    )
    session.commit()


def _grant(  # noqa: PLR0913 - each binding dimension is explicit
    *,
    organization_id: str = ORG_1,
    user_id: UUID = USER_1,
    role_bundle: RoleBundle = RoleBundle.VIEWER,
    institution_scope: InstitutionScope = InstitutionScope.INSTITUTION,
    institution_id: str | None = SAMPLE_BANK_ID,
    module_scope: ModuleScope = ModuleScope.LIQUIDITY,
    sensitivity_scope: SensitivityScope = SensitivityScope.CONFIDENTIAL,
) -> tuple[UUID, int]:
    session = get_sessionmaker()()
    session.info["organization_id"] = organization_id
    try:
        user = session.get(User, user_id)
        assert user is not None
        binding = authorization.create_role_binding(
            session,
            organization_id=organization_id,
            principal_user_id=user.id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=role_bundle,
            scope=authorization.BindingScope(
                institution_scope,
                institution_id,
                module_scope,
                sensitivity_scope,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "test-suite"),
            reason="exercise Liquidity Monitoring enforcement",
        )
        session.refresh(user)
        return binding.id, user.authorization_version
    finally:
        session.close()


def _capture_binding_records() -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    sink_id = logger.add(lambda message: records.append(dict(message.record)), level="DEBUG")
    return records, sink_id


def _binding_extras(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        record["extra"]
        for record in records
        if record["extra"].get("condition") == Condition.AUTHORIZATION_BINDING_DECISION.value
    ]


def _get(
    client: TestClient,
    *,
    organization_id: str = ORG_1,
    user_id: UUID | None = None,
    authorization_version: int = 1,
    roles: tuple[str, ...] = ("viewer",),
):
    return client.get(
        URL,
        headers=headers(
            organization_id,
            user_id=user_id,
            roles=roles,
            authorization_version=authorization_version,
        ),
        params={"as_of": FIXTURE_AS_OF.isoformat()},
    )


@pytest.mark.parametrize(
    ("institution_scope", "institution_id"),
    [
        (InstitutionScope.INSTITUTION, SAMPLE_BANK_ID),
        (InstitutionScope.ORGANIZATION, None),
    ],
)
def test_exact_or_explicit_organization_binding_allows_liquidity_monitoring(
    db_client: TestClient,
    institution_scope: InstitutionScope,
    institution_id: str | None,
) -> None:
    _seed_liquidity_book()
    binding_id, version = _grant(
        institution_scope=institution_scope,
        institution_id=institution_id,
    )
    records, sink_id = _capture_binding_records()
    try:
        response = _get(db_client, authorization_version=version)
    finally:
        logger.remove(sink_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["institution_class"] == "bank"
    assert body["maturity_ladder"]
    assert body["funding_concentration"]["total_deposits_ghs"]
    assert "counterbalancing_capacity" in body
    assert "ratios" not in body
    assert "reserves" not in body

    decisions = _binding_extras(records)
    assert len(decisions) == 1
    assert decisions[0]["allowed"] is True
    assert decisions[0]["reason"] == "allowed"
    assert decisions[0]["surface"] == "liquidity_monitoring"
    assert decisions[0]["matching_binding_ids"] == str(binding_id)


def test_no_binding_defaults_to_denial_without_legacy_role_fallback(
    db_client: TestClient,
) -> None:
    _seed_liquidity_book()
    records, sink_id = _capture_binding_records()
    try:
        response = _get(db_client, roles=("admin",))
    finally:
        logger.remove(sink_id)

    assert response.status_code == 403
    assert response.json()["error"]["message"] == (
        "Liquidity Monitoring access requires an active scoped binding."
    )
    decisions = _binding_extras(records)
    assert len(decisions) == 1
    assert decisions[0]["allowed"] is False
    assert decisions[0]["reason"] == "no_active_exact_binding"
    assert decisions[0]["binding_trace"] == ""


@pytest.mark.parametrize(
    ("scope_changes", "expected_trace_reason"),
    [
        ({"institution_id": SIBLING_BANK_ID}, "institution_mismatch"),
        ({"module_scope": ModuleScope.CAPITAL}, "module_mismatch"),
        ({"sensitivity_scope": SensitivityScope.PUBLISHED}, "sensitivity_mismatch"),
        ({"role_bundle": RoleBundle.ACCOUNT_ADMIN}, "permission_not_in_bundle"),
    ],
)
def test_partial_binding_denies_liquidity_monitoring(
    db_client: TestClient,
    scope_changes: dict[str, object],
    expected_trace_reason: str,
) -> None:
    _seed_liquidity_book()
    if scope_changes.get("institution_id") == SIBLING_BANK_ID:
        session = get_sessionmaker()()
        try:
            _add_bank(session, ORG_1, SIBLING_BANK_ID)
        finally:
            session.close()
    _, version = _grant(**scope_changes)  # type: ignore[arg-type]
    records, sink_id = _capture_binding_records()
    try:
        response = _get(db_client, authorization_version=version)
    finally:
        logger.remove(sink_id)

    assert response.status_code == 403
    decisions = _binding_extras(records)
    assert len(decisions) == 1
    assert expected_trace_reason in decisions[0]["binding_trace"]


@pytest.mark.parametrize("lifecycle", ["suspended", "expired", "revoked"])
def test_inactive_binding_lifecycle_denies_liquidity_monitoring(
    db_client: TestClient,
    lifecycle: str,
) -> None:
    _seed_liquidity_book()
    binding_id, version = _grant()
    session = get_sessionmaker()()
    try:
        binding = session.get(AuthorizationBinding, binding_id)
        assert binding is not None
        if lifecycle == "suspended":
            binding.status = BindingStatus.SUSPENDED.value
        elif lifecycle == "expired":
            binding.valid_from = utc_now() - timedelta(days=2)
            binding.valid_until = utc_now() - timedelta(days=1)
        else:
            binding.status = BindingStatus.REVOKED.value
            binding.revoked_at = utc_now()
            binding.revoked_by_type = GrantorType.SYSTEM.value
            binding.revoked_by_id = "test-suite"
            binding.revoked_reason = "test lifecycle"
        session.commit()
    finally:
        session.close()

    response = _get(db_client, authorization_version=version)

    assert response.status_code == 403


def test_multiple_partial_bindings_do_not_compose_into_authority(
    db_client: TestClient,
) -> None:
    _seed_liquidity_book()
    _grant(sensitivity_scope=SensitivityScope.PUBLISHED)
    _, version = _grant(module_scope=ModuleScope.CAPITAL)

    response = _get(db_client, authorization_version=version)

    assert response.status_code == 403


def test_cross_tenant_binding_neither_grants_access_nor_leaks_bank_existence(
    db_client: TestClient,
) -> None:
    _seed_liquidity_book()
    session = get_sessionmaker()()
    try:
        _add_bank(session, ORG_2, OTHER_BANK_ID)
    finally:
        session.close()
    _, version = _grant(
        organization_id=ORG_2,
        user_id=USER_2,
        institution_id=OTHER_BANK_ID,
    )

    response = _get(
        db_client,
        organization_id=ORG_2,
        user_id=USER_2,
        authorization_version=version,
    )

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Bank not found."


def test_stale_authorization_version_is_rejected_before_binding_evaluation(
    db_client: TestClient,
) -> None:
    _seed_liquidity_book()
    _grant()

    response = _get(db_client, authorization_version=1)

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Session authorization is stale. Sign in again."


def test_revocation_invalidates_session_then_denies_with_current_version(
    db_client: TestClient,
) -> None:
    _seed_liquidity_book()
    binding_id, granted_version = _grant()
    assert _get(db_client, authorization_version=granted_version).status_code == 200

    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        grant_administration.revoke_scoped_grant(
            session,
            organization_id=ORG_1,
            binding_id=binding_id,
            actor_user_id=USER_1,
            reason="remove Liquidity Monitoring access",
        )
        user = session.get(User, USER_1)
        assert user is not None
        revoked_version = user.authorization_version
    finally:
        session.close()

    stale = _get(db_client, authorization_version=granted_version)
    current = _get(db_client, authorization_version=revoked_version)

    assert stale.status_code == 401
    assert current.status_code == 403


def test_evaluator_failure_denies_closed_and_records_error(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_liquidity_book()
    records, sink_id = _capture_binding_records()

    def fail_evaluation(*args: object, **kwargs: object) -> None:
        raise RuntimeError("evaluator unavailable")

    monkeypatch.setattr(authorization, "_load_principal_grants", fail_evaluation)
    try:
        response = _get(db_client)
    finally:
        logger.remove(sink_id)

    assert response.status_code == 403
    decisions = _binding_extras(records)
    assert len(decisions) == 1
    assert decisions[0]["allowed"] is False
    assert decisions[0]["reason"] == "binding_evaluation_failed"
    assert decisions[0]["severity"] == "error"
    assert decisions[0]["error_type"] == "RuntimeError"


def test_bank_list_and_detail_expose_the_same_server_evaluated_access(
    db_client: TestClient,
) -> None:
    _seed_liquidity_book()
    denied = db_client.get("/api/v1/banks", headers=headers(roles=("viewer",)))
    denied_detail = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}",
        headers=headers(roles=("viewer",)),
    )
    assert denied.status_code == 200
    assert denied_detail.status_code == 200
    denied_bank = next(row for row in denied.json()["banks"] if row["id"] == SAMPLE_BANK_ID)
    assert denied_bank["liquidity_monitoring_access"] is False
    assert denied_detail.json()["liquidity_monitoring_access"] is False

    _, version = _grant()
    allowed = db_client.get(
        "/api/v1/banks",
        headers=headers(roles=("viewer",), authorization_version=version),
    )
    allowed_detail = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}",
        headers=headers(roles=("viewer",), authorization_version=version),
    )
    assert allowed.status_code == 200
    assert allowed_detail.status_code == 200
    allowed_bank = next(row for row in allowed.json()["banks"] if row["id"] == SAMPLE_BANK_ID)
    assert allowed_bank["liquidity_monitoring_access"] is True
    assert allowed_detail.json()["liquidity_monitoring_access"] is True
