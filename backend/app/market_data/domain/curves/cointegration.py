"""Engle-Granger cointegration for the synthetic discounting level (spec §6.2).

Ghana has no OIS market, so the discounting level beyond the meeting-date step
short end is inferred from a cointegrating relationship between the compounded
overnight rate and a liquid term benchmark — the Jakarasi, Labuschagne &
Mahomed (2015) approach as fused by Van Heeswijk (2017), who found simple OLS
cointegration outperforms a VECM in ZAR.

Two-step Engle-Granger, fully self-contained (no statsmodels):

1. OLS of ``y`` on ``x`` with a constant: ``y_t = alpha + beta x_t + e_t``.
2. ADF unit-root test on the residuals — regression with a constant,
   ``de_t = c + rho e_{t-1} + sum phi_j de_{t-j} + u_t`` — lag order fixed or
   AIC-selected over 0..maxlag (default cap 8).

Critical values are hard-coded from MacKinnon, J.G. (2010), "Critical Values
for Cointegration Tests", Queen's Economics Department Working Paper No. 1227
— asymptotic values for the residual-based cointegration case with N=2
variables and a constant in the cointegrating regression (approximately
1%: -3.90, 5%: -3.34, 10%: -3.04). The beta coefficients and the resulting
level are the platform's single most methodology-sensitive numbers: they are
re-estimated only as a governed Track-2 event (spec §5).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

import numpy as np
from numpy.typing import NDArray

__all__ = [
    "ADF_CRITICAL_VALUES",
    "CointegrationError",
    "CointegrationResult",
    "engle_granger",
    "synthetic_ois_level",
]

# MacKinnon (2010), QED WP 1227: residual-based cointegration test, N=2,
# constant included (the "c" case), asymptotic approximations.
ADF_CRITICAL_VALUES: MappingProxyType[str, float] = MappingProxyType(
    {"1%": -3.90, "5%": -3.34, "10%": -3.04}
)

_DEFAULT_MAXLAG = 8


class CointegrationError(ValueError):
    """The series are unusable (too short, mismatched, degenerate)."""


@dataclass(frozen=True)
class CointegrationResult:
    """Engle-Granger two-step output.

    ``alpha``/``beta`` are the cointegrating OLS coefficients of
    ``y = alpha + beta x``; ``residuals`` the fitted spread; ``adf_stat`` the
    residual ADF t-statistic (constant included, ``adf_lags`` augmenting lags
    over ``adf_nobs`` usable observations).
    """

    alpha: float
    beta: float
    residuals: tuple[float, ...]
    adf_stat: float
    adf_lags: int
    adf_nobs: int
    critical_values: MappingProxyType[str, float] = ADF_CRITICAL_VALUES

    def is_cointegrated(self, at: str = "5%") -> bool:
        """Reject the no-cointegration null at the given significance level."""
        if at not in self.critical_values:
            raise CointegrationError(
                f"Unknown significance level '{at}'; use one of {tuple(self.critical_values)}."
            )
        return self.adf_stat < self.critical_values[at]


def _ols(
    response: NDArray[np.float64], design: NDArray[np.float64]
) -> tuple[NDArray[np.float64], NDArray[np.float64], float]:
    """OLS via least squares: coefficients, residuals, and sigma^2 (SSR / dof)."""
    coefficients, _, rank, _ = np.linalg.lstsq(design, response, rcond=None)
    if rank < design.shape[1]:
        raise CointegrationError("Design matrix is rank-deficient (constant series?).")
    residuals = response - design @ coefficients
    dof = design.shape[0] - design.shape[1]
    if dof <= 0:
        raise CointegrationError("Not enough observations for the regression.")
    sigma_squared = float(residuals @ residuals) / dof
    return coefficients, residuals, sigma_squared


def _adf_design(
    series: NDArray[np.float64], lags: int, offset: int
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Build the ADF regression sample: response d e_t; regressors
    [constant, e_{t-1}, d e_{t-1}, ..., d e_{t-lags}], skipping the first
    ``offset`` differenced observations so every lag candidate uses the SAME
    sample (required for AIC comparability)."""
    differences = np.diff(series)
    start = offset  # index into `differences`
    response = differences[start:]
    n = response.shape[0]
    columns: list[NDArray[np.float64]] = [
        np.ones(n, dtype=np.float64),
        series[start:-1],
    ]
    columns.extend(differences[start - j : -j] for j in range(1, lags + 1))
    design = np.column_stack(columns)
    return response, design


def _adf_tstat(series: NDArray[np.float64], lags: int, offset: int) -> tuple[float, float, int]:
    """ADF t-statistic on rho, plus AIC and sample size, at a fixed lag order."""
    response, design = _adf_design(series, lags, offset)
    n = response.shape[0]
    if n <= design.shape[1]:
        raise CointegrationError("Series too short for the requested ADF lag order.")
    coefficients, residuals, sigma_squared = _ols(response, design)
    xtx_inverse = np.linalg.inv(design.T @ design)
    standard_error = math.sqrt(sigma_squared * float(xtx_inverse[1, 1]))
    if standard_error == 0.0:
        raise CointegrationError("Degenerate ADF regression (zero-variance residual).")
    t_stat = float(coefficients[1]) / standard_error
    ssr = float(residuals @ residuals)
    parameters = design.shape[1]
    aic = n * math.log(max(ssr, 1e-300) / n) + 2.0 * parameters
    return t_stat, aic, n


def _adf_on_residuals(
    residuals: NDArray[np.float64],
    maxlag: int,
    lag_selection: Literal["fixed", "aic"],
) -> tuple[float, int, int]:
    """Residual ADF: the statistic, the chosen lag order and the sample size.

    With ``lag_selection="aic"`` every candidate lag 0..maxlag is evaluated on
    the common sample (holding out ``maxlag`` observations) and the AIC
    winner's statistic is returned; ``"fixed"`` uses exactly ``maxlag`` lags.
    """
    if lag_selection == "fixed":
        t_stat, _, n = _adf_tstat(residuals, maxlag, offset=maxlag)
        return t_stat, maxlag, n
    best_lag = 0
    best_aic = math.inf
    for lags in range(maxlag + 1):
        _, aic, _ = _adf_tstat(residuals, lags, offset=maxlag)
        if aic < best_aic:
            best_aic = aic
            best_lag = lags
    t_stat, _, n = _adf_tstat(residuals, best_lag, offset=maxlag)
    return t_stat, best_lag, n


def engle_granger(
    y: Sequence[float] | NDArray[np.float64],
    x: Sequence[float] | NDArray[np.float64],
    maxlag: int = _DEFAULT_MAXLAG,
    lag_selection: Literal["fixed", "aic"] = "aic",
) -> CointegrationResult:
    """Two-step Engle-Granger: OLS level regression, then residual ADF."""
    y_array = np.asarray(y, dtype=np.float64)
    x_array = np.asarray(x, dtype=np.float64)
    if y_array.ndim != 1 or y_array.shape != x_array.shape:
        raise CointegrationError("y and x must be one-dimensional and equally long.")
    if not (np.all(np.isfinite(y_array)) and np.all(np.isfinite(x_array))):
        raise CointegrationError("y and x must be finite.")
    if maxlag < 0 or maxlag > _DEFAULT_MAXLAG:
        raise CointegrationError(f"maxlag must be between 0 and {_DEFAULT_MAXLAG}.")
    minimum = maxlag + 12  # enough observations left after lagging to say anything
    if y_array.size < minimum:
        raise CointegrationError(f"Need at least {minimum} observations, got {y_array.size}.")
    design = np.column_stack([np.ones(x_array.shape[0], dtype=np.float64), x_array])
    coefficients, residuals, _ = _ols(y_array, design)
    adf_stat, adf_lags, adf_nobs = _adf_on_residuals(residuals, maxlag, lag_selection)
    return CointegrationResult(
        alpha=float(coefficients[0]),
        beta=float(coefficients[1]),
        residuals=tuple(float(value) for value in residuals),
        adf_stat=adf_stat,
        adf_lags=adf_lags,
        adf_nobs=adf_nobs,
    )


def synthetic_ois_level(benchmark_rate: float, alpha: float, beta: float) -> float:
    """Implied overnight-linked (discounting) level from a liquid benchmark.

    The cointegrating regression is ``overnight_leg = alpha + beta * benchmark``,
    so given today's benchmark rate the implied compounded-overnight-consistent
    level is simply ``alpha + beta * benchmark_rate``. Alpha and beta come from
    a governed parameter set, never re-fit on the fly (spec §5, Track 2).
    """
    return alpha + beta * benchmark_rate
