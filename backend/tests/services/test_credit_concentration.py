"""Concentration monitor service (credit PR-3): loading, regime basis, registers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, CanonicalPosition, CanonicalPositionSnapshot
from app.schemas.regulatory_credit import (
    ConcentrationLimitEntry,
    ConcentrationLimitUpdate,
    CreditThresholdUpdate,
)
from app.services import credit_concentration, credit_params, fact_derivation
from tests.api.helpers import ORG_1, USER_1
from tests.factories.canonical import FIXTURE_AS_OF, seed_canonical_fixture
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book

CTX = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _prepare(db_session: Session) -> Bank:
    materialize_canonical_test_book(db_session)
    db_session.flush()
    seed_canonical_fixture(db_session, organization_id=ORG_1, bank_id=SAMPLE_BANK_ID)
    fact_derivation.derive_facts(db_session, CTX, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    fact_derivation.derive_current_facts(db_session, CTX, SAMPLE_BANK_ID, FIXTURE_AS_OF)
    db_session.commit()
    bank = db_session.scalar(select(Bank).where(Bank.id == SAMPLE_BANK_ID))
    assert bank is not None
    return bank


def _stamp_employers(db_session: Session) -> None:
    """Add employer attributes to two loan snapshots, ingested-style."""
    rows = db_session.execute(
        select(CanonicalPositionSnapshot)
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .where(
            CanonicalPosition.position_type == "LOAN",
            CanonicalPositionSnapshot.superseded_by.is_(None),
        )
        .order_by(CanonicalPositionSnapshot.source_reference)
    ).scalars()
    for index, snapshot in enumerate(list(rows)[:2]):
        snapshot.attributes = {
            **(snapshot.attributes or {}),
            "employer": "Ghana Education Service" if index == 0 else "ACME Mining",
        }
    db_session.commit()


def test_monitor_reads_the_book_with_employer_coverage_disclosed(db_session: Session) -> None:
    bank = _prepare(db_session)
    _stamp_employers(db_session)
    result = credit_concentration.monitor(db_session, CTX, bank, FIXTURE_AS_OF)
    employer = result.dimension("employer")
    assert employer is not None
    assert employer.bucket_count == 2
    assert Decimal("0") < employer.coverage_pct < Decimal("100")
    single = result.dimension("single_name")
    assert single is not None
    assert single.coverage_pct == Decimal("100")
    # The bank fixture derives capital components, so the Tier-1 basis resolves.
    assert result.capital_base_ghs is not None


def test_board_limits_flow_from_the_register_into_breaches(db_session: Session) -> None:
    bank = _prepare(db_session)
    _stamp_employers(db_session)
    credit_params.update_concentration_limit_register(
        db_session,
        CTX,
        SAMPLE_BANK_ID,
        ConcentrationLimitUpdate(
            effective_from=date(2026, 1, 1),
            approved_by="Board Credit Committee",
            reason="Initial concentration limit structure",
            limits=[
                ConcentrationLimitEntry(
                    dimension="employer", limit_kind="share_of_book_pct", value=Decimal("0.5")
                )
            ],
        ),
    )
    result = credit_concentration.monitor(db_session, CTX, bank, FIXTURE_AS_OF)
    assert result.breaches, "a 0.5%-of-book employer limit must breach on this fixture"
    assert all(b.limit_status == "above_limit" for b in result.breaches)


def test_threshold_register_rejects_unknown_codes(db_session: Session) -> None:
    _prepare(db_session)
    with pytest.raises(HTTPException) as raised:
        credit_params.update_credit_threshold_register(
            db_session,
            CTX,
            SAMPLE_BANK_ID,
            CreditThresholdUpdate(
                effective_from=date(2026, 1, 1),
                approved_by="Board",
                reason="typo test",
                thresholds={"npl_bored_trigger_pct": Decimal("8")},
            ),
        )
    assert raised.value.status_code == 422
