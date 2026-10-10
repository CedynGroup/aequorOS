from __future__ import annotations

import json
import logging
from typing import cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger

from app.core.errors import UnhandledExceptionMiddleware, register_exception_handlers
from app.core.logging import configure_logging, reset_request_id, safe_request_id, set_request_id
from app.core.observability import Condition, emit
from app.core.request_id import RequestIdMiddleware

_CUSTOMER_DATA = "Borrower Jane Private account 123456789012 balance 9876543.21 rate 13.75%"
_REQUEST_ID = "867a57c5-5704-4525-bcae-77eb23fb9073"


@pytest.mark.parametrize(
    "condition",
    [Condition.CALCULATION_FAILED, Condition.CALCULATION_BLOCKED, Condition.REGULATORY_RUN_FAILED],
)
def test_calculation_events_allow_only_codes_and_safe_context(
    capsys: pytest.CaptureFixture[str], condition: Condition
) -> None:
    token = set_request_id(_REQUEST_ID)
    try:
        with logger.contextualize(borrower=_CUSTOMER_DATA):
            emit(
                condition,
                _CUSTOMER_DATA,
                tenant_id="OR-1234ABCD",
                reason_code="missing_parameter",
                rule_citation="BCBS 238",
                figure_id="lcr_pct",
                engine_version="regulatory-liquidity-v2.0.0",
                bank_id="BK-PRIVATE1",
                row_ref=(2, 3),
                row_count=3,
                exception=RuntimeError(_CUSTOMER_DATA),
                detail={"nested": _CUSTOMER_DATA},
                amount="9876543.21",
                account_number="123456789012",
                rate="13.75",
                customer_name="Jane Private",
                request_body=_CUSTOMER_DATA,
            )
    finally:
        reset_request_id(token)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "condition": condition.value,
        "severity": "warning",
        "tenant_id": "OR-1234ABCD",
        "request_id": safe_request_id(_REQUEST_ID),
        "reason_code": "missing_parameter",
        "rule_citation": "BCBS 238",
        "figure_id": "lcr_pct",
        "engine_version": "regulatory-liquidity-v2.0.0",
        "row_ref": [2, 3],
    }
    for secret in ("Jane Private", "123456789012", "9876543.21", "13.75", "BK-PRIVATE1"):
        assert secret not in captured.err


@pytest.mark.parametrize(
    "row_ref, row_count",
    [
        ((0,), 3),
        ((-1,), 3),
        ((4,), 3),
        ((True,), 3),
        ((2.0,), 3),
        (("2",), 3),
        ((2, _CUSTOMER_DATA), 3),
        ((123456789012,), 3),
        ((9876543,), 3),
        ((13.75,), 3),
        (("00000000-0000-4000-8000-123456789012",), 3),
        ([2], 3),
        ({"row": _CUSTOMER_DATA}, 3),
        (_CUSTOMER_DATA, 3),
        ((2,), None),
        ((2,), _CUSTOMER_DATA),
        ((2,), True),
        ((2,), 3.0),
        ((2,), -1),
    ],
)
def test_invalid_row_locators_are_dropped_without_customer_content(
    capsys: pytest.CaptureFixture[str], row_ref: object, row_count: object
) -> None:
    emit(
        Condition.CALCULATION_BLOCKED,
        _CUSTOMER_DATA,
        reason_code="missing_parameter",
        row_ref=row_ref,
        row_count=row_count,
    )
    output = capsys.readouterr().err
    payload = cast(dict[str, object], json.loads(output))
    assert payload["reason_code"] == "missing_parameter"
    assert "row_ref" not in payload and "row_count" not in payload
    for secret in ("Jane Private", "123456789012", "9876543", "13.75"):
        assert secret not in output


def test_empty_internal_row_positions_do_not_invent_an_affected_row(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit(Condition.CALCULATION_BLOCKED, "refused", row_ref=(), row_count=0)
    payload = cast(dict[str, object], json.loads(capsys.readouterr().err))
    assert payload["row_ref"] == []
    assert "row_count" not in payload


@pytest.mark.parametrize("caller_id", [_CUSTOMER_DATA, "00000000-0000-4000-8000-123456789012"])
def test_allowlisted_field_names_cannot_smuggle_free_text(
    capsys: pytest.CaptureFixture[str],
    caller_id: str,
) -> None:
    token = set_request_id(caller_id)
    try:
        emit(
            Condition.CALCULATION_FAILED,
            _CUSTOMER_DATA,
            tenant_id=_CUSTOMER_DATA,
            reason_code=_CUSTOMER_DATA,
            rule_citation=_CUSTOMER_DATA,
            figure_id=_CUSTOMER_DATA,
            engine_version=_CUSTOMER_DATA,
        )
    finally:
        reset_request_id(token)
    captured = capsys.readouterr()
    payload = cast(dict[str, str], json.loads(captured.err))
    assert payload["tenant_id"] == "unknown"
    assert payload["reason_code"] == "unspecified"
    assert payload["request_id"].startswith("sha256:")
    assert set(payload) == {"condition", "severity", "tenant_id", "reason_code", "request_id"}
    for secret in ("Jane Private", "123456789012", "9876543.21", "13.75"):
        assert secret not in captured.err


def test_no_request_context_still_has_a_correlation_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit(Condition.CALCULATION_FAILED, "failure", reason_code="unexpected_error")
    payload = cast(dict[str, str], json.loads(capsys.readouterr().err))
    assert str(UUID(payload["request_id"])) == payload["request_id"]


def test_calculation_logging_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenStderr:
        def write(self, _value: str) -> None:
            raise OSError("stderr unavailable")

    monkeypatch.setattr("app.core.observability.sys.stderr", BrokenStderr())
    emit(Condition.CALCULATION_FAILED, "failure", reason_code="unexpected_error")


@pytest.mark.parametrize("caller_id", [_CUSTOMER_DATA, "00000000-0000-4000-8000-123456789012"])
@pytest.mark.parametrize("emit_first", [False, True])
def test_http_boundary_does_not_relog_a_sensitive_propagated_exception(
    capsys: pytest.CaptureFixture[str],
    caller_id: str,
    monkeypatch: pytest.MonkeyPatch,
    emit_first: bool,
) -> None:
    monkeypatch.setattr(logging.root, "handlers", list(logging.root.handlers))
    configure_logging("INFO")
    app = FastAPI()
    app.add_middleware(UnhandledExceptionMiddleware)
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)

    def broken() -> None:
        if emit_first:
            emit(Condition.CALCULATION_FAILED, "failure", reason_code="unexpected_error")
        raise RuntimeError(_CUSTOMER_DATA)

    app.add_api_route("/banks/BK-PRIVATE1", broken)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/banks/BK-PRIVATE1", headers={"X-Request-ID": caller_id})
    finally:
        logger.remove()
        logger.configure(patcher=None)
    assert response.status_code == 500
    captured = capsys.readouterr()
    assert captured.out == ""
    payloads = [cast(dict[str, object], json.loads(line)) for line in captured.err.splitlines()]
    calculations = [payload for payload in payloads if "condition" in payload]
    assert len(calculations) == int(emit_first)
    log_records = [
        cast(dict[str, object], payload["record"])
        for payload in payloads
        if "record" in payload
        and cast(dict[str, object], payload["record"])["message"]
        in ("Unhandled exception while processing request", "Request completed")
    ]
    extras = [record["extra"] for record in log_records]
    assert len(log_records) == 2
    request_id = safe_request_id(caller_id)
    assert isinstance(request_id, str)
    assert request_id.startswith("sha256:")
    assert all(cast(dict[str, object], extra)["request_id"] == request_id for extra in extras)
    assert any(record["message"] == "Request completed" for record in log_records)
    for secret in ("Jane Private", "123456789012", "9876543.21", "13.75", "BK-PRIVATE1"):
        error_records = [
            record for record in log_records if record["message"] != "Request completed"
        ]
        assert secret not in json.dumps(error_records)
        if secret != "BK-PRIVATE1":
            assert secret not in captured.err


@pytest.mark.parametrize("caller_id", [_CUSTOMER_DATA, "00000000-0000-4000-8000-123456789012"])
def test_bound_and_intercepted_correlation_ids_use_the_shared_safe_representation(
    capsys: pytest.CaptureFixture[str],
    caller_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(logging.root, "handlers", list(logging.root.handlers))
    configure_logging("INFO")
    token = set_request_id(caller_id)
    try:
        logger.bind(request_id=caller_id).error("Safe error code")
        logging.getLogger("correlation-test").error("Safe error code")
    finally:
        reset_request_id(token)
        logger.remove()
        logger.configure(patcher=None)
    captured = capsys.readouterr()
    assert captured.out == ""
    payloads = [cast(dict[str, object], json.loads(line)) for line in captured.err.splitlines()]
    records = [cast(dict[str, object], payload["record"]) for payload in payloads]
    ids = [cast(dict[str, object], record["extra"])["request_id"] for record in records]
    assert len(records) == 2
    assert ids[0] == ids[1]
    assert isinstance(ids[0], str)
    assert ids[0].startswith("sha256:")
    for secret in ("Jane Private", "123456789012", "9876543.21", "13.75"):
        assert secret not in captured.err
