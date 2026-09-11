from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.core.security import create_token
from app.db.session import get_sessionmaker
from app.services.integration_keys import issue_key

ORG_1 = "OR-DEM00001"  # sample_bank_seed.DEMO_ORG_ID
ORG_2 = "OR-1S000002"  # sample_bank_seed.ISOLATED_ORG_ID
USER_1 = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
USER_2 = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def headers(
    org_id: str = ORG_1,
    user_id: UUID | None = None,
    roles: Sequence[str] = ("admin",),
    authorization_version: int = 1,
) -> dict[str, str]:
    """Auth headers for a request: a signed bearer access token for the tenant.

    (Replaces the former X-Org-Id/X-User-Id demo trust.) Defaults to an ``admin``
    token so existing tests keep passing; RBAC-specific tests pass a narrower role.
    """
    if user_id is None:
        user_id = USER_2 if org_id == ORG_2 else USER_1
    token = create_token(
        subject=user_id,
        organization_id=org_id,
        roles=list(roles),
        authorization_version=authorization_version,
        token_type="access",
        email="test@aequoros.example",
        settings=get_settings().auth,
    )
    return {"Authorization": f"Bearer {token}"}


def integration_key_headers(bank_id: str, organization_id: str = ORG_1) -> dict[str, str]:
    """Issue the real bank-scoped machine credential used by push-flow tests."""
    with get_sessionmaker()() as db:
        db.info["organization_id"] = organization_id
        issued = issue_key(
            db,
            TenantContext(
                organization_id=organization_id,
                actor_user_id=USER_1,
                authorization_version=1,
            ),
            bank_id,
            "Test push feed",
        )
    return {"Authorization": f"Bearer {issued.key}"}
