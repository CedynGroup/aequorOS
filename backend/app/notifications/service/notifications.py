"""In-app notification service (docs/submission_pipeline_plan.md §W3).

``emit`` writes rows inside the caller's transaction — a notification is
atomic with the state change that caused it (the caller commits). Role
fan-out targets every active user holding the role *or higher* per the
``app.core.security.ROLES`` hierarchy; ``recipient_user_id=None`` with no
role means one org-wide row, subject to the read-side visibility rules below.

Read-side visibility: recipient and tenant scope, the requester's current
``view`` authority over the typed source locator (SQL-filtered before counts
and pagination, #186), and return-family authority for filing notifications.
``mark_read``/``mark_all_read`` only ever touch rows visible to the actor.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, String, and_, cast, false, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import InstitutionScope, Module, Permission, Sensitivity
from app.core.locator_visibility import locator_visibility
from app.core.security import ROLES
from app.identity.public import Bank, User, load_permission_grants
from app.notifications.models import Notification
from app.reporting.models.packages import RETURN_FAMILIES, RegulatoryPackage
from app.reporting.service import family_access
from app.reporting.service.registry import REGISTRY


def _roles_at_or_above(role: str) -> tuple[str, ...]:
    """Roles at least as privileged as ``role`` (ROLES is most- to least-)."""
    if role not in ROLES:
        msg = f"Unknown recipient role {role!r}; expected one of {ROLES}."
        raise ValueError(msg)
    return ROLES[: ROLES.index(role) + 1]


def emit(  # noqa: PLR0913 - a notification carries the full display envelope
    db: Session,
    ctx: TenantContext,
    *,
    type: str,  # noqa: A002 - mirrors the column name
    severity: str,
    title: str,
    body: str,
    entity_type: str | None = None,
    entity_id: UUID | str | None = None,
    recipient_user_id: UUID | None = None,
    recipient_role: str | None = None,
    bank_id: str | None = None,
    module: Module,
    sensitivity: Sensitivity,
) -> list[Notification]:
    """Add notification rows to the caller's transaction (no commit).

    - ``recipient_role``: one row per active user in the org holding that role
      or higher (maker-checker queues, regulator outcomes).
    - ``recipient_user_id``: one row for that user.
    - neither: one org-wide row (``recipient_user_id`` NULL).

    Every emitter also declares the typed source locator (mission D9): the
    producing module, the data sensitivity of what the notification is about,
    and the bank it concerns. ``institution_scope`` derives from ``bank_id``.
    A row without a locator is hidden from every tenant read path, so the
    arguments are not optional — an emitter that cannot classify its own
    module must not emit.
    """
    if recipient_role is not None:
        recipient_ids: list[UUID | None] = list(
            db.scalars(
                select(User.id).where(
                    User.organization_id == ctx.organization_id,
                    User.is_active.is_(True),
                    User.role.in_(_roles_at_or_above(recipient_role)),
                )
            )
        )
    else:
        recipient_ids = [recipient_user_id]
    scope = InstitutionScope.INSTITUTION if bank_id is not None else InstitutionScope.ORGANIZATION
    rows = [
        Notification(
            organization_id=ctx.organization_id,
            recipient_user_id=recipient,
            type=type,
            severity=severity,
            title=title,
            body=body,
            entity_type=entity_type,
            entity_id=None if entity_id is None else str(entity_id),
            institution_scope=scope.value,
            bank_id=bank_id,
            module=module.value,
            sensitivity=sensitivity.value,
        )
        for recipient in recipient_ids
    ]
    db.add_all(rows)
    db.flush()
    return rows


def visibility_conditions(db: Session, ctx: TenantContext) -> tuple[ColumnElement[bool], ...]:
    """Recipient, tenant, locator-authority and return-family boundaries for disclosure.

    Every factor is re-evaluated per call, so a binding granted or revoked
    between requests changes visibility immediately. A row the locator factor
    refuses (legacy rows without a locator, modules or sensitivities the
    caller holds no current ``view`` grant for) is indistinguishable from one
    that does not exist.
    """
    if ctx.actor_user_id is None:
        recipient = Notification.recipient_user_id.is_(None)
    else:
        recipient = or_(
            Notification.recipient_user_id.is_(None),
            Notification.recipient_user_id == ctx.actor_user_id,
        )
    # Current view authority over the persisted locator (#186): list, counts,
    # pagination and both mark-read paths all filter through this factor, and
    # it hides every locator-less (legacy) row.
    locator_view = locator_visibility(
        load_permission_grants(db, ctx, Permission.VIEW),
        Permission.VIEW,
        institution_scope=Notification.institution_scope,
        bank_id=Notification.bank_id,
        module=Notification.module,
        sensitivity=Notification.sensitivity,
    )
    deadline = or_(
        Notification.type.startswith("reporting.deadline."),
        Notification.type.startswith("reporting.instrument."),
    )
    package_scopes: list[ColumnElement[bool]] = []
    deadline_scopes: list[ColumnElement[bool]] = []
    banks = db.scalars(select(Bank).where(Bank.organization_id == ctx.organization_id))
    for bank in banks:
        visible = set(RETURN_FAMILIES) - family_access.hidden_families(db, ctx, bank)
        if not visible:
            continue
        package_scopes.append(
            and_(RegulatoryPackage.bank_id == bank.id, RegulatoryPackage.return_family.in_(visible))
        )
        deadline_scopes.append(
            and_(
                Notification.entity_id == bank.id,
                or_(
                    false(),
                    *(
                        Notification.type.contains(f":{definition.code}:", autoescape=True)
                        for definition in REGISTRY.values()
                        if definition.family in visible
                    ),
                ),
            )
        )
    package_visible = (
        select(RegulatoryPackage.id)
        .where(
            RegulatoryPackage.organization_id == ctx.organization_id,
            func.replace(cast(RegulatoryPackage.id, String), "-", "")
            == func.replace(Notification.entity_id, "-", ""),
            or_(false(), *package_scopes),
        )
        .exists()
    )
    return (
        Notification.organization_id == ctx.organization_id,
        recipient,
        locator_view,
        or_(
            and_(
                ~deadline,
                or_(
                    Notification.entity_type.is_(None),
                    Notification.entity_type != "regulatory_package",
                ),
            ),
            and_(
                deadline,
                Notification.entity_type == "bank",
                or_(false(), *deadline_scopes),
            ),
            and_(Notification.entity_type == "regulatory_package", package_visible),
        ),
    )


def list_notifications(
    db: Session,
    ctx: TenantContext,
    *,
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Notification], int, int]:
    """Visible notifications, newest first: ``(rows, total, unread_count)``.

    ``total`` counts the listed set (honoring ``unread_only``) for pagination;
    ``unread_count`` always counts every visible unread row (badge value).
    """
    visible = visibility_conditions(db, ctx)
    listed = (*visible, Notification.read_at.is_(None)) if unread_only else visible
    rows = list(
        db.scalars(
            select(Notification)
            .where(*listed)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    total = db.scalar(select(func.count()).select_from(Notification).where(*listed)) or 0
    unread_count = (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(*visible, Notification.read_at.is_(None))
        )
        or 0
    )
    return rows, total, unread_count


def mark_read(db: Session, ctx: TenantContext, notification_id: UUID) -> Notification:
    """Set ``read_at`` on one visible notification (idempotent) and commit."""
    row = db.scalar(
        select(Notification).where(
            Notification.id == notification_id, *visibility_conditions(db, ctx)
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")
    if row.read_at is None:
        row.read_at = datetime.now(UTC)
    db.commit()
    return row


def mark_all_read(db: Session, ctx: TenantContext) -> int:
    """Set ``read_at`` on every visible unread notification; returns the count."""
    rows = list(
        db.scalars(
            select(Notification).where(
                *visibility_conditions(db, ctx), Notification.read_at.is_(None)
            )
        )
    )
    now = datetime.now(UTC)
    for row in rows:
        row.read_at = now
    db.commit()
    return len(rows)
