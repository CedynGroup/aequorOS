from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import create_token

ORG_1 = "OR-DEM00001"  # sample_bank_seed.DEMO_ORG_ID
ORG_2 = "OR-1S000002"  # sample_bank_seed.ISOLATED_ORG_ID
USER_1 = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
USER_2 = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def headers(
    org_id: str = ORG_1,
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


def relax_signing(client: TestClient, return_code: str, *, org_id: str = ORG_1) -> None:
    """Configure a signature-OPTIONAL policy through the real admin endpoint.

    Signing is required for every return by default, so an API suite that drives
    a package to a channel meets the attestation gate on the way. Suites that are
    about something else opt the return out exactly as an administrator would —
    an audited PUT, not a row poked into the database — so the escape hatch under
    test is the one a bank actually has.

    Never reach for this in a suite that is *about* the filing gate: that would
    be deleting the gate while keeping the tests green.
    """
    response = client.put(
        "/api/v1/attestation/signing-policies",
        headers=headers(org_id=org_id),
        json={
            "return_code": return_code,
            "required_signatures": [],
            "require_signature": False,
            "effective_from": "2000-01-01",
            "reason": "test fixture: this suite is not about the signing gate",
        },
    )
    assert response.status_code in (200, 201), response.text
