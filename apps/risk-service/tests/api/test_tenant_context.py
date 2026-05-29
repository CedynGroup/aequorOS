from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from tests.api.helpers import ORG_1, headers


def test_rejects_unknown_tenant(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/cases", headers=headers(uuid4()))

    assert response.status_code == 401


def test_rejects_user_outside_tenant(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/cases", headers=headers(ORG_1, uuid4()))

    assert response.status_code == 401
