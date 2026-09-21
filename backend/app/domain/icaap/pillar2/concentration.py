"""Credit concentration add-ons: benchmark-mapped, and the labelled heuristic.

Two methods, and the difference between them is honesty about calibration.

``benchmark_mapped`` measures the book (HHI, CRn, Gini), looks the measurement
up in a governed band table, and charges the band's add-on. The bands are a
platform calibration nobody has published — so they are REPRESENTATIVE rows in
the console, editable, and every output says so.

``hhi_proportional_heuristic`` is the legacy stress engine's coefficient × HHI
charge, kept because banks have been using it, and named for what it is. It is
**not** a granularity adjustment (D-016): a Gordy–Lütkebohmert GA is a
derivation from the single-risk-factor model with obligor PDs and LGDs, and
calling a proportional coefficient by that name would claim a model that is not
there. The real GA is P5's.

Three rules run through both:

* **D-035** — each metric is ``max(stated-only, with the unstated exposure as
  one bucket)``. Counting unstated as one bucket is usually conservative but
  not always: a book of two equal stated names plus a large unclassified
  remainder scores LOWER as one bucket than on its stated names alone. The
  envelope takes whichever reads worse.
* **Coverage** — below the governed coverage floor, a dimension is charged its
  table's top band. A measurement of a fifth of the book is not a measurement.
* **fn 2** — the input carries EAD only. Expected loss is provisioned, not
  capitalised, so a change in provisions must not move this figure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.domain.icaap.pillar2 import concentration_metrics as metrics_lib
from app.domain.icaap.pillar2.bands import BandTable, lookup
from app.domain.icaap.pillar2.types import (
    MethodResult,
    MethodStatus,
    MissingParameter,
    ParameterUse,
    detail_of,
    text,
)
from app.domain.icaap.units import HUNDRED, ZERO, Basis, Denominators, amount, ratio, to_amount
from app.domain.stress.concentration import ConcentrationExposure

DIMENSION_SINGLE_NAME = "single_name"
DIMENSION_SECTOR = "sector"

METRIC_HHI = "hhi"
METRIC_GINI = "gini"
METRIC_CRN = "crn"
METRICS: frozenset[str] = frozenset({METRIC_HHI, METRIC_GINI, METRIC_CRN})

#: Which exposure attribute carries each dimension's bucket key.
DIMENSION_ATTRIBUTES: Mapping[str, str] = {
    DIMENSION_SINGLE_NAME: "group_key",
    DIMENSION_SECTOR: "sector",
    "geography": "geography",
    "product": "product",
    "collateral_type": "collateral_type",
    "employer": "employer",
}

#: The governed code holding each dimension's band table, for the refusal
#: message when one is not configured. Codes, never values (D-024).
BAND_PARAM_CODES: Mapping[tuple[str, str], str] = {
    (DIMENSION_SINGLE_NAME, METRIC_HHI): "ccr_name_bands_hhi",
    (DIMENSION_SINGLE_NAME, METRIC_GINI): "ccr_name_bands_gini",
    (DIMENSION_SINGLE_NAME, METRIC_CRN): "ccr_name_bands_crn",
    (DIMENSION_SECTOR, METRIC_HHI): "ccr_sector_bands_hhi",
}

PARAM_METRIC_SET = "ccr_metric_set"
PARAM_CR_N = "ccr_name_cr_n"
PARAM_MIN_COVERAGE = "ccr_min_dimension_coverage_pct"
PARAM_NAME_COEFF = "ccr_name_hhi_coeff"
PARAM_SECTOR_COEFF = "ccr_sector_hhi_coeff"

BENCHMARK_METHOD = "benchmark_mapped"
HEURISTIC_METHOD = "hhi_proportional_heuristic"
#: The label every surface must print with a heuristic figure (D-016, M4).
HEURISTIC_LABEL = (
    "HHI-proportional heuristic (representative calibration — not a granularity adjustment)"
)


@dataclass(frozen=True)
class DimensionVector:
    """A book aggregated on one dimension, with its unclassified remainder."""

    dimension: str
    stated: tuple[Decimal, ...]
    unstated: Decimal = ZERO

    @property
    def stated_total(self) -> Decimal:
        return sum(self.stated, ZERO)

    @property
    def total(self) -> Decimal:
        return self.stated_total + self.unstated

    @property
    def one_bucket(self) -> tuple[Decimal, ...]:
        """The stated buckets plus the unclassified remainder as one more."""
        if self.unstated <= ZERO:
            return self.stated
        return (*self.stated, self.unstated)

    @property
    def coverage_pct(self) -> Decimal | None:
        """How much of the book the dimension actually classifies."""
        total = self.total
        if total <= ZERO:
            return None
        return ratio(self.stated_total * HUNDRED / total)


@dataclass(frozen=True)
class MetricValue:
    """One metric on one dimension, both readings and the envelope (D-035)."""

    dimension: str
    metric: str
    stated_only: Decimal | None
    with_unstated: Decimal | None
    value: Decimal | None

    @property
    def computable(self) -> bool:
        return self.value is not None


def aggregate_dimension(
    exposures: Sequence[ConcentrationExposure], dimension: str
) -> DimensionVector:
    """Sum EAD into the dimension's buckets, keeping the unstated remainder.

    Full vectors, not a top-n cut: a truncated book cannot produce an honest
    Gini or CRn, and its HHI is wrong in the safe-looking direction.
    """
    attribute = DIMENSION_ATTRIBUTES.get(dimension)
    if attribute is None:
        raise ValueError(f"unknown_concentration_dimension:{dimension}")
    buckets: dict[str, Decimal] = {}
    unstated = ZERO
    for exposure in exposures:
        if exposure.ead <= ZERO:
            continue
        key = getattr(exposure, attribute, None)
        if key is None or not str(key).strip():
            unstated += exposure.ead
            continue
        buckets[str(key)] = buckets.get(str(key), ZERO) + exposure.ead
    stated = tuple(value for _key, value in sorted(buckets.items()) if value > ZERO)
    return DimensionVector(dimension=dimension, stated=stated, unstated=unstated)


def _metric(values: Sequence[Decimal], metric: str, cr_n: int | None) -> Decimal | None:
    if metric == METRIC_HHI:
        return metrics_lib.hhi(values)
    if metric == METRIC_GINI:
        return metrics_lib.gini(values)
    if metric == METRIC_CRN:
        if cr_n is None:
            raise MissingParameter(PARAM_CR_N)
        return metrics_lib.concentration_ratio(values, cr_n)
    raise ValueError(f"unknown_concentration_metric:{metric}")


def dimension_metrics(
    vector: DimensionVector, *, metrics: Sequence[str], cr_n: int | None
) -> tuple[MetricValue, ...]:
    """Every configured metric on ``vector``, as the D-035 envelope."""
    results: list[MetricValue] = []
    for metric in metrics:
        stated_only = _metric(vector.stated, metric, cr_n)
        with_unstated = _metric(vector.one_bucket, metric, cr_n)
        candidates = [value for value in (stated_only, with_unstated) if value is not None]
        results.append(
            MetricValue(
                dimension=vector.dimension,
                metric=metric,
                stated_only=stated_only,
                with_unstated=with_unstated,
                value=max(candidates) if candidates else None,
            )
        )
    return tuple(results)


@dataclass(frozen=True)
class DimensionAddOn:
    """What one dimension charges, and which metric decided it."""

    dimension: str
    values: tuple[MetricValue, ...]
    addon_pct: Decimal | None
    basis: Basis | None
    deciding_metric: str | None
    coverage_pct: Decimal | None
    coverage_floor_applied: bool


def _dimension_addon(
    vector: DimensionVector,
    *,
    metrics: Sequence[str],
    cr_n: int | None,
    tables: Mapping[tuple[str, str], BandTable],
    min_coverage_pct: Decimal,
) -> tuple[DimensionAddOn, tuple[str, ...]]:
    reasons: list[str] = []
    coverage = vector.coverage_pct
    if vector.total <= ZERO:
        return (
            DimensionAddOn(vector.dimension, (), None, None, None, coverage, False),
            (f"dimension_absent:{vector.dimension}",),
        )

    values = dimension_metrics(vector, metrics=metrics, cr_n=cr_n)
    floor_applied = coverage is not None and coverage < min_coverage_pct
    if floor_applied:
        reasons.append(f"coverage_floor_applied:{vector.dimension}")

    best_pct: Decimal | None = None
    best_basis: Basis | None = None
    best_metric: str | None = None
    for value in values:
        table = tables.get((vector.dimension, value.metric))
        if table is None:
            raise MissingParameter(
                BAND_PARAM_CODES.get((vector.dimension, value.metric), "ccr_band_table"),
                detail=f"{vector.dimension}:{value.metric}",
            )
        if floor_applied:
            candidate = table.top_addon
        elif value.value is None:
            reasons.append(f"metric_not_computable:{vector.dimension}:{value.metric}")
            continue
        else:
            candidate = lookup(table, value.value)
        if best_pct is None or candidate > best_pct:
            best_pct, best_basis, best_metric = candidate, table.basis, value.metric

    return (
        DimensionAddOn(
            dimension=vector.dimension,
            values=values,
            addon_pct=best_pct,
            basis=best_basis,
            deciding_metric=best_metric,
            coverage_pct=coverage,
            coverage_floor_applied=floor_applied,
        ),
        tuple(reasons),
    )


def _convert(addons: Sequence[DimensionAddOn], denominators: Denominators) -> Decimal:
    bases = {addon.basis for addon in addons if addon.basis is not None}
    if len(bases) == 1:
        basis = next(iter(bases))
        total_pct = sum((addon.addon_pct or ZERO for addon in addons), ZERO)
        return to_amount(basis, total_pct, denominators)
    total = ZERO
    for addon in addons:
        if addon.basis is None or addon.addon_pct is None:
            continue
        total += to_amount(addon.basis, addon.addon_pct, denominators)
    return amount(total)


def benchmark_mapped(  # noqa: PLR0913 - every governed input arrives explicitly (D-024)
    *,
    name_vector: DimensionVector,
    sector_vector: DimensionVector,
    metric_set: Mapping[str, Sequence[str]] | None,
    cr_n: int | None,
    tables: Mapping[tuple[str, str], BandTable],
    min_coverage_pct: Decimal | None,
    baseline: Denominators,
    stressed: Denominators | None = None,
) -> MethodResult:
    """Map each dimension's worst metric to its governed band, then sum."""
    if metric_set is None:
        raise MissingParameter(PARAM_METRIC_SET)
    if min_coverage_pct is None:
        raise MissingParameter(PARAM_MIN_COVERAGE)

    reasons: list[str] = []
    addons: list[DimensionAddOn] = []
    uses: list[ParameterUse] = [
        ParameterUse(PARAM_METRIC_SET, "metric selection"),
        ParameterUse(PARAM_MIN_COVERAGE, "coverage floor"),
    ]
    for vector in (name_vector, sector_vector):
        configured = tuple(metric_set.get(vector.dimension, ()))
        if not configured:
            reasons.append(f"dimension_not_configured:{vector.dimension}")
            continue
        if METRIC_CRN in configured:
            uses.append(ParameterUse(PARAM_CR_N, f"{vector.dimension}:crn"))
        addon, dimension_reasons = _dimension_addon(
            vector,
            metrics=configured,
            cr_n=cr_n,
            tables=tables,
            min_coverage_pct=min_coverage_pct,
        )
        reasons.extend(dimension_reasons)
        addons.append(addon)
        for metric in configured:
            table = tables.get((vector.dimension, metric))
            if table is not None:
                uses.append(ParameterUse(table.param_code, f"{vector.dimension}:{metric}"))

    baseline_amount = _convert(addons, baseline)
    bases = {addon.basis for addon in addons if addon.basis is not None}
    single_basis = next(iter(bases)) if len(bases) == 1 else None
    basis_value = (
        sum((addon.addon_pct or ZERO for addon in addons), ZERO)
        if single_basis is not None
        else None
    )

    if stressed is None:
        stressed_amount = None
        stressed_derivation = "not_assessed"
        reasons.append("stressed_denominators_missing")
    else:
        stressed_amount = _convert(addons, stressed)
        stressed_derivation = "method"

    detail: dict[str, str | None] = {}
    for addon in addons:
        prefix = addon.dimension
        detail[f"{prefix}_coverage_pct"] = text(addon.coverage_pct)
        detail[f"{prefix}_coverage_floor_applied"] = str(addon.coverage_floor_applied).lower()
        detail[f"{prefix}_addon_pct"] = text(addon.addon_pct)
        detail[f"{prefix}_deciding_metric"] = addon.deciding_metric
        detail[f"{prefix}_basis"] = addon.basis.value if addon.basis else None
        for value in addon.values:
            detail[f"{prefix}_{value.metric}_stated_only"] = text(value.stated_only)
            detail[f"{prefix}_{value.metric}_with_unstated"] = text(value.with_unstated)
            detail[f"{prefix}_{value.metric}"] = text(value.value)

    return MethodResult(
        method=BENCHMARK_METHOD,
        method_version="v1",
        status=MethodStatus.COMPUTED,
        basis=single_basis if single_basis is not None else Basis.ABSOLUTE,
        basis_value=basis_value if basis_value is not None else baseline_amount,
        baseline_amount=baseline_amount,
        stressed_amount=stressed_amount,
        baseline_derivation="method",
        stressed_derivation=stressed_derivation,
        detail=detail_of(detail),
        reasons=tuple(reasons),
        parameters_used=tuple(uses),
    )


def hhi_proportional_heuristic(  # noqa: PLR0913 - coefficients and both bases
    *,
    name_vector: DimensionVector,
    sector_vector: DimensionVector,
    name_coeff: Decimal | None,
    sector_coeff: Decimal | None,
    baseline: Denominators,
    stressed: Denominators | None = None,
) -> MethodResult:
    """Coefficient × HHI of credit RWA — the legacy charge, correctly named."""
    if name_coeff is None:
        raise MissingParameter(PARAM_NAME_COEFF)
    if sector_coeff is None:
        raise MissingParameter(PARAM_SECTOR_COEFF)

    reasons: list[str] = []
    contributions = ZERO
    detail: dict[str, str | None] = {"label": HEURISTIC_LABEL}
    for vector, coeff in ((name_vector, name_coeff), (sector_vector, sector_coeff)):
        envelope = dimension_metrics(vector, metrics=(METRIC_HHI,), cr_n=None)[0]
        detail[f"{vector.dimension}_hhi_stated_only"] = text(envelope.stated_only)
        detail[f"{vector.dimension}_hhi_with_unstated"] = text(envelope.with_unstated)
        detail[f"{vector.dimension}_hhi"] = text(envelope.value)
        detail[f"{vector.dimension}_coefficient"] = text(coeff)
        if envelope.value is None:
            reasons.append(f"dimension_absent:{vector.dimension}")
            continue
        contributions += coeff * envelope.value

    basis_value = HUNDRED * contributions
    baseline_amount = to_amount(Basis.PCT_CREDIT_RWA, basis_value, baseline)
    if stressed is None:
        stressed_amount = None
        stressed_derivation = "not_assessed"
        reasons.append("stressed_denominators_missing")
    else:
        stressed_amount = to_amount(Basis.PCT_CREDIT_RWA, basis_value, stressed)
        stressed_derivation = "method"
    detail["addon_pct_credit_rwa"] = text(basis_value)

    return MethodResult(
        method=HEURISTIC_METHOD,
        method_version="v0",
        status=MethodStatus.COMPUTED,
        basis=Basis.PCT_CREDIT_RWA,
        basis_value=basis_value,
        baseline_amount=baseline_amount,
        stressed_amount=stressed_amount,
        baseline_derivation="method",
        stressed_derivation=stressed_derivation,
        detail=detail_of(detail),
        reasons=tuple(reasons),
        parameters_used=(
            ParameterUse(PARAM_NAME_COEFF, "single_name:hhi"),
            ParameterUse(PARAM_SECTOR_COEFF, "sector:hhi"),
        ),
    )


__all__ = [
    "BAND_PARAM_CODES",
    "BENCHMARK_METHOD",
    "DIMENSION_ATTRIBUTES",
    "DIMENSION_SECTOR",
    "DIMENSION_SINGLE_NAME",
    "HEURISTIC_LABEL",
    "HEURISTIC_METHOD",
    "METRICS",
    "METRIC_CRN",
    "METRIC_GINI",
    "METRIC_HHI",
    "PARAM_CR_N",
    "PARAM_METRIC_SET",
    "PARAM_MIN_COVERAGE",
    "PARAM_NAME_COEFF",
    "PARAM_SECTOR_COEFF",
    "DimensionAddOn",
    "DimensionVector",
    "MetricValue",
    "aggregate_dimension",
    "benchmark_mapped",
    "dimension_metrics",
    "hhi_proportional_heuristic",
]
