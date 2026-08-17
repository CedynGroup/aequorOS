"""W6 remainder: basis dimension, DBK daily family, T-1 comparative, deadline
overrides (docs/submission_pipeline_plan.md §W6 items 1, 5, 6, 7).

Service-level tests over the seeded sample bank, mirroring
``test_regulatory_reporting_exports`` / ``_workflow``.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import BankReportingPeriod, RegulatoryPackage
from app.schemas.regulatory_fx import FxScenarioBatchCreate
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.schemas.regulatory_reporting import (
    RegulatoryPackageCreate,
    ReportingSettingsPut,
)
from app.services import regulatory_fx, regulatory_liquidity
from app.services.regulatory_reporting import (
    calendar,
    generation,
    reporting_settings,
)
from app.services.regulatory_reporting.registry import (
    daily_next_business_day,
    get_definition,
)
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
MARCH = date(2026, 3, 31)
FEBRUARY = date(2026, 2, 28)


def _period_id(db: Session, period_end: date):
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == period_end,
        )
    )
    assert period_id is not None, f"seed lacks a period ending {period_end}"
    return period_id


def _liquidity_run(db: Session, period_end: date) -> None:
    run = regulatory_liquidity.create_liquidity_run(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="liquidity",
            reporting_period_id=_period_id(db, period_end),
            scenario_code="baseline",
        ),
    )
    assert run.status == "succeeded"


def _fx_run(db: Session, period_end: date) -> None:
    batch = regulatory_fx.run_all_fx_scenarios(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        FxScenarioBatchCreate(reporting_period_id=_period_id(db, period_end)),
    )
    assert any(run.status == "succeeded" for run in batch.runs)


def _generate(
    db: Session,
    return_code: str,
    reporting_date: date,
    basis: Literal["solo", "consolidated"] = "solo",
) -> RegulatoryPackage:
    read = generation.generate_package(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(
            return_code=return_code, reporting_date=reporting_date, basis=basis
        ),
    )
    row = db.scalar(select(RegulatoryPackage).where(RegulatoryPackage.id == read.id))
    assert row is not None
    return row


def _current(db: Session, return_code: str, reporting_date: date, basis: str):
    return db.scalar(
        select(RegulatoryPackage).where(
            RegulatoryPackage.organization_id == DEMO_ORG_ID,
            RegulatoryPackage.bank_id == SAMPLE_BANK_ID,
            RegulatoryPackage.return_code == return_code,
            RegulatoryPackage.reporting_date == reporting_date,
            RegulatoryPackage.basis == basis,
            RegulatoryPackage.status != "superseded",
        )
    )


# --------------------------------------------------------------------------
# Item 6 — SOLO | CONSOLIDATED basis dimension
# --------------------------------------------------------------------------


def test_solo_and_consolidated_coexist_as_independent_current_versions(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    _liquidity_run(db_session, MARCH)

    solo = _generate(db_session, "LCR-NSFR", MARCH, basis="solo")
    consolidated = _generate(db_session, "LCR-NSFR", MARCH, basis="consolidated")

    # Both are current (non-superseded) for the same (return, date) — the new
    # unique index keys on basis, so they do not collide.
    assert solo.basis == "solo"
    assert consolidated.basis == "consolidated"
    assert solo.status == "generated"
    assert consolidated.status == "generated"
    assert solo.id != consolidated.id
    assert solo.snapshot["institution"]["basis"] == "solo"
    assert consolidated.snapshot["institution"]["basis"] == "consolidated"
    assert consolidated.snapshot["metadata"]["basis"] == "consolidated"

    current_count = db_session.scalar(
        select(func.count())
        .select_from(RegulatoryPackage)
        .where(
            RegulatoryPackage.bank_id == SAMPLE_BANK_ID,
            RegulatoryPackage.return_code == "LCR-NSFR",
            RegulatoryPackage.reporting_date == MARCH,
            RegulatoryPackage.status != "superseded",
        )
    )
    assert current_count == 2


def test_regenerate_solo_does_not_supersede_consolidated(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _liquidity_run(db_session, MARCH)

    _generate(db_session, "LCR-NSFR", MARCH, basis="solo")
    consolidated_v1 = _generate(db_session, "LCR-NSFR", MARCH, basis="consolidated")

    solo_v2 = _generate(db_session, "LCR-NSFR", MARCH, basis="solo")

    assert solo_v2.version == 2
    # The consolidated chain is untouched by the solo regeneration.
    db_session.refresh(consolidated_v1)
    assert consolidated_v1.status == "generated"
    assert consolidated_v1.version == 1
    consolidated_current = _current(db_session, "LCR-NSFR", MARCH, "consolidated")
    assert consolidated_current is not None
    assert consolidated_current.id == consolidated_v1.id


# --------------------------------------------------------------------------
# Item 1 — DBK daily family
# --------------------------------------------------------------------------


def test_dbk_daily_next_business_day_skips_weekends() -> None:
    rule = daily_next_business_day(10, 0)
    # Friday 2026-03-27 -> Monday 2026-03-30 (T+1 rolls over the weekend).
    assert rule(date(2026, 3, 27)) == date(2026, 3, 30)
    # A weekday rolls to the next calendar day.
    assert rule(date(2026, 3, 30)) == date(2026, 3, 31)
    definition = get_definition("DBK-DAILY")
    assert definition is not None
    assert definition.frequency == "daily"
    assert definition.due_time == "10:00"
    assert definition.event_driven is False


def test_dbk_generation_requires_fx_canonical_data(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    with pytest.raises(HTTPException) as exc_info:
        _generate(db_session, "DBK-DAILY", MARCH)
    assert exc_info.value.status_code == 409
    assert "no_canonical_data" in str(exc_info.value.detail)


def test_dbk_generation_builds_nop_and_contingents_sections(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _fx_run(db_session, MARCH)

    package = _generate(db_session, "DBK-DAILY", MARCH)
    assert package.return_family == "dbk"
    assert package.frequency == "daily"
    snapshot = package.snapshot
    assert snapshot["template_id"] == "bog-dbk-daily-v1"
    assert snapshot["fidelity"] == "REPRESENTATIVE"
    sections = {section["code"]: section for section in snapshot["sections"]}
    assert set(sections) == {"nop_by_currency", "nop_aggregate", "contingents"}
    assert sections["nop_by_currency"]["rows"], "FX positions produce per-currency rows"
    # Contingents are honestly empty (off-balance data not carried by the FX run).
    assert sections["contingents"]["rows"] == []
    assert "no_canonical_data" not in str(snapshot)
    totals = {row["code"]: row for row in snapshot["totals"]}
    assert "nop_ghs" in totals and "nop_pct_nof" in totals


def test_daily_obligations_are_windowed_not_expanded(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    as_of = date(2026, 3, 31)

    result = calendar.list_obligations(
        db_session, MAKER, SAMPLE_BANK_ID, horizon_months=3, as_of=as_of
    )
    dbk = [item for item in result.obligations if item.return_code == "DBK-DAILY"]
    # A bounded trailing business-day window (5), not one row per day of the year.
    assert len(dbk) == 5
    for item in dbk:
        assert item.frequency == "daily"
        assert item.due_time == "10:00"
        assert item.basis == "solo"
        assert item.due_date > item.reporting_date

    wider = calendar.list_obligations(
        db_session, MAKER, SAMPLE_BANK_ID, horizon_months=12, as_of=as_of
    )
    wider_dbk = [item for item in wider.obligations if item.return_code == "DBK-DAILY"]
    # The daily window is independent of the horizon.
    assert len(wider_dbk) == 5


# --------------------------------------------------------------------------
# Item 5 — T-1 comparative column
# --------------------------------------------------------------------------


def _comparative(package: RegulatoryPackage) -> dict[str, dict]:
    section = next(s for s in package.snapshot["sections"] if s["code"] == "headline_comparative")
    return {row["code"]: row for row in section["rows"]}


def test_prior_period_column_blank_on_first_period(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _liquidity_run(db_session, MARCH)

    package = _generate(db_session, "LCR-NSFR", MARCH)
    rows = _comparative(package)
    assert rows, "headline_comparative section is emitted"
    # No prior period exists -> every prior_value is blank, never fabricated.
    assert all(row["prior_value"] is None for row in rows.values())
    assert "prior_period_reporting_date" not in package.snapshot["metadata"]


def test_prior_period_column_populated_on_second_period(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _liquidity_run(db_session, FEBRUARY)
    _liquidity_run(db_session, MARCH)

    first = _generate(db_session, "LCR-NSFR", FEBRUARY)
    first_totals = {row["code"]: row["value"] for row in first.snapshot["totals"]}

    second = _generate(db_session, "LCR-NSFR", MARCH)
    rows = _comparative(second)
    # Each headline total's prior_value equals the February package's value.
    assert rows["hqla_total_ghs"]["prior_value"] == first_totals["hqla_total_ghs"]
    assert rows["lcr_pct"]["prior_value"] == first_totals["lcr_pct"]
    assert all(rows[code]["prior_value"] == first_totals[code] for code in first_totals)
    assert second.snapshot["metadata"]["prior_period_reporting_date"] == (FEBRUARY.isoformat())


def test_prior_period_comparative_is_basis_scoped(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    _liquidity_run(db_session, FEBRUARY)
    _liquidity_run(db_session, MARCH)

    # A February SOLO package must not seed a March CONSOLIDATED comparative.
    _generate(db_session, "LCR-NSFR", FEBRUARY, basis="solo")
    march_consolidated = _generate(db_session, "LCR-NSFR", MARCH, basis="consolidated")
    rows = _comparative(march_consolidated)
    assert all(row["prior_value"] is None for row in rows.values())


# --------------------------------------------------------------------------
# Item 7 — bank-level deadline overrides
# --------------------------------------------------------------------------


def test_deadline_override_changes_due_date(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    as_of = date(2026, 3, 15)

    baseline = calendar.list_obligations(
        db_session, MAKER, SAMPLE_BANK_ID, horizon_months=1, as_of=as_of
    )
    bsd2_default = next(item for item in baseline.obligations if item.return_code == "CAR-RWA")
    # Registry default is day 14 of the month after the reporting date.
    assert bsd2_default.due_date.day == 14

    reporting_settings.put_reporting_settings(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        ReportingSettingsPut(deadline_overrides={"CAR-RWA": 21}),
    )

    overridden = calendar.list_obligations(
        db_session, MAKER, SAMPLE_BANK_ID, horizon_months=1, as_of=as_of
    )
    bsd2_over = next(item for item in overridden.obligations if item.return_code == "CAR-RWA")
    assert bsd2_over.reporting_date == bsd2_default.reporting_date
    assert bsd2_over.due_date.day == 21
    # Returns without an override keep the registry default.
    bsd3_over = next(item for item in overridden.obligations if item.return_code == "LCR-NSFR")
    bsd3_default = next(item for item in baseline.obligations if item.return_code == "LCR-NSFR")
    assert bsd3_over.due_date == bsd3_default.due_date


def test_reporting_settings_get_defaults_empty_then_round_trips(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)

    empty = reporting_settings.get_reporting_settings(db_session, MAKER, SAMPLE_BANK_ID)
    assert empty.deadline_overrides == {}

    reporting_settings.put_reporting_settings(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        ReportingSettingsPut(deadline_overrides={"CAR-RWA": 14, "FX-NOP": 10}),
    )
    stored = reporting_settings.get_reporting_settings(db_session, MAKER, SAMPLE_BANK_ID)
    assert stored.deadline_overrides == {"CAR-RWA": 14, "FX-NOP": 10}


def test_reporting_settings_rejects_out_of_range_day() -> None:
    with pytest.raises(ValueError, match="between 1 and 31"):
        ReportingSettingsPut(deadline_overrides={"CAR-RWA": 40})
