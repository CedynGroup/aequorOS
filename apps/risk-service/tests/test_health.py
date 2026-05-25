from __future__ import annotations

import pytest
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
        "database": {"status": "skipped"},
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
