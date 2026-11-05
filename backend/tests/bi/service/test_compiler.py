"""The BI compiler, executed against a small hand-built mart on SQLite.

The fixture below is the whole of the arithmetic: four loans (one unconverted
foreign-currency), one deposit, three dates, two branches, two obligors, three
loan events and two engine copies. Every expected number in this file is
derived by hand from those rows, so a test that fails names the rule that
broke — a ratio of sums, a NULL denominator, the month-end scoping of a stock
measure — rather than a golden that drifted.

The aggregate table is built HERE from the same fact rows by the definition in
``bi_contracts.md`` (additive sums per grain), so the "aggregate on / off give
the same numbers" proof is a proof about the compiler, not about the builder.

This module's other-suite cases live in ``test_compiler_integration.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.bi.domain.catalogue import Catalogue
from app.bi.schemas.query import (
    MEMBER_ID_MAX_LENGTH,
    BiDateRange,
    BiPivot,
    BiQuery,
    BiSort,
    BiTime,
    BiTopN,
)
from app.bi.service import compiler
from app.bi.service.errors import is_member_id
from tests.bi.service.test_compiler_integration import (
    _D042_CASES,
    _HOSTILE_ID,
    AUG_31,
    SEP_15,
    SEP_18,
    _query,
)
from tests.bi.service.test_compiler_integration import (
    cat as cat,  # noqa: PLC0414 - shared with the split module
)


@pytest.mark.parametrize(
    "flt",
    [
        {"member": "branch.code", "op": "in", "values": [f"B{i}" for i in range(501)]},
        {"member": "branch.code", "op": "in", "values": []},
        {"member": "branch.code", "op": "between", "values": ["A"]},
        {"member": "branch.code", "op": "is_null", "values": ["A"]},
        {"member": "branch.code", "op": "eq", "values": ["A", "B"]},
        {"member": "branch.code", "op": "like", "values": ["A"]},
        {"member": "branch.code", "op": "eq", "values": ["A"], "sql": "1=1"},
    ],
)
def test_filter_arity_and_operator_whitelist_are_pydantic_errors(flt: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _query(filters=[flt])


def test_time_needs_exactly_one_window() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        BiTime()
    with pytest.raises(ValidationError, match="exactly one"):
        BiTime(as_of=SEP_18, range=BiDateRange(start=AUG_31, end=SEP_18))


def test_a_range_must_start_on_or_before_it_ends() -> None:
    """The ordering rule belongs to the range itself, so it is refused at
    construction and the message names the field that is wrong."""
    with pytest.raises(ValidationError, match="start must be on or before end"):
        BiDateRange(start=SEP_18, end=AUG_31)
    with pytest.raises(ValidationError, match="start must be on or before end"):
        BiTime.model_validate({"range": {"start": SEP_18, "end": AUG_31}})
    same_day = BiDateRange(start=SEP_18, end=SEP_18)
    assert (same_day.start, same_day.end) == (SEP_18, SEP_18)


def test_a_range_is_named_at_both_ends_on_the_wire() -> None:
    """``start``/``end``, never a positional pair: a client reading
    ``["2026-08-31", "2026-09-18"]`` would have to know which end is which, and
    the tuple form was also untypeable by the generated client (it rendered as
    an OpenAPI array with ``prefixItems`` and no ``items``)."""
    window = BiTime.model_validate({"range": {"start": AUG_31, "end": SEP_18}})
    assert window.range is not None
    assert (window.range.start, window.range.end) == (AUG_31, SEP_18)
    assert window.model_dump(mode="json")["range"] == {
        "start": AUG_31.isoformat(),
        "end": SEP_18.isoformat(),
    }
    for positional in ([AUG_31, SEP_18], (AUG_31, SEP_18)):
        with pytest.raises(ValidationError):
            BiTime.model_validate({"range": positional})
    with pytest.raises(ValidationError):
        BiTime.model_validate({"range": {"start": AUG_31, "end": SEP_18, "middle": SEP_15}})


def test_query_shape_is_closed_and_bounded() -> None:
    with pytest.raises(ValidationError):
        BiQuery.model_validate(
            {"measures": ["loans.balance_rc"], "time": {"as_of": SEP_18}, "raw_sql": "x"}
        )
    with pytest.raises(ValidationError):
        BiQuery.model_validate({"measures": [], "time": {"as_of": SEP_18}})
    with pytest.raises(ValidationError):
        BiTopN(dimension="loan.sector", n=51)
    with pytest.raises(ValidationError):
        BiPivot(dimension="loan.sector", max_columns=0)
    with pytest.raises(ValidationError):
        BiSort.model_validate({"member": "loan.sector", "direction": "sideways"})
    with pytest.raises(ValidationError):
        _query(limit=0)


#: Measures that select on a column which CANNOT be missing, or on one the daily
#: aggregate pre-computes as its own column (so the question is unanswerable on
#: that source and asking it on the fact alone would make the two disagree).
#: The NPL pair is the second kind, and R1 reconciles it to the engine instead.
_D042_EXEMPT: frozenset[str] = frozenset(
    {
        "loans.npl_exposure_rc",
        "loans.specific_provision_held_rc",
        "loans.unconverted_count",
        "deposits.unconverted_count",
        "positions.unconverted_count",
        "events.disbursement_rc",
        "events.repayment_rc",
        "events.write_off_rc",
        "events.recovery_rc",
        "events.unconverted_count",
        "events.unattributed_count",
    }
)


def _selecting_measures(cat: Catalogue) -> dict[str, list[str]]:
    """Every measure with a selection filter → the columns it selects on."""
    population = compiler._POPULATION_COLUMNS
    out: dict[str, list[str]] = {}
    for measure in cat.measures():
        selection = [f for f in measure.row_filters if f.column not in population]
        if selection:
            out[measure.id] = [f.column for f in selection]
    return out


def test_every_measure_that_selects_on_a_nullable_column_has_a_case(cat: Catalogue) -> None:
    """The affected set comes from the CATALOGUE, so a new measure cannot slip
    past this suite: it lands in neither table and the test fails."""
    affected: set[str] = set()
    for member_id, columns in _selecting_measures(cat).items():
        table = compiler._TABLES[cat.measure(member_id).table]
        if any(
            column in table.c
            and table.c[column].nullable
            and column not in compiler._PRECOMPUTED_SELECTION_COLUMNS
            for column in columns
        ):
            affected.add(member_id)
    assert affected == set(_D042_CASES)
    assert set(_selecting_measures(cat)) - affected == _D042_EXEMPT


_OVERSIZED_ID = "a" * (MEMBER_ID_MAX_LENGTH + 1)


@pytest.mark.parametrize(
    "payload",
    [
        {"measures": [_OVERSIZED_ID]},
        {"measures": ["loans.balance_rc"], "dimensions": [_OVERSIZED_ID]},
        {
            "measures": ["loans.balance_rc"],
            "filters": [{"member": _OVERSIZED_ID, "op": "not_null"}],
        },
        {"measures": ["loans.balance_rc"], "sort": [{"member": _OVERSIZED_ID}]},
        {"measures": ["loans.balance_rc"], "top_n": {"dimension": _OVERSIZED_ID, "n": 1}},
        {"measures": ["loans.balance_rc"], "pivot": {"dimension": _OVERSIZED_ID}},
    ],
)
def test_an_oversized_member_id_is_refused_in_every_member_field(payload: dict[str, Any]) -> None:
    """Every field that names a member carries the same bound.

    ``measures`` and ``dimensions`` had only a LIST-length cap, so one 427-char
    id rode in inside a list while the four scalar fields rejected it (A6-03).
    All six now share one type, so the bound cannot be present on some and
    missing on others.
    """
    with pytest.raises(ValidationError, match="string_too_long|at most"):
        BiQuery.model_validate({**payload, "time": {"as_of": SEP_18}})
    # One character shorter is a normal (if unknown) id: the bound is a bound,
    # not a rejection of long-but-plausible names.
    assert (
        len(
            BiQuery.model_validate(
                {"measures": ["a" * MEMBER_ID_MAX_LENGTH], "time": {"as_of": SEP_18}}
            ).measures
        )
        == 1
    )


def test_the_echo_bound_is_the_schema_bound() -> None:
    """One number, so what the API accepts and what an error repeats cannot drift."""
    assert is_member_id("a" * MEMBER_ID_MAX_LENGTH)
    assert not is_member_id("a" * (MEMBER_ID_MAX_LENGTH + 1))
    assert not is_member_id(_HOSTILE_ID)
    assert is_member_id("loans.balance_rc")
