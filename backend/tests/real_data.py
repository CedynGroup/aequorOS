"""Shared constants + helpers for the opt-in DB-backed real-data suite.

The engine/API tests that used to build on the retired sample-bank seed now run
against the ACTUAL primary database via the ``real_client`` fixture (defined in
``tests/conftest.py``). This module is the importable home for the real-tenant
identities, the auth-header helper, and the opt-in skip marker so every migrated
test file shares one source of truth.

Opt in by pointing ``REAL_DATA_DATABASE_URL`` at the primary's tenant-scoped
app-role URL (the same value as ``DATABASE_URL`` in ``backend/.env``) so RLS is
genuinely enforced — cross-tenant reads then see nothing, which is what the
isolation tests assert. Unset -> the whole suite skips, keeping the hermetic CI
run green. Mirrors ``tests/live_data`` (``LIVE_DATA_DATABASE_URL``).
"""

from __future__ import annotations

import os
from uuid import UUID

import pytest

from app.core.config import get_settings
from app.core.security import create_token

REAL_DATA_DATABASE_URL = os.environ.get("REAL_DATA_DATABASE_URL", "").strip()

requires_real_data = pytest.mark.skipif(
    not REAL_DATA_DATABASE_URL,
    reason="opt-in DB-backed suite: set REAL_DATA_DATABASE_URL to the primary (app-role) URL",
)

# Real primary identities (resolved 2026-08-15; the retired seed's OR-DEM00001 /
# BK-SAMP0001 are gone). Sample Bank Ltd is the GHS/GH bank with 120 reporting
# periods, in the primary org.
REAL_ORG_ID = "OR-QVXE0FQV"
REAL_BANK_ID = "BK-0PMD7Z5M"
REAL_USER_ID = UUID("793a30bc-221d-4d00-89cf-8ec5a9a5bef1")
REAL_USER_EMAIL = "admin@samplebank.com.gh"

# A DIFFERENT real tenant with an ACTIVE admin (Horizon Bank Nigeria, TEST),
# used as the counterparty in cross-tenant isolation tests: this user
# authenticates fine, but RLS hides Sample Bank's rows, so its requests 404.
# (The dedicated OR-K6BYNRZA isolation tenant has only an inactive user, which
# would fail auth (401) before RLS ever applies — no good for isolation tests.)
REAL_OTHER_ORG_ID = "OR-ADGE1DPV"
REAL_OTHER_BANK_ID = "BK-7CF5N6KS"
REAL_OTHER_USER_ID = UUID("21287655-9fec-445f-ae39-be54c20b0bb0")
REAL_OTHER_USER_EMAIL = "admin@horizonbank-test.example"


def real_headers(
    org_id: str = REAL_ORG_ID,
    user_id: UUID = REAL_USER_ID,
    roles: tuple[str, ...] = ("admin",),
    email: str = REAL_USER_EMAIL,
    authorization_version: int = 1,
) -> dict[str, str]:
    """Signed bearer token for a REAL user in a REAL org on the primary DB."""
    token = create_token(
        subject=user_id,
        organization_id=org_id,
        roles=list(roles),
        authorization_version=authorization_version,
        token_type="access",
        email=email,
        settings=get_settings().auth,
    )
    return {"Authorization": f"Bearer {token}"}


def other_headers(roles: tuple[str, ...] = ("admin",)) -> dict[str, str]:
    """Auth headers for a DIFFERENT tenant's active admin — for isolation tests
    (authenticates, then RLS returns 404 for Sample Bank resources)."""
    return real_headers(
        org_id=REAL_OTHER_ORG_ID,
        user_id=REAL_OTHER_USER_ID,
        roles=roles,
        email=REAL_OTHER_USER_EMAIL,
    )
