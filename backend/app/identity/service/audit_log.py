"""Read only attributable bank events; never infer bank ownership from a tenant."""

from __future__ import annotations

import base64
import binascii
from contextlib import suppress
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, JsonValue, TypeAdapter
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    String,
    Uuid,
    and_,
    case,
    column,
    func,
    literal,
    literal_column,
    or_,
    select,
    table,
    text,
)
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.audit_attribution import (
    SHARED_REGISTER_SUBJECTS,
    organization_binding_sql,
    shared_register_sql,
    staff_bank_sql,
    subject_bank_sql,
)
from app.core.audit_projection import EVENT_OUTCOMES, normalize_event_details
from app.core.authorization import (
    DataScope,
    InstitutionScope,
    Module,
    Permission,
    PrincipalLocator,
    PrincipalType,
    ResourceLocator,
    RoleBundle,
    Sensitivity,
)
from app.core.tenancy import TenantContext
from app.identity.models.bank import Bank
from app.identity.schemas.audit_log import AuditEventRead, AuditLogRead
from app.identity.service import authorization, scoped_authorization
from app.models.audit_event import AuditEvent
from app.services.audit import record_event

# The staff table is deliberately not imported through the operator feature.
# Its database-owned, tenant-readable evidence projection is the contract here.
_STAFF = table(
    "operator_audit_log",
    column("id", Uuid),
    column("target_org", String),
    column("operator_email", String),
    column("action", String),
    column("detail", JSON),
    column("created_at", DateTime(timezone=True)),
)
_STAFF_FIELDS = (
    "bank_id",
    "batch_id",
    "session_id",
    "reason",
    "support_reference",
    "reference",
    "outcome",
    "source",
    "entity_type",
    "entity_id",
    "before",
    "after",
)


def _json_text(value: ColumnElement[object], *keys: str) -> ColumnElement[str]:
    for key in keys[:-1]:
        value = value.op("->")(key)
    return sql_cast(value.op("->>")(keys[-1]), String)


class StoredEvent(BaseModel):
    id: UUID
    stream: Literal["audit_events", "operator_audit_log"]
    actor: str | None
    action: str
    target_type: str | None
    target_id: str | None
    details: dict[str, JsonValue]
    created_at: datetime


class Cursor(BaseModel):
    organization: str
    bank: str
    occurred_at: datetime
    stream: Literal["audit_events", "operator_audit_log"]
    event: UUID


class Filters(BaseModel):
    start: datetime | None = None
    end: datetime | None = None
    actor: str | None = None
    event_type: str | None = None
    outcome: str | None = None


def require_access(db: Session, ctx: TenantContext, bank_id: str, permission: Permission) -> Bank:
    bank = scoped_authorization.resolve_bank(db, ctx, bank_id, module=Module.AUDIT)
    if ctx.actor_user_id is None or ctx.authorization_version is None:
        raise HTTPException(403, "An audit officer binding is required.")
    principal = PrincipalLocator(ctx.organization_id, ctx.actor_user_id, PrincipalType.HUMAN)
    active, bindings = authorization.prefetch_principal_bindings(db, principal)
    bundles = {RoleBundle.SECURITY_OFFICER.value, RoleBundle.COMPLIANCE_OFFICER.value}
    if permission is Permission.VIEW:
        bundles.add(RoleBundle.AUDITOR.value)
    decision = authorization.evaluate_prefetched_permission(
        principal,
        permission,
        ResourceLocator(
            ctx.organization_id,
            InstitutionScope.INSTITUTION,
            bank.id,
            Module.AUDIT,
            Sensitivity.RESTRICTED,
        ),
        [b for b in bindings if b.role_bundle in bundles and b.data_scope_kind == DataScope.ALL],
        principal_active=active,
    )
    authorization.record_binding_decision(decision, surface="bank_audit_log")
    if not decision.allowed:
        raise HTTPException(403, "An audit officer binding is required.")
    return bank


def _string(value: JsonValue) -> str | None:
    return value if isinstance(value, str) else None


def _decode_cursor(token: str, ctx: TenantContext, bank: Bank) -> Cursor:
    try:
        cursor = Cursor.model_validate_json(base64.b64decode(token, altchars=b"-_", validate=True))
        if cursor.organization != ctx.organization_id or cursor.bank != bank.id:
            raise ValueError("cursor belongs to a different resource")
        if cursor.occurred_at.tzinfo is None:
            raise ValueError("cursor must have a timezone")
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(422, "Invalid audit cursor.") from exc
    return cursor


def _validate_range(filters: Filters) -> None:
    if filters.start is not None and filters.end is not None and filters.start >= filters.end:
        raise HTTPException(422, "Audit range end must be after its start.")


def read_log(  # noqa: PLR0913 - pagination and filters are independent inputs
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    filters: Filters,
    *,
    cursor: str | None = None,
    limit: int = 100,
) -> AuditLogRead:
    bank = require_access(db, ctx, bank_id, Permission.VIEW)
    for value in (filters.start, filters.end):
        if value is not None and value.tzinfo is None:
            raise HTTPException(422, "Audit dates must include a timezone.")
    _validate_range(filters)
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT set_config('app.audit_bank_id', :bank, true)"), {"bank": bank.id})
    event = AuditEvent.__table__
    owned_bank = literal_column(
        subject_bank_sql(
            "audit_events.organization_id",
            "audit_events.entity_type",
            "audit_events.entity_id",
            details="audit_events.details",
            postgresql=db.get_bind().dialect.name == "postgresql",
        ),
        String,
    )
    institution = case(
        (
            event.c.entity_type == "authorization_access_request",
            _json_text(event.c.details, "institution_id"),
        )
    )
    shared_register = sql_cast(
        text(
            shared_register_sql(
                "audit_events.organization_id",
                "audit_events.entity_type",
                "audit_events.entity_id",
                "audit_events.details",
                ":audit_bank",
            )
        ),
        Boolean,
    ).params(audit_bank=bank.id)
    tenant_events = select(
        event.c.id,
        literal("audit_events").label("stream"),
        event.c.actor_user_id.cast(String).label("actor"),
        event.c.event_type.label("action"),
        event.c.entity_type.label("target_type"),
        event.c.entity_id.label("target_id"),
        event.c.details.label("details"),
        event.c.created_at,
    ).where(
        event.c.organization_id == ctx.organization_id,
        or_(
            shared_register,
            and_(
                or_(
                    _json_text(event.c.details, "bank_id") == bank.id,
                    institution == bank.id,
                    and_(event.c.entity_type == "bank", event.c.entity_id == bank.id),
                    _json_text(event.c.details, "scope", "institution_id") == bank.id,
                    owned_bank == bank.id,
                    literal_column(
                        organization_binding_sql(
                            "audit_events.organization_id",
                            "audit_events.entity_type",
                            "audit_events.entity_id",
                            postgresql=db.get_bind().dialect.name == "postgresql",
                        )
                    ),
                ),
                or_(
                    _json_text(event.c.details, "bank_id").is_(None),
                    _json_text(event.c.details, "bank_id") == bank.id,
                ),
                or_(
                    _json_text(event.c.details, "scope", "institution_id").is_(None),
                    _json_text(event.c.details, "scope", "institution_id") == bank.id,
                ),
                or_(
                    event.c.entity_type != "bank",
                    event.c.entity_type.is_(None),
                    event.c.entity_id == bank.id,
                ),
                or_(owned_bank.is_(None), owned_bank == bank.id),
                or_(institution.is_(None), institution == bank.id),
            ),
        ),
    )
    staff_details = (
        sql_cast(_STAFF.c.detail, JSONB)
        if db.get_bind().dialect.name == "postgresql"
        else _STAFF.c.detail
    )
    staff_owned_bank = literal_column(
        staff_bank_sql(
            "operator_audit_log.target_org",
            "operator_audit_log.detail",
            postgresql=db.get_bind().dialect.name == "postgresql",
        ),
        String,
    )
    batch_target = _json_text(_STAFF.c.detail, "batch_id")
    staff_bank = _json_text(_STAFF.c.detail, "bank_id")
    staff_events = select(
        _STAFF.c.id,
        literal("operator_audit_log").label("stream"),
        _STAFF.c.operator_email.label("actor"),
        _STAFF.c.action,
        func.coalesce(
            _json_text(_STAFF.c.detail, "entity_type"),
            case(
                (batch_target.is_not(None), "ingestion_batch"),
                (_json_text(_STAFF.c.detail, "kind") == "mapping_active", "mapping_config"),
                (_json_text(_STAFF.c.detail, "kind") == "threshold_value", "threshold_register"),
                else_="bank",
            ),
        ).label("target_type"),
        func.coalesce(
            _json_text(_STAFF.c.detail, "entity_id"),
            func.coalesce(
                batch_target, _json_text(_STAFF.c.detail, "target_id"), staff_bank, staff_owned_bank
            ),
        ).label("target_id"),
        staff_details.label("details"),
        _STAFF.c.created_at,
    ).where(
        _STAFF.c.target_org == ctx.organization_id,
        or_(staff_bank == bank.id, staff_owned_bank == bank.id),
        or_(staff_bank.is_(None), staff_bank == bank.id),
        or_(staff_owned_bank.is_(None), staff_owned_bank == bank.id),
        or_(
            _json_text(_STAFF.c.detail, "after", "bank_id").is_(None),
            _json_text(_STAFF.c.detail, "after", "bank_id") == bank.id,
        ),
    )
    feed = tenant_events.union_all(staff_events).subquery()
    query = select(feed)
    if filters.start is not None:
        query = query.where(feed.c.created_at >= filters.start)
    if filters.end is not None:
        query = query.where(feed.c.created_at < filters.end)
    if filters.actor is not None:
        actor = filters.actor
        if db.get_bind().dialect.name == "sqlite":
            with suppress(ValueError):
                actor = UUID(actor).hex
        query = query.where(
            or_(
                feed.c.target_type.not_in(SHARED_REGISTER_SUBJECTS),
                feed.c.target_type.is_(None),
                feed.c.target_id == bank.id,
            ),
            or_(
                feed.c.actor == actor,
                _json_text(feed.c.details, "act_operator") == filters.actor,
            ),
        )
    if filters.event_type is not None:
        query = query.where(feed.c.action == filters.event_type)
    if filters.outcome is not None:
        query = query.where(
            func.coalesce(
                _json_text(feed.c.details, "outcome"),
                case(EVENT_OUTCOMES, value=feed.c.action, else_="unspecified"),
            )
            == filters.outcome
        )
    if cursor is not None:
        after = _decode_cursor(cursor, ctx, bank)
        query = query.where(
            or_(
                feed.c.created_at < after.occurred_at,
                and_(feed.c.created_at == after.occurred_at, feed.c.stream < after.stream),
                and_(
                    feed.c.created_at == after.occurred_at,
                    feed.c.stream == after.stream,
                    feed.c.id < after.event,
                ),
            )
        )
    rows = (
        db.execute(
            query.order_by(feed.c.created_at.desc(), feed.c.stream.desc(), feed.c.id.desc()).limit(
                limit + 1
            )
        )
        .mappings()
        .all()
    )
    events = [
        _present(row, bank.id)
        for row in TypeAdapter(list[StoredEvent]).validate_python(rows[:limit])
    ]
    _inspection_evidence(db, events)
    _verify_events(db, events)
    next_cursor = None
    if len(rows) > limit:
        last = events[-1]
        next_cursor = base64.urlsafe_b64encode(
            Cursor(
                organization=ctx.organization_id,
                bank=bank.id,
                occurred_at=last.occurred_at,
                stream=last.stream,
                event=last.id,
            )
            .model_dump_json()
            .encode()
        ).decode()
    record_event(
        db,
        ctx,
        event_type="audit_log.viewed",
        entity_type="bank",
        entity_id=bank.id,
        details={
            "bank_id": bank.id,
            "filters": filters.model_dump(mode="json"),
            "count": len(events),
            "outcome": "success",
        },
    )
    db.commit()
    return AuditLogRead(events=events, next_cursor=next_cursor)


def _present(row: StoredEvent, bank_id: str) -> AuditEventRead:
    details = normalize_event_details(row.action, row.details)
    staff = row.stream == "operator_audit_log"
    if staff:
        details = {
            **details,
            "reason": _string(details.get("reason")) or _string(details.get("note")),
        }
        details = {key: details[key] for key in _STAFF_FIELDS if key in details}
    actor = _string(details.get("act_operator")) or row.actor
    if actor and not staff and not _string(details.get("act_operator")):
        actor = str(UUID(actor))
    item = AuditEventRead(
        id=row.id,
        stream=row.stream,
        actor=actor,
        actor_kind="aequoros_staff"
        if staff or _string(details.get("act_operator"))
        else "bank_user"
        if actor
        else "system",
        action=row.action,
        target_type=row.target_type,
        target_id=row.target_id,
        occurred_at=row.created_at,
        source=_string(details.get("source")),
        outcome=_string(details.get("outcome")) or "unspecified",
        before=details.get("before"),
        after=details.get("after"),
        reason=_string(details.get("reason")),
        support_reference=_string(details.get("support_reference"))
        or _string(details.get("reference")),
        details=details,
    )
    if item.occurred_at.tzinfo is None:
        item.occurred_at = item.occurred_at.replace(tzinfo=UTC)
    if row.target_type in SHARED_REGISTER_SUBJECTS and row.target_id != bank_id:
        item.actor = None
        item.reason = None
        item.support_reference = None
        item.details = {
            key: value
            for key, value in details.items()
            if key
            in {
                "bank_id",
                "institution_class",
                "effective_from",
                "entries",
                "collateral_classes",
                "rows",
                "codes",
                "asset_classes",
                "after",
                "outcome",
            }
        }
        item.before = None
        item.after = item.details.get("after")
        item.source = None
    return item


def _inspection_evidence(db: Session, events: list[AuditEventRead]) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    for event in events:
        if "session_id" not in event.details and "impersonation_session_id" not in event.details:
            continue
        context = cast(
            "object",
            db.scalar(
                select(sql_cast(func.bank_audit_inspection_context(event.stream, event.id), JSONB))
            ),
        )
        if context is None:
            continue
        evidence = TypeAdapter(dict[str, JsonValue]).validate_python(context)
        if event.reason is None:
            event.reason = _string(evidence.get("reason"))
            event.details["reason"] = event.reason
        if event.support_reference is None:
            event.support_reference = _string(evidence.get("support_reference"))
            event.details["support_reference"] = event.support_reference


def _verify_events(db: Session, events: list[AuditEventRead]) -> None:
    if db.get_bind().dialect.name != "postgresql" or not events:
        return
    db.execute(text("SET LOCAL TimeZone = 'UTC'"))
    for stream in ("audit_events", "operator_audit_log"):
        selected = [event for event in events if event.stream == stream]
        if not selected:
            continue
        # Identifiers come only from the fixed stream catalogue. Hashing uses
        # PostgreSQL JSONB serialization, exactly as the database insertion trigger.
        rows = db.execute(
            text(f"""
            SELECT c.event_id, c.sequence,
                c.entry_hash = encode(sha256(convert_to(jsonb_build_array(
                    'aequoros-audit-v1', CAST(:stream AS text), c.sequence, c.previous_hash,
                    (SELECT jsonb_object_agg(key, value) FROM jsonb_each(to_jsonb(e))
                     WHERE key = ANY(c.payload_columns)))::text, 'UTF8')), 'hex')
                AND bank_audit_previous_link(:stream, c.sequence, c.previous_hash) AS valid
            FROM audit_chain_entries c JOIN {stream} e ON e.id = c.event_id
            WHERE c.stream = :stream AND c.event_id = ANY(:ids)
        """),
            {"stream": stream, "ids": [event.id for event in selected]},
        ).all()
        statuses = {
            event_id: (sequence, valid)
            for event_id, sequence, valid in TypeAdapter(
                list[tuple[UUID, int, bool]]
            ).validate_python([tuple(row) for row in rows])
        }
        for event in selected:
            # The PostgreSQL migration seals historical and new events alike.
            # An attributable source row without its chain entry is broken evidence.
            event.integrity = "failed"
            if event.id in statuses:
                event.sequence, valid = statuses[event.id]
                event.integrity = "verified" if valid else "failed"
