"""Track-1 research adjustments (Option B): digest, apply, draft-only write."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import DeskDetermination, DeskMethodology, DeskObservation
from app.services.market_desk import calculation, determinations, register

COB = date(2026, 8, 7)
ANALYST = "analyst@aequoros.com"


@pytest.fixture
def desk(db_session: Session) -> Session:
    methodology = DeskMethodology(
        methodology_code=register.DEFAULT_METHODOLOGY_CODE,
        version=1,
        status="approved",
        parameters=register.DEFAULT_METHODOLOGY_PARAMETERS_V1,
        change_rationale="test bootstrap",
        proposed_by="steward@aequoros.com",
        approved_by="lead@aequoros.com",
        effective_from=date(2026, 1, 1),
    )
    db_session.add(methodology)
    for code, value in (
        ("GHS.MPR", "15.00"),
        ("GHS.INTERBANK.ON", "10.00"),
        ("GHS.TBILL.91.DISCOUNT", "20.00"),
        ("GHS.TBILL.182.DISCOUNT", "21.00"),
        ("GHS.TBILL.364.DISCOUNT", "22.00"),
        ("GHS.USDGHS.MID", "12.50"),
        ("GHS.GRR", "21.30"),
    ):
        db_session.add(
            DeskObservation(
                series_code=code,
                as_of_date=COB,
                value=Decimal(value),
                unit="pct" if code != "GHS.USDGHS.MID" else "rate",
                entered_by=ANALYST,
            )
        )
    db_session.commit()
    return db_session


def test_set_research_adjustments_requires_rationale_for_override(desk: Session) -> None:
    draft = determinations.create_draft(desk, cob_date=COB, prepared_by=ANALYST)
    with pytest.raises(HTTPException) as exc:
        determinations.set_research_adjustments(
            desk,
            draft.id,
            adjustments=[
                {
                    "series_code": "GHS.LENDING.INDICATOR",
                    "kind": "override",
                    "value": "25.5",
                    "rationale": "",
                }
            ],
            applied_by=ANALYST,
        )
    assert exc.value.status_code == 422


def test_override_and_additive_bps_applied_on_compute(desk: Session) -> None:
    draft = determinations.create_draft(desk, cob_date=COB, prepared_by=ANALYST)
    methodology = register.get_version(
        desk, draft.methodology_code, draft.methodology_version
    )
    calculation.compute_determination(desk, draft, methodology=methodology)
    base_mpr = float(draft.derived_values["rates"]["GHS.MPR"]["value"])

    determinations.set_research_adjustments(
        desk,
        draft.id,
        adjustments=[
            {
                "series_code": "GHS.MPR",
                "kind": "additive_bps",
                "value": "25",
                "rationale": "Desk steeps policy interpretation +25bp this week",
            },
            {
                "series_code": "GHS.LENDING.INDICATOR",
                "kind": "override",
                "value": "28.75",
                "rationale": "Desk lending base override pending APR re-issue",
            },
            {
                "series_code": "GHS.GRR",
                "kind": "assumption_note",
                "rationale": "GRR print confirmed against BoG bulletin",
            },
        ],
        applied_by=ANALYST,
    )
    calculation.compute_determination(desk, draft, methodology=methodology)
    desk.refresh(draft)

    rates = draft.derived_values["rates"]
    assert float(rates["GHS.MPR"]["value"]) == pytest.approx(base_mpr + 0.25)
    assert rates["GHS.MPR"]["treatment"] == "research_spread"
    assert float(rates["GHS.LENDING.INDICATOR"]["value"]) == pytest.approx(28.75)
    assert rates["GHS.LENDING.INDICATOR"]["treatment"] == "research_override"
    assert "research_adjustment" in rates["GHS.MPR"]["detail"]
    assert draft.derived_values["package_digest"]
    assert draft.derived_values["research_adjustments"]
    # Package digest must change when adjustments change.
    digest_with = draft.derived_values["package_digest"]
    determinations.set_research_adjustments(
        desk, draft.id, adjustments=[], applied_by=ANALYST
    )
    calculation.compute_determination(desk, draft, methodology=methodology)
    desk.refresh(draft)
    assert draft.derived_values["package_digest"] != digest_with


def test_adjustments_only_on_draft(desk: Session) -> None:
    draft = determinations.create_draft(desk, cob_date=COB, prepared_by=ANALYST)
    methodology = register.get_version(
        desk, draft.methodology_code, draft.methodology_version
    )
    calculation.compute_determination(desk, draft, methodology=methodology)
    determinations.submit_for_review(desk, draft.id)
    with pytest.raises(HTTPException) as exc:
        determinations.set_research_adjustments(
            desk,
            draft.id,
            adjustments=[
                {
                    "series_code": "GHS.MPR",
                    "kind": "override",
                    "value": "16",
                    "rationale": "too late",
                }
            ],
            applied_by=ANALYST,
        )
    assert exc.value.status_code == 409


def test_package_digest_stable_for_identical_package() -> None:
    a = determinations.package_digest(
        input_digest="abc",
        methodology_code="AEQ-GHS-CURVES",
        methodology_version=1,
        research_adjustments=[{"series_code": "GHS.MPR", "kind": "override", "value": "1"}],
    )
    b = determinations.package_digest(
        input_digest="abc",
        methodology_code="AEQ-GHS-CURVES",
        methodology_version=1,
        research_adjustments=[{"series_code": "GHS.MPR", "kind": "override", "value": "1"}],
    )
    c = determinations.package_digest(
        input_digest="abc",
        methodology_code="AEQ-GHS-CURVES",
        methodology_version=1,
        research_adjustments=[{"series_code": "GHS.MPR", "kind": "override", "value": "2"}],
    )
    assert a == b
    assert a != c
