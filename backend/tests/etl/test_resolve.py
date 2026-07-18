"""Guard-alias resolver tests (:mod:`app.etl.resolve`).

The central §12.5 guarantee lives here: a regulatory-critical value is guarded by its
canonical *concept*, not its raw source spelling, and a value-changing edit to a critical
concept can never be expressed as a SANCTIONED op — even when the column is a raw alias.
"""

from __future__ import annotations

from app.etl.contracts import (
    REGULATORY_CRITICAL_FIELDS,
    Disposition,
    ETLOperationType,
)
from app.etl.resolve import (
    canonical_view,
    is_regulatory_critical,
    make_operation,
    resolve_concept,
)
from tests.etl._factories import position


def test_raw_alias_resolves_to_critical_concept() -> None:
    # The headline requirement: a value under 'balance_ghs' is guarded as 'balance'.
    assert resolve_concept("balance_ghs") == "balance"
    assert is_regulatory_critical("balance_ghs")
    assert resolve_concept("BALANCE_GHS") == "balance"  # separator/case insensitive
    assert resolve_concept("ccy") == "currency"
    assert is_regulatory_critical("ccy")
    assert resolve_concept("customer_id") == "counterparty_id"
    assert is_regulatory_critical("customer_id")


def test_non_critical_concepts_resolve_but_are_not_guarded() -> None:
    assert resolve_concept("counterparty_name") == "name"
    assert not is_regulatory_critical("counterparty_name")
    assert resolve_concept("country_code") == "country"
    assert not is_regulatory_critical("country_code")
    # Unknown columns resolve to themselves and are non-critical.
    assert resolve_concept("branch_code") == "branch_code"
    assert not is_regulatory_critical("branch_code")


def test_every_critical_field_is_reachable_by_at_least_itself() -> None:
    for critical in REGULATORY_CRITICAL_FIELDS:
        assert resolve_concept(critical) == critical
        assert is_regulatory_critical(critical)


def test_make_operation_flags_value_change_on_critical_alias() -> None:
    # A value-CHANGING edit under a raw alias must FLAG, never SANCTION (guard-alias-resolver).
    op = make_operation(
        record_id="P1",
        source_field="balance_ghs",
        before="1000",
        after="2000",  # a genuine change, not a format normalization
        operation_type=ETLOperationType.NORMALIZE,
        operation_ref="test",
        value_preserving=False,
    )
    assert op is not None
    assert op.disposition is Disposition.FLAGGED
    assert op.after is None
    assert op.reason


def test_make_operation_sanctions_value_preserving_on_critical_alias() -> None:
    op = make_operation(
        record_id="P1",
        source_field="balance_ghs",
        before="1,000.00",
        after="1000.00",
        operation_type=ETLOperationType.TYPE_COERCE,
        operation_ref="test",
        value_preserving=True,
    )
    assert op is not None
    assert op.disposition is Disposition.SANCTIONED
    assert op.after == "1000.00"


def test_make_operation_skips_value_preserving_on_literal_critical_name() -> None:
    # A column literally named as the critical concept cannot carry a SANCTIONED rewrite
    # (contract guard), so a cosmetic value-preserving change is dropped, not applied.
    op = make_operation(
        record_id="P1",
        source_field="balance",  # literally the critical concept name
        before="1,000.00",
        after="1000.00",
        operation_type=ETLOperationType.TYPE_COERCE,
        operation_ref="test",
        value_preserving=True,
    )
    assert op is None


def test_make_operation_sanctions_non_critical_freely() -> None:
    op = make_operation(
        record_id="C1",
        source_field="counterparty_name",
        before="  Acme  ",
        after="Acme",
        operation_type=ETLOperationType.NORMALIZE,
        operation_ref="test",
        value_preserving=True,
    )
    assert op is not None
    assert op.disposition is Disposition.SANCTIONED


def test_make_operation_returns_none_for_noop() -> None:
    assert (
        make_operation(
            record_id="C1",
            source_field="counterparty_name",
            before="Acme",
            after="Acme",
            operation_type=ETLOperationType.NORMALIZE,
            operation_ref="test",
            value_preserving=True,
        )
        is None
    )


def test_canonical_view_maps_concepts_to_source_columns() -> None:
    record = position(
        "P1", source_reference="ARR/1", as_of_date="2026-04-30", balance_ghs="1000", ccy="GHS"
    )
    view = canonical_view(record)
    assert view["balance"].source_field == "balance_ghs"
    assert view["balance"].is_critical
    assert view["currency"].source_field == "ccy"
    assert view["currency"].is_critical
    assert not view["as_of_date"].is_critical
