"""The Eikon Forward Curve Template v3 reproduction benchmark (real Refinitiv data).

Fixture: ``tests/fixtures/market_desk/eikon_v3`` — USD, USA calendar,
as-of 2023-12-29. Four tabs (curve_1/curve_2/curve_3/3m_sofr) each ship 52 raw
pillar nodes, a 148-row tenor-adjusted forward grid, and the USA holiday CSV.

Three graded benchmarks live here:

1. **Date reproduction (FC-0).** Every pillar maturity and every forward-grid
   Start/End reproduced exactly by the calendar engine.
2. **Forward-grid math (FC-2).** The Start/End/DF/Yield arithmetic reproduced to
   machine precision from a discount curve; plus how well each interpolation
   densifies a sparse node set, and the (documented, large) error of building
   from the fixture's own pillar tab.
3. **Closed-loop bootstrap (FC-1/FC-2).** Par swap rates implied from the fixture
   pillar discount factors, re-bootstrapped, recover those discount factors.

READ THE MODULE-LEVEL FINDINGS below — two documented fixture inconsistencies
gate what "Refinitiv exactness" can mean here.

Findings (verified by these tests):

* **The pillar tab is anchored on an internal base date 2024-03-18** (two business
  days before the 2024-03-20 March IMM), NOT on the stated as-of 2023-12-29. Every
  pillar maturity reproduces exactly from the calendar once anchored there; the
  grid tab, by contrast, is anchored on the as-of spot (2023-12-31). The two tabs
  therefore describe *different curves*: naively building a discount curve from the
  pillar tab and resampling it does NOT reproduce the grid (~90 bps error) — see
  ``test_pillar_tab_does_not_reproduce_grid``.
* **The USA calendar Eikon adjusted against uses Sunday->Monday observance only**
  (no Saturday->Friday roll-back). The shipped holiday CSV lists both-direction
  observances and is thus not that calendar; the generator-based :data:`USA`
  calendar reproduces every fixture date.

Run ``pytest tests/domain/curves/test_eikon_reproduction.py -s`` to print the
benchmark table.
"""

from __future__ import annotations

import csv
import math
from datetime import date
from pathlib import Path

import pytest

from app.domain.curves.calendars import USA, BusinessDayConvention, add_calendar_months
from app.domain.curves.conventions import DayCount
from app.domain.curves.instruments import DepositHelper, SwapHelper
from app.domain.curves.multicurve import (
    MarketQuote,
    SolvedCurve,
    build_curve_set,
    forward_grid,
)

FIXTURE_DIR = Path(__file__).parents[2] / "fixtures" / "market_desk" / "eikon_v3"

AS_OF = date(2023, 12, 29)
GRID_SPOT = date(2023, 12, 31)  # the grid tab's anchor (quarter-end on/after as-of)
PILLAR_BASE = date(2024, 3, 18)  # the pillar tab's internal anchor (2bd before March IMM)
MF = BusinessDayConvention.MODIFIED_FOLLOWING

QUARTERLY = ("curve_1", "curve_2", "3m_sofr")
MONTHLY = ("curve_3",)
FREQUENCY_MONTHS = {"curve_1": 3, "curve_2": 3, "curve_3": 1, "3m_sofr": 3}


# --------------------------------------------------------------------------- #
# Fixture loaders                                                             #
# --------------------------------------------------------------------------- #


def _load_pillars(name: str) -> list[tuple[str, date, float]]:
    with (FIXTURE_DIR / f"{name}_pillars.csv").open() as handle:
        return [
            (row["tenor"], date.fromisoformat(row["maturity_date"]), float(row["discount_factor"]))
            for row in csv.DictReader(handle)
        ]


def _load_grid(name: str) -> list[tuple[date, date, float, float]]:
    with (FIXTURE_DIR / f"{name}_grid.csv").open() as handle:
        return [
            (
                date.fromisoformat(row["start_date"]),
                date.fromisoformat(row["end_date"]),
                float(row["discount_factor"]),
                float(row["yield"]),
            )
            for row in csv.DictReader(handle)
        ]


# --------------------------------------------------------------------------- #
# Shared curve builders                                                       #
# --------------------------------------------------------------------------- #


def _pillar_maturity(tenor: str) -> date:
    """The pillar tab's maturity from its internal base (ON/TN from base, rest from spot)."""
    spot = USA.spot_date(PILLAR_BASE)  # advance(base, 2) == 2024-03-20
    if tenor in ("ON", "TN"):
        return USA.add_tenor(PILLAR_BASE, tenor)
    return USA.add_tenor(spot, tenor)


def _solved_from_dates(
    valuation: date, pairs: list[tuple[date, float]], interpolation: str
) -> SolvedCurve:
    dates = tuple(day for day, _ in pairs)
    zeros = tuple(
        -math.log(df) / ((day - valuation).days / 365.0) for day, df in pairs
    )
    return SolvedCurve(valuation, dates, zeros, interpolation=interpolation)


def _grid_discount_curve(name: str, interpolation: str = "log_linear_df") -> SolvedCurve:
    """Discount curve from the grid's own cumulative DFs at its end dates (anchor=spot)."""
    grid = _load_grid(name)
    cumulative = 1.0
    pairs: list[tuple[date, float]] = []
    for _, end, period_df, _yield in grid[1:]:
        cumulative *= period_df
        pairs.append((end, cumulative))
    return _solved_from_dates(GRID_SPOT, pairs, interpolation)


def _reproduce_grid(curve: SolvedCurve, name: str) -> tuple[float, float, bool]:
    """Run forward_grid and compare to the fixture; returns (maxDFerr, maxYerr, dates_ok)."""
    grid = _load_grid(name)
    produced = forward_grid(
        curve,
        as_of=AS_OF,
        curve_frequency_months=FREQUENCY_MONTHS[name],
        calendar=USA,
        periods=len(grid) - 1,
        output_basis=DayCount.ACT_360,
    )
    max_df = 0.0
    max_yield = 0.0
    dates_ok = True
    for produced_row, (start, end, period_df, yield_value) in zip(
        produced.rows, grid, strict=True
    ):
        if produced_row.start != start or produced_row.end != end:
            dates_ok = False
        max_df = max(max_df, abs(produced_row.discount_factor - period_df))
        max_yield = max(max_yield, abs(produced_row.yield_ - yield_value))
    return max_df, max_yield, dates_ok


# --------------------------------------------------------------------------- #
# Benchmark 1 — date reproduction (FC-0)                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["curve_1", "curve_2", "curve_3", "3m_sofr"])
def test_pillar_dates_reproduce_exactly(name: str) -> None:
    for tenor, fixture_date, _df in _load_pillars(name):
        assert _pillar_maturity(tenor) == fixture_date, f"{name}:{tenor}"


@pytest.mark.parametrize("name", ["curve_1", "curve_2", "curve_3", "3m_sofr"])
def test_grid_dates_reproduce_exactly(name: str) -> None:
    grid = _load_grid(name)
    # A dummy curve spanning past 2060 lets forward_grid emit the full date sequence.
    dummy = SolvedCurve(AS_OF, (date(2075, 1, 2),), (0.03,))
    produced = forward_grid(
        dummy,
        as_of=AS_OF,
        curve_frequency_months=FREQUENCY_MONTHS[name],
        calendar=USA,
        periods=len(grid) - 1,
    )
    produced_dates = [(row.start, row.end) for row in produced.rows]
    fixture_dates = [(start, end) for start, end, _, _ in grid]
    assert produced_dates == fixture_dates


# --------------------------------------------------------------------------- #
# Benchmark 2 — forward-grid math (FC-2)                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["curve_1", "3m_sofr"])
def test_forward_grid_math_reproduces_grid_to_machine_precision(name: str) -> None:
    """Given the discount curve, the Start/End/DF/Yield arithmetic is exact."""
    curve = _grid_discount_curve(name)
    max_df, max_yield, dates_ok = _reproduce_grid(curve, name)
    assert dates_ok
    assert max_df < 1e-9, f"{name} maxDF={max_df:.2e}"
    assert max_yield < 1e-9, f"{name} maxYield={max_yield:.2e}"


@pytest.mark.parametrize("name", ["curve_1", "3m_sofr"])
def test_pchip_best_densifies_a_sparse_node_set(name: str) -> None:
    """With only ~16 nodes, PCHIP reproduces the dense grid best (documented numbers)."""
    errors = {interp: _sparse_reproduction(name, interp) for interp in _SPARSE_INTERP}
    best = min(errors, key=lambda k: errors[k][0])
    assert best == "pchip", f"{name}: best interpolation was {best}"
    # PCHIP keeps the whole 40-year grid within a few tenths of a percent in DF terms.
    assert errors["pchip"][0] < 3e-3


def test_pillar_tab_does_not_reproduce_grid() -> None:
    """The fixture's pillar tab and grid tab are different curves — a documented gap.

    Building a discount curve from the pillar DFs (placed at as-of + tenor) and
    resampling reproduces the grid only to ~90 bps, NOT to Refinitiv exactness.
    """
    max_yield = _pillar_tab_cross_error("curve_1")
    assert 5e-3 < max_yield < 2e-2  # 50-200 bps: firmly non-reproducing


# --------------------------------------------------------------------------- #
# Benchmark 3 — closed-loop bootstrap (FC-1/FC-2)                              #
# --------------------------------------------------------------------------- #


def _closed_loop_recovery(name: str) -> float:
    """Imply par swap/deposit quotes from the pillar DFs, re-bootstrap, recover DFs."""
    by_date = {day: df for _, day, df in _load_pillars(name)}
    deposits = [DepositHelper(t, USA) for t in ("1M", "2M", "3M", "6M", "9M")]
    swaps = [
        SwapHelper(t, USA)
        for t in ("1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y")
    ]
    helpers = [*deposits, *swaps]
    node_dates = sorted(helper.pillar_date(USA, PILLAR_BASE) for helper in helpers)
    source = _solved_from_dates(
        PILLAR_BASE, [(day, by_date[day]) for day in node_dates], "log_linear_df"
    )
    quotes = [MarketQuote(helper, helper.implied_quote(source, source)) for helper in helpers]
    quotes.sort(key=lambda q: q.helper.pillar_date(USA, PILLAR_BASE))
    rebuilt = build_curve_set(as_of=PILLAR_BASE, calendar=USA, discount_quotes=quotes)
    return max(abs(rebuilt.discount.discount(day) - by_date[day]) for day in node_dates)


@pytest.mark.parametrize("name", ["curve_1", "3m_sofr"])
def test_closed_loop_bootstrap_recovers_pillar_dfs(name: str) -> None:
    assert _closed_loop_recovery(name) < 1e-6


# --------------------------------------------------------------------------- #
# Interpolation / cross-error helpers                                         #
# --------------------------------------------------------------------------- #

_SPARSE_INTERP = ("log_linear_df", "monotone_convex", "pchip")
_SPARSE_MONTHS = (3, 6, 9, 12, 18, 24, 36, 48, 60, 84, 120, 144, 180, 240, 300, 360)


def _sparse_reproduction(name: str, interpolation: str) -> tuple[float, float]:
    dense = _grid_discount_curve(name)
    last_date = dense.node_dates[-1]
    pairs: list[tuple[date, float]] = []
    for months in _SPARSE_MONTHS:
        node = USA.adjust(add_calendar_months(GRID_SPOT, months, end_of_month=True), MF)
        if node >= last_date:
            break
        pairs.append((node, dense.discount(node)))
    sparse = _solved_from_dates(GRID_SPOT, pairs, interpolation)
    max_df, max_yield, _ = _reproduce_grid(sparse, name)
    return max_df, max_yield


def _pillar_tab_cross_error(name: str) -> float:
    """Max grid yield error when the curve is built from the fixture pillar tab."""
    spot = USA.spot_date(AS_OF)
    pairs: list[tuple[date, float]] = []
    for tenor, _fixture_date, df in _load_pillars(name):
        node = USA.advance(AS_OF, 1) if tenor == "ON" else (
            spot if tenor == "TN" else USA.add_tenor(spot, tenor)
        )
        if node <= AS_OF:
            continue
        pairs.append((node, df))
    pairs.sort(key=lambda item: item[0])
    curve = _solved_from_dates(AS_OF, pairs, "log_linear_df")
    _max_df, max_yield, _dates = _reproduce_grid(curve, name)
    return max_yield


# --------------------------------------------------------------------------- #
# Benchmark table (printed under -s)                                          #
# --------------------------------------------------------------------------- #


def test_print_benchmark_table() -> None:
    lines = [
        "",
        "=" * 92,
        "EIKON FORWARD CURVE v3 — REPRODUCTION BENCHMARK (as-of 2023-12-29, USD, USA)",
        "=" * 92,
    ]
    lines.append(
        f"{'curve':10}{'grid-math maxDF':>18}{'grid-math maxY(bps)':>22}"
        f"{'closed-loop maxDF':>20}"
    )
    for name in ("curve_1", "3m_sofr"):
        curve = _grid_discount_curve(name)
        max_df, max_yield, _ = _reproduce_grid(curve, name)
        recovery = _closed_loop_recovery(name)
        lines.append(
            f"{name:10}{max_df:>18.2e}{max_yield * 1e4:>22.4f}{recovery:>20.2e}"
        )
    lines.append("")
    lines.append("Sparse-node densification (16 nodes sampled from the grid curve):")
    lines.append(f"{'curve':10}{'interp':18}{'maxDF':>14}{'maxY(bps)':>14}")
    for name in ("curve_1", "3m_sofr"):
        for interpolation in _SPARSE_INTERP:
            df_error, yield_error = _sparse_reproduction(name, interpolation)
            lines.append(
                f"{name:10}{interpolation:18}{df_error:>14.2e}{yield_error * 1e4:>14.2f}"
            )
    lines.append("")
    lines.append(
        "Fixture pillar-tab -> grid cross-reproduction (documented inconsistency): "
        f"curve_1 maxY = {_pillar_tab_cross_error('curve_1') * 1e4:.1f} bps"
    )
    lines.append("=" * 92)
    print("\n".join(lines))  # noqa: T201 - benchmark output is the point under -s
