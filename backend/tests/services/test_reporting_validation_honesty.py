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
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.domain.forecasting.engine import ProjectionYear
from app.models import Bank, RegulatoryPackage
from app.services.regulatory_forecasting import _balance_ties_row
from app.services.regulatory_reporting import validation
from tests.api.helpers import ORG_1, USER_1


def _bank(db: Session) -> Bank:
    """A minimal tenant bank — the movement rule needs a package row, and a
    package row needs an institution to hang off."""
    bank = Bank(
        organization_id=ORG_1,
        name="Movement rule tenant",
        short_name="Mov",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="x",
        institution_type="universal_bank",
    )
    db.add(bank)
    db.flush()
    return bank


def _package(  # noqa: PLR0913 — a package row needs its identifying columns
    db: Session,
    bank: Bank,
    *,
    reporting_date: date,
    status: str,
    totals: list[dict[str, Any]],
    version: int = 1,
) -> RegulatoryPackage:
    package = RegulatoryPackage(
        organization_id=ORG_1,
        bank_id=bank.id,
        return_family="liquidity",
        return_code="TEST-MOVEMENT",
        reporting_date=reporting_date,
        frequency="monthly",
        basis="solo",
        status=status,
        version=version,
        snapshot={
            "schema_version": "regulatory-package-v1",
            "sections": [],
            "totals": totals,
            "metadata": {"generated_at": "2026-03-31T00:00:00+00:00"},
        },
        source_runs=[],
        generated_by=USER_1,
        generated_at=datetime.now(UTC),
    )
    db.add(package)
    db.flush()
    return package


def _details(findings: list[dict[str, str]]) -> str:
    return " ".join(f["detail"] for f in findings)


# ---------------------------------------------------------------------------
# the movement rule
# ---------------------------------------------------------------------------


def test_a_movement_out_of_zero_is_reported_not_skipped(db_session: Session) -> None:
    """0 -> X used to be silently skipped and then declared clean."""
    bank = _bank(db_session)
    _package(
        db_session,
        bank,
        reporting_date=date(2026, 2, 28),
        status="submitted",
        totals=[{"code": "hqla_total_ghs", "value": "0"}],
    )
    current = _package(
        db_session,
        bank,
        reporting_date=date(2026, 3, 31),
        status="generated",
        totals=[{"code": "hqla_total_ghs", "value": "4200000"}],
    )
    findings = validation._movement_findings(db_session, current)  # noqa: SLF001
    warnings = [f for f in findings if f["severity"] == "WARNING"]
    assert warnings, findings
    assert "moved from zero to 4200000" in warnings[0]["detail"]
    # ... and the false all-clear is gone.
    assert "No headline total moved more than" not in _details(findings)


def test_zero_to_zero_is_genuinely_no_movement(db_session: Session) -> None:
    """The counterpart: 0 -> 0 must not manufacture a warning."""
    bank = _bank(db_session)
    _package(
        db_session,
        bank,
        reporting_date=date(2026, 2, 28),
        status="submitted",
        totals=[{"code": "hqla_total_ghs", "value": "0"}],
    )
    current = _package(
        db_session,
        bank,
        reporting_date=date(2026, 3, 31),
        status="generated",
        totals=[{"code": "hqla_total_ghs", "value": "0"}],
    )
    findings = validation._movement_findings(db_session, current)  # noqa: SLF001
    assert not [f for f in findings if f["severity"] == "WARNING"]
    assert "1 headline total(s) were compared" in _details(findings)


def test_uncomparable_totals_are_named_rather_than_swallowed(db_session: Session) -> None:
    """A total with no prior counterpart, and one with no numeric value."""
    bank = _bank(db_session)
    _package(
        db_session,
        bank,
        reporting_date=date(2026, 2, 28),
        status="submitted",
        totals=[{"code": "hqla_total_ghs", "value": "100"}],
    )
    current = _package(
        db_session,
        bank,
        reporting_date=date(2026, 3, 31),
        status="generated",
        totals=[
            {"code": "hqla_total_ghs", "value": "105"},
            {"code": "brand_new_total", "value": "7"},
            {"code": "narrative_total", "value": "not a number"},
        ],
    )
    findings = validation._movement_findings(db_session, current)  # noqa: SLF001
    detail = _details(findings)
    assert "brand_new_total" in detail
    assert "narrative_total" in detail
    # The clean statement is scoped to what was actually compared.
    assert "1 headline total(s) were compared" in detail
    assert "2 further total(s) could not be compared" in detail


def test_a_real_swing_still_warns_with_its_percentage(db_session: Session) -> None:
    """The rule that already worked must keep working, unchanged."""
    bank = _bank(db_session)
    _package(
        db_session,
        bank,
        reporting_date=date(2026, 2, 28),
        status="submitted",
        totals=[{"code": "hqla_total_ghs", "value": "100"}],
    )
    current = _package(
        db_session,
        bank,
        reporting_date=date(2026, 3, 31),
        status="generated",
        totals=[{"code": "hqla_total_ghs", "value": "200"}],
    )
    findings = validation._movement_findings(db_session, current)  # noqa: SLF001
    warnings = [f for f in findings if f["severity"] == "WARNING"]
    assert len(warnings) == 1
    assert "100.00%" in warnings[0]["detail"]


def test_the_rule_version_records_that_the_rule_changed() -> None:
    # v1.3.0: the completeness rule stopped requiring a headline ``totals``
    # block from a snapshot whose authority record declares an official
    # template as the authority for its derived figures
    # (tests/services/test_reporting_totals_authority.py).
    assert validation.RULE_VERSION == "regulatory-package-validation-v1.3.0"


# ---------------------------------------------------------------------------
# the forecast balance identity
# ---------------------------------------------------------------------------


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


def test_the_old_unconditional_assurance_is_gone() -> None:
    """The exact sentence the audit called out must not be reachable."""
    from pathlib import Path  # noqa: PLC0415

    source = Path(__file__).resolve().parents[2] / "app" / "services" / "regulatory_forecasting.py"
    text = source.read_text()
    assert (
        '"Projected assets equal liabilities plus equity in every forecast year."' not in text
    )
