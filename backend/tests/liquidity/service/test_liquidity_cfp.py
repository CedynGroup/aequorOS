"""EWI framework + CFP engine (LRMD 2026 ¶28(e)–(f), ¶70–77; Phase 2 item 3).

The evaluator computes the eight BoG starter indicators from canonical data
and stored runs — with honest ``no_data`` for signals whose inputs are not
ingested — and RAG states appear only once the Board sets trigger levels
through the audited register. The CFP lifecycle enforces the ¶72(a)–(g)
minimum contents at approval, maker-checker separation, and the ¶74
regulator-notification event on activation and de-escalation.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from queue import Queue
from threading import Event
from time import monotonic, sleep
from typing import Literal, cast
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from hypothesis import settings
from hypothesis.stateful import (
    RuleBasedStateMachine,
    rule,
    run_state_machine_as_test,  # pyright: ignore[reportUnknownVariableType] - Hypothesis factory generic
)
from sqlalchemy import Engine, event, select, text
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import (
    GrantorType,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.identity.service import authorization
from app.liquidity.schemas.liquidity_cfp import (
    CfpActivationCreate,
    CfpApprove,
    CfpContent,
    CfpEventListRead,
    CfpPut,
    CfpRead,
    CfpSummaryRead,
    EwiIndicatorUpdate,
    EwiRegisterPut,
)
from app.liquidity.service import liquidity_cfp, liquidity_ewi
from app.models import AuditEvent, BankReportingPeriod, ContingencyFundingPlan, Notification, User
from app.schemas.common import ErrorResponse
from tests.fixtures.canonical_bank_fixture import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    materialize_canonical_test_book,
)
from tests.services.test_le_and_lmt import _CanonicalSeeder
from tests.support.helpers import headers

MAKER = TenantContext(
    organization_id=DEMO_ORG_ID,
    actor_user_id=DEMO_USER_ID,
    authorization_version=1,
)
CHECKER = TenantContext(
    organization_id=DEMO_ORG_ID,
    actor_user_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
    authorization_version=1,
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


def _grant_cfp_authority(db: Session) -> None:
    for principal_id, bundle in (
        (MAKER.actor_user_id, RoleBundle.ANALYST),
        (MAKER.actor_user_id, RoleBundle.APPROVER),
        (CHECKER.actor_user_id, RoleBundle.APPROVER),
    ):
        assert principal_id is not None
        authorization.create_role_binding(
            db,
            organization_id=DEMO_ORG_ID,
            principal_user_id=principal_id,
            principal_type=PrincipalType.HUMAN,
            role_bundle=bundle,
            scope=authorization.BindingScope(
                InstitutionScope.INSTITUTION,
                SAMPLE_BANK_ID,
                ModuleScope.LIQUIDITY,
                SensitivityScope.CONFIDENTIAL,
            ),
            grantor=authorization.GrantorRef(GrantorType.SYSTEM, "cfp-test"),
            reason="exercise CFP lifecycle with exact maker and checker authority",
        )


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
        "EWI/D1",
        "DEPOSIT",
        Decimal("30000000"),
        counterparty=cp_a,
        deposit_account_type="CALL",
        interest_rate=Decimal("18"),
    )
    seeder.position(
        "EWI/D2",
        "DEPOSIT",
        Decimal("10000000"),
        counterparty=cp_b,
        deposit_account_type="FIXED",
        maturity=date(2026, 9, 30),
        interest_rate=Decimal("22"),
    )
    seeder.position("EWI/D3", "DEPOSIT", Decimal("10000000"), deposit_account_type="CURRENT")
    seeder.position(
        "EWI/D4",
        "DEPOSIT",
        Decimal("12500000"),
        counterparty=cp_c,
        currency="USD",
        deposit_account_type="FIXED",
        maturity=date(2026, 6, 29),
        interest_rate=Decimal("5"),
    )
    prior = _CanonicalSeeder(db, as_of=PRIOR_DATE)
    prior_cp = prior.counterparty("EWI/CP-P", "Alpha Holdings", "CORPORATE")
    prior.position("EWI/P-L1", "LOAN", Decimal("80000000"), ifrs9_stage=1)
    prior.position(
        "EWI/P-D1",
        "DEPOSIT",
        Decimal("20000000"),
        counterparty=prior_cp,
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
    materialize_canonical_test_book(db_session)
    _seed_ewi_book(db_session)

    evaluations = {
        entry.code: entry
        for entry in liquidity_ewi.evaluate_ewis(
            db_session,
            MAKER,
            liquidity_ewi._get_bank_or_404(db_session, MAKER, SAMPLE_BANK_ID),  # noqa: SLF001
            db_session.get(BankReportingPeriod, _period_id(db_session)),  # type: ignore[index]
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
    materialize_canonical_test_book(db_session)
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
    materialize_canonical_test_book(db_session)
    _seed_ewi_book(db_session)
    _ensure_checker(db_session)
    _grant_cfp_authority(db_session)
    period_id = _period_id(db_session)

    # Activation requires a Board-approved plan.
    with pytest.raises(HTTPException) as excinfo:
        liquidity_cfp.activate_cfp(
            db_session,
            CHECKER,
            SAMPLE_BANK_ID,
            CfpActivationCreate(reporting_period_id=period_id, reason="Funding shock"),
        )
    assert excinfo.value.detail["error_code"] == "no_approved_cfp"  # type: ignore[index]

    # A plan missing the intraday horizon cannot be approved (¶75(b)).
    incomplete = _full_content().model_copy(
        update={
            "funding_options": [
                option for option in _full_content().funding_options if option.horizon != "intraday"
            ]
        }
    )
    draft = liquidity_cfp.put_cfp(
        db_session, MAKER, SAMPLE_BANK_ID, CfpPut(content=incomplete, reason="First draft")
    )
    with pytest.raises(HTTPException) as excinfo:
        liquidity_cfp.approve_cfp(
            db_session,
            CHECKER,
            SAMPLE_BANK_ID,
            CfpApprove(
                expected_revision=draft.revision,
                approval_reference="BM-2026-08",
                reason="Annual approval",
            ),
        )
    assert excinfo.value.detail["error_code"] == "cfp_missing_intraday"  # type: ignore[index]

    # Complete the draft; the preparer cannot approve their own plan.
    draft = liquidity_cfp.put_cfp(
        db_session, MAKER, SAMPLE_BANK_ID, CfpPut(content=_full_content(), reason="Complete")
    )
    with pytest.raises(HTTPException) as excinfo:
        liquidity_cfp.approve_cfp(
            db_session,
            MAKER,
            SAMPLE_BANK_ID,
            CfpApprove(
                expected_revision=draft.revision,
                approval_reference="BM-2026-08",
                reason="Annual approval",
            ),
        )
    assert excinfo.value.detail["error_code"] == "self_approval"  # type: ignore[index]

    approved = liquidity_cfp.approve_cfp(
        db_session,
        CHECKER,
        SAMPLE_BANK_ID,
        CfpApprove(
            expected_revision=draft.revision,
            approval_reference="BM-2026-08",
            reason="Annual approval",
        ),
    )
    assert approved.status == "approved"
    assert approved.approval_expires_at is not None
    assert (approved.approval_expires_at - approved.approval_timestamp.date()).days == 365  # type: ignore[index]

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
    assert excinfo.value.detail["error_code"] == "cfp_already_active"  # type: ignore[index]

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


def test_cfp_api_refuses_unseen_revisions_until_checker_reloads(
    db_client: TestClient,
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    _ensure_checker(db_session)
    _grant_cfp_authority(db_session)
    maker = db_session.get(User, MAKER.actor_user_id)
    checker = db_session.get(User, CHECKER.actor_user_id)
    assert maker is not None and checker is not None
    maker_headers = headers(
        DEMO_ORG_ID, maker.id, authorization_version=maker.authorization_version
    )
    checker_headers = headers(
        DEMO_ORG_ID, checker.id, authorization_version=checker.authorization_version
    )
    url = f"/api/v1/banks/{SAMPLE_BANK_ID}/liquidity/cfp"
    first = db_client.put(
        url,
        headers=maker_headers,
        json={
            "content": _full_content().model_dump(mode="json"),
            "reason": "Initial draft",
        },
    )
    assert first.status_code == 200, first.text
    displayed = CfpSummaryRead.model_validate_json(
        db_client.get(url, headers=checker_headers).text
    ).current
    assert displayed is not None
    changed = _full_content()
    changed.funding_options[0].source = "Replacement intraday facility"
    latest_response = db_client.put(
        url,
        headers=maker_headers,
        json={
            "content": changed.model_dump(mode="json"),
            "reason": "Revised funding source",
        },
    )
    assert latest_response.status_code == 200, latest_response.text
    latest = CfpRead.model_validate_json(latest_response.text)
    assert latest.id == displayed.id
    assert latest.version == displayed.version
    assert latest.revision != displayed.revision
    payload = {
        "expected_revision": displayed.revision,
        "approval_reference": "Board minute",
        "reason": "Reviewed displayed content",
    }
    stale = db_client.post(f"{url}/approve", headers=checker_headers, json=payload)
    assert stale.status_code == 409, stale.text
    expected_error = {
        "error_code": "stale_cfp_revision",
        "message": (
            "The plan changed since you loaded it. Reload the latest plan "
            "and review it before approving."
        ),
    }
    assert (
        cast(object, ErrorResponse.model_validate_json(stale.text).error.details) == expected_error
    )
    reloaded = CfpSummaryRead.model_validate_json(db_client.get(url, headers=checker_headers).text)
    assert reloaded.current is not None
    assert (
        reloaded.current.id,
        reloaded.current.revision,
        reloaded.current.content,
        reloaded.current.status,
        reloaded.current.approval_reference,
        reloaded.current.approved_by_user_id,
        reloaded.current.approval_timestamp,
        reloaded.approved,
    ) == (latest.id, latest.revision, changed, "draft", None, None, None, None)
    assert (
        CfpEventListRead.model_validate_json(
            db_client.get(f"{url}/events", headers=checker_headers).text
        ).events
        == []
    )
    assert (
        db_session.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "liquidity_cfp.approved",
            )
        )
        is None
    )
    unbound = db_client.post(
        f"{url}/approve",
        headers=checker_headers,
        json={
            "approval_reference": "Board minute",
            "reason": "No displayed revision",
        },
    )
    assert unbound.status_code == 422
    payload["expected_revision"] = reloaded.current.revision
    approved_response = db_client.post(f"{url}/approve", headers=checker_headers, json=payload)
    assert approved_response.status_code == 200, approved_response.text
    approved = CfpRead.model_validate_json(approved_response.text)
    assert approved.status == "approved"
    assert approved.content == changed
    replacement_response = db_client.put(
        url,
        headers=maker_headers,
        json={
            "content": changed.model_dump(mode="json"),
            "reason": "Next version",
        },
    )
    assert replacement_response.status_code == 200, replacement_response.text
    replacement = CfpRead.model_validate_json(replacement_response.text)
    assert replacement.version == displayed.version + 1
    stale = db_client.post(f"{url}/approve", headers=checker_headers, json=payload)
    assert stale.status_code == 409
    assert (
        cast(object, ErrorResponse.model_validate_json(stale.text).error.details) == expected_error
    )
    persisted = CfpSummaryRead.model_validate_json(db_client.get(url, headers=checker_headers).text)
    assert persisted.current is not None and persisted.approved is not None
    assert (
        persisted.current.revision,
        persisted.current.status,
        persisted.current.content,
        persisted.approved.revision,
        persisted.approved.status,
        persisted.approved.content,
    ) == (replacement.revision, "draft", changed, approved.revision, "approved", changed)


def _wait_for_plan_row_lock(database: Engine, pid: int) -> None:
    deadline = monotonic() + 10
    while monotonic() < deadline:
        with Session(database) as observer:
            blocked = cast(
                object,
                observer.scalar(
                    text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                    {"pid": pid},
                ),
            )
        if blocked is True:
            break
        sleep(0.01)
    else:
        pytest.fail("The second CFP action did not wait for the plan row lock.")


def _assert_serialized_cfp_results(
    first_action: Literal["save", "approve"],
    draft: CfpRead,
    changed: CfpContent,
    first_result: Future[CfpRead],
    second_result: Future[CfpRead],
) -> None:
    completed = first_result.result(timeout=10)
    if first_action == "save":
        with pytest.raises(HTTPException) as refused:
            second_result.result(timeout=10)
        assert refused.value.status_code == 409
        assert cast(object, refused.value.detail) == {
            "error_code": "stale_cfp_revision",
            "message": (
                "The plan changed since you loaded it. Reload the latest plan "
                "and review it before approving."
            ),
        }
        assert completed.status == "draft"
    else:
        saved = second_result.result(timeout=10)
        assert completed.status == "approved"
        assert completed.content == draft.content
        assert saved.status == "draft"
        assert saved.version == draft.version + 1
        assert saved.content == changed


@pytest.mark.committing_db
@pytest.mark.parametrize("first_action", ["save", "approve"])
def test_cfp_edit_and_approval_serialize_on_the_displayed_revision(
    db_session: Session,
    first_action: Literal["save", "approve"],
) -> None:
    database = db_session.get_bind()
    if not isinstance(database, Engine) or database.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locks and independent connections are required.")
    materialize_canonical_test_book(db_session)
    _ensure_checker(db_session)
    _grant_cfp_authority(db_session)
    draft = liquidity_cfp.put_cfp(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        CfpPut(
            content=_full_content(),
            reason="Initial draft",
        ),
    )
    changed = _full_content()
    changed.funding_options[0].source = "Replacement intraday facility"
    entered, release = Event(), Event()
    waiting_pids: Queue[int] = Queue()

    def pause_after_loading_plan(_db: Session, instance: object) -> None:
        if not isinstance(instance, ContingencyFundingPlan) or entered.is_set():
            return
        entered.set()
        if not release.wait(15):
            raise RuntimeError("The test did not release the first CFP action.")

    def run_action(db: Session, action: Literal["save", "approve"]) -> CfpRead:
        if action == "save":
            return liquidity_cfp.put_cfp(
                db,
                MAKER,
                SAMPLE_BANK_ID,
                CfpPut(
                    content=changed,
                    reason="Revised funding source",
                ),
            )
        return liquidity_cfp.approve_cfp(
            db,
            CHECKER,
            SAMPLE_BANK_ID,
            CfpApprove(
                expected_revision=draft.revision,
                approval_reference="Board",
                reason="Reviewed",
            ),
        )

    def first() -> CfpRead:
        with Session(database) as db:
            event.listen(db, "loaded_as_persistent", pause_after_loading_plan)
            return run_action(db, first_action)

    def second() -> CfpRead:
        with Session(database) as db:
            pid = cast(object, db.scalar(text("SELECT pg_backend_pid()")))
            assert isinstance(pid, int)
            waiting_pids.put(pid)
            return run_action(db, "approve" if first_action == "save" else "save")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result = pool.submit(first)
        try:
            assert entered.wait(10)
            second_result = pool.submit(second)
            pid = waiting_pids.get(timeout=10)
            _wait_for_plan_row_lock(database, pid)
        finally:
            release.set()
        _assert_serialized_cfp_results(first_action, draft, changed, first_result, second_result)
    db_session.expire_all()
    persisted = liquidity_cfp.get_cfp(db_session, CHECKER, SAMPLE_BANK_ID)
    assert persisted.current is not None
    assert persisted.current.content == changed
    assert persisted.current.status == "draft"
    assert (persisted.approved is not None) == (first_action == "approve")


def test_expired_approval_refuses_activation_without_event_but_allows_stand_down(
    db_session: Session,
) -> None:
    materialize_canonical_test_book(db_session)
    _seed_ewi_book(db_session)
    _ensure_checker(db_session)
    _grant_cfp_authority(db_session)
    period_id = _period_id(db_session)
    draft = liquidity_cfp.put_cfp(
        db_session, MAKER, SAMPLE_BANK_ID, CfpPut(content=_full_content(), reason="Draft")
    )
    approved = liquidity_cfp.approve_cfp(
        db_session,
        CHECKER,
        SAMPLE_BANK_ID,
        CfpApprove(
            expected_revision=draft.revision, approval_reference="Board minute", reason="Approved"
        ),
    )
    plan = db_session.get(ContingencyFundingPlan, approved.id)
    assert plan is not None
    plan.approval_expires_at = date.today() - timedelta(days=1)
    db_session.commit()
    payload = CfpActivationCreate(reporting_period_id=period_id, reason="Funding stress")
    with pytest.raises(HTTPException) as refused:
        liquidity_cfp.activate_cfp(db_session, CHECKER, SAMPLE_BANK_ID, payload)
    assert refused.value.status_code == 409
    assert refused.value.detail == {
        "error_code": "cfp_approval_expired",
        "message": "Renew Board approval before activating this plan.",
    }
    assert liquidity_cfp.list_events(db_session, MAKER, SAMPLE_BANK_ID).events == []
    assert plan.active is False
    # An existing activation must remain reversible after annual approval expires.
    plan.active = True
    db_session.commit()
    event = liquidity_cfp.de_escalate_cfp(db_session, CHECKER, SAMPLE_BANK_ID, payload)
    assert event.event_type == "de_escalated"
    assert event.approval_overdue is True
    assert plan.active is False


def test_cfp_generated_lifecycle_sequences(db_session: Session) -> None:
    class CfpMachine(RuleBasedStateMachine):
        def __init__(self) -> None:
            super().__init__()
            self.savepoint = db_session.connection().begin_nested()
            self.db = Session(
                bind=db_session.connection(), join_transaction_mode="create_savepoint"
            )
            materialize_canonical_test_book(self.db)
            _seed_ewi_book(self.db)
            _ensure_checker(self.db)
            _grant_cfp_authority(self.db)
            self.period = _period_id(self.db)
            self.revision = "not-loaded"
            self.state = "empty"
            self.active = False
            self.event_count = 0
            self.has_approval = False

        @rule()
        def save_draft(self) -> None:
            draft = liquidity_cfp.put_cfp(
                self.db,
                MAKER,
                SAMPLE_BANK_ID,
                CfpPut(content=_full_content(), reason="Draft revision"),
            )
            self.revision = draft.revision
            self.state = "draft"

        @rule()
        def maker_cannot_approve(self) -> None:
            with pytest.raises(HTTPException):
                liquidity_cfp.approve_cfp(
                    self.db,
                    MAKER,
                    SAMPLE_BANK_ID,
                    CfpApprove(
                        expected_revision=self.revision, approval_reference="Board", reason="Review"
                    ),
                )
            assert (
                len(liquidity_cfp.list_events(self.db, MAKER, SAMPLE_BANK_ID).events)
                == self.event_count
            )

        @rule()
        def approve(self) -> None:
            payload = CfpApprove(
                expected_revision=self.revision, approval_reference="Board", reason="Review"
            )
            if self.state != "draft":
                with pytest.raises(HTTPException):
                    liquidity_cfp.approve_cfp(self.db, CHECKER, SAMPLE_BANK_ID, payload)
                return
            approved = liquidity_cfp.approve_cfp(self.db, CHECKER, SAMPLE_BANK_ID, payload)
            self.revision = approved.revision
            self.state = "approved"
            self.has_approval = True

        @rule()
        def activate(self) -> None:
            payload = CfpActivationCreate(reporting_period_id=self.period, reason="Activate")
            if self.has_approval and not self.active:
                liquidity_cfp.activate_cfp(self.db, CHECKER, SAMPLE_BANK_ID, payload)
                self.active = True
                self.event_count += 1
            else:
                with pytest.raises(HTTPException):
                    liquidity_cfp.activate_cfp(self.db, CHECKER, SAMPLE_BANK_ID, payload)

        @rule()
        def stand_down(self) -> None:
            payload = CfpActivationCreate(reporting_period_id=self.period, reason="Stand down")
            if self.active:
                liquidity_cfp.de_escalate_cfp(self.db, CHECKER, SAMPLE_BANK_ID, payload)
                self.active = False
                self.event_count += 1
            else:
                with pytest.raises(HTTPException):
                    liquidity_cfp.de_escalate_cfp(self.db, CHECKER, SAMPLE_BANK_ID, payload)
            assert (
                len(liquidity_cfp.list_events(self.db, MAKER, SAMPLE_BANK_ID).events)
                == self.event_count
            )

        def teardown(self) -> None:
            self.db.close()
            self.savepoint.rollback()

    run_state_machine_as_test(
        CfpMachine,
        settings=settings(max_examples=10, stateful_step_count=12, derandomize=True, deadline=None),
    )
