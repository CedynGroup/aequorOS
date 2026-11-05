"""Silent substitutions removed from the derivation (audit §3).

Each test names the exact substitution the audit found and pins the fail-closed
behaviour that replaced it. Every case is written against a book that would
previously have produced a plausible, wrong regulatory number in silence.

This module's other-suite cases live in ``test_derivation_fail_closed_defaults.py``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.live.service.fact_derivation import (
    GroupResult,
    _derive_crm_collateral,
    _derive_ecl_exposure,
)
from tests.services.test_derivation_fail_closed_defaults import _canonical, _classify_loans, _row


def test_absent_ecl_and_crm_report_an_explicit_not_computable_state(
    db_session: Session,
) -> None:
    canonical = _canonical(
        _row("L/1", "LOAN", balance="40000000", regulatory_category="CORPORATE_UNRATED"),
    )
    loan_rows = _classify_loans(canonical, [])
    groups: list[GroupResult] = []
    assert _derive_ecl_exposure(loan_rows, groups) == []
    assert _derive_crm_collateral(canonical, loan_rows, groups) == []

    ecl = next(item for item in groups if item.group == "ecl_exposure")
    assert ecl.status == "skipped"
    assert "Not computable" in (ecl.note or "")
    assert "INGESTED provisions" in (ecl.note or "")
    crm = next(item for item in groups if item.group == "crm_collateral")
    assert crm.status == "skipped"
    assert "Not computable" in (crm.note or "")
