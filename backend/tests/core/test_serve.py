"""How the API and staff listeners start uvicorn."""

from __future__ import annotations

import sys

import pytest

from app.core import serve


@pytest.mark.parametrize(("service", "workers"), [("api", 4), ("operator", 1)])
def test_workers_get_time_to_start_on_a_busy_host(
    service: str, workers: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uvicorn's 5 s default killed every API worker in a loop on a two-core host."""
    calls: list[dict[str, object]] = []

    def run(app: str, **options: object) -> None:
        calls.append({"app": app, **options})

    monkeypatch.setattr(serve, "listener_options", lambda: {"proxy_headers": False})
    monkeypatch.setattr(serve.uvicorn, "run", run)
    monkeypatch.setattr(sys, "argv", ["serve", service])
    monkeypatch.setenv("WEB_CONCURRENCY", "4")

    serve.main()

    assert len(calls) == 1
    assert calls[0]["workers"] == workers
    assert calls[0]["timeout_worker_healthcheck"] == serve.WORKER_PING_TIMEOUT_SECONDS
    assert serve.WORKER_PING_TIMEOUT_SECONDS >= 30
