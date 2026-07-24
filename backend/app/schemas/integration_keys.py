from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntegrationKeyRead(ClosedModel):
    id: UUID
    label: str
    # Display fragment only ("aeq_live_AB12…") — the raw key is never readable.
    key_prefix: str
    created_at: datetime
    created_by: UUID | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class IntegrationKeyListRead(ClosedModel):
    keys: list[IntegrationKeyRead]


class IntegrationKeyIssueRequest(ClosedModel):
    label: str = Field(min_length=1, max_length=80)


class IntegrationKeyIssued(ClosedModel):
    # Shown exactly once; only the hash is stored.
    key: str
    record: IntegrationKeyRead


class IntegrationKeyRevokeRequest(ClosedModel):
    reason: str = Field(min_length=1, max_length=500)
