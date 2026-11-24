"""Per-bank reporting settings: deadline overrides and the filing profile.

The registry ships placeholder monthly deadlines where BoG has not published
the real day (e.g. the BSD2 CAR return at day 14, the FX-NOP monthly summary at
day 10). Rather than hard-code a guess, Bank-IT records the confirmed day per
return here at onboarding; ``calendar.list_obligations`` then computes the due
date with ``monthly_day(override)`` instead of the registry default (item W6-7).

The filing profile is what every channel shares, set once: the reporting basis
and the reporting contact (the institution code stays on the institution
profile), plus the bank's default channel for approved returns.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.authorization import Module, Permission, Sensitivity
from app.identity.public import (
    Bank,
    orass_institution_code,
    require_institution_coverage,
    require_resolved_bank_permission,
)
from app.reporting.models.packages import RegulatoryChannelConfig, RegulatoryReportingSettings
from app.reporting.schemas.packages import (
    ChannelCode,
    DefaultChannelCode,
    FilingProfilePut,
    FilingProfileRead,
    FilingProfileWriteAcknowledgement,
    ReportingBasis,
    ReportingSettingsPut,
    ReportingSettingsRead,
    ReportingSettingsWriteAcknowledgement,
)
from app.reporting.service.common import require_actor
from app.reporting.service.configure_access import require_reporting_configure_access
from app.services.audit import record_event


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


def get_or_create_reporting_settings(
    db: Session, ctx: TenantContext, bank_id: str
) -> tuple[RegulatoryReportingSettings, bool]:
    db.execute(
        select(Bank.id)
        .where(Bank.organization_id == ctx.organization_id, Bank.id == bank_id)
        .with_for_update()
    ).scalar_one()
    settings = _settings_row(db, ctx, bank_id)
    created = settings is None
    if settings is None:
        settings = RegulatoryReportingSettings(
            organization_id=ctx.organization_id,
            bank_id=bank_id,
            deadline_overrides={},
            instrument_observations={},
        )
        db.add(settings)
        db.flush()
    return settings, created


def get_reporting_settings(db: Session, ctx: TenantContext, bank_id: str) -> ReportingSettingsRead:
    # The deadline-override map is restricted-surface configuration of this
    # bank: reading it demands the same exact-bank reg/restricted sentence its
    # write does, at view. Coverage first, then the scoped denial; the empty-map
    # fallback below is answered only once authorization has passed.
    bank, _ = require_institution_coverage(db, ctx, bank_id)
    require_resolved_bank_permission(
        db,
        ctx,
        bank,
        permission=Permission.VIEW,
        module=Module.REGULATORY,
        sensitivity=Sensitivity.RESTRICTED,
        surface="reporting_settings_read",
    )
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
) -> ReportingSettingsWriteAcknowledgement:
    # Configuration duty (VAL-CFG-007): exact reg/configure/restricted authority
    # for this bank BEFORE any mutation or audit row. The write-only
    # acknowledgement (D27/D34) echoes the submitted map only — the stored row's
    # timestamps are read through the tenant-gated GET.
    bank = require_reporting_configure_access(db, ctx, bank_id)
    require_actor(ctx)
    settings, created = get_or_create_reporting_settings(db, ctx, bank.id)
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
    return ReportingSettingsWriteAcknowledgement(
        bank_id=bank.id,
        deadline_overrides=dict(payload.deadline_overrides),
        created=created,
    )


# -- filing profile ----------------------------------------------------------


def _require_restricted_view(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank, _ = require_institution_coverage(db, ctx, bank_id)
    require_resolved_bank_permission(
        db,
        ctx,
        bank,
        permission=Permission.VIEW,
        module=Module.REGULATORY,
        sensitivity=Sensitivity.RESTRICTED,
        surface="reporting_settings_read",
    )
    return bank


def _basis(value: object) -> ReportingBasis:
    return "consolidated" if value == "consolidated" else "solo"


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _default(value: str | None) -> DefaultChannelCode | None:
    match value:
        case "reporting_gateway" | "orass_api" | "manual":
            return value
        case _:
            return None


def get_filing_profile(db: Session, ctx: TenantContext, bank_id: str) -> FilingProfileRead:
    bank = _require_restricted_view(db, ctx, bank_id)
    settings = _settings_row(db, ctx, bank.id)
    profile: dict[str, Any] = dict(settings.filing_profile) if settings is not None else {}
    return FilingProfileRead(
        bank_id=bank.id,
        institution_code=orass_institution_code(db, ctx, bank.id),
        reporting_basis=_basis(profile.get("reporting_basis")),
        contact_name=_text(profile.get("contact_name")),
        contact_email=_text(profile.get("contact_email")),
        default_channel=_default(settings.default_channel) if settings is not None else None,
        updated_at=settings.updated_at if settings is not None else None,
        orass_ready=_channel_ready(db, ctx, bank.id, "orass_api"),
    )


def _channel_ready(db: Session, ctx: TenantContext, bank_id: str, channel: str) -> bool:
    row = db.scalar(
        select(RegulatoryChannelConfig).where(
            RegulatoryChannelConfig.organization_id == ctx.organization_id,
            RegulatoryChannelConfig.bank_id == bank_id,
            RegulatoryChannelConfig.channel == channel,
        )
    )
    if row is None:
        return False
    key = "enrolled_at" if channel == "reporting_gateway" else "api_base_url"
    return bool(row.config.get(key))


_NOT_READY = {
    "reporting_gateway": (
        "Your bank is not yet enrolled on the AequorOS Reporting Gateway, so it cannot be the "
        "default channel. Ask your AequorOS team to enrol it."
    ),
    "orass_api": (
        "ORASS is not connected for your bank, so it cannot be the default channel. "
        "Add the ORASS connection details first."
    ),
}


def put_filing_profile(
    db: Session, ctx: TenantContext, bank_id: str, payload: FilingProfilePut
) -> FilingProfileWriteAcknowledgement:
    bank = require_reporting_configure_access(db, ctx, bank_id)
    require_actor(ctx)
    channel = payload.default_channel
    if channel in _NOT_READY and not _channel_ready(db, ctx, bank.id, channel):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=_NOT_READY[channel]
        )
    settings, created = get_or_create_reporting_settings(db, ctx, bank.id)
    settings.filing_profile = {
        "reporting_basis": payload.reporting_basis,
        "contact_name": _text(payload.contact_name),
        "contact_email": _text(payload.contact_email),
    }
    settings.default_channel = channel
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
            "filing_profile": settings.filing_profile,
            "default_channel": channel,
        },
    )
    db.commit()
    return FilingProfileWriteAcknowledgement(
        bank_id=bank.id,
        reporting_basis=payload.reporting_basis,
        contact_name=_text(payload.contact_name),
        contact_email=_text(payload.contact_email),
        default_channel=channel,
        created=created,
    )


#: Registry channels a bank's default channel replaces: the regulator's
#: electronic returns system. Returns the registry files by email or records
#: manually keep their own route.
ELECTRONIC_RETURN_CHANNELS = ("orass_api", "orass_sandbox")


def effective_channel(
    registry_default: ChannelCode,
    bank_default: DefaultChannelCode | None,
    *,
    return_code: str | None = None,
    allowed: tuple[ChannelCode, ...] | None = None,
) -> ChannelCode:
    """The channel a return uses when none is chosen for one filing.

    The bank's default never replaces the registry's with a channel the return does
    not allow. The AequorOS Reporting Gateway takes every return it carries a
    definition for (:func:`gateway_catalog.accepts`), whatever the registry default;
    any other bank default replaces only an electronic (ORASS) registry default.
    """
    from app.reporting.service import gateway_catalog  # noqa: PLC0415 - registry-heavy

    if bank_default is None or (allowed is not None and bank_default not in allowed):
        return registry_default
    if bank_default == "reporting_gateway":
        accepted = return_code is not None and gateway_catalog.accepts(return_code)
        return bank_default if accepted else registry_default
    if registry_default in ELECTRONIC_RETURN_CHANNELS:
        return bank_default
    return registry_default


def filing_defaults(
    db: Session, ctx: TenantContext, bank_id: str
) -> tuple[DefaultChannelCode | None, dict[str, Any]]:
    """The bank's default channel and the profile values channels fall back on.

    For the submission pipeline, after the caller's own authorization.
    """
    settings = _settings_row(db, ctx, bank_id)
    profile: dict[str, Any] = dict(settings.filing_profile) if settings is not None else {}
    shared = {
        key: value
        for key, value in {
            "reporting_basis": profile.get("reporting_basis"),
            "contact_name": _text(profile.get("contact_name")),
            "contact_email": _text(profile.get("contact_email")),
        }.items()
        if value
    }
    return (_default(settings.default_channel) if settings is not None else None), shared
