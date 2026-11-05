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

This module's other-suite cases live in ``test_reporting_validation_honesty.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Bank, RegulatoryPackage
from app.reporting.service import validation
from tests.support.helpers import ORG_1, USER_1


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
