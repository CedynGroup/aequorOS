"""Worker dispatch wiring that does not require a live database."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from app import worker


def test_run_once_passes_runtime_identity_to_job_claim(monkeypatch: Any) -> None:
    claimed_by: list[str | None] = []

    def claim_next(_db: object, _now: object, _types: object, *, claimed_by: str | None) -> None:
        claimed_by_values.append(claimed_by)
        return None

    claimed_by_values = claimed_by
    monkeypatch.setattr(worker, "_new_session", lambda *_args: nullcontext(object()))
    monkeypatch.setattr(worker.job_queue, "claim_next", claim_next)

    assert worker.run_once(("pipeline_refresh",), worker_id="risk-worker:test-blue") is False
    assert claimed_by_values == ["risk-worker:test-blue"]