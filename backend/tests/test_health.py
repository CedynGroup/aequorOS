from __future__ import annotations

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


def test_create_app() -> None:
    app = create_app()

    assert app.title == "risk-service"


def test_production_refuses_to_start_when_it_cannot_sign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A production deployment that cannot sign cannot file — fail at boot.

    Signing is required for every return by default, so an unset pepper or
    disabled signing means no statutory return can leave the platform. The
    operator must learn that when the container starts, not when an officer
    opens a return on the morning it is due.
    """
    monkeypatch.setenv("APP_ENV", "production")
    # setenv("") not delenv: deleting lets pydantic-settings read the value
    # back out of a developer's .env (see tests/conftest.py).
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "0")
    monkeypatch.setenv("SIGNER_ID_PEPPER", "")
    get_settings.cache_clear()

    with pytest.raises(RuntimeError) as excinfo:
        create_app()

    # The message must name the settings — "misconfigured" is not actionable.
    assert "ATTESTATION_SIGNING_ENABLED" in str(excinfo.value)
    assert "SIGNER_ID_PEPPER" in str(excinfo.value)
    get_settings.cache_clear()


def test_non_production_still_starts_without_signing_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Developers work on unrelated things without a signing key.

    The gap is still reported per request by the submission gate
    (`signing_not_configured`), so it is surfaced rather than hidden — it just
    does not stop the process from starting outside production.
    """
    monkeypatch.setenv("APP_ENV", "staging")
    # setenv("") not delenv: deleting lets pydantic-settings read the value
    # back out of a developer's .env (see tests/conftest.py).
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "0")
    monkeypatch.setenv("SIGNER_ID_PEPPER", "")
    get_settings.cache_clear()

    assert create_app().title == "risk-service"
    get_settings.cache_clear()


def test_live_health(client: TestClient) -> None:
    response = client.get("/api/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "service": "risk-service",
        "environment": "test",
        "status": "ok",
    }


def test_ready_health_skips_database_when_unconfigured_in_test(client: TestClient) -> None:
    response = client.get("/api/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "service": "risk-service",
        "environment": "test",
        "status": "ok",
        "database": {"status": "skipped", "storage": "ok"},
    }


def test_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/api/health/live", headers={"X-Request-ID": "test-request-id"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request-id"


def test_http_error_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/api/missing", headers={"X-Request-ID": "missing-request"})

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Not Found",
            "request_id": "missing-request",
        },
    }


def test_unexpected_error_uses_initial_error_shape() -> None:
    router = APIRouter()

    @router.get("/boom")
    def boom() -> None:
        raise RuntimeError("boom")

    app = create_app()
    app.include_router(router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/boom", headers={"X-Request-ID": "req_test"})

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_server_error",
            "message": "An unexpected error occurred.",
            "request_id": "req_test",
        },
    }


def test_ready_health_requires_database_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    # A production app refuses to construct when it cannot sign, because every
    # return requires signatures and an unsignable deployment cannot file. That
    # guard is asserted in its own test; here it is satisfied so the readiness
    # probe is what is under test.
    monkeypatch.setenv("ATTESTATION_SIGNING_ENABLED", "1")
    monkeypatch.setenv("SIGNER_ID_PEPPER", "health-test-pepper-not-production")

    get_settings.cache_clear()

    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/health/ready", headers={"X-Request-ID": "ready-request"})

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "service_unavailable",
            "message": "Database is not configured.",
            "request_id": "ready-request",
        },
    }


def test_ready_health_requires_storage_when_database_is_configured(
    db_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Storage credentials are the shared MinIO set (S3_*); emptying the bucket
    # and region makes StorageSettings.configured False.
    monkeypatch.setenv("S3_BUCKET", "")
    monkeypatch.setenv("S3_REGION", "")
    get_settings.cache_clear()

    response = db_client.get("/api/health/ready", headers={"X-Request-ID": "ready-request"})

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "service_unavailable",
            "message": "Storage is not configured.",
            "request_id": "ready-request",
        },
    }
