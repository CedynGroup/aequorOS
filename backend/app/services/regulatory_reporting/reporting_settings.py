"""Per-bank reporting settings — the deadline-override map (item W6-7).

The registry ships placeholder monthly deadlines where BoG has not published
the real day (e.g. the BSD2 CAR return at day 14, the FX-NOP monthly summary at
day 10). Rather than hard-code a guess, Bank-IT records the confirmed day per
return here at onboarding; ``calendar.list_obligations`` then computes the due
date with ``monthly_day(override)`` instead of the registry default.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import RegulatoryReportingSettings
from app.schemas.regulatory_reporting import ReportingSettingsPut, ReportingSettingsRead
from app.services.audit import record_event
from app.services.regulatory_reporting.common import get_bank_or_404, require_actor


def _read(settings: RegulatoryReportingSettings) -> ReportingSettingsRead:
    return ReportingSettingsRead(
        bank_id=settings.bank_id,
        deadline_overrides={
            str(code): int(day) for code, day in settings.deadline_overrides.items()
        },
        created_at=settings.created_at,
        updated_at=settings.updated_at,
    )


def _settings_row(
    db: Session, ctx: TenantContext, bank_id: str
) -> RegulatoryReportingSettings | None:
    return db.scalar(
        select(RegulatoryReportingSettings).where(
            RegulatoryReportingSettings.organization_id == ctx.organization_id,
            RegulatoryReportingSettings.bank_id == bank_id,
        )
    )


def get_reporting_settings(db: Session, ctx: TenantContext, bank_id: str) -> ReportingSettingsRead:
    bank = get_bank_or_404(db, ctx, bank_id)
    settings = _settings_row(db, ctx, bank.id)
    if settings is None:
        # An unconfigured bank simply has no overrides — return the empty view
        # rather than 404 so callers can render the (blank) settings form.
        now = datetime.now(UTC)
        return ReportingSettingsRead(
            bank_id=bank.id, deadline_overrides={}, created_at=now, updated_at=now
        )
    return _read(settings)


def put_reporting_settings(
    db: Session, ctx: TenantContext, bank_id: str, payload: ReportingSettingsPut
) -> ReportingSettingsRead:
    require_actor(ctx)
    bank = get_bank_or_404(db, ctx, bank_id)
    settings = _settings_row(db, ctx, bank.id)
    created = settings is None
    if settings is None:
        settings = RegulatoryReportingSettings(
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            deadline_overrides={},
        )
        db.add(settings)
    settings.deadline_overrides = dict(payload.deadline_overrides)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="regulatory_reporting_settings.updated",
        entity_type="regulatory_reporting_settings",
        entity_id=settings.id,
        details={
            "bank_id": str(bank.id),
            "created": created,
            "deadline_overrides": settings.deadline_overrides,
        },
    )
    db.commit()
    return _read(settings)
