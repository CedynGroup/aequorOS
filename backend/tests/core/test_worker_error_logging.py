from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from typing import cast
from uuid import UUID

import pytest
from loguru import logger
from sqlalchemy.orm import Session

from app import worker
from app.core.logging import (
    configure_logging,
    get_request_id,
    reset_request_id,
    safe_request_id,
    set_request_id,
)
from app.core.observability import Condition
from app.liquidity.service import regulatory_liquidity
from app.models import Job
from tests.liquidity.helpers import inputs

_CUSTOMER_DATA = "Borrower Jane Private account 123456789012 balance 9876543.21 rate 13.75%"
_TENANT_ID = "OR-1234ABCD"
_JOB_ID = UUID("00000000-0000-4000-8000-123456789012")


@pytest.fixture
def configure_stderr_logging(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[], None]]:
    monkeypatch.setattr(logging.root, "handlers", list(logging.root.handlers))
    monkeypatch.setattr(logging.root, "level", logging.root.level)
    try:
        # Bind the sink during the call phase, after pytest swaps capture streams.
        yield lambda: configure_logging("INFO")
    finally:
        logger.remove()
        logger.configure(patcher=None)


_LOGURU_HEADER_TIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+ \| ")
_LOGURU_CLOCK_KEYS = ("time", "elapsed")


def _scannable(line: str) -> str:
    """Return the line's scan target with loguru's own clock metadata removed.

    The privacy property covers application content. Loguru's serialized
    envelope always carries clock fields the application does not control —
    ``record["elapsed"]`` is the process uptime (Risk Service CI run
    42942245063 rendered ``"0:03:13.751519"``, whose ``13.75`` coincided with
    the synthetic rate) — and ``record["time"]`` plus the rendered ``text``
    header embed the wall-clock timestamp. Any digit pattern can occur in
    those fields, so they are excluded from the scan while everything else
    (message, extra, exception, file, process/thread identity) stays scanned.
    """
    try:
        payload = cast(dict[str, object], json.loads(line))
    except json.JSONDecodeError:
        return _LOGURU_HEADER_TIME.sub("", line, count=1)
    record = payload.get("record")
    if isinstance(record, dict):
        for key in _LOGURU_CLOCK_KEYS:
            record.pop(key, None)
    text = payload.get("text")
    if isinstance(text, str):
        payload["text"] = _LOGURU_HEADER_TIME.sub("", text, count=1)
    return json.dumps(payload)


def _assert_private(output: str) -> None:
    scanned = "\n".join(_scannable(line) for line in output.splitlines())
    for secret in ("Jane Private", "123456789012", "9876543.21", "13.75", str(_JOB_ID)):
        assert secret not in scanned
    assert re.search(r"\b\d{12}\b", scanned) is None


def _loguru_envelope(message: str) -> str:
    """A serialized loguru line whose clock metadata embeds the digit pattern
    that collided in Risk Service CI run 42942245063 (``0:03:13.751519``)."""
    return json.dumps(
        {
            "text": f"2026-11-11 13:45:13.751 | ERROR    | app.worker:run_once:12 - {message}\n",
            "record": {
                "elapsed": {"repr": "0:03:13.751519", "seconds": 193.751519},
                "exception": None,
                "extra": {"error_code": "unexpected_error", "tenant_id": _TENANT_ID},
                "message": message,
                "time": {"repr": "2026-11-11 13:45:13.751519+00:00", "timestamp": 1.0},
            },
        }
    )


def test_assert_private_ignores_loguru_clock_metadata_containing_secret_digits() -> None:
    assert _assert_private(_loguru_envelope("Unexpected application error")) is None


def test_assert_private_still_rejects_customer_data_in_log_content() -> None:
    with pytest.raises(AssertionError):
        _assert_private(_loguru_envelope(_CUSTOMER_DATA))


@dataclass
class _Session:
    job: Job
    operations: list[str] = field(default_factory=list)
    retry_errors: list[str] = field(default_factory=list)

    def get(self, _model: object, _id: object) -> Job:
        return self.job

    def rollback(self) -> None:
        self.operations.append("rollback")

    def commit(self) -> None:
        self.operations.append("commit")


def _prepare_job(monkeypatch: pytest.MonkeyPatch, job_type: str) -> _Session:
    job = Job(id=_JOB_ID, organization_id=_TENANT_ID, job_type=job_type, status="running")
    session = _Session(job)

    def new_session(_tenant: str | None = None) -> AbstractContextManager[Session]:
        return nullcontext(cast(Session, session))

    def claim_next(
        _db: Session, _now: datetime, _types: tuple[str, ...], *, claimed_by: str
    ) -> Job:
        assert claimed_by == "synthetic-worker"
        return job

    def retry(_db: Session, claimed: Job, error: str, *, commit: bool) -> None:
        assert claimed is job and commit is False
        session.operations.append("retry")
        session.retry_errors.append(error)

    monkeypatch.setattr(worker, "_new_session", new_session)
    monkeypatch.setattr(worker.job_queue, "claim_next", claim_next)
    monkeypatch.setattr(worker.job_queue, "fail_with_retry", retry)
    return session


@pytest.mark.parametrize("job_type", ["official_run", "pipeline_refresh"])
@pytest.mark.parametrize("caller_id", [None, _CUSTOMER_DATA])
def test_worker_redacts_propagated_calculation_errors_and_preserves_retries(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    job_type: str,
    caller_id: str | None,
    configure_stderr_logging: Callable[[], None],
) -> None:
    configure_stderr_logging()
    session = _prepare_job(monkeypatch, job_type)
    facts, params = inputs()

    def broken_engine(_facts: object, _params: object) -> None:
        raise RuntimeError(_CUSTOMER_DATA)

    def handler(_db: Session, job: Job) -> None:
        _ = regulatory_liquidity.compute_liquidity_results(facts, params, job.organization_id)

    monkeypatch.setattr(regulatory_liquidity, "compute_liquidity", broken_engine)
    monkeypatch.setitem(worker.HANDLERS, job_type, handler)
    token = set_request_id(caller_id) if caller_id is not None else None
    previous = get_request_id()
    try:
        with logger.contextualize(
            borrower=_CUSTOMER_DATA, request_body={"account": _CUSTOMER_DATA}
        ):
            assert worker.run_once((job_type,), worker_id="synthetic-worker") is True
        assert get_request_id() == previous
    finally:
        if token is not None:
            reset_request_id(token)
    captured = capsys.readouterr()
    assert captured.out == ""
    _assert_private(captured.err)
    payloads = [cast(dict[str, object], json.loads(line)) for line in captured.err.splitlines()]
    assert [payload["condition"] for payload in payloads] == [
        Condition.CALCULATION_FAILED.value,
        Condition.WORKER_JOB_FAILED.value,
    ]
    assert all(payload["tenant_id"] == _TENANT_ID for payload in payloads)
    assert all(payload["reason_code"] == "unexpected_error" for payload in payloads)
    correlation = payloads[0]["request_id"]
    assert isinstance(correlation, str) and correlation.startswith("sha256:")
    assert payloads[1]["request_id"] == correlation
    if caller_id is not None:
        assert correlation == safe_request_id(caller_id)
    assert session.operations == ["rollback", "retry", "commit"]
    assert session.retry_errors == [_CUSTOMER_DATA]


def test_non_calculation_worker_errors_use_the_same_private_failure_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    configure_stderr_logging: Callable[[], None],
) -> None:
    configure_stderr_logging()
    session = _prepare_job(monkeypatch, "bi_mart_refresh")

    def handler(_db: Session, _job: Job) -> None:
        raise OSError(_CUSTOMER_DATA)

    monkeypatch.setitem(worker.HANDLERS, "bi_mart_refresh", handler)
    assert worker.run_once(("bi_mart_refresh",), worker_id="synthetic-worker") is True
    captured = capsys.readouterr()
    assert captured.out == ""
    _assert_private(captured.err)
    payload = cast(dict[str, object], json.loads(captured.err))
    assert payload["condition"] == Condition.WORKER_JOB_FAILED.value
    assert payload["tenant_id"] == _TENANT_ID
    assert set(payload) == {"condition", "severity", "reason_code", "tenant_id", "request_id"}
    assert session.operations == ["rollback", "retry", "commit"]
    assert session.retry_errors == [_CUSTOMER_DATA]
    assert get_request_id() == "-"


@pytest.mark.parametrize("source", ["standard", "loguru"])
@pytest.mark.parametrize("tenant", [_TENANT_ID, _CUSTOMER_DATA])
def test_unexpected_exception_records_scrub_messages_traces_and_bound_context(
    capsys: pytest.CaptureFixture[str],
    source: str,
    tenant: str,
    configure_stderr_logging: Callable[[], None],
) -> None:
    configure_stderr_logging()
    token = set_request_id(_CUSTOMER_DATA)
    try:
        with logger.contextualize(tenant_id=tenant, borrower=_CUSTOMER_DATA, amount="9876543.21"):
            try:
                raise RuntimeError(_CUSTOMER_DATA)
            except RuntimeError:
                if source == "standard":
                    logging.getLogger("uvicorn.error").exception(_CUSTOMER_DATA)
                else:
                    logger.exception(_CUSTOMER_DATA)
    finally:
        reset_request_id(token)
    captured = capsys.readouterr()
    assert captured.out == ""
    _assert_private(captured.err)
    payload = cast(dict[str, object], json.loads(captured.err))
    record = cast(dict[str, object], payload["record"])
    assert record["message"] == "Unexpected application error"
    assert record["exception"] is None
    assert record["extra"] == {
        "error_code": "unexpected_error",
        "tenant_id": tenant if tenant == _TENANT_ID else "unknown",
        "request_id": safe_request_id(_CUSTOMER_DATA),
    }


def test_stderr_write_failure_does_not_dump_the_active_sensitive_exception(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    configure_stderr_logging: Callable[[], None],
) -> None:
    destination = sys.stderr

    class FailsOnce:
        failed = False

        def write(self, message: str) -> int:
            if not self.failed:
                self.failed = True
                raise OSError("stderr unavailable")
            return destination.write(message)

        def flush(self) -> None:
            destination.flush()

    monkeypatch.setattr(sys, "stderr", FailsOnce())
    configure_stderr_logging()
    try:
        raise RuntimeError(_CUSTOMER_DATA)
    except RuntimeError:
        logger.exception(_CUSTOMER_DATA)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_background_exception_retains_safe_tenant_and_creates_a_correlation_id(
    capsys: pytest.CaptureFixture[str], configure_stderr_logging: Callable[[], None]
) -> None:
    configure_stderr_logging()
    assert get_request_id() == "-"
    try:
        raise RuntimeError(_CUSTOMER_DATA)
    except RuntimeError:
        logger.bind(organization_id=_TENANT_ID).exception(_CUSTOMER_DATA)
    captured = capsys.readouterr()
    _assert_private(captured.err)
    record = cast(dict[str, object], json.loads(captured.err)["record"])
    extra = cast(dict[str, object], record["extra"])
    assert extra["tenant_id"] == _TENANT_ID
    correlation = extra["request_id"]
    assert isinstance(correlation, str) and correlation.startswith("sha256:")
    assert get_request_id() == "-"
