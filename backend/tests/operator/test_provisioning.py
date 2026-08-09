"""The onboarding saga: success, schema 422s, rollback, no-seeding honesty."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_operator_settings
from app.models import (
    Bank,
    OperatorAuditLog,
    Organization,
    SsoConnection,
    TenantStorage,
    User,
)
from app.operator.features.provision import get_provisioning_clients
from app.operator.services.tenant_provisioning import ProvisioningClients
from tests.operator.conftest import (
    FakeKmsClient,
    FakeS3Client,
    fake_storage_settings,
    operator_headers,
    provision_payload,
)


def _steps_by_name(body: dict) -> dict[str, dict]:
    return {step["step"]: step for step in body["steps"]}


def test_saga_success_end_to_end(  # noqa: PLR0915 - the one happy path, asserted end to end
    operator_client: TestClient, operator_db: Session, fake_s3: FakeS3Client
) -> None:
    response = operator_client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["succeeded"] is True
    assert body["warnings"] == []

    steps = _steps_by_name(body)
    assert steps["organization"]["status"] == "succeeded"
    assert steps["bank"]["status"] == "succeeded"
    assert steps["storage"]["status"] == "succeeded"
    assert steps["kms"]["status"] == "skipped"
    assert steps["kms"]["detail"] == (
        "KMS disabled (OPERATOR_AWS_KMS_ENABLED=0) — SSE-KMS not applied"
    )
    assert steps["sso_stub"]["status"] == "succeeded"
    assert steps["first_admin"]["status"] == "succeeded"
    assert steps["readiness"]["status"] == "succeeded"
    assert "empty-but-wired" in steps["readiness"]["detail"]

    organization_id = body["organization_id"]
    bank_id = body["bank_id"]
    assert organization_id.startswith("OR-")
    assert bank_id.startswith("BK-")
    # Both redirect URIs in the handoff pack — the documented foot-gun guard.
    assert body["sso_redirect_uris"] == [
        "/api/auth/callback/sso",
        "/api/attestation/step-up/callback",
    ]

    # Bank row: currency/jurisdiction exactly as confirmed, slug persisted so
    # first ingestion reuses the provisioned buckets.
    bank = operator_db.scalar(select(Bank).where(Bank.id == bank_id))
    assert bank is not None
    assert bank.organization_id == organization_id
    assert bank.currency == "GHS"
    assert bank.jurisdiction_code == "GH"
    assert bank.storage_slug == bank_id.lower()

    # Storage: four tier buckets on the fake backend AND recorded in the registry.
    expected_buckets = sorted(
        f"aequoros-dev-{bank_id.lower()}-{tier}" for tier in ("raw", "canonical", "outputs", "temp")
    )
    assert sorted(fake_s3.buckets) == expected_buckets
    registry = operator_db.scalar(
        select(TenantStorage).where(TenantStorage.organization_id == organization_id)
    )
    assert registry is not None
    assert sorted(registry.bucket_names) == expected_buckets
    assert registry.provider == "minio"
    assert registry.kms_key_arn is None
    # Probe object was cleaned up.
    assert all(objects == {} for objects in fake_s3.buckets.values())

    # SSO stub: present, disabled, inert.
    sso = operator_db.scalar(
        select(SsoConnection).where(SsoConnection.organization_id == organization_id)
    )
    assert sso is not None
    assert sso.enabled is False
    assert sso.jit_enabled is False

    # First admin: active admin, password auth, and the ONE-TIME plaintext
    # verifies against the stored Argon2id hash.
    one_time_password = body["admin_one_time_password"]
    assert one_time_password
    admin = operator_db.scalar(
        select(User).where(
            User.organization_id == organization_id,
            User.email == "admin@testbank.example",
        )
    )
    assert admin is not None
    assert admin.role == "admin"
    assert admin.auth_provider == "password"
    assert admin.is_active is True
    assert admin.password_hash is not None
    assert security.verify_password(one_time_password, admin.password_hash)

    # Audit: the provision action is recorded — WITHOUT the password.
    audit = operator_db.scalar(
        select(OperatorAuditLog).where(OperatorAuditLog.action == "tenants.provision")
    )
    assert audit is not None
    assert audit.operator_email == "dev@aequoros.com"
    assert audit.auth_mode == "dev"
    assert audit.target_org == organization_id
    assert audit.detail["succeeded"] is True
    assert one_time_password not in json.dumps(audit.detail)


def test_missing_currency_is_schema_level_422(operator_client: TestClient) -> None:
    payload = provision_payload()
    del payload["currency"]
    response = operator_client.post(
        "/operator/v1/tenants", json=payload, headers=operator_headers()
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_lowercase_currency_is_422(operator_client: TestClient) -> None:
    response = operator_client.post(
        "/operator/v1/tenants",
        json=provision_payload(currency="ghs"),
        headers=operator_headers(),
    )
    assert response.status_code == 422


def test_unknown_jurisdiction_is_422(
    operator_client: TestClient, operator_db: Session
) -> None:
    response = operator_client.post(
        "/operator/v1/tenants",
        json=provision_payload(jurisdiction_code="ZZ"),
        headers=operator_headers(),
    )
    assert response.status_code == 422
    assert "jurisdictions registry" in response.json()["error"]["message"]
    # Nothing was created.
    assert operator_db.scalar(select(Organization)) is None


def test_currency_jurisdiction_mismatch_warns_but_never_auto_couples(
    operator_client: TestClient, operator_db: Session
) -> None:
    response = operator_client.post(
        "/operator/v1/tenants",
        json=provision_payload(currency="NGN"),  # jurisdiction stays GH
        headers=operator_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["succeeded"] is True
    assert any("differs" in warning for warning in body["warnings"])
    bank = operator_db.scalar(select(Bank).where(Bank.id == body["bank_id"]))
    assert bank is not None
    assert bank.currency == "NGN"  # the operator's explicit choice stands


def test_storage_failure_rolls_back_everything(
    operator_client: TestClient, operator_db: Session, fake_s3: FakeS3Client
) -> None:
    fake_s3.fail_on = "put_object"  # buckets create fine; the probe write fails

    response = operator_client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["succeeded"] is False
    assert body["organization_id"] is None
    assert body["bank_id"] is None
    assert body["admin_one_time_password"] is None

    steps = _steps_by_name(body)
    assert steps["organization"]["status"] == "rolled_back"
    assert steps["bank"]["status"] == "rolled_back"
    assert steps["storage"]["status"] == "failed"
    assert "cleanup" in steps
    assert "first_admin" not in steps  # subsequent steps never ran

    # DB: no half-tenant — org, bank, registry, users all rolled back.
    assert operator_db.scalar(select(Organization)) is None
    assert operator_db.scalar(select(Bank)) is None
    assert operator_db.scalar(select(TenantStorage)) is None
    assert operator_db.scalar(select(User)) is None

    # External: the created (empty) buckets were deleted again.
    assert fake_s3.buckets == {}

    # The FAILED attempt is still audited (fresh transaction after rollback).
    audit = operator_db.scalar(
        select(OperatorAuditLog).where(OperatorAuditLog.action == "tenants.provision")
    )
    assert audit is not None
    assert audit.detail["succeeded"] is False


def test_duplicate_names_warn_but_do_not_block(operator_client: TestClient) -> None:
    first = operator_client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    )
    assert first.json()["succeeded"] is True
    second = operator_client.post(
        "/operator/v1/tenants",
        json=provision_payload(admin_email="admin2@testbank.example"),
        headers=operator_headers(),
    )
    body = second.json()
    assert body["succeeded"] is True
    assert body["organization_id"] != first.json()["organization_id"]
    assert len(body["warnings"]) == 2  # duplicate org name + duplicate bank name


def test_kms_step_applies_sse_kms_when_enabled(
    operator_client: TestClient,
    operator_db: Session,
    fake_s3: FakeS3Client,
    monkeypatch,  # noqa: ANN001 - pytest fixture
) -> None:
    monkeypatch.setenv("OPERATOR_AWS_KMS_ENABLED", "1")
    get_operator_settings.cache_clear()
    fake_kms = FakeKmsClient()
    operator_client.app.dependency_overrides[get_provisioning_clients] = (  # type: ignore[attr-defined]
        lambda: ProvisioningClients(
            s3_client=fake_s3,
            storage_settings=fake_storage_settings(),
            kms_client=fake_kms,
        )
    )

    response = operator_client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    )
    body = response.json()
    assert body["succeeded"] is True, body
    steps = _steps_by_name(body)
    assert steps["kms"]["status"] == "succeeded"

    organization_id = body["organization_id"]
    assert fake_kms.aliases == {f"alias/aequoros-{organization_id}": FakeKmsClient.KEY_ID}
    # SSE-KMS default encryption set on all four tier buckets.
    assert sorted(fake_s3.encryption) == sorted(fake_s3.buckets)
    registry = operator_db.scalar(
        select(TenantStorage).where(TenantStorage.organization_id == organization_id)
    )
    assert registry is not None
    assert registry.provider == "aws"
    assert registry.kms_key_arn == FakeKmsClient.ARN


def test_unconfigured_storage_fails_the_saga_honestly(
    operator_client: TestClient, operator_db: Session
) -> None:
    operator_client.app.dependency_overrides[get_provisioning_clients] = (  # type: ignore[attr-defined]
        lambda: ProvisioningClients(
            s3_client=None,
            storage_settings=fake_storage_settings(),
            unavailable_reason="object storage is not configured",
        )
    )
    response = operator_client.post(
        "/operator/v1/tenants", json=provision_payload(), headers=operator_headers()
    )
    body = response.json()
    assert body["succeeded"] is False
    steps = _steps_by_name(body)
    assert steps["storage"]["status"] == "failed"
    assert "not configured" in steps["storage"]["detail"]
    assert operator_db.scalar(select(Organization)) is None
