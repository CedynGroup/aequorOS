"""Modules open on a graceful empty state, not a 409 (founder call 2026-08-20).

"No computed data yet" is a valid state, not a conflict: the live-view dashboards
return HTTP 200 ``{available: false, reason}`` so a module page opens with a clean
onboarding panel instead of a red error + a console 4xx. A bank with no ingested
facts exercises the path.

The second test is the other edge of the same envelope (BI Phase 0 item 2): a
bank whose LIVE plane is ready but whose latest business date has never been
minted as an official run must open on its live figures, not on
``financial_facts_missing`` — the official spine is not the live plane's source.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.db.session import get_sessionmaker
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CurrentFinancialFact,
    LiveMetric,
)
from app.services.reporting_periods import new_snapshot_period
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.fixtures.live_plane import materialize_live_plane
from tests.support.helpers import ORG_1, headers

#: The section each dashboard must populate from the live plane in current mode.
_SECTION_FIELDS = {
    "capital": ("rwa_composition", "credit_lines"),
    "liquidity": ("hqla_composition", None),
    "irr": ("gap_table", None),
    "fx": ("positions", None),
    "ftp": ("products", None),
}


def _fresh_bank() -> str:
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)  # init app engine + reference rows
        bank = Bank(
            organization_id=ORG_1,
            name="Empty State Bank",
            short_name="ESB",
            currency="GHS",
            jurisdiction_code="GH",
            license_type="universal",
            institution_type="universal_bank",
        )
        session.add(bank)
        session.commit()
        return bank.id
    finally:
        session.close()


def test_dashboards_return_200_unavailable_when_no_data(db_client) -> None:  # noqa: ANN001
    bank_id = _fresh_bank()
    for module in ("capital", "liquidity", "irr", "fx", "ftp"):
        resp = db_client.get(f"/api/v1/banks/{bank_id}/{module}/dashboard", headers=headers())
        assert resp.status_code == 200, f"{module}: {resp.status_code} {resp.text}"
        body = resp.json()
        assert body["available"] is False, f"{module}: {body}"
        assert body["error_code"] == "current_facts_missing"
        assert body["reason"]


def _live_plane_ahead_of_the_official_spine() -> tuple[str, str]:
    """The Sample Bank with its live plane on a date that has NO official facts.

    Mirrors the latest official period into the live plane, then moves the live
    plane forward to a mid-month business date exactly as a daily feed would:
    the refresh mints the snapshot period row (``new_snapshot_period``, the same
    constructor ``pipeline._ensure_live_period`` uses) and stamps every current
    fact and live metric with that date, while ``bank_financial_facts`` — which
    only an official run writes — carries nothing for it.

    Returns ``(live_date_iso, live_period_id)``.
    """
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        materialize_live_plane(session, organization_id=DEMO_ORG_ID, bank_id=SAMPLE_BANK_ID)
        latest_official = session.scalars(
            select(BankReportingPeriod)
            .where(
                BankReportingPeriod.organization_id == DEMO_ORG_ID,
                BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            )
            .order_by(BankReportingPeriod.period_end.desc())
            .limit(1)
        ).one()
        live_date = latest_official.period_end + timedelta(days=15)
        live_period = new_snapshot_period(
            organization_id=DEMO_ORG_ID, bank_id=SAMPLE_BANK_ID, as_of=live_date
        )
        session.add(live_period)
        session.flush()
        session.execute(
            update(CurrentFinancialFact)
            .where(
                CurrentFinancialFact.organization_id == DEMO_ORG_ID,
                CurrentFinancialFact.bank_id == SAMPLE_BANK_ID,
            )
            .values(source_as_of_date=live_date)
        )
        session.execute(
            update(LiveMetric)
            .where(
                LiveMetric.organization_id == DEMO_ORG_ID,
                LiveMetric.bank_id == SAMPLE_BANK_ID,
            )
            .values(source_as_of_date=live_date, source_fact_period_id=live_period.id)
        )
        session.commit()
        official_rows_for_live_date = session.scalar(
            select(BankFinancialFact.id)
            .where(
                BankFinancialFact.organization_id == DEMO_ORG_ID,
                BankFinancialFact.bank_id == SAMPLE_BANK_ID,
                BankFinancialFact.reporting_period_id == live_period.id,
            )
            .limit(1)
        )
        assert official_rows_for_live_date is None, "the live date must have no official facts"
        return live_date.isoformat(), str(live_period.id)
    finally:
        session.close()


@pytest.mark.parametrize("module", ("capital", "liquidity", "irr", "fx", "ftp"))
def test_current_mode_computes_from_the_live_plane_without_an_official_run(
    db_client,  # noqa: ANN001
    module: str,
) -> None:
    """Live plane ready + no official run for its date → 200 with live sections.

    Before BI Phase 0 item 2 the dashboards resolved the period from the live
    plane and then recomputed from the batch's ``bank_financial_facts`` for it,
    which is empty here, so every module answered ``financial_facts_missing``.
    """
    live_date, live_period_id = _live_plane_ahead_of_the_official_spine()

    resp = db_client.get(f"/api/v1/banks/{SAMPLE_BANK_ID}/{module}/dashboard", headers=headers())

    assert resp.status_code == 200, f"{module}: {resp.status_code} {resp.text}"
    body = resp.json()
    assert body.get("available", True) is not False, f"{module}: {body}"
    assert body.get("error_code") != "financial_facts_missing", f"{module}: {body}"
    # The headline is the live plane's date, computed inline — never a stored run.
    assert body["period"]["id"] == live_period_id
    assert body["period"]["period_end"] == live_date
    assert body["stored"] is False
    assert body["latest_run_id"] is None
    field, nested = _SECTION_FIELDS[module]
    section = body[field][nested] if nested else body[field]
    assert section, f"{module}: {field} is empty — the live plane was not used"
    # The trend stays on the official spine: the live date has no official
    # picture, so it is not a point, and no point is invented for it.
    assert live_period_id not in {point["reporting_period_id"] for point in body["trend"]}
    assert body["trend"], f"{module}: the official month-ends still trend"
