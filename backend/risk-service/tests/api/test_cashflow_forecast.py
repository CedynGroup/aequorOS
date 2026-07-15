from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.services import cashflow_forecast
from app.services.sample_bank_seed import SAMPLE_BANK_ID
from tests.api.helpers import ORG_2, headers

FORECAST_PAYLOAD: dict[str, Any] = {
    "mode": "lstm",
    "horizon": 60,
    "asOfDate": "2026-07-14",
    "modelVersion": "lstm-20260714120000",
    "accuracy": {"lstmMape": 7.2, "staticMape": 11.8, "improvementPct": 38.9},
    "points": [
        {
            "day": 1,
            "date": "2026-07-15",
            "netFlow": 120000.5,
            "lower": 90000.25,
            "upper": 150000.75,
        },
        {
            "day": 2,
            "date": "2026-07-16",
            "netFlow": -35000.0,
            "lower": -60000.0,
            "upper": -10000.0,
        },
    ],
}
HISTORY_PAYLOAD: dict[str, Any] = {
    "points": [
        {"date": "2026-07-01", "netFlow": 100000.0},
        {"date": "2026-07-02", "netFlow": -2500.5},
    ]
}


class _FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: Any = None,
    error: Exception | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeClient:
        def __init__(self, *, base_url: str, timeout: float) -> None:
            assert base_url == "http://127.0.0.1:8010"
            assert timeout == 60.0

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, path: str, params: dict[str, Any] | None = None) -> _FakeResponse:
            calls.append((path, dict(params or {})))
            if error is not None:
                raise error
            return _FakeResponse(payload)

    monkeypatch.setattr(cashflow_forecast, "Client", FakeClient)
    return calls


def _seed_bank(db_client: TestClient) -> None:
    response = db_client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text


def test_forecast_proxies_ml_json_into_typed_contract(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_bank(db_client)
    calls = _install_fake_client(monkeypatch, payload=FORECAST_PAYLOAD)

    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/cashflow-forecast",
        headers=headers(),
        params={"horizon": 60, "mode": "lstm"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "lstm"
    assert body["horizon"] == 60
    assert body["asOfDate"] == "2026-07-14"
    assert body["modelVersion"] == "lstm-20260714120000"
    assert body["accuracy"] == {"lstmMape": 7.2, "staticMape": 11.8, "improvementPct": 38.9}
    assert len(body["points"]) == 2
    assert body["points"][0]["netFlow"] == 120000.5
    assert body["points"][1]["date"] == "2026-07-16"
    assert calls == [("/forecast", {"horizon": 60, "mode": "lstm"})]


def test_history_proxies_ml_json_with_default_days(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_bank(db_client)
    calls = _install_fake_client(monkeypatch, payload=HISTORY_PAYLOAD)

    response = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/cashflow-history", headers=headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["points"][0] == {"date": "2026-07-01", "netFlow": 100000.0}
    assert calls == [("/history", {"days": 90})]


def test_ml_connection_failure_maps_to_503(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_bank(db_client)
    _install_fake_client(monkeypatch, error=httpx.ConnectError("connection refused"))

    response = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/cashflow-forecast", headers=headers())
    assert response.status_code == 503
    assert response.json()["error"]["message"] == ("Cash flow forecasting service is unavailable.")


def test_ml_timeout_maps_to_503(db_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_bank(db_client)
    _install_fake_client(monkeypatch, error=httpx.ReadTimeout("timed out"))

    response = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/cashflow-history", headers=headers())
    assert response.status_code == 503
    assert response.json()["error"]["message"] == ("Cash flow forecasting service is unavailable.")


def test_invalid_horizon_mode_and_days_are_rejected_with_422(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_bank(db_client)
    calls = _install_fake_client(monkeypatch, payload=FORECAST_PAYLOAD)

    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/cashflow-forecast",
            headers=headers(),
            params={"horizon": 45},
        ).status_code
        == 422
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/cashflow-forecast",
            headers=headers(),
            params={"mode": "prophet"},
        ).status_code
        == 422
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/cashflow-history",
            headers=headers(),
            params={"days": 20},
        ).status_code
        == 422
    )
    assert calls == []


def test_unknown_or_cross_tenant_bank_returns_404_without_calling_ml(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_bank(db_client)
    calls = _install_fake_client(monkeypatch, payload=FORECAST_PAYLOAD)

    assert (
        db_client.get(f"/api/v1/banks/{uuid4()}/cashflow-forecast", headers=headers()).status_code
        == 404
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/cashflow-forecast", headers=headers(ORG_2)
        ).status_code
        == 404
    )
    assert (
        db_client.get(
            f"/api/v1/banks/{SAMPLE_BANK_ID}/cashflow-history", headers=headers(ORG_2)
        ).status_code
        == 404
    )
    assert calls == []
