"""Who a request or job acts for: the tenant context every service receives.

It lives in the kernel so services and jobs can take a ``TenantContext`` without
importing the HTTP dependency module. ``app.api.deps`` builds it from a request's
credentials and re-exports it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class TenantContext:
    # The platform tenant identifier (OR-XXXXXXXX) — the organizations PK.
    organization_id: str
    actor_user_id: UUID | None = None
    roles: tuple[str, ...] = ()
    # Present on normal app tokens and compared with users.authorization_version
    # before their role claims are accepted. Integration keys and impersonation
    # use separate credential lifecycles and leave this unset.
    authorization_version: int | None = None
    # Present only for the integration-key credential branch. A legacy key has
    # no bank target and therefore cannot satisfy machine ingest authorization.
    integration_key_id: UUID | None = None
    integration_key_bank_id: str | None = None
    # Set ONLY under operator act-as-examiner impersonation: the originating
    # inspector session id. Its presence marks the principal as a read-only
    # operator view (actor_user_id is None — the actor is staff, not a tenant
    # user). RLS still pins to ``organization_id``, so a single-tenant view.
    impersonation_context: str | None = None
    # The email of the operator acting as examiner (impersonation only) —
    # provenance for audit; never a tenant identity.
    actor_operator: str | None = None
