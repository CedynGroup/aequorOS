#!/usr/bin/env python
"""Ghana desk calibration — the Track-2 evidence artifact behind AEQ-GHS-CURVES v1.

Reproduces, deterministically and offline, the first real Ghana calibration
run on the harvested market-desk fixtures
(``tests/fixtures/market_desk/series/`` — real values captured off the wire
2026-08-09, see the fixture README). This is the research record the
methodology register points at: every recommended v1 parameter below is
justified by a number this script prints.

    uv run python scripts/desk_calibration_ghana.py

What it establishes (and what v1 therefore encodes):

1. **Cointegration is a diagnostic, not a level-setter.** Engle-Granger of
   the 91-day T-bill true yield on the daily interbank rate (auction-date
   aligned) gives a full-sample beta of ~0.9458 — inside the ZAR 0.93-0.97
   literature range (Jakarasi 2015 / Van Heeswijk 2017) — but the
   no-cointegration null survives the 5% bar in EVERY regime window (best
   ADF ~-3.08 in the 2025+ window vs -3.34 at 5%; only marginal against
   -3.04 at 10% asymptotic), with residual std ~4pp. A level inferred from
   this relationship would be noise dressed as methodology.
2. **The overnight spread window IS the level.** The interbank rate sits
   ~477 bps BELOW the 15.00% MPR (2026-08-07) — outside the ±100 bps
   corridor on 100% of 2026 trading days — but the spread is extremely
   stable (std ~73 bps over 2026, ~0 bps over the last 20 business days).
   ``MPR + rolling mean of (interbank - MPR)`` over 20 business days is
   directly observable, tight, and honest.
3. **The AGD long-end basis is observable at the short anchor.** The gap
   between the step-curve-implied short zero and the sovereign short zero at
   the shortest liquid tenor is the disclosed basis, tapered linearly to
   zero by 5y.

No network, no randomness, no database: fixtures in, numbers out.
"""

from __future__ import annotations

import csv
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ on path

from app.domain.curves.bootstrap import BillQuote, bootstrap_zero_curve  # noqa: E402
from app.domain.curves.cointegration import (  # noqa: E402
    ADF_CRITICAL_VALUES,
    engle_granger,
)
from app.domain.curves.ois_step import MeetingDateStepCurve  # noqa: E402

SERIES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "market_desk" / "series"

# Auction-date alignment: pair each 91-day tender with the freshest interbank
# print at most this many calendar days old (the interbank series is
# business-daily, so this only drops pairs across data gaps).
PAIR_MAX_GAP_DAYS = 7

REGIME_WINDOWS: tuple[tuple[str, date | None], ...] = (
    ("full sample", None),
    ("2024+ (disinflation)", date(2024, 1, 1)),
    ("2025+ (post-stabilization)", date(2025, 1, 1)),
    ("2026+ (corridor-floor regime)", date(2026, 1, 1)),
)


@dataclass(frozen=True)
class Print:
    as_of: date
    value: float


def _read(name: str) -> list[dict[str, str]]:
    with (SERIES_DIR / name).open(newline="") as handle:
        return list(csv.DictReader(handle))


def _tbill_91_yields() -> list[Print]:
    """91-day tender true yields (BoG's published interest rate leg)."""
    prints = [
        Print(date.fromisoformat(row["date"]), float(row["interest_rate"]))
        for row in _read("tbill_rates.csv")
        if row["security"].strip() == "91 DAY BILL" and float(row["interest_rate"]) > 0.0
    ]
    return sorted(prints, key=lambda p: p.as_of)


def _interbank_daily() -> list[Print]:
    prints = [
        Print(date.fromisoformat(row["date"]), float(row["rate"]))
        for row in _read("interbank_rate.csv")
    ]
    return sorted(prints, key=lambda p: p.as_of)


def _mpc_rates() -> list[Print]:
    prints = [
        Print(date.fromisoformat(row["date"]), float(row["rate"]))
        for row in _read("mpc_policy_rate.csv")
    ]
    return sorted(prints, key=lambda p: p.as_of)


def _latest_leq(prints: list[Print], day: date) -> Print | None:
    candidate: Print | None = None
    for item in prints:
        if item.as_of > day:
            break
        candidate = item
    return candidate


def _aligned_pairs(
    tbill: list[Print], interbank: list[Print], start: date | None
) -> tuple[list[float], list[float]]:
    """(y, x) = (tender-date 91d yield, freshest interbank print <= tender)."""
    y: list[float] = []
    x: list[float] = []
    for auction in tbill:
        if start is not None and auction.as_of < start:
            continue
        pair = _latest_leq(interbank, auction.as_of)
        if pair is None or (auction.as_of - pair.as_of).days > PAIR_MAX_GAP_DAYS:
            continue
        y.append(auction.value)
        x.append(pair.value)
    return y, x


def _engle_granger_table(tbill: list[Print], interbank: list[Print]) -> None:
    print("1. Engle-Granger: 91-day T-bill yield on the daily interbank rate")
    print("   (auction-date aligned; residual ADF, AIC lag selection, MacKinnon")
    print(f"   asymptotic critical values 5%={ADF_CRITICAL_VALUES['5%']}, "
          f"10%={ADF_CRITICAL_VALUES['10%']})")
    print()
    header = (
        f"   {'window':<30} {'n':>5} {'alpha':>9} {'beta':>8} "
        f"{'ADF':>8} {'lags':>4} {'resid std':>10}  verdict @5%"
    )
    print(header)
    print("   " + "-" * (len(header) - 3))
    for label, start in REGIME_WINDOWS:
        y, x = _aligned_pairs(tbill, interbank, start)
        if len(y) < 20:
            print(f"   {label:<30} {len(y):>5}  insufficient observations")
            continue
        result = engle_granger(y, x)
        residual_std = statistics.pstdev(result.residuals)
        verdict = "cointegrated" if result.is_cointegrated("5%") else "REJECTED"
        print(
            f"   {label:<30} {len(y):>5} {result.alpha:>9.4f} {result.beta:>8.4f} "
            f"{result.adf_stat:>8.3f} {result.adf_lags:>4} {residual_std:>8.3f}pp  {verdict}"
        )
    print()
    print("   Reading: full-sample beta sits inside the ZAR 0.93-0.97 literature")
    print("   range, but no regime window rejects the no-cointegration null at 5%")
    print("   (the 2025+ window is only marginal against the 10% asymptotic value,")
    print("   and residual std ~4pp dwarfs any usable level precision).")
    print("   => v1 runs Engle-Granger as a WEEKLY DIAGNOSTIC recorded in QA")
    print("      results with significance_disclosed=true; promotion of the")
    print("      relationship to AGD level-setter is a future Track-2 event.")
    print()


def _corridor_stats(interbank: list[Print], mpc: list[Print]) -> float:
    print("2. Interbank-to-MPR corridor spread (the AGD short-end anchor)")
    print()
    spreads_2026 = [
        (item.as_of, item.value - prevailing.value)
        for item in interbank
        if item.as_of.year == 2026 and (prevailing := _latest_leq(mpc, item.as_of)) is not None
    ]
    values = [spread for _, spread in spreads_2026]
    outside = sum(1 for spread in values if abs(spread) > 1.0)
    last_20 = values[-20:]
    latest_print = interbank[-1]
    latest_mpr = mpc[-1]
    latest_spread = latest_print.value - latest_mpr.value
    print(f"   latest print          : {latest_print.as_of} interbank "
          f"{latest_print.value:.2f}% vs MPR {latest_mpr.value:.2f}% "
          f"=> spread {latest_spread * 100:+.0f} bps")
    print(f"   2026 trading days     : {len(values)}; outside ±100 bps corridor: "
          f"{outside} ({100.0 * outside / len(values):.0f}%)")
    print(f"   2026 spread std       : {statistics.pstdev(values) * 100:.0f} bps "
          f"(mean {statistics.mean(values):+.2f} pp)")
    print(f"   last 20 business days : mean {statistics.mean(last_20):+.2f} pp, "
          f"std {statistics.pstdev(last_20) * 100:.2f} bps")
    print()
    print("   Reading: the corridor is broken as a bound (100% of 2026 days sit")
    print("   below it) but the SPREAD is extremely stable. A short rolling mean")
    print("   is therefore the honest level: all inputs directly observable, no")
    print("   inferred relationship in the level.")
    print("   => AGD short end = MPR + rolling mean of (interbank - MPR) over")
    print("      overnight_spread_window_bdays = 20, on the MPC meeting-date grid.")
    print()
    return statistics.mean(last_20)


def _basis_anchor(tbill_window_spread_pp: float, interbank: list[Print], mpc: list[Print]) -> None:
    print("3. AGD long-end basis at the short anchor (disclosed assumption)")
    print()
    cob = interbank[-1].as_of
    tenders: dict[int, tuple[date, float]] = {}
    for row in _read("tbill_rates.csv"):
        security = row["security"].strip()
        if not security.endswith("DAY BILL"):
            continue
        tenor = int(security.split()[0])
        discount = float(row["discount_rate"])
        tender_date = date.fromisoformat(row["date"])
        if tenor in (91, 182, 364) and discount > 0.0 and tender_date <= cob:
            tenders[tenor] = (tender_date, discount)  # ascending file: last wins
    bills = [
        BillQuote(
            settlement=cob,
            maturity=cob + timedelta(days=tenor),
            discount_rate=discount / 100.0,
        )
        for tenor, (_, discount) in sorted(tenders.items())
    ]
    curve = bootstrap_zero_curve(bills)
    step = MeetingDateStepCurve(
        anchor_date=cob,
        mpr=mpc[-1].value / 100.0,
        meeting_dates=(),
        spread_bps=tbill_window_spread_pp * 100.0,
    )
    t0 = curve.times[0]
    days0 = round(t0 * 364.0)
    z_step = -math.log(step.df(cob + timedelta(days=days0))) / t0
    z_sov = curve.zero(t0)
    basis_bps = (z_step - z_sov) * 10_000.0
    bills_text = ", ".join(
        f"{tenor}d {discount:.4f}%" for tenor, (_, discount) in sorted(tenders.items())
    )
    print(f"   latest tender bills   : {bills_text}")
    print(f"   sovereign zero at t0  : {z_sov * 100:.4f}% (t0 = {t0:.4f}y)")
    print(f"   step-implied zero t0  : {z_step * 100:.4f}%")
    print(f"   observed basis        : {basis_bps:+.0f} bps at the shortest liquid tenor")
    print()
    print("   Reading: bills trade far through the overnight-linked level, so the")
    print("   discounting basis is large and positive. v1 publishes it as an")
    print("   OBSERVED short-anchor basis tapering linearly to zero by 5y —")
    print("   a disclosed assumption in the register, overridable by a governed")
    print("   explicit_bps_by_tenor_y map (Track 2).")
    print()


def _recommendations() -> None:
    print("4. Recommended AEQ-GHS-CURVES v1 parameter values")
    print()
    for line in (
        "overnight_spread_window_bdays = 20      # spread std ~0 bps over the window",
        "overnight_spread_bps          = -477    # static reference snapshot, 2026-08-07",
        "discount_basis.mode           = observed_short_anchor_linear_taper",
        "discount_basis.basis_taper_tenor_y = 5.0",
        "cointegration.role            = diagnostic   # NOT a level-setter",
        "cointegration.decision_significance = 5%",
        "cointegration.diagnostic_window_bdays = 250",
        "nss_fallback_min_liquid_points = 3      # the standing tender is 3 bills",
        "oscillation_tolerance          = 3.0    # qa_forwards total-variation ratio",
        "grr_check_tolerance_pp         = 1.0    # reconstruction flag, never a block",
    ):
        print(f"   {line}")
    print()
    print("   Encoded in app/services/market_desk/register.py")
    print("   (DEFAULT_METHODOLOGY_PARAMETERS_V1) and consumed by")
    print("   app/services/market_desk/calculation.py.")


def main() -> None:
    print("=" * 78)
    print("AequorOS market desk — Ghana methodology calibration (Track-2 evidence)")
    print(f"fixtures: {SERIES_DIR}")
    print("=" * 78)
    print()
    tbill = _tbill_91_yields()
    interbank = _interbank_daily()
    mpc = _mpc_rates()
    _engle_granger_table(tbill, interbank)
    window_spread = _corridor_stats(interbank, mpc)
    _basis_anchor(window_spread, interbank, mpc)
    _recommendations()


if __name__ == "__main__":
    main()
