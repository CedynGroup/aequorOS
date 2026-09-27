"""Target variants: what a budget may be stated against, and what it may say.

Three founder decisions are asserted here rather than described:

* **D-062** — ``attainment_pct`` is not emitted where lower is better. The test
  is written over the whole catalogue, in both directions, so a measure that
  arrives ``lower_better`` tomorrow cannot acquire an attainment variant by
  default and one that arrives ``higher_better`` cannot lose it.
* **D-063** — ``variance_pct`` on a percentage-valued base is the difference in
  PERCENTAGE POINTS, and on an amount or count base the relative overshoot; the
  label says which, per measure.
* **D-064** — every variant reads ONE fact table, so the compiler's
  one-fact-table rule holds without being relaxed.

Plus the structural property the whole design rests on: a variant is DERIVED
from its base, so it cannot be born with a broader module, sensitivity,
entitlement, grain or dimension set than the measure it budgets, and it can
never badge itself certified.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.bi.catalogue import Catalogue, MeasureDef, catalogue
from app.domain.bi.catalogue.targets import (
    ATTAINMENT_PCT_SUFFIX,
    BANK_WIDE_SCOPE,
    TARGET_SUFFIX,
    TARGET_TABLE,
    TARGET_VERSION,
    VARIANCE_PCT_SUFFIX,
    VARIANT_SUFFIXES,
    is_target_variant,
    is_targetable,
    variant_id,
    variants_for,
)
from app.schemas.bi import BiQuery, BiTime
from app.services.bi.authorization import query_members, scope_pairs
from app.services.bi.compiler import (  # pyright: ignore[reportPrivateUsage]
    _POPULATION_COLUMNS,
    _resolve,
)

AS_OF = date(2026, 6, 30)

#: Fields a variant may never restate: they decide who may read it.
INHERITED_FIELDS = (
    "module",
    "sensitivity",
    "entitlement",
    "grain",
    "allowed_dimensions",
    "advisory_designation",
)


@pytest.fixture(scope="module")
def cat() -> Catalogue:
    return catalogue()


def _bases(cat: Catalogue) -> list[MeasureDef]:
    return [m for m in cat.measures() if not is_target_variant(m.id)]


def _base_of(cat: Catalogue, variant: MeasureDef) -> MeasureDef:
    return cat.measure(variant.id.rsplit(".", 1)[0])


# --- what is targetable -------------------------------------------------------------------


def test_targetability_is_derived_from_what_a_measure_declares(cat: Catalogue) -> None:
    targetable = [m for m in _bases(cat) if is_targetable(m)]
    assert targetable, "nothing is targetable at all"
    for measure in targetable:
        assert measure.value_type in ("amount", "pct", "ratio", "count"), measure.id
        assert measure.aggregation not in ("top_n_share", "hhi"), measure.id
        assert measure.measure_kind in ("portfolio", "certified_engine"), measure.id
    # A concentration measure computes at a second grouping level; a variance
    # over one would need a third the compiler has no shape for.
    assert not is_targetable(cat.measure("loans.sector_hhi"))
    assert not is_targetable(cat.measure("loans.largest_single_name_share_pct"))


def test_only_the_official_tier_of_an_engine_metric_is_targetable(cat: Catalogue) -> None:
    """A budget is compared against the figure the bank filed, not the live one."""
    assert is_targetable(cat.measure("engine.car_pct.crd.official"))
    assert not is_targetable(cat.measure("engine.car_pct.crd.live"))
    for measure in cat.target_measures():
        base = _base_of(cat, measure)
        assert base.engine_rule is None or base.engine_rule.tier == "official", measure.id


def test_a_variant_is_never_itself_targetable(cat: Catalogue) -> None:
    """There is no ``.target.target``."""
    for measure in cat.target_measures():
        assert is_target_variant(measure.id), measure.id
        assert not is_targetable(measure), measure.id
    assert set(cat.target_measures()) == set(
        variant for base in _bases(cat) if is_targetable(base) for variant in variants_for(base)
    )


# --- the variants cannot be born broader ---------------------------------------------------


def test_every_variant_inherits_its_bases_scope_exactly(cat: Catalogue) -> None:
    for variant in cat.target_measures():
        base = _base_of(cat, variant)
        for field in INHERITED_FIELDS:
            assert getattr(variant, field) == getattr(base, field), (variant.id, field)


def test_the_variants_add_no_authorization_scope_pair(cat: Catalogue) -> None:
    """The provable-superset statement, in one line.

    Every variant evaluates under a ``(module, sensitivity)`` pair its base
    already put to the evaluator, so 588 new members cost the catalogue
    endpoint no additional authorization probe and can reach no reader the
    base could not.
    """
    without_variants = tuple(m for m in cat.members() if not is_target_variant(m.id))
    assert set(scope_pairs(without_variants)) == set(scope_pairs(cat.members()))


def test_no_variant_is_certified_and_none_carries_an_engine_rule(cat: Catalogue) -> None:
    """A target is the BANK's number; the platform certifies none of it (D-022)."""
    for variant in cat.target_measures():
        assert variant.engine_rule is None, variant.id
        assert variant.certified is False, variant.id
        assert variant.measure_kind == "portfolio", variant.id
    # ... even when the figure it is compared against is a sealed engine copy.
    base = cat.measure("engine.car_pct.crd.official")
    assert base.certified is True
    assert cat.measure(variant_id(base.id, TARGET_SUFFIX)).certified is False


def test_only_the_target_variant_drops_the_platforms_own_caveats(cat: Catalogue) -> None:
    for variant in cat.target_measures():
        base = _base_of(cat, variant)
        if variant.id.endswith(f".{TARGET_SUFFIX}"):
            assert variant.reconciliation_checks == (), variant.id
            assert variant.thresholds_source is None, variant.id
        else:
            assert variant.reconciliation_checks == base.reconciliation_checks, variant.id
            assert variant.thresholds_source == base.thresholds_source, variant.id


# --- D-062 ---------------------------------------------------------------------------------


def test_attainment_is_never_emitted_where_lower_is_better(cat: Catalogue) -> None:
    """``actual / target = 120 %`` on an NPL ratio is a MISS that reads as a win."""
    emitted = {m.id for m in cat.target_measures() if m.id.endswith(f".{ATTAINMENT_PCT_SUFFIX}")}
    for base in _bases(cat):
        if not is_targetable(base):
            continue
        has_attainment = variant_id(base.id, ATTAINMENT_PCT_SUFFIX) in emitted
        lower_is_better = base.favourable_direction in (
            "lower_better",
            "magnitude_lower_better",
        )
        assert has_attainment is not lower_is_better, (base.id, base.favourable_direction)
    # Named cases, in both directions.
    assert variant_id("loans.npl_ratio_pct", ATTAINMENT_PCT_SUFFIX) not in cat
    magnitude = "engine.worst_eve_change_pct_tier1.crd.official"
    assert variant_id(magnitude, ATTAINMENT_PCT_SUFFIX) not in cat
    assert variant_id("engine.car_pct.crd.official", ATTAINMENT_PCT_SUFFIX) in cat


def test_a_lower_better_measure_still_gets_target_variance_and_variance_pct(
    cat: Catalogue,
) -> None:
    base = cat.measure("loans.npl_ratio_pct")
    assert base.favourable_direction == "lower_better"
    emitted = {v.id for v in variants_for(base)}
    assert emitted == {
        variant_id(base.id, suffix)
        for suffix in VARIANT_SUFFIXES
        if suffix != ATTAINMENT_PCT_SUFFIX
    }


# --- D-063 ---------------------------------------------------------------------------------


def test_a_percentage_base_states_its_variance_in_percentage_points(cat: Catalogue) -> None:
    base = cat.measure("loans.npl_ratio_pct")
    variant = cat.measure(variant_id(base.id, VARIANCE_PCT_SUFFIX))
    # The difference itself, not a ratio: 6.0 pp against 5.0 pp reads 1.0 pp.
    assert variant.aggregation == "sum"
    assert variant.source.column == "variance_value"
    assert variant.numerator is None and variant.denominator is None
    assert "percentage points" in variant.label


def test_an_amount_base_keeps_the_relative_variance(cat: Catalogue) -> None:
    base = cat.measure("loans.balance_rc")
    assert base.value_type == "amount"
    variant = cat.measure(variant_id(base.id, VARIANCE_PCT_SUFFIX))
    assert variant.aggregation == "ratio_of_sums"
    assert variant.numerator == variant_id(base.id, "variance")
    assert variant.denominator == variant_id(base.id, TARGET_SUFFIX)
    assert variant.value_type == "pct"
    assert "percentage points" not in variant.label


def test_every_variance_pct_label_names_its_convention(cat: Catalogue) -> None:
    for variant in cat.target_measures():
        if not variant.id.endswith(f".{VARIANCE_PCT_SUFFIX}"):
            continue
        base = _base_of(cat, variant)
        points = base.value_type == "pct"
        assert ("percentage points" in variant.label) is points, variant.id
        assert (variant.aggregation == "sum") is points, variant.id


def test_a_bare_ratio_base_gets_no_variance_pct_at_all(cat: Catalogue) -> None:
    """Neither convention is settled for a ``ratio``; ``.variance`` is unambiguous."""
    base = cat.measure("engine.duration_gap.crd.official")
    assert base.value_type == "ratio" and is_targetable(base)
    assert variant_id(base.id, VARIANCE_PCT_SUFFIX) not in cat
    assert variant_id(base.id, "variance") in cat


# --- D-064: one fact table, safe division, NULL not zero ------------------------------------


def test_every_variant_reads_the_one_pre_matched_target_fact(cat: Catalogue) -> None:
    for variant in cat.target_measures():
        assert variant.table == TARGET_TABLE, variant.id
        assert variant.time_behaviour == "stock", variant.id


@pytest.mark.parametrize("suffix", VARIANT_SUFFIXES)
def test_a_variant_query_satisfies_the_compilers_one_fact_rule(cat: Catalogue, suffix: str) -> None:
    """The rule is NOT relaxed: the variant and everything it composes are one fact."""
    member_id = variant_id("loans.balance_rc", suffix)
    resolved = _resolve(cat, BiQuery(measures=[member_id], time=BiTime(as_of=AS_OF)), ())
    assert resolved.fact == TARGET_TABLE
    for composed_id in resolved.member_ids:
        assert cat.measure(composed_id).table == TARGET_TABLE, composed_id


@pytest.mark.parametrize("suffix", VARIANT_SUFFIXES)
def test_the_authorization_walk_stays_a_provable_superset(cat: Catalogue, suffix: str) -> None:
    """Composition rides the EXISTING numerator / denominator, which both walks read."""
    member_id = variant_id("loans.balance_rc", suffix)
    query = BiQuery(measures=[member_id], time=BiTime(as_of=AS_OF))
    walked = {member.id for member in query_members(cat, query)}
    compiled = set(_resolve(cat, query, ()).member_ids)
    assert compiled <= walked, sorted(compiled - walked)


def test_both_percentage_variants_divide_through_the_compilers_own_safe_division(
    cat: Catalogue,
) -> None:
    """A target of zero is legitimate, so the division must be the safe one.

    ``ratio_of_sums`` is compiled by ``_ratio`` — ``num / nullif(den, 0)`` —
    which is the platform's one safe division. No second one is written here.
    """
    for base_id in ("loans.balance_rc", "engine.car_pct.crd.official"):
        base = cat.measure(base_id)
        attainment = cat.measure(variant_id(base_id, ATTAINMENT_PCT_SUFFIX))
        denominator_id = variant_id(base_id, TARGET_SUFFIX)
        assert attainment.aggregation == "ratio_of_sums"
        assert attainment.denominator == denominator_id
        assert cat.measure(denominator_id).aggregation == "sum"
        assert base.value_type in ("amount", "pct")


def test_every_variant_is_pinned_to_the_bank_wide_scope_and_one_register_version(
    cat: Catalogue,
) -> None:
    """The two scopes are NEVER summed, made impossible rather than guarded.

    A variant's population is the bank-wide row of one measure under one
    register version, so no query — grouped or not — can add a bank-wide target
    to the scoped ones stored beside it.
    """
    for variant in cat.target_measures():
        base = _base_of(cat, variant)
        by_column = {f.column: f for f in variant.row_filters}
        assert set(by_column) == {"measure_id", "scope_dimension", "target_version"}, variant.id
        assert by_column["measure_id"].values == (base.id,)
        assert by_column["scope_dimension"].values == (BANK_WIDE_SCOPE,)
        assert by_column["target_version"].values == (TARGET_VERSION,)
        assert all(f.op == "in" for f in variant.row_filters), variant.id


def test_the_discriminators_are_population_filters_so_a_missing_target_is_null() -> None:
    """The one rule that decides whether a blank renders as ``—`` or as ``0``.

    As a SELECTION each discriminator would contribute ``0`` for the rows of
    every OTHER measure, so a bank holding a budget for anything else on the
    same date would see an untargeted measure render zero. As a POPULATION they
    contribute NULL, and a sum of nothing is NULL.
    """
    assert {"measure_id", "scope_dimension", "target_version"} <= _POPULATION_COLUMNS


# --- production copy ------------------------------------------------------------------------


def test_every_variant_label_is_production_copy(cat: Catalogue) -> None:
    for variant in cat.target_measures():
        base = _base_of(cat, variant)
        assert variant.label.startswith(f"{base.label} · "), variant.id
        assert variant.label != variant.id
        assert variant.description.strip(), variant.id
        # A wire key never reaches the label: "Variance against target (%)",
        # never "variance_pct".
        for suffix in VARIANT_SUFFIXES:
            if "_" in suffix:
                assert suffix not in variant.label, (variant.id, suffix)
        assert variant.id not in variant.label, variant.id
