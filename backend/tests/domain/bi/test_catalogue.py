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
from app.domain.bi.catalogue.engine import ENGINE_LABELS, engine_measure_id
from app.domain.bi.catalogue.measures import dpd_bands_from
from app.domain.bi.catalogue.members import DPD_COMPLETENESS
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
    assert len(cat.portfolio_measures()) == 44
    assert len(cat.dimensions()) == 66
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


def test_classification_figures_reconcile_to_r1_and_balances_to_r2_r3(cat: Catalogue) -> None:
    assert "R1" in cat.measure("loans.npl_ratio_pct").reconciliation_checks
    assert "R1" in cat.measure("loans.npl_exposure_rc").reconciliation_checks
    assert "R2" in cat.measure("loans.balance_rc").reconciliation_checks
    assert "R3" in cat.measure("deposits.balance_rc").reconciliation_checks
    assert "R6" in cat.measure("positions.unconverted_count").reconciliation_checks
    assert "R5" in cat.measure("positions.count").reconciliation_checks
    assert "R1" in cat.measure("engine.npl_ratio_pct.crd.official").reconciliation_checks
    assert "R8" in cat.measure("engine.car_pct.crd.live").reconciliation_checks
    assert "R8" not in cat.measure("engine.car_pct.crd.official").reconciliation_checks


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


def test_every_dpd_dependent_measure_carries_the_completeness_check(cat: Catalogue) -> None:
    """D-042 + D-046: no DPD-dependent figure may be badged green over absent data.

    A measure's value depends on days-past-due either because it selects on the
    mart's derived ``dpd_band`` (the compiler returns NULL for a wholly-NULL
    population) or because it is a verbatim COPY of an engine portfolio-at-risk
    metric (the engine divides raw DPD exposures and yields a genuine ``0``).
    Both are badged by BI, so both carry R10; nothing else does.
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
    expected = DPD_MART_MEASURES | DPD_ENGINE_MEASURES
    carrying = {m.id for m in cat.measures() if DPD_COMPLETENESS in m.reconciliation_checks}
    assert carrying == expected, (
        f"missing {sorted(expected - carrying)}, unexpected {sorted(carrying - expected)}"
    )
    # The copied value is still the engine's: R10 badges, it never recomputes.
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


def test_the_dpd_band_dimension_carries_no_checks(cat: Catalogue) -> None:
    """Grouping BY the band is not the defect; selecting on it is (dimensions hold no checks)."""
    dimension = cat.dimension("loan.dpd_band")
    assert not hasattr(dimension, "reconciliation_checks")


def test_every_reconciliation_check_id_is_well_formed(cat: Catalogue) -> None:
    ids = {check for measure in cat.measures() for check in measure.reconciliation_checks}
    assert ids == {"R1", "R2", "R3", "R5", "R6", "R8", "R9", DPD_COMPLETENESS}
    for check_id in ids:
        assert re.fullmatch(r"R[1-9][0-9]*", check_id), check_id


def test_single_obligor_exposure_is_restricted_and_aggregates_are_aggregated(
    cat: Catalogue,
) -> None:
    assert cat.measure("loans.largest_single_name_share_pct").sensitivity == "restricted"
    assert cat.measure("loans.largest_single_name_share_pct").over == "counterparty.id"
    assert cat.measure("loans.sector_hhi").sensitivity == "aggregated"
    restricted = {m.id for m in cat.members() if m.sensitivity == "restricted"}
    assert restricted == {
        "loans.largest_single_name_share_pct",
        "counterparty.id",
        "counterparty.name",
        "counterparty.source_reference",
        "counterparty.group",
    }
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
            assert measure.time_behaviour == "stock", measure.id
    assert cat.measure("events.write_off_rc").row_filters[0].values == ("WRITE_OFF",)


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
