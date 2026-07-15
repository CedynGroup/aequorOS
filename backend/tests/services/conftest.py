from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import Settings
from tests.conftest import FakeStorage
from tests.services.factories import ServiceFactories


@pytest.fixture
def service_factories(
    db_session: Session,
    fake_storage: FakeStorage,
    test_settings: Settings,
    tenant_ctx: TenantContext,
) -> ServiceFactories:
    return ServiceFactories(db_session, fake_storage, test_settings, tenant_ctx)
