"""The two BI Phase 2 reference schemas: ``performance_targets`` and ``business_units``.

Both are bank-declared datasets that feed a board figure, so the tests are about
what the schema REFUSES. Three rules carry the weight:

* a target states its ``time_behaviour``; nothing defaults it, because a flow
  read as a stock still subtracts cleanly and is wrong by a whole period;
* a target's ``period`` is the last day of the grain it names, so "Q1" cannot
  mean two different dates;
* ``business_units`` is keyed by the names the READERS use, with the names the
  public doc used accepted as aliases (D-019). Reference payloads are preserved
  verbatim and a mapping config cannot rename a column, so the alias has to be
  resolved on read — by exactly one function.

The last test pins the dependency direction: ``app/domain/ingestion`` may not
import ``app/domain/bi``, which is why the measure id is validated for shape
rather than against the catalogue.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest

from app.domain.ingestion.constants import REFERENCE_DATASET_KINDS
from app.domain.ingestion.reference_schemas import schema_for
from app.domain.ingestion.reference_schemas.business_units import (
    ALIASES,
    normalise_row,
    validate_business_unit_row,
)
from app.domain.ingestion.reference_schemas.business_units import SCHEMA as UNITS
from app.domain.ingestion.reference_schemas.performance_targets import (
    GRAINS,
    TIME_BEHAVIOURS,
    VERSIONS,
    period_end_for,
    target_key,
    validate_target_row,
)
from app.domain.ingestion.reference_schemas.performance_targets import SCHEMA as TARGETS

GOOD_TARGET = {
    "period": "2026-03-31",
    "grain": "quarter",
    "measure_id": "loans.balance_rc",
    "time_behaviour": "stock",
    "value": "1250000",
    "version": "budget",
}
GOOD_UNIT = {"business_unit_id": "BR-001", "business_unit_name": "Accra Main"}


# ---------------------------------------------------------------------------
# 1. both kinds are registered
# ---------------------------------------------------------------------------


def test_both_kinds_are_registered_and_admitted() -> None:
    assert schema_for("performance_targets") is TARGETS
    assert schema_for("business_units") is UNITS
    for kind in ("performance_targets", "business_units"):
        assert kind in REFERENCE_DATASET_KINDS


# ---------------------------------------------------------------------------
# 2. performance_targets
# ---------------------------------------------------------------------------


def test_a_well_formed_target_is_accepted_bank_wide_and_scoped() -> None:
    assert validate_target_row(GOOD_TARGET) == []
    # A bank-wide target names no dimension; a scoped one names both halves.
    assert (
        validate_target_row(
            {**GOOD_TARGET, "scope_dimension": "branch.code", "scope_value": "BR-001"}
        )
        == []
    )
    assert validate_target_row({**GOOD_TARGET, "notes": "board pack, Feb 2026"}) == []


def test_every_required_field_is_named_when_it_is_missing() -> None:
    assert TARGETS.required == (
        "period",
        "grain",
        "measure_id",
        "time_behaviour",
        "value",
        "version",
    )
    for field in TARGETS.required:
        problems = validate_target_row({k: v for k, v in GOOD_TARGET.items() if k != field})
        assert f"missing required field '{field}'" in problems, field
    # The CSV template teaches the canonical columns and nothing else.
    assert TARGETS.columns == (
        "period",
        "grain",
        "measure_id",
        "time_behaviour",
        "value",
        "version",
        "scope_dimension",
        "scope_value",
        "notes",
    )


def test_the_stock_or_flow_basis_is_declared_never_defaulted() -> None:
    assert TIME_BEHAVIOURS == ("stock", "flow")
    # Absent is a refusal, not an assumed `stock`.
    assert "missing required field 'time_behaviour'" in validate_target_row(
        {k: v for k, v in GOOD_TARGET.items() if k != "time_behaviour"}
    )
    assert "missing required field 'time_behaviour'" in validate_target_row(
        {**GOOD_TARGET, "time_behaviour": ""}
    )
    assert validate_target_row({**GOOD_TARGET, "time_behaviour": "STOCK"}) == [
        "field 'time_behaviour' must be one of ['stock', 'flow'] (got 'STOCK')"
    ]
    assert validate_target_row({**GOOD_TARGET, "time_behaviour": "cumulative"}) == [
        "field 'time_behaviour' must be one of ['stock', 'flow'] (got 'cumulative')"
    ]
    assert validate_target_row({**GOOD_TARGET, "time_behaviour": "flow"}) == []


def test_the_version_vocabulary_is_exact() -> None:
    assert VERSIONS == ("budget", "reforecast")
    assert validate_target_row({**GOOD_TARGET, "version": "reforecast"}) == []
    assert validate_target_row({**GOOD_TARGET, "version": "forecast"}) == [
        "field 'version' must be one of ['budget', 'reforecast'] (got 'forecast')"
    ]


def test_the_value_must_be_numeric() -> None:
    assert validate_target_row({**GOOD_TARGET, "value": "1,250,000.50"}) == []
    assert validate_target_row({**GOOD_TARGET, "value": "-40000"}) == []
    assert validate_target_row({**GOOD_TARGET, "value": "about a million"}) == [
        "field 'value' must be numeric (got 'about a million')"
    ]


@pytest.mark.parametrize(
    ("grain", "period"),
    [
        ("month", "2026-02-28"),
        ("month", "2026-12-31"),
        ("quarter", "2026-09-30"),
        ("half_year", "2026-06-30"),
        ("half_year", "2026-12-31"),
        ("year", "2026-12-31"),
    ],
)
def test_a_period_on_the_last_day_of_its_grain_is_accepted(grain: str, period: str) -> None:
    assert GRAINS == ("month", "quarter", "half_year", "year")
    assert validate_target_row({**GOOD_TARGET, "grain": grain, "period": period}) == []


def test_the_period_must_parse_and_must_end_its_grain() -> None:
    assert validate_target_row({**GOOD_TARGET, "period": "31/03/2026"}) == [
        "field 'period' must be an ISO date, YYYY-MM-DD (got '31/03/2026')"
    ]
    assert validate_target_row({**GOOD_TARGET, "period": "2026-03"}) == [
        "field 'period' must be an ISO date, YYYY-MM-DD (got '2026-03')"
    ]
    # Mid-quarter: a mistake about which quarter is meant, and the refusal says
    # which date that quarter actually ends on.
    assert validate_target_row({**GOOD_TARGET, "period": "2026-03-15"}) == [
        "field 'period' must be the last day of the quarter it names "
        "(got 2026-03-15, that quarter ends 2026-03-31)"
    ]
    # A month end that is not a quarter end.
    assert validate_target_row({**GOOD_TARGET, "period": "2026-04-30"}) == [
        "field 'period' must be the last day of the quarter it names "
        "(got 2026-04-30, that quarter ends 2026-06-30)"
    ]
    # An unknown grain is reported as the enum problem only — the period rule
    # has no window to check against and must not invent a second complaint.
    assert validate_target_row({**GOOD_TARGET, "grain": "week"}) == [
        "field 'grain' must be one of ['month', 'quarter', 'half_year', 'year'] (got 'week')"
    ]


def test_period_end_for_resolves_each_grain() -> None:
    assert period_end_for(date(2026, 2, 10), "month") == date(2026, 2, 28)
    assert period_end_for(date(2024, 2, 10), "month") == date(2024, 2, 29)
    assert period_end_for(date(2026, 8, 1), "quarter") == date(2026, 9, 30)
    assert period_end_for(date(2026, 8, 1), "half_year") == date(2026, 12, 31)
    assert period_end_for(date(2026, 1, 1), "year") == date(2026, 12, 31)
    assert period_end_for(date(2026, 1, 1), "week") is None


def test_the_measure_id_is_checked_for_shape_not_membership() -> None:
    # A real portfolio id and a real engine id, which carries four segments.
    assert validate_target_row({**GOOD_TARGET, "measure_id": "engine.car_pct.crd.filed"}) == []
    # Shape only: an id the catalogue does not define still passes here, because
    # membership is the catalogue's decision at read time.
    assert validate_target_row({**GOOD_TARGET, "measure_id": "loans.no_such_measure"}) == []
    for bad in ("loans", "Loans.balance_rc", "loans..balance_rc", "loans.balance rc", "loans."):
        assert validate_target_row({**GOOD_TARGET, "measure_id": bad}) == [
            "field 'measure_id' must be a catalogue measure id like 'loans.balance_rc' "
            f"(got {bad!r})"
        ], bad


def test_scope_dimension_and_value_are_given_together_or_not_at_all() -> None:
    together = (
        "'scope_dimension' and 'scope_value' are given together or not at all "
        "(a bank-wide target gives neither)"
    )
    assert validate_target_row({**GOOD_TARGET, "scope_dimension": "branch.code"}) == [together]
    assert validate_target_row({**GOOD_TARGET, "scope_value": "BR-001"}) == [together]
    assert validate_target_row({**GOOD_TARGET, "scope_dimension": "", "scope_value": ""}) == []
    assert validate_target_row(
        {**GOOD_TARGET, "scope_dimension": "branch", "scope_value": "BR-001"}
    ) == [
        "field 'scope_dimension' must be a catalogue dimension id like 'branch.code' (got 'branch')"
    ]
    # `branch.region` is the dimension the sibling business_units schema makes
    # real, so it has to be expressible as a target scope.
    assert (
        validate_target_row(
            {**GOOD_TARGET, "scope_dimension": "branch.region", "scope_value": "Greater Accra"}
        )
        == []
    )


def test_the_target_key_separates_scopes_and_versions() -> None:
    bank_wide = target_key(GOOD_TARGET)
    scoped = target_key({**GOOD_TARGET, "scope_dimension": "branch.code", "scope_value": "BR-001"})
    assert bank_wide == ("2026-03-31", "quarter", "loans.balance_rc", "", "", "budget")
    assert bank_wide != scoped
    assert target_key({**GOOD_TARGET, "version": "reforecast"}) != bank_wide
    # The same target stated twice, one row padded with whitespace and a note.
    assert target_key({**GOOD_TARGET, "period": " 2026-03-31 ", "notes": "restated"}) == bank_wide


# ---------------------------------------------------------------------------
# 3. business_units — the code's keys are canonical, the documented ones alias
# ---------------------------------------------------------------------------


def test_the_canonical_keys_are_the_ones_the_readers_use() -> None:
    assert UNITS.required == ("business_unit_id", "business_unit_name")
    assert UNITS.optional == ("region", "parent_unit_id", "outlet_number", "cost_centre", "notes")
    # Aliases are accepted on input but are NOT template columns: the template
    # teaches the canonical spelling only.
    assert ALIASES == {"unit_id": "business_unit_id", "name": "business_unit_name"}
    assert not set(ALIASES).intersection(UNITS.columns)
    assert validate_business_unit_row(GOOD_UNIT) == []
    assert (
        validate_business_unit_row(
            {
                **GOOD_UNIT,
                "region": "Greater Accra",
                "parent_unit_id": "BR-000",
                "outlet_number": "12",
                "cost_centre": "CC-4400",
            }
        )
        == []
    )


def test_the_documented_spelling_is_accepted_as_an_alias() -> None:
    documented = {"unit_id": "BR-001", "name": "Accra Main"}
    assert validate_business_unit_row(documented) == []
    assert normalise_row(documented) == {
        "unit_id": "BR-001",
        "name": "Accra Main",
        "business_unit_id": "BR-001",
        "business_unit_name": "Accra Main",
    }
    # Half and half is still a complete row.
    assert (
        validate_business_unit_row({"unit_id": "BR-001", "business_unit_name": "Accra Main"}) == []
    )
    # A row that spells neither is told the canonical name.
    assert validate_business_unit_row({"branch": "BR-001"}) == [
        "missing required field 'business_unit_id'",
        "missing required field 'business_unit_name'",
    ]


def test_the_canonical_value_wins_and_a_disagreement_is_reported() -> None:
    both = {**GOOD_UNIT, "unit_id": "BR-999", "name": "Accra Main"}
    assert normalise_row(both)["business_unit_id"] == "BR-001"
    assert validate_business_unit_row(both) == [
        "fields 'business_unit_id' and 'unit_id' disagree ('BR-001' vs 'BR-999'); "
        "'unit_id' is an alias of 'business_unit_id'"
    ]
    # Agreeing duplicates are not a problem.
    assert validate_business_unit_row({**GOOD_UNIT, "unit_id": "BR-001"}) == []


def test_normalise_row_leaves_the_ingested_payload_intact() -> None:
    row = {"unit_id": "BR-001", "name": "Accra Main"}
    normalise_row(row)
    assert row == {"unit_id": "BR-001", "name": "Accra Main"}


def test_a_unit_cannot_be_its_own_parent() -> None:
    assert validate_business_unit_row({**GOOD_UNIT, "parent_unit_id": "BR-000"}) == []
    assert validate_business_unit_row({**GOOD_UNIT, "parent_unit_id": "BR-001"}) == [
        "field 'parent_unit_id' must not be the unit itself (got 'BR-001')"
    ]


def test_region_is_optional_because_it_is_declared_not_inferred() -> None:
    # No region is a legitimate register (D-020): the BI branch dimension reports
    # it as unassigned rather than parsing an address for one.
    assert "region" not in UNITS.required
    assert validate_business_unit_row({**GOOD_UNIT, "region": ""}) == []


# ---------------------------------------------------------------------------
# 4. the dependency direction that makes shape-only validation necessary
# ---------------------------------------------------------------------------


def test_the_reference_schemas_do_not_import_the_bi_catalogue() -> None:
    """``app/domain/ingestion`` is upstream of ``app/domain/bi``.

    The catalogue CONSUMES these datasets, so a schema that imported it would
    make the input depend on its own consumer — which is why ``measure_id``,
    ``scope_dimension`` and ``time_behaviour`` are validated for shape and
    vocabulary here and for membership there. Checked by AST, so the module
    docstrings may name ``app.domain.bi`` in order to explain the rule.
    """
    package = Path(__file__).parents[3] / "app" / "domain" / "ingestion"
    offenders: list[str] = []
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name == "app.domain.bi" or name.startswith("app.domain.bi.") for name in names):
                offenders.append(str(path.relative_to(package.parents[2])))
    assert not offenders, f"ingestion must not import the BI catalogue: {sorted(set(offenders))}"
