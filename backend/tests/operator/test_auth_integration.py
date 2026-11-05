"""Operator identity: dev-token gate, production boot refusal, 401 envelope.

This module's other-suite cases live in ``test_auth.py``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_operator_settings, get_settings
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


@pytest.mark.parametrize("app_env", ["production", "staging"])
def test_dev_token_rejected_on_every_deployed_environment(
    operator_client: TestClient, monkeypatch: pytest.MonkeyPatch, app_env: str
) -> None:
    """Request-level belt to the boot-refusal braces.

    The test used to name only ``production``, which is exactly the shape of
    the bug (D-29): the guard asked ``app_env == "production"``, so ``staging``
    — a deployed host on the SAME primary database, reached through the same
    cross-tenant BYPASSRLS session — accepted a static shared secret as
    ``super_admin``. The rule is now an allow-list of UNDEPLOYED environments,
    so every deployed value is refused and a future one is refused by default.
    """
    monkeypatch.setenv("APP_ENV", app_env)
    get_settings.cache_clear()
    get_operator_settings.cache_clear()
    try:
        response = operator_client.get("/operator/v1/tenants", headers=operator_headers())
        assert response.status_code == 401
    finally:
        get_settings.cache_clear()
        get_operator_settings.cache_clear()


def test_provision_requires_auth(operator_client: TestClient) -> None:
    response = operator_client.post("/operator/v1/tenants", json={})
    assert response.status_code == 401
