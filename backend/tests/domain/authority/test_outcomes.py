"""Fail-closed outcome states (primitive P3)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.authority.outcomes import (
    BLOCKING_STATES,
    CalculationOutcome,
    NotComputable,
    OutcomeDetail,
    OutcomeState,
    Severity,
    outcome,
)


def test_all_five_declared_states_exist() -> None:
    assert {state.value for state in OutcomeState} == {
        "not_computable",
        "missing_required_input",
        "policy_unresolved",
        "data_quality_block",
        "reconciliation_failed",
    }


@pytest.mark.parametrize("state", list(OutcomeState))
def test_every_state_blocks_filing_by_default(state: OutcomeState) -> None:
    detail = outcome(state, metric_id="car_pct", reason="test")
    assert state in BLOCKING_STATES
    assert detail.blocks_filing is True
    assert detail.severity is Severity.BLOCKING


def test_advisory_occurrence_does_not_block_filing() -> None:
    detail = outcome(
        OutcomeState.NOT_COMPUTABLE,
        metric_id="portfolio_nim_pct",
        reason="No FTP product rows; dashboard tile only.",
        advisory=True,
    )
    assert detail.blocks_filing is False
    assert detail.severity is Severity.ADVISORY


def test_detail_carries_code_reason_and_specific_items() -> None:
    detail = outcome(
        OutcomeState.MISSING_REQUIRED_INPUT,
        metric_id="lcr_pct",
        reason="No HQLA facts for the reporting period.",
        items=("fact:hqla_level1", "fact:hqla_level2a"),
    )
    assert detail.code == "missing_required_input:lcr_pct"
    assert detail.items == ("fact:hqla_level1", "fact:hqla_level2a")
    assert "No HQLA facts" in detail.message
    assert "fact:hqla_level1" in detail.message


def test_detail_rejects_empty_metric_or_reason() -> None:
    with pytest.raises(ValueError, match="metric_id"):
        OutcomeDetail(state=OutcomeState.NOT_COMPUTABLE, metric_id="", reason="x")
    with pytest.raises(ValueError, match="reason"):
        OutcomeDetail(state=OutcomeState.NOT_COMPUTABLE, metric_id="car_pct", reason="")


def test_detail_round_trips_through_dict() -> None:
    detail = outcome(
        OutcomeState.POLICY_UNRESOLVED,
        metric_id="car_pct",
        reason="No car_min parameter for GH / SDI / 2026-06-30.",
        items=("param:car_min",),
        context={"jurisdiction": "GH", "as_of": "2026-06-30"},
    )
    payload = detail.to_dict()
    assert payload["blocks_filing"] is True
    assert payload["severity"] == "blocking"
    assert OutcomeDetail.from_dict(payload) == detail


def test_advisory_detail_round_trips_and_keeps_non_blocking() -> None:
    detail = outcome(
        OutcomeState.DATA_QUALITY_BLOCK,
        metric_id="nop_ghs",
        reason="Unmapped currency in FX positions.",
        items=("position:XXX",),
        advisory=True,
    )
    restored = OutcomeDetail.from_dict(detail.to_dict())
    assert restored == detail
    assert restored.blocks_filing is False


def test_computed_outcome_carries_value_and_is_ok() -> None:
    result = CalculationOutcome.computed(Decimal("134.2"), metric_id="lcr_pct")
    assert result.ok is True
    assert result.blocks_filing is False
    assert result.unwrap() == Decimal("134.2")


def test_blocked_outcome_has_no_value_and_raises_on_unwrap() -> None:
    result: CalculationOutcome[Decimal] = CalculationOutcome.blocked(
        OutcomeState.MISSING_REQUIRED_INPUT,
        metric_id="lcr_pct",
        reason="No HQLA facts ingested for the period.",
        items=("fact:hqla_level1",),
    )
    assert result.ok is False
    assert result.value is None
    assert result.blocks_filing is True
    assert result.codes == ("missing_required_input:lcr_pct",)
    with pytest.raises(NotComputable) as excinfo:
        result.unwrap()
    assert excinfo.value.state is OutcomeState.MISSING_REQUIRED_INPUT
    assert excinfo.value.blocks_filing is True


def test_outcome_cannot_hold_a_value_and_a_block_together() -> None:
    detail = outcome(OutcomeState.NOT_COMPUTABLE, metric_id="car_pct", reason="x")
    with pytest.raises(ValueError, match="cannot carry both"):
        CalculationOutcome(metric_id="car_pct", value=Decimal("1"), details=(detail,))


def test_computed_rejects_none_so_no_silent_fallback() -> None:
    with pytest.raises(ValueError, match="requires a value"):
        CalculationOutcome.computed(None, metric_id="car_pct")  # type: ignore[arg-type]


def test_merge_propagates_blocks_and_rejects_metric_mismatch() -> None:
    ok = CalculationOutcome.computed(Decimal("1"), metric_id="car_pct")
    blocked: CalculationOutcome[Decimal] = CalculationOutcome.blocked(
        OutcomeState.DATA_QUALITY_BLOCK,
        metric_id="car_pct",
        reason="RWA components do not reconcile to total.",
    )
    merged = ok.merge(blocked)
    assert merged.ok is False
    assert merged.blocks_filing is True

    other = CalculationOutcome.computed(Decimal("1"), metric_id="lcr_pct")
    with pytest.raises(ValueError, match="different metrics"):
        ok.merge(other)


def test_outcome_serialises_and_deserialises_reporting_blocks_filing() -> None:
    result: CalculationOutcome[Decimal] = CalculationOutcome.from_details(
        (
            outcome(
                OutcomeState.RECONCILIATION_FAILED,
                metric_id="lcr_pct",
                reason="BSD3 template cell disagrees with the liquidity run.",
                items=("cell:BSD3!G9", "run:liquidity"),
            ),
            outcome(
                OutcomeState.POLICY_UNRESOLVED,
                metric_id="lcr_pct",
                reason="No lcr_min threshold resolved.",
                items=("param:lcr_min",),
                advisory=True,
            ),
        ),
        metric_id="lcr_pct",
    )
    payload = result.to_dict()
    assert payload["ok"] is False
    assert payload["blocks_filing"] is True
    assert [d["blocks_filing"] for d in payload["details"]] == [True, False]

    restored = CalculationOutcome.from_dict(payload)
    assert restored.states == result.states
    assert restored.codes == result.codes
    assert restored.blocks_filing is True


def test_not_computable_exception_requires_at_least_one_detail() -> None:
    with pytest.raises(ValueError, match="at least one OutcomeDetail"):
        NotComputable()


def test_not_computable_exception_serialises_for_persistence() -> None:
    error = NotComputable(
        outcome(
            OutcomeState.POLICY_UNRESOLVED,
            metric_id="car_pct",
            reason="No car_min parameter for GH / SDI / 2026-06-30.",
            items=("param:car_min",),
        )
    )
    payload = error.to_dict()
    assert payload["error"] == "not_computable"
    assert payload["blocks_filing"] is True
    assert payload["details"][0]["code"] == "policy_unresolved:car_pct"
    assert "No car_min parameter" in str(error)
