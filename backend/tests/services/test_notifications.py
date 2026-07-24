from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import BankReportingPeriod, Notification, User
from app.schemas.regulatory_liquidity import RegulatoryRunCreate
from app.schemas.regulatory_reporting import (
    PackageApprovalDecisionCreate,
    PackageApprovalRequestCreate,
    RegulatoryPackageCreate,
)
from app.services import notifications, regulatory_liquidity, reporting_deadline_scan
from app.services.regulatory_reporting import generation, validation, workflow
from app.services.sample_bank_seed import (
    DEMO_ORG_ID,
    DEMO_USER_ID,
    SAMPLE_BANK_ID,
    seed_sample_bank,
)
from tests.api.helpers import ORG_1, ORG_2, USER_1, USER_2

MAKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=DEMO_USER_ID)
ADMIN_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
APPROVER_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
ANALYST_ID = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
VIEWER_ID = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
INACTIVE_APPROVER_ID = UUID("abababab-abab-4bab-8bab-abababababab")
CHECKER = TenantContext(organization_id=DEMO_ORG_ID, actor_user_id=APPROVER_ID)
REPORTING_DATE = date(2026, 3, 31)
BSD3_DUE_DATE = date(2026, 4, 9)  # monthly_day(9) for the 2026-03-31 period

_ROLE_USERS: tuple[tuple[UUID, str, str, bool], ...] = (
    (ADMIN_ID, "admin", "demo.admin@example.test", True),
    (APPROVER_ID, "approver", "demo.approver@example.test", True),
    (ANALYST_ID, "analyst", "demo.analyst@example.test", True),
    (VIEWER_ID, "viewer", "demo.viewer@example.test", True),
    (INACTIVE_APPROVER_ID, "approver", "demo.approver.inactive@example.test", False),
)


def _ensure_role_users(db: Session, organization_id: UUID) -> None:
    for user_id, role, email, is_active in _ROLE_USERS:
        if db.scalar(select(User.id).where(User.id == user_id)) is None:
            db.add(
                User(
                    id=user_id,
                    organization_id=organization_id,
                    email=email,
                    display_name=f"Demo {role.title()}",
                    role=role,
                    is_active=is_active,
                )
            )
    db.commit()


def _notification_rows(db: Session, type_: str) -> list[Notification]:
    return list(
        db.scalars(select(Notification).where(Notification.type == type_).order_by(Notification.id))
    )


def _total_notifications(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Notification)) or 0


# ---------------------------------------------------------------------------
# emit / list / mark-read (conftest-seeded tenants; no bank needed)
# ---------------------------------------------------------------------------


def test_emit_role_fanout_targets_role_and_higher_active_users(db_session: Session) -> None:
    _ensure_role_users(db_session, ORG_1)
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    rows = notifications.emit(
        db_session,
        ctx,
        type="reporting.package.pending_approval",
        severity="warning",
        title="BSD3 2026-03-31 awaits approval",
        body="Version 1 is pending an approval decision.",
        recipient_role="approver",
    )
    db_session.commit()
    # Approver fan-out reaches approver + admin only — never analyst/viewer,
    # never a deactivated approver.
    assert {row.recipient_user_id for row in rows} == {ADMIN_ID, APPROVER_ID}
    assert all(row.severity == "warning" for row in rows)
    assert all(row.read_at is None for row in rows)


def test_emit_unknown_role_is_rejected(db_session: Session) -> None:
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    with pytest.raises(ValueError, match="Unknown recipient role"):
        notifications.emit(
            db_session,
            ctx,
            type="reporting.test",
            severity="info",
            title="t",
            body="b",
            recipient_role="supervisor",
        )


def test_list_visibility_user_specific_vs_org_wide_vs_other_user(db_session: Session) -> None:
    _ensure_role_users(db_session, ORG_1)
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    notifications.emit(
        db_session, ctx, type="reporting.test.org_wide", severity="info", title="org", body="b"
    )
    notifications.emit(
        db_session,
        ctx,
        type="reporting.test.mine",
        severity="info",
        title="mine",
        body="b",
        recipient_user_id=USER_1,
    )
    notifications.emit(
        db_session,
        ctx,
        type="reporting.test.other",
        severity="info",
        title="other",
        body="b",
        recipient_user_id=APPROVER_ID,
    )
    db_session.commit()

    rows, total, unread = notifications.list_notifications(db_session, ctx)
    assert total == 2
    assert unread == 2
    assert {row.type for row in rows} == {"reporting.test.org_wide", "reporting.test.mine"}
    # Newest first.
    assert [row.type for row in rows] == ["reporting.test.mine", "reporting.test.org_wide"]

    other_ctx = TenantContext(organization_id=ORG_1, actor_user_id=APPROVER_ID)
    other_rows, other_total, _ = notifications.list_notifications(db_session, other_ctx)
    assert other_total == 2
    assert {row.type for row in other_rows} == {"reporting.test.org_wide", "reporting.test.other"}

    # A different tenant sees nothing.
    stranger = TenantContext(organization_id=ORG_2, actor_user_id=USER_2)
    _, stranger_total, stranger_unread = notifications.list_notifications(db_session, stranger)
    assert stranger_total == 0
    assert stranger_unread == 0


def test_mark_read_and_mark_all_read_touch_only_visible_rows(db_session: Session) -> None:
    _ensure_role_users(db_session, ORG_1)
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    org_wide = notifications.emit(
        db_session, ctx, type="reporting.test.org_wide", severity="info", title="org", body="b"
    )[0]
    notifications.emit(
        db_session,
        ctx,
        type="reporting.test.other",
        severity="info",
        title="other",
        body="b",
        recipient_user_id=APPROVER_ID,
    )
    db_session.commit()

    marked = notifications.mark_read(db_session, ctx, org_wide.id)
    assert marked.read_at is not None

    # Another user's targeted row is invisible to the actor: 404.
    other_row = _notification_rows(db_session, "reporting.test.other")[0]
    with pytest.raises(HTTPException) as exc_info:
        notifications.mark_read(db_session, ctx, other_row.id)
    assert exc_info.value.status_code == 404

    notifications.emit(
        db_session,
        ctx,
        type="reporting.test.mine",
        severity="info",
        title="mine",
        body="b",
        recipient_user_id=USER_1,
    )
    db_session.commit()
    assert notifications.mark_all_read(db_session, ctx) == 1  # org-wide already read
    _, _, unread = notifications.list_notifications(db_session, ctx)
    assert unread == 0
    # The other user's row was never touched.
    assert _notification_rows(db_session, "reporting.test.other")[0].read_at is None
    # Nothing left to mark.
    assert notifications.mark_all_read(db_session, ctx) == 0

    unread_rows, unread_total, _ = notifications.list_notifications(
        db_session, ctx, unread_only=True
    )
    assert unread_rows == []
    assert unread_total == 0


# ---------------------------------------------------------------------------
# Workflow emission hooks (sample bank + baseline run, like the workflow suite)
# ---------------------------------------------------------------------------


def _seed_with_baseline_run(db: Session) -> None:
    seed_sample_bank(db)
    _ensure_role_users(db, DEMO_ORG_ID)
    period_id = db.scalar(
        select(BankReportingPeriod.id).where(
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.period_end == REPORTING_DATE,
        )
    )
    assert period_id is not None
    run = regulatory_liquidity.create_liquidity_run(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryRunCreate(
            module="liquidity", reporting_period_id=period_id, scenario_code="baseline"
        ),
    )
    assert run.status == "succeeded"


def _generate(db: Session):
    return generation.generate_package(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        RegulatoryPackageCreate(return_code="BSD3", reporting_date=REPORTING_DATE),
    )


def _drive_to_submitted(db: Session) -> UUID:
    package = _generate(db)
    validation.validate_package(db, MAKER, SAMPLE_BANK_ID, package.id)
    workflow.request_approval(db, MAKER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate())
    workflow.decide_approval(
        db,
        CHECKER,
        SAMPLE_BANK_ID,
        package.id,
        PackageApprovalDecisionCreate(action="approved"),
    )
    workflow.submit_package(
        db,
        MAKER,
        SAMPLE_BANK_ID,
        package.id,
        channel="manual",
        external_ref=f"BOG-RCPT-{package.version:04d}",
    )
    return package.id


def test_approval_request_and_decision_emit_notifications(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)
    package = _generate(db_session)
    validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    workflow.request_approval(
        db_session, MAKER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate()
    )

    pending = _notification_rows(db_session, "reporting.package.pending_approval")
    assert {row.recipient_user_id for row in pending} == {ADMIN_ID, APPROVER_ID}
    assert all(row.severity == "warning" for row in pending)
    assert all("BSD3" in row.title and "2026-03-31" in row.title for row in pending)
    assert all(row.entity_id == package.id for row in pending)

    workflow.decide_approval(
        db_session,
        CHECKER,
        SAMPLE_BANK_ID,
        package.id,
        PackageApprovalDecisionCreate(action="approved"),
    )
    approved = _notification_rows(db_session, "reporting.package.approved")
    assert [row.recipient_user_id for row in approved] == [DEMO_USER_ID]
    assert approved[0].severity == "info"
    assert "BSD3" in approved[0].title and "2026-03-31" in approved[0].title


def test_approval_rejection_notifies_generator_with_reason(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)
    package = _generate(db_session)
    validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    workflow.request_approval(
        db_session, MAKER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate()
    )
    workflow.decide_approval(
        db_session,
        CHECKER,
        SAMPLE_BANK_ID,
        package.id,
        PackageApprovalDecisionCreate(action="rejected", reason="Numbers moved after cutoff."),
    )
    rejected = _notification_rows(db_session, "reporting.package.approval_rejected")
    assert [row.recipient_user_id for row in rejected] == [DEMO_USER_ID]
    assert rejected[0].severity == "warning"
    assert "Numbers moved after cutoff." in rejected[0].body


def test_regulator_decisions_notify_approvers_and_generator(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)

    # Cycle 1: rejected (returned for correction) with supervisor comments.
    package_id = _drive_to_submitted(db_session)
    workflow.record_regulator_decision(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        package_id,
        channel="manual",
        event="rejected",
        detail={"comments": "Fix the Table 2 maturity balances."},
    )
    rejected = _notification_rows(db_session, "reporting.regulator.rejected")
    # Approver-class fan-out (admin + approver) plus a direct row for the
    # generator, who is not approver-class here.
    assert {row.recipient_user_id for row in rejected} == {ADMIN_ID, APPROVER_ID, DEMO_USER_ID}
    assert all(row.severity == "warning" for row in rejected)
    assert all("Fix the Table 2 maturity balances." in row.body for row in rejected)
    assert all("BSD3" in row.title and "2026-03-31" in row.title for row in rejected)

    # Cycle 2: declined (final refusal) is critical.
    package_id = _drive_to_submitted(db_session)
    workflow.record_regulator_decision(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        package_id,
        channel="manual",
        event="declined",
        detail={"message": "Return set refused."},
    )
    declined = _notification_rows(db_session, "reporting.regulator.declined")
    assert {row.recipient_user_id for row in declined} == {ADMIN_ID, APPROVER_ID, DEMO_USER_ID}
    assert all(row.severity == "critical" for row in declined)
    assert all("Return set refused." in row.body for row in declined)

    # Cycle 3: acknowledged is informational.
    package_id = _drive_to_submitted(db_session)
    workflow.record_regulator_decision(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        package_id,
        channel="manual",
        event="acknowledged",
        detail={"note": "Received in good order."},
    )
    acknowledged = _notification_rows(db_session, "reporting.regulator.acknowledged")
    assert {row.recipient_user_id for row in acknowledged} == {ADMIN_ID, APPROVER_ID, DEMO_USER_ID}
    assert all(row.severity == "info" for row in acknowledged)


# ---------------------------------------------------------------------------
# Deadline scan (idempotency + threshold offsets, explicit as_of)
# ---------------------------------------------------------------------------


def test_deadline_scan_is_idempotent_for_the_same_day(db_session: Session) -> None:
    seed_sample_bank(db_session)
    as_of = date(2026, 4, 2)

    first = reporting_deadline_scan.scan_reporting_deadlines(db_session, DEMO_ORG_ID, as_of=as_of)
    db_session.commit()
    total_after_first = _total_notifications(db_session)
    assert first["notifications_emitted"] == total_after_first
    assert first["notifications_emitted"] > 0
    assert first["banks_scanned"] == 1

    second = reporting_deadline_scan.scan_reporting_deadlines(db_session, DEMO_ORG_ID, as_of=as_of)
    db_session.commit()
    assert second["notifications_emitted"] == 0
    assert _total_notifications(db_session) == total_after_first

    # Every scan row is org-wide and bank-scoped.
    rows = list(db_session.scalars(select(Notification)))
    assert all(row.recipient_user_id is None for row in rows)
    assert all(row.entity_type == "bank" and row.entity_id == SAMPLE_BANK_ID for row in rows)


def test_deadline_scan_thresholds_fire_at_the_right_offsets(db_session: Session) -> None:
    seed_sample_bank(db_session)
    scope = f"BSD3:{REPORTING_DATE.isoformat()}"

    for days_before, threshold in ((7, 7), (3, 3), (1, 1)):
        as_of = BSD3_DUE_DATE - timedelta(days=days_before)
        reporting_deadline_scan.scan_reporting_deadlines(db_session, DEMO_ORG_ID, as_of=as_of)
        db_session.commit()
        rows = _notification_rows(db_session, f"reporting.deadline.due_soon_{threshold}:{scope}")
        assert len(rows) == 1
        assert rows[0].severity == "warning"
        assert "BSD3" in rows[0].title and REPORTING_DATE.isoformat() in rows[0].title

    # Re-running the T-1 scan emits nothing new anywhere.
    total = _total_notifications(db_session)
    rerun = reporting_deadline_scan.scan_reporting_deadlines(
        db_session, DEMO_ORG_ID, as_of=BSD3_DUE_DATE - timedelta(days=1)
    )
    db_session.commit()
    assert rerun["notifications_emitted"] == 0
    assert _total_notifications(db_session) == total

    # Past the due date the obligation escalates to a daily critical overdue.
    for days_after in (1, 2):
        as_of = BSD3_DUE_DATE + timedelta(days=days_after)
        reporting_deadline_scan.scan_reporting_deadlines(db_session, DEMO_ORG_ID, as_of=as_of)
        db_session.commit()
        key = f"reporting.deadline.overdue:{scope}:{as_of.isoformat()}"
        rows = _notification_rows(db_session, key)
        assert len(rows) == 1
        assert rows[0].severity == "critical"


def test_deadline_scan_flags_pending_orass_reupload_daily(db_session: Session) -> None:
    _seed_with_baseline_run(db_session)
    package = _generate(db_session)
    validation.validate_package(db_session, MAKER, SAMPLE_BANK_ID, package.id)
    workflow.request_approval(
        db_session, MAKER, SAMPLE_BANK_ID, package.id, PackageApprovalRequestCreate()
    )
    workflow.decide_approval(
        db_session,
        CHECKER,
        SAMPLE_BANK_ID,
        package.id,
        PackageApprovalDecisionCreate(action="approved"),
    )
    # BG/FMD/2026/07 downtime email submission: deemed complete only after the
    # ORASS re-upload, so the scan must chase it daily.
    workflow.submit_package(
        db_session,
        MAKER,
        SAMPLE_BANK_ID,
        package.id,
        channel="email",
        external_ref="EMAIL-BSD3-0001",
        detail={"pending_orass_reupload": True},
    )

    scope = f"BSD3:{REPORTING_DATE.isoformat()}"
    for day in (date(2026, 4, 20), date(2026, 4, 21)):
        reporting_deadline_scan.scan_reporting_deadlines(db_session, DEMO_ORG_ID, as_of=day)
        db_session.commit()
        rows = _notification_rows(
            db_session, f"reporting.deadline.reupload_pending:{scope}:{day.isoformat()}"
        )
        assert len(rows) == 1
        assert rows[0].severity == "critical"
        assert "BG/FMD/2026/07" in rows[0].body

    # Same-day re-scan stays silent.
    total = _total_notifications(db_session)
    rerun = reporting_deadline_scan.scan_reporting_deadlines(
        db_session, DEMO_ORG_ID, as_of=date(2026, 4, 21)
    )
    db_session.commit()
    assert rerun["notifications_emitted"] == 0
    assert _total_notifications(db_session) == total
