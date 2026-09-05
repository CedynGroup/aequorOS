"""The observability vocabulary: stable codes, safe fields, and never raising."""

from __future__ import annotations

from typing import Any

import pytest
from loguru import logger

from app.core.observability import (
    CONDITION_SOURCES,
    Condition,
    auth_anomaly,
    authorization_binding_decision,
    authorization_denied,
    cross_tenant_attempt,
    emit,
    package_failed,
    ssrf_blocked,
)


@pytest.fixture
def captured() -> Any:
    """Collect loguru records emitted during a test."""
    records: list[dict[str, Any]] = []
    sink_id = logger.add(lambda message: records.append(dict(message.record)), level="DEBUG")
    yield records
    logger.remove(sink_id)


class TestVocabulary:
    def test_every_condition_has_a_documented_authoritative_source(self) -> None:
        """A condition with no recorded source is one nobody can trace in an incident."""
        assert set(CONDITION_SOURCES) == set(Condition)

    def test_condition_values_are_dotted_and_lowercase(self) -> None:
        for condition in Condition:
            assert condition.value == condition.value.lower()
            assert "." in condition.value

    def test_condition_values_are_unique(self) -> None:
        values = [c.value for c in Condition]
        assert len(values) == len(set(values))

    def test_no_condition_is_declared_without_being_reported(self) -> None:
        """Declaring the vocabulary is useful; implying coverage it lacks is not.

        The convention stands: a condition nothing emits must say ``NOT EMITTED``
        rather than be described vaguely. As of 2026-08-22 no entry needs it —
        ``reporting.package_failed`` was the last gap and is now emitted from the
        single package-mint site, so this asserts the stronger property: every
        condition in the vocabulary has a live reporter.
        """
        unwired = [c.value for c, src in CONDITION_SOURCES.items() if src.startswith("NOT EMITTED")]
        assert unwired == []

    def test_package_failure_names_its_emitter_not_a_table(self) -> None:
        """A refused package writes no audit event and no row on purpose — no
        package exists to attach one to — so the log line must be named as the
        record, never implied to be a table lookup."""
        source = CONDITION_SOURCES[Condition.PACKAGE_FAILED]
        assert "generation.py::generate_package" in source


class TestEmit:
    def test_condition_and_severity_are_structured_fields_not_prose(
        self, captured: list[dict[str, Any]]
    ) -> None:
        emit(Condition.STORAGE_FAILED, "Storage unreachable", severity="error", bucket="raw")
        assert len(captured) == 1
        extra = captured[0]["extra"]
        assert extra["condition"] == "storage.failed"
        assert extra["severity"] == "error"
        assert extra["bucket"] == "raw"

    def test_severity_maps_to_the_log_level(self, captured: list[dict[str, Any]]) -> None:
        emit(Condition.DATA_MISSING, "gap", severity="error")
        emit(Condition.DATA_MISSING, "gap", severity="warning")
        emit(Condition.DATA_MISSING, "gap", severity="info")
        assert [r["level"].name for r in captured] == ["ERROR", "WARNING", "INFO"]

    def test_unknown_severity_falls_back_to_warning(self, captured: list[dict[str, Any]]) -> None:
        emit(Condition.DATA_MISSING, "gap", severity="bogus")
        assert captured[0]["level"].name == "WARNING"

    def test_none_valued_fields_are_dropped(self, captured: list[dict[str, Any]]) -> None:
        emit(Condition.DATA_MISSING, "gap", present="yes", absent=None)
        assert "absent" not in captured[0]["extra"]
        assert captured[0]["extra"]["present"] == "yes"

    @pytest.mark.parametrize(
        "dangerous", ["password", "secret", "token", "api_key", "database_url", "PASSWORD"]
    )
    def test_credential_shaped_fields_never_reach_the_log(
        self, captured: list[dict[str, Any]], dangerous: str
    ) -> None:
        emit(Condition.AUTH_ANOMALY, "anomaly", **{dangerous: "swordfish"})
        serialized = str(captured[0]["extra"])
        assert "swordfish" not in serialized

    def test_long_values_are_truncated(self, captured: list[dict[str, Any]]) -> None:
        emit(Condition.DATA_MISSING, "gap", blob="x" * 5000)
        assert len(captured[0]["extra"]["blob"]) < 600
        assert captured[0]["extra"]["blob"].endswith("...[truncated]")

    def test_emit_never_raises_even_on_an_unserialisable_field(self) -> None:
        """An observability bug must not turn a clean 403 into a 500."""

        class Explosive:
            def __repr__(self) -> str:
                raise RuntimeError("boom")

        emit(Condition.AUTHORIZATION_DENIED, "denied", thing=Explosive())


class TestHelpers:
    def test_authorization_denied_carries_the_reason_code(
        self, captured: list[dict[str, Any]]
    ) -> None:
        authorization_denied(reason="insufficient_role", required_role="approver")
        extra = captured[0]["extra"]
        assert extra["condition"] == Condition.AUTHORIZATION_DENIED.value
        assert extra["reason"] == "insufficient_role"
        assert extra["required_role"] == "approver"

    def test_cross_tenant_attempt_is_an_error_not_a_warning(
        self, captured: list[dict[str, Any]]
    ) -> None:
        cross_tenant_attempt(reason="organization_not_visible", organization_id="OR-X")
        assert captured[0]["extra"]["severity"] == "error"

    def test_authorization_binding_decision_records_the_enforcing_outcome(
        self, captured: list[dict[str, Any]]
    ) -> None:
        authorization_binding_decision(
            allowed=False,
            reason="no_active_exact_binding",
            institution_id="BK-SAMP0001",
        )
        extra = captured[0]["extra"]
        assert extra["condition"] == Condition.AUTHORIZATION_BINDING_DECISION.value
        assert extra["severity"] == "info"
        assert extra["allowed"] is False
        assert extra["reason"] == "no_active_exact_binding"

    def test_auth_anomaly_uses_the_auth_condition(self, captured: list[dict[str, Any]]) -> None:
        auth_anomaly(reason="account_locked_after_repeated_failures", user_id="u1")
        assert captured[0]["extra"]["condition"] == Condition.AUTH_ANOMALY.value

    def test_package_failed_carries_the_return_and_the_refusal_code(
        self, captured: list[dict[str, Any]]
    ) -> None:
        package_failed(
            reason="template_pending",
            status_code=409,
            return_code="BSD-MONTHLY",
            reporting_date="2026-06-30",
        )
        extra = captured[0]["extra"]
        assert extra["condition"] == Condition.PACKAGE_FAILED.value
        assert extra["reason"] == "template_pending"
        assert extra["return_code"] == "BSD-MONTHLY"
        assert extra["severity"] == "warning"

    def test_package_failed_can_be_raised_to_error_for_an_unexpected_failure(
        self, captured: list[dict[str, Any]]
    ) -> None:
        package_failed(reason="unhandled_exception", severity="error", return_code="LMT")
        assert captured[0]["extra"]["severity"] == "error"
        assert captured[0]["level"].name == "ERROR"

    def test_ssrf_blocked_records_the_field_and_reason(
        self, captured: list[dict[str, Any]]
    ) -> None:
        ssrf_blocked(reason="loopback", field="endpoint", target="127.0.0.1")
        extra = captured[0]["extra"]
        assert extra["condition"] == Condition.SSRF_BLOCKED.value
        assert (extra["reason"], extra["field"]) == ("loopback", "endpoint")
