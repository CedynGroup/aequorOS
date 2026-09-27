"""Every catalogue value type must be named by the insights unit maps.

Audit finding A8-03. The insights layer is the FOURTH consumer that switches on
a value type, and like the other three it does not reject an unknown one: it
falls through to no scale and no unit. So a measure held as a proportion of one
rendered as a bare ``0.061`` in the same strip as its percentage twin reading
``6.1 %`` — one quantity, two figures, neither marked — and a duration rendered
as ``2.35`` with nothing saying years.

The maps are small enough to enumerate and the units genuinely differ per type,
so completeness is asserted here rather than derived, in both directions: a type
the catalogue declares and the maps do not know is a silent wrong rendering, and
a key naming a type the catalogue retired is dead code that reads as coverage.
"""

from __future__ import annotations

from decimal import Decimal
from typing import get_args

from app.domain.bi.catalogue.members import NUMERIC_VALUE_TYPES, ValueType
from app.services.bi.insights.rules import _CHANGE_SUFFIX
from app.services.bi.insights.statements import _STATED_AS, render_value, stated_as


def test_every_numeric_value_type_says_how_it_is_stated() -> None:
    missing = sorted(t for t in NUMERIC_VALUE_TYPES if t not in _STATED_AS)
    assert not missing, (
        "these catalogue value types have no scale or unit, so an insight renders "
        f"them as a bare number with nothing naming it: {missing}"
    )


def test_every_numeric_value_type_says_how_its_CHANGE_is_named() -> None:
    missing = sorted(t for t in NUMERIC_VALUE_TYPES if t not in _CHANGE_SUFFIX)
    assert not missing, (
        "these catalogue value types have no change unit, so a movement sentence "
        f"states a difference with nothing naming it: {missing}"
    )


def test_neither_map_names_a_value_type_the_catalogue_retired() -> None:
    declared = set(get_args(ValueType))
    for name, keys in (("_STATED_AS", set(_STATED_AS)), ("_CHANGE_SUFFIX", set(_CHANGE_SUFFIX))):
        stale = sorted(keys - declared)
        assert not stale, f"{name} names types the catalogue does not declare: {stale}"


def test_a_proportion_of_one_and_a_percentage_render_the_same_quantity_alike() -> None:
    """The defect in one line: both are 6.1 %, so both must read 6.1 %."""
    assert render_value(Decimal("6.1"), "pct") == "6.1 %"
    assert render_value(Decimal("0.061"), "fraction") == "6.1 %"


def test_a_duration_is_named_in_years_and_is_not_scaled() -> None:
    assert render_value(Decimal("2.35"), "duration_years") == "2.35 years"
    assert stated_as("duration_years")[0] == Decimal(1)


def test_an_index_and_a_count_are_bare_because_neither_has_a_unit() -> None:
    """Inventing a unit is a claim about the number, so silence is correct here."""
    assert render_value(Decimal("0.184"), "index") == "0.184"
    assert render_value(Decimal("42"), "count") == "42"


def test_a_non_numeric_type_is_the_only_case_where_no_unit_is_right() -> None:
    for value_type in ("text", "date", "flag"):
        assert stated_as(value_type) == (Decimal(1), ""), value_type
