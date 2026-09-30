"""The registered schema is ENFORCED on the way in, for the kinds that enforce it.

Audit A7-07 / H-027: the reference-schema modules stated rules —
``time_behaviour`` is "required, never defaulted", a period must be the last day
of the grain it names, a ``business_units`` row spelling a field both ways with
different values is refused — and none of them ran on a real push, because
nothing in ``app/`` ever asked. ``ReferenceRowData`` now asks, so every producer
(API push, Excel/CSV, every core-banking adapter) goes through one seam.

Scope is deliberate and is the subject of these tests as much as the rules are:
the two registers new in this release enforce; the nine older ones do not,
because tenants have rows stored under them today.
"""

from __future__ import annotations

import pytest

from app.domain.ingestion.contracts import ENFORCED_REFERENCE_KINDS, ReferenceRowData
from app.domain.ingestion.reference_schemas import SCHEMAS, schema_for


def _row(kind: str, payload: dict[str, str | None]) -> ReferenceRowData:
    return ReferenceRowData(
        dataset_kind=kind,  # type: ignore[arg-type]
        source_locator="book.xlsx!A1",
        row_index=1,
        payload=payload,
    )


_VALID_TARGET = {
    "period": "2026-03-31",
    "grain": "quarter",
    "measure_id": "loans.balance_rc",
    "time_behaviour": "stock",
    "value": "1000",
    "version": "budget",
}
_VALID_UNIT = {"business_unit_id": "BR-001", "business_unit_name": "Head Office"}


def test_a_well_formed_target_is_accepted() -> None:
    assert _row("performance_targets", dict(_VALID_TARGET)).row_index == 1


def test_a_period_that_is_not_the_end_of_its_grain_is_refused() -> None:
    """A quarterly target dated mid-quarter is a mistake about WHICH quarter is
    meant, and the message has to say which date that quarter ends on."""
    with pytest.raises(ValueError, match="last day of the quarter") as caught:
        _row("performance_targets", dict(_VALID_TARGET, period="2026-03-15"))
    assert "2026-03-31" in str(caught.value)


def test_a_missing_time_behaviour_is_refused_rather_than_defaulted() -> None:
    """The whole point of the field. A defaulted ``stock`` silently reads a flow
    target as a level, which is wrong by a period and looks like a real number."""
    payload: dict[str, str | None] = {
        k: v for k, v in _VALID_TARGET.items() if k != "time_behaviour"
    }
    with pytest.raises(ValueError, match="time_behaviour"):
        _row("performance_targets", payload)


def test_an_empty_time_behaviour_is_refused_too() -> None:
    with pytest.raises(ValueError, match="time_behaviour"):
        _row("performance_targets", dict(_VALID_TARGET, time_behaviour=""))


def test_a_business_unit_row_using_the_documented_aliases_is_accepted() -> None:
    """``unit_id`` / ``name`` is the spelling the public contract used to
    document; it stays a complete row."""
    assert _row("business_units", {"unit_id": "BR-001", "name": "Head Office"})


def test_a_row_that_spells_a_field_both_ways_with_different_values_is_refused() -> None:
    """Resolving it silently would pick a branch name by import order."""
    with pytest.raises(ValueError):
        _row("business_units", {**_VALID_UNIT, "unit_id": "BR-999"})


def test_a_unit_may_not_be_its_own_parent() -> None:
    with pytest.raises(ValueError):
        _row("business_units", {**_VALID_UNIT, "parent_unit_id": "BR-001"})


@pytest.mark.parametrize("kind", sorted(set(SCHEMAS) - ENFORCED_REFERENCE_KINDS))
def test_an_unenforced_kind_still_accepts_whatever_the_bank_sends(kind: str) -> None:
    """The nine older registers are NOT enforced, and that is load-bearing, not
    an oversight: tenants have rows stored under them that predate the schema
    modules, so refusing here would reject data that lands today. H-027 is the
    decision to widen this; until then, this test is what makes the scope
    explicit rather than accidental.
    """
    assert _row(kind, {"nothing": "the schema would accept"})


def test_every_enforced_kind_is_registered_and_carries_its_own_rules() -> None:
    """An enforced kind with no schema, or with only the declarative half, would
    enforce less than this module claims."""
    for kind in ENFORCED_REFERENCE_KINDS:
        schema = schema_for(kind)
        assert schema is not None, f"{kind} is enforced but not registered"
        assert schema.row_validator is not None, (
            f"{kind} is enforced but carries no row_validator, so problems_for "
            "would run the declarative checks only"
        )


# --- audit A360 H3: the width the mart can store is enforced at the door ------------------


def test_an_over_long_unit_id_is_refused_at_the_door_with_the_limit_named() -> None:
    """A ``bi_dim_branch.branch_code`` is 120 characters wide. Accepted here, a
    longer id used to fail the tenant's whole nightly mart build on Postgres;
    the refusal now says which limit and never repeats the value."""
    wide = "x" * 121
    over: dict[str, str | None] = {**_VALID_UNIT, "business_unit_id": wide}
    with pytest.raises(ValueError, match="120-character limit") as caught:
        _row("business_units", over)
    assert "business_unit_id" in str(caught.value)
    assert "121 characters" in str(caught.value)
    assert wide not in str(caught.value)
    assert _row("business_units", {**_VALID_UNIT, "business_unit_id": "x" * 120})


def test_an_over_long_segment_branch_is_refused_at_the_door() -> None:
    row: dict[str, str | None] = {
        "as_of_date": "2026-06-30",
        "gl_account_code": "4001",
        "branch_id": "b" * 121,
        "ytd_balance": "1000",
    }
    with pytest.raises(ValueError, match="branch_id"):
        _row("gl_segment_balances", row)
    assert _row("gl_segment_balances", {**row, "branch_id": "b" * 120})
