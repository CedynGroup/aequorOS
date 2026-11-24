# pyright: standard
"""Test-only registry helpers: publish the synthetic definitions through the real workflow."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.audit.chain import SYSTEM, append
from app.auth.staff import StaffPrincipal
from app.core.context import AppContext
from app.db.models import ReturnDefinition
from app.returns.service import DraftIn, approve, create_draft, current_published, definition
from app.seed.staff import sign_in
from tests.synthetic_definitions import (
    SYNTHETIC_DEFINITIONS,
    DefinitionSpec,
    build_rules,
    build_schema,
)


def register_definition(
    session: Session,
    ctx: AppContext,
    spec: DefinitionSpec,
    *,
    position: int,
    at: datetime | None = None,
) -> ReturnDefinition:
    """Bootstrap a definition row (reference data; there is no endpoint that creates codes)."""
    existing = definition(session, spec.code)
    if existing is not None:
        return existing
    now = at or ctx.clock.now()
    row = ReturnDefinition(
        code=spec.code,
        name=spec.name,
        cadence=spec.cadence,
        applies_to=list(spec.applies_to),
        synthetic=True,
        auto_accept=spec.auto_accept,
        position=position,
        created_at=now,
    )
    session.add(row)
    session.flush()
    append(
        session,
        ctx.keys,
        ctx.clock,
        actor=SYSTEM,
        category="configuration",
        at=now,
        action="Registered return definition",
        object_type="return_definition",
        object_ref=spec.code,
        detail={
            "name": spec.name,
            "cadence": spec.cadence,
            "applies_to": list(spec.applies_to),
            "synthetic": True,
        },
    )
    return row


def draft_from_spec(spec: DefinitionSpec, *, notice_days: int = 60) -> DraftIn:
    return DraftIn(
        version=spec.version,
        change_summary=spec.change_summary,
        first_reporting_date=spec.first_reporting_date,
        notice_days=notice_days,
        schema=build_schema(spec),
        rules=build_rules(spec),
    )


def publish_spec_as(
    session: Session,
    ctx: AppContext,
    spec: DefinitionSpec,
    drafter: StaffPrincipal,
    approvers: tuple[StaffPrincipal, StaffPrincipal],
    at: datetime,
) -> None:
    """Seed helper: draft and approve a synthetic version through the real workflow."""
    create_draft(session, ctx, drafter, spec.code, draft_from_spec(spec, notice_days=60), at=at)
    approve(session, ctx, approvers[0], spec.code, spec.version, at=at + timedelta(days=1))
    approve(session, ctx, approvers[1], spec.code, spec.version, at=at + timedelta(days=2))


def bootstrap_registry(
    session: Session, ctx: AppContext, at: datetime, codes: Iterable[str] | None = None
) -> list[DefinitionSpec]:
    """Register the synthetic definitions and publish 2026.1 of each: drafted by a returns
    policy officer, approved by a second policy officer and a senior supervisor."""
    wanted = set(codes) if codes is not None else None
    drafter = sign_in(session, ctx, "easante")
    policy = sign_in(session, ctx, "madjei")
    supervision = sign_in(session, ctx, "kowusu")
    published: list[DefinitionSpec] = []
    for position, spec in enumerate(SYNTHETIC_DEFINITIONS):
        if wanted is not None and spec.code not in wanted:
            continue
        row = register_definition(session, ctx, spec, position=position, at=at)
        if current_published(session, row.id) is None:
            publish_spec_as(session, ctx, spec, drafter, (policy, supervision), at)
        published.append(spec)
    return published
