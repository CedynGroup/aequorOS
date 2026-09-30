"""The BI catalogue's own invariants (no service, no model, no session).

What the registry-facing and model-facing checks look like lives in
``tests/architecture/test_bi_catalogue_authority.py``; this file pins the
catalogue's shape: the vocabularies it enumerates come from the domain
modules that own them, ids are stable and unique, labels are production copy,
ratios compose from existing measures, and the "certified" badge is derivable
from nothing but the member itself.
"""

from __future__ import annotations

import re

import pytest

from app.domain.authority.registry import REGISTRY
from app.domain.bi.authority import (
    ENGINE_MODULES,
    READ_COMPUTED_LIVE_MODULES,
    TEXT_VALUED_METRIC_IDS,
    engine_authorities,
    engine_module_for,
)
from app.domain.bi.catalogue import (
    CATALOGUE_VERSION,
    Catalogue,
    MeasureDef,
    UnknownMember,
    build_catalogue,
    catalogue,
)
from app.domain.bi.catalogue.dimensions import POSITION_TYPE_LABELS
from app.domain.bi.catalogue.engine import (
    ENGINE_LABELS,
    UndeclaredValueType,
    engine_measure_id,
    engine_value_type,
)
from app.domain.bi.catalogue.measures import dpd_bands_from
from app.domain.bi.catalogue.members import (
    NUMERIC_VALUE_TYPES,
    VALUE_TYPES,
)
from app.domain.bi.extract import MATURITY_BUCKETS, PRODUCT_FAMILY_LABELS
from app.domain.credit.dpd_bands import DPD_BANDS
from app.domain.ingestion.constants import POSITION_TYPES
from app.domain.irr.buckets import REPRICING_BUCKETS

RAW_TOKEN = re.compile(r"^[A-Z0-9_]+$|_[a-z]")


@pytest.fixture(scope="module")
def cat() -> Catalogue:
    return catalogue()


# --- structure --------------------------------------------------------------------------


def test_the_version_is_a_dotted_release_string() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", CATALOGUE_VERSION)
    assert catalogue().version == CATALOGUE_VERSION


def test_build_is_deterministic_and_cached(cat: Catalogue) -> None:
    fresh = build_catalogue()
    assert [m.id for m in fresh.members()] == [m.id for m in cat.members()]
    assert catalogue() is cat


def test_ids_are_unique_stable_dotted_snake_case(cat: Catalogue) -> None:
    ids = [member.id for member in cat.members()]
    assert len(ids) == len(set(ids))
    for member_id in ids:
        assert re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z0-9][a-z0-9_]*)+", member_id), member_id
    hierarchy_ids = [h.id for h in cat.hierarchies()]
    assert len(hierarchy_ids) == len(set(hierarchy_ids))


def test_lookups_and_unknown_member(cat: Catalogue) -> None:
    measure = cat.measure("loans.npl_ratio_pct")
    assert measure.kind == "measure"
    dimension = cat.dimension("branch.region")
    assert dimension.kind == "dimension"
    assert cat.member("loans.npl_ratio_pct") is measure
    assert "loans.npl_ratio_pct" in cat
    assert "loans.nope" not in cat
    with pytest.raises(UnknownMember):
        cat.measure("branch.region")
    with pytest.raises(UnknownMember):
        cat.dimension("loans.npl_ratio_pct")
    with pytest.raises(UnknownMember):
        cat.member("nothing.here")


def test_for_module_partitions_the_members(cat: Catalogue) -> None:
    modules = {member.module for member in cat.members()}
    assert modules == {"cap", "liq", "credit", "irrbb", "fx", "ftp", "fcst", "markets", "risk"}
    total = sum(len(cat.for_module(module)) for module in modules)
    assert total == len(cat.members())
    assert all(m.module == "credit" for m in cat.for_module("credit"))
    assert cat.for_module("audit") == ()


def test_member_counts_are_what_the_sources_imply(cat: Catalogue) -> None:
    numeric_authorities = [
        e for e in engine_authorities() if e.metric_id not in TEXT_VALUED_METRIC_IDS
    ]
    assert len(cat.engine_measures()) == 2 * len(numeric_authorities)
    # 48 since Phase 5: the four arrears / branch-ledger measures (two over
    # ``arrears_amount_rc``, two over the new ``bi_fact_gl_branch_monthly``).
    assert len(cat.portfolio_measures()) == 48
    # The Phase 2 target variants (``app/domain/bi/catalogue/targets.py``): five
    # per targetable base per REGISTER VERSION (D-072 exposes the budget and the
    # reforecast as distinct members), less ``attainment_pct`` where lower is
    # better (D-062). Since D-071 every targetable base carries a
    # ``variance_pct``, so none of the five is missing for a unit reason.
    # The arithmetic is spelled out in
    # ``tests/domain/bi/test_targets.py::test_the_member_count_is_what_the_two_decisions_imply``.
    assert len(cat.target_measures()) == 1234
    assert len(cat.measures()) == 1456
    # 69 since Phase 5: ``position.officer_code`` / ``position.channel`` /
    # ``position.account_status`` over the four new mart columns.
    assert len(cat.dimensions()) == 69
    assert len(cat.hierarchies()) == 13


# --- labels are production copy -------------------------------------------------------


def test_every_label_is_production_copy(cat: Catalogue) -> None:
    for member in cat.members():
        assert member.label.strip(), member.id
        assert not RAW_TOKEN.search(member.label), (member.id, member.label)
        assert member.label != member.id
        assert member.label != member.column
    for hierarchy in cat.hierarchies():
        assert hierarchy.label.strip() and not RAW_TOKEN.search(hierarchy.label)


def test_every_enumerated_value_carries_a_label(cat: Catalogue) -> None:
    for dimension in cat.dimensions():
        for value in dimension.values:
            assert value.code and value.label.strip(), (dimension.id, value)
            assert not RAW_TOKEN.search(value.label), (dimension.id, value)


def test_engine_labels_cover_every_engine_metric_and_nothing_else() -> None:
    metric_ids = {e.metric_id for e in engine_authorities()} - TEXT_VALUED_METRIC_IDS
    assert set(ENGINE_LABELS) == metric_ids


# --- value types: what each figure IS ---------------------------------------------------


def test_every_value_type_in_use_is_one_the_catalogue_declares(cat: Catalogue) -> None:
    """No member carries a type outside the declared set, and ``ratio`` is gone.

    A single ``ratio`` type used to serve a fractional interest rate, a
    concentration index and a duration in years at once, so a surface could not
    render all three correctly: scaling a rate by a hundred is right and scaling
    an index or a duration is wrong. The type is split, and the token is retired
    so nothing can declare it again — the check is over the runtime values too,
    because ``value_type`` crosses the wire as a plain string.
    """
    assert "ratio" not in VALUE_TYPES
    for member in (*cat.measures(), *cat.dimensions()):
        assert member.value_type in VALUE_TYPES, (member.id, member.value_type)


def test_every_measure_is_a_number_and_every_dimension_type_is_used(cat: Catalogue) -> None:
    """A measure is a figure; ``text`` / ``date`` / ``flag`` describe dimensions."""
    for measure in cat.measures():
        assert measure.value_type in NUMERIC_VALUE_TYPES, (measure.id, measure.value_type)
    # Nothing in the declared set is dead: every type is carried by a member, so
    # a renderer held to the whole set is not being asked to handle a fiction.
    in_use = {member.value_type for member in (*cat.measures(), *cat.dimensions())}
    assert in_use == set(VALUE_TYPES), sorted(set(VALUE_TYPES) - in_use)


def test_each_former_ratio_member_says_what_its_number_is(cat: Catalogue) -> None:
    """The split, member by member — this is the list a renderer is written against.

    ``fraction`` is a proportion of one and a surface scales it by a hundred;
    ``index`` is a dimensionless number on its own scale and must never be scaled;
    ``duration_years`` is a length of time. The last two entries were not ratios
    of anything at all: both are ``x / tier1 × 100`` in their own engines, so they
    are percentages that the old "no unit suffix means ratio" guess typed as bare
    ratios and rendered a hundred times too small.
    """
    expected: dict[str, str] = {
        "loans.weighted_average_rate": "fraction",
        "deposits.weighted_average_rate": "fraction",
        "positions.weighted_average_rate": "fraction",
        "loans.sector_hhi": "index",
    }
    for metric_id, value_type in (
        ("npl_ratio", "fraction"),
        ("asset_duration", "duration_years"),
        ("liability_duration", "duration_years"),
        ("duration_gap", "duration_years"),
        ("pit_systematic_factor", "index"),
        ("nop_pct_tier1", "pct"),
        ("worst_eve_change_pct_tier1", "pct"),
    ):
        for measure in cat.engine_measures():
            rule = measure.engine_rule
            if rule is not None and rule.metric_id == metric_id:
                expected[measure.id] = value_type
    for member_id, value_type in expected.items():
        assert cat.measure(member_id).value_type == value_type, member_id
    # And the two percentages agree with their ``_pct`` twins where one exists.
    assert cat.measure("engine.npl_ratio.crd.official").value_type == "fraction"
    assert cat.measure("engine.npl_ratio_pct.crd.official").value_type == "pct"


def test_an_engine_metric_with_no_declared_unit_is_refused_not_guessed() -> None:
    """The guess that mis-typed two percentages is gone; a new metric must declare.

    The refusal is deliberately loud: it is raised while the catalogue is being
    built, so it surfaces in every BI test rather than as a figure that is a
    hundred times too small on one screen.
    """
    assert engine_value_type("car_pct") == "pct"
    assert engine_value_type("total_capital_ghs") == "amount"
    assert engine_value_type("duration_gap") == "duration_years"
    with pytest.raises(UndeclaredValueType, match="does not say what its figure is"):
        engine_value_type("some_new_metric")


# --- vocabularies come from their owners -----------------------------------------------


def test_enumerated_dimensions_read_the_domain_vocabularies(cat: Catalogue) -> None:
    assert [v.code for v in cat.dimension("loan.dpd_band").values] == [b.code for b in DPD_BANDS]
    assert [v.label for v in cat.dimension("loan.dpd_band").values] == [b.label for b in DPD_BANDS]
    assert [v.code for v in cat.dimension("position.repricing_bucket").values] == [
        name for name, _, _ in REPRICING_BUCKETS
    ]
    assert [v.code for v in cat.dimension("position.maturity_bucket").values] == [
        b.code for b in MATURITY_BUCKETS
    ]
    assert [v.code for v in cat.dimension("position.type").values] == list(POSITION_TYPES)
    assert set(POSITION_TYPE_LABELS) == set(POSITION_TYPES)
    assert [v.code for v in cat.dimension("product.family").values] == list(PRODUCT_FAMILY_LABELS)
    assert [v.code for v in cat.dimension("loan.grade").values] == [
        "standard",
        "olem",
        "substandard",
        "doubtful",
        "loss",
    ]
    assert [v.code for v in cat.dimension("loan.ifrs9_stage").values] == ["1", "2", "3"]


def test_par_bands_are_the_band_sets_at_or_beyond_each_boundary() -> None:
    assert dpd_bands_from(30) == ("30_59", "60_89", "90_179", "180_359", "360_plus")
    assert dpd_bands_from(60) == ("60_89", "90_179", "180_359", "360_plus")
    assert dpd_bands_from(90) == ("90_179", "180_359", "360_plus")


def test_engine_module_selection_rule() -> None:
    """Sealed by a live module's run, or published on read by a named module."""
    modules = {engine_module_for(e) for e in engine_authorities()}
    assert modules <= set(ENGINE_MODULES)
    assert modules == set(ENGINE_MODULES)  # every live module contributes at least one metric
    for entry in REGISTRY:
        module = engine_module_for(entry)
        if entry.authoritative_run_type in ENGINE_MODULES:
            assert module == entry.authoritative_run_type
        elif entry.methodology_id in READ_COMPUTED_LIVE_MODULES:
            assert module == READ_COMPUTED_LIVE_MODULES[entry.methodology_id]
        else:
            assert module is None, entry.key
    # The standardised framework and the stress orchestrators are not live modules.
    assert engine_module_for(REGISTRY.for_metric("sf_eve_risk_measure")[0]) is None
    assert engine_module_for(REGISTRY.for_metric("stressed_car_end_pct")[0]) is None
    # Only primaries: the forecast projection PATH alternates of car_pct are excluded.
    ids = {
        (m.engine_rule.metric_id, m.engine_rule.module)
        for m in catalogue().engine_measures()
        if m.engine_rule
    }
    assert ("car_pct", "forecast") not in ids
    assert ("car_pct", "capital") in ids


# --- engine measures ------------------------------------------------------------------


def test_engine_measures_come_in_official_and_live_pairs(cat: Catalogue) -> None:
    by_key: dict[tuple[str, str], set[str]] = {}
    for measure in cat.engine_measures():
        assert measure.engine_rule is not None
        assert measure.id == engine_measure_id(
            measure.engine_rule.metric_id, measure.id.split(".")[2], measure.engine_rule.tier
        )
        key = (measure.engine_rule.metric_id, measure.id.split(".")[2])
        by_key.setdefault(key, set()).add(measure.engine_rule.tier)
        assert measure.aggregation == "last_value"
        assert measure.grain == "institution"
        assert measure.time_behaviour == "stock"
        assert measure.sensitivity == "aggregated"
        assert measure.fx_rule is None
        assert measure.source.table == "bi_fact_engine_metric"
        assert measure.source.column == "value"
        assert measure.label.endswith((" · Official", " · Live"))
    assert all(tiers == {"official", "live"} for tiers in by_key.values())


def test_certified_is_official_tier_and_filed_only(cat: Catalogue) -> None:
    for measure in cat.engine_measures():
        assert measure.engine_rule is not None
        expected = (
            measure.engine_rule.tier == "official" and measure.advisory_designation == "filed"
        )
        assert measure.certified is expected, measure.id
    for measure in cat.portfolio_measures():
        assert measure.certified is False
    assert cat.measure("engine.car_pct.crd.official").certified is True
    assert cat.measure("engine.car_pct.crd.live").certified is False
    assert cat.measure("engine.pit_pd_upper_pct.advisory_internal.official").certified is False
    assert cat.measure("engine.par_30_pct.crd.official").certified is False


def test_engine_thresholds_are_register_codes(cat: Catalogue) -> None:
    assert cat.measure("engine.car_pct.crd.official").thresholds_source == "car_min"
    assert cat.measure("engine.lcr_pct.crd.live").thresholds_source == "lcr_min"
    assert cat.measure("engine.npl_ratio_pct.s29.official").thresholds_source == "npl_limit_pct"
    assert (
        cat.measure("engine.worst_eve_change_pct_tier1.crd.official").thresholds_source
        == "eve_tier1_limit_pct"
    )
    assert cat.measure("engine.nop_pct_tier1.crd.official").thresholds_source == (
        "fx_nop_aggregate_limit_pct"
    )
    assert cat.measure("engine.total_rwa_ghs.crd.official").thresholds_source is None


def test_engine_measures_carry_the_registry_designation(cat: Catalogue) -> None:
    assert cat.measure("engine.npl_ratio_pct.crd.official").advisory_designation == "filed"
    assert (
        cat.measure("engine.par_90_pct.crd.official").advisory_designation
        == "supervisory_monitoring"
    )
    assert cat.measure("engine.portfolio_nim_pct.advisory_internal.live").advisory_designation == (
        "advisory_only"
    )
    assert cat.measure("engine.car_pct.s29.official").advisory_designation == (
        "supervisory_monitoring"
    )
    assert cat.measure("engine.ecl_total_ghs.ifrs9.official").module == "cap"
    assert cat.measure("engine.net_own_funds_ghs.s29.live").module == "cap"
    assert cat.measure("engine.pit_pd_upper_pct.advisory_internal.live").module == "markets"
    assert cat.measure("engine.year5_car_pct.advisory_internal.official").module == "fcst"


def test_text_valued_registry_metrics_are_not_measures(cat: Catalogue) -> None:
    for metric_id in TEXT_VALUED_METRIC_IDS:
        assert REGISTRY.for_metric(metric_id)  # registered …
        assert not [
            m
            for m in cat.engine_measures()
            if m.engine_rule and m.engine_rule.metric_id == metric_id
        ]


# --- portfolio measures ----------------------------------------------------------------


def test_fx_rule_follows_the_column(cat: Catalogue) -> None:
    for measure in cat.portfolio_measures():
        if measure.aggregation in ("count",):
            assert measure.fx_rule is None
            continue
        if measure.source.column == "classification_exposure_rc":
            assert measure.fx_rule == "classification", measure.id
        if measure.source.column in ("balance_rc", "amount_rc", "notional_rc"):
            assert measure.fx_rule == "derivation", measure.id


def test_ratios_compose_from_existing_measures(cat: Catalogue) -> None:
    npl = cat.measure("loans.npl_ratio_pct")
    assert npl.aggregation == "ratio_of_sums"
    assert npl.numerator == "loans.npl_exposure_rc"
    assert npl.denominator == "loans.classification_exposure_rc"
    coverage = cat.measure("loans.provision_coverage_pct")
    assert coverage.numerator == "loans.specific_provision_held_rc"
    assert coverage.denominator == "loans.npl_exposure_rc"
    share = cat.measure("deposits.demand_share_pct")
    assert share.aggregation == "share"
    rate = cat.measure("loans.weighted_average_rate")
    assert rate.aggregation == "weighted_avg"
    assert rate.weight == "loans.balance_rc"
    for measure in cat.measures():
        for ref in (measure.numerator, measure.denominator, measure.weight):
            if ref is not None:
                assert cat.measure(ref).aggregation in ("sum", "count")
        if measure.aggregation in ("ratio_of_sums", "share"):
            assert measure.numerator and measure.denominator, measure.id
        if measure.aggregation == "weighted_avg":
            assert measure.weight, measure.id
        if measure.aggregation in ("top_n_share", "hhi"):
            assert measure.over and cat.dimension(measure.over), measure.id


def test_par_measures_filter_on_band_sets(cat: Catalogue) -> None:
    par30 = cat.measure("loans.par_30_exposure_rc")
    filters = {f.column: f for f in par30.row_filters}
    assert filters["position_type"].values == ("LOAN",)
    assert filters["dpd_band"].values == dpd_bands_from(30)
    assert cat.measure("loans.par_30_pct").numerator == "loans.par_30_exposure_rc"


def _selects_on_dpd_band(cat: Catalogue, measure: MeasureDef) -> bool:
    """Whether the measure's own filters select on ``dpd_band``, or a referenced one's do.

    Derivation is followed transitively, so a ratio of a ratio is caught too.
    """
    if any(f.column == "dpd_band" for f in measure.row_filters):
        return True
    return any(
        _selects_on_dpd_band(cat, cat.measure(reference))
        for reference in (measure.numerator, measure.denominator, measure.weight)
        if reference is not None
    )


#: The mart-side PAR measures, which select on the derived ``dpd_band``.
DPD_MART_MEASURES = {
    "loans.par_30_exposure_rc",
    "loans.par_60_exposure_rc",
    "loans.par_90_exposure_rc",
    "loans.par_30_pct",
    "loans.par_60_pct",
    "loans.par_90_pct",
}
#: The engine metric ids whose VALUE depends on days-past-due having been supplied
#: (``regulatory_credit._portfolio_at_risk`` divides raw DPD exposures, D-046).
DPD_ENGINE_METRIC_IDS = {"par_30_pct", "par_60_pct", "par_90_pct"}
#: Every id those metrics expand to: each registered regime × both tiers.
DPD_ENGINE_MEASURES = {
    f"engine.{metric_id}.{regime}.{tier}"
    for metric_id in DPD_ENGINE_METRIC_IDS
    for regime in ("crd", "s29")
    for tier in ("official", "live")
}


def test_the_engine_par_expansion_is_the_whole_registered_set(cat: Catalogue) -> None:
    """Every registered PAR copy is enumerated above — a new one cannot hide.

    The expansion is derived from the registry here, so adding a ``par_180_pct``
    authority (or a third regime) fails this test until D-046's set admits it,
    rather than shipping an unbadged copy.
    """
    registered = {
        measure.id
        for measure in cat.engine_measures()
        if measure.engine_rule is not None and measure.engine_rule.metric_id.startswith("par_")
    }
    assert registered == DPD_ENGINE_MEASURES
    assert {
        measure.engine_rule.metric_id
        for measure in cat.engine_measures()
        if measure.engine_rule is not None and measure.engine_rule.metric_id.startswith("par_")
    } == DPD_ENGINE_METRIC_IDS


def test_every_dpd_dependent_measure_is_enumerated_and_copied_verbatim(cat: Catalogue) -> None:
    """D-042 + D-046: the DPD-dependent figures are a known, closed set.

    A measure's value depends on days-past-due either because it selects on the
    mart's derived ``dpd_band`` (the compiler returns NULL for a wholly-NULL
    population — a book that never supplied the attribute reads as absent, never
    as 0 % at risk) or because it is a verbatim COPY of an engine portfolio-at-risk
    metric (the engine divides raw DPD exposures and yields a genuine ``0``, which
    BI copies as the engine's own figure and never recomputes).
    """
    selecting = {m.id for m in cat.measures() if _selects_on_dpd_band(cat, m)}
    assert selecting == DPD_MART_MEASURES
    copied = {
        measure.id
        for measure in cat.engine_measures()
        if measure.engine_rule is not None
        and measure.engine_rule.metric_id in DPD_ENGINE_METRIC_IDS
    }
    assert copied == DPD_ENGINE_MEASURES
    # The copied value is still the engine's: never recomputed.
    for member_id in DPD_ENGINE_MEASURES:
        measure = cat.measure(member_id)
        assert measure.measure_kind == "certified_engine"
        assert measure.aggregation == "last_value"
        assert measure.source.column == "value"


#: The engine fact's KEY and provenance columns, withdrawn from the catalogue by
#: A6-05: every engine measure's identity already pins them, so a group-by could
#: only return the ungrouped figure while looking like a tier/regime comparison.
WITHDRAWN_ENGINE_COLUMNS = ("module", "metric_id", "tier", "regime", "advisory_designation")


def test_every_advertised_dimension_is_admitted_by_at_least_one_measure(cat: Catalogue) -> None:
    """A dimension the catalogue publishes must compile somewhere (A6-05).

    The catalogue is what the Explore UI builds its group-by control from, so a
    member advertised but admitted by no measure is a control that always 422s.
    Both directions: nothing advertised is unreachable, and nothing admitted is
    unpublished (the latter would be a member the UI cannot label).
    """
    advertised = {dimension.id for dimension in cat.dimensions()}
    admitted = {
        dimension_id for measure in cat.measures() for dimension_id in measure.allowed_dimensions
    }
    assert advertised - admitted == set(), sorted(advertised - admitted)
    assert admitted - advertised == set(), sorted(admitted - advertised)


def test_the_engine_facts_key_and_provenance_columns_are_not_dimensions(cat: Catalogue) -> None:
    """A6-05: withdrawn on purpose, so re-adding one is a conscious act.

    Each is constant across the rows any engine measure selects — the measure id
    pins metric, regime and tier — so grouping by it cannot compare anything.
    """
    bound = {(member.table, member.column) for member in cat.members()}
    for column in WITHDRAWN_ENGINE_COLUMNS:
        assert ("bi_fact_engine_metric", column) not in bound, column
    for member_id in (
        "engine.module",
        "engine.regime",
        "engine.tier",
        "engine.advisory_designation",
    ):
        assert member_id not in cat, member_id
    # What DOES vary per as-of date within one measure stays groupable.
    for member_id in ("engine.status", "engine.pipeline_state", "engine.reconciliation_blocked"):
        dimension = cat.dimension(member_id)
        assert dimension.table == "bi_fact_engine_metric"
        assert any(member_id in measure.allowed_dimensions for measure in cat.engine_measures()), (
            member_id
        )
    # The information itself is not lost: it is on the member.
    measure = cat.measure("engine.car_pct.crd.official")
    assert measure.engine_rule is not None
    assert (measure.engine_rule.module, measure.engine_rule.tier) == ("capital", "official")
    assert measure.advisory_designation == "filed"
    assert measure.label.endswith("· Official")


def test_single_obligor_exposure_is_restricted_and_aggregates_are_aggregated(
    cat: Catalogue,
) -> None:
    assert cat.measure("loans.largest_single_name_share_pct").sensitivity == "restricted"
    assert cat.measure("loans.largest_single_name_share_pct").over == "counterparty.id"
    assert cat.measure("loans.sector_hhi").sensitivity == "aggregated"
    restricted = {m.id for m in cat.members() if m.sensitivity == "restricted"}
    # ``position.officer_code`` joined the set in Phase 5 and belongs there: it is
    # the bank's code for one member of STAFF, and filtering on it discloses that
    # individual's whole book. The property, pinned below rather than implied by a
    # literal: every restricted member either names a natural person or can
    # disclose one obligor.
    assert restricted == {
        "loans.largest_single_name_share_pct",
        "counterparty.id",
        "counterparty.name",
        "counterparty.source_reference",
        "counterparty.group",
        "position.officer_code",
    }
    assert cat.dimension("position.officer_code").sensitivity == "restricted"
    # Its neighbours over the same three new mart columns are NOT restricted: a
    # channel and an account status are attributes of an account, not of a person.
    for member_id in ("position.channel", "position.account_status"):
        assert cat.dimension(member_id).sensitivity == "aggregated", member_id
    confidential = {m.id for m in cat.members() if m.sensitivity == "confidential"}
    assert confidential == {
        "position.id",
        "position.source_reference",
        "event.position_id",
        "loan.employer",
    }


def test_flows_are_flow_measures_on_the_event_table(cat: Catalogue) -> None:
    for measure in cat.portfolio_measures():
        if measure.source.table == "bi_fact_loan_event":
            assert measure.time_behaviour == "flow", measure.id
            assert measure.aggregation in ("flow_sum", "count"), measure.id
            assert set(measure.allowed_dimensions) >= {"event.type", "branch.code", "time.date"}
            assert "loan.grade" not in measure.allowed_dimensions
        else:
            # A flow is no longer only an event: ``gl.branch_movement_rc`` is a
            # month's MOVEMENT in a branch ledger balance, so months add. The
            # original property was "a position-fact measure is a stock", which is
            # what is asserted here now; the two-way equivalence between
            # ``flow_sum`` and ``flow`` is pinned separately below, so a measure
            # cannot be declared one and aggregated as the other.
            expected = "flow" if measure.aggregation == "flow_sum" else "stock"
            assert measure.time_behaviour == expected, measure.id
    assert cat.measure("events.write_off_rc").row_filters[0].values == ("WRITE_OFF",)


def test_flow_and_flow_sum_imply_each_other(cat: Catalogue) -> None:
    """Neither half of the stock/flow declaration may drift from the other.

    ``docs/bi.md`` names "targets misread (flow vs stock)" as a risk, and the two
    ways to get it wrong are opposite: summing a YTD level across months, or
    reporting a month's movement as a level. A measure aggregated as ``flow_sum``
    must declare ``flow`` and vice versa.
    """
    for measure in cat.portfolio_measures():
        assert (measure.aggregation == "flow_sum") == (
            measure.time_behaviour == "flow" and measure.aggregation != "count"
        ), measure.id
    flows = {m.id for m in cat.portfolio_measures() if m.time_behaviour == "flow"}
    assert "gl.branch_movement_rc" in flows
    assert "gl.branch_ytd_rc" not in flows


def test_portfolio_modules_and_entitlements(cat: Catalogue) -> None:
    for measure in cat.portfolio_measures():
        assert measure.measure_kind == "portfolio"
        assert measure.grain == "portfolio"
        assert measure.engine_rule is None
        assert measure.advisory_designation is None
        assert measure.thresholds_source is None
        prefix = measure.id.split(".")[0]
        if prefix in ("loans", "events"):
            assert (measure.module, measure.entitlement) == ("credit", "credit")
        elif prefix == "deposits":
            assert (measure.module, measure.entitlement) == ("liq", "liquidity")
        else:
            assert measure.module in ("risk", "liq")
            assert measure.entitlement in ("risk", "liquidity")
        assert measure.entitlement != "positions"  # bank-only slug; an SDI keeps its book


# --- hierarchies ------------------------------------------------------------------------


def test_hierarchies_drill_through_existing_dimensions(cat: Catalogue) -> None:
    by_id = {h.id: h for h in cat.hierarchies()}
    assert by_id["geography"].levels == ("branch.region", "branch.code")
    assert by_id["product"].levels == ("position.type", "product.family", "product.code")
    assert by_id["counterparty"].levels == (
        "counterparty.type",
        "counterparty.group",
        "counterparty.id",
    )
    assert by_id["calendar_time"].levels[0] == "time.calendar_year"
    assert by_id["fiscal_time"].levels[0] == "time.fiscal_year"
    for hierarchy in cat.hierarchies():
        for level in hierarchy.levels:
            cat.dimension(level)
    # Drilling into a named counterparty crosses into restricted data.
    assert cat.dimension(by_id["counterparty"].levels[0]).sensitivity == "aggregated"
    assert cat.dimension(by_id["counterparty"].levels[-1]).sensitivity == "restricted"


def test_no_measure_carries_a_number(cat: Catalogue) -> None:
    """Thresholds are register codes; predicates are code sets."""
    for measure in cat.measures():
        _assert_no_numeric_literal(measure)


def _assert_no_numeric_literal(measure: MeasureDef) -> None:
    if measure.thresholds_source is not None:
        # A register CODE (an identifier), never a value.
        assert re.fullmatch(r"[a-z][a-z0-9_]*", measure.thresholds_source), measure.id
        assert not re.fullmatch(r"[0-9.]+", measure.thresholds_source), measure.id
    for row_filter in measure.row_filters:
        for value in row_filter.values:
            assert not re.fullmatch(r"[0-9.]+", value), (measure.id, value)
    for name in ("thresholds_source", "numerator", "denominator", "weight", "over"):
        assert not isinstance(getattr(measure, name), int | float), (measure.id, name)
