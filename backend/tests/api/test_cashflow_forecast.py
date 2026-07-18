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

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.ml.config import TrainingConfig
from app.ml.synthetic import generate_daily_series
from app.services import cashflow_forecast
from app.services.sample_bank_seed import SAMPLE_BANK_ID
from tests.api.helpers import ORG_1, ORG_2, headers

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
    # The seed bank has no ingested historical_cashflows, so it is served the
    # generic bootstrap model — honestly labelled, never passed off as bank-specific.
    assert body["modelScope"] == "generic"
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

    # Generic-scope artifacts persist under the generic/ subdir (per-tenant models
    # live under {org}/{bank}/); lazy training wrote all three.
    for name in ("model.pt", "scaler.json", "metrics.json"):
        assert (ml_artifacts_dir / "generic" / name).exists()


def test_build_service_routes_bank_specific_vs_generic_with_isolated_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bank with enough of its own history trains a bank-specific model in a
    per-tenant artifact dir; a bank without falls back to the shared generic model.
    Proves the no-spillover routing without paying for LSTM training."""
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=None)
    total = TrainingConfig.from_settings(get_settings().cashflow).total_days

    bank_specific = uuid4()
    long_series = generate_daily_series(days=total + 30)  # enough own history
    monkeypatch.setattr(
        cashflow_forecast, "load_bank_daily_series", lambda _db, _ctx, _bid: long_series
    )
    service = cashflow_forecast._build_service(None, ctx, bank_specific)  # type: ignore[arg-type]
    assert service._scope == "bank_specific"
    assert service._series is not long_series  # trains on the recent window slice
    assert len(service._series) == total
    artifacts = str(service._artifacts_dir)
    assert str(ORG_1) in artifacts and str(bank_specific) in artifacts  # per-tenant path

    generic_bank = uuid4()
    monkeypatch.setattr(
        cashflow_forecast, "load_bank_daily_series", lambda _db, _ctx, _bid: long_series[:5]
    )
    generic = cashflow_forecast._build_service(None, ctx, generic_bank)  # type: ignore[arg-type]
    assert generic._scope == "generic"
    assert generic._artifacts_dir.name == "generic"
    # A generic bank's model dir carries neither tenant id — no spillover either way.
    assert str(generic_bank) not in str(generic._artifacts_dir)


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
