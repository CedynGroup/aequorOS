from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from loguru import logger

from app.core.authorization import (
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.core.observability import Condition
from app.db.session import get_sessionmaker
from app.models import User
from app.services import authorization
from tests.api.helpers import ORG_1, USER_1, headers
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book


def _seed_liquidity_book() -> None:
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        session.flush()
        seed_canonical_fixture(session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
        session.commit()
    finally:
        session.close()


def _capture_shadow_records() -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    sink_id = logger.add(lambda message: records.append(dict(message.record)), level="DEBUG")
    return records, sink_id


def _shadow_extras(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        record["extra"]
        for record in records
        if record["extra"].get("condition") == Condition.AUTHORIZATION_SHADOW_DECISION.value
    ]


def test_universal_bank_gets_shared_liquidity_monitoring(db_client: TestClient) -> None:
    _seed_liquidity_book()
    records, sink_id = _capture_shadow_records()

    try:
        response = db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/liquidity-monitoring",
            headers=headers(),
            params={"as_of": FIXTURE_AS_OF.isoformat()},
        )
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

    shadow = _shadow_extras(records)
    assert len(shadow) == 1
    expected = {
        "condition": Condition.AUTHORIZATION_SHADOW_DECISION.value,
        "severity": "info",
        "binding_allowed": False,
        "legacy_allowed": True,
        "reason": "no_active_exact_binding",
        "organization_id": ORG_1,
        "principal_id": str(USER_1),
        "principal_type": PrincipalType.HUMAN.value,
        "permission": "view",
        "institution_scope": InstitutionScope.INSTITUTION.value,
        "institution_id": SAMPLE_BANK_ID,
        "module": "liq",
        "sensitivity": "confidential",
        "matching_binding_ids": "",
        "binding_trace": "",
    }
    assert {key: shadow[0][key] for key in expected} == expected


def test_liquidity_monitoring_shadow_observes_exact_institution_binding(
    db_client: TestClient,
) -> None:
    _seed_liquidity_book()
    session = get_sessionmaker()()
    try:
        user = session.get(User, USER_1)
        assert user is not None
        binding = authorization.create_role_binding(
            session,
            organization_id=ORG_1,
            principal_user_id=user.id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=RoleBundle.VIEWER,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                SAMPLE_BANK_ID,
                ModuleScope.LIQUIDITY,
                SensitivityScope.CONFIDENTIAL,
            ),
            grantor=authorization.GrantorRef(
                authorization.GrantorType.SYSTEM,
                "test-suite",
            ),
            reason="pilot liquidity-monitoring shadow binding",
        )
    finally:
        session.close()
    records, sink_id = _capture_shadow_records()

    try:
        response = db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/liquidity-monitoring",
            headers=headers(authorization_version=2),
            params={"as_of": FIXTURE_AS_OF.isoformat()},
        )
    finally:
        logger.remove(sink_id)

    assert response.status_code == 200, response.text
    shadow = _shadow_extras(records)
    assert len(shadow) == 1
    assert shadow[0]["binding_allowed"] is True
    assert shadow[0]["legacy_allowed"] is True
    assert shadow[0]["reason"] == "allowed"
    assert shadow[0]["institution_id"] == SAMPLE_BANK_ID
    assert shadow[0]["matching_binding_ids"] == str(binding.id)


def test_liquidity_monitoring_shadow_failure_does_not_become_an_endpoint_gate(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_liquidity_book()

    def fail_shadow_evaluation(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic shadow-only failure")

    monkeypatch.setattr(authorization, "evaluate_permission", fail_shadow_evaluation)
    records, sink_id = _capture_shadow_records()
    try:
        response = db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/liquidity-monitoring",
            headers=headers(),
            params={"as_of": FIXTURE_AS_OF.isoformat()},
        )
    finally:
        logger.remove(sink_id)

    assert response.status_code == 200, response.text
    shadow = _shadow_extras(records)
    assert len(shadow) == 1
    assert shadow[0]["binding_allowed"] is False
    assert shadow[0]["legacy_allowed"] is True
    assert shadow[0]["reason"] == "shadow_evaluation_failed"
    assert shadow[0]["severity"] == "error"
    assert shadow[0]["error_type"] == "RuntimeError"
