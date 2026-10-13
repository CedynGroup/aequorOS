from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.authority.results import Computed, Refused


def test_a_computed_zero_is_a_value() -> None:
    match Computed(Decimal("0")):
        case Computed(value=value):
            assert value == Decimal("0")


def test_a_computed_figure_cannot_be_missing() -> None:
    with pytest.raises(ValueError, match="requires a value"):
        _ = Computed(None)


def test_a_refused_figure_has_no_value() -> None:
    result = Refused("missing_parameter", "BCBS 238", (1, 3))
    match result:
        case Refused(reason_code=code, rule_citation=citation, row_ref=rows):
            assert (code, citation, rows) == ("missing_parameter", "BCBS 238", (1, 3))


def test_a_refusal_requires_an_explanation() -> None:
    with pytest.raises(ValueError, match="requires a reason code"):
        _ = Refused("", "BCBS 238")


@pytest.mark.parametrize("position", [0, -1, True])
def test_row_references_are_one_based_positions(position: int) -> None:
    with pytest.raises(ValueError, match="positive one-based"):
        _ = Refused("missing_parameter", "BCBS 238", (position,))
