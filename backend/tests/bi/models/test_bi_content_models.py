"""The four tenant-authored BI tables: their vocabularies and their constraints.

Two kinds of test, and the second kind is the point of the file. The first is
parity: every vocabulary in ``app/bi/models/content.py`` is a deliberate mirror
of a source of truth elsewhere (the catalogue's value types and directions, the
formula language's own length cap, the role bundles), because ``app.models`` does
not import ``app.bi.domain``; a mirror nobody checks is a mirror that drifts.

The second is that the promotion rules hold in the DATABASE. Maker-checker for a
calculated measure is enforced by the service, audited, and expressed through the
platform's separation-of-duties machinery — and it is ALSO a CHECK constraint, so
a self-approved or half-approved row cannot be stored even if a future code path
is wrong about it. SQLite enforces CHECK constraints, so these run hermetically
and prove the constraint rather than the model's opinion of it.

This module's other-suite cases live in ``test_bi_content_models_integration.py``.
"""

from __future__ import annotations

from typing import get_args

from sqlalchemy import String, inspect

from app.bi.domain import expr
from app.bi.domain.catalogue.members import NUMERIC_VALUE_TYPES, FavourableDirection
from app.bi.models.content import (
    BI_CONTENT_TABLES,
    BI_CONTENT_UNALTERABLE_TABLES,
    DASHBOARD_BADGES,
    DASHBOARD_VISIBILITIES,
    MEASURE_EXPRESSION_MAX_LENGTH,
    MEASURE_FAVOURABLE_DIRECTIONS,
    MEASURE_STATES,
    MEASURE_VALUE_TYPES,
    STORED_DASHBOARD_BADGES,
    BiMeasure,
)
from app.core.authorization import RoleBundle
from app.db.base import Base


def test_measure_value_types_are_exactly_the_catalogue_numeric_types() -> None:
    """A calculated measure is a figure, so its units are the catalogue's numeric ones.

    Both directions: a numeric type the catalogue gains cannot stay unstorable,
    and a type stored here cannot be one the catalogue does not know.
    """

    assert set(MEASURE_VALUE_TYPES) == set(NUMERIC_VALUE_TYPES)


def test_measure_directions_are_exactly_the_catalogue_directions() -> None:
    assert set(MEASURE_FAVOURABLE_DIRECTIONS) == set(get_args(FavourableDirection))


def test_the_expression_column_is_as_long_as_the_language_allows() -> None:
    """The database refuses exactly what the parser refuses, not more and not less."""

    assert MEASURE_EXPRESSION_MAX_LENGTH == expr.MAX_EXPRESSION_LENGTH
    column_type = inspect(BiMeasure).columns["expression"].type
    assert isinstance(column_type, String)
    assert column_type.length == expr.MAX_EXPRESSION_LENGTH


def test_a_stored_dashboard_can_never_be_platform_certified() -> None:
    """ "Platform-certified" is a pack FILE, never a tenant row.

    A row claiming it would be a tenant asserting the platform's authority over
    its own content, and an edit of a certified pack produces a copy rather than
    changing the pack — so the copy is the copier's.
    """

    assert "platform_certified" in DASHBOARD_BADGES
    assert "platform_certified" not in STORED_DASHBOARD_BADGES
    assert set(STORED_DASHBOARD_BADGES) < set(DASHBOARD_BADGES)


def test_the_visibility_vocabulary_is_the_four_reachability_rules() -> None:
    assert DASHBOARD_VISIBILITIES == ("private", "users", "role", "org")


def test_the_measure_states_are_the_promotion_ladder() -> None:
    assert MEASURE_STATES == ("personal", "proposed", "bank_certified")


def test_every_content_table_is_registered_and_named_once() -> None:
    """The tuple the migration and this test both read holds every table, once."""

    assert len(set(BI_CONTENT_TABLES)) == len(BI_CONTENT_TABLES)
    for table in BI_CONTENT_TABLES:
        assert table in Base.metadata.tables, table
    assert set(BI_CONTENT_UNALTERABLE_TABLES) <= set(BI_CONTENT_TABLES)


def test_every_content_table_is_tenant_and_institution_scoped() -> None:
    """No row of this plane exists outside one organization and one institution."""

    for table_name in BI_CONTENT_TABLES:
        table = Base.metadata.tables[table_name]
        assert "organization_id" in table.columns, table_name
        assert "bank_id" in table.columns, table_name
        assert not table.columns["organization_id"].nullable, table_name
        assert not table.columns["bank_id"].nullable, table_name


def test_the_promotion_constraints_are_named_on_the_measure_table() -> None:
    """The constraint names the migration has to create, read off the model."""

    names = {
        constraint.name
        for constraint in Base.metadata.tables[BiMeasure.__tablename__].constraints
        if constraint.name
    }
    assert {
        "ck_bi_measures_state",
        "ck_bi_measures_value_type",
        "ck_bi_measures_favourable_direction",
        "ck_bi_measures_proposal_complete",
        "ck_bi_measures_approval_complete",
        "ck_bi_measures_promotion_separation",
        "ck_bi_measures_certified_matches_expression",
        "uq_bi_measures_bank_key",
    } <= names


def test_a_role_visibility_names_a_role_the_platform_issues() -> None:
    """The role vocabulary the ``role`` visibility draws on is the binding's own."""

    assert "viewer" in {bundle.value for bundle in RoleBundle}
