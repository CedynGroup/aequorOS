"""Operator identity: dev-token gate, production boot refusal, 401 envelope."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_operator_settings, get_settings
from app.operator.main import create_operator_app
from tests.operator.conftest import operator_headers


def test_health_requires_no_auth(operator_client: TestClient) -> None:
    response = operator_client.get("/operator/health")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "aequoros-operator"
    assert body["status"] == "ok"


def test_missing_token_is_401_with_house_envelope(operator_client: TestClient) -> None:
    response = operator_client.get("/operator/v1/tenants")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "unauthorized"
    assert body["error"]["request_id"]


def test_wrong_token_is_401(operator_client: TestClient) -> None:
    # OIDC is unconfigured in this suite, so a non-dev token has no valid path.
    response = operator_client.get(
        "/operator/v1/tenants", headers=operator_headers("not-the-token")
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_dev_token_rejected_when_dev_auth_disabled(
    operator_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPERATOR_DEV_AUTH_ENABLED", "0")
    get_operator_settings.cache_clear()
    response = operator_client.get("/operator/v1/tenants", headers=operator_headers())
    assert response.status_code == 401


def test_dev_token_rejected_in_production_even_if_enabled(
    operator_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Request-level belt to the boot-refusal braces: were a production app
    # somehow running with the flag on, the token still would not authenticate.
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    get_operator_settings.cache_clear()
    response = operator_client.get("/operator/v1/tenants", headers=operator_headers())
    assert response.status_code == 401


def test_dev_auth_in_production_refuses_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPERATOR_DEV_AUTH_ENABLED", "1")
    get_settings.cache_clear()
    get_operator_settings.cache_clear()
    with pytest.raises(RuntimeError, match="OPERATOR_DEV_AUTH_ENABLED"):
        create_operator_app()
    get_settings.cache_clear()
    get_operator_settings.cache_clear()


def test_provision_requires_auth(operator_client: TestClient) -> None:
    response = operator_client.post("/operator/v1/tenants", json={})
    assert response.status_code == 401
