"""Scoped-binding enforcement for Capital, capital plans, SDI, and ILAAP."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from sqlalchemy import delete, select

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
from app.models import (
    AuthorizationBinding,
    Bank,
    BankReportingPeriod,
    CapitalPlan,
    IlaapSnapshot,
    User,
)
from app.services import authorization
from app.services.institution_types import FALLBACK_TYPE_CODE
from tests.api.helpers import ORG_1, ORG_2, USER_1, headers
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

CHECKER_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
SIBLING_BANK_ID = "BK-CAP00002"
PLAN_PAYLOAD = {
    "content": {
        "pillar2_addons": [],
        "management_actions": [
            {
                "action": "Retain earnings",
                "trigger": "Capital headroom reaches the action threshold",
                "owner": "CFO",
            }
        ],
        "trigger_framework": [
            {
                "metric_code": "car_pct",
                "early_warning_level": "16",
                "action_level": "14",
                "escalation": "Escalate to ALCO and the Board.",
            }
        ],
    },
    "reason": "Exercise scoped capital-plan authority.",
}


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


def _seed_capital_book() -> UUID:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        materialize_canonical_test_book(session)
        session.commit()
        resolved = session.scalar(
            select(BankReportingPeriod.id)
            .where(
                BankReportingPeriod.organization_id == ORG_1,
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            )
            .order_by(BankReportingPeriod.period_end.desc())
            .limit(1)
        )
        assert resolved is not None
        return resolved
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
                name="Capital sibling bank",
                short_name="Capital sibling",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
                institution_type=FALLBACK_TYPE_CODE,
            )
        )
        session.commit()
    finally:
        session.close()


def _ensure_checker() -> None:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        checker = session.get(User, CHECKER_ID)
        if checker is None:
            session.add(
                User(
                    id=CHECKER_ID,
                    organization_id=ORG_1,
                    email="capital.checker@example.test",
                    display_name="Capital Checker",
                    role="approver",
                )
            )
            session.commit()
    finally:
        session.close()


def _grant(  # noqa: PLR0913 - each binding dimension is an enforcement input
    *,
    user_id: UUID = USER_1,
    role_bundle: RoleBundle = RoleBundle.VIEWER,
    institution_scope: InstitutionScope = InstitutionScope.INSTITUTION,
    institution_id: str | None = SAMPLE_BANK_ID,
    module_scope: ModuleScope = ModuleScope.CAPITAL,
    sensitivity_scope: SensitivityScope = SensitivityScope.AGGREGATED,
    status: BindingStatus = BindingStatus.ACTIVE,
    expired: bool = False,
) -> tuple[UUID, int]:
    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        user = session.get(User, user_id)
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
            reason="Exercise Capital scoped-binding enforcement.",
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


@pytest.mark.parametrize(
    ("institution_scope", "institution_id"),
    [
        (InstitutionScope.INSTITUTION, SAMPLE_BANK_ID),
        (InstitutionScope.ORGANIZATION, None),
    ],
)
def test_exact_or_explicit_organization_binding_allows_aggregated_capital_view(
    db_client: TestClient,
    institution_scope: InstitutionScope,
    institution_id: str | None,
) -> None:
    _seed_capital_book()
    binding_id, version = _grant(
        institution_scope=institution_scope,
        institution_id=institution_id,
    )
    records, sink_id = _capture_binding_records()
    try:
        response = db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/dashboard",
            headers=headers(authorization_version=version),
        )
    finally:
        logger.remove(sink_id)

    assert response.status_code != 403, response.text
    decisions = _binding_extras(records)
    assert len(decisions) == 1
    assert decisions[0]["allowed"] is True
    assert decisions[0]["permission"] == "view"
    assert decisions[0]["module"] == "cap"
    assert decisions[0]["sensitivity"] == "aggregated"
    assert decisions[0]["matching_binding_ids"] == str(binding_id)


def test_no_binding_denies_capital_without_legacy_role_fallback(
    db_client: TestClient,
) -> None:
    _seed_capital_book()
    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/dashboard",
        headers=headers(roles=("admin",)),
    )

    assert response.status_code == 403
    assert response.json()["error"]["message"] == (
        "Capital access requires an active scoped binding."
    )


@pytest.mark.parametrize(
    ("grant_changes", "trace_reason"),
    [
        ({"institution_id": "BK-CAP00002"}, "institution_mismatch"),
        ({"module_scope": ModuleScope.LIQUIDITY}, "module_mismatch"),
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
def test_partial_capital_binding_denies(
    db_client: TestClient,
    grant_changes: dict[str, object],
    trace_reason: str,
) -> None:
    _seed_capital_book()
    if grant_changes.get("institution_id") == SIBLING_BANK_ID:
        _add_sibling_bank()
    _, version = _grant(**grant_changes)  # type: ignore[arg-type]
    records, sink_id = _capture_binding_records()
    try:
        response = db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/dashboard",
            headers=headers(authorization_version=version),
        )
    finally:
        logger.remove(sink_id)

    assert response.status_code == 403
    decisions = _binding_extras(records)
    assert len(decisions) == 1
    assert decisions[0]["binding_trace"].endswith(f":{trace_reason}")


def test_partial_bindings_never_compose_into_capital_authority(
    db_client: TestClient,
) -> None:
    _seed_capital_book()
    _grant(
        module_scope=ModuleScope.LIQUIDITY,
        sensitivity_scope=SensitivityScope.AGGREGATED,
    )
    _, version = _grant(
        module_scope=ModuleScope.CAPITAL,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )

    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/dashboard",
        headers=headers(authorization_version=version),
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("status_value", "expired"),
    [
        (BindingStatus.SUSPENDED, False),
        (BindingStatus.REVOKED, False),
        (BindingStatus.ACTIVE, True),
    ],
)
def test_inactive_capital_binding_denies(
    db_client: TestClient,
    status_value: BindingStatus,
    expired: bool,
) -> None:
    _seed_capital_book()
    _, version = _grant(status=status_value, expired=expired)

    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/dashboard",
        headers=headers(authorization_version=version),
    )

    assert response.status_code == 403


def test_stale_authorization_version_denies_before_capital_evaluation(
    db_client: TestClient,
) -> None:
    _seed_capital_book()
    _grant()

    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/dashboard",
        headers=headers(authorization_version=1),
    )

    assert response.status_code == 401


def test_cross_tenant_capital_probe_stays_hidden(
    db_client: TestClient,
) -> None:
    _seed_capital_book()

    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/dashboard",
        headers=headers(ORG_2),
    )

    assert response.status_code == 404


def test_capital_evaluator_failure_denies_closed(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_capital_book()
    _, version = _grant()

    def fail_evaluation(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("evaluator unavailable")

    monkeypatch.setattr(authorization, "evaluate_permission", fail_evaluation)
    records, sink_id = _capture_binding_records()
    try:
        response = db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/capital/dashboard",
            headers=headers(authorization_version=version),
        )
    finally:
        logger.remove(sink_id)

    assert response.status_code == 403
    decisions = _binding_extras(records)
    assert len(decisions) == 1
    assert decisions[0]["reason"] == "binding_evaluation_failed"
    assert decisions[0]["severity"] == "error"


def test_capital_sensitivity_boundaries_are_exact(
    db_client: TestClient,
) -> None:
    _seed_capital_book()
    _, aggregated_version = _grant()
    preview = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/submissions/bsd2",
        headers=headers(authorization_version=aggregated_version),
        params={"reporting_period_id": str(uuid4())},
    )
    assert preview.status_code == 403

    _, confidential_version = _grant(sensitivity_scope=SensitivityScope.CONFIDENTIAL)
    preview = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/submissions/bsd2",
        headers=headers(authorization_version=confidential_version),
        params={"reporting_period_id": str(uuid4())},
    )
    assurance = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/sdi/capital-assurance",
        headers=headers(authorization_version=confidential_version),
    )
    assert preview.status_code == 404
    assert assurance.status_code == 403

    _, restricted_version = _grant(sensitivity_scope=SensitivityScope.RESTRICTED)
    assurance = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/sdi/capital-assurance",
        headers=headers(authorization_version=restricted_version),
    )
    assert assurance.status_code != 403, assurance.text


def test_capital_plan_create_then_edit_uses_exact_permissions(
    db_client: TestClient,
) -> None:
    _seed_capital_book()
    _, version = _grant(
        role_bundle=RoleBundle.ANALYST,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    records, sink_id = _capture_binding_records()
    try:
        created = db_client.put(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/capital-plan",
            headers=headers(authorization_version=version),
            json=PLAN_PAYLOAD,
        )
        edited = db_client.put(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/capital-plan",
            headers=headers(authorization_version=version),
            json={**PLAN_PAYLOAD, "reason": "Revise the existing draft."},
        )
    finally:
        logger.remove(sink_id)

    assert created.status_code == 200, created.text
    assert edited.status_code == 200, edited.text
    decisions = _binding_extras(records)
    assert [decision["permission"] for decision in decisions] == ["create", "edit"]


def test_capital_plan_approval_requires_binding_and_distinct_checker(
    db_client: TestClient,
) -> None:
    _seed_capital_book()
    _, maker_version = _grant(
        role_bundle=RoleBundle.ANALYST,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    created = db_client.put(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital-plan",
        headers=headers(authorization_version=maker_version),
        json=PLAN_PAYLOAD,
    )
    assert created.status_code == 200, created.text

    _, self_approver_version = _grant(
        role_bundle=RoleBundle.APPROVER,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    self_approval = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital-plan/approve",
        headers=headers(authorization_version=self_approver_version),
        json={
            "approval_reference": "BOARD-CAP-1",
            "reason": "Attempt self-approval.",
        },
    )
    assert self_approval.status_code == 403

    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        plan = session.scalar(
            select(CapitalPlan).where(
                CapitalPlan.organization_id == ORG_1,
                CapitalPlan.bank_id == SAMPLE_BANK_ID,
            )
        )
        assert plan is not None and plan.status == "draft"
    finally:
        session.close()

    _ensure_checker()
    _, checker_version = _grant(
        user_id=CHECKER_ID,
        role_bundle=RoleBundle.APPROVER,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    checker_response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital-plan/approve",
        headers=headers(
            user_id=CHECKER_ID,
            roles=("viewer",),
            authorization_version=checker_version,
        ),
        json={
            "approval_reference": "BOARD-CAP-1",
            "reason": "Independent checker approval.",
        },
    )
    assert checker_response.status_code == 409
    assert checker_response.json()["error"]["details"]["error_code"] == "no_forecast_run"


def test_ilaap_refresh_requires_independent_capital_and_liquidity_bindings(
    db_client: TestClient,
) -> None:
    period_id = _seed_capital_book()
    _, cap_only_version = _grant(
        role_bundle=RoleBundle.ANALYST,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    missing_liquidity = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital-plan/ilaap-refresh",
        headers=headers(authorization_version=cap_only_version),
        json={"reporting_period_id": str(period_id)},
    )
    assert missing_liquidity.status_code == 403

    _, both_version = _grant(
        module_scope=ModuleScope.LIQUIDITY,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )
    both = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital-plan/ilaap-refresh",
        headers=headers(authorization_version=both_version),
        json={"reporting_period_id": str(period_id)},
    )
    assert both.status_code == 409
    assert both.json()["error"]["details"]["error_code"] == "no_baseline_run"

    session = get_sessionmaker()()
    session.info["organization_id"] = ORG_1
    try:
        assert (
            session.scalar(
                select(IlaapSnapshot.id).where(
                    IlaapSnapshot.organization_id == ORG_1,
                    IlaapSnapshot.bank_id == SAMPLE_BANK_ID,
                )
            )
            is None
        )
    finally:
        session.close()


def test_ilaap_refresh_does_not_compose_two_incomplete_decisions(
    db_client: TestClient,
) -> None:
    period_id = _seed_capital_book()
    _grant(
        role_bundle=RoleBundle.ANALYST,
        module_scope=ModuleScope.CAPITAL,
        sensitivity_scope=SensitivityScope.AGGREGATED,
    )
    _, version = _grant(
        module_scope=ModuleScope.LIQUIDITY,
        sensitivity_scope=SensitivityScope.CONFIDENTIAL,
    )

    response = db_client.post(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/capital-plan/ilaap-refresh",
        headers=headers(authorization_version=version),
        json={"reporting_period_id": str(period_id)},
    )

    assert response.status_code == 403
