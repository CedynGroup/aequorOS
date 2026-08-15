"""FC-G3 forward-start reproduction against the Eikon v3 pillar tab (real Refinitiv data).

The pillar tab (``tests/fixtures/market_desk/eikon_v3``) is a SOFR-OIS curve whose
short pillars are **forward-starting** off the March-2024 IMM: its internal base
is 2024-03-18 (two business days before the 2024-03-20 IMM), not the as-of spot
2024-01-03. ``test_eikon_reproduction`` already documents that the pillar tab and
the *published grid tab* are two different curves (~90 bps apart); this module
proves the FC-G3 fix on the reproducible object — the pillar tab's **own**
Start/End/DF/Yield forward grid:

* modelled as **forward-start** instruments the pillars reprice to <1e-8, recover
  every fixture pillar discount factor to <1e-8, and reproduce the pillar forward
  grid to machine precision;
* fed as **spot-start** instruments (the pre-FC-G3 assumption) the *same* market
  quotes distort that grid by tens of bps.

The full before/after benchmark (with the irreducible pillar-vs-published-grid
context) is printed by :func:`test_print_forward_start_benchmark` under ``-s``.
"""

from __future__ import annotations

import csv
import math
from datetime import date
from pathlib import Path

import pytest

from app.domain.curves.calendars import USA, next_imm_date
from app.domain.curves.conventions import DayCount, year_fraction
from app.domain.curves.instruments import DepositHelper
from app.domain.curves.multicurve import (
    MarketQuote,
    SolvedCurve,
    build_curve_set,
    forward_grid,
)

FIXTURE_DIR = Path(__file__).parents[2] / "fixtures" / "market_desk" / "eikon_v3"

AS_OF = date(2023, 12, 29)
IMM = next_imm_date(AS_OF)  # 2024-03-20 (third Wednesday of March 2024)
BASE = USA.advance(IMM, -2)  # 2024-03-18 (pillar-tab internal base: IMM spot - 2bd)


# --------------------------------------------------------------------------- #
# Fixture loaders / helpers                                                    #
# --------------------------------------------------------------------------- #


def _load_pillars(name: str) -> list[tuple[str, date, float]]:
    with (FIXTURE_DIR / f"{name}_pillars.csv").open() as handle:
        return [
            (row["tenor"], date.fromisoformat(row["maturity_date"]), float(row["discount_factor"]))
            for row in csv.DictReader(handle)
        ]


def _solved(valuation: date, pairs: list[tuple[date, float]]) -> SolvedCurve:
    dates = tuple(day for day, _ in pairs)
    zeros = tuple(-math.log(df) / ((day - valuation).days / 365.0) for day, df in pairs)
    return SolvedCurve(valuation, dates, zeros, interpolation="log_linear_df")


def _simple_forward(curve: SolvedCurve, start: date, end: date) -> float:
    tau = year_fraction(start, end, DayCount.ACT_360)
    return (curve.discount(start) / curve.discount(end) - 1.0) / tau


def _forward_start_for(tenor: str) -> date:
    """ON/TN accrue from the IMM base; every longer pillar accrues from the IMM spot."""
    return BASE if tenor in ("ON", "TN") else IMM


def _reproduction(name: str) -> dict[str, float]:
    """Build the pillar-tab forward curve two ways and measure the reproduction.

    Returns the forward-start reprice residual, fixture-DF recovery error, and the
    forward-start vs spot-start pillar-grid reproduction errors (fraction, not bps).
    """
    pillars = [(t, m, d) for t, m, d in _load_pillars(name) if m > BASE]
    df_by_mat = {m: d for _t, m, d in pillars}
    nodes = sorted(df_by_mat)

    # Source of truth: the pillar tab as a forward curve anchored at its IMM base.
    source = _solved(BASE, [(m, df_by_mat[m]) for m in nodes])
    reference = forward_grid(
        source, as_of=BASE, curve_frequency_months=3, calendar=USA, periods=120,
        output_basis=DayCount.ACT_360,
    )
    ref_rows = [row for row in reference.rows[1:] if row.end <= nodes[-1]]

    # One deposit per pillar (a deposit pins DF(maturity)/DF(start) exactly), quotes
    # implied from the source with the CORRECT forward-start accrual.
    fwd_helpers = [
        DepositHelper(t, USA, forward_start=_forward_start_for(t)) for t, _m, _d in pillars
    ]
    quotes = {h.tenor: h.implied_quote(source, source) for h in fwd_helpers}

    def build(helpers: list[DepositHelper]) -> SolvedCurve:
        market = sorted(
            (MarketQuote(h, quotes[h.tenor]) for h in helpers),
            key=lambda q: q.helper.pillar_date(USA, AS_OF),
        )
        return build_curve_set(as_of=AS_OF, calendar=USA, discount_quotes=market).discount

    fwd_curve = build(fwd_helpers)
    reprice = max(
        abs(h.reprice_residual(quotes[h.tenor], fwd_curve, fwd_curve)) for h in fwd_helpers
    )
    # Fixture pillar DFs recovered as ratios to the IMM-spot DF (anchor-free content).
    df_imm_src, df_imm_fwd = source.discount(IMM), fwd_curve.discount(IMM)
    pillar_df = max(
        abs(fwd_curve.discount(m) / df_imm_fwd - df_by_mat[m] / df_imm_src)
        for m in nodes if m > IMM
    )
    fwd_grid = max(abs(_simple_forward(fwd_curve, r.start, r.end) - r.yield_) for r in ref_rows)

    # Same quotes, spot-start helpers (the pre-FC-G3 bug).
    spot_curve = build([DepositHelper(t, USA) for t, _m, _d in pillars])
    spot_grid = max(abs(_simple_forward(spot_curve, r.start, r.end) - r.yield_) for r in ref_rows)

    return {
        "reprice": reprice,
        "pillar_df": pillar_df,
        "fwd_grid": fwd_grid,
        "spot_grid": spot_grid,
    }


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #


def test_imm_base_matches_fixture_pillar_anchor() -> None:
    """The IMM helper reproduces the pillar tab's documented internal base date."""
    assert date(2024, 3, 20) == IMM
    assert date(2024, 3, 18) == BASE
    # ON/TN pillars mature one/two business days after the base; 1W+ roll from the IMM spot.
    on_tenor, on_date, _df = _load_pillars("3m_sofr")[0]
    assert on_tenor == "ON"
    assert USA.add_tenor(BASE, "ON") == on_date


@pytest.mark.parametrize("name", ["curve_1", "3m_sofr"])
def test_forward_start_reprices_and_recovers_pillar_dfs(name: str) -> None:
    result = _reproduction(name)
    assert result["reprice"] < 1e-8
    assert result["pillar_df"] < 1e-8


@pytest.mark.parametrize("name", ["curve_1", "3m_sofr"])
def test_forward_start_reproduces_pillar_grid_spot_start_does_not(name: str) -> None:
    result = _reproduction(name)
    # Forward-start reproduces the pillar tab's own forward grid to machine precision.
    assert result["fwd_grid"] < 1e-9
    # The same quotes modelled spot-start distort the grid by tens of bps (>= 40 here).
    assert result["spot_grid"] > 40e-4
    # The fix collapses the error by more than four orders of magnitude.
    assert result["spot_grid"] > 1e5 * result["fwd_grid"]


def test_print_forward_start_benchmark() -> None:
    lines = [
        "",
        "=" * 84,
        "FC-G3 FORWARD-START REPRODUCTION — Eikon v3 pillar tab (USD, as-of 2023-12-29)",
        f"IMM={IMM}  base={BASE}",
        "=" * 84,
        f"{'curve':10}{'reprice':>12}{'pillarDF':>12}"
        f"{'fwd-grid(bps)':>16}{'spot-grid(bps)':>16}",
    ]
    for name in ("curve_1", "3m_sofr"):
        r = _reproduction(name)
        lines.append(
            f"{name:10}{r['reprice']:>12.2e}{r['pillar_df']:>12.2e}"
            f"{r['fwd_grid'] * 1e4:>16.4f}{r['spot_grid'] * 1e4:>16.2f}"
        )
    lines.append("=" * 84)
    print("\n".join(lines))  # noqa: T201 - benchmark output is the point under -s
