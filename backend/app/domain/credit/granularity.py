"""The full granularity adjustment (Gordy-Lütkebohmert), pure.

A Pillar 1 IRB charge is an asymptotic figure: it assumes the book is infinitely
fine-grained, so that every idiosyncratic shock diversifies away and only the
single systematic factor is left. Real books are not. The granularity adjustment
is the first-order correction for that — the capital the asymptotic formula
leaves out because a finite number of names still carry undiversified risk.

**This is the full adjustment, and it is not what the platform already had.**
``app/domain/icaap/pillar2/concentration.py`` ships ``hhi_proportional_heuristic``
— a governed coefficient multiplied by the book's HHI — and its own docstring
says it is *not* a granularity adjustment (D-016). That is the honest position:
a coefficient × HHI has no obligor PD, no LGD, no correlation and no confidence
level in it, so it cannot be read as a derivation from the single-risk-factor
model. This module is the derivation. The two produce different numbers from
different inputs, and a reader must always be able to tell which one ran, so the
P2 adapter stamps ``method_variant`` on every result it returns.

The method, per exposure group *i* with share ``sᵢ`` of total EAD::

    GA = (1 / 2K*) · Σ sᵢ² · Cᵢ · [δ(Kᵢ + Rᵢ) − Kᵢ],   K* = Σ sᵢKᵢ

    Rᵢ = ELGDᵢ · PDᵢ
    Kᵢ = ELGDᵢ · Φ( (Φ⁻¹(PDᵢ) + √ρᵢ · Φ⁻¹(q)) / √(1 − ρᵢ) ) − Rᵢ      [× MA]
    ρᵢ = r_min·w + r_max·(1 − w),  w = (1 − e^(−k·PD)) / (1 − e^(−k))
    Cᵢ = (ELGDᵢ² + γ·ELGDᵢ(1 − ELGDᵢ)) / ELGDᵢ
    MA = (1 + (M − m_centre)·b) / (1 − m_scale·b),  b = (b_int − b_slope·ln PD)²

Every one of ``q``, ``δ``, ``γ``, ``r_min``, ``r_max``, ``k``, the maturity
coefficients and the minimum effective name count is a governed control-plane row
resolved by the caller and handed in as :class:`GaParams` (D-024). Nothing here
knows a calibration; the module holds only the structure of the formula.

**Refusals are typed and explicit — never a quiet approximation.** Two kinds:

* a book that cannot support the first-order correction returns
  ``status="not_applicable"`` with the machine reason, no ``ga`` and no
  ``addon_amount``. The gate is the EFFECTIVE number of names ``N_eff = 1/Σsᵢ²``,
  not the raw count, because one dominant obligor plus a thousand small ones
  passes any count test while ``N_eff`` is barely above 1 — and it is exactly
  that book the asymptotic assumption fails hardest on;
* an input that is not a number this formula can consume — a PD outside (0, 1),
  a non-positive EAD or ELGD, a segment with no governed correlation, a missing
  maturity while the maturity adjustment is switched on — raises
  :class:`GranularityInputError` naming the code and the offending reference. The
  caller filters and resolves the book first; this module never substitutes.

Φ and Φ⁻¹ come from :class:`statistics.NormalDist` (IEEE-754 doubles), which is
the one float boundary in the module. Each crossing is converted straight back
to ``Decimal`` at :data:`FLOAT_QUANTUM` and everything else is exact decimal
arithmetic at :data:`WORKING_PRECISION`, under a local context so the result does
not depend on the caller's. Summations run in sorted key order, so the figure is
invariant to the order the exposures arrive in.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext
from statistics import NormalDist
from typing import Literal

# --- structural constants (no regulatory value is stated here, D-024) -------

ZERO = Decimal(0)
ONE = Decimal(1)
#: The 2 in ``1/(2K*)``, and the square in the maturity coefficient ``b``. Both
#: are the shape of the formula, not a calibration.
TWO = Decimal(2)

#: Working precision for the decimal arithmetic. Far beyond anything reported;
#: it exists so intermediate quotients and square roots do not round.
WORKING_PRECISION = 50
#: Where a float from ``NormalDist`` / ``math`` re-enters decimal arithmetic:
#: ten decimal places, deterministic for IEEE-754 doubles.
FLOAT_QUANTUM = Decimal("1E-10")
#: Reported precision of ``ga``, ``k_star`` and each contribution.
RATIO_QUANTUM = Decimal("1E-10")
#: Reported precision of ``n_eff`` — a count of names, to six places.
COUNT_QUANTUM = Decimal("1E-6")
#: Reported precision of ``total_ead`` and ``addon_amount``. Finer than the
#: engine's money quantum so the consumer, not this module, decides the
#: rounding it files.
AMOUNT_QUANTUM = Decimal("1E-6")
#: How many obligor groups the contribution diagnostic lists. A display depth,
#: not a threshold — the figure is unaffected.
TOP_CONTRIBUTORS = 5

_NORMAL = NormalDist()

# --- vocabulary -------------------------------------------------------------

#: The IRB correlation segments this module knows. The segment NAMES are a
#: taxonomy; every correlation VALUE behind them is governed.
Segment = Literal["corporate", "retail_other"]
SEGMENT_CORPORATE: Segment = "corporate"
SEGMENT_RETAIL_OTHER: Segment = "retail_other"
#: What the governed counterparty map calls an exposure that belongs to the
#: sovereign component instead. Exported so the caller filters on one spelling;
#: an exposure carrying it must never reach this module.
SEGMENT_EXCLUDED = "excluded"

GaStatus = Literal["computed", "not_applicable"]
STATUS_COMPUTED: GaStatus = "computed"
STATUS_NOT_APPLICABLE: GaStatus = "not_applicable"

#: The book holds too few EFFECTIVE names for a first-order correction.
REASON_BELOW_MIN_EFFECTIVE_NAMES = "ga_below_min_effective_names"
#: Nothing to measure: no exposures survived, or total EAD is zero.
REASON_NO_EXPOSURES = "ga_no_exposures"
#: ``K*`` is not positive, so ``1/2K*`` is undefined. Structurally impossible
#: for a book of real exposures; refused rather than divided by.
REASON_K_STAR_NOT_POSITIVE = "ga_k_star_not_positive"

#: Sanity trace: an add-on larger than the Pillar 1 charge it corrects. It
#: should not fire on a book that passes the gate.
WARNING_EXCEEDS_K_STAR = "ga_exceeds_kstar"

ERROR_EAD_NOT_POSITIVE = "ga_ead_not_positive"
ERROR_PD_OUT_OF_RANGE = "ga_pd_out_of_range"
ERROR_ELGD_OUT_OF_RANGE = "ga_elgd_out_of_range"
ERROR_SEGMENT_CORRELATION_MISSING = "ga_segment_correlation_missing"
ERROR_MATURITY_MISSING = "ga_maturity_missing"
ERROR_MATURITY_FACTOR_UNDEFINED = "ga_maturity_factor_undefined"
ERROR_CONFIDENCE_OUT_OF_RANGE = "ga_confidence_out_of_range"
ERROR_PARAMETER_OUT_OF_RANGE = "ga_parameter_out_of_range"
ERROR_CORRELATION_OUT_OF_RANGE = "ga_correlation_out_of_range"


class GranularityInputError(ValueError):
    """An input this formula cannot consume, named by code and reference.

    Raised rather than returned, because it is not a property of the book — it
    is a resolution the caller has not finished. The service turns it into a
    typed refusal naming the exposures; it never becomes a number.
    """

    def __init__(self, code: str, *, ref: str | None = None, detail: str | None = None):
        self.code = code
        self.ref = ref
        self.detail = detail
        parts = [code, *[part for part in (ref, detail) if part is not None]]
        super().__init__(":".join(parts))


# --- governed inputs --------------------------------------------------------

#: One segment's governed asset correlation: ``(r_min, r_max, k)``.
Correlation = tuple[Decimal, Decimal, Decimal]


@dataclass(frozen=True)
class MaturityAdjustment:
    """The governed IRB maturity adjustment, and whether it is switched on.

    Off is the honest default for a book whose maturities are not reliably
    stated: it is equivalent to every exposure sitting at the formula's centre.
    On, a missing maturity is a refusal, not a filled-in year.
    """

    apply: bool
    b_intercept: Decimal
    b_slope: Decimal
    m_centre: Decimal
    m_scale: Decimal


@dataclass(frozen=True)
class GaExposure:
    """One exposure, with its resolved PD, ELGD and segment, and the provenance.

    ``group_key`` is the connected-obligor identity — the same key the
    concentration engine groups on. Exposures sharing it are one obligor for the
    purpose of the adjustment, because that is the unit an idiosyncratic default
    lands on.

    ``pd_source`` and ``lgd_source`` say where each figure came from (the
    exposure's own attribute, the Board-approved ECL register, or a governed
    proxy). They do not enter the arithmetic; they are carried so the consumer
    can disclose how much of the book rests on a proxy.
    """

    ref: str
    group_key: str
    ead: Decimal
    pd: Decimal
    elgd: Decimal
    segment: Segment
    maturity_years: Decimal | None = None
    pd_source: str = ""
    lgd_source: str = ""


@dataclass(frozen=True)
class GaParams:
    """Every governed value the adjustment applies, resolved by the caller."""

    #: Confidence level of the conditional default rate.
    q: Decimal
    #: The gamma-factor multiplier δ.
    delta: Decimal
    #: LGD variance coefficient γ in ``V(LGD) = γ·ELGD·(1 − ELGD)``.
    gamma: Decimal
    #: Effective-name floor below which the correction is not applied.
    min_effective_names: Decimal
    #: Segment → ``(r_min, r_max, k)``.
    corr: Mapping[str, Correlation]
    ma: MaturityAdjustment


# --- output -----------------------------------------------------------------


@dataclass(frozen=True)
class GaResult:
    """The adjustment, or the stated reason there is not one.

    ``ga`` and ``addon_amount`` are ``None`` on the ``not_applicable`` path. The
    diagnostics that explain the refusal — ``n_eff``, ``n_obligors``,
    ``k_star``, ``total_ead`` — are present either way, because the operator has
    to be able to see WHY the method declined and what would change it.
    """

    status: GaStatus
    ga: Decimal | None
    k_star: Decimal | None
    n_obligors: int
    n_eff: Decimal
    total_ead: Decimal
    addon_amount: Decimal | None
    reason: str | None = None
    warnings: tuple[str, ...] = ()
    top_contributors: tuple[tuple[str, Decimal], ...] = ()

    @property
    def computed(self) -> bool:
        return self.status == STATUS_COMPUTED


# --- the float boundary -----------------------------------------------------


def _from_float(value: float) -> Decimal:
    """Re-enter decimal arithmetic from a float, at a fixed decimal place."""
    return Decimal(repr(value)).quantize(FLOAT_QUANTUM, rounding=ROUND_HALF_UP)


def normal_cdf(value: Decimal) -> Decimal:
    """Φ — the standard normal distribution function."""
    return _from_float(_NORMAL.cdf(float(value)))


def normal_inv_cdf(value: Decimal) -> Decimal:
    """Φ⁻¹ — the standard normal quantile."""
    return _from_float(_NORMAL.inv_cdf(float(value)))


# --- the pieces of the formula ---------------------------------------------


def _exp(value: Decimal) -> Decimal:
    return _from_float(math.exp(float(value)))


def asset_correlation(pd: Decimal, correlation: Correlation) -> Decimal:
    """ρ = r_min·w + r_max·(1 − w), with w the governed PD weighting."""
    r_min, r_max, k = correlation
    span = ONE - _exp(-k)
    if span <= ZERO:
        raise GranularityInputError(ERROR_CORRELATION_OUT_OF_RANGE, detail=str(k))
    weight = (ONE - _exp(-k * pd)) / span
    return r_min * weight + r_max * (ONE - weight)


def maturity_factor(pd: Decimal, maturity_years: Decimal, ma: MaturityAdjustment) -> Decimal:
    """``(1 + (M − m_centre)·b) / (1 − m_scale·b)`` with ``b`` from the PD."""
    b = (ma.b_intercept - ma.b_slope * _from_float(math.log(float(pd)))) ** TWO
    denominator = ONE - ma.m_scale * b
    if denominator == ZERO:
        raise GranularityInputError(ERROR_MATURITY_FACTOR_UNDEFINED, detail=str(b))
    return (ONE + (maturity_years - ma.m_centre) * b) / denominator


def _validate_params(p: GaParams) -> None:
    if not (ZERO < p.q < ONE):
        raise GranularityInputError(ERROR_CONFIDENCE_OUT_OF_RANGE, detail=str(p.q))
    if p.delta < ZERO:
        raise GranularityInputError(ERROR_PARAMETER_OUT_OF_RANGE, detail=str(p.delta))
    if p.gamma < ZERO:
        raise GranularityInputError(ERROR_PARAMETER_OUT_OF_RANGE, detail=str(p.gamma))
    if p.min_effective_names < ZERO:
        raise GranularityInputError(ERROR_PARAMETER_OUT_OF_RANGE, detail=str(p.min_effective_names))
    for segment, (r_min, r_max, k) in p.corr.items():
        if not (ZERO <= r_min < ONE) or not (ZERO <= r_max < ONE) or k <= ZERO:
            raise GranularityInputError(ERROR_CORRELATION_OUT_OF_RANGE, detail=segment)


def _validate_exposure(exposure: GaExposure, p: GaParams) -> None:
    if exposure.ead <= ZERO:
        raise GranularityInputError(ERROR_EAD_NOT_POSITIVE, ref=exposure.ref)
    if not (ZERO < exposure.pd < ONE):
        raise GranularityInputError(
            ERROR_PD_OUT_OF_RANGE, ref=exposure.ref, detail=str(exposure.pd)
        )
    if not (ZERO < exposure.elgd <= ONE):
        raise GranularityInputError(
            ERROR_ELGD_OUT_OF_RANGE, ref=exposure.ref, detail=str(exposure.elgd)
        )
    if exposure.segment not in p.corr:
        raise GranularityInputError(
            ERROR_SEGMENT_CORRELATION_MISSING, ref=exposure.ref, detail=exposure.segment
        )
    if p.ma.apply and exposure.maturity_years is None:
        raise GranularityInputError(ERROR_MATURITY_MISSING, ref=exposure.ref)


def exposure_k_and_r(exposure: GaExposure, p: GaParams) -> tuple[Decimal, Decimal]:
    """``(Kᵢ, Rᵢ)`` for one exposure — the IRB unexpected and expected loss."""
    expected = exposure.elgd * exposure.pd
    rho = asset_correlation(exposure.pd, p.corr[exposure.segment])
    conditional = normal_cdf(
        (normal_inv_cdf(exposure.pd) + rho.sqrt() * normal_inv_cdf(p.q)) / (ONE - rho).sqrt()
    )
    unexpected = exposure.elgd * conditional - expected
    if p.ma.apply and exposure.maturity_years is not None:
        unexpected = unexpected * maturity_factor(exposure.pd, exposure.maturity_years, p.ma)
    return unexpected, expected


@dataclass(frozen=True)
class _Obligor:
    """One connected group, as the formula sees it."""

    key: str
    ead: Decimal
    k: Decimal
    r: Decimal
    c: Decimal


def _sort_key(exposure: GaExposure) -> tuple[str, str, str, str]:
    """A canonical order, so the sums do not depend on the input order."""
    return (exposure.ref, str(exposure.ead), str(exposure.pd), str(exposure.elgd))


def _obligors(exposures: Sequence[GaExposure], p: GaParams) -> list[_Obligor]:
    """Collapse exposures onto connected groups, EAD-weighting K, R and ELGD."""
    grouped: dict[str, list[GaExposure]] = {}
    for exposure in exposures:
        _validate_exposure(exposure, p)
        grouped.setdefault(exposure.group_key, []).append(exposure)

    obligors: list[_Obligor] = []
    for key in sorted(grouped):
        members = sorted(grouped[key], key=_sort_key)
        ead = ZERO
        weighted_k = ZERO
        weighted_r = ZERO
        weighted_elgd = ZERO
        for member in members:
            unexpected, expected = exposure_k_and_r(member, p)
            ead += member.ead
            weighted_k += member.ead * unexpected
            weighted_r += member.ead * expected
            weighted_elgd += member.ead * member.elgd
        elgd = weighted_elgd / ead
        variance = p.gamma * elgd * (ONE - elgd)
        obligors.append(
            _Obligor(
                key=key,
                ead=ead,
                k=weighted_k / ead,
                r=weighted_r / ead,
                c=(elgd**TWO + variance) / elgd,
            )
        )
    return obligors


def granularity_adjustment(exposures: Sequence[GaExposure], p: GaParams) -> GaResult:
    """The full Gordy-Lütkebohmert adjustment, as a fraction of total EAD.

    Returns ``status="not_applicable"`` — with no figure — when the book cannot
    carry the first-order correction; raises :class:`GranularityInputError` when
    an input is not one the formula can consume. It never approximates its way
    past either.
    """
    with localcontext() as ctx:
        ctx.prec = WORKING_PRECISION
        ctx.rounding = ROUND_HALF_UP
        _validate_params(p)
        obligors = _obligors(exposures, p)

        total_ead = sum((obligor.ead for obligor in obligors), ZERO)
        if not obligors or total_ead <= ZERO:
            return GaResult(
                status=STATUS_NOT_APPLICABLE,
                ga=None,
                k_star=None,
                n_obligors=len(obligors),
                n_eff=ZERO,
                total_ead=_quantize(total_ead, AMOUNT_QUANTUM),
                addon_amount=None,
                reason=REASON_NO_EXPOSURES,
            )

        shares = [obligor.ead / total_ead for obligor in obligors]
        herfindahl = sum((share**TWO for share in shares), ZERO)
        n_eff = _quantize(ONE / herfindahl, COUNT_QUANTUM)
        k_star = sum(
            (share * obligor.k for share, obligor in zip(shares, obligors, strict=True)), ZERO
        )

        if k_star <= ZERO:
            return GaResult(
                status=STATUS_NOT_APPLICABLE,
                ga=None,
                k_star=_quantize(k_star, RATIO_QUANTUM),
                n_obligors=len(obligors),
                n_eff=n_eff,
                total_ead=_quantize(total_ead, AMOUNT_QUANTUM),
                addon_amount=None,
                reason=REASON_K_STAR_NOT_POSITIVE,
            )

        if n_eff < p.min_effective_names:
            return GaResult(
                status=STATUS_NOT_APPLICABLE,
                ga=None,
                k_star=_quantize(k_star, RATIO_QUANTUM),
                n_obligors=len(obligors),
                n_eff=n_eff,
                total_ead=_quantize(total_ead, AMOUNT_QUANTUM),
                addon_amount=None,
                reason=REASON_BELOW_MIN_EFFECTIVE_NAMES,
            )

        divisor = TWO * k_star
        contributions = [
            (
                obligor.key,
                share**TWO * obligor.c * (p.delta * (obligor.k + obligor.r) - obligor.k) / divisor,
            )
            for share, obligor in zip(shares, obligors, strict=True)
        ]
        ga = sum((value for _key, value in contributions), ZERO)

        reported_ga = _quantize(ga, RATIO_QUANTUM)
        reported_k_star = _quantize(k_star, RATIO_QUANTUM)
        reported_total = _quantize(total_ead, AMOUNT_QUANTUM)
        warnings = (WARNING_EXCEEDS_K_STAR,) if reported_ga > reported_k_star else ()
        ranked = sorted(contributions, key=lambda item: (-item[1], item[0]))[:TOP_CONTRIBUTORS]

        return GaResult(
            status=STATUS_COMPUTED,
            ga=reported_ga,
            k_star=reported_k_star,
            n_obligors=len(obligors),
            n_eff=n_eff,
            total_ead=reported_total,
            # From the REPORTED figures, so the amount reproduces from what the
            # result prints rather than from a precision the reader cannot see.
            addon_amount=_quantize(reported_ga * reported_total, AMOUNT_QUANTUM),
            reason=None,
            warnings=warnings,
            top_contributors=tuple((key, _quantize(value, RATIO_QUANTUM)) for key, value in ranked),
        )


def _quantize(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


__all__ = [
    "AMOUNT_QUANTUM",
    "COUNT_QUANTUM",
    "ERROR_CONFIDENCE_OUT_OF_RANGE",
    "ERROR_CORRELATION_OUT_OF_RANGE",
    "ERROR_EAD_NOT_POSITIVE",
    "ERROR_ELGD_OUT_OF_RANGE",
    "ERROR_MATURITY_FACTOR_UNDEFINED",
    "ERROR_MATURITY_MISSING",
    "ERROR_PARAMETER_OUT_OF_RANGE",
    "ERROR_PD_OUT_OF_RANGE",
    "ERROR_SEGMENT_CORRELATION_MISSING",
    "FLOAT_QUANTUM",
    "RATIO_QUANTUM",
    "REASON_BELOW_MIN_EFFECTIVE_NAMES",
    "REASON_K_STAR_NOT_POSITIVE",
    "REASON_NO_EXPOSURES",
    "SEGMENT_CORPORATE",
    "SEGMENT_EXCLUDED",
    "SEGMENT_RETAIL_OTHER",
    "STATUS_COMPUTED",
    "STATUS_NOT_APPLICABLE",
    "TOP_CONTRIBUTORS",
    "WARNING_EXCEEDS_K_STAR",
    "WORKING_PRECISION",
    "Correlation",
    "GaExposure",
    "GaParams",
    "GaResult",
    "GaStatus",
    "GranularityInputError",
    "MaturityAdjustment",
    "Segment",
    "asset_correlation",
    "exposure_k_and_r",
    "granularity_adjustment",
    "maturity_factor",
    "normal_cdf",
    "normal_inv_cdf",
]
