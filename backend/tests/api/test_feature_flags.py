"""``GET /api/v1/feature-flags``: how the dashboard learns whether BI is on.

Authenticated tenant route, no bank, mounted unconditionally. It projects the
three BI booleans and nothing else — no limit, no URL, no key — so a future
flag is added to the schema deliberately, never by spreading settings.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.schemas.feature_flags import FeatureFlagsRead
from tests.api.helpers import ORG_2, headers

URL = "/api/v1/feature-flags"

EXPECTED_KEYS = {"bi_enabled", "bi_mart_enqueue_enabled", "bi_scheduler_enabled"}


def test_an_unauthenticated_caller_is_refused(db_client: TestClient) -> None:
    assert db_client.get(URL).status_code == 401


def test_the_shape_is_exactly_the_three_bi_booleans(db_client: TestClient) -> None:
    response = db_client.get(URL, headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == EXPECTED_KEYS
    assert all(isinstance(value, bool) for value in body.values())


def test_the_defaults_are_off(db_client: TestClient) -> None:
    """The hermetic pins mirror the product defaults: nothing is on."""
    body = db_client.get(URL, headers=headers()).json()
    assert body == {
        "bi_enabled": False,
        "bi_mart_enqueue_enabled": False,
        "bi_scheduler_enabled": False,
    }


def test_the_values_follow_the_deployment_settings(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BI_ENABLED", "1")
    monkeypatch.setenv("BI_SCHEDULER_ENABLED", "1")
    get_settings.cache_clear()

    body = db_client.get(URL, headers=headers()).json()

    assert body == {
        "bi_enabled": True,
        "bi_mart_enqueue_enabled": False,
        "bi_scheduler_enabled": True,
    }


def test_the_response_carries_no_key_outside_the_schema(db_client: TestClient) -> None:
    """Nothing secret and nothing tunable rides along: no URL, cap, timeout or key."""
    body = db_client.get(URL, headers=headers()).json()
    assert set(body) == set(FeatureFlagsRead.model_fields)
    forbidden = ("url", "cap", "timeout", "license", "licence", "key", "retention")
    assert not [key for key in body if any(token in key for token in forbidden)]


def test_the_flags_are_deployment_wide_not_per_tenant(db_client: TestClient) -> None:
    """Any authenticated tenant reads the same projection; there is no bank in the path."""
    first = db_client.get(URL, headers=headers()).json()
    second = db_client.get(URL, headers=headers(org_id=ORG_2)).json()
    assert first == second


def test_no_bank_is_required_or_accepted(db_client: TestClient) -> None:
    """A bank query parameter is ignored: the route is not bank-scoped."""
    response = db_client.get(URL, params={"bank_id": "BK-NOPE0000"}, headers=headers())
    assert response.status_code == 200
