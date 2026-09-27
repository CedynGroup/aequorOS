"""The properties an insight must hold whatever the data does.

These are the four ways a dashboard sentence lies, each pinned here:

* it reports a figure the bank never supplied as ``0`` or as "unchanged";
* it presents a reconciliation check that could not run as a pass;
* it presents analytical output as a filed, certified figure;
* it reads a signed risk figure's sign instead of its size, so a reduction in
  risk is announced as a deterioration (D-013).
"""

from __future__ import annotations

import typing
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.bi.catalogue import catalogue
from app.services.bi.insights import digest, drivers, facts, projections, rules, statements

_AS_OF = date(2026, 6, 30)
_PRIOR = date(2026, 5, 31)
_ALL_GREEN: dict[str, str] = dict.fromkeys(("R1", "R8", "R9", "R10"), "green")

#: A filed, official-tier engine figure — the only class that may read certified.
_FILED = "engine.car_pct.crd.official"
#: The same figure under the regime that only monitors it (D-022 / D-055).
_ADVISORY = "engine.car_pct.s29.official"
#: A signed figure judged on magnitude (D-013 / H-007).
_MAGNITUDE = "engine.worst_eve_change_pct_tier1.crd.live"
#: A figure whose completeness check is the one that can be "never told" (D-046).
_DPD = "engine.par_90_pct.crd.official"


def _provenance() -> facts.FactProvenance:
    return facts.FactProvenance(fact_id=uuid4(), derived_at=datetime.now(tz=UTC), build_id=uuid4())


def _sheet(*items: facts.Fact) -> facts.FactSheet:
    return facts.fact_sheet(
        institution_id="BK-SAMP0001",
        as_of=_AS_OF,
        catalogue_version=catalogue().version,
        facts=items,
        generated_at=datetime.now(tz=UTC),
    )


def _movement(  # noqa: PLR0913 - a movement takes two dates, two figures and a badge
    member_id: str,
    prior: str | None,
    current: str | None,
    *,
    statuses: dict[str, str] | None = None,
    build_overall: str = "green",
    missing_reason: facts.MissingReason | None = None,
) -> facts.MovementFact:
    return facts.movement_fact(
        catalogue().measure(member_id),
        as_of=_AS_OF,
        prior_as_of=_PRIOR,
        provenance=_provenance(),
        statuses=_ALL_GREEN if statuses is None else statuses,
        build_overall=build_overall,
        prior=None if prior is None else Decimal(prior),
        current=None if current is None else Decimal(current),
        missing_reason=missing_reason,
    )


def _all_text(result: rules.InsightSet) -> str:
    return " ".join(
        f"{insight.headline} {insight.detail} {' '.join(insight.qualifiers)}"
        for insight in result.insights
    )


def _by_rule(result: rules.InsightSet, rule_id: str) -> list[object]:
    return [insight for insight in result.insights if insight.rule_id == rule_id]


# ---------------------------------------------------------------------------
# missing data is never zero and never flat
# ---------------------------------------------------------------------------


def test_a_missing_figure_produces_a_data_gap_and_no_movement() -> None:
    result = rules.derive_insights(
        _sheet(_movement(_DPD, None, None, missing_reason="not_supplied"))
    )
    assert [insight.rule_id for insight in result.insights] == ["data_gap"]
    gap = result.insights[0]
    assert gap.statement_class == "data_gap"
    assert "It is not zero, and it has not stayed flat." in gap.detail
    assert "0 %" not in gap.detail
    assert "unchanged" not in gap.detail.lower()


def test_a_fact_cannot_state_a_value_and_a_missing_reason_at_once() -> None:
    with pytest.raises(facts.FactError):
        facts.observed_fact(
            catalogue().measure(_FILED),
            as_of=_AS_OF,
            provenance=_provenance(),
            statuses=_ALL_GREEN,
            value=Decimal("13.2"),
            missing_reason="not_supplied",
        )


def test_a_fact_cannot_state_neither_a_value_nor_a_reason() -> None:
    with pytest.raises(facts.FactError):
        facts.observed_fact(
            catalogue().measure(_FILED),
            as_of=_AS_OF,
            provenance=_provenance(),
            statuses=_ALL_GREEN,
        )


@pytest.mark.parametrize(
    "reason, fragment",
    [
        ("not_supplied", "has not been supplied"),
        ("not_answerable", "cannot be answered"),
        ("not_computed", "has not been computed"),
    ],
)
def test_each_kind_of_gap_says_which_question_went_unanswered(reason: str, fragment: str) -> None:
    result = rules.derive_insights(
        _sheet(
            facts.observed_fact(
                catalogue().measure(_DPD),
                as_of=_AS_OF,
                provenance=_provenance(),
                statuses=_ALL_GREEN,
                build_overall="green",
                missing_reason=reason,  # pyright: ignore[reportArgumentType]
            )
        )
    )
    assert fragment in result.insights[0].detail


# ---------------------------------------------------------------------------
# a check that could not run is never a pass
# ---------------------------------------------------------------------------


def test_an_unassessed_check_is_reported_as_not_checked_and_never_as_a_pass() -> None:
    result = rules.derive_insights(
        _sheet(_movement(_DPD, "3.0", "4.5", statuses={}, build_overall="grey"))
    )
    notices = [i for i in result.insights if i.rule_id == "trust_notice"]
    assert len(notices) == 1
    notice = notices[0]
    assert notice.trust.overall == "grey"
    assert notice.trust.assessed is False
    assert notice.trust.reconciles is False
    assert "have not been checked" in notice.headline
    assert "Not checked is not the same as checked and correct." in notice.detail
    assert all(insight.certified is False for insight in result.insights)
    assert "agree" not in notice.headline


def test_the_grey_state_rides_on_every_insight_drawn_from_it() -> None:
    result = rules.derive_insights(
        _sheet(_movement(_DPD, "3.0", "4.5", statuses={}, build_overall="grey"))
    )
    movements = [i for i in result.insights if i.rule_id == "movement"]
    assert movements
    for insight in movements:
        assert insight.trust.overall == "grey"
        assert any("have not been checked" in text for text in insight.qualifiers)


@pytest.mark.parametrize(
    "state, fragment",
    [
        ("red", "do not agree with the returns"),
        ("amber", "a gap was found"),
        ("grey", "have not been checked"),
    ],
)
def test_every_non_green_state_is_stated_in_the_bank_s_own_words(state: str, fragment: str) -> None:
    result = rules.derive_insights(
        _sheet(_movement(_DPD, "3.0", "4.5", statuses={"R10": state}, build_overall=state))
    )
    assert fragment in _all_text(result).lower() or fragment in _all_text(result)


def test_a_green_book_carries_no_trust_notice() -> None:
    result = rules.derive_insights(_sheet(_movement(_FILED, "13.0", "11.0")))
    assert _by_rule(result, "trust_notice") == []


# ---------------------------------------------------------------------------
# advisory analysis never reads as a filed, certified figure
# ---------------------------------------------------------------------------


def test_an_advisory_metric_is_not_certified_and_says_what_it_is() -> None:
    result = rules.derive_insights(_sheet(_movement(_ADVISORY, "13.0", "11.0")))
    movements = [i for i in result.insights if i.rule_id == "movement"]
    assert movements
    for insight in movements:
        assert insight.certified is False
        assert insight.advisory == "supervisory_monitoring"
        assert any("not a filed return line" in text for text in insight.qualifiers)
    assert "certified" not in _all_text(result).lower()


def test_a_filed_figure_under_a_green_badge_may_be_certified() -> None:
    """The counterpart: ``certified`` is not vacuously false for everything."""
    result = rules.derive_insights(_sheet(_movement(_FILED, "13.0", "11.0")))
    movements = [i for i in result.insights if i.rule_id == "movement"]
    assert movements
    assert all(insight.certified for insight in movements)


def test_a_filed_figure_under_a_grey_badge_is_not_certified() -> None:
    result = rules.derive_insights(
        _sheet(_movement(_FILED, "13.0", "11.0", statuses={}, build_overall="grey"))
    )
    assert all(insight.certified is False for insight in result.insights)


# ---------------------------------------------------------------------------
# direction: D-013 magnitude
# ---------------------------------------------------------------------------


def test_a_magnitude_judged_gain_is_not_reported_as_adverse() -> None:
    result = rules.derive_insights(_sheet(_movement(_MAGNITUDE, "-12.5", "-8.0")))
    movements = [i for i in result.insights if i.rule_id == "movement"]
    assert movements
    for insight in movements:
        assert insight.favourability == "favourable"
        assert insight.emphasis != "high"
        assert "shrank" in insight.detail
        assert "adverse" not in insight.detail.lower()


def test_a_magnitude_judged_loss_is_reported_as_adverse() -> None:
    result = rules.derive_insights(_sheet(_movement(_MAGNITUDE, "-8.0", "-12.5")))
    movements = [i for i in result.insights if i.rule_id == "movement"]
    assert movements
    assert all(insight.favourability == "adverse" for insight in movements)
    assert "grew" in movements[0].detail


def test_a_risk_ratio_rising_is_adverse_and_a_buffer_rising_is_favourable() -> None:
    risk = rules.derive_insights(_sheet(_movement("loans.npl_ratio_pct", "3.0", "4.5")))
    buffer_ = rules.derive_insights(_sheet(_movement(_FILED, "11.0", "13.0")))
    assert [i.favourability for i in risk.insights if i.rule_id == "movement"] == ["adverse"]
    assert [i.favourability for i in buffer_.insights if i.rule_id == "movement"] == ["favourable"]


# ---------------------------------------------------------------------------
# the sentences state only what the facts hold
# ---------------------------------------------------------------------------


def test_a_movement_states_both_figures_exactly_as_the_facts_hold_them() -> None:
    result = rules.derive_insights(_sheet(_movement(_FILED, "13.257", "11.004")))
    detail = result.insights[0].detail
    assert "13.257" in detail
    assert "11.004" in detail
    assert "2.253" in detail
    assert "approx" not in detail.lower()
    assert "around" not in detail.lower()


def test_an_immaterial_move_earns_no_sentence() -> None:
    result = rules.derive_insights(_sheet(_movement(_FILED, "13.00", "13.01")))
    assert result.insights == ()


def test_materiality_is_a_presentation_choice_the_caller_can_set() -> None:
    sheet = _sheet(_movement(_FILED, "13.00", "13.01"))
    widened = rules.derive_insights(
        sheet, rules.InsightPolicy(material_relative_change=Decimal("0.0001"))
    )
    assert [i.rule_id for i in widened.insights] == ["movement"]


def test_a_move_from_a_zero_base_is_not_given_an_invented_proportion() -> None:
    result = rules.derive_insights(_sheet(_movement(_FILED, "0", "13.0")))
    assert result.insights == ()


# ---------------------------------------------------------------------------
# attribution and projection
# ---------------------------------------------------------------------------


def _bridge_fact() -> facts.BridgeFact:
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
        direction="lower_better",
        value_type="pct",
    )
    return facts.bridge_fact(
        catalogue().measure("loans.npl_ratio_pct"),
        as_of=_AS_OF,
        prior_as_of=_PRIOR,
        bridge=bridge,
        provenance=_provenance(),
        statuses=_ALL_GREEN,
        build_overall="green",
    )


def test_an_attribution_names_the_biggest_driver_and_says_the_parts_add_up() -> None:
    result = rules.derive_insights(_sheet(_bridge_fact()))
    attribution = [i for i in result.insights if i.rule_id == "attribution"]
    assert len(attribution) == 1
    detail = attribution[0].detail
    assert "Non-performing loans" in attribution[0].headline
    assert "moved by 2.5 percentage points" in detail
    assert "Non-performing loans contributed 5 percentage points" in detail
    assert "Loans under classification contributed -1.6667 percentage points" in detail
    assert "the two moving together contributed -0.8333 percentage points" in detail
    assert "add up to the whole move exactly" in detail


def test_an_unavailable_bridge_produces_no_attribution_at_all() -> None:
    unavailable = facts.bridge_fact(
        catalogue().measure("loans.npl_ratio_pct"),
        as_of=_AS_OF,
        prior_as_of=_PRIOR,
        bridge=drivers.BridgeUnavailable("loans.npl_ratio_pct", "denominator_zero"),
        provenance=_provenance(),
        statuses=_ALL_GREEN,
        build_overall="green",
    )
    result = rules.derive_insights(_sheet(unavailable))
    assert _by_rule(result, "attribution") == []


def _projection_fact(*values: str) -> facts.ProjectionFact:
    projection = projections.project(
        _FILED,
        [
            projections.Observation(date(2026, month, 28), Decimal(value))
            for month, value in zip((1, 2, 3), values, strict=True)
        ],
        horizon=date(2026, 12, 31),
    )
    return facts.projection_fact(
        catalogue().measure(_FILED),
        as_of=_AS_OF,
        projection=projection,
        provenance=_provenance(),
        statuses=_ALL_GREEN,
        build_overall="green",
    )


def test_a_projection_is_written_as_a_projection_and_never_as_an_observation() -> None:
    result = rules.derive_insights(_sheet(_projection_fact("13.0", "12.5", "12.0")))
    projection = [i for i in result.insights if i.rule_id == "projection"]
    assert len(projection) == 1
    assert projection[0].statement_class == "projection"
    assert "would reach" in projection[0].headline
    assert "This is a projection, not something that has happened" in projection[0].detail
    assert projections.ASSUMPTION in projection[0].detail


def test_a_projection_that_could_not_be_drawn_produces_no_sentence() -> None:
    fact = facts.projection_fact(
        catalogue().measure(_FILED),
        as_of=_AS_OF,
        projection=projections.ProjectionUnavailable(_FILED, "observation_missing"),
        provenance=_provenance(),
        statuses=_ALL_GREEN,
        build_overall="green",
    )
    result = rules.derive_insights(_sheet(fact))
    assert result.insights == ()


# ---------------------------------------------------------------------------
# the set as a whole
# ---------------------------------------------------------------------------


def test_the_set_is_bound_to_the_hash_of_the_facts_it_came_from() -> None:
    sheet = _sheet(_movement(_FILED, "13.0", "11.0"))
    assert rules.derive_insights(sheet).fact_sheet_hash == digest.fact_sheet_hash(sheet)


def test_the_same_facts_always_produce_the_same_sentences_in_the_same_order() -> None:
    def sheet() -> facts.FactSheet:
        return _sheet(
            _movement(_FILED, "13.0", "11.0"),
            _movement("loans.npl_ratio_pct", "3.0", "4.5", statuses={}, build_overall="grey"),
            _bridge_fact(),
            _projection_fact("13.0", "12.5", "12.0"),
        )

    first, second = rules.derive_insights(sheet()), rules.derive_insights(sheet())
    assert [i.id for i in first.insights] == [i.id for i in second.insights]
    assert [i.headline for i in first.insights] == [i.headline for i in second.insights]
    assert first.fact_sheet_hash == second.fact_sheet_hash


def test_trust_and_gaps_are_shown_before_anything_else() -> None:
    result = rules.derive_insights(
        _sheet(
            _movement(_FILED, "13.0", "11.0", statuses={}, build_overall="red"),
            _movement(_DPD, None, None, missing_reason="not_supplied"),
            _projection_fact("13.0", "12.5", "12.0"),
        )
    )
    assert [i.statement_class for i in result.insights][0] == "trust_notice"
    assert result.insights[-1].statement_class == "projection"


def test_a_capped_strip_says_it_was_capped() -> None:
    sheet = _sheet(
        _movement(_FILED, "13.0", "11.0"),
        _movement("loans.npl_ratio_pct", "3.0", "4.5"),
        _bridge_fact(),
    )
    result = rules.derive_insights(sheet, rules.InsightPolicy(max_insights=1))
    assert len(result.insights) == 1
    assert result.truncated is True


def test_the_evidence_is_a_stable_key_and_never_the_volatile_fact_id() -> None:
    sheet = _sheet(_movement(_FILED, "13.0", "11.0"))
    result = rules.derive_insights(sheet)
    fact = sheet.facts[0]
    assert result.insights[0].evidence == (fact.key,)
    assert str(fact.provenance.fact_id) not in result.insights[0].id


def test_the_ui_legend_names_every_class_the_rules_can_produce() -> None:
    """A new statement class must reach the legend, not appear unlabelled."""
    assert set(rules.statement_classes()) == set(typing.get_args(statements.StatementClass))
    produced = {
        insight.statement_class
        for insight in rules.derive_insights(
            _sheet(
                _movement(_FILED, "13.0", "11.0", statuses={}, build_overall="grey"),
                _movement(_DPD, None, None, missing_reason="not_supplied"),
                _bridge_fact(),
                _projection_fact("13.0", "12.5", "12.0"),
            ),
            rules.InsightPolicy(max_insights=99),
        ).insights
    }
    assert produced == set(rules.statement_classes())
