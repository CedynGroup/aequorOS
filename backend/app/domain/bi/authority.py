"""The bridge from the metric authority registry to BI engine measures.

Two questions the BI plane asks the registry, answered here and nowhere else:

1. Which registered metrics are PRODUCED on one of the platform's two
   computation tiers, and by which live module? (``engine_authorities``)
   The registry records *consumption* (``canonical_inputs``) and *sealing*
   (``authoritative_run_type``); it does not record which live module publishes
   a metric that is computed on read. The two such cases today are named in
   :data:`READ_COMPUTED_LIVE_MODULES` rather than inferred.
2. Given a metric key copied from a payload and the institution it was computed
   for, which single authority owns it — and therefore which advisory
   designation it carries (D-022)? (``resolve_authority`` /
   ``advisory_designation_for``). An unresolvable pair is ``"unregistered"``,
   never silently promoted (H-009: an SDI's IRRBB figures resolve to nothing
   until the registry gains the s.29 IRRBB entry).
"""

from __future__ import annotations

from functools import cache
from typing import Literal

from app.domain.authority.registry import (
    REGISTRY,
    InstitutionClass,
    MetricAuthority,
    Regime,
    UnknownMetricError,
)
from app.domain.authority.registry import AdvisoryDesignation as RegistryDesignation

#: The registry's three designations plus the one the registry cannot express.
AdvisoryDesignation = Literal["filed", "supervisory_monitoring", "advisory_only", "unregistered"]

#: The live-engine module vocabulary (``app.models.live.LIVE_MODULES``), restated
#: here because the domain may not import the model. Parity is pinned by
#: ``tests/services/bi/test_catalogue_parity.py``.
ENGINE_MODULES: tuple[str, ...] = (
    "liquidity",
    "capital",
    "credit",
    "irr",
    "fx",
    "ftp",
    "rating",
    "forecast",
)

#: Methodologies whose metrics are computed on read (no sealed run, so the
#: registry's ``authoritative_run_type`` is ``None``) yet ARE published by a live
#: module's ``compute_live`` payload. Explicit data, not inference: adding a
#: methodology here asserts that the named module writes those metric keys,
#: which the parity test checks against the service source.
READ_COMPUTED_LIVE_MODULES: dict[str, str] = {
    # ``implied_rating.compute_live`` → live module ``rating``.
    "aequoros_implied_rating_scorecard": "rating",
    # ``regulatory_capital._sdi_compute_live`` → live module ``capital`` under s.29.
    "act930_s29_nof_rwa": "capital",
}

#: Registry metric ids whose payload value is text (a grade, a ceiling, a flag),
#: not a number. ``bi_fact_engine_metric.value`` is numeric, so they are copied
#: with a NULL value and are not offered as measures.
TEXT_VALUED_METRIC_IDS: frozenset[str] = frozenset(
    {
        "pit_rating_grade",
        "ttc_rating_grade",
        "standalone_grade",
        "sovereign_ceiling",
        "ddep_eligible",
    }
)

#: Regimes that are not the law for one class of institution: a metric
#: registered only under one of these is the same figure for a bank and an SDI.
CLASS_NEUTRAL_REGIMES: frozenset[Regime] = frozenset(
    {Regime.LMTD, Regime.IFRS9, Regime.ADVISORY_INTERNAL}
)

#: Live-module key → authorization ``Module`` value. Restated from the
#: dashboard's ``CAPABILITY_MODULES`` / ``module_scope.MODULE_SCOPE_KEY`` shape;
#: the architecture test resolves every value through the enum.
AUTHORIZATION_MODULE: dict[str, str] = {
    "liquidity": "liq",
    "capital": "cap",
    "credit": "credit",
    "irr": "irrbb",
    "fx": "fx",
    "ftp": "ftp",
    "rating": "markets",
    "forecast": "fcst",
}

#: Live-module key → ``institution_types.default_modules`` entitlement slug.
#: Mirror of ``module_scope.MODULE_SCOPE_KEY`` (parity-tested).
ENTITLEMENT_SLUG: dict[str, str] = {
    "liquidity": "liquidity",
    "capital": "capital",
    "credit": "credit",
    "irr": "irrbb",
    "fx": "fx",
    "ftp": "ftp",
    "rating": "markets",
    "forecast": "forecasting",
}

UNREGISTERED: AdvisoryDesignation = "unregistered"

#: Registry enum → the catalogue's string vocabulary (the catalogue adds
#: ``"unregistered"``, which the registry cannot express).
_DESIGNATIONS: dict[RegistryDesignation, AdvisoryDesignation] = {
    RegistryDesignation.FILED: "filed",
    RegistryDesignation.SUPERVISORY_MONITORING: "supervisory_monitoring",
    RegistryDesignation.ADVISORY_ONLY: "advisory_only",
}


def designation_of(entry: MetricAuthority) -> AdvisoryDesignation:
    """A registry entry's designation in the catalogue vocabulary."""
    return _DESIGNATIONS[entry.advisory_designation]


def engine_module_for(entry: MetricAuthority) -> str | None:
    """The live module that produces ``entry``'s metric, or ``None``.

    A metric is tier-produced when a run of a live module seals it
    (``authoritative_run_type``) or when a live module publishes it on read
    (:data:`READ_COMPUTED_LIVE_MODULES`). Everything else — form ratios read off
    a template, LMTD table ratios computed from the book, standardised-framework
    and stress runs outside the live module set — is not copied into
    ``bi_fact_engine_metric`` and has no engine measure.
    """
    run_type = entry.authoritative_run_type
    if run_type in ENGINE_MODULES:
        return run_type
    return READ_COMPUTED_LIVE_MODULES.get(entry.methodology_id)


@cache
def engine_authorities() -> tuple[MetricAuthority, ...]:
    """Every PRIMARY registry entry that is tier-produced, in registry order."""
    return tuple(
        entry for entry in REGISTRY if entry.is_primary and engine_module_for(entry) is not None
    )


def resolve_authority(
    metric_id: str, *, regime: str, institution_class: str
) -> MetricAuthority | None:
    """The single primary authority for ``metric_id`` as computed for one tenant.

    ``regime`` is the institution's capital regime (``institution_types.
    capital_regime``: ``crd`` / ``s29``) and ``institution_class`` its class
    (``bank`` / ``sdi``). Resolution, in order:

    1. the primary authority registered under the institution's own regime;
    2. otherwise the ONE primary authority under a class-neutral regime whose
       ``institution_class`` covers the tenant (``all`` or the exact class);
    3. otherwise ``None`` — the figure has no declared owner for this tenant.

    A metric is never resolved across regimes that bind a different class: an
    SDI's ``worst_eve_change_pct_tier1`` does not borrow the CRD entry (H-009).
    """
    try:
        own_regime = Regime(regime)
    except ValueError:
        return None
    try:
        klass = InstitutionClass(institution_class)
    except ValueError:
        return None
    try:
        return REGISTRY.primary_for(metric_id, regime=own_regime)
    except UnknownMetricError:
        pass
    neutral = [
        entry
        for entry in REGISTRY
        if entry.metric_id == metric_id
        and entry.is_primary
        and entry.regime in CLASS_NEUTRAL_REGIMES
        and entry.institution_class in (InstitutionClass.ALL, klass)
    ]
    if len(neutral) == 1:
        return neutral[0]
    return None


def advisory_designation_for(
    metric_id: str, *, regime: str, institution_class: str
) -> AdvisoryDesignation:
    """The registry's designation for a copied figure, or ``"unregistered"``."""
    entry = resolve_authority(metric_id, regime=regime, institution_class=institution_class)
    if entry is None:
        return UNREGISTERED
    return designation_of(entry)
