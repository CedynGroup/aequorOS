"""Row-level cross-source position matching: what it catches, and what it must not.

The gap this closes, verified against the primary database before a line was
written: ``PositionDeduplicator`` groups positions on ``source_reference``, which
is a PER-SOURCE-SYSTEM identifier, and ``run_etl`` is pure and sees one batch. So
neither can see a bank whose book arrives twice — and BK-0PMD7Z5M holds exactly
that: 150,314 positions carry the SAME ``source_reference`` under two different
``source_system`` values (DB_DIRECT and EXCEL_CSV), with supersession scoped per
source system so both live books survive in full.

These tests pin, in order of how much damage getting them wrong would do:

* a single-source book produces NOTHING — a matcher that fires on a healthy book
  is worse than no matcher;
* two systems never resolve to a winner: no merge, no retirement, no
  auto-confirmation, every subsumed id preserved;
* a shared source reference across systems is matched, and the same reference
  repeated INSIDE one system is not (that is the position deduplicator's job);
* the attribute fingerprint matches only when every contract term is present and
  agrees, and an incomplete row is COUNTED as unassessable rather than paired;
* a group that is not one-row-per-system is reported as ambiguous, never as a
  confirmed pair — this is the facility/drawdown granularity case;
* the output is deterministic.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.etl.contracts import MatchType
from app.etl.deduplication.cross_source_positions import (
    AMBIGUOUS_CONFIDENCE,
    ATTRIBUTE_CONFIDENCE,
    ATTRIBUTE_UNSTATED_MATURITY_CONFIDENCE,
    MATCH_ATTRIBUTE_FINGERPRINT,
    MATCH_SHARED_REFERENCE,
    SHARED_REFERENCE_CONFIDENCE,
    CanonicalPositionRow,
    CrossSourcePositionMatcher,
)

ORIG = date(2025, 3, 4)
MATURITY = date(2027, 11, 8)
RATE = Decimal("0.1079")


def row(  # noqa: PLR0913 - a keyword-only builder for every matchable field
    row_id: str,
    *,
    system: str,
    reference: str,
    position_type: str = "LOAN",
    currency: str = "GHS",
    counterparty: str | None = "ref:SBL-CUST-017027",
    product: str | None = "LN.CORP.USD",
    origination: date | None = ORIG,
    maturity: date | None = MATURITY,
    rate: Decimal | None = RATE,
    balance: Decimal | None = Decimal("1000"),
) -> CanonicalPositionRow:
    return CanonicalPositionRow(
        row_id=row_id,
        source_system=system,
        source_reference=reference,
        position_type=position_type,
        currency=currency,
        counterparty_key=counterparty,
        product_code=product,
        origination_date=origination,
        contractual_maturity=maturity,
        interest_rate=rate,
        balance=balance,
    )


# --- the negative case, first: a healthy book must stay silent ----------------


def test_single_source_system_produces_no_linkage() -> None:
    rows = [
        row("p1", system="API_PUSH", reference="SBL-LOAN-1"),
        row("p2", system="API_PUSH", reference="SBL-LOAN-2"),
        row("p3", system="API_PUSH", reference="SBL-LOAN-3"),
    ]

    result = CrossSourcePositionMatcher().link(rows)

    assert result.linkages == ()
    assert result.coverage.considered == 3
    assert result.coverage.source_systems == ("API_PUSH",)
    assert result.coverage.matched_rows == 0


def test_empty_population_is_silent() -> None:
    result = CrossSourcePositionMatcher().link([])
    assert result.linkages == ()
    assert result.coverage.considered == 0


def test_repeated_reference_inside_one_source_system_is_not_cross_source() -> None:
    # Two rows, same reference, SAME system: a within-source duplicate, which is
    # PositionDeduplicator's territory. This matcher must not claim it.
    rows = [
        row("p1", system="DB_DIRECT", reference="SBL-LOAN-010816"),
        row("p2", system="DB_DIRECT", reference="SBL-LOAN-010816"),
    ]

    assert CrossSourcePositionMatcher().link(rows).linkages == ()


def test_partitioned_book_across_systems_produces_no_linkage() -> None:
    # Core banking owns the loans, treasury owns the securities: two systems, no
    # shared reference, no shared contract terms. Nothing may be paired.
    rows = [
        row("p1", system="DB_DIRECT", reference="SBL-LOAN-1"),
        row(
            "p2",
            system="API_PUSH",
            reference="SEC.POSITION/9",
            position_type="SECURITY_HOLDING",
            counterparty="ref:SBL-SOV-017052",
            product="SEC.TBILL.91",
            origination=date(2026, 4, 17),
            maturity=date(2026, 7, 16),
            rate=Decimal("0.1604"),
        ),
    ]

    assert CrossSourcePositionMatcher().link(rows).linkages == ()


# --- tier 1: the bank's own identifier, used by two systems -------------------


def test_shared_source_reference_across_systems_is_matched() -> None:
    rows = [
        row("p1", system="DB_DIRECT", reference="SBL-LOAN-010816"),
        row("p2", system="EXCEL_CSV", reference="SBL-LOAN-010816", balance=Decimal("2000")),
    ]

    result = CrossSourcePositionMatcher().link(rows)

    assert len(result.linkages) == 1
    link = result.linkages[0]
    assert link.match_type is MatchType.CROSS_SOURCE
    assert link.linked_source_ids == ("p1", "p2")
    assert link.combined_confidence == SHARED_REFERENCE_CONFIDENCE
    assert link.signals[MATCH_SHARED_REFERENCE] == 1.0
    assert link.signals["one_to_one"] == 1.0
    # Balance is deliberately NOT part of the key: two extracts taken at
    # different times legitimately disagree on it, and requiring agreement would
    # miss real duplicates.
    assert result.coverage.matched_rows == 2
    assert result.by_match() == {MATCH_SHARED_REFERENCE: 1, MATCH_ATTRIBUTE_FINGERPRINT: 0}


def test_shared_reference_is_matched_case_and_whitespace_insensitively() -> None:
    rows = [
        row("p1", system="DB_DIRECT", reference=" sbl-loan-010816 "),
        row("p2", system="EXCEL_CSV", reference="SBL-LOAN-010816"),
    ]

    assert len(CrossSourcePositionMatcher().link(rows).linkages) == 1


def test_shared_reference_requires_agreeing_type_and_currency() -> None:
    # An identifier string reused across unrelated namespaces is not an identity.
    rows = [
        row("p1", system="DB_DIRECT", reference="REF-1", position_type="LOAN"),
        row("p2", system="EXCEL_CSV", reference="REF-1", position_type="DEPOSIT"),
        row("p3", system="DB_DIRECT", reference="REF-2", currency="GHS"),
        row("p4", system="EXCEL_CSV", reference="REF-2", currency="USD"),
    ]

    # No tier-1 match; and the attribute tier cannot pair them either, because
    # position type and currency are part of that key too.
    assert CrossSourcePositionMatcher().link(rows).linkages == ()


def test_reference_namespaces_are_never_stripped_to_force_a_match() -> None:
    # ACCOUNT/2782827 and SBL-DEP-2782827 share a numeric tail and nothing else.
    # Inventing that equivalence would be source-system reasoning the ML-ETL
    # layer must not do.
    rows = [
        row("p1", system="API_PUSH", reference="ACCOUNT/2782827", counterparty=None),
        row("p2", system="DB_DIRECT", reference="SBL-DEP-2782827", counterparty=None),
    ]

    assert CrossSourcePositionMatcher().link(rows).linkages == ()


# --- tier 2: the contract terms ----------------------------------------------


def test_attribute_fingerprint_matches_across_reference_namespaces() -> None:
    rows = [
        row("p1", system="API_PUSH", reference="AA.ARRANGEMENT/1181932"),
        row("p2", system="DB_DIRECT", reference="SBL-LOAN-010092", balance=Decimal("999")),
    ]

    result = CrossSourcePositionMatcher().link(rows)

    assert len(result.linkages) == 1
    link = result.linkages[0]
    assert link.combined_confidence == ATTRIBUTE_CONFIDENCE
    assert link.signals[MATCH_ATTRIBUTE_FINGERPRINT] == 1.0
    assert result.by_match() == {MATCH_SHARED_REFERENCE: 0, MATCH_ATTRIBUTE_FINGERPRINT: 1}


def test_rate_scale_does_not_split_a_match() -> None:
    rows = [
        row("p1", system="API_PUSH", reference="A/1", rate=Decimal("0.1143")),
        row("p2", system="DB_DIRECT", reference="B/1", rate=Decimal("0.11430000")),
    ]

    assert len(CrossSourcePositionMatcher().link(rows).linkages) == 1


def _pair_missing(field: str) -> list[CanonicalPositionRow]:
    """The same candidate pair with one REQUIRED contract term absent on both sides."""
    if field == "counterparty":
        return [
            row("p1", system="API_PUSH", reference="A/1", counterparty=None),
            row("p2", system="DB_DIRECT", reference="B/1", counterparty=None),
        ]
    if field == "product":
        return [
            row("p1", system="API_PUSH", reference="A/1", product=None),
            row("p2", system="DB_DIRECT", reference="B/1", product=None),
        ]
    if field == "origination":
        return [
            row("p1", system="API_PUSH", reference="A/1", origination=None),
            row("p2", system="DB_DIRECT", reference="B/1", origination=None),
        ]
    return [
        row("p1", system="API_PUSH", reference="A/1", rate=None),
        row("p2", system="DB_DIRECT", reference="B/1", rate=None),
    ]


@pytest.mark.parametrize("field", ["counterparty", "product", "origination", "rate"])
def test_any_missing_required_term_makes_a_row_unassessable_not_matched(field: str) -> None:
    result = CrossSourcePositionMatcher().link(_pair_missing(field))

    assert result.linkages == (), f"{field}=None must not match"
    if field == "counterparty":
        assert result.coverage.unresolved_counterparty == 2
    else:
        assert result.coverage.incomplete_attributes == 2


def test_a_jointly_unstated_maturity_is_agreement_not_a_gap() -> None:
    """A demand deposit has no contractual maturity, in either system.

    Requiring its presence would make this tier blind to the whole retail deposit
    book. Measured on the primary (BK-0PMD7Z5M, 2026-06-30) the relaxation turns
    0 deposit matches into 80, every one of them one-row-per-system.
    """
    rows = [
        row("p1", system="API_PUSH", reference="A/1", maturity=None),
        row("p2", system="DB_DIRECT", reference="B/1", maturity=None),
    ]

    result = CrossSourcePositionMatcher().link(rows)

    assert len(result.linkages) == 1
    link = result.linkages[0]
    # Agreement on one fewer STATED term, so the evidence is scored lower and
    # the linkage says which shape it is.
    assert link.signals["maturity_stated"] == 0.0
    assert link.combined_confidence == ATTRIBUTE_UNSTATED_MATURITY_CONFIDENCE
    assert result.coverage.incomplete_attributes == 0


def test_a_maturity_stated_on_one_side_only_is_not_agreement() -> None:
    # One system knows a term the other does not: "cannot tell", never a match.
    rows = [
        row("p1", system="API_PUSH", reference="A/1", maturity=MATURITY),
        row("p2", system="DB_DIRECT", reference="B/1", maturity=None),
    ]

    result = CrossSourcePositionMatcher().link(rows)

    assert result.linkages == ()
    assert result.coverage.assessed_unmatched == 2


def test_one_disagreeing_contract_term_prevents_a_match() -> None:
    rows = [
        row("p1", system="API_PUSH", reference="A/1", rate=Decimal("0.10")),
        row("p2", system="DB_DIRECT", reference="B/1", rate=Decimal("0.11")),
    ]

    result = CrossSourcePositionMatcher().link(rows)

    assert result.linkages == ()
    # Fully assessable, simply unmatched — a different answer from "unknown".
    assert result.coverage.assessed_unmatched == 2
    assert result.coverage.incomplete_attributes == 0


def test_different_counterparties_do_not_match_on_identical_terms() -> None:
    rows = [
        row("p1", system="API_PUSH", reference="A/1", counterparty="ref:CUST-1"),
        row("p2", system="DB_DIRECT", reference="B/1", counterparty="ref:CUST-2"),
    ]

    assert CrossSourcePositionMatcher().link(rows).linkages == ()


# --- granularity: stated, not silently missed ---------------------------------


def test_one_to_many_group_is_reported_ambiguous_not_as_a_confirmed_pair() -> None:
    # The facility-vs-drawdown shape: one row on one side, several on the other.
    rows = [
        row("facility", system="API_PUSH", reference="A/1"),
        row("draw1", system="DB_DIRECT", reference="B/1"),
        row("draw2", system="DB_DIRECT", reference="B/2"),
    ]

    result = CrossSourcePositionMatcher().link(rows)

    assert len(result.linkages) == 1
    link = result.linkages[0]
    assert link.signals["one_to_one"] == 0.0
    assert link.combined_confidence == AMBIGUOUS_CONFIDENCE
    # Nothing is apportioned or aggregated to force a one-to-one reading.
    assert link.linked_source_ids == ("draw1", "draw2", "facility")


# --- the hard constraint: detect, never resolve -------------------------------


def test_no_linkage_names_a_winner_or_auto_confirms() -> None:
    rows = [
        row("p2", system="DB_DIRECT", reference="SBL-LOAN-1"),
        row("p1", system="EXCEL_CSV", reference="SBL-LOAN-1"),
        row("p3", system="API_PUSH", reference="X/1"),
        row("p4", system="DB_DIRECT", reference="Y/1"),
    ]

    result = CrossSourcePositionMatcher().link(rows)

    assert result.linkages
    for link in result.linkages:
        assert link.auto_confirmed is False
        assert link.signals["system_of_record_determined"] == 0.0
        # The winner field is a stable grouping representative, and it is always
        # one of the members: nothing is invented, nothing is dropped.
        assert link.canonical_winner_id in link.linked_source_ids
        assert link.canonical_winner_id == min(link.linked_source_ids)


def test_every_source_row_is_preserved_on_the_linkage() -> None:
    rows = [
        row("p1", system="DB_DIRECT", reference="SBL-LOAN-1"),
        row("p2", system="EXCEL_CSV", reference="SBL-LOAN-1"),
        row("p3", system="API_PUSH", reference="SBL-LOAN-1"),
    ]

    result = CrossSourcePositionMatcher().link(rows)

    assert len(result.linkages) == 1
    assert set(result.linkages[0].linked_source_ids) == {"p1", "p2", "p3"}
    assert result.linkages[0].signals["distinct_source_systems"] == 3.0


def test_output_is_deterministic_regardless_of_input_order() -> None:
    rows = [
        row("p1", system="DB_DIRECT", reference="SBL-LOAN-1"),
        row("p2", system="EXCEL_CSV", reference="SBL-LOAN-1"),
        row("p3", system="API_PUSH", reference="A/9"),
        row("p4", system="DB_DIRECT", reference="B/9"),
    ]

    def fingerprint(rs: list[CanonicalPositionRow]) -> list[tuple[str, ...]]:
        result = CrossSourcePositionMatcher().link(rs)
        return sorted(link.linked_source_ids for link in result.linkages)

    assert fingerprint(rows) == fingerprint(list(reversed(rows)))


def test_tier_one_match_is_not_re_reported_by_tier_two() -> None:
    # Same reference AND identical contract terms: one linkage, not two.
    rows = [
        row("p1", system="DB_DIRECT", reference="SBL-LOAN-1"),
        row("p2", system="EXCEL_CSV", reference="SBL-LOAN-1"),
    ]

    result = CrossSourcePositionMatcher().link(rows)

    assert len(result.linkages) == 1
    assert result.by_match() == {MATCH_SHARED_REFERENCE: 1, MATCH_ATTRIBUTE_FINGERPRINT: 0}


def test_coverage_accounts_for_every_unmatched_row() -> None:
    rows = [
        row("p1", system="DB_DIRECT", reference="SBL-LOAN-1"),
        row("p2", system="EXCEL_CSV", reference="SBL-LOAN-1"),
        row("p3", system="API_PUSH", reference="A/1", counterparty=None),
        row("p4", system="DB_DIRECT", reference="B/1", rate=None),
        row("p5", system="API_PUSH", reference="C/1", rate=Decimal("0.99")),
    ]

    coverage = CrossSourcePositionMatcher().link(rows).coverage

    assert coverage.considered == 5
    assert coverage.matched_rows == 2
    assert coverage.unresolved_counterparty == 1
    assert coverage.incomplete_attributes == 1
    assert coverage.assessed_unmatched == 1
    unaccounted = (
        coverage.considered
        - coverage.matched_rows
        - coverage.unresolved_counterparty
        - coverage.incomplete_attributes
        - coverage.assessed_unmatched
    )
    assert unaccounted == 0


def test_coverage_names_only_what_actually_matched() -> None:
    """A finding must not list a contested type whose rows never paired.

    On the primary at BK-0PMD7Z5M / 2026-06-30 six position types are contested
    and only the deposit book produces row-level matches; naming all six would
    overstate the evidence, and an overstated finding gets ignored.
    """
    rows = [
        row("d1", system="API_PUSH", reference="A/1", position_type="DEPOSIT"),
        row("d2", system="DB_DIRECT", reference="B/1", position_type="DEPOSIT"),
        row("l1", system="API_PUSH", reference="A/2", rate=Decimal("0.11")),
        row("l2", system="DB_DIRECT", reference="B/2", rate=Decimal("0.22")),
    ]

    coverage = CrossSourcePositionMatcher().link(rows).coverage

    assert coverage.position_types == ("DEPOSIT", "LOAN")
    assert coverage.matched_position_types == ("DEPOSIT",)
    assert coverage.matched_source_systems == ("API_PUSH", "DB_DIRECT")
