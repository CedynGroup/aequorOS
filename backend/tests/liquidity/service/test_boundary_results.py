from __future__ import annotations

import json
from dataclasses import replace
from typing import cast

import pytest

from app.core.logging import get_request_id, reset_request_id, set_request_id
from app.domain.authority.results import Computed, Refused
from app.services import regulatory_liquidity
from tests.liquidity.helpers import inputs


def test_boundary_keeps_successful_results() -> None:
    facts, params = inputs()
    lcr, nsfr = regulatory_liquidity.compute_liquidity_results(facts, params, "OR-1234ABCD")
    assert isinstance(lcr, Computed)
    assert isinstance(nsfr, Computed)
    assert lcr.value.lcr_pct == 1000
    assert nsfr.value.nsfr_pct == 190


def test_boundary_returns_and_logs_both_refusals(
    capsys: pytest.CaptureFixture[str],
) -> None:
    facts, params = inputs()
    token = set_request_id("867a57c5-5704-4525-bcae-77eb23fb9073")
    try:
        lcr, nsfr = regulatory_liquidity.compute_liquidity_results(
            facts, replace(params, outflow_rates={}, rsf_weights={}), "OR-1234ABCD"
        )
        assert isinstance(lcr, Refused)
        assert isinstance(nsfr, Refused)
        assert lcr.row_ref == (2,)
        assert nsfr.row_ref == (3,)
        payloads = [
            cast(dict[str, str], json.loads(line)) for line in capsys.readouterr().err.splitlines()
        ]
        assert [payload["figure_id"] for payload in payloads] == ["lcr_pct", "nsfr_pct"]
        assert all(payload["reason_code"] == "missing_parameter" for payload in payloads)
        assert all(payload["tenant_id"] == "OR-1234ABCD" for payload in payloads)
        assert all(
            payload["engine_version"] == regulatory_liquidity.ENGINE_VERSION for payload in payloads
        )
        assert {payload["request_id"] for payload in payloads} == {get_request_id()}
    finally:
        reset_request_id(token)


def test_worker_boundary_generates_one_id_for_both_refusals_and_restores_context(
    capsys: pytest.CaptureFixture[str],
) -> None:
    facts, params = inputs()
    assert get_request_id() == "-"
    _ = regulatory_liquidity.compute_liquidity_results(
        facts, replace(params, outflow_rates={}, rsf_weights={}), "OR-1234ABCD"
    )
    payloads = [
        cast(dict[str, str], json.loads(line)) for line in capsys.readouterr().err.splitlines()
    ]
    assert len(payloads) == 2
    assert payloads[0]["request_id"] == payloads[1]["request_id"]
    assert get_request_id() == "-"


def test_sensitive_refusal_explanation_is_bank_data_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "Borrower Jane Private account 123456789012 balance 9876543.21 rate 13.75%"
    facts, params = inputs()
    facts = (facts[0], replace(facts[1], category=secret), facts[2])
    lcr, _nsfr = regulatory_liquidity.compute_liquidity_results(facts, params, "OR-1234ABCD")
    assert isinstance(lcr, Refused)
    assert lcr.detail is not None
    assert secret in lcr.detail.reason
    output = capsys.readouterr().err
    payloads = [cast(dict[str, str], json.loads(line)) for line in output.splitlines()]
    assert len(payloads) == 2
    for value in ("Jane Private", "123456789012", "9876543.21", "13.75"):
        assert value not in output


def test_unexpected_error_is_logged_without_exception_message_or_locals(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "Borrower Jane Private account 123456789012 balance 9876543.21 rate 13.75%"
    error = RuntimeError(secret)

    def broken(_facts: object, _params: object) -> None:
        raise error

    monkeypatch.setattr(regulatory_liquidity, "compute_liquidity", broken)
    facts, params = inputs()
    with pytest.raises(RuntimeError) as caught:
        _ = regulatory_liquidity.compute_liquidity_results(facts, params, "OR-1234ABCD")
    assert caught.value is error
    output = capsys.readouterr().err
    payload = cast(dict[str, str], json.loads(output))
    assert payload["reason_code"] == "unexpected_error"
    assert payload["severity"] == "error"
    assert payload["engine_version"] == regulatory_liquidity.ENGINE_VERSION
    assert payload["tenant_id"] == "OR-1234ABCD"
    for value in ("Jane Private", "123456789012", "9876543.21", "13.75"):
        assert value not in output
