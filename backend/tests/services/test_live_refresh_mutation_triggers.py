from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, CurrentFinancialFact, Job
from app.schemas.credit_params import (
    CrmHaircutUpdate,
    EclAssumptionEntry,
    EclAssumptionUpdate,
)
from app.schemas.liquidity_thresholds import (
    LiquidityHaircutUpdate,
    LiquidityThresholdUpdate,
)
from app.services import credit_params, liquidity_thresholds, reconciliation
from tests.api.helpers import ORG_1, USER_1
from tests.factories.canonical import FIXTURE_AS_OF
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)


def _seed_live_input(db: Session) -> TenantContext:
    materialize_canonical_test_book(db)
    db.add(
        CurrentFinancialFact(
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            source_as_of_date=FIXTURE_AS_OF,
            source_generation=1,
            fact_group="balance_sheet",
            category="cash_vault",
            amount=Decimal("1"),
            currency="GHS",
        )
    )
    db.commit()
    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _refresh(db: Session) -> Job:
    db.expire_all()
    return db.scalars(
        select(Job)
        .where(Job.job_type == "pipeline_refresh", Job.status == "queued")
        .order_by(Job.queued_at.desc())
        .limit(1)
    ).one()


def test_live_consumed_governed_mutations_refresh_the_affected_bank(
    db_session: Session,
) -> None:
    ctx = _seed_live_input(db_session)

    credit_params.update_ecl_register(
        db_session,
        ctx,
        SAMPLE_BANK_ID,
        EclAssumptionUpdate(
            assumptions=[
                EclAssumptionEntry(
                    segment="ALL",
                    stage=1,
                    pd_pct=Decimal("1"),
                    lgd_pct=Decimal("40"),
                )
            ],
            effective_from=date(2026, 1, 1),
            approved_by="Model committee",
            reason="Annual calibration",
        ),
    )
    assert _refresh(db_session).payload["reason"] == "ECL assumption register updated"

    credit_params.update_crm_register(
        db_session,
        ctx,
        SAMPLE_BANK_ID,
        CrmHaircutUpdate(
            haircuts={"CASH": Decimal("1")},
            effective_from=date(2026, 1, 1),
            approved_by="Credit committee",
            reason="Annual calibration",
        ),
    )
    assert _refresh(db_session).payload["reason"] == "CRM haircut register updated"

    liquidity_thresholds.update_register(
        db_session,
        ctx,
        SAMPLE_BANK_ID,
        LiquidityThresholdUpdate(
            institution_class="bank",
            effective_from=date(2026, 1, 1),
            approved_by="Board minute",
            thresholds={"narrow_to_volatile": Decimal("81")},
            reason="Annual review",
        ),
    )
    assert _refresh(db_session).payload["reason"] == "liquidity threshold register updated"

    liquidity_thresholds.update_haircut_schedule(
        db_session,
        ctx,
        SAMPLE_BANK_ID,
        LiquidityHaircutUpdate(
            effective_from=date(2026, 1, 1),
            approved_by="Board minute",
            haircuts={"GOVERNMENT_SECURITIES": Decimal("2")},
            reason="Annual review",
        ),
    )
    assert _refresh(db_session).payload["reason"] == "liquidity haircut schedule updated"

    bank = db_session.get(Bank, SAMPLE_BANK_ID)
    assert bank is not None
    exception = reconciliation.grant_exception(
        db_session,
        ctx,
        bank,
        reason="Approved temporary difference",
        approved_by="Risk committee",
        max_gap_fraction=Decimal("0.2"),
        effective_from=date(2026, 1, 1),
    )
    assert _refresh(db_session).payload["reason"] == (
        f"reconciliation exception granted:{exception.id}"
    )

    reconciliation.revoke_exception(
        db_session,
        ctx,
        bank,
        exception.id,
        revoked_by="Risk committee",
        reason="Difference resolved",
    )
    assert _refresh(db_session).payload["reason"] == (
        f"reconciliation exception revoked:{exception.id}"
    )
