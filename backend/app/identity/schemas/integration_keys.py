from __future__ import annotations

from datetime import datetime
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.authorization import (
    DataScope,
    normalise_data_scope_values,
)

# Reader issuance is retired until BI has a governed delegation flow.
IntegrationKeyPurpose = Literal["writer"]
WRITER_PURPOSE: Final[IntegrationKeyPurpose] = "writer"


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntegrationKeyRead(ClosedModel):
    id: UUID
    bank_id: str | None = Field(title="Integration key bank ID")
    label: str
    # Display fragment only ("aeq_live_AB12…") — the raw key is never readable.
    key_prefix: str
    created_at: datetime
    created_by: UUID | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    #: DERIVED from the machine binding the key's service identity holds, never
    #: stored on the key row: the binding IS the authority, so a second copy of
    #: it could disagree with the thing that decides. ``None`` means no supported
    #: purpose, including legacy rows and reader bindings. Historical
    #: credentials remain listable for lifecycle evidence.
    purpose: IntegrationKeyPurpose | None = None
    # Reserved scope metadata; data push keys read no slice.
    data_scope_kind: DataScope | None = None
    data_scope_values: list[str] = Field(default_factory=list)


class IntegrationKeyListRead(ClosedModel):
    keys: list[IntegrationKeyRead]


class IntegrationKeyIssueRequest(ClosedModel):
    bank_id: str = Field(title="Integration key bank ID")
    label: str = Field(min_length=1, max_length=80)
    # Existing data push callers may omit the purpose.
    purpose: IntegrationKeyPurpose = WRITER_PURPOSE
    data_scope_kind: DataScope = DataScope.ALL
    data_scope_values: list[str] = Field(
        default_factory=list,
        max_length=500,
        title="Selected branch codes or region names",
    )

    @model_validator(mode="after")
    def validate_data_scope(self) -> IntegrationKeyIssueRequest:
        """Data push keys cannot carry read scopes."""
        values = normalise_data_scope_values(self.data_scope_values)
        if self.data_scope_kind is not DataScope.ALL or values:
            raise ValueError(
                "A data push key writes rather than reads, so it cannot be limited "
                "to selected branches or regions."
            )
        self.data_scope_values = list(values)
        return self


class IntegrationKeyIssued(ClosedModel):
    # Shown exactly once; only the hash is stored.
    key: str
    record: IntegrationKeyRead


class IntegrationKeyRevokeRequest(ClosedModel):
    reason: str = Field(min_length=1, max_length=500)
