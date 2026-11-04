"""Tests for test_behavioral_liquidity."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.liquidity.service.behavioral_liquidity import get_behavioral_liquidity_report
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.support.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture

CTX = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=None)


def test_behavioral_liquidity_report_reads_canonical_deposit_history(db_session: Session) -> None:
    materialize_canonical_test_book(db_session)
    seed_canonical_fixture(db_session, organization_id=DEMO_ORG_ID, bank_id=SAMPLE_BANK_ID)
    db_session.commit()

    report = get_behavioral_liquidity_report(db_session, CTX, SAMPLE_BANK_ID)

    assert report.as_of_date == FIXTURE_AS_OF
    assert report.segments
    assert {segment.dimension for segment in report.segments} <= {
        "product",
        "customer_segment",
        "concentration_group",
        "branch",
    }
