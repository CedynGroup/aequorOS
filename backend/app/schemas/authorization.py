"""Tenant grant-administration contracts.

One create payload represents one indivisible binding.  The four authority
dimensions are scalar enums by construction; there is no array-shaped request
that could fan out into a Cartesian product.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.authorization import (
    BindingStatus,
    GrantorType,
    InstitutionScope,
    ModuleScope,
    SensitivityScope,
)

GrantableRoleBundle = Literal[
    "viewer",
    "auditor",
    "analyst",
    "approver",
    "account_admin",
]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScopedGrantInput(ClosedModel):
    """The complete scalar scope shared by grant and SSO-approval flows."""

    role_bundle: GrantableRoleBundle
    institution_scope: InstitutionScope
    institution_id: str | None = Field(
        default=None,
        max_length=16,
        title="Grant target institution ID",
    )
    module_scope: ModuleScope
    sensitivity_scope: SensitivityScope
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_institution_target(self) -> ScopedGrantInput:
        if self.institution_scope is InstitutionScope.ORGANIZATION:
            if self.institution_id is not None:
                raise ValueError("organization-wide coverage must not include an institution id")
        elif not self.institution_id or not self.institution_id.strip():
            raise ValueError("institution coverage requires an exact institution id")
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("a grant reason is required")
        return self


class BindingCreateRequest(ScopedGrantInput):
    principal_user_id: UUID


class BindingRevokeRequest(ClosedModel):
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def strip_reason(self) -> BindingRevokeRequest:
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("a revocation reason is required")
        return self


class SodPolicyFindingRead(ClosedModel):
    code: str
    message: str


class SodDecisionRead(ClosedModel):
    outcome: Literal["allow", "warn", "block"]
    findings: list[SodPolicyFindingRead]


class BindingRead(ClosedModel):
    id: UUID
    principal_user_id: UUID
    principal_name: str
    role_bundle: str
    institution_scope: InstitutionScope
    institution_id: str | None = Field(title="Binding institution ID")
    institution_name: str | None
    module_scope: ModuleScope
    sensitivity_scope: SensitivityScope
    status: BindingStatus
    effective: bool
    authority_sentence: str
    effective_permissions: list[str]
    granted_by_type: GrantorType
    granted_by_id: str
    granted_by_name: str
    grant_reason: str
    granted_at: datetime
    valid_from: datetime
    valid_until: datetime | None
    revoked_at: datetime | None
    revoked_by_type: GrantorType | None
    revoked_by_id: str | None
    revoked_by_name: str | None
    revoked_reason: str | None


class BindingCreateResponse(ClosedModel):
    binding: BindingRead
    sod_decision: SodDecisionRead


class BindingListRead(ClosedModel):
    bindings: list[BindingRead]


class MemberRead(ClosedModel):
    user_id: UUID
    email: str
    display_name: str | None
    job_title: str | None
    lifecycle_status: Literal["active", "invited", "deactivated"]
    access_request_state: Literal["none", "approval_needed", "rejected"]
    last_activity_at: datetime | None
    authentication_method: Literal["password", "sso", "service"]
    active_grant_count: int
    grants: list[BindingRead]


class MemberListRead(ClosedModel):
    members: list[MemberRead]
