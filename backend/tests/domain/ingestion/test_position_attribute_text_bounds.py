"""Audit A360 H3: free-text position attributes are bounded at the door, at the
widths of the mart columns they are copied into — and the two agree by test.

``branch_id``, ``sector``, ``employer``, ``hqla_level``, ``collateral_type`` and
their aliases ride the ``attributes`` bag unbounded and are copied VERBATIM into
``VARCHAR(n)`` columns of ``bi_fact_position_daily`` / ``_eom`` /
``bi_agg_position_daily`` / ``bi_fact_loan_event``. Postgres refuses a longer
value; SQLite ignores VARCHAR lengths, so the hermetic suite can never see the
failure by inserting. What it CAN pin is:

* the one table both consumers read (``POSITION_ATTRIBUTE_TEXT_LIMITS``) equals
  the mart models' own ``String(n)`` widths, column by column;
* the ingestion rule ``position_attribute_text_bounds`` fires on an over-long
  value at the default WARNING, names key / length / limit and never the value,
  and leaves the position included with its bag preserved as sent.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.ingestion.constants import INCLUDED_VALIDATION_STATUSES
from app.domain.ingestion.contracts import CanonicalRecords, PositionData
from app.domain.ingestion.optional_position_fields import (
    OFFICER_ID_MAX_LENGTH,
    POSITION_ATTRIBUTE_TEXT_LIMITS,
    normalize_positions,
    over_long_text_attributes,
)
from app.domain.ingestion.validation import (
    RULE_NAMES,
    RuleConfig,
    ValidationConfig,
    ValidationContext,
    ValidationOutcome,
    default_validation_config,
    run_validation,
)
from app.models.bi import (
    BiAggPositionDaily,
    BiFactLoanEvent,
    BiFactPositionDaily,
    BiFactPositionEom,
)

AS_OF = date(2026, 6, 30)
RULE = "position_attribute_text_bounds"

#: attribute key → the position-fact column it is copied into (aliases included).
_KEY_TO_COLUMN: dict[str, str] = {
    "branch_id": "branch_code",
    "officer_id": "officer_id",
    "hqla_level": "hqla_level",
    "collateral_type": "collateral_type",
    "crm_collateral_class": "collateral_type",
    "sector": "sector",
    "industry": "sector",
    "employer": "employer",
    "channel": "channel",
    "account_status": "account_status",
}


def _width(model: type, column: str) -> int:
    length = model.__table__.c[column].type.length  # type: ignore[attr-defined]
    assert isinstance(length, int), (model.__tablename__, column)  # type: ignore[attr-defined]
    return length


# ---------------------------------------------------------------------------
# 1. the door and the marts agree, column by column
# ---------------------------------------------------------------------------


def test_every_bounded_key_is_exactly_the_width_of_the_mart_column_it_is_copied_into() -> None:
    """Whichever side is larger is the trap: a value the door accepts and the
    mart cannot store fails the build; a value the mart could hold but the door
    refuses is a code the bank cannot state."""
    assert set(POSITION_ATTRIBUTE_TEXT_LIMITS) == set(_KEY_TO_COLUMN)
    for key, column in _KEY_TO_COLUMN.items():
        for model in (BiFactPositionDaily, BiFactPositionEom):
            assert POSITION_ATTRIBUTE_TEXT_LIMITS[key] == _width(model, column), (
                key,
                model.__tablename__,
            )
    # The aggregate grain and the loan-event fact carry the branch code (and the
    # event fact the sector) at the same width, or a value the daily fact holds
    # would fail the sibling table's insert.
    assert POSITION_ATTRIBUTE_TEXT_LIMITS["branch_id"] == _width(BiAggPositionDaily, "branch_code")
    assert POSITION_ATTRIBUTE_TEXT_LIMITS["branch_id"] == _width(BiFactLoanEvent, "branch_code")
    assert POSITION_ATTRIBUTE_TEXT_LIMITS["sector"] == _width(BiFactLoanEvent, "sector")
    # ``officer_id`` was bounded first, by normalisation; the two bounds are one.
    assert POSITION_ATTRIBUTE_TEXT_LIMITS["officer_id"] == OFFICER_ID_MAX_LENGTH


def test_the_six_columns_the_audit_named_are_all_in_the_table() -> None:
    assert {
        key: POSITION_ATTRIBUTE_TEXT_LIMITS[key]
        for key in (
            "branch_id",
            "officer_id",
            "hqla_level",
            "collateral_type",
            "sector",
            "employer",
        )
    } == {
        "branch_id": 120,
        "officer_id": 120,
        "hqla_level": 16,
        "collateral_type": 80,
        "sector": 120,
        "employer": 255,
    }


# ---------------------------------------------------------------------------
# 2. the measurement
# ---------------------------------------------------------------------------


def test_over_long_text_attributes_measures_the_verbatim_text() -> None:
    assert over_long_text_attributes({}) == ()
    assert over_long_text_attributes({"branch_id": "x" * 120}) == ()
    assert over_long_text_attributes({"branch_id": "x" * 121}) == (("branch_id", 121, 120),)
    # Verbatim: the extractor stores the value as sent, so surrounding whitespace
    # is part of what the column would have to hold.
    assert over_long_text_attributes({"branch_id": " " + "x" * 120}) == (("branch_id", 121, 120),)
    # Blank is never over-long, absent is absent, a number is measured as text.
    assert over_long_text_attributes({"branch_id": "   ", "sector": None, "employer": ""}) == ()
    assert over_long_text_attributes({"hqla_level": 12345678901234567}) == (("hqla_level", 17, 16),)
    # Keys the marts do not copy into a bounded column are not this rule's business.
    assert over_long_text_attributes({"notes": "z" * 10_000, "balance_ghs": "1" * 200}) == ()
    # Several at once, in the table's own order.
    found = over_long_text_attributes({"employer": "e" * 256, "branch_id": "b" * 121})
    assert found == (("branch_id", 121, 120), ("employer", 256, 255))


# ---------------------------------------------------------------------------
# 3. the ingestion rule fires, at the door, and the position still lands
# ---------------------------------------------------------------------------


def _position(reference: str = "LN-0001", **overrides: object) -> PositionData:
    values: dict[str, object] = {
        "source_reference": reference,
        "source_locator": f"source.json#position!{reference}",
        "position_type": "LOAN",
        "currency": "GHS",
        "balance": Decimal("1000"),
    }
    values.update(overrides)
    return PositionData.model_validate(values)


def _run(*positions: PositionData, config: ValidationConfig | None = None) -> ValidationOutcome:
    records = CanonicalRecords(positions=list(positions))
    problems = normalize_positions(records)
    return run_validation(
        records,
        config or default_validation_config(),
        ValidationContext(as_of_date=AS_OF, attribute_problems=problems),
    )


def _bounds_findings(outcome: ValidationOutcome) -> list:
    return [finding for finding in outcome.findings if finding.rule == RULE]


def test_the_rule_is_registered_and_in_the_default_configuration_at_warning() -> None:
    """A rule that is defined and never configured never runs (the inert-feature
    class). WARNING, because the value is readable and kept — what the bank is
    told is that analytics will carry the field as absent until it is shortened."""
    assert RULE in RULE_NAMES
    configured = {rule.name: rule for rule in default_validation_config().rules}
    assert configured[RULE].severity == "WARNING"
    assert configured[RULE].enabled is True


def test_an_over_long_branch_code_is_reported_by_key_length_and_limit_never_by_value() -> None:
    wide = "B" * 121
    outcome = _run(_position("LN-9", attributes={"branch_id": wide, "sector": "Agri"}))
    (finding,) = _bounds_findings(outcome)
    assert finding.severity == "WARNING"
    assert finding.category == "STRUCTURAL"
    assert finding.entity_type == "position"
    assert finding.source_reference == "LN-9"
    assert finding.source_locator == "source.json#position!LN-9"
    assert "branch_id" in finding.detail
    assert "121 characters" in finding.detail
    assert "120-character limit" in finding.detail
    assert "not stated" in finding.detail  # says what analytics will show
    assert wide not in finding.detail  # a 121-character code is not the message


def test_the_position_still_lands_and_its_bag_is_preserved_as_sent() -> None:
    """The facility is not deleted from the balance sheet because a code was too
    long: ``warning`` is an included status, and the attribute stays on the
    position verbatim — the marts, not the book, carry it as absent."""
    wide = "B" * 121
    row = _position("LN-9", attributes={"branch_id": wide})
    outcome = _run(row)
    assert outcome.record_statuses[("position", "LN-9")] == "warning"
    assert outcome.record_statuses[("position", "LN-9")] in INCLUDED_VALIDATION_STATUSES
    assert row.attributes == {"branch_id": wide}


def test_a_value_at_the_limit_is_silent_and_a_clean_batch_says_nothing() -> None:
    outcome = _run(
        _position(
            attributes={
                "branch_id": "B" * 120,
                "sector": "S" * 120,
                "employer": "E" * 255,
                "hqla_level": "H" * 16,
                "collateral_type": "C" * 80,
            }
        )
    )
    assert _bounds_findings(outcome) == []
    assert outcome.overall_status == "accepted"


def test_one_finding_per_over_long_key_on_the_same_position() -> None:
    outcome = _run(
        _position(
            "LN-3",
            attributes={"branch_id": "B" * 121, "sector": "S" * 121, "employer": "E" * 256},
        )
    )
    findings = _bounds_findings(outcome)
    assert len(findings) == 3
    assert {finding.source_reference for finding in findings} == {"LN-3"}
    assert sorted(
        key for key in ("branch_id", "sector", "employer") if any(key in f.detail for f in findings)
    ) == ["branch_id", "employer", "sector"]


def test_officer_id_is_bounded_earlier_by_normalisation_so_this_rule_never_fires_for_it() -> None:
    """Normalisation drops an over-long ``officer_id`` and reports it under
    ``optional_position_attributes``; by the time this rule runs the key is
    gone. Listed in the table for completeness of the widths, not because it
    fires — and pinned so the two rules never report the same value twice."""
    outcome = _run(_position("LN-7", attributes={"officer_id": "O" * 121}))
    rules = [finding.rule for finding in outcome.findings]
    assert "optional_position_attributes" in rules
    assert RULE not in rules


def test_severity_is_per_institution_configuration() -> None:
    outcome = _run(
        _position("LN-9", attributes={"branch_id": "B" * 121}),
        config=ValidationConfig(rules=[RuleConfig(name=RULE, severity="ERROR")]),  # type: ignore[arg-type]
    )
    (finding,) = _bounds_findings(outcome)
    assert finding.severity == "ERROR"
    assert outcome.record_statuses[("position", "LN-9")] == "error"


def test_a_position_with_no_attributes_is_untouched() -> None:
    outcome = _run(_position("LN-1"), _position("LN-2", attributes={}))
    assert _bounds_findings(outcome) == []
