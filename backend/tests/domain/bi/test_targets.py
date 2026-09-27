"""Target variants: what a budget may be stated against, and what it may say.

Five founder decisions are asserted here rather than described:

* **D-062** — ``attainment_pct`` is not emitted where lower is better. The test
  is written over the whole catalogue, in both directions, so a measure that
  arrives ``lower_better`` tomorrow cannot acquire an attainment variant by
  default and one that arrives ``higher_better`` cannot lose it.
* **D-063** — ``variance_pct`` on a percentage-valued base is the difference in
  PERCENTAGE POINTS, and on an amount or count base the relative overshoot; the
  label says which, per measure.
* **D-064** — every variant reads ONE fact table, so the compiler's
  one-fact-table rule holds without being relaxed.
* **D-071** — the three types the old single ``ratio`` used to hide state their
  variance as a DIFFERENCE too, each with its own unit in the label. The label is
  the safety mechanism: a difference read as a relative change is a wrong number,
  and on a fractional rate the two differ by roughly twenty times.
* **D-072** — both register versions are exposed as distinct members, and no
  variant leaves its version to be inferred: its id, its label and the population
  filter that selects its rows all name the same one.

Plus the structural property the whole design rests on: a variant is DERIVED
from its base, so it cannot be born with a broader module, sensitivity,
entitlement, grain or dimension set than the measure it budgets, and it can
never badge itself certified.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from app.domain.bi.catalogue import Catalogue, MeasureDef, catalogue
from app.domain.bi.catalogue.members import NUMERIC_VALUE_TYPES
from app.domain.bi.catalogue.targets import (
    _COMPARABLE_VALUE_TYPES,  # pyright: ignore[reportPrivateUsage]
    _RELATIVE_VALUE_TYPES,  # pyright: ignore[reportPrivateUsage]
    ATTAINMENT_PCT_SUFFIX,
    BANK_WIDE_SCOPE,
    DIFFERENCE_UNIT_LABELS,
    NO_ATTAINMENT_DIRECTIONS,
    RELATIVE_UNIT_LABEL,
    TARGET_SUFFIX,
    TARGET_TABLE,
    TARGET_VERSION,
    TARGET_VERSIONS,
    VARIANCE_PCT_SUFFIX,
    VARIANCE_SUFFIX,
    VARIANT_SUFFIXES,
    VERSION_NOUNS,
    VERSION_PHRASES,
    base_id_of,
    is_target_variant,
    is_targetable,
    variant_id,
    variants_for,
    variants_for_version,
    version_of,
)
from app.domain.ingestion.reference_schemas import performance_targets
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
    return cat.measure(base_id_of(variant.id))


# --- what is targetable -------------------------------------------------------------------


def test_targetability_is_derived_from_what_a_measure_declares(cat: Catalogue) -> None:
    targetable = [m for m in _bases(cat) if is_targetable(m)]
    assert targetable, "nothing is targetable at all"
    for measure in targetable:
        assert measure.value_type in _COMPARABLE_VALUE_TYPES, measure.id
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
    already put to the evaluator, so 1,198 new members — both register versions
    of every targetable measure — cost the catalogue endpoint no additional
    authorization probe and can reach no reader the base could not.
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
    # Named cases, in both directions, for BOTH register versions.
    for version in TARGET_VERSIONS:
        assert variant_id("loans.npl_ratio_pct", ATTAINMENT_PCT_SUFFIX, version=version) not in cat
        magnitude = "engine.worst_eve_change_pct_tier1.crd.official"
        assert variant_id(magnitude, ATTAINMENT_PCT_SUFFIX, version=version) not in cat
        car = variant_id("engine.car_pct.crd.official", ATTAINMENT_PCT_SUFFIX, version=version)
        assert car in cat


def test_a_lower_better_measure_still_gets_target_variance_and_variance_pct(
    cat: Catalogue,
) -> None:
    base = cat.measure("loans.npl_ratio_pct")
    assert base.favourable_direction == "lower_better"
    emitted = {v.id for v in variants_for(base)}
    assert emitted == {
        variant_id(base.id, suffix, version=version)
        for version in TARGET_VERSIONS
        for suffix in VARIANT_SUFFIXES
        if suffix != ATTAINMENT_PCT_SUFFIX
    }


# --- D-063 and D-071 -----------------------------------------------------------------------


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
    """The safety mechanism, asserted per measure: label ⇔ arithmetic ⇔ unit.

    A difference quoted as if it were a relative change is a wrong number — on a
    fractional rate the two conventions differ by roughly twenty times — so for
    every ``variance_pct`` in the catalogue this checks all four statements
    together: which convention the label declares, which arithmetic the measure
    actually performs, which unit the figure is therefore in, and (D-071) that the
    difference form keeps its base's own value type so a surface renders it in
    that unit rather than scaling it by a hundred.
    """
    checked: set[str] = set()
    for variant in cat.target_measures():
        if not variant.id.endswith(f".{VARIANCE_PCT_SUFFIX}"):
            continue
        base = _base_of(cat, variant)
        checked.add(base.value_type)
        unit = DIFFERENCE_UNIT_LABELS.get(base.value_type)
        if unit is not None:
            # A DIFFERENCE: the pre-computed column, summed, in the base's unit.
            assert variant.label.endswith(f" ({unit})"), variant.id
            assert variant.aggregation == "sum", variant.id
            assert variant.source.column == "variance_value", variant.id
            assert variant.numerator is None and variant.denominator is None, variant.id
            assert variant.value_type == base.value_type, variant.id
            # ... and it must not be readable as the relative form.
            assert f"({RELATIVE_UNIT_LABEL})" not in variant.label, variant.id
        else:
            # A RELATIVE change: the share of the target, so a percentage.
            assert variant.label.endswith(f" ({RELATIVE_UNIT_LABEL})"), variant.id
            assert variant.aggregation == "ratio_of_sums", variant.id
            assert variant.value_type == "pct", variant.id
            assert base.value_type in _RELATIVE_VALUE_TYPES, variant.id
            # ... and it must not be readable as a difference in any unit.
            for other in DIFFERENCE_UNIT_LABELS.values():
                assert f"({other})" not in variant.label, (variant.id, other)
    # Every comparable value type in the catalogue was actually exercised above,
    # so this is a statement about the catalogue and not about four named ids.
    assert checked == {m.value_type for m in _bases(cat) if is_targetable(m)}


def test_the_two_variance_conventions_partition_every_comparable_value_type() -> None:
    """D-071 closed the gap D-063 left: no comparable type is in neither set.

    A ``ratio``-valued base used to fall between the two conventions and got no
    ``variance_pct`` at all. The two sets now partition the comparable types, so a
    type added to the catalogue cannot silently lose its variance — and cannot be
    claimed by both conventions at once, which would make the label a coin toss.
    """
    difference = set(DIFFERENCE_UNIT_LABELS)
    assert difference | _RELATIVE_VALUE_TYPES == _COMPARABLE_VALUE_TYPES
    assert not difference & _RELATIVE_VALUE_TYPES
    assert "ratio" not in _COMPARABLE_VALUE_TYPES
    # What a bank may budget is every value type that is a NUMBER. The two sets
    # are stated separately because "may a bank budget this?" is a decision and
    # not a consequence of being numeric — so a divergence has to be deliberate.
    assert _COMPARABLE_VALUE_TYPES == NUMERIC_VALUE_TYPES, (
        "a numeric value type is no longer targetable (or a non-numeric one is); "
        "D-072 says the payload is not to be trimmed by narrowing this"
    )


def test_every_targetable_base_now_emits_a_variance_pct(cat: Catalogue) -> None:
    for base in _bases(cat):
        if not is_targetable(base):
            continue
        for version in TARGET_VERSIONS:
            assert variant_id(base.id, VARIANCE_PCT_SUFFIX, version=version) in cat, base.id


def test_the_three_types_the_ratio_split_exposed_state_their_own_unit(cat: Catalogue) -> None:
    """D-071 named case per type, with the unit the reader is owed.

    A duration variance is years, an index variance is index points, and a
    fractional rate's variance is read in percentage points because the figure is
    scaled by a hundred for display — never the relative ``(actual − target) /
    target``, which on a rate of 0.185 against 0.170 would read 8.8 % where the
    bank is 1.5 percentage points off plan.
    """
    cases = (
        ("engine.duration_gap.crd.official", "duration_years", "years"),
        ("engine.pit_systematic_factor.advisory_internal.official", "index", "index points"),
        ("loans.weighted_average_rate", "fraction", "percentage points"),
    )
    for base_id, value_type, unit in cases:
        base = cat.measure(base_id)
        assert base.value_type == value_type, base_id
        assert is_targetable(base), base_id
        for version in TARGET_VERSIONS:
            variant = cat.measure(variant_id(base.id, VARIANCE_PCT_SUFFIX, version=version))
            assert variant.label.endswith(f" ({unit})"), variant.id
            assert variant.value_type == value_type, variant.id
            assert variant.aggregation == "sum", variant.id
            # ``.variance`` states the same difference; the two agree by design.
            plain = cat.measure(variant_id(base.id, VARIANCE_SUFFIX, version=version))
            assert plain.source.column == variant.source.column
            assert plain.value_type == variant.value_type


# --- D-072: both register versions, and never an inferred one -------------------------------


def test_both_register_versions_are_reachable_as_distinct_members(cat: Catalogue) -> None:
    """The bank's approved plan and its in-year revision are different figures.

    Both are stored by the mart builder already, so this is an exposure decision
    rather than a data one: exposing only one meant a bank that had revised its
    plan could not see the variance it actually manages against.
    """
    assert TARGET_VERSIONS == ("budget", "reforecast")
    by_version: dict[str, set[tuple[str, str]]] = {version: set() for version in TARGET_VERSIONS}
    for variant in cat.target_measures():
        base, suffix = base_id_of(variant.id), variant.id.rsplit(".", 1)[1]
        by_version[version_of(variant.id)].add((base, suffix))
    budget, reforecast = (by_version[version] for version in TARGET_VERSIONS)
    # The same variant set for each version: neither is a partial view.
    assert budget == reforecast
    assert budget, "no variants at all"
    # And they are DISTINCT members, not one member read twice.
    ids = [m.id for m in cat.target_measures()]
    assert len(ids) == len(set(ids)) == 2 * len(budget)


def test_no_variant_leaves_its_version_to_be_inferred(cat: Catalogue) -> None:
    """Id, label and population filter must name the SAME version, per member.

    This is the whole of D-072's safety: a member whose id said budget while its
    rows came from the reforecast would be a wrong number that no surface could
    detect, and a member that named no version at all would be the platform
    choosing a revision of the bank's plan on its behalf.
    """
    for variant in cat.target_measures():
        named = version_of(variant.id)
        assert named in TARGET_VERSIONS, variant.id
        assert variant.id == variant_id(
            base_id_of(variant.id), variant.id.rsplit(".", 1)[1], version=named
        )
        by_column = {f.column: f for f in variant.row_filters}
        assert by_column["target_version"].values == (named,), variant.id
        assert VERSION_PHRASES[named] in variant.label or VERSION_NOUNS[named] in variant.label, (
            variant.id
        )
        # No variant carries the OTHER version's words as well.
        other = next(v for v in TARGET_VERSIONS if v != named)
        assert VERSION_PHRASES[other] not in variant.label, variant.id
        assert VERSION_NOUNS[other] not in variant.label, variant.id


def test_the_two_versions_differ_in_nothing_but_the_version(cat: Catalogue) -> None:
    """One plan is not a broader or a looser member than the other.

    A reforecast variant reads a different ROW of the same fact — nothing else
    about it changes, so it cannot be born with a wider module, a different
    aggregation or a different unit than the budget variant beside it. The only
    fields that may differ are the three that carry the version: the id, the copy
    a reader sees, and the population predicate that picks its rows.
    """
    may_differ = {"id", "label", "description", "row_filters"}
    fields = {f.name for f in dataclasses.fields(MeasureDef)} - may_differ
    for variant in cat.target_measures():
        if version_of(variant.id) != TARGET_VERSIONS[0]:
            continue
        suffix = variant.id.rsplit(".", 1)[1]
        other_id = variant_id(base_id_of(variant.id), suffix, version=TARGET_VERSIONS[1])
        other = cat.measure(other_id)
        for name in sorted(fields):
            mine, theirs = getattr(variant, name), getattr(other, name)
            if name in ("numerator", "denominator") and mine is not None:
                # Composition points at the SAME version's parts, by construction.
                assert version_of(str(mine)) == TARGET_VERSIONS[0], variant.id
                assert version_of(str(theirs)) == TARGET_VERSIONS[1], other_id
                continue
            assert mine == theirs, (variant.id, name)
        mine_versions = {f.values for f in variant.row_filters if f.column == "target_version"}
        theirs_versions = {f.values for f in other.row_filters if f.column == "target_version"}
        assert mine_versions == {(TARGET_VERSIONS[0],)}
        assert theirs_versions == {(TARGET_VERSIONS[1],)}
        assert not mine_versions & theirs_versions


def test_no_member_keeps_the_pre_version_two_segment_shape(cat: Catalogue) -> None:
    """``loans.balance_rc.target`` is gone; the id names its version or nothing."""
    for measure in cat.measures():
        tail = measure.id.rsplit(".", 1)[-1]
        if tail not in VARIANT_SUFFIXES:
            continue
        assert is_target_variant(measure.id), measure.id
        assert version_of(measure.id) in TARGET_VERSIONS, measure.id


def test_the_catalogue_never_takes_the_default_version(cat: Catalogue) -> None:
    """``variant_id``'s default is a caller convenience, not a catalogue behaviour."""
    for base in _bases(cat):
        if not is_targetable(base):
            continue
        for version in TARGET_VERSIONS:
            emitted = variants_for_version(base, version)
            assert emitted, (base.id, version)
            for variant in emitted:
                assert version_of(variant.id) == version, variant.id
    assert TARGET_VERSION in TARGET_VERSIONS


def test_the_version_vocabulary_is_the_registers_own() -> None:
    """One vocabulary, asserted from the side that may see both.

    ``app/domain/ingestion`` is upstream of ``app/domain/bi`` and must not import
    it, so the two tuples are mirrors. A version the register accepts but the
    catalogue does not expose is a target the bank states and never sees; one the
    catalogue exposes but the register refuses is a member that can never hold a
    figure. ``app/models/bi.py::TARGET_VERSIONS`` and the mart's
    ``ck_bi_fact_target_version`` are the third side — edit all three together.
    """
    assert TARGET_VERSIONS == performance_targets.VERSIONS
    assert set(performance_targets.SCHEMA.enums["version"]) == set(TARGET_VERSIONS)
    assert set(VERSION_PHRASES) == set(VERSION_NOUNS) == set(TARGET_VERSIONS)


def test_the_member_count_is_what_the_two_decisions_imply(cat: Catalogue) -> None:
    """The arithmetic of the payload, stated so a change to it is deliberate.

    129 targetable bases × 2 register versions (D-072) × three level variants,
    plus one ``variance_pct`` each now that D-071 leaves no base without one, plus
    ``attainment_pct`` wherever higher is better (D-062).
    """
    targetable = [m for m in _bases(cat) if is_targetable(m)]
    assert len(targetable) == 129
    attainable = [m for m in targetable if m.favourable_direction not in NO_ATTAINMENT_DIRECTIONS]
    assert len(attainable) == 83
    expected = len(TARGET_VERSIONS) * (3 * len(targetable) + len(targetable) + len(attainable))
    assert expected == 1198
    assert len(cat.target_measures()) == expected
    assert len(cat.measures()) == len(_bases(cat)) + expected == 1416


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
        assert base.value_type in _COMPARABLE_VALUE_TYPES


def test_every_variant_is_pinned_to_the_bank_wide_scope_and_one_register_version(
    cat: Catalogue,
) -> None:
    """The two scopes are NEVER summed, made impossible rather than guarded.

    A variant's population is the bank-wide row of one measure under ONE register
    version — the version its own id names (D-072) — so no query, grouped or not,
    can add a bank-wide target to the scoped ones stored beside it, and none can
    add the budget to the reforecast.
    """
    for variant in cat.target_measures():
        base = _base_of(cat, variant)
        by_column = {f.column: f for f in variant.row_filters}
        assert set(by_column) == {"measure_id", "scope_dimension", "target_version"}, variant.id
        assert by_column["measure_id"].values == (base.id,)
        assert by_column["scope_dimension"].values == (BANK_WIDE_SCOPE,)
        assert by_column["target_version"].values == (version_of(variant.id),), variant.id
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
        # A wire key never reaches the label: "Variance against the approved
        # budget (%)", never "variance_pct".
        for suffix in VARIANT_SUFFIXES:
            if "_" in suffix:
                assert suffix not in variant.label, (variant.id, suffix)
        assert variant.id not in variant.label, variant.id
        # The version is named in the description as well as the label, because a
        # description is what a picker shows when a figure is being chosen — and
        # in words ("the approved budget"), never as the register's bare code.
        named = version_of(variant.id)
        assert VERSION_PHRASES[named] in variant.description, variant.id
        other = next(v for v in TARGET_VERSIONS if v != named)
        assert VERSION_PHRASES[other] not in variant.description, variant.id
