"""The fact assembler: what it selects, and what it refuses to say.

``app/services/bi/insights/assemble.py`` is the only impure piece of the insights
package, and the four properties asserted here are the reasons it exists rather
than the route doing the reading itself:

* **a missing figure never becomes a zero or a "flat"** — the only path from a
  ``NULL`` cell to a fact is the ``missing_reason`` argument, so no insight can
  say a figure stayed the same when the platform does not know what it was;
* **the headline set is derived, and regime-correct** — a bank's headline figures
  come from its own CRD authorities and an SDI's do not include them, decided by
  the SAME registry resolution the mart builder used when it stamped the rows;
* **target variants are excluded structurally**, not by the shape of their ids: a
  variance against a register the institution has not fed is a dash, not a
  headline;
* **the read is bounded** — one insights request is charged once against the read
  budget, so the number of compiled statements it may run is a constant here.

The source-reading test at the end is the cheapest guard on the first property:
the pressure to write ``value or 0`` arrives with the first ``None`` a developer
sees in a debugger.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.bi.catalogue import ColumnRef, EngineRule, MeasureDef, catalogue
from app.services.bi.insights.assemble import (
    HEADLINE_MEASURE_CAP,
    MAX_COMPILED_READS,
    _Answer,
    _grouped,
    _is_bridgeable,
    _movement_or_gap,
    _point_time,
    default_compare_to,
    engine_measure_applies,
    engine_regime,
    headline_measures,
)
from app.services.bi.insights.facts import MovementFact, ObservedFact

_AS_OF = date(2026, 6, 30)

_SOURCE = Path("app/services/bi/insights/assemble.py")

_DOCSTRING = re.compile(r'(""".*?"""|\'\'\'.*?\'\'\')', re.DOTALL)


def _code(path: Path) -> str:
    """The module's executable text: docstrings and comments removed.

    Both have to go. This file's own prose names the patterns it forbids — that is
    what makes the prohibition legible — so a scan that read the docstrings would
    convict the explanation instead of the code.
    """
    source = _DOCSTRING.sub("", path.read_text(encoding="utf-8"))
    return "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))


def _cat():  # noqa: ANN202 - the catalogue's own type, resolved by the caller
    return catalogue()


# --- the prior period ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [
        (date(2026, 6, 30), date(2026, 5, 31)),
        (date(2026, 3, 31), date(2026, 2, 28)),
        (date(2026, 1, 31), date(2025, 12, 31)),
        (date(2026, 6, 15), date(2026, 5, 15)),
    ],
)
def test_the_prior_period_is_the_pack_windows_own_convention(as_of: date, expected: date) -> None:
    """A month end maps to a month end, so the prior column has a snapshot to read.

    The pack widgets' comparison and the insight strip beside them must land on
    the same date or the page contradicts itself.
    """
    assert default_compare_to(as_of) == expected


# --- which measures earn a headline --------------------------------------------------------


def test_a_banks_headline_set_is_its_own_regime_and_an_sdis_is_not() -> None:
    """D-070's mechanism, at the measure level: a CRD figure is not an SDI's."""
    cat = _cat()
    bank = {m.id for m in headline_measures(cat, institution_class="bank", capital_regime="crd")}
    sdi = {m.id for m in headline_measures(cat, institution_class="sdi", capital_regime="s29")}
    assert "engine.car_pct.crd.official" in bank
    assert not any(".crd." in member_id for member_id in sdi)
    # The accounting standard is shared by both classes, so its measures are not
    # withheld from either: this is a regime question, not a licence-class one.
    assert any(".ifrs9." in member_id for member_id in sdi)
    assert bank and sdi


def test_the_headline_set_is_capped_and_deterministic() -> None:
    cat = _cat()
    first = headline_measures(cat, institution_class="bank", capital_regime="crd")
    again = headline_measures(cat, institution_class="bank", capital_regime="crd")
    assert [m.id for m in first] == [m.id for m in again]
    assert 0 < len(first) <= HEADLINE_MEASURE_CAP


def test_every_module_with_a_candidate_reaches_the_strip() -> None:
    """No module is crowded out by another module's variants of one figure.

    Without the interleave one module's four earnings-at-risk figures take a third
    of the strip and another module's headline ratio never appears at all. The
    available set is read from the catalogue with the cap lifted, so this cannot
    pass by asserting the shape the code happens to produce.
    """
    cat = _cat()
    available = headline_measures(cat, institution_class="bank", capital_regime="crd", cap=10_000)
    selected = headline_measures(cat, institution_class="bank", capital_regime="crd")
    counts: dict[str, int] = {}
    for measure in selected:
        counts[measure.module] = counts.get(measure.module, 0) + 1
    assert len(available) > len(selected), "the cap is not exercised; this test is vacuous"
    assert set(counts) == {measure.module for measure in available}
    # A module may hold two slots where it has both a certified figure and a
    # bridgeable ratio; it may never hold a share of the strip out of proportion.
    assert max(counts.values()) <= 3, counts


def test_a_target_variant_is_never_a_headline() -> None:
    """A variance against a register the bank has not fed is a dash, not news.

    Excluded structurally — the catalogue's own ``engine_measures`` /
    ``portfolio_measures`` accessors do not carry target variants — rather than by
    matching the shape of an id.
    """
    cat = _cat()
    target_ids = {measure.id for measure in cat.target_measures()}
    assert target_ids, "the catalogue publishes no target variants; this test is vacuous"
    for klass, regime in (("bank", "crd"), ("sdi", "s29")):
        selected = headline_measures(cat, institution_class=klass, capital_regime=regime)
        assert not target_ids & {measure.id for measure in selected}


def test_a_candidates_own_numerator_is_not_also_a_headline() -> None:
    """Its move is already stated exactly, as a leg of the ratio's bridge."""
    cat = _cat()
    selected = headline_measures(cat, institution_class="bank", capital_regime="crd")
    chosen = {measure.id for measure in selected}
    for measure in selected:
        for component in (measure.numerator, measure.denominator):
            assert component not in chosen, f"{component} rides in twice via {measure.id}"


def test_at_least_one_headline_can_be_bridged() -> None:
    """An engine copy is a ``last_value``; only a ratio can be attributed.

    If no bridgeable ratio survives the cut the attribution rule can never fire,
    and the exact bridge T4 built is dead code in production.
    """
    cat = _cat()
    selected = headline_measures(cat, institution_class="bank", capital_regime="crd")
    assert any(_is_bridgeable(measure) for measure in selected)


# --- which engine measures are this institution's ------------------------------------------


def test_an_engine_measure_with_an_unreadable_regime_is_refused_not_admitted() -> None:
    """Fail closed: an engine figure whose authority cannot be identified is not
    asserted about an institution."""
    measure = MeasureDef(
        id="engine.car_pct.crd.official.renamed",
        module="cap",
        sensitivity="aggregated",
        label="A capital figure whose id no longer names its regime",
        source=ColumnRef("bi_fact_engine_metric", "value"),
        measure_kind="certified_engine",
        engine_rule=EngineRule(metric_id="car_pct", module="capital", tier="official"),
        advisory_designation="filed",
        favourable_direction="higher_better",
        value_type="pct",
    )
    assert engine_regime(measure) is None
    assert not engine_measure_applies(measure, institution_class="bank", capital_regime="crd")


def test_a_measure_reading_the_banks_own_book_has_no_regime_to_check() -> None:
    cat = _cat()
    measure = cat.measure("loans.npl_ratio_pct")
    assert engine_regime(measure) is None
    assert engine_measure_applies(measure, institution_class="bank", capital_regime="crd")
    assert engine_measure_applies(measure, institution_class="sdi", capital_regime="s29")


# --- a missing figure ----------------------------------------------------------------------


def _movement(current: Decimal | None, prior: Decimal | None):  # noqa: ANN202 - a Fact
    cat = _cat()
    return _movement_or_gap(
        cat.measure("engine.car_pct.crd.official"),
        as_of=_AS_OF,
        compare_to=default_compare_to(_AS_OF),
        current=current,
        prior=prior,
    )


def test_both_figures_present_is_a_movement() -> None:
    fact = _movement(Decimal("14.25"), Decimal("13.80"))
    assert isinstance(fact, MovementFact)
    assert fact.delta == Decimal("0.45")
    assert fact.missing_reason is None


def test_a_missing_current_figure_is_a_stated_gap_and_never_a_zero() -> None:
    fact = _movement(None, Decimal("13.80"))
    assert isinstance(fact, ObservedFact)
    assert fact.value is None
    assert fact.missing_reason == "not_computed"


def test_a_missing_prior_figure_asserts_no_movement_at_all() -> None:
    """Not a movement of zero, and not a gap in the current figure either.

    The figure IS known; what is unknown is the comparison. An ``ObservedFact``
    carrying the value says exactly that, and no rule can read a move out of it.
    """
    fact = _movement(Decimal("14.25"), None)
    assert isinstance(fact, ObservedFact)
    assert fact.value == Decimal("14.25")
    assert fact.missing_reason is None


@pytest.mark.parametrize(("current", "prior"), [(None, None), (None, Decimal("0"))])
def test_no_pair_of_absent_figures_can_produce_a_movement(
    current: Decimal | None, prior: Decimal | None
) -> None:
    fact = _movement(current, prior)
    assert not isinstance(fact, MovementFact)


def test_an_absent_cell_reads_as_absent_and_not_as_zero() -> None:
    """The one place a database value becomes a figure, exercised directly."""
    answer = _Answer(columns={("engine.car_pct.crd.official", "current"): 0}, rows=((None,),))
    assert answer.value("engine.car_pct.crd.official", "current") is None
    assert answer.value("engine.car_pct.crd.official", "prior") is None
    assert answer.value("nope", "current") is None


def test_a_read_that_returned_no_rows_yields_no_figure() -> None:
    answer = _Answer(columns={("engine.car_pct.crd.official", "current"): 0}, rows=())
    assert answer.value("engine.car_pct.crd.official", "current") is None


# --- how the reads are grouped and windowed -----------------------------------------------


def test_measures_are_grouped_by_fact_table_and_clock() -> None:
    """One ``BiQuery`` resolves to one fact table and one time behaviour."""
    cat = _cat()
    selected = headline_measures(cat, institution_class="bank", capital_regime="crd")
    groups = _grouped(cat, selected)
    assert groups
    for group in groups:
        assert {measure.table for measure in group.measures} == {group.table}
        assert {measure.time_behaviour for measure in group.measures} == {group.time_behaviour}
        assert len(group.measure_ids) <= 25
    assert sum(len(group.measures) for group in groups) == len(selected)
    # Every group needs a point read and may need a series read, and the budget
    # charges the request once, so the constant has to cover what is grouped.
    assert 2 * len(groups) <= MAX_COMPILED_READS


def test_a_bridgeable_ratio_reads_its_components_in_the_same_statement() -> None:
    cat = _cat()
    ratio = cat.measure("loans.npl_ratio_pct")
    group = _grouped(cat, [ratio])[0]
    assert ratio.numerator in group.measure_ids
    assert ratio.denominator in group.measure_ids


def test_a_position_is_read_at_a_date_and_an_amount_over_a_period() -> None:
    """A flow measure has no value "at" a date, so its window is the month.

    The compiler then derives an equal-length prior window ending at the
    comparison date — the rule ``provenance.data_window`` states — so a flow's
    prior column is a comparable period and not a single day.
    """
    cat = _cat()
    stock = _grouped(cat, [cat.measure("loans.npl_ratio_pct")])[0]
    prior = default_compare_to(_AS_OF)
    stock_time = _point_time(stock, _AS_OF, prior)
    assert stock_time.as_of == _AS_OF
    assert stock_time.range is None
    assert stock_time.compare_to == prior

    flow = _grouped(cat, [cat.measure("events.write_off_rc")])[0]
    flow_time = _point_time(flow, _AS_OF, prior)
    assert flow_time.as_of is None
    assert flow_time.range is not None
    assert (flow_time.range.start, flow_time.range.end) == (date(2026, 6, 1), _AS_OF)
    assert flow_time.compare_to == prior


# --- the source guard ----------------------------------------------------------------------


def test_the_assembler_carries_no_fallback_that_could_invent_a_figure() -> None:
    """No ``or 0``, no ``Decimal(0)`` default, no ``fillna``."""
    code = _code(_SOURCE)
    forbidden = (
        r"\bor\s+0\b",
        r"\bor\s+Decimal\(",
        r"Decimal\(\s*0\s*\)\s*(?:if|$)",
        r"\.fillna\(",
        r"\bdefault\s*=\s*Decimal\(",
    )
    for pattern in forbidden:
        assert not re.search(pattern, code), f"{pattern} appears in {_SOURCE}"


def test_the_assembler_never_writes_its_own_aggregation() -> None:
    """Every figure comes from the query path's compiler, not from a second one."""
    code = _code(_SOURCE)
    for forbidden in ("func.sum(", "func.count(", "text(", "select("):
        assert forbidden not in code, f"{forbidden} appears in {_SOURCE}"
    assert "compile_query(" in code
