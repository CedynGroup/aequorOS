"""Scoped-binding enforcement for FX dashboards and calculation runs."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from loguru import logger
from sqlalchemy import delete

from app.core.authorization import (
    BindingStatus,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    ResourceLocator,
    RoleBundle,
    SensitivityScope,
)
from app.core.observability import Condition
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.models import AuthorizationBinding, Bank, User
from app.schemas.regulatory_fx import FxScenarioBatchCreate
from app.schemas.regulatory_liquidity import RegulatoryRunBatchRead
from app.services import authorization, regulatory_fx
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID

SIBLING_BANK_ID = "BK-FX000002"


@pytest.fixture(autouse=True)
def _start_without_fixture_authority(db_client: TestClient) -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        session.execute(
            delete(AuthorizationBinding).where(AuthorizationBinding.organization_id == ORG_1)
        )
        if session.get(Bank, SAMPLE_BANK_ID) is None:
            session.add(
                Bank(
                    id=SAMPLE_BANK_ID,
                    organization_id=ORG_1,
                    name="FX authorization bank",
                    short_name="FX auth",
                    currency="GHS",
                    jurisdiction_code="GH",
                    license_type="universal_bank",
                    institution_type=FALLBACK_TYPE_CODE,
                )
            )
        session.commit()
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
                name="FX sibling bank",
                short_name="FX sibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
        session.commit()
    finally:
        session.close()


def _grant(  # noqa: PLR0913 - each binding dimension is an enforcement input
    *,
    role_bundle: RoleBundle = RoleBundle.VIEWER,
    institution_scope: InstitutionScope = InstitutionScope.INSTITUTION,
    institution_id: str | None = SAMPLE_BANK_ID,
    module_scope: ModuleScope = ModuleScope.FX,
    sensitivity_scope: SensitivityScope = SensitivityScope.AGGREGATED,
    status: BindingStatus = BindingStatus.ACTIVE,
    expired: bool = False,
) -> tuple[UUID, int]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        user = session.get(User, USER_1)
        assert user is not None
        binding = authorization.create_role_binding(
            session,
            organization_id=ORG_1,
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
            reason="Exercise FX scoped-binding enforcement.",
        )
        if status is not BindingStatus.ACTIVE or expired:
            binding.status = status.value
            if status is BindingStatus.REVOKED:
                binding.revoked_at = utc_now()
                binding.revoked_by_type = GrantorType.SYSTEM.value
                binding.revoked_by_id = "test-suite"
                binding.revoked_reason = "Exercise revoked binding denial."
            if expired:
                binding.valid_from = utc_now() - timedelta(days=2)
                binding.valid_until = utc_now() - timedelta(days=1)
            session.commit()
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


def _dashboard_probe(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    version: int,
) -> tuple[Any, list[str]]:
    calls: list[str] = []

    def stop_after_authorization(
        _db: object,
        _ctx: object,
        bank_id: str,
        _reporting_period_id: object,
    ) -> None:
        calls.append(bank_id)
        raise HTTPException(status_code=409, detail="authorized FX probe")

    monkeypatch.setattr(regulatory_fx, "get_fx_dashboard", stop_after_authorization)
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/dashboard",
        headers=headers(authorization_version=version),
    )
    return response, calls


@pytest.mark.parametrize(
    ("institution_scope", "institution_id"),
    [
        (InstitutionScope.INSTITUTION, SAMPLE_BANK_ID),
        (InstitutionScope.ORGANIZATION, None),
    ],
)
def test_exact_or_explicit_organization_binding_allows_aggregated_fx_view(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    institution_scope: InstitutionScope,
    institution_id: str | None,
) -> None:
    binding_id, version = _grant(
        institution_scope=institution_scope,
        institution_id=institution_id,
    )
    records, sink_id = _capture_binding_records()
    try:
        response, calls = _dashboard_probe(db_client, monkeypatch, version)
    finally:
        logger.remove(sink_id)

    assert response.status_code == 409
    assert calls == [SAMPLE_BANK_ID]
    decisions = _binding_extras(records)
    assert len(decisions) == 1
    assert decisions[0]["allowed"] is True
    assert decisions[0]["permission"] == "view"
    assert decisions[0]["module"] == "fx"
    assert decisions[0]["sensitivity"] == "aggregated"
    assert decisions[0]["matching_binding_ids"] == str(binding_id)


def test_no_binding_denies_fx_without_legacy_role_fallback(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        regulatory_fx,
        "get_fx_dashboard",
        lambda *_args, **_kwargs: calls.append("called"),
    )

    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/dashboard",
        headers=headers(roles=("admin",)),
    )

    assert response.status_code == 403
    assert response.json()["error"]["message"] == ("FX access requires an active scoped binding.")
    assert calls == []


@pytest.mark.parametrize(
    ("grant_changes", "trace_reason"),
    [
        ({"institution_id": SIBLING_BANK_ID}, "institution_mismatch"),
        ({"module_scope": ModuleScope.CAPITAL}, "module_mismatch"),
        (
            {"sensitivity_scope": SensitivityScope.CONFIDENTIAL},
            "sensitivity_mismatch",
        ),
        (
            {"role_bundle": RoleBundle.ACCOUNT_ADMIN},
            "permission_not_in_bundle",
        ),
    ],
)
def test_partial_fx_binding_denies(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    grant_changes: dict[str, object],
    trace_reason: str,
) -> None:
    if grant_changes.get("institution_id") == SIBLING_BANK_ID:
        _add_sibling_bank()
    _, version = _grant(**grant_changes)  # type: ignore[arg-type]
    records, sink_id = _capture_binding_records()
    try:
        response, calls = _dashboard_probe(db_client, monkeypatch, version)
    finally:
        logger.remove(sink_id)

    assert response.status_code == 403
    assert calls == []
    decisions = _binding_extras(records)
    assert len(decisions) == 1
    assert decisions[0]["binding_trace"].endswith(f":{trace_reason}")


def test_partial_bindings_never_compose_into_fx_authority(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _grant(
        module_scope=ModuleScope.CAPITAL,
        sensitivity_scope=SensitivityScope.AGGREGATED,
    )
    _, version = _grant(sensitivity_scope=SensitivityScope.CONFIDENTIAL)

    response, calls = _dashboard_probe(db_client, monkeypatch, version)

    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    ("status_value", "expired"),
    [
        (BindingStatus.SUSPENDED, False),
        (BindingStatus.REVOKED, False),
        (BindingStatus.ACTIVE, True),
    ],
)
def test_inactive_fx_binding_denies(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    status_value: BindingStatus,
    expired: bool,
) -> None:
    _, version = _grant(status=status_value, expired=expired)

    response, calls = _dashboard_probe(db_client, monkeypatch, version)

    assert response.status_code == 403
    assert calls == []


def test_stale_authorization_version_denies_before_fx_evaluation(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _grant()

    response, calls = _dashboard_probe(db_client, monkeypatch, 1)

    assert response.status_code == 401
    assert calls == []


def test_cross_tenant_fx_probe_stays_hidden(db_client: TestClient) -> None:
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/dashboard",
        headers=headers(ORG_2),
    )

    assert response.status_code == 404


def test_fx_evaluator_failure_denies_closed(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, version = _grant()

    def fail_evaluation(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("evaluator unavailable")

    monkeypatch.setattr(authorization, "evaluate_permission", fail_evaluation)
    records, sink_id = _capture_binding_records()
    try:
        response, calls = _dashboard_probe(db_client, monkeypatch, version)
    finally:
        logger.remove(sink_id)

    assert response.status_code == 403
    assert calls == []
    decisions = _binding_extras(records)
    assert len(decisions) == 1
    assert decisions[0]["reason"] == "binding_evaluation_failed"
    assert decisions[0]["severity"] == "error"


def test_fx_run_requires_analyst_confidential_binding_before_execution(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    period_id = uuid4()
    calls: list[str] = []

    def run_probe(
        _db: object,
        _ctx: object,
        bank_id: str,
        payload: FxScenarioBatchCreate,
    ) -> RegulatoryRunBatchRead:
        calls.append(bank_id)
        return RegulatoryRunBatchRead(
            bank_id=bank_id,
            reporting_period_id=payload.reporting_period_id,
            runs=[],
        )

    monkeypatch.setattr(regulatory_fx, "run_all_fx_scenarios", run_probe)
    _, viewer_version = _grant(sensitivity_scope=SensitivityScope.CONFIDENTIAL)
    denied = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/run-all-scenarios",
        headers=headers(authorization_version=viewer_version),
        json={"reporting_period_id": str(period_id)},
    )
    assert denied.status_code == 403
    assert calls == []

    _, analyst_version = _grant(
        role_bundle=RoleBundle.ANALYST,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    allowed = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/fx/run-all-scenarios",
        headers=headers(authorization_version=analyst_version),
        json={"reporting_period_id": str(period_id)},
    )
    assert allowed.status_code == 201, allowed.text
    assert calls == [SAMPLE_BANK_ID]


def test_fx_v1_authority_is_institution_and_module_scoped_only(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One FX binding covers the institution; v1 has no desk or currency dimension."""

    assert "desk_id" not in ResourceLocator.__dataclass_fields__
    assert "currency" not in ResourceLocator.__dataclass_fields__
    _, version = _grant()
    response, calls = _dashboard_probe(db_client, monkeypatch, version)

    assert response.status_code == 409
    assert calls == [SAMPLE_BANK_ID]
