from __future__ import annotations

from datetime import datetime
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.authorization import (
    DATA_SCOPE_VALUE_MAX_LENGTH,
    DataScope,
    normalise_data_scope_values,
)

#: What a machine credential is FOR. One key, one purpose, one indivisible
#: binding — and the two purposes are deliberately disjoint authorities: a
#: ``writer`` pushes canonical data and may read nothing, a ``reader`` pulls the
#: curated analytics feed and may write nothing. A ``Literal`` rather than an
#: enum class, following ``GrantableRoleBundle``: it inlines in OpenAPI and adds
#: no component name for the generated client to map.
IntegrationKeyPurpose = Literal["writer", "reader"]

WRITER_PURPOSE: Final[IntegrationKeyPurpose] = "writer"
READER_PURPOSE: Final[IntegrationKeyPurpose] = "reader"
INTEGRATION_KEY_PURPOSES: Final[tuple[IntegrationKeyPurpose, ...]] = (
    WRITER_PURPOSE,
    READER_PURPOSE,
)

#: Production copy for each purpose, so no surface prints the wire word.
PURPOSE_LABELS: Final[dict[IntegrationKeyPurpose, str]] = {
    WRITER_PURPOSE: "Data push",
    READER_PURPOSE: "Analytics feed",
}


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
    #: it could disagree with the thing that decides. ``None`` means the identity
    #: holds no machine binding at all — a pre-binding legacy row, which stays
    #: listable and revocable and authorizes nothing.
    purpose: IntegrationKeyPurpose | None = None
    #: The slice of the institution's book a reader key may pull. ``None`` for a
    #: writer key (a push credential reads nothing) and for a legacy row.
    data_scope_kind: DataScope | None = None
    data_scope_values: list[str] = Field(default_factory=list)


class IntegrationKeyListRead(ClosedModel):
    keys: list[IntegrationKeyRead]


class IntegrationKeyIssueRequest(ClosedModel):
    bank_id: str = Field(title="Integration key bank ID")
    label: str = Field(min_length=1, max_length=80)
    #: Defaulted to ``writer`` so every caller written before the analytics feed
    #: existed keeps meaning exactly what it meant: a push credential.
    purpose: IntegrationKeyPurpose = WRITER_PURPOSE
    data_scope_kind: DataScope = DataScope.ALL
    data_scope_values: list[str] = Field(
        default_factory=list,
        max_length=500,
        title="Selected branch codes or region names",
    )

    @model_validator(mode="after")
    def validate_data_scope(self) -> IntegrationKeyIssueRequest:
        """Refuse an unusable credential here, with a sentence, not at the CHECK.

        Values are normalised in place, so two spellings of one scope produce one
        stored row and one authority sentence. A feed key covers every module,
        so it cannot use the narrowing available only to Credit grants.
        """

        values = normalise_data_scope_values(self.data_scope_values)
        if self.purpose == READER_PURPOSE and self.data_scope_kind is not DataScope.ALL:
            raise ValueError(
                "Analytics feed keys cover every module and require whole-institution coverage. "
                "Branch and region narrowing is supported only for Credit."
            )
        if self.purpose == WRITER_PURPOSE and (self.data_scope_kind is not DataScope.ALL or values):
            raise ValueError(
                "A data push key writes rather than reads, so it cannot be limited "
                "to selected branches or regions."
            )
        if self.data_scope_kind is DataScope.ALL:
            if values:
                raise ValueError(
                    "Whole-institution access covers every branch, "
                    "so do not select branches or regions."
                )
        else:
            if not values:
                raise ValueError(
                    "Select at least one branch or region, "
                    "or choose whole-institution access instead."
                )
            overlong = sorted(value for value in values if len(value) > DATA_SCOPE_VALUE_MAX_LENGTH)
            if overlong:
                raise ValueError(
                    f"A branch or region name may be at most {DATA_SCOPE_VALUE_MAX_LENGTH} "
                    f"characters; this one is longer: {overlong[0][:40]}…"
                )
        self.data_scope_values = list(values)
        return self


class IntegrationKeyIssued(ClosedModel):
    # Shown exactly once; only the hash is stored.
    key: str
    record: IntegrationKeyRead


class IntegrationKeyRevokeRequest(ClosedModel):
    reason: str = Field(min_length=1, max_length=500)
