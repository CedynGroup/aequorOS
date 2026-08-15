"""Act-as-examiner impersonation ON THE TENANT API — the isolation crown jewel.

The operator control plane mints a READ-ONLY, session-bound impersonation token
(``app/core/security.mint_impersonation_token``); here we prove the tenant API
accepts it EXACTLY as a read-only examiner scoped to ONE tenant, blocks every
mutation, rejects expired tokens, fails closed when the dedicated secret is
unset, and leaves the normal access-token path completely unaffected.

The token is minted directly with the same dedicated secret the app verifies
with (``get_settings().auth.impersonation_jwt_secret``, pinned by the root
conftest) — a faithful stand-in for the operator mint endpoint, kept on the
tenant side so the accept path is exercised in isolation.
"""

from __future__ import annotations

import datetime as dt
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core import security
from app.core.config import get_settings
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.models import Bank
from tests.api.helpers import ORG_1, ORG_2, headers

_SESSION_ID = "11111111-2222-4333-8444-555555555555"
_OPERATOR = "ops@aequoros.com"


def _impersonation_token(
    org_id: str,
    *,
    session_id: str = _SESSION_ID,
    operator: str = _OPERATOR,
    ttl_minutes: float = 15,
    secret: str | None = None,
) -> str:
    """Mint an act-as-examiner token the tenant API will verify."""
    resolved = secret or get_settings().auth.impersonation_jwt_secret
    assert resolved, "the impersonation secret must be configured for this helper"
    now = utc_now()
    return security.mint_impersonation_token(
        organization_id=org_id,
        act_operator=operator,
        session_id=session_id,
        secret=resolved,
        issued_at=now,
        expires_at=now + dt.timedelta(minutes=ttl_minutes),
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _add_bank(org_id: str, bank_id: str, name: str) -> None:
    """Insert one bank under ``org_id`` (RLS-correct on Postgres via the GUC)."""
    session = get_sessionmaker()()
    session.info["organization_id"] = org_id
    try:
        session.add(
            Bank(
                id=bank_id,
                organization_id=org_id,
                name=name,
                short_name=name[:8],
                currency="GHS",
                jurisdiction_code="GH",
                license_type="universal_bank",
            )
        )
        session.commit()
    finally:
        session.close()


def test_impersonation_token_reads_as_examiner(db_client: TestClient) -> None:
    _add_bank(ORG_1, "BK-IMPRSN01", "Impersonation Read Bank")

    response = db_client.get(
        "/api/v1/banks", headers=_bearer(_impersonation_token(ORG_1))
    )
    assert response.status_code == 200, response.text
    bank_ids = [bank["id"] for bank in response.json()["banks"]]
    assert "BK-IMPRSN01" in bank_ids


def test_impersonation_is_single_org_isolated(db_client: TestClient) -> None:
    """The core safety test: a token minted for org A can read ONLY org A."""
    _add_bank(ORG_1, "BK-ORGA0001", "Org A Bank")
    _add_bank(ORG_2, "BK-ORGB0001", "Org B Bank")

    org_a = db_client.get("/api/v1/banks", headers=_bearer(_impersonation_token(ORG_1)))
    assert org_a.status_code == 200, org_a.text
    a_ids = {bank["id"] for bank in org_a.json()["banks"]}
    assert "BK-ORGA0001" in a_ids
    assert "BK-ORGB0001" not in a_ids  # org B's row is invisible to org A's token

    org_b = db_client.get("/api/v1/banks", headers=_bearer(_impersonation_token(ORG_2)))
    assert org_b.status_code == 200, org_b.text
    b_ids = {bank["id"] for bank in org_b.json()["banks"]}
    assert "BK-ORGB0001" in b_ids
    assert "BK-ORGA0001" not in b_ids  # …and vice versa


def test_impersonation_blocks_every_mutation(db_client: TestClient) -> None:
    """Representative POST/PUT mutations across modules all return 403.

    The auth dependency (``get_mutation_tenant_context`` / ``get_approver_tenant_context``)
    short-circuits before body validation, so a dummy id + empty body still
    exercises the gate.
    """
    token = _impersonation_token(ORG_1)
    case_id = str(uuid4())
    mutations = [
        ("post", "/api/v1/banks/BK-IMPRSN01/regulatory-runs"),
        ("post", f"/api/v1/cases/{case_id}/calculation-runs"),
        ("post", f"/api/v1/cases/{case_id}/scenarios"),
        ("put", "/api/v1/banks/BK-IMPRSN01/capital-plan"),
        ("post", "/api/v1/banks/BK-IMPRSN01/capital-plan/approve"),  # approver ladder
    ]
    for method, path in mutations:
        response = getattr(db_client, method)(path, headers=_bearer(token), json={})
        assert response.status_code == 403, f"{method.upper()} {path} -> {response.status_code}"


def test_expired_impersonation_token_is_401(db_client: TestClient) -> None:
    expired = _impersonation_token(ORG_1, ttl_minutes=-1)
    response = db_client.get("/api/v1/banks", headers=_bearer(expired))
    assert response.status_code == 401, response.text


def test_impersonation_fails_closed_when_secret_unset(
    db_client: TestClient, monkeypatch
) -> None:
    """With the dedicated secret unset, a would-be impersonation token is
    rejected: the accept branch is a no-op, so the token falls through to the
    normal decode (wrong secret) and is refused. No impersonation is possible."""
    # Mint while the secret is still set (a genuine impersonation token) …
    token = _impersonation_token(ORG_1)
    # … then take the secret away.
    monkeypatch.setenv("IMPERSONATION_JWT_SECRET", "")
    get_settings.cache_clear()
    assert get_settings().auth.impersonation_jwt_secret is None

    response = db_client.get("/api/v1/banks", headers=_bearer(token))
    assert response.status_code == 401, response.text


def test_normal_access_token_unaffected(db_client: TestClient) -> None:
    """Regression: a normal tenant access token authenticates and reads/writes
    exactly as before — the impersonation branch never touches it."""
    admin = headers(ORG_1, roles=("admin",))

    # Read.
    listing = db_client.get("/api/v1/banks", headers=admin)
    assert listing.status_code == 200, listing.text

    # Write (a real mutation succeeds).
    created = db_client.post(
        "/api/v1/cases",
        headers=admin,
        json={"title": "Regression case", "case_type": "vendor"},
    )
    assert created.status_code == 201, created.text

    # The mutation gate ADMITS a normal analyst+ token (not 401/403): a missing
    # bank yields the handler's own 404, proving auth passed.
    gate = db_client.post(
        "/api/v1/banks/BK-NOEXIST9/regulatory-runs",
        headers=admin,
        json={
            "module": "liquidity",
            "reporting_period_id": str(uuid4()),
            "scenario_code": "baseline",
        },
    )
    assert gate.status_code not in (401, 403), gate.text
