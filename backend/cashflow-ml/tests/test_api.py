"""API contract tests. Order matters: the untrained-health check runs first,
then a forecast call exercises lazy training, then /train retrains."""

from __future__ import annotations

import datetime

AS_OF = datetime.date(2026, 3, 31)


def test_health_reports_untrained_model(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["model_trained"] is False
    assert body["model_version"] is None


def test_forecast_rejects_invalid_horizon(client):
    assert client.get("/forecast", params={"horizon": 45}).status_code == 422
    assert client.get("/forecast", params={"horizon": "abc"}).status_code == 422


def test_forecast_rejects_invalid_mode(client):
    assert client.get("/forecast", params={"mode": "arima"}).status_code == 422


def test_history_rejects_invalid_days(client):
    assert client.get("/history", params={"days": 0}).status_code == 422
    assert client.get("/history", params={"days": 9999}).status_code == 422


def test_history_returns_trailing_points(client):
    response = client.get("/history", params={"days": 90})
    assert response.status_code == 200
    points = response.json()["points"]
    assert len(points) == 90
    assert points[-1]["date"] == AS_OF.isoformat()
    assert all("netFlow" in point for point in points)
    dates = [datetime.date.fromisoformat(point["date"]) for point in points]
    assert dates == sorted(dates)


def test_forecast_lstm_lazy_trains_and_returns_bands(client):
    response = client.get("/forecast", params={"horizon": 30, "mode": "lstm"})
    assert response.status_code == 200
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


def test_health_after_lazy_training(client):
    body = client.get("/health").json()
    assert body["model_trained"] is True
    assert body["model_version"] == "lstm-v1.0.0"


def test_train_endpoint_returns_metrics(client):
    response = client.post("/train")
    assert response.status_code == 200
    body = response.json()
    assert body["lstm_mape"] < body["static_mape"]
    assert body["improvement_pct"] > 0
    assert body["lstm_rmse"] > 0
    assert body["static_rmse"] > 0
    assert body["model_version"] == "lstm-v1.0.0"
    assert body["trained_at"]


def test_forecast_static_has_degenerate_bands(client):
    response = client.get("/forecast", params={"horizon": 60, "mode": "static"})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "static"
    points = body["points"]
    assert len(points) == 60
    for point in points:
        assert point["lower"] == point["netFlow"] == point["upper"]
