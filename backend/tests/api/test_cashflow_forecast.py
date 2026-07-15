"""Contract tests for the in-process cash-flow forecast endpoints.

The ML module runs in fast-test config with a module-scoped isolated
artifacts directory so the LSTM trains at most once for the whole module.
Order matters: the validation, tenant, and torch-failure tests run first and
assert no training was triggered (empty artifacts dir); the LSTM forecast
test then lazy-trains, and later tests reuse the warm model.
"""

from __future__ import annotations

import datetime
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.services import cashflow_forecast
from app.services.sample_bank_seed import SAMPLE_BANK_ID
from tests.api.helpers import ORG_2, headers

AS_OF = datetime.date(2026, 3, 31)


@pytest.fixture(scope="module", autouse=True)
def ml_artifacts_dir() -> Iterator[Path]:
    """Fast-test config + isolated artifacts shared by every test in this module."""
    tmpdir = Path(tempfile.mkdtemp(prefix="cashflow-api-artifacts-"))
    mp = pytest.MonkeyPatch()
    mp.setenv("CASHFLOW_FAST_TEST", "1")
    mp.setenv("CASHFLOW_ARTIFACTS_DIR", str(tmpdir))
    cashflow_forecast.reset_forecast_service()
    yield tmpdir
    mp.undo()
    cashflow_forecast.reset_forecast_service()
    shutil.rmtree(tmpdir, ignore_errors=True)


def _seed_bank(db_client: TestClient) -> None:
    response = db_client.post("/api/v1/banks/seed-demo", headers=headers())
    assert response.status_code == 200, response.text


def test_invalid_horizon_mode_and_days_are_rejected_with_422(
    db_client: TestClient, ml_artifacts_dir: Path
) -> None:
    _seed_bank(db_client)

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
    assert list(ml_artifacts_dir.iterdir()) == []  # nothing trained


def test_unknown_or_cross_tenant_bank_returns_404_without_touching_the_model(
    db_client: TestClient, ml_artifacts_dir: Path
) -> None:
    _seed_bank(db_client)

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
    assert list(ml_artifacts_dir.iterdir()) == []  # nothing trained


def test_ml_runtime_import_failure_maps_to_503(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_bank(db_client)

    def _broken_import() -> object:
        raise ImportError("torch is unavailable")

    monkeypatch.setattr(cashflow_forecast, "_import_ml_model", _broken_import)

    response = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/cashflow-forecast", headers=headers())
    assert response.status_code == 503
    assert response.json()["error"]["message"] == ("Cash flow forecasting service is unavailable.")


def test_history_works_without_the_ml_runtime(
    db_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_bank(db_client)

    def _broken_import() -> object:
        raise ImportError("torch is unavailable")

    monkeypatch.setattr(cashflow_forecast, "_import_ml_model", _broken_import)

    response = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/cashflow-history", headers=headers())
    assert response.status_code == 200, response.text


def test_forecast_lstm_lazy_trains_and_returns_bands(
    db_client: TestClient, ml_artifacts_dir: Path
) -> None:
    _seed_bank(db_client)

    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/cashflow-forecast",
        headers=headers(),
        params={"horizon": 30, "mode": "lstm"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "lstm"
    assert body["horizon"] == 30
    assert body["asOfDate"] == AS_OF.isoformat()
    assert body["modelVersion"] == "lstm-v1.0.0"
    accuracy = body["accuracy"]
    assert accuracy["lstmMape"] < accuracy["staticMape"]
    assert accuracy["improvementPct"] > 0

    points = body["points"]
    assert [point["day"] for point in points] == list(range(1, 31))
    assert points[0]["date"] == (AS_OF + datetime.timedelta(days=1)).isoformat()
    assert points[-1]["date"] == (AS_OF + datetime.timedelta(days=30)).isoformat()
    for point in points:
        assert point["lower"] <= point["netFlow"] <= point["upper"]
    first_width = points[0]["upper"] - points[0]["lower"]
    last_width = points[-1]["upper"] - points[-1]["lower"]
    assert last_width > first_width > 0

    for name in ("model.pt", "scaler.json", "metrics.json"):
        assert (ml_artifacts_dir / name).exists()  # lazy training persisted artifacts


def test_forecast_static_has_degenerate_bands(db_client: TestClient) -> None:
    _seed_bank(db_client)

    response = db_client.get(
        f"/api/v1/banks/{SAMPLE_BANK_ID}/cashflow-forecast",
        headers=headers(),
        params={"horizon": 60, "mode": "static"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "static"
    assert body["horizon"] == 60
    points = body["points"]
    assert len(points) == 60
    for point in points:
        assert point["lower"] == point["netFlow"] == point["upper"]


def test_history_returns_trailing_points_with_default_days(db_client: TestClient) -> None:
    _seed_bank(db_client)

    response = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/cashflow-history", headers=headers())
    assert response.status_code == 200, response.text
    points = response.json()["points"]
    assert len(points) == 90  # default days=90
    assert points[-1]["date"] == AS_OF.isoformat()
    assert all("netFlow" in point for point in points)
    dates = [datetime.date.fromisoformat(point["date"]) for point in points]
    assert dates == sorted(dates)
