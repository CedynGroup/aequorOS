"""The regulator's queries on the bank's filings, through the AequorOS Reporting Gateway.

A supervisor's query lives at the gateway, which is the record of the correspondence;
this module reads it live and answers it. Nothing is cached: what the bank sees is what
the regulator holds. The bank's own audit trail records each act on it: that the query
reached the bank's system (the acknowledgement the gateway shows the supervisor) and
every reply, by digest and length.

Who may do what follows the filing chain: anyone who may see regulatory filings reads
the queries; answering the regulator is the transmitting officer's act (``SUBMIT``),
the same authority that sends a return.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import (
    ConditionCheck,
    ConditionKind,
    Module,
    Permission,
    Sensitivity,
)
from app.identity import public as scoped_authorization
from app.identity.public import Bank, get_bank_or_404
from app.reporting.models.packages import (
    RegulatoryPackage,
    RegulatoryResubmissionRequest,
    RegulatorySubmissionEvent,
)
from app.reporting.schemas.packages import ResubmissionRequestCreate, ResubmissionRequestRead
from app.reporting.schemas.reporting_gateway import (
    RegulatorQueryFilingRead,
    RegulatorQueryListRead,
    RegulatorQueryMessageRead,
    RegulatorQueryRead,
    RegulatorQueryResponseCreate,
    RegulatorQueryStatus,
)
from app.reporting.service import family_access
from app.reporting.service.channel_config import channel_config_row, decrypt_channel_credentials
from app.reporting.service.channels.errors import ChannelError
from app.reporting.service.channels.reporting_gateway import (
    CHANNEL_CODE,
    ReportingGatewayChannel,
)
from app.reporting.service.common import get_package_or_404, require_actor
from app.reporting.service.registry import get_definition
from app.services.audit import record_event

_NOT_ENROLLED = (
    "Your bank is not enrolled on the AequorOS Reporting Gateway, so there are no "
    "queries to show. Ask your AequorOS team to enrol it."
)


def _require(
    db: Session, ctx: TenantContext, bank: Bank, permission: Permission, surface: str
) -> None:
    scoped_authorization.require_resolved_bank_permission(
        db,
        ctx,
        bank,
        permission=permission,
        module=Module.REGULATORY,
        sensitivity=Sensitivity.RESTRICTED,
        surface=surface,
    )


def _require_on_return(  # noqa: PLR0913 - the actor, the return and how it is decided
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    package_id: UUID,
    *,
    surface: str,
    preparer_may: bool,
) -> None:
    """The authority over the return a query is about, decided on that return.

    Answering the regulator is the transmitting officer's act, decided exactly as
    transmission is (``family_access.require_transmission_authority``), WITH the
    same four-eyes verdict: ``SUBMIT`` declares ``MAKER_CHECKER`` a required runtime
    condition, so an evaluation that is not handed one denies every binding, the
    Validator's included. Opening a correction is ALSO the preparer's act (the
    correction route asks for ``EDIT`` on the return's family), so ``preparer_may``
    admits either.
    """
    package = get_package_or_404(db, ctx, bank.id, package_id)
    try:
        family_access.require_transmission_authority(
            db,
            ctx,
            bank,
            package,
            Permission.SUBMIT,
            surface=surface,
            conditions=_four_eyes(ctx, package),
        )
        return
    except HTTPException as refusal:
        if refusal.status_code != status.HTTP_403_FORBIDDEN:
            raise
        if not preparer_may:
            generated = not _four_eyes(ctx, package)[0].passed
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"You generated this {package.return_code} return, so another officer "
                    "with Validator authority must answer the regulator about it."
                    if generated
                    else f"Replying to the regulator about this {package.return_code} return "
                    "is the Validator's act. Your account does not hold Validator authority "
                    "for this institution."
                ),
            ) from refusal
    try:
        family_access.require_permission(db, ctx, bank, package, Permission.EDIT, surface=surface)
    except HTTPException as refusal:
        if refusal.status_code != status.HTTP_403_FORBIDDEN:
            raise
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Opening a correction of this {package.return_code} return is the Validator's "
                "or its preparer's act. Your account holds neither for this return."
            ),
        ) from refusal


def _four_eyes(ctx: TenantContext, package: RegulatoryPackage | None) -> tuple[ConditionCheck]:
    """The maker/checker verdict a ``SUBMIT`` evaluation must be handed.

    The rule is the transmit route's own (``api.deps._package_transmission_conditions``):
    the officer who generated the return does not speak for it to the regulator. A
    query about no return of this workspace has no maker, so the verdict passes.
    """
    if package is None:
        return (
            ConditionCheck(
                kind=ConditionKind.MAKER_CHECKER,
                passed=True,
                reason="the query concerns no return this workspace generated",
            ),
        )
    distinct = ctx.actor_user_id is not None and package.generated_by != ctx.actor_user_id
    return (
        ConditionCheck(
            kind=ConditionKind.MAKER_CHECKER,
            passed=distinct,
            reason=(
                "the officer answering for this return is not the officer who generated it"
                if distinct
                else "the officer who generated this return cannot also answer for it"
            ),
        ),
    )


def _channel(db: Session, ctx: TenantContext, bank: Bank) -> ReportingGatewayChannel | None:
    row = channel_config_row(db, ctx, bank.id, CHANNEL_CODE)
    if row is None or not row.config.get("enrolled_at"):
        return None
    return ReportingGatewayChannel(
        config=dict(row.config), credentials=decrypt_channel_credentials(row)
    )


def _filings(
    db: Session, ctx: TenantContext, bank: Bank, receipts: set[str]
) -> dict[str, RegulatorQueryFilingRead]:
    """The bank's own return behind each receipt, newest transmission first."""
    if not receipts:
        return {}
    rows = db.execute(
        select(RegulatorySubmissionEvent.external_ref, RegulatoryPackage)
        .join(RegulatoryPackage, RegulatoryPackage.id == RegulatorySubmissionEvent.package_id)
        .where(
            RegulatorySubmissionEvent.organization_id == ctx.organization_id,
            RegulatoryPackage.organization_id == ctx.organization_id,
            RegulatoryPackage.bank_id == bank.id,
            RegulatorySubmissionEvent.channel == CHANNEL_CODE,
            RegulatorySubmissionEvent.event == "submitted",
            RegulatorySubmissionEvent.external_ref.in_(receipts),
        )
        .order_by(RegulatorySubmissionEvent.occurred_at.desc())
    ).tuples()
    found: dict[str, RegulatorQueryFilingRead] = {}
    for receipt, package in rows:
        if receipt is None or receipt in found:
            continue
        found[receipt] = RegulatorQueryFilingRead(
            package_id=package.id,
            return_code=package.return_code,
            reporting_date=package.reporting_date,
            version=package.version,
        )
    return found


def _when(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


_AUTHORS: dict[str, Literal["supervisor", "institution", "system"]] = {
    "supervisor": "supervisor",
    "institution": "institution",
    "system": "system",
}
_STATUSES: dict[str, RegulatorQueryStatus] = {
    "awaiting_institution": "awaiting_institution",
    "awaiting_supervisor": "awaiting_supervisor",
    "closed": "closed",
}


def _message(raw: dict[str, Any]) -> RegulatorQueryMessageRead:
    return RegulatorQueryMessageRead(
        id=str(raw.get("id") or ""),
        author=_AUTHORS.get(str(raw.get("author") or ""), "system"),
        author_label=str(raw.get("author_label") or ""),
        body=str(raw.get("body") or ""),
        created_at=_when(raw.get("created_at")) or datetime.now(UTC),
    )


def _query(
    raw: dict[str, Any], filings: dict[str, RegulatorQueryFilingRead]
) -> RegulatorQueryRead | None:
    number = raw.get("number")
    opened = _when(raw.get("opened_at"))
    if not isinstance(number, str) or opened is None:
        return None
    receipt = str(raw.get("receipt_id") or "")
    messages = raw.get("messages")
    return RegulatorQueryRead(
        number=number,
        kind="resubmission" if raw.get("kind") == "resubmission" else "query",
        subject=str(raw.get("subject") or ""),
        status=_STATUSES.get(str(raw.get("status") or ""), "awaiting_institution"),
        receipt_id=receipt,
        due_at=_when(raw.get("due_at")),
        opened_at=opened,
        acknowledged_at=_when(raw.get("acknowledged_at")),
        messages=[
            _message(cast(dict[str, Any], item))
            for item in (cast(list[object], messages) if isinstance(messages, list) else [])
            if isinstance(item, dict)
        ],
        filing=filings.get(receipt),
    )


def list_queries(db: Session, ctx: TenantContext, bank_id: str) -> RegulatorQueryListRead:
    bank = get_bank_or_404(db, ctx, bank_id)
    _require(db, ctx, bank, Permission.VIEW, "regulator_queries")
    read_at = datetime.now(UTC)
    channel = _channel(db, ctx, bank)
    if channel is None:
        return RegulatorQueryListRead(
            connected=False, items=[], message=_NOT_ENROLLED, read_at=read_at
        )
    try:
        raw = channel.list_queries()
    except ChannelError as exc:
        return RegulatorQueryListRead(
            connected=True, items=[], message=exc.operator_message, read_at=read_at
        )
    filings = _filings(db, ctx, bank, {str(item.get("receipt_id") or "") for item in raw})
    items = [query for item in raw if (query := _query(item, filings)) is not None]
    items.sort(key=lambda query: query.opened_at, reverse=True)
    return RegulatorQueryListRead(connected=True, items=items, message=None, read_at=read_at)


def fetch_queries(db: Session, ctx: TenantContext, bank: Bank) -> list[RegulatorQueryRead] | None:
    """The bank's regulator queries for the background status sync, or ``None``.

    No authorization check: the caller is the platform's own scheduled sync,
    which reads on the institution's behalf and only publishes notifications
    (each filtered by the recipient's own authority). ``None`` means the bank
    is not enrolled or the gateway could not be read this cycle.
    """
    channel = _channel(db, ctx, bank)
    if channel is None:
        return None
    try:
        raw = channel.list_queries()
    except ChannelError:
        return None
    filings = _filings(db, ctx, bank, {str(item.get("receipt_id") or "") for item in raw})
    return [query for item in raw if (query := _query(item, filings)) is not None]


def _connected(db: Session, ctx: TenantContext, bank: Bank) -> ReportingGatewayChannel:
    channel = _channel(db, ctx, bank)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_NOT_ENROLLED)
    return channel


def _refused(exc: ChannelError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.operator_message)


def acknowledge_query(db: Session, ctx: TenantContext, bank_id: str, number: str) -> None:
    """Record at the regulator that the query reached the bank. Idempotent there."""
    require_actor(ctx)
    bank = get_bank_or_404(db, ctx, bank_id)
    _require(db, ctx, bank, Permission.VIEW, "regulator_query_acknowledge")
    try:
        _connected(db, ctx, bank).acknowledge_query(number)
    except ChannelError as exc:
        raise _refused(exc) from exc
    record_event(
        db,
        ctx,
        event_type="regulator_query.acknowledged",
        entity_type="bank",
        entity_id=bank.id,
        details={"number": number, "channel": CHANNEL_CODE},
        bank_id=bank.id,
    )
    db.commit()


def respond_to_query(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    number: str,
    payload: RegulatorQueryResponseCreate,
) -> RegulatorQueryMessageRead:
    """Send the bank's reply to the regulator; the transmitting officer's act."""
    require_actor(ctx)
    bank = get_bank_or_404(db, ctx, bank_id)
    _require(db, ctx, bank, Permission.VIEW, "regulator_query_response")
    body = payload.body.strip()
    if not body:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Write a reply first."
        )
    channel = _connected(db, ctx, bank)
    query = _open_query(db, ctx, bank, channel, number)
    if query.filing is not None:
        # Decided on the return the query is about, exactly as its transmission is.
        _require_on_return(
            db,
            ctx,
            bank,
            query.filing.package_id,
            surface="regulator_query_response",
            preparer_may=False,
        )
    else:
        # A receipt this workspace did not file: no return to decide on, so the
        # bank-wide transmitting authority answers it.
        scoped_authorization.require_resolved_bank_permission(
            db,
            ctx,
            bank,
            permission=Permission.SUBMIT,
            module=Module.REGULATORY,
            sensitivity=Sensitivity.RESTRICTED,
            surface="regulator_query_response",
            conditions=_four_eyes(ctx, None),
        )
    try:
        sent = channel.respond_to_query(number, body, payload.resubmission_receipt_id)
    except ChannelError as exc:
        raise _refused(exc) from exc
    record_event(
        db,
        ctx,
        event_type="regulator_query.answered",
        entity_type="bank",
        entity_id=bank.id,
        details={
            "number": number,
            "channel": CHANNEL_CODE,
            "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "body_chars": len(body),
            "resubmission_receipt_id": payload.resubmission_receipt_id,
            "gateway_message_id": sent.get("id"),
        },
        bank_id=bank.id,
    )
    db.commit()
    return _message(sent)


def _open_query(
    db: Session, ctx: TenantContext, bank: Bank, channel: ReportingGatewayChannel, number: str
) -> RegulatorQueryRead:
    try:
        raw = channel.list_queries()
    except ChannelError as exc:
        raise _refused(exc) from exc
    filings = _filings(db, ctx, bank, {str(item.get("receipt_id") or "") for item in raw})
    for item in raw:
        if item.get("number") == number and (query := _query(item, filings)) is not None:
            return query
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"No regulator query {number}."
    )


def start_correction(
    db: Session, ctx: TenantContext, bank_id: str, number: str
) -> ResubmissionRequestRead:
    """Open the correction the regulator asked for: the return goes back to its preparer.

    The regulator's request for resubmission becomes the bank's correction of the
    filed return (``workflow.request_resubmission``, granted by the gateway's rule)
    with the regulator's own words as the reason. The next generation of that return
    and reporting date is the corrected version; transmitting it files an amendment,
    and AequorOS then answers this query with the new receipt
    (:func:`answer_resubmission_requests`). The transmitting officer's act.
    """
    from app.reporting.service import workflow  # noqa: PLC0415 - workflow imports this module

    require_actor(ctx)
    bank = get_bank_or_404(db, ctx, bank_id)
    _require(db, ctx, bank, Permission.VIEW, "regulator_query_correction")
    query = _open_query(db, ctx, bank, _connected(db, ctx, bank), number)
    if query.kind != "resubmission":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This query asks a question, not for a corrected filing. Answer it instead.",
        )
    if query.status == "closed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Query {number} is closed."
        )
    if query.filing is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Receipt {query.receipt_id} is not a filing of this workspace, so there is no "
                "return here to correct."
            ),
        )
    _require_on_return(
        db,
        ctx,
        bank,
        query.filing.package_id,
        surface="regulator_query_correction",
        preparer_may=True,
    )
    pending = db.scalar(
        select(RegulatoryResubmissionRequest).where(
            RegulatoryResubmissionRequest.organization_id == ctx.organization_id,
            RegulatoryResubmissionRequest.package_id == query.filing.package_id,
            RegulatoryResubmissionRequest.status == "granted",
            RegulatoryResubmissionRequest.consumed_by_package_id.is_(None),
        )
    )
    if pending is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A correction of this return is already open. The preparer generates the "
                "corrected version under Returns."
            ),
        )
    asked = next((m.body for m in query.messages if m.author == "supervisor"), "").strip()
    definition = get_definition(query.filing.return_code)
    regulator = definition.regulator if definition is not None else "The regulator"
    reason = f"{regulator} requested a corrected filing ({number})" + (
        f": {asked}" if asked else "."
    )
    granted = workflow.request_resubmission(
        db,
        ctx,
        bank.id,
        query.filing.package_id,
        ResubmissionRequestCreate(reason=reason[:2000]),
    )
    record_event(
        db,
        ctx,
        event_type="regulator_query.correction_started",
        entity_type="bank",
        entity_id=bank.id,
        details={
            "number": number,
            "receipt_id": query.receipt_id,
            "package_id": str(query.filing.package_id),
            "resubmission_request_id": str(granted.id),
        },
        bank_id=bank.id,
    )
    db.commit()
    return granted


def answer_resubmission_requests(
    channel: ReportingGatewayChannel, superseded_receipt: str, corrected_receipt: str
) -> list[str]:
    """Answer every open request for a corrected filing of ``superseded_receipt``.

    Called once the corrected version has been filed as an amendment: the reply names
    the new receipt, which is how the regulator's request is answered. Returns the
    query numbers answered. A query that cannot be answered now is left for the
    officer to answer by hand; the filing itself already stands.
    """
    answered: list[str] = []
    for item in channel.list_queries():
        if (
            item.get("kind") != "resubmission"
            or item.get("status") == "closed"
            or item.get("receipt_id") != superseded_receipt
        ):
            continue
        number = str(item.get("number") or "")
        try:
            channel.respond_to_query(
                number,
                f"The corrected filing is {corrected_receipt}, filed as an amendment of "
                f"{superseded_receipt}.",
                corrected_receipt,
            )
        except ChannelError:
            continue
        answered.append(number)
    return answered


__all__ = [
    "acknowledge_query",
    "answer_resubmission_requests",
    "list_queries",
    "respond_to_query",
    "start_correction",
]
