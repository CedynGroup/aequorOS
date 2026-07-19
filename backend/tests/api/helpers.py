from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.core.config import get_settings
from app.core.security import create_token

ORG_1 = UUID("11111111-1111-4111-8111-111111111111")
ORG_2 = UUID("22222222-2222-4222-8222-222222222222")
USER_1 = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
USER_2 = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def headers(
    org_id: UUID = ORG_1,
    user_id: UUID | None = None,
    roles: Sequence[str] = ("admin",),
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
        token_type="access",
        email="test@aequoros.example",
        settings=get_settings().auth,
    )
    return {"Authorization": f"Bearer {token}"}
