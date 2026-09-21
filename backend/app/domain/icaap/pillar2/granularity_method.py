"""Pillar 2 method ``granularity_adjustment`` — the FULL adjustment (P5).

This adapter is the only thing that may be called a granularity adjustment
(D-016). It wraps the pure Gordy-Lütkebohmert derivation in
``app/domain/credit/granularity.py`` in the Pillar 2 method protocol, and holds
no arithmetic of its own: it parses the governed console bodies into
:class:`~app.domain.credit.granularity.GaParams`, calls the pure function, and
translates the answer.

**How a reader tells this apart from the simplified charge.** The sibling module
``concentration.py`` offers ``hhi_proportional_heuristic`` — a governed
coefficient × HHI, which carries no obligor PD, no LGD, no asset correlation and
no confidence level. It already stamps ``detail["label"]`` with a sentence
saying it is *not* a granularity adjustment. This method stamps the mirror
image: ``detail["method_variant"] = "gordy_luetkebohmert_full"`` and
``detail["label"] = FULL_LABEL`` on every result, including the refusals. So on
any surface, in any export and in any stored computation, exactly one of the two
strings is present and they never both are. The numbers differ in kind, not
degree — the heuristic scales with a measured index, this one with each
obligor's own conditional loss — and the two must never be read as versions of
one figure.

**When the full one runs.** Only when the book supports it: enough EFFECTIVE
names (``ga_min_effective_names``, a governed REPRESENTATIVE row), a resolved PD
and ELGD for every exposure, and a governed correlation for every segment. When
it does not, the result is ``not_computable`` carrying the machine reason —
never a quietly degraded figure — and P2's aggregation falls back to the
benchmark and judgemental methods, which say what they are.

Every value the method applies is a console row (D-024); an absent one is
:class:`~app.domain.icaap.pillar2.types.MissingParameter` naming the code. The
GA rows are seeded as REPRESENTATIVE and pending confirmation — platform
methodology, not a published regulator calibration — and the service attaches
that provenance to ``parameters_used``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.credit.granularity import (
    TOP_CONTRIBUTORS,
    Correlation,
    GaExposure,
    GaParams,
    GaResult,
    MaturityAdjustment,
    granularity_adjustment,
)
from app.domain.icaap.pillar2.types import (
    MethodResult,
    MethodStatus,
    MissingParameter,
    ParameterUse,
    detail_of,
    text,
)
from app.domain.icaap.units import Basis, amount

METHOD = "granularity_adjustment"
METHOD_VERSION = "v1"

#: The machine name of the derivation, on every result this module returns.
METHOD_VARIANT = "gordy_luetkebohmert_full"
#: The sentence every surface prints with the figure — the counterpart to
#: ``concentration.HEURISTIC_LABEL``, so the two can never be confused.
FULL_LABEL = (
    "Granularity adjustment (Gordy-Lütkebohmert, full; obligor PD/LGD, "
    "governed correlation and confidence level)"
)
#: Why the figure is still calibrated by the platform rather than a regulator.
CALIBRATION_NOTE = (
    "Confidence level, delta, LGD variance coefficient, asset correlations and the "
    "effective-name floor are governed console rows, seeded REPRESENTATIVE and "
    "pending confirmation."
)

PARAM_CONFIDENCE_Q = "ga_confidence_q"
PARAM_DELTA = "ga_delta"
PARAM_LGD_VARIANCE_GAMMA = "ga_lgd_variance_gamma"
PARAM_MIN_EFFECTIVE_NAMES = "ga_min_effective_names"
PARAM_ASSET_CORRELATION = "ga_asset_correlation"
PARAM_MATURITY_ADJUSTMENT = "ga_maturity_adjustment"

#: Codes the caller resolves for the BOOK rather than for the formula. Declared
#: here so the service and the readiness check name them the same way.
PARAM_DEFAULT_ELGD_PCT = "ga_default_elgd_pct"
PARAM_PROXY_PD_BY_RW_CODE = "ga_proxy_pd_by_rw_code"
PARAM_COUNTERPARTY_SEGMENT_MAP = "ga_counterparty_segment_map"

#: Every governed table body names the shape it was validated against. It is
#: structural metadata, not an entry, so a parser reading the table skips it.
KEY_SCHEMA = "schema"
KEY_R_MIN = "r_min"
KEY_R_MAX = "r_max"
KEY_K = "k"
KEY_APPLY = "apply"
KEY_B_INTERCEPT = "b_intercept"
KEY_B_SLOPE = "b_slope"
KEY_M_CENTRE = "m_centre"
KEY_M_SCALE = "m_scale"

#: The stressed side needs a stressed BOOK, not a stressed denominator — the
#: add-on is an absolute amount. Absent, it is declared not assessed.
REASON_STRESSED_BOOK_NOT_ASSESSED = "stressed_book_not_assessed"

_TRUE = "true"
_FALSE = "false"
_FIELD = ":"
_ROW = "|"


def _decimal(value: Any, code: str) -> Decimal:
    """A governed scalar as a Decimal, or the typed refusal naming its code."""
    if value is None or isinstance(value, bool):
        raise MissingParameter(code)
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise MissingParameter(code, detail="malformed") from error


def _flag(value: Any, code: str) -> bool:
    if isinstance(value, bool):
        return value
    text_value = str(value).strip().lower()
    if text_value == _TRUE:
        return True
    if text_value == _FALSE:
        return False
    raise MissingParameter(code, detail=KEY_APPLY)


def parse_asset_correlations(payload: Mapping[str, Any] | None) -> dict[str, Correlation]:
    """Read ``{segment: {r_min, r_max, k}}`` from the governed body."""
    if not isinstance(payload, Mapping) or not payload:
        raise MissingParameter(PARAM_ASSET_CORRELATION)
    correlations: dict[str, Correlation] = {}
    for segment, body in payload.items():
        if segment == KEY_SCHEMA:
            continue
        if not isinstance(body, Mapping):
            raise MissingParameter(PARAM_ASSET_CORRELATION, detail=str(segment))
        correlations[str(segment)] = (
            _segment_value(body, KEY_R_MIN, segment),
            _segment_value(body, KEY_R_MAX, segment),
            _segment_value(body, KEY_K, segment),
        )
    if not correlations:
        raise MissingParameter(PARAM_ASSET_CORRELATION)
    return correlations


def _segment_value(body: Mapping[str, Any], key: str, segment: object) -> Decimal:
    if key not in body:
        raise MissingParameter(PARAM_ASSET_CORRELATION, detail=f"{segment}{_FIELD}{key}")
    try:
        return Decimal(str(body[key]))
    except InvalidOperation as error:
        raise MissingParameter(PARAM_ASSET_CORRELATION, detail=f"{segment}{_FIELD}{key}") from error


def parse_maturity_adjustment(payload: Mapping[str, Any] | None) -> MaturityAdjustment:
    """Read the governed maturity-adjustment body, switch included."""
    if not isinstance(payload, Mapping) or not payload:
        raise MissingParameter(PARAM_MATURITY_ADJUSTMENT)
    code = PARAM_MATURITY_ADJUSTMENT
    return MaturityAdjustment(
        apply=_flag(payload.get(KEY_APPLY), code),
        b_intercept=_decimal(payload.get(KEY_B_INTERCEPT), code),
        b_slope=_decimal(payload.get(KEY_B_SLOPE), code),
        m_centre=_decimal(payload.get(KEY_M_CENTRE), code),
        m_scale=_decimal(payload.get(KEY_M_SCALE), code),
    )


def parse_params(  # noqa: PLR0913 - one argument per governed console row (D-024)
    *,
    confidence_q: Any,
    delta: Any,
    lgd_variance_gamma: Any,
    min_effective_names: Any,
    asset_correlation: Mapping[str, Any] | None,
    maturity_adjustment: Mapping[str, Any] | None,
) -> GaParams:
    """Build the formula's parameter bundle from six governed console rows."""
    return GaParams(
        q=_decimal(confidence_q, PARAM_CONFIDENCE_Q),
        delta=_decimal(delta, PARAM_DELTA),
        gamma=_decimal(lgd_variance_gamma, PARAM_LGD_VARIANCE_GAMMA),
        min_effective_names=_decimal(min_effective_names, PARAM_MIN_EFFECTIVE_NAMES),
        corr=parse_asset_correlations(asset_correlation),
        ma=parse_maturity_adjustment(maturity_adjustment),
    )


PARAMETER_USES: tuple[ParameterUse, ...] = (
    ParameterUse(PARAM_CONFIDENCE_Q, "conditional default rate confidence"),
    ParameterUse(PARAM_DELTA, "gamma-factor multiplier"),
    ParameterUse(PARAM_LGD_VARIANCE_GAMMA, "lgd variance coefficient"),
    ParameterUse(PARAM_MIN_EFFECTIVE_NAMES, "effective-name gate"),
    ParameterUse(PARAM_ASSET_CORRELATION, "segment asset correlation"),
    ParameterUse(PARAM_MATURITY_ADJUSTMENT, "maturity adjustment"),
)


def inputs_digest(exposures: Sequence[GaExposure], p: GaParams) -> str:
    """A value-based digest of the exact book and calibration that were used.

    Canonical: the exposures sorted by their own values, then every governed
    figure. Order-invariant and id-free, like the regulatory ``input_hash`` —
    so a rerun on the same book and the same console rows digests identically,
    and any change to either is visible.
    """
    rows = sorted(
        _FIELD.join(
            (
                exposure.ref,
                exposure.group_key,
                str(exposure.ead),
                str(exposure.pd),
                str(exposure.elgd),
                exposure.segment,
                str(exposure.maturity_years),
                exposure.pd_source,
                exposure.lgd_source,
            )
        )
        for exposure in exposures
    )
    ma = p.ma
    calibration = [
        str(p.q),
        str(p.delta),
        str(p.gamma),
        str(p.min_effective_names),
        str(ma.apply).lower(),
        str(ma.b_intercept),
        str(ma.b_slope),
        str(ma.m_centre),
        str(ma.m_scale),
    ]
    calibration.extend(
        _FIELD.join((segment, str(r_min), str(r_max), str(k)))
        for segment, (r_min, r_max, k) in sorted(p.corr.items())
    )
    canonical = _ROW.join((*rows, *calibration))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _tally(values: Sequence[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _diagnostics(
    result: GaResult,
    exposures: Sequence[GaExposure],
    p: GaParams,
    excluded: Mapping[str, int] | None,
) -> dict[str, str | None]:
    detail: dict[str, str | None] = {
        "label": FULL_LABEL,
        "method_variant": METHOD_VARIANT,
        "calibration": CALIBRATION_NOTE,
        "ga_status": result.status,
        "n_obligors": str(result.n_obligors),
        "n_exposures": str(len(exposures)),
        "n_eff": text(result.n_eff),
        "min_effective_names": text(p.min_effective_names),
        "total_ead": text(result.total_ead),
        "ga": text(result.ga),
        "k_star": text(result.k_star),
        "addon_amount": text(result.addon_amount),
        "confidence_q": text(p.q),
        "delta": text(p.delta),
        "lgd_variance_gamma": text(p.gamma),
        "maturity_adjustment_applied": str(p.ma.apply).lower(),
        "inputs_digest": inputs_digest(exposures, p),
    }
    for segment, (r_min, r_max, k) in sorted(p.corr.items()):
        detail[f"correlation{_FIELD}{segment}"] = _FIELD.join((str(r_min), str(r_max), str(k)))
    for source, count in sorted(_tally([e.pd_source for e in exposures]).items()):
        detail[f"pd_source{_FIELD}{source}"] = str(count)
    for source, count in sorted(_tally([e.lgd_source for e in exposures]).items()):
        detail[f"lgd_source{_FIELD}{source}"] = str(count)
    for reason, count in sorted((excluded or {}).items()):
        detail[f"excluded{_FIELD}{reason}"] = str(count)
    for rank, (key, contribution) in enumerate(result.top_contributors, start=1):
        detail[f"contributor{_FIELD}{rank}{_FIELD}{key}"] = text(contribution)
    detail["top_contributors_shown"] = str(min(len(result.top_contributors), TOP_CONTRIBUTORS))
    return detail


def granularity_adjustment_method(  # noqa: PLR0913 - the book, its refusals and its provenance
    *,
    exposures: Sequence[GaExposure],
    params: GaParams,
    excluded: Mapping[str, int] | None = None,
    stressed: GaResult | None = None,
    book_reasons: Sequence[str] = (),
    book_parameters: Sequence[ParameterUse] = (),
) -> MethodResult:
    """Run the full adjustment and answer in the Pillar 2 method protocol.

    ``excluded`` carries the caller's disclosed exclusion counts (IFRS 9 stage
    3, zero-risk-weighted and sovereign-segment rows, unconverted rows,
    non-positive EAD) so the diagnostics state what the book does NOT contain.

    ``book_reasons`` is how the caller says it could not assemble a COMPLETE
    book — an obligor with no resolvable PD, a counterparty type the governed
    map does not carry. The method then measures nothing at all: a partial book
    produces a smaller figure that looks exactly like a correct one, so the
    exposures are dropped rather than summed and the reasons are carried
    through. ``book_parameters`` are the governed codes that assembly rested on,
    which the service turns into provenance beside the formula's own.

    ``stressed`` is a second run of the same method on a stressed book. Without
    one the stressed side is declared not assessed — the add-on is an absolute
    amount, so it cannot be re-expressed on stressed denominators the way a
    percentage-of-RWA charge can.
    """
    measured: Sequence[GaExposure] = () if book_reasons else exposures
    result = granularity_adjustment(measured, params)
    detail = _diagnostics(result, measured, params, excluded)
    parameters_used = (*PARAMETER_USES, *book_parameters)
    reasons: list[str] = list(book_reasons)

    if not result.computed or result.addon_amount is None:
        if not book_reasons and result.reason is not None:
            reasons.append(result.reason)
        return MethodResult(
            method=METHOD,
            method_version=METHOD_VERSION,
            status=MethodStatus.NOT_COMPUTABLE,
            baseline_derivation="not_applicable",
            stressed_derivation="not_applicable",
            detail=detail_of(detail),
            reasons=tuple(reasons),
            parameters_used=parameters_used,
        )

    reasons.extend(result.warnings)
    baseline = amount(result.addon_amount)
    if stressed is None or stressed.addon_amount is None:
        stressed_amount = None
        stressed_derivation = "not_assessed"
        reasons.append(REASON_STRESSED_BOOK_NOT_ASSESSED)
    else:
        stressed_amount = amount(stressed.addon_amount)
        stressed_derivation = "method"
        detail["stressed_ga"] = text(stressed.ga)
        detail["stressed_total_ead"] = text(stressed.total_ead)

    return MethodResult(
        method=METHOD,
        method_version=METHOD_VERSION,
        status=MethodStatus.COMPUTED,
        basis=Basis.ABSOLUTE,
        basis_value=baseline,
        baseline_amount=baseline,
        stressed_amount=stressed_amount,
        baseline_derivation="method",
        stressed_derivation=stressed_derivation,
        detail=detail_of(detail),
        reasons=tuple(reasons),
        parameters_used=parameters_used,
    )


__all__ = [
    "CALIBRATION_NOTE",
    "FULL_LABEL",
    "KEY_SCHEMA",
    "METHOD",
    "METHOD_VARIANT",
    "METHOD_VERSION",
    "PARAMETER_USES",
    "PARAM_ASSET_CORRELATION",
    "PARAM_CONFIDENCE_Q",
    "PARAM_COUNTERPARTY_SEGMENT_MAP",
    "PARAM_DEFAULT_ELGD_PCT",
    "PARAM_DELTA",
    "PARAM_LGD_VARIANCE_GAMMA",
    "PARAM_MATURITY_ADJUSTMENT",
    "PARAM_MIN_EFFECTIVE_NAMES",
    "PARAM_PROXY_PD_BY_RW_CODE",
    "REASON_STRESSED_BOOK_NOT_ASSESSED",
    "granularity_adjustment_method",
    "inputs_digest",
    "parse_asset_correlations",
    "parse_maturity_adjustment",
    "parse_params",
]
