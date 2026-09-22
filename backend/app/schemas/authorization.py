"""Tenant grant-administration contracts.

One create payload represents one indivisible binding.  The four authority
dimensions are scalar enums by construction; there is no array-shaped request
that could fan out into a Cartesian product.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.authorization import (
    BindingStatus,
    GrantorType,
    GrantReasonCategory,
    InstitutionScope,
    Module,
    ModuleScope,
    Permission,
    Sensitivity,
    SensitivityScope,
)

GrantableRoleBundle = Literal[
    "viewer",
    "auditor",
    "analyst",
    "approver",
    # The officer who transmits a return to the regulator. Grantable because
    # transmission authority exists only as an explicit binding: nothing
    # backfilled it and no scalar role produces it.
    "validator",
    "account_admin",
]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EffectiveCapabilityRead(ClosedModel):
    module: Module
    sensitivity: Sensitivity
    permission: Permission
    requires_contextual_authorization: bool


class InstitutionCapabilitiesRead(ClosedModel):
    institution_id: str
    capabilities: list[EffectiveCapabilityRead]


class EffectiveAuthorityRead(ClosedModel):
    authv: int
    organization_capabilities: list[EffectiveCapabilityRead]
    institution_capabilities: list[InstitutionCapabilitiesRead]


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
    reason_category: GrantReasonCategory
    reason_detail: str = Field(default="", max_length=2000)
    reference: str | None = Field(default=None, max_length=255)
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def validate_institution_target(self) -> ScopedGrantInput:
        if self.institution_scope is InstitutionScope.ORGANIZATION:
            if self.institution_id is not None:
                raise ValueError("organization-wide coverage must not include an institution id")
        elif not self.institution_id or not self.institution_id.strip():
            raise ValueError("institution coverage requires an exact institution id")
        _validate_grant_reason(self)
        return self


TEMPORARY_REASON_CATEGORIES = frozenset(
    {GrantReasonCategory.TEMPORARY_COVER, GrantReasonCategory.INCIDENT_BREAK_GLASS}
)


class _GrantReasonFields(Protocol):
    reason_category: GrantReasonCategory
    reason_detail: str
    reference: str | None
    valid_until: datetime | None


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _validate_grant_reason(payload: _GrantReasonFields) -> None:
    payload.reason_detail = payload.reason_detail.strip()
    payload.reference = payload.reference.strip() if payload.reference else None
    payload.valid_until = _aware_utc(payload.valid_until)
    if payload.reason_category is GrantReasonCategory.OTHER and not payload.reason_detail:
        raise ValueError("reason detail is required when the category is other")
    if payload.reason_category in TEMPORARY_REASON_CATEGORIES and payload.valid_until is None:
        raise ValueError("temporary cover and break-glass grants require an expiry")


class BindingCreateRequest(ScopedGrantInput):
    principal_user_id: UUID
    expected_authority_sentence: str = Field(min_length=1, max_length=2000)


class BindingPreviewRequest(ScopedGrantInput):
    principal_user_id: UUID


class BindingPreviewRead(ClosedModel):
    authority_sentence: str


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
    grant_reason_category: GrantReasonCategory
    grant_reason: str
    grant_reference: str | None
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


class InstitutionDirectoryEntryRead(ClosedModel):
    id: str
    name: str
    short_name: str | None
    institution_class: str | None


class InstitutionDirectoryRead(ClosedModel):
    """Every institution in the organization, for scoping grants.

    Account-plane data: it does not depend on the caller's own operational
    coverage, unlike ``/banks``.
    """

    institutions: list[InstitutionDirectoryEntryRead]


class AccessRequestCreate(ClosedModel):
    """One exact route permission, targeted at an institution or — for Account
    Administration routes, which the evaluator resolves organization-wide — at
    the organization itself (no institution id)."""

    route: str = Field(min_length=1, max_length=255, pattern=r"^/")
    institution_id: str | None = Field(default=None, min_length=1, max_length=16)
    module_scope: ModuleScope
    sensitivity_scope: Sensitivity
    permission: Permission
    reason_category: GrantReasonCategory
    reason_detail: str = Field(default="", max_length=2000)
    reference: str | None = Field(default=None, max_length=255)
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def validate_reason(self) -> AccessRequestCreate:
        if self.module_scope is ModuleScope.ACCOUNT:
            if self.institution_id is not None:
                raise ValueError("organization-wide access requests must not name an institution")
        elif not self.institution_id or not self.institution_id.strip():
            raise ValueError("institution access requests require an exact institution id")
        _validate_grant_reason(self)
        return self


class AccessRequestRead(ClosedModel):
    id: UUID
    requester_user_id: UUID
    requester_name: str
    requester_email: str
    route: str
    page_title: str
    institution_scope: InstitutionScope
    institution_id: str | None
    institution_name: str | None
    module_scope: ModuleScope
    sensitivity_scope: Sensitivity
    permission: Permission
    reason_category: GrantReasonCategory
    reason_detail: str
    reference: str | None
    valid_until: datetime | None
    status: Literal["pending", "approved", "rejected"]
    requested_at: datetime

    @field_validator("valid_until")
    @classmethod
    def _valid_until_utc(cls, value: datetime | None) -> datetime | None:
        return _aware_utc(value)


class AccessRequestListRead(ClosedModel):
    requests: list[AccessRequestRead]


class AccessRequestApprove(ScopedGrantInput):
    role_bundle: GrantableRoleBundle
    expected_authority_sentence: str = Field(min_length=1, max_length=2000)


class AccessRequestReject(ClosedModel):
    """Why an Org Owner declined a request, in the grant vocabulary (no expiry)."""

    reason_category: GrantReasonCategory
    reason_detail: str = Field(default="", max_length=2000)
    reference: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_reason(self) -> AccessRequestReject:
        self.reason_detail = self.reason_detail.strip()
        self.reference = self.reference.strip() if self.reference else None
        if self.reason_category is GrantReasonCategory.OTHER and not self.reason_detail:
            raise ValueError("reason detail is required when the category is other")
        return self
