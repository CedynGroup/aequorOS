"""P0-14 — two validations that asserted instead of testing.

Both wrote a reassuring sentence into a record a supervisor reads, and neither
had checked anything:

* ``regulatory_reporting/validation.py`` skipped every ``0 -> X`` movement — the
  most material movement a return can show — and then stated "No headline total
  moved more than 25% versus the previous package."
* ``regulatory_forecasting._validation_rows`` hard-coded
  ``("projection_balance_ties", True, "error", "Projected assets equal
  liabilities plus equity in every forecast year.")``. Nothing was evaluated,
  and it was only ever "true" because the projection carries a funding plug that
  forces it.

These tests pin the replacements: the movement rule now reports what it could
not compare and only claims a clean bill of health over what it did compare,
and the balance rule computes a real identity over the projected years.

This module's other-suite cases live in

``test_reporting_validation_honesty_integration.py``.
"""

from __future__ import annotations

from decimal import Decimal

from app.forecasting.domain.engine import ProjectionYear
from app.forecasting.service.regulatory_forecasting import _balance_ties_row
from app.reporting.service import validation


def test_the_rule_version_records_that_the_rule_changed() -> None:
    # v1.3.0: the completeness rule stopped requiring a headline ``totals``
    # block from a snapshot whose authority record declares an official
    # template as the authority for its derived figures
    # (tests/reporting/service/test_reporting_totals_authority_integration.py).
    assert validation.RULE_VERSION == "regulatory-package-validation-v1.3.0"


def _year(  # noqa: PLR0913 — one keyword per balance-sheet component
    year: int,
    *,
    total_assets: str,
    loans: str,
    securities: str,
    cash: str,
    deposits: str,
    borrowings: str,
    equity: str,
) -> ProjectionYear:
    zero = Decimal("0")
    return ProjectionYear(
        year=year,
        period_label=f"Y{year}",
        total_assets=Decimal(total_assets),
        loans=Decimal(loans),
        securities=Decimal(securities),
        cash=Decimal(cash),
        deposits=Decimal(deposits),
        borrowings_plug=Decimal(borrowings),
        equity=Decimal(equity),
        nii=zero,
        fees=zero,
        total_income=zero,
        opex=zero,
        credit_losses=zero,
        net_income=zero,
        dividends=zero,
        roe_pct=None,
        car_pct=zero,
        tier1_ratio_pct=zero,
        cet1_ratio_pct=zero,
        lcr_pct=zero,
        nsfr_pct=zero,
    )


def test_balance_ties_passes_and_says_what_it_checked() -> None:
    """A tied projection: constant blocks identical in every year."""
    years = [
        _year(
            0,
            total_assets="1000",
            loans="600",
            securities="200",
            cash="150",
            deposits="700",
            borrowings="100",
            equity="150",
        ),
        _year(
            1,
            total_assets="1100",
            loans="660",
            securities="220",
            cash="170",
            deposits="770",
            borrowings="110",
            equity="170",
        ),
    ]
    code, passed, severity, message = _balance_ties_row(years)
    assert code == "projection_balance_ties"
    assert passed is True
    assert severity == "error"
    # It reports the scope of the check instead of asserting an outcome.
    assert "Checked across 2 projected periods" in message
    assert "Y0 to Y1" in message


def test_balance_ties_fails_when_the_funding_side_drifts() -> None:
    """The case the hard-coded ``True`` could never have reported."""
    years = [
        _year(
            0,
            total_assets="1000",
            loans="600",
            securities="200",
            cash="150",
            deposits="700",
            borrowings="100",
            equity="150",
        ),
        _year(
            1,
            total_assets="1100",
            loans="660",
            securities="220",
            cash="170",
            deposits="770",
            borrowings="110",
            equity="120",  # equity dropped without any other side moving
        ),
    ]
    code, passed, severity, message = _balance_ties_row(years)
    assert code == "projection_balance_ties"
    assert passed is False
    assert severity == "error"
    assert "funding side of the balance sheet does not tie" in message
    assert "Y1" in message


def test_balance_ties_fails_when_the_asset_side_drifts() -> None:
    years = [
        _year(
            0,
            total_assets="1000",
            loans="600",
            securities="200",
            cash="150",
            deposits="700",
            borrowings="100",
            equity="150",
        ),
        _year(
            1,
            total_assets="1100",
            loans="660",
            securities="220",
            cash="120",  # cash short: total assets no longer reconcile
            deposits="770",
            borrowings="110",
            equity="170",
        ),
    ]
    _code, passed, _severity, message = _balance_ties_row(years)
    assert passed is False
    assert "asset side of the balance sheet does not tie" in message


def test_balance_ties_is_honest_when_there_is_nothing_to_compare() -> None:
    """One period cannot demonstrate an identity across years, and says so."""
    years = [
        _year(
            0,
            total_assets="1000",
            loans="600",
            securities="200",
            cash="150",
            deposits="700",
            borrowings="100",
            equity="150",
        )
    ]
    _code, passed, _severity, message = _balance_ties_row(years)
    assert passed is True
    assert "nothing to compare across years" in message
    assert "Projected assets equal liabilities plus equity" not in message
