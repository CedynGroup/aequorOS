"""Weekly rates package completeness + WoW deltas."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models import DeskDetermination, DeskMethodology, DeskObservation
from app.services.market_desk import calculation, determinations, package, register

COB = date(2026, 8, 7)
PRIOR_COB = date(2026, 7, 31)
ANALYST = "analyst@aequoros.com"


@pytest.fixture
def desk(db_session: Session) -> Session:
    db_session.add(
        DeskMethodology(
            methodology_code=register.DEFAULT_METHODOLOGY_CODE,
            version=1,
            status="approved",
            parameters=register.DEFAULT_METHODOLOGY_PARAMETERS_V1,
            change_rationale="test",
            proposed_by="steward@aequoros.com",
            approved_by="lead@aequoros.com",
            effective_from=date(2026, 1, 1),
        )
    )
    for code, value, as_of in (
        ("GHS.MPR", "15.00", COB),
        ("GHS.INTERBANK.ON", "10.00", COB),
        ("GHS.TBILL.91.DISCOUNT", "20.00", COB),
        ("GHS.TBILL.182.DISCOUNT", "21.00", COB),
        ("GHS.TBILL.364.DISCOUNT", "22.00", COB),
        ("GHS.USDGHS.MID", "12.50", COB),
        ("GHS.GRR", "21.30", date(2026, 7, 1)),
    ):
        db_session.add(
            DeskObservation(
                series_code=code,
                as_of_date=as_of,
                value=Decimal(value),
                unit="pct" if code != "GHS.USDGHS.MID" else "rate",
                entered_by=ANALYST,
            )
        )
    db_session.commit()
    return db_session


def test_completeness_ready_when_required_present(desk: Session) -> None:
    report = package.completeness_report(desk, cob_date=COB)
    assert report["ready"] is True
    assert report["required_missing"] == []
    assert report["required_present"] == report["required_total"]
    mpr = next(i for i in report["items"] if i["series_code"] == "GHS.MPR")
    assert mpr["status"] == "present"
    assert mpr["provenance"]["source"] == "manual"


def test_completeness_flags_missing_required(desk: Session) -> None:
    # Drop GRR current-generation by superseding… easier: report for empty COB
    empty_cob = date(2020, 1, 1)
    report = package.completeness_report(desk, cob_date=empty_cob)
    assert report["ready"] is False
    assert "GHS.MPR" in report["required_missing"]


def test_package_view_includes_wow_and_provenance(desk: Session) -> None:
    # Seed a prior published package with known rates for WoW deltas.
    prior = DeskDetermination(
        cob_date=PRIOR_COB,
        methodology_code=register.DEFAULT_METHODOLOGY_CODE,
        methodology_version=1,
        input_snapshot=[{"series_code": "GHS.MPR", "as_of_date": "2026-07-22", "value": "14.5"}],
        input_digest="a" * 64,
        derived_values={
            "rates": {
                "GHS.MPR": {"value": "14.500000", "unit": "pct"},
                "GHS.INTERBANK.ON": {"value": "9.500000", "unit": "pct"},
            },
            "rates_qa_passed": True,
            "qa_passed": True,
        },
        status="published",
        prepared_by=ANALYST,
        reviewed_by="lead@aequoros.com",
    )
    desk.add(prior)
    determinations.mark_published(desk, prior)
    desk.commit()

    draft = determinations.create_draft(desk, cob_date=COB, prepared_by=ANALYST)
    methodology = register.get_version(
        desk, draft.methodology_code, draft.methodology_version
    )
    calculation.compute_determination(desk, draft, methodology=methodology)
    desk.commit()

    view = package.build_package_view(desk, draft)
    assert view["completeness"]["ready"] is True
    assert view["week_over_week"]["prior_determination_id"] == str(prior.id)
    mpr_delta = next(
        d for d in view["week_over_week"]["deltas"] if d["series_code"] == "GHS.MPR"
    )
    assert mpr_delta["prior"] == "14.500000"
    assert mpr_delta["delta_pp"] is not None
    assert any(r["series_code"] == "GHS.MPR" for r in view["input_provenance"])
