"""The egress gates: may this request reach an external model, right now?

One function, ``evaluate``, called at BOTH enqueue and run. That is not belt and
braces — it is the whole design. A request is enqueued by the API process and
run, possibly an hour later, by a different process reading a different
environment. In between, a founder can pull the deployment kill-switch, an
Organisation Owner can switch the tenant off, or the consent version can be
bumped. A gate checked only at enqueue would let every queued request through
after the switch was pulled, which is exactly what a kill-switch exists to stop.

Order matters: the first failure wins, and the deployment-level refusals come
before the tenant-level ones, so a tenant is never told "you have not consented"
by a deployment that would have refused them anyway.

Since D-053 the approval check is per VENDOR: the gate refuses only when NO vendor
in ``AI_PROVIDER_TIER`` has a reviewed configuration for this environment, and the
tier itself skips the individual vendors that do not. A deployment that has
reviewed one provider must be able to use it without waiting for the other two,
and an unreviewed provider must never be reachable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings, is_undeployed_environment
from app.models.ai import AiCommentarySettings
from app.services.ai import approvals
from app.services.ai.features import AiFeature

GateCode = Literal[
    "allowed",
    "deployment_disabled",
    "deployment_not_approved",
    "configuration_not_approved",
    "tenant_disabled",
    "feature_disabled",
    "consent_outdated",
    "queue_expired",
    "not_configured",
]

GatePhase = Literal["enqueue", "run"]

#: What a person is told. Never mentions the model, the provider or the key.
GATE_MESSAGES: dict[GateCode, str] = {
    "allowed": "AI drafting is available.",
    "deployment_disabled": "AI drafting is switched off on this platform.",
    "deployment_not_approved": (
        "AI drafting has not been approved for use in this environment yet."
    ),
    "configuration_not_approved": (
        "AI drafting has not been approved for use in this environment yet."
    ),
    "tenant_disabled": (
        "AI drafting is switched off for your organisation. An Organisation Owner "
        "can turn it on in Settings."
    ),
    "feature_disabled": (
        "Your organisation has not switched on AI drafting for this part of the product."
    ),
    "consent_outdated": (
        "The terms for AI drafting have changed. An Organisation Owner needs to read "
        "and accept them again before it can be used."
    ),
    "queue_expired": "This request waited too long and was cancelled.",
    "not_configured": "AI drafting is not configured on this platform.",
}


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    code: GateCode

    @property
    def message(self) -> str:
        return GATE_MESSAGES[self.code]


def _refuse(code: GateCode) -> GateDecision:
    return GateDecision(allowed=False, code=code)


def tenant_row(db: Session, organization_id: str) -> AiCommentarySettings | None:
    return db.scalar(
        select(AiCommentarySettings).where(AiCommentarySettings.organization_id == organization_id)
    )


def deployment_gate(settings: Settings | None = None) -> GateDecision:
    """The deployment-level half: kill-switch, then environment approval.

    Split out because the route dependency needs it BEFORE resolving a tenant:
    a platform with AI switched off should not have an AI route that answers
    differently depending on who asks.
    """
    settings = settings or get_settings()
    if not settings.ai.commentary_enabled:
        return _refuse("deployment_disabled")
    if not is_undeployed_environment(settings.app.app_env) and (
        settings.ai.production_approval_ref is None
    ):
        return _refuse("deployment_not_approved")
    return GateDecision(allowed=True, code="allowed")


def approved_vendors(
    feature: AiFeature, prompt_version: str, *, settings: Settings | None = None
) -> tuple[str, ...]:
    """Vendors in the tier whose EXACT configuration is approved here, in order.

    Every vendor in an undeployed environment; in a deployed one, only those with
    a reviewed ``(feature, prompt_version, vendor, model, effort, app_env)`` entry
    in ``approved_configurations.json``. Empty means the gate refuses — which is
    the state the file ships in.

    Credential-free: this runs in the API process at enqueue, which must not parse
    a model key, so it asks each adapter what it WOULD send rather than preparing
    it. The descriptor carries the model RESOLVED for this feature (D-061), so the
    key is the one the request will actually carry. A vendor that cannot even be
    described (no endpoint, no model, a pinned feature aimed at a floating id)
    counts as unapproved, because it could not be called either.
    """
    settings = settings or get_settings()
    order = settings.ai.provider_order
    if is_undeployed_environment(settings.app.app_env):
        return tuple(order)
    from app.services.ai import tiered  # noqa: PLC0415 - avoids an import cycle

    approved: list[str] = []
    for vendor in order:
        descriptor = tiered.describe(vendor, settings, feature)
        if descriptor is None:
            continue
        if (
            approvals.find(
                feature=feature,
                prompt_version=prompt_version,
                vendor=descriptor.vendor,
                model=descriptor.model,
                effort=descriptor.effort,
                app_env=settings.app.app_env,
            )
            is not None
        ):
            approved.append(vendor)
    return tuple(approved)


def evaluate(  # noqa: PLR0911, PLR0913 - one return per refusal reads better than a chain
    db: Session,
    organization_id: str,
    feature: AiFeature,
    *,
    phase: GatePhase,
    prompt_version: str,
    requested_at: datetime | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> GateDecision:
    """May a request for ``feature`` in ``organization_id`` call the model now?"""
    settings = settings or get_settings()

    deployment = deployment_gate(settings)
    if not deployment.allowed:
        return deployment

    if not approved_vendors(feature, prompt_version, settings=settings):
        return _refuse("configuration_not_approved")

    row = tenant_row(db, organization_id)
    if row is None or not row.enabled:
        return _refuse("tenant_disabled")
    if feature not in (row.enabled_features or []):
        return _refuse("feature_disabled")
    if row.consent_version != settings.ai.consent_version:
        return _refuse("consent_outdated")

    if phase == "run":
        if requested_at is not None:
            moment = now or datetime.now(UTC)
            aware = requested_at if requested_at.tzinfo else requested_at.replace(tzinfo=UTC)
            if moment - aware > timedelta(seconds=settings.ai.queue_expiry_seconds):
                return _refuse("queue_expired")
        # The credential check belongs to the RUN phase only: the API process
        # does not hold the key and must not conclude anything from its absence.
        from app.services.ai import client as client_module  # noqa: PLC0415 - lazy SDK boundary

        if not client_module.backend_configured(settings):
            return _refuse("not_configured")

    return GateDecision(allowed=True, code="allowed")


def descriptor_only(db: Session, organization_id: str) -> bool:
    """Whether this tenant's fact sheets must carry descriptors and no values.

    Defaults to the STRICTER answer when no row exists: a tenant that has not
    made a choice has not chosen to send figures.
    """
    row = tenant_row(db, organization_id)
    return True if row is None else bool(row.descriptor_only)


__all__ = [
    "GATE_MESSAGES",
    "GateCode",
    "GateDecision",
    "GatePhase",
    "approved_vendors",
    "deployment_gate",
    "descriptor_only",
    "evaluate",
    "tenant_row",
]
