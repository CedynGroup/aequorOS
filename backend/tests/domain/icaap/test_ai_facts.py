"""Descriptors: the only things the model may say about a figure."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.icaap import ai_facts


def _limit(value: str, kind: str = "minimum", status: str = "confirmed") -> ai_facts.LimitInput:
    return ai_facts.LimitInput(value=Decimal(value), kind=kind, status=status)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "limit", "kind", "expected"),
    [
        ("15.0", "13.0", "minimum", "meets"),
        ("13.0", "13.0", "minimum", "at_limit"),
        ("11.0", "13.0", "minimum", "breaches"),
        ("8.0", "10.0", "maximum", "meets"),
        ("10.0", "10.0", "maximum", "at_limit"),
        ("12.0", "10.0", "maximum", "breaches"),
    ],
)
def test_vs_limit_truth_table(value: str, limit: str, kind: str, expected: str) -> None:
    result = ai_facts.compare_to_limit(Decimal(value), _limit(limit, kind))
    assert result.vs_limit == expected
    assert result.limit_kind == kind


def test_at_limit_is_not_meets() -> None:
    """A ratio exactly at its minimum has no headroom, and must not read as comfort."""
    assert ai_facts.compare_to_limit(Decimal("13"), _limit("13")).vs_limit == "at_limit"


def test_missing_parameter_is_not_assessed_never_a_fallback() -> None:
    """A narrative measured against a guessed minimum would be a false filing."""
    assert ai_facts.compare_to_limit(Decimal("15"), None).vs_limit == "not_assessed"
    assert ai_facts.compare_to_limit(None, _limit("13")).vs_limit == "not_assessed"


def test_limit_status_is_carried_through() -> None:
    result = ai_facts.compare_to_limit(Decimal("15"), _limit("13", status="pending_confirmation"))
    assert result.limit_status == "pending_confirmation"


def test_limit_kind_comes_from_the_governed_direction() -> None:
    assert ai_facts.limit_kind_for("floor") == "minimum"
    assert ai_facts.limit_kind_for("ceiling") == "maximum"
    assert ai_facts.limit_kind_for(None) is None
    assert ai_facts.limit_kind_for("unknown") is None


def test_prior_year_is_compared_at_display_precision() -> None:
    """ "Unchanged" means "the report prints the same number", not a tolerance."""
    quantum = Decimal("0.01")
    assert (
        ai_facts.compare_to_prior_year(Decimal("15.004"), Decimal("15.001"), quantum=quantum)
        == "unchanged"
    )
    assert (
        ai_facts.compare_to_prior_year(Decimal("15.02"), Decimal("15.00"), quantum=quantum)
        == "higher"
    )
    assert (
        ai_facts.compare_to_prior_year(Decimal("14.98"), Decimal("15.00"), quantum=quantum)
        == "lower"
    )


def test_prior_year_without_a_sealed_cycle_is_not_assessed() -> None:
    assert ai_facts.compare_to_prior_year(Decimal("15"), None, quantum=None) == "not_assessed"


@pytest.mark.parametrize(
    ("series", "expected"),
    [
        (["1", "2", "3"], "rising"),
        (["3", "2", "1"], "falling"),
        (["1", "3", "2"], "mixed"),
        (["2", "2", "2"], "flat"),
        (["2"], "not_assessed"),
        ([], "not_assessed"),
    ],
)
def test_trajectory(series: list[str], expected: str) -> None:
    values = [Decimal(item) for item in series]
    assert ai_facts.trajectory_of(values) == expected


def test_boolean_flag() -> None:
    assert ai_facts.boolean_flag("true") == "yes"
    assert ai_facts.boolean_flag("False") == "no"
    assert ai_facts.boolean_flag("maybe") is None
    assert ai_facts.boolean_flag(None) is None


def test_amounts_and_dates_never_carry_a_value_in_either_mode() -> None:
    """Absolute size and a reporting date identify an institution on their own."""
    for kind in ("amount", "date"):
        assert not ai_facts.value_is_sendable(kind, descriptor_only=False)
        assert not ai_facts.value_is_sendable(kind, descriptor_only=True)


def test_descriptor_only_strips_every_value() -> None:
    for kind in ai_facts.VALUE_BEARING_KINDS:
        assert ai_facts.value_is_sendable(kind, descriptor_only=False)
        assert not ai_facts.value_is_sendable(kind, descriptor_only=True)


def test_text_values_are_allow_listed_not_shape_checked() -> None:
    """A free-text field that happens to look like a code is still free text."""
    assert ai_facts.text_value_is_sendable("scenario_code", "severe_adverse")
    assert not ai_facts.text_value_is_sendable("commentary", "severe_adverse")
    assert not ai_facts.text_value_is_sendable("scenario_code", "Severe Adverse")
    assert not ai_facts.text_value_is_sendable("scenario_code", None)


def test_descriptors_serialise_to_a_closed_vocabulary() -> None:
    payload = ai_facts.Descriptors(
        vs_limit="meets", limit_kind="minimum", limit_status="confirmed", vs_prior_year="higher"
    ).as_dict()
    assert payload == {
        "vs_limit": "meets",
        "limit_kind": "minimum",
        "limit_status": "confirmed",
        "vs_prior_year": "higher",
        "vs_appetite": "not_assessed",
    }


def test_boolean_descriptors_carry_only_the_flag() -> None:
    assert ai_facts.Descriptors(flag="no").as_dict() == {"flag": "no"}


def test_limit_params_map_names_codes_only() -> None:
    """D-024: a governed limit is referenced by CODE; no value lives here.

    ``cet1_min`` legitimately contains a digit in its name — the rule is that
    the map holds parameter identifiers the control plane resolves, never a
    number somebody typed, so nothing here may look like a threshold value.
    """
    for code in ai_facts.LIMIT_PARAMS.values():
        assert code.replace("_", "").isalnum()
        assert "." not in code
        assert not code.strip().replace(".", "").isdigit()
