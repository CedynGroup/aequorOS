"""EWI framework + CFP engine (LRMD 2026 ¶28(e)–(f), ¶70–77; Phase 2 item 3).

The evaluator computes the eight BoG starter indicators from canonical data
and stored runs — with honest ``no_data`` for signals whose inputs are not
ingested — and RAG states appear only once the Board sets trigger levels
through the audited register. The CFP lifecycle enforces the ¶72(a)–(g)
minimum contents at approval, maker-checker separation, and the ¶74
regulator-notification event on activation and de-escalation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import BankReportingPeriod, Notification, User
from app.schemas.liquidity_cfp import (
    CfpActivationCreate,
    CfpApprove,
    CfpContent,
    CfpPut,
    EwiIndicatorUpdate,
    EwiRegisterPut,
)
from app.services import liquidity_cfp, liquidity_ewi
from app.services.sample_bank_seed import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    seed_sample_bank,
)
from tests.services.test_le_and_lmt import _CanonicalSeeder

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
CHECKER = TenantContext(
    organization_id=DEMO_ORG_ID,
    actor_user_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
)
REPORTING_DATE = date(2026, 3, 31)
PRIOR_DATE = date(2026, 2, 28)


def _period_id(db: Session, period_end: date = REPORTING_DATE) -> UUID:
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == period_end,
        )
    )
    assert period_id is not None
    return period_id


def _ensure_checker(db: Session) -> None:
    if db.scalar(select(User.id).where(User.id == CHECKER.actor_user_id)) is None:
        db.add(
            User(
                id=CHECKER.actor_user_id,
                organization_id=DEMO_ORG_ID,
                email="demo.checker@example.test",
                display_name="Demo Checker",
                role="approver",
            )
        )
        db.commit()


def _seed_ewi_book(db: Session) -> None:
    """Current book: 100M loans (40M stage 3); 62.5M liabilities — 30M CALL +
    10M fixed (183d) + 10M retail CURRENT (no counterparty) + 12.5M-cedi USD
    fixed (90d). Prior month: 80M loans, one 20M deposit."""
    seeder = _CanonicalSeeder(db)
    cp_a = seeder.counterparty("EWI/CP-A", "Alpha Holdings", "CORPORATE")
    cp_b = seeder.counterparty("EWI/CP-B", "Beta Pension Trust", "NBFI")
    cp_c = seeder.counterparty("EWI/CP-C", "Gamma Trade Ltd", "CORPORATE")
    seeder.position("EWI/L1", "LOAN", Decimal("60000000"), ifrs9_stage=1)
    seeder.position("EWI/L2", "LOAN", Decimal("40000000"), ifrs9_stage=3)
    seeder.position(
        "EWI/D1", "DEPOSIT", Decimal("30000000"), counterparty=cp_a,
        deposit_account_type="CALL", interest_rate=Decimal("18"),
    )
    seeder.position(
        "EWI/D2", "DEPOSIT", Decimal("10000000"), counterparty=cp_b,
        deposit_account_type="FIXED", maturity=date(2026, 9, 30),
        interest_rate=Decimal("22"),
    )
    seeder.position("EWI/D3", "DEPOSIT", Decimal("10000000"), deposit_account_type="CURRENT")
    seeder.position(
        "EWI/D4", "DEPOSIT", Decimal("12500000"), counterparty=cp_c, currency="USD",
        deposit_account_type="FIXED", maturity=date(2026, 6, 29),
        interest_rate=Decimal("5"),
    )
    prior = _CanonicalSeeder(db, as_of=PRIOR_DATE)
    prior_cp = prior.counterparty("EWI/CP-P", "Alpha Holdings", "CORPORATE")
    prior.position("EWI/P-L1", "LOAN", Decimal("80000000"), ifrs9_stage=1)
    prior.position(
        "EWI/P-D1", "DEPOSIT", Decimal("20000000"), counterparty=prior_cp,
        deposit_account_type="CALL",
    )


def _full_content() -> CfpContent:
    return CfpContent.model_validate(
        {
            "ewi_triggers": [
                {
                    "indicator_code": "funding_concentration",
                    "trigger_condition": "Action threshold breached for 2 business days",
                }
            ],
            "funding_options": [
                {"horizon": "intraday", "source": "Central-bank intraday facility"},
                {"horizon": "up_to_1m", "source": "Interbank repo lines"},
            ],
            "action_plans": [
                {"side": "asset", "action": "Monetize HQLA buffer", "owner": "Treasury"},
                {
                    "side": "liability",
                    "action": "Draw committed lines",
                    "owner": "Treasury",
                },
            ],
            "alternative_sources": [
                {"source": "Parent-group facility", "conditions": "Board sign-off"}
            ],
            "escalation_procedures": [
                {
                    "priority": 1,
                    "stage": "Heightened monitoring",
                    "trigger": "Any indicator at watch",
                    "actions": "Daily liquidity call",
                    "owner": "Head of Treasury",
                }
            ],
            "key_relationships": [
                {
                    "institution": "Central bank domestic operations desk",
                    "role": "Lender of last resort contact",
                }
            ],
            "communication_plans": [
                {"audience": "regulator", "channel": "Formal letter + call", "owner": "CRO"},
                {"audience": "media", "channel": "Press office statement", "owner": "CEO"},
                {"audience": "internal", "channel": "Crisis bridge", "owner": "COO"},
            ],
        }
    )


def test_starters_evaluate_with_honest_gaps_and_no_invented_rag(db_session: Session) -> None:
    seed_sample_bank(db_session)
    _seed_ewi_book(db_session)

    evaluations = {
        entry.code: entry
        for entry in liquidity_ewi.evaluate_ewis(
            db_session,
            MAKER,
            liquidity_ewi._get_bank_or_404(db_session, MAKER, SAMPLE_BANK_ID),  # noqa: SLF001
            db_session.get(BankReportingPeriod, _period_id(db_session)),
        )
    }
    assert set(evaluations) == {starter.code for starter in liquidity_ewi.STARTER_INDICATORS}

    # Computable levels from the canonical book.
    assert evaluations["funding_concentration"].value == Decimal("84")
    assert evaluations["currency_mismatch"].value == Decimal("20")
    assert evaluations["weighted_liability_maturity"].value == Decimal("131.333333")
    assert evaluations["earnings_asset_quality"].value == Decimal("40")
    assert evaluations["funding_costs"].value == Decimal("15.666667")
    # Trend vs the prior month's canonical book: (100M - 80M) / 80M.
    assert evaluations["asset_growth_volatile_funding"].value == Decimal("25")
    assert evaluations["funding_concentration"].prior_value == Decimal("100")

    # No Board thresholds yet: values shown, no RAG claim invented.
    assert evaluations["funding_concentration"].status == "unconfigured"

    # Signals whose inputs are not ingested say so instead of fabricating.
    spreads = evaluations["debt_spreads"]
    assert spreads.status == "no_data"
    assert spreads.detail is not None and "market data" in spreads.detail
    assert liquidity_ewi.escalation_state(list(evaluations.values()), cfp_active=False) == (
        "normal"
    )


def test_board_thresholds_drive_rag_and_escalation(db_session: Session) -> None:
    seed_sample_bank(db_session)
    _seed_ewi_book(db_session)
    _ensure_checker(db_session)

    liquidity_ewi.update_register(
        db_session,
        CHECKER,
        SAMPLE_BANK_ID,
        EwiRegisterPut(
            indicators=[
                EwiIndicatorUpdate(
                    code="funding_concentration",
                    watch_threshold=Decimal("50"),
                    action_threshold=Decimal("90"),
                    recovery_plan_reference="Recovery plan §4.2 indicator R-7",
                ),
                EwiIndicatorUpdate(
                    code="currency_mismatch",
                    watch_threshold=Decimal("25"),
                    action_threshold=Decimal("40"),
                ),
                # Direction 'below': breach when maturity shortens under floor.
                EwiIndicatorUpdate(
                    code="weighted_liability_maturity",
                    watch_threshold=Decimal("200"),
                    action_threshold=Decimal("100"),
                ),
                EwiIndicatorUpdate(
                    code="earnings_asset_quality",
                    watch_threshold=Decimal("5"),
                    action_threshold=Decimal("20"),
                ),
                EwiIndicatorUpdate(
                    code="bog_balance_trend",
                    custom=True,
                    name="Central-bank settlement balance trend",
                    direction="below",
                    unit="ghs",
                ),
            ],
            approved_by="Board minute 2026-07 item 5",
            reason="Adopt EWI trigger framework",
        ),
    )

    dashboard = liquidity_cfp.ewi_dashboard(
        db_session, MAKER, SAMPLE_BANK_ID, _period_id(db_session)
    )
    states = {entry.code: entry for entry in dashboard.indicators}
    assert states["funding_concentration"].status == "watch"  # 84 in [50, 90)
    assert states["currency_mismatch"].status == "normal"  # 20 < 25
    assert states["weighted_liability_maturity"].status == "watch"  # 131 <= 200, > 100
    assert states["earnings_asset_quality"].status == "action"  # 40 >= 20
    assert states["funding_concentration"].recovery_plan_reference == (
        "Recovery plan §4.2 indicator R-7"
    )
    custom = states["bog_balance_trend"]
    assert custom.custom is True and custom.status == "no_data"
    assert dashboard.escalation_state == "escalation"

    # Register validation: unknown codes and mislabeled starters are rejected.
    with pytest.raises(HTTPException) as excinfo:
        liquidity_ewi.update_register(
            db_session,
            CHECKER,
            SAMPLE_BANK_ID,
            EwiRegisterPut(
                indicators=[EwiIndicatorUpdate(code="made_up_signal")],
                approved_by="Board",
                reason="x",
            ),
        )
    assert excinfo.value.status_code == 422


def test_cfp_lifecycle_maker_checker_completeness_and_74_notifications(
    db_session: Session,
) -> None:
    seed_sample_bank(db_session)
    _seed_ewi_book(db_session)
    _ensure_checker(db_session)
    period_id = _period_id(db_session)

    # Activation requires a Board-approved plan.
    with pytest.raises(HTTPException) as excinfo:
        liquidity_cfp.activate_cfp(
            db_session,
            CHECKER,
            SAMPLE_BANK_ID,
            CfpActivationCreate(reporting_period_id=period_id, reason="Funding shock"),
        )
    assert excinfo.value.detail["error_code"] == "no_approved_cfp"

    # A plan missing the intraday horizon cannot be approved (¶75(b)).
    incomplete = _full_content().model_copy(
        update={
            "funding_options": [
                option
                for option in _full_content().funding_options
                if option.horizon != "intraday"
            ]
        }
    )
    liquidity_cfp.put_cfp(
        db_session, MAKER, SAMPLE_BANK_ID, CfpPut(content=incomplete, reason="First draft")
    )
    with pytest.raises(HTTPException) as excinfo:
        liquidity_cfp.approve_cfp(
            db_session,
            CHECKER,
            SAMPLE_BANK_ID,
            CfpApprove(approval_reference="BM-2026-08", reason="Annual approval"),
        )
    assert excinfo.value.detail["error_code"] == "cfp_missing_intraday"

    # Complete the draft; the preparer cannot approve their own plan.
    liquidity_cfp.put_cfp(
        db_session, MAKER, SAMPLE_BANK_ID, CfpPut(content=_full_content(), reason="Complete")
    )
    with pytest.raises(HTTPException) as excinfo:
        liquidity_cfp.approve_cfp(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            CfpApprove(approval_reference="BM-2026-08", reason="Annual approval"),
        )
    assert excinfo.value.detail["error_code"] == "self_approval"

    approved = liquidity_cfp.approve_cfp(
        db_session,
        CHECKER,
        SAMPLE_BANK_ID,
        CfpApprove(approval_reference="BM-2026-08", reason="Annual approval"),
    )
    assert approved.status == "approved"
    assert approved.approval_expires_at is not None
    assert (approved.approval_expires_at - approved.approval_timestamp.date()).days == 365

    # Activation: ¶74 — EWI snapshot + regulator notification into the pipeline.
    event = liquidity_cfp.activate_cfp(
        db_session,
        CHECKER,
        SAMPLE_BANK_ID,
        CfpActivationCreate(
            reporting_period_id=period_id, reason="Severe deposit outflow underway."
        ),
    )
    assert event.event_type == "activated"
    assert {entry.code for entry in event.ewi_snapshot} >= {
        starter.code for starter in liquidity_ewi.STARTER_INDICATORS
    }
    assert event.regulator_notification_id is not None
    notification = db_session.get(Notification, event.regulator_notification_id)
    assert notification is not None
    assert notification.type == "cfp.activated"
    assert "LRMD ¶74" in notification.title
    # Jurisdiction is data: the seeded Ghana bank resolves its regulator name.
    assert "BoG" in notification.title

    dashboard = liquidity_cfp.ewi_dashboard(db_session, MAKER, SAMPLE_BANK_ID, period_id)
    assert dashboard.cfp_active is True
    assert dashboard.escalation_state == "cfp_active"

    with pytest.raises(HTTPException) as excinfo:
        liquidity_cfp.activate_cfp(
            db_session,
            CHECKER,
            SAMPLE_BANK_ID,
            CfpActivationCreate(reporting_period_id=period_id, reason="Again"),
        )
    assert excinfo.value.detail["error_code"] == "cfp_already_active"

    de_escalated = liquidity_cfp.de_escalate_cfp(
        db_session,
        CHECKER,
        SAMPLE_BANK_ID,
        CfpActivationCreate(reporting_period_id=period_id, reason="Outflows stabilized."),
    )
    assert de_escalated.event_type == "de_escalated"
    events = liquidity_cfp.list_events(db_session, MAKER, SAMPLE_BANK_ID).events
    assert [entry.event_type for entry in events] == ["de_escalated", "activated"]

    summary = liquidity_cfp.get_cfp(db_session, MAKER, SAMPLE_BANK_ID)
    assert summary.approved is not None and summary.approved.active is False
