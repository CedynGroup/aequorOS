"""Observe legal status and announce upcoming commencement in the daily scan."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.identity.public import Bank
from app.notifications.public import notifications
from app.reporting.models.packages import RegulatoryReportingSettings
from app.reporting.service.eligibility import resolve_eligibility
from app.reporting.service.registry import REGISTRY

REMINDER_DAYS = (1, 7, 30, 90)


def instrument_events(
    previous: str | None, current: str, effective: date | None, today: date
) -> tuple[str, ...]:
    """A first observation establishes a baseline; drafts never acquire a deadline."""
    status = current.split(":", 1)[0]
    events: list[str] = []
    if previous is not None and previous != current:
        events.append("changed")
    if status == "final_not_in_force" and effective is not None:
        days = (effective - today).days
        threshold = next((window for window in REMINDER_DAYS if 0 < days <= window), None)
        if threshold is not None:
            events.append(f"commencement_{threshold}")
    return tuple(events)


def scan_instrument_statuses(db: Session, ctx: TenantContext, bank: Bank, today: date) -> int:
    """Persist the observation and notifications together in the scan transaction."""
    # Serialize initialization as well as subsequent scans for this bank.
    db.execute(
        select(Bank.id)
        .where(Bank.organization_id == ctx.organization_id, Bank.id == bank.id)
        .with_for_update()
    ).scalar_one()
    settings = db.scalar(
        select(RegulatoryReportingSettings).where(
            RegulatoryReportingSettings.organization_id == ctx.organization_id,
            RegulatoryReportingSettings.bank_id == bank.id,
        )
    )
    if settings is None:
        settings = RegulatoryReportingSettings(
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            deadline_overrides={},
            instrument_observations={},
        )
        db.add(settings)
    observations = dict(settings.instrument_observations)
    eligibility = resolve_eligibility(db, ctx, bank, as_of=today)
    emitted = 0
    for definition in REGISTRY.values():
        if (
            eligibility.institution_class not in definition.institution_classes
            or (
                definition.jurisdictions
                and eligibility.jurisdiction_code not in definition.jurisdictions
            )
            or (
                eligibility.regulator is not None
                and definition.regulator.casefold() != eligibility.regulator.casefold()
            )
        ):
            continue
        effective = eligibility.effective_from(definition)
        status = eligibility.instrument_status(definition, today)
        current = f"{status}:{effective.isoformat() if effective else 'unknown'}"
        previous = observations.get(definition.code)
        for event in instrument_events(previous, current, effective, today):
            # Reminder keys survive repeat scans; changes record the observed transition.
            key = f"{definition.code}:{current}:{event}"
            if event == "changed":
                key += f":{today.isoformat()}"
            if observations.get(f"sent:{key}") == "yes":
                continue
            notifications.emit(
                db,
                ctx,
                type=f"reporting.instrument.{event}:{definition.code}:",
                severity="info" if status in ("exposure_draft", "unpublished") else "warning",
                title=f"{definition.code}: "
                + ("rule status changed" if event == "changed" else "commencement approaching"),
                body=(
                    f"{definition.title}. Legal status: {status.replace('_', ' ')}. "
                    f"Effective date: {effective.isoformat() if effective else 'not established'}. "
                    "Review the regulatory instruments register for source evidence. "
                    + (
                        "Preparation only, no filing deadline or penalty applies to this draft."
                        if status == "exposure_draft"
                        else ""
                    )
                ),
                entity_type="bank",
                entity_id=bank.id,
            )
            observations[f"sent:{key}"] = "yes"
            emitted += 1
        observations[definition.code] = current
    settings.instrument_observations = observations
    return emitted
