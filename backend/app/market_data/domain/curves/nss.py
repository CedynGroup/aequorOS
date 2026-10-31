"""Nelson-Siegel-Svensson parametric zero curve (spec §5 step 4 — the fallback).

The NSS fallback fires when the day's liquid points fall below the methodology
threshold: 4-6 parameters fit the whole curve robustly from sparse data. The
fit is a bounded ``scipy.optimize.least_squares`` with a *deterministic*
multi-start grid — no random restarts, ever (platform-wide determinism
requirement: same inputs must give the same published curve, bit for bit).

Parameter recovery is famously degenerate (tau_1/tau_2 can swap roles, betas
can trade off), so callers — and the tests — must judge a fit by **curve
values**, never parameter identity.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import least_squares

__all__ = ["NssBounds", "NssFit", "NssParameters", "fit_nss", "nss_zero"]

# Below this tau-scaled time the loading functions switch to their series
# expansion; the t -> 0 limits are (1 - e^-x)/x -> 1 and the curvature
# loading -> 0.
_SMALL = 1e-10


@dataclass(frozen=True)
class NssParameters:
    """The six Svensson parameters (rates as decimals; taus in years)."""

    beta0: float
    beta1: float
    beta2: float
    beta3: float
    tau1: float
    tau2: float

    def as_tuple(self) -> tuple[float, float, float, float, float, float]:
        return (self.beta0, self.beta1, self.beta2, self.beta3, self.tau1, self.tau2)


@dataclass(frozen=True)
class NssBounds:
    """Box bounds for the fit. Defaults: level in [0, 1] (0-100%), slope and
    curvature terms in [-1, 1], both taus in [0.05, 30] years (a tau below
    ~18 days is numerically indistinguishable from a spike at zero)."""

    lower: tuple[float, float, float, float, float, float] = (0.0, -1.0, -1.0, -1.0, 0.05, 0.05)
    upper: tuple[float, float, float, float, float, float] = (1.0, 1.0, 1.0, 1.0, 30.0, 30.0)


@dataclass(frozen=True)
class NssFit:
    """A completed fit: parameters, objective diagnostics and the start that won."""

    parameters: NssParameters
    residual_norm: float
    max_abs_residual: float
    n_points: int
    start_index: int


def _slope_loading(scaled: NDArray[np.float64]) -> NDArray[np.float64]:
    """(1 - e^-x)/x with the x -> 0 limit of 1."""
    safe = np.maximum(scaled, _SMALL)
    return np.where(scaled < _SMALL, 1.0 - scaled / 2.0, -np.expm1(-scaled) / safe)


def _curvature_loading(scaled: NDArray[np.float64]) -> NDArray[np.float64]:
    """(1 - e^-x)/x - e^-x with the x -> 0 limit of 0."""
    return _slope_loading(scaled) - np.exp(-scaled)


def nss_zero(  # noqa: PLR0913 - the six Svensson parameters are the signature
    t: float | Sequence[float] | NDArray[np.float64],
    beta0: float,
    beta1: float,
    beta2: float,
    beta3: float,
    tau1: float,
    tau2: float,
) -> float | NDArray[np.float64]:
    """Svensson zero rate at time(s) ``t`` (years), with the standard t -> 0 limits.

    ``z(t) = b0 + b1 (1-e^-x1)/x1 + b2 [(1-e^-x1)/x1 - e^-x1]
           + b3 [(1-e^-x2)/x2 - e^-x2]`` with ``x_i = t / tau_i``;
    ``z(0) = b0 + b1``.
    """
    if tau1 <= 0.0 or tau2 <= 0.0:
        raise ValueError("NSS taus must be strictly positive.")
    times = np.asarray(t, dtype=np.float64)
    if np.any(times < 0.0):
        raise ValueError("NSS times must be non-negative.")
    values = (
        beta0
        + beta1 * _slope_loading(times / tau1)
        + beta2 * _curvature_loading(times / tau1)
        + beta3 * _curvature_loading(times / tau2)
    )
    if np.isscalar(t):
        return float(values)
    return values


def _default_starts(
    times: NDArray[np.float64], zeros: NDArray[np.float64], bounds: NssBounds
) -> tuple[NssParameters, ...]:
    """Deterministic multi-start grid: level from the long end, slope from the
    short end, curvature zeroed, tau pairs spanning short/medium/long humps."""
    lower = np.asarray(bounds.lower)
    upper = np.asarray(bounds.upper)
    level = float(np.clip(zeros[-1], lower[0], upper[0]))
    slope = float(np.clip(zeros[0] - zeros[-1], lower[1], upper[1]))
    tau_pairs = ((0.5, 3.0), (1.0, 5.0), (2.0, 10.0), (5.0, 15.0))
    starts: list[NssParameters] = []
    for tau1, tau2 in tau_pairs:
        starts.append(
            NssParameters(
                beta0=level,
                beta1=slope,
                beta2=0.0,
                beta3=0.0,
                tau1=float(np.clip(tau1, lower[4], upper[4])),
                tau2=float(np.clip(tau2, lower[5], upper[5])),
            )
        )
    return tuple(starts)


def fit_nss(
    times: Sequence[float] | NDArray[np.float64],
    zeros: Sequence[float] | NDArray[np.float64],
    bounds: NssBounds | None = None,
    x0: NssParameters | Sequence[NssParameters] | None = None,
) -> NssFit:
    """Fit NSS to observed zeros by bounded least squares with deterministic multi-start.

    ``x0`` overrides the default start grid (one start or several). The best
    start wins by residual cost; ties break on the earliest start index, so the
    result is a pure function of the inputs.
    """
    time_array = np.asarray(times, dtype=np.float64)
    zero_array = np.asarray(zeros, dtype=np.float64)
    if time_array.ndim != 1 or time_array.shape != zero_array.shape:
        raise ValueError("times and zeros must be one-dimensional and equally long.")
    if time_array.size < 6:
        raise ValueError("NSS has six parameters; at least six observations are required.")
    if np.any(time_array < 0.0):
        raise ValueError("NSS times must be non-negative.")
    box = bounds or NssBounds()
    if x0 is None:
        starts = _default_starts(time_array, zero_array, box)
    elif isinstance(x0, NssParameters):
        starts = (x0,)
    else:
        starts = tuple(x0)
        if not starts:
            raise ValueError("x0 must contain at least one starting point.")

    def residuals(params: NDArray[np.float64]) -> NDArray[np.float64]:
        values = nss_zero(
            time_array, params[0], params[1], params[2], params[3], params[4], params[5]
        )
        return np.asarray(values, dtype=np.float64) - zero_array

    best: NssFit | None = None
    best_cost = math.inf
    for index, start in enumerate(starts):
        clipped = np.clip(np.asarray(start.as_tuple()), box.lower, box.upper)
        result = least_squares(
            residuals,
            clipped,
            bounds=(box.lower, box.upper),
            method="trf",
            xtol=1e-15,
            ftol=1e-15,
            gtol=1e-15,
            max_nfev=2000,
        )
        cost = float(result.cost)
        if cost < best_cost:
            best_cost = cost
            fitted = np.asarray(result.x, dtype=np.float64)
            fit_residuals = residuals(fitted)
            best = NssFit(
                parameters=NssParameters(
                    beta0=float(fitted[0]),
                    beta1=float(fitted[1]),
                    beta2=float(fitted[2]),
                    beta3=float(fitted[3]),
                    tau1=float(fitted[4]),
                    tau2=float(fitted[5]),
                ),
                residual_norm=float(np.sqrt(np.sum(fit_residuals**2))),
                max_abs_residual=float(np.max(np.abs(fit_residuals))),
                n_points=int(time_array.size),
                start_index=index,
            )
    assert best is not None  # at least one start always runs
    return best
