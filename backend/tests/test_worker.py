"""Worker dispatch wiring that does not require a live database.

This module's other-suite cases live in ``test_worker_integration.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app import worker
from app.db import session as session_module


def test_production_worker_requires_dedicated_database_url(monkeypatch: Any) -> None:
    settings = SimpleNamespace(
        app=SimpleNamespace(app_env="production"),
        worker=SimpleNamespace(worker_database_url=None),
        database=SimpleNamespace(database_url="postgresql://tenant-role"),
    )
    monkeypatch.setattr(session_module, "get_settings", lambda: settings)

    with pytest.raises(RuntimeError, match="WORKER_DATABASE_URL"):
        session_module.get_worker_sessionmaker()


def test_inprocess_worker_refuses_to_start_when_it_cannot_claim(monkeypatch: Any) -> None:
    """Raising inside the daemon thread would be as silent as the bug itself."""
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: SimpleNamespace(worker=SimpleNamespace(run_inprocess_worker=True)),
    )
    monkeypatch.setattr(
        worker,
        "assert_worker_database_access",
        lambda: (_ for _ in ()).throw(session_module.RlsBlindError("blind")),
    )
    started: list[str] = []
    monkeypatch.setattr(
        worker.threading,
        "Thread",
        lambda **_kwargs: started.append("thread") or SimpleNamespace(start=lambda: None),
    )

    with pytest.raises(session_module.RlsBlindError):
        worker.start_inprocess_worker()

    assert started == []


def test_inprocess_worker_never_selects_the_bi_lane(monkeypatch: Any) -> None:
    """The API process is pinned to the core lane whatever WORKER_JOB_TYPES says.

    A request handler's neighbour thread must never stream a bank's book into
    a mart, so the pin that keeps AI work out of the API keeps BI work out too.
    """
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: SimpleNamespace(worker=SimpleNamespace(run_inprocess_worker=True)),
    )
    monkeypatch.setattr(worker, "assert_worker_database_access", lambda: None)
    captured: dict[str, Any] = {}

    def fake_thread(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(start=lambda: None)

    monkeypatch.setattr(worker.threading, "Thread", fake_thread)

    worker.start_inprocess_worker()

    selected = captured["kwargs"]["job_types"]
    assert "bi_mart_refresh" not in selected
    assert "bi_mart_backfill" not in selected
    assert "bi_retention" not in selected
    assert "pipeline_refresh" in selected
