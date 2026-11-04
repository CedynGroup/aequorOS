from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import ModuleScope, RoleBundle, SensitivityScope
from app.credit.schemas.credit_params import (
    CrmHaircutUpdate,
    EclAssumptionEntry,
    EclAssumptionUpdate,
)
from app.credit.service import credit_params
from app.data_engine.service import reconciliation
from app.liquidity.schemas.liquidity_thresholds import (
    LiquidityHaircutUpdate,
    LiquidityThresholdUpdate,
)
from app.liquidity.service import liquidity_thresholds
from app.models import Bank, CurrentFinancialFact, Job, User
from tests.fixtures.canonical_bank_fixture import (
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.support.factories.authorization import grant_institution_authority
from tests.support.factories.canonical import FIXTURE_AS_OF
from tests.support.helpers import ORG_1, USER_1

CHECKER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


def _seed_live_input(db: Session) -> TenantContext:
    materialize_canonical_test_book(db)
    db.add(
        User(
            id=CHECKER,
            organization_id=ORG_1,
            email="checker@bank.test",
            display_name="Fixture checker",
            is_active=True,
        )
    )
    db.flush()
    for user, bundle in ((USER_1, RoleBundle.ANALYST), (CHECKER, RoleBundle.APPROVER)):
        grant_institution_authority(
            db,
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            user_id=user,
            bundle=bundle,
            module=ModuleScope.DATA,
            sensitivity=SensitivityScope.CONFIDENTIAL,
        )
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
    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1, authorization_version=1)


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
    previous_job = _refresh(db_session).id
    exception = reconciliation.propose_exception(
        db_session,
        ctx,
        bank,
        reason="Approved temporary difference",
        max_gap_fraction=Decimal("0.2"),
        effective_from=date(2026, 1, 1),
    )
    assert _refresh(db_session).id == previous_job
    checker = TenantContext(organization_id=ORG_1, actor_user_id=CHECKER, authorization_version=1)
    reconciliation.approve_exception(
        db_session,
        checker,
        bank,
        exception.id,
        approved_by="Risk committee",
    )
    assert _refresh(db_session).payload["reason"] == (
        f"reconciliation exception granted:{exception.id}"
    )

    reconciliation.revoke_exception(
        db_session,
        checker,
        bank,
        exception.id,
        revoked_by="Risk committee",
        reason="Difference resolved",
    )
    assert _refresh(db_session).payload["reason"] == (
        f"reconciliation exception revoked:{exception.id}"
    )
