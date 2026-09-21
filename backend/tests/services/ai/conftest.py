"""Shared AI-test setup: a consented tenant, and a model that cannot be real."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.ai import AiCommentarySettings
from app.services.ai import client as ai_client
from tests.api.helpers import ORG_1, USER_1


@pytest.fixture
def ai_enabled(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The deployment kill-switch ON, in an undeployed environment."""
    monkeypatch.setenv("AI_COMMENTARY_ENABLED", "1")
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def consented_tenant(db_session: Session) -> AiCommentarySettings:
    """A tenant that has switched AI on under the current consent version."""
    from app.db.base import utc_now  # noqa: PLC0415 - fixture-local

    row = AiCommentarySettings(
        organization_id=ORG_1,
        enabled=True,
        enabled_features=["icaap_drafting"],
        descriptor_only=False,
        consent_version=get_settings().ai.consent_version,
        consented_by=USER_1,
        consented_at=utc_now(),
        updated_by=USER_1,
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def recorded_model() -> Iterator[ai_client.RecordedModel]:
    """A canned model. No test ever reaches the network."""
    model = ai_client.RecordedModel([])
    with ai_client.use_model(model):
        yield model
