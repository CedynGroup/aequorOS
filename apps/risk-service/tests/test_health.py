from __future__ import annotations

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


def test_create_app() -> None:
    app = create_app()

    assert app.title == "risk-service"


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
    monkeypatch.delenv("RISK_S3_BUCKET", raising=False)
    monkeypatch.delenv("RISK_S3_REGION", raising=False)
    monkeypatch.setenv("RISK_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("RISK_S3_BUCKET", "")
    monkeypatch.setenv("RISK_S3_REGION", "")
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
