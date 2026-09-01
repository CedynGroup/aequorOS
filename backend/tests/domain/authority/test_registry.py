"""Metric Authority Registry integrity (primitive P1).

These tests are the enforcement half of the registry: they fail if a second
authority is ever registered for the same (metric x regime x methodology), if a
registered engine does not exist, or if an alternate methodology is declared
without documenting its divergence.
"""

from __future__ import annotations

import importlib
from datetime import date
from decimal import Decimal

import pytest

from app.domain.authority.registry import (
    ACCEPTED_BY_AUTHORITY,
    EXTERNAL_REGULATORY_VERIFICATION_REQUIRED,
    REGISTRY,
    UNRESOLVED_AUDIT_FINDING,
    AdvisoryDesignation,
    DuplicateAuthorityError,
    InstitutionClass,
    MethodologyDivergence,
    MetricAuthority,
    MetricAuthorityRegistry,
    MetricFamily,
    Regime,
    UnknownMetricError,
    authorities_for_metric,
    get_authority,
    multi_authority_metrics,
)


def _authority(**overrides: object) -> MetricAuthority:
    base: dict[str, object] = {
        "metric_id": "test_metric",
        "metric_family": MetricFamily.CAPITAL,
        "institution_class": InstitutionClass.BANK,
        "jurisdiction": "GH",
        "regulator": "BOG",
        "regime": Regime.CRD_BASEL,
        "methodology_id": "test_methodology",
        "return_family": "capital",
        "effective_from": date(2026, 1, 1),
        "calculation_engine": "app.domain.capital.engine:compute_rwa",
    }
    base.update(overrides)
    return MetricAuthority(**base)  # type: ignore[arg-type]


# -- uniqueness: exactly ONE authority per (metric x regime x methodology) --


def test_registering_a_duplicate_authority_fails_loudly() -> None:
    registry = MetricAuthorityRegistry()
    registry.register(_authority())
    with pytest.raises(DuplicateAuthorityError) as excinfo:
        registry.register(_authority(calculation_engine="app.domain.capital.engine:tier1_capital"))
    assert "duplicate metric authority" in str(excinfo.value)
    assert excinfo.value.key.metric_id == "test_metric"
    assert len(registry) == 1


def test_same_metric_under_a_different_regime_is_allowed() -> None:
    registry = MetricAuthorityRegistry()
    registry.register(_authority())
    registry.register(_authority(regime=Regime.ACT930_S29))
    assert len(registry) == 2


def test_same_metric_and_regime_under_a_different_methodology_is_allowed() -> None:
    registry = MetricAuthorityRegistry()
    registry.register(_authority())
    registry.register(
        _authority(
            methodology_id="other_methodology",
            is_primary=False,
            divergence=MethodologyDivergence(
                versus_methodology_id="test_methodology",
                authority_reference="test citation",
                reason="different denominator",
                direction="lower",
                reconciliation_rule="not reconciled by equality",
            ),
        )
    )
    assert len(registry) == 2


def test_the_live_registry_has_no_duplicate_authority_keys() -> None:
    keys = REGISTRY.keys
    assert len(keys) == len(set(keys)) == len(REGISTRY)


def test_every_metric_regime_pair_has_at_most_one_primary_authority() -> None:
    seen: dict[tuple[str, Regime], MetricAuthority] = {}
    for entry in REGISTRY:
        if not entry.is_primary:
            continue
        pair = (entry.metric_id, entry.regime)
        assert pair not in seen, (
            f"two PRIMARY authorities for {entry.metric_id} under {entry.regime.value}: "
            f"{seen[pair].methodology_id} and {entry.methodology_id}"
        )
        seen[pair] = entry


def test_primary_for_resolves_a_single_authority() -> None:
    entry = REGISTRY.primary_for("car_pct", regime=Regime.CRD_BASEL)
    assert entry.methodology_id == "crd_basel_capital_run"
    assert entry.calculation_engine == "app.domain.capital.engine:compute_capital_ratios"
    assert entry.advisory_designation is AdvisoryDesignation.FILED

    sdi = REGISTRY.primary_for("car_pct", regime=Regime.ACT930_S29)
    assert sdi.methodology_id == "act930_s29_nof_rwa"
    assert sdi.institution_class is InstitutionClass.SDI


def test_unknown_metric_lookups_raise() -> None:
    with pytest.raises(UnknownMetricError):
        authorities_for_metric("not_a_real_metric")
    with pytest.raises(UnknownMetricError):
        get_authority("car_pct", regime=Regime.LMTD, methodology_id="crd_basel_capital_run")


# -- every registered engine actually exists -------------------------------


def test_every_registered_engine_imports_and_is_callable() -> None:
    for entry in REGISTRY:
        assert entry.calculation_engine, f"{entry.key} has no calculation_engine"
        module_path, sep, attribute = entry.calculation_engine.partition(":")
        if not sep:
            # A module-level authority (a formula evaluator, not one callable).
            importlib.import_module(module_path)
            continue
        module = importlib.import_module(module_path)
        target = getattr(module, attribute, None)
        assert target is not None, f"{entry.key}: {entry.calculation_engine} does not exist"
        assert callable(target), f"{entry.key}: {entry.calculation_engine} is not callable"


def test_every_registered_policy_resolver_imports_where_it_names_one() -> None:
    for entry in REGISTRY:
        resolver = entry.policy_resolver
        if EXTERNAL_REGULATORY_VERIFICATION_REQUIRED in resolver or ":" not in resolver:
            continue
        module_path, _, attribute = resolver.partition(":")
        module = importlib.import_module(module_path)
        assert callable(getattr(module, attribute, None)), (
            f"{entry.key}: policy_resolver {resolver} is not a callable"
        )


# -- alternate methodologies must document their divergence ----------------


def test_an_alternate_without_divergence_documentation_is_rejected() -> None:
    with pytest.raises(ValueError, match="must document"):
        _authority(methodology_id="alt", is_primary=False)


def test_every_registered_alternate_documents_its_divergence() -> None:
    alternates = [entry for entry in REGISTRY if not entry.is_primary]
    assert alternates, "the registry must encode at least one alternate methodology"
    for entry in alternates:
        divergence = entry.divergence
        assert divergence is not None, f"{entry.key} is an alternate with no divergence"
        assert divergence.versus_methodology_id
        assert divergence.reason
        assert divergence.reconciliation_rule
        assert divergence.direction in {"lower", "higher", "either"}
        assert divergence.resolution_status in {ACCEPTED_BY_AUTHORITY, UNRESOLVED_AUDIT_FINDING}
        counterpart = REGISTRY.for_metric(entry.metric_id)
        assert divergence.versus_methodology_id in {c.methodology_id for c in counterpart}, (
            f"{entry.key} diverges from an unregistered methodology "
            f"{divergence.versus_methodology_id!r}"
        )


def test_divergence_rejects_an_unknown_direction_or_resolution_status() -> None:
    kwargs = {
        "versus_methodology_id": "x",
        "authority_reference": "y",
        "reason": "z",
        "reconciliation_rule": "r",
    }
    with pytest.raises(ValueError, match="direction"):
        MethodologyDivergence(direction="sideways", **kwargs)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="resolution_status"):
        MethodologyDivergence(direction="lower", resolution_status="maybe", **kwargs)  # type: ignore[arg-type]


# -- the specific divergences the audit found ------------------------------


def test_lcr_has_two_declared_methodologies_and_no_equality_is_asserted() -> None:
    """BSD3/LCR-NSFR LCR vs LMT Table 11 LCR — the audit's finding 1."""
    uncapped = get_authority(
        "lcr_pct", regime=Regime.CRD_BASEL, methodology_id="basel_bog_liquidity_run"
    )
    capped = get_authority("lcr_pct", regime=Regime.LMTD, methodology_id="lmtd_table11_capped")

    assert uncapped.is_primary is True
    assert "lmtd_table11_capped" in uncapped.approved_alternate_methodologies

    assert capped.is_primary is False
    assert capped.divergence is not None
    assert capped.divergence.versus_methodology_id == "basel_bog_liquidity_run"
    assert capped.divergence.direction == "lower"
    assert capped.divergence.resolution_status == ACCEPTED_BY_AUTHORITY
    assert capped.divergence.equality_assertion_forbidden is True
    assert "75%" in capped.divergence.reason
    assert "_LCR_INFLOW_CAP" in capped.divergence.reason
    assert "39-43" in capped.authority_reference
    # Both are filed: each is correct under its own return's authority.
    assert uncapped.advisory_designation is AdvisoryDesignation.FILED
    assert capped.advisory_designation is AdvisoryDesignation.FILED


def test_car_has_a_declared_bog_template_methodology() -> None:
    """BSD5A E70 = E25/E69 is BoG's own ratio, not the Basel engine's."""
    template = get_authority(
        "car_pct", regime=Regime.CRD_BASEL, methodology_id="bog_bsd5a_form_ratio"
    )
    assert template.divergence is not None
    assert template.divergence.resolution_status == ACCEPTED_BY_AUTHORITY
    assert template.divergence.equality_assertion_forbidden is True
    assert "BSD5A!E70" in template.reporting_mappings
    assert any("test_bsd5" in item for item in template.divergence.evidence)


def test_forecast_year0_divergence_is_unresolved_and_never_filable() -> None:
    """The audit's HIGH finding: forecast Y0 CAR can differ from the capital run."""
    forecast = get_authority(
        "car_pct",
        regime=Regime.ADVISORY_INTERNAL,
        methodology_id="bank_forecast_projection_path",
    )
    assert forecast.divergence is not None
    assert forecast.divergence.resolution_status == UNRESOLVED_AUDIT_FINDING
    assert forecast.advisory_designation is AdvisoryDesignation.ADVISORY_ONLY
    assert "ecl_exposure" in forecast.divergence.reason
    assert forecast.audit_findings


def test_unresolved_divergences_are_never_designated_filed() -> None:
    unresolved = REGISTRY.unresolved_divergences()
    assert unresolved, "the audit's unresolved divergences must be encoded"
    for entry in unresolved:
        assert entry.advisory_designation is AdvisoryDesignation.ADVISORY_ONLY, (
            f"{entry.key} carries an unresolved divergence but is designated "
            f"{entry.advisory_designation.value}"
        )


def test_sdi_and_bank_capital_are_separate_regimes_not_one_engine() -> None:
    bank = REGISTRY.primary_for("car_pct", regime=Regime.CRD_BASEL)
    sdi = REGISTRY.primary_for("car_pct", regime=Regime.ACT930_S29)
    assert bank.calculation_engine != sdi.calculation_engine
    assert "Act 930" in sdi.authority_reference
    assert bank.calculation_engine in sdi.forbidden_alternative_sources


def test_multi_authority_metrics_is_the_headline_output() -> None:
    collisions = multi_authority_metrics()
    assert set(collisions) == {
        "car_pct",
        "lcr_pct",
        "nsfr_pct",
        "total_rwa_ghs",
        # Every credit metric is deliberately dual-authority: the same figure is
        # owned by the bank 5-grade methodology under CRD and the NBFI 4-grade
        # methodology under Act 930 s.29 (credit PR-2) — one engine, two legal
        # grids, never one methodology across both regimes.
        "npl_ratio",
        "npl_ratio_pct",
        "gross_loans_ghs",
        "npl_exposure_ghs",
        "total_provision_required_ghs",
        "provision_held_ghs",
        "provision_coverage_pct",
        "par_30_pct",
        "par_60_pct",
        "par_90_pct",
        "unclassified_exposure_ghs",
    }
    for metric_id, entries in collisions.items():
        methodologies = [entry.methodology_id for entry in entries]
        assert len(methodologies) == len(set(methodologies)), (
            f"{metric_id} repeats a methodology_id across regimes without distinguishing it"
        )
        filed = [e for e in entries if e.advisory_designation is AdvisoryDesignation.FILED]
        for entry in filed:
            assert entry.is_primary or entry.divergence is not None


# -- coverage of the material metric families ------------------------------


def test_every_named_material_metric_family_is_registered() -> None:
    assert set(REGISTRY.counts_by_family()) == {
        "capital",
        "credit",
        "forecast",
        "ftp",
        "fx",
        "irrbb",
        "liquidity",
        "stress",
    }


@pytest.mark.parametrize(
    "metric_id",
    [
        # capital
        "car_pct",
        "tier1_ratio_pct",
        "cet1_ratio_pct",
        "leverage_ratio_pct",
        "total_capital_ghs",
        "cet1_capital",
        "tier1_capital",
        "tier2_capital",
        "total_rwa_ghs",
        "credit_rwa_ghs",
        "market_rwa_ghs",
        "operational_rwa_ghs",
        # liquidity
        "hqla_total_ghs",
        "net_outflows_30d_ghs",
        "lcr_pct",
        "asf_total_ghs",
        "rsf_total_ghs",
        "nsfr_pct",
        # irrbb / fx / ftp
        "eve_base_ghs",
        "nii_base_ghs",
        "ear_up_200_ghs",
        "nop_ghs",
        "portfolio_nim_pct",
        # projections and stress
        "year5_car_pct",
        "year5_lcr_pct",
        "min_car_pct",
        "stressed_car_end_pct",
        "capital_breach_multiplier",
    ],
)
def test_material_metric_is_registered(metric_id: str) -> None:
    assert authorities_for_metric(metric_id)


# -- schema discipline -----------------------------------------------------


def test_every_entry_names_a_metric_family_class_and_regime() -> None:
    for entry in REGISTRY:
        assert entry.metric_id
        assert entry.methodology_id
        assert isinstance(entry.metric_family, MetricFamily)
        assert isinstance(entry.institution_class, InstitutionClass)
        assert isinstance(entry.regime, Regime)
        assert entry.jurisdiction
        assert entry.regulator
        assert entry.authority_reference


def test_filed_metrics_declare_a_reporting_mapping() -> None:
    for entry in REGISTRY.filable():
        assert entry.reporting_mappings, (
            f"{entry.key} is designated FILED but names no return code or template cell"
        )


def test_filed_metrics_forbid_the_case_scoped_plane() -> None:
    """The audit's non-competition boundary, encoded per metric."""
    for entry in REGISTRY.filable():
        forbidden = set(entry.forbidden_alternative_sources)
        assert "app.models.calculation:CalculationRun" in forbidden, (
            f"{entry.key} does not forbid the case-scoped CalculationRun plane"
        )


def test_exact_tolerance_metrics_are_run_backed() -> None:
    for entry in REGISTRY:
        if entry.expected_tolerance == Decimal("0"):
            assert entry.authoritative_run_type or entry.calculation_version, (
                f"{entry.key} claims exact tolerance without a sealed run or version"
            )


def test_advisory_only_metrics_are_never_mapped_to_a_return() -> None:
    for entry in REGISTRY:
        if entry.advisory_designation is AdvisoryDesignation.ADVISORY_ONLY:
            assert not entry.reporting_mappings, (
                f"{entry.key} is ADVISORY_ONLY but is mapped to {entry.reporting_mappings}"
            )


def test_effective_dating_is_coherent() -> None:
    for entry in REGISTRY:
        assert entry.is_effective_on(date(2026, 8, 21))
        assert not entry.is_effective_on(date(2025, 12, 31))
    with pytest.raises(ValueError, match="effective_to precedes"):
        _authority(effective_from=date(2026, 6, 1), effective_to=date(2026, 1, 1))


def test_every_entry_serialises_to_a_json_ready_dict() -> None:
    for entry in REGISTRY:
        payload = entry.to_dict()
        assert payload["metric_id"] == entry.metric_id
        assert payload["methodology_id"] == entry.methodology_id
        assert payload["regime"] == entry.regime.value
        assert isinstance(payload["canonical_inputs"], list)
        assert (payload["divergence"] is None) == (entry.divergence is None)


def test_entries_needing_external_verification_are_flagged_not_guessed() -> None:
    flagged = REGISTRY.requiring_external_verification()
    assert flagged, "the registry must be honest about unestablished authority"
    for entry in flagged:
        assert entry.requires_external_verification is True
    # An entry with a real citation and a real resolver is not flagged.
    grounded = REGISTRY.primary_for("car_pct", regime=Regime.CRD_BASEL)
    assert grounded.requires_external_verification is False


#: Every module that DECLARES an engine generation. The registry's
#: ``calculation_version`` is a copy of one of these, so the two drift silently
#: unless something joins them — which is the mechanism behind forensic
#: re-audit D-5, where two engines changed methodology and neither the constant
#: nor the registry moved.
_ENGINE_VERSION_MODULES = (
    "app.domain.stress.orchestrator",
    "app.services.calculations",
    "app.services.capital",
    "app.services.enterprise_stress",
    "app.services.implied_rating",
    "app.services.regulatory_capital",
    "app.services.regulatory_forecasting",
    "app.services.regulatory_ftp",
    "app.services.regulatory_fx",
    "app.services.regulatory_irr",
    "app.services.regulatory_liquidity",
    "app.services.reverse_stress",
)

#: ``calculation_version`` values that are deliberately NOT an engine constant,
#: each naming what it IS instead. Anything else shaped like an engine version
#: must resolve to a live ``ENGINE_VERSION``.
_NON_ENGINE_VERSIONS = {
    EXTERNAL_REGULATORY_VERIFICATION_REQUIRED: "no engine owns the figure yet",
    "bog_form/template-formula-evaluator": "BoG's own workbook formulas, not an engine",
    "lmt/bog-lmt-liquidity-v1": "the LMT generator's template id, not an engine",
}


def test_every_declared_calculation_version_is_a_live_engine_version() -> None:
    """The registry may not name an engine generation that no longer exists.

    Forensic re-audit D-5: ``regulatory_capital`` and ``regulatory_liquidity``
    both changed methodology — a re-based operational RWA and the Basel HQLA
    haircuts/caps — while ``ENGINE_VERSION`` stayed at ``v1.0.0``, so four
    distinct capital-metric generations sit on the primary under one string.
    Bumping the constants fixes that instance; this test is what makes the NEXT
    bump impossible to apply in only one of the two places, in either direction.
    """
    live = {
        importlib.import_module(name).ENGINE_VERSION for name in _ENGINE_VERSION_MODULES
    }
    stale = sorted(
        {
            entry.calculation_version
            for entry in REGISTRY
            if entry.calculation_version
            and entry.calculation_version not in _NON_ENGINE_VERSIONS
            and entry.calculation_version not in live
        }
    )
    assert not stale, (
        "these calculation_version values are declared in the authority registry but "
        "are not the ENGINE_VERSION of any engine module — either the constant was "
        f"bumped and the registry was not, or the reverse: {stale}"
    )
