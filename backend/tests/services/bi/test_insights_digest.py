"""``fact_sheet_hash`` is value-based: stable under re-derivation, sharp on values.

The live plane re-derives its facts on every refresh with new identifiers and a
new timestamp, so a digest that moved with them would be useless. These pin both
halves: what must NOT move the hash (identity, timing, build, arrival order,
trailing zeros) and what MUST (any value, any designation, the institution, the
date, the catalogue version).
"""

from __future__ import annotations

import json
import typing
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.domain.bi.catalogue import catalogue
from app.domain.bi.catalogue.members import MeasureDef
from app.services.bi.insights import digest, drivers, facts, projections

_AS_OF = date(2026, 6, 30)
_PRIOR = date(2026, 5, 31)
_INSTITUTION = "BK-SAMP0001"


def _provenance(build: UUID | None = None) -> facts.FactProvenance:
    return facts.FactProvenance(
        fact_id=uuid4(), derived_at=datetime.now(tz=UTC), build_id=build or uuid4()
    )


def _measure(member_id: str = "engine.car_pct.crd.live") -> MeasureDef:
    return catalogue().measure(member_id)


def _observed(
    value: str = "13.25",
    *,
    member_id: str = "engine.car_pct.crd.live",
) -> facts.ObservedFact:
    return facts.observed_fact(
        _measure(member_id),
        as_of=_AS_OF,
        provenance=_provenance(),
        value=Decimal(value),
    )


def _sheet(*items: facts.Fact) -> facts.FactSheet:
    return facts.fact_sheet(
        institution_id=_INSTITUTION,
        as_of=_AS_OF,
        catalogue_version=catalogue().version,
        facts=items,
        generated_at=datetime.now(tz=UTC),
    )


def test_re_derivation_with_new_identifiers_does_not_move_the_hash() -> None:
    first = _sheet(_observed())
    second = _sheet(_observed())
    assert {fact.provenance.fact_id for fact in first.facts} != {
        fact.provenance.fact_id for fact in second.facts
    }
    assert digest.fact_sheet_hash(first) == digest.fact_sheet_hash(second)


def test_a_changed_value_moves_the_hash() -> None:
    assert digest.fact_sheet_hash(_sheet(_observed("13.25"))) != digest.fact_sheet_hash(
        _sheet(_observed("13.26"))
    )


def test_trailing_zeros_are_the_same_number() -> None:
    assert digest.fact_sheet_hash(_sheet(_observed("13.25"))) == digest.fact_sheet_hash(
        _sheet(_observed("13.250000"))
    )


def test_a_negative_zero_is_a_zero() -> None:
    assert digest.canonical_decimal(Decimal("-0.00")) == "0"
    assert digest.fact_sheet_hash(_sheet(_observed("0"))) == digest.fact_sheet_hash(
        _sheet(_observed("-0.00"))
    )


def test_a_missing_figure_does_not_hash_as_a_zero() -> None:
    missing = facts.observed_fact(
        _measure(),
        as_of=_AS_OF,
        provenance=_provenance(),
        missing_reason="not_supplied",
    )
    assert digest.fact_sheet_hash(_sheet(missing)) != digest.fact_sheet_hash(_sheet(_observed("0")))


def test_arrival_order_is_not_content() -> None:
    one = _observed("13.25")
    two = _observed("9.5", member_id="engine.lcr_pct.crd.live")
    assert digest.fact_sheet_hash(_sheet(one, two)) == digest.fact_sheet_hash(_sheet(two, one))


def test_the_institution_the_date_and_the_catalogue_version_are_content() -> None:
    base = _sheet(_observed())
    for changed in (
        facts.FactSheet(
            institution_id="BK-OTHER01",
            as_of=base.as_of,
            catalogue_version=base.catalogue_version,
            facts=base.facts,
        ),
        facts.FactSheet(
            institution_id=base.institution_id,
            as_of=date(2026, 5, 31),
            catalogue_version=base.catalogue_version,
            facts=base.facts,
        ),
        facts.FactSheet(
            institution_id=base.institution_id,
            as_of=base.as_of,
            catalogue_version="something-else",
            facts=base.facts,
        ),
    ):
        assert digest.fact_sheet_hash(changed) != digest.fact_sheet_hash(base)


def test_no_identifier_or_timestamp_reaches_the_digest_input() -> None:
    sheet = _sheet(_observed())
    text = json.dumps(digest.fact_sheet_payload(sheet))
    for volatile in (
        str(sheet.facts[0].provenance.fact_id),
        str(sheet.facts[0].provenance.build_id),
        "fact_id",
        "build_id",
        "derived_at",
        "generated_at",
    ):
        assert volatile not in text


def test_the_schema_is_named_in_the_digest_input() -> None:
    assert digest.fact_sheet_payload(_sheet())["schema"] == digest.FACT_SHEET_SCHEMA


def test_every_kind_of_fact_is_covered_by_the_digest() -> None:
    """A new fact type must be taught to the digest, not silently unhashed."""
    covered = {
        type(fact)
        for fact in (
            _observed(),
            facts.movement_fact(
                _measure(),
                as_of=_AS_OF,
                prior_as_of=_PRIOR,
                provenance=_provenance(),
                current=Decimal("13.25"),
                prior=Decimal("12.75"),
            ),
            facts.bridge_fact(
                catalogue().measure("loans.npl_ratio_pct"),
                as_of=_AS_OF,
                prior_as_of=_PRIOR,
                bridge=drivers.BridgeUnavailable("loans.npl_ratio_pct", "component_missing"),
                provenance=_provenance(),
            ),
            facts.projection_fact(
                _measure(),
                as_of=_AS_OF,
                projection=projections.ProjectionUnavailable(
                    "engine.car_pct.crd.live", "too_few_observations"
                ),
                provenance=_provenance(),
            ),
        )
    }
    declared = set(typing.get_args(facts.Fact.__value__))
    assert covered == declared


def test_an_unknown_fact_type_raises_instead_of_hashing_nothing() -> None:
    with pytest.raises(digest.UnhashableFact):
        digest.fact_payload(object())  # pyright: ignore[reportArgumentType]


def test_a_bridge_and_a_projection_carry_their_values_into_the_digest() -> None:
    bridge = drivers.ratio_bridge(
        measure_id="loans.npl_ratio_pct",
        numerator_measure_id="loans.npl_exposure_rc",
        denominator_measure_id="loans.classification_exposure_rc",
        numerator_label="Non-performing loans",
        denominator_label="Loans under classification",
        prior_numerator=Decimal(100),
        prior_denominator=Decimal(1000),
        current_numerator=Decimal(150),
        current_denominator=Decimal(1200),
        value_type="pct",
    )
    other = drivers.ratio_bridge(
        measure_id="loans.npl_ratio_pct",
        numerator_measure_id="loans.npl_exposure_rc",
        denominator_measure_id="loans.classification_exposure_rc",
        numerator_label="Non-performing loans",
        denominator_label="Loans under classification",
        prior_numerator=Decimal(100),
        prior_denominator=Decimal(1000),
        current_numerator=Decimal(151),
        current_denominator=Decimal(1200),
        value_type="pct",
    )

    def sheet_for(built: object) -> facts.FactSheet:
        return _sheet(
            facts.bridge_fact(
                catalogue().measure("loans.npl_ratio_pct"),
                as_of=_AS_OF,
                prior_as_of=_PRIOR,
                bridge=built,  # pyright: ignore[reportArgumentType]
                provenance=_provenance(),
            )
        )

    assert digest.fact_sheet_hash(sheet_for(bridge)) != digest.fact_sheet_hash(sheet_for(other))
