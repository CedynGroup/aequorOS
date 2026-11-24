"""A bank's view of its connection to the AequorOS Reporting Gateway.

Banks do not set the connection up: the channel operator enrols each bank from
the staff console (:mod:`app.reporting.service.gateway_enrolment`). A bank can
read its connection, its signature key's facts (never the key) and what it has
sent, and can test the connection without filing anything. Steps shown:

1. Enrolled by AequorOS: the gateway issued the bank's API client.
2. Signature key created: the bank's own filing signature key is registered.
3. Connection tested: a token and the gateway's keys, nothing filed.
4. Filing live: the first approved package has been sent and receipted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import Module, Permission, Sensitivity
from app.identity.public import (
    Bank,
    User,
    require_institution_coverage,
    require_resolved_bank_permission,
)
from app.reporting.models.packages import (
    RegulatoryChannelConfig,
    RegulatoryPackage,
    RegulatoryPackageApproval,
    RegulatoryResubmissionRequest,
    RegulatorySubmissionEvent,
)
from app.reporting.schemas.reporting_gateway import (
    GatewayConnectionFacts,
    GatewayConnectionTest,
    GatewaySigningKeyFacts,
    GatewayStep,
    GatewayTransmission,
    ReportingGatewayStatus,
)
from app.reporting.service.channel_config import channel_config_row, decrypt_channel_credentials
from app.reporting.service.channels.errors import ChannelError
from app.reporting.service.channels.reporting_gateway import (
    CHANNEL_CODE,
    ReportingGatewayChannel,
    certificate_facts,
)
from app.reporting.service.common import require_actor
from app.reporting.service.configure_access import require_reporting_configure_access
from app.reporting.service.gateway_enrolment import RENEWAL_CHECK
from app.services.audit import record_event

_TRANSMISSIONS_SHOWN = 10
#: The bank is told its certificate renews automatically only while the staff
#: plane's scheduled check has looked at it this recently.
_RENEWAL_CHECK_FRESH = timedelta(days=3)
_SUPERSEDES_WALK_LIMIT = 50


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=message)


def _audit(
    db: Session,
    ctx: TenantContext,
    row: RegulatoryChannelConfig,
    action: str,
    details: dict[str, Any],
) -> None:
    db.flush()
    record_event(
        db,
        ctx,
        event_type="regulatory_channel_config.updated",
        entity_type="regulatory_channel_config",
        entity_id=row.id,
        details={"bank_id": row.bank_id, "channel": CHANNEL_CODE, "action": action, **details},
        bank_id=row.bank_id,
    )


def test_connection(db: Session, ctx: TenantContext, bank_id: str) -> GatewayConnectionTest:
    bank = require_reporting_configure_access(db, ctx, bank_id)
    require_actor(ctx)
    row = channel_config_row(db, ctx, bank.id, CHANNEL_CODE)
    if row is None or not row.config.get("enrolled_at"):
        raise _unprocessable(
            "Your bank is not yet enrolled on the AequorOS Reporting Gateway. "
            "Ask your AequorOS team to enrol it."
        )
    credentials = decrypt_channel_credentials(row)
    tested_at = datetime.now(UTC)
    try:
        result = ReportingGatewayChannel(
            config=dict(row.config), credentials=credentials
        ).connection_test()
        outcome = GatewayConnectionTest(
            ok=True,
            gateway_keys=int(cast(int, result["gateway_keys"])),
            tested_at=tested_at,
            message="Connected: the gateway issued a token and published its keys.",
        )
    except ChannelError as exc:
        outcome = GatewayConnectionTest(
            ok=False, gateway_keys=0, tested_at=tested_at, message=exc.operator_message
        )
    finally:
        del credentials
    config = dict(row.config)
    config["last_connection_test"] = {"at": tested_at.isoformat(), "ok": outcome.ok}
    row.config = config
    _audit(db, ctx, row, "gateway_connection_tested", {"ok": outcome.ok})
    db.commit()
    return outcome


def gateway_status(db: Session, ctx: TenantContext, bank_id: str) -> ReportingGatewayStatus:
    bank, _ = require_institution_coverage(db, ctx, bank_id)
    require_resolved_bank_permission(
        db,
        ctx,
        bank,
        permission=Permission.VIEW,
        module=Module.REGULATORY,
        sensitivity=Sensitivity.RESTRICTED,
        surface="reporting_channel_config_read",
    )
    return _status(db, ctx, bank)


def enrolled_institution_code(db: Session, ctx: TenantContext, bank_id: str) -> str | None:
    """The institution code the bank is enrolled under on the gateway, if enrolled.

    For a caller already authorised for the filing it describes (the transmit preview).
    """
    row = channel_config_row(db, ctx, bank_id, CHANNEL_CODE)
    config: dict[str, Any] = dict(row.config) if row is not None else {}
    code = config.get("institution_code")
    return code if isinstance(code, str) and config.get("enrolled_at") else None


def _transmissions(
    db: Session, ctx: TenantContext, bank: Bank
) -> list[tuple[RegulatorySubmissionEvent, RegulatoryPackage]]:
    rows = (
        db.execute(
            select(RegulatorySubmissionEvent, RegulatoryPackage)
            .join(RegulatoryPackage, RegulatoryPackage.id == RegulatorySubmissionEvent.package_id)
            .where(
                RegulatorySubmissionEvent.organization_id == ctx.organization_id,
                RegulatoryPackage.organization_id == ctx.organization_id,
                RegulatoryPackage.bank_id == bank.id,
                RegulatorySubmissionEvent.channel == CHANNEL_CODE,
                RegulatorySubmissionEvent.event == "submitted",
                RegulatorySubmissionEvent.external_ref.is_not(None),
            )
            .order_by(RegulatorySubmissionEvent.occurred_at.desc())
            .limit(_TRANSMISSIONS_SHOWN)
        )
        .tuples()
        .all()
    )
    return [(event, package) for event, package in rows]


def _latest_gateway_status(db: Session, event: RegulatorySubmissionEvent) -> str:
    later = db.scalars(
        select(RegulatorySubmissionEvent)
        .where(
            RegulatorySubmissionEvent.package_id == event.package_id,
            RegulatorySubmissionEvent.channel == CHANNEL_CODE,
            RegulatorySubmissionEvent.occurred_at >= event.occurred_at,
        )
        .order_by(RegulatorySubmissionEvent.occurred_at.desc())
    ).all()
    for candidate in later:
        state = candidate.detail.get("gateway_status")
        if isinstance(state, str):
            return state
    return "received"


def _time(value: object) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) and value else None


def _connection(config: dict[str, Any], now: datetime) -> GatewayConnectionFacts | None:
    pem = config.get("client_certificate_pem")
    client_id = config.get("client_id")
    environment = config.get("environment")
    if not isinstance(pem, str) or not pem or not isinstance(client_id, str):
        return None
    facts = certificate_facts(pem)
    raw_check = config.get(RENEWAL_CHECK)
    check = cast(dict[str, object], raw_check) if isinstance(raw_check, dict) else {}
    checked_at = _time(check.get("checked_at"))
    automatic = checked_at is not None and now - checked_at <= _RENEWAL_CHECK_FRESH
    return GatewayConnectionFacts(
        client_id=client_id,
        environment=environment if environment in ("sandbox", "production") else "sandbox",
        renews_by=datetime.fromisoformat(facts["not_after"]),
        fingerprint_sha256=facts["fingerprint_sha256"],
        renewed_at=_time(config.get("certificate_renewed_at")),
        automatic_renewal_from=_time(check.get("renew_from")) if automatic else None,
    )


def _signing_facts(
    config: dict[str, Any], last_used_at: datetime | None
) -> GatewaySigningKeyFacts | None:
    raw = config.get("signing_key")
    if not isinstance(raw, dict) or not config.get("enrolled_at"):
        return None
    key = cast(dict[str, object], raw)
    return GatewaySigningKeyFacts(
        kid=str(key.get("kid") or ""),
        algorithm="ES256",
        held_by="aequoros_vault" if key.get("provider") == "vault" else "bank_key_service",
        created_at=_time(key.get("created_at")),
        last_used_at=last_used_at,
    )


def _status(db: Session, ctx: TenantContext, bank: Bank) -> ReportingGatewayStatus:
    row = channel_config_row(db, ctx, bank.id, CHANNEL_CODE)
    config: dict[str, Any] = dict(row.config) if row is not None else {}
    enrolled_at = _time(config.get("enrolled_at"))
    connection = _connection(config, datetime.now(UTC)) if enrolled_at else None
    transmissions = _transmissions(db, ctx, bank)
    signing = _signing_facts(config, transmissions[0][0].occurred_at if transmissions else None)
    raw_test = config.get("last_connection_test")
    test = cast(dict[str, object], raw_test) if isinstance(raw_test, dict) else {}
    tested_ok = test.get("ok")
    steps = [
        GatewayStep(key="enrolled", done=connection is not None),
        GatewayStep(key="signing_key_created", done=signing is not None),
        GatewayStep(key="connection_tested", done=tested_ok is True),
        GatewayStep(key="first_filing", done=bool(transmissions)),
    ]
    return ReportingGatewayStatus(
        enrolled=connection is not None and signing is not None,
        institution_code=cast(str | None, config.get("institution_code")) if enrolled_at else None,
        enrolled_at=enrolled_at,
        connection=connection,
        signing_key=signing,
        last_connection_test_at=_time(test.get("at")),
        last_connection_test_ok=tested_ok if isinstance(tested_ok, bool) else None,
        steps=steps,
        transmissions=[
            GatewayTransmission(
                package_id=package.id,
                return_code=package.return_code,
                reporting_date=package.reporting_date,
                approved_by_role=cast(str | None, event.detail.get("approved_by_role")),
                sent_at=event.occurred_at,
                receipt_id=str(event.external_ref),
                gateway_status=_latest_gateway_status(db, event),
            )
            for event, package in transmissions
        ],
    )


def _role_label(db: Session, user_id: UUID | None, fallback: str) -> str:
    if user_id is None:
        return fallback
    user = db.get(User, user_id)
    title = user.job_title if user is not None else None
    return title.strip() if title and title.strip() else fallback


def _supersedes_receipt(db: Session, package: RegulatoryPackage) -> str | None:
    """The gateway receipt of the newest earlier version of this return that was filed."""
    ancestor_id = package.supersedes_id
    for _ in range(_SUPERSEDES_WALK_LIMIT):
        if ancestor_id is None:
            return None
        receipt = db.scalar(
            select(RegulatorySubmissionEvent.external_ref)
            .where(
                RegulatorySubmissionEvent.package_id == ancestor_id,
                RegulatorySubmissionEvent.channel == CHANNEL_CODE,
                RegulatorySubmissionEvent.event == "submitted",
                RegulatorySubmissionEvent.external_ref.is_not(None),
            )
            .order_by(RegulatorySubmissionEvent.occurred_at.desc())
            .limit(1)
        )
        if receipt:
            return receipt
        ancestor = db.get(RegulatoryPackage, ancestor_id)
        ancestor_id = ancestor.supersedes_id if ancestor is not None else None
    return None


def _amendment_reason(db: Session, package: RegulatoryPackage) -> str | None:
    """Why this version corrects the filed one: its own notes, else the reason the
    correction was recorded with (the request this version consumed)."""
    notes = (package.notes or "").strip()
    if notes:
        return notes
    reason = db.scalar(
        select(RegulatoryResubmissionRequest.reason)
        .where(
            RegulatoryResubmissionRequest.organization_id == package.organization_id,
            RegulatoryResubmissionRequest.consumed_by_package_id == package.id,
        )
        .order_by(RegulatoryResubmissionRequest.occurred_at.desc())
        .limit(1)
    )
    return reason.strip() if isinstance(reason, str) and reason.strip() else None


def gateway_channel_for_submission(
    db: Session,
    bank: Bank,
    package: RegulatoryPackage,
    *,
    config: dict[str, Any],
    credentials: dict[str, Any] | None,
) -> ReportingGatewayChannel:
    """A gateway channel carrying this package's declaration and amendment link."""
    approval = db.scalar(
        select(RegulatoryPackageApproval)
        .where(
            RegulatoryPackageApproval.package_id == package.id,
            RegulatoryPackageApproval.action == "approved",
        )
        .order_by(RegulatoryPackageApproval.occurred_at.desc())
        .limit(1)
    )
    supersedes = _supersedes_receipt(db, package)
    return ReportingGatewayChannel(
        config=config,
        credentials=credentials,
        currency=bank.currency,
        declaration={
            "prepared_by_role": _role_label(db, package.generated_by, "Report preparer"),
            "approved_by_role": _role_label(
                db, approval.actor_user_id if approval else None, "Approver"
            ),
            # Unset when no approval row exists: the channel then uses the
            # package's own last change, never "now", so a retry is identical.
            "approved_at": approval.occurred_at.isoformat() if approval else None,
            "amendment_reason": _amendment_reason(db, package) if supersedes else None,
        },
        supersedes_receipt_id=supersedes,
    )
