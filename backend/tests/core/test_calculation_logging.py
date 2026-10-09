from __future__ import annotations

import json
from typing import cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger

from app.core.errors import UnhandledExceptionMiddleware, register_exception_handlers
from app.core.logging import reset_request_id, set_request_id
from app.core.observability import Condition, emit

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
                row_ref=(123456789012,),
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
        "request_id": _REQUEST_ID,
        "reason_code": "missing_parameter",
        "rule_citation": "BCBS 238",
        "figure_id": "lcr_pct",
        "engine_version": "regulatory-liquidity-v2.0.0",
    }
    for secret in ("Jane Private", "123456789012", "9876543.21", "13.75", "BK-PRIVATE1"):
        assert secret not in captured.err


def test_allowlisted_field_names_cannot_smuggle_free_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = set_request_id(_CUSTOMER_DATA)
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


def test_http_boundary_does_not_relog_a_sensitive_propagated_exception() -> None:
    records: list[str] = []
    sink = logger.add(lambda message: records.append(str(message)), serialize=True, level="ERROR")
    app = FastAPI()
    app.add_middleware(UnhandledExceptionMiddleware)
    register_exception_handlers(app)

    def broken() -> None:
        raise RuntimeError(_CUSTOMER_DATA)

    app.add_api_route("/banks/BK-PRIVATE1", broken)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/banks/BK-PRIVATE1")
    finally:
        logger.remove(sink)
    assert response.status_code == 500
    assert len(records) == 1
    for secret in ("Jane Private", "123456789012", "9876543.21", "13.75", "BK-PRIVATE1"):
        assert secret not in records[0]
