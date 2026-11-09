"""Shared bank seeding helper: canonical test book plus DATA authority grants.

Lives outside any test module so suites other than the Data Engine's can reuse
it without a cross-suite test import.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.authorization import ModuleScope, RoleBundle, SensitivityScope
from app.db.session import get_sessionmaker
from tests.fixtures.canonical_bank_fixture import SAMPLE_BANK_ID, materialize_canonical_test_book
from tests.support.factories.authorization import grant_institution_authority
from tests.support.helpers import ORG_1, USER_1


def seed_bank(client: TestClient) -> str:
    _ = client  # initialize the test app's engine before opening a direct session
    session = get_sessionmaker()()
    try:
        materialize_canonical_test_book(session)
        grant_institution_authority(
            session,
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            user_id=USER_1,
            bundle=RoleBundle.ANALYST,
            module=ModuleScope.DATA,
            sensitivity=SensitivityScope.ALL,
        )
        # Mapping onboarding is a configure duty; the journey officer holds it
        # explicitly, never through the analyst bundle.
        grant_institution_authority(
            session,
            organization_id=ORG_1,
            bank_id=SAMPLE_BANK_ID,
            user_id=USER_1,
            bundle=RoleBundle.CONFIGURATOR,
            module=ModuleScope.DATA,
            sensitivity=SensitivityScope.ALL,
        )
        session.commit()
    finally:
        session.close()
    return SAMPLE_BANK_ID
