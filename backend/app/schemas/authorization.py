"""Tenant grant-administration contracts.

One create payload represents one indivisible binding.  The four authority
dimensions are scalar enums by construction; there is no array-shaped request
that could fan out into a Cartesian product.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.authorization import (
    DATA_SCOPE_VALUE_MAX_LENGTH,
    BindingStatus,
    DataScope,
    GrantorType,
    InstitutionScope,
    Module,
    ModuleScope,
    Permission,
    Sensitivity,
    SensitivityScope,
    normalise_data_scope_values,
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
# ``bi_reader`` is absent on purpose and must stay absent: it is a MACHINE
# bundle, minted only by issuing a feed key, and a human holding it would be a
# person authenticating with a long-lived bearer credential against routes that
# log every call as an integration's.

#: Production copy for the stored data-scope vocabulary. One home, so the
#: authority sentence, the Members grant list and the composer's control cannot
#: disagree, and no surface ever prints ``branch``.
DATA_SCOPE_LABELS: Mapping[DataScope, str] = MappingProxyType(
    {
        DataScope.ALL: "Whole institution",
        DataScope.BRANCH: "Selected branches",
        DataScope.REGION: "Selected regions",
    }
)


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DataScopeRead(ClosedModel):
    """The slice of an institution's book one capability reads.

    ``kind`` widens beyond the stored vocabulary: ``mixed`` is the union of a
    branch grant and a region grant, and ``none`` means nothing authorized the
    read. Neither is storable on a binding row.
    """

    kind: Literal["all", "branch", "region", "mixed", "none"]
    branches: list[str]
    regions: list[str]


class EffectiveCapabilityRead(ClosedModel):
    module: Module
    sensitivity: Sensitivity
    permission: Permission
    requires_contextual_authorization: bool
    #: The declared slice this exact (institution, module, sensitivity,
    #: permission) capability reads — reduced from the bindings that matched
    #: THIS resource, never one answer standing for a whole institution.
    data_scope: DataScopeRead


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
    data_scope_kind: DataScope = DataScope.ALL
    data_scope_values: list[str] = Field(
        default_factory=list,
        max_length=500,
        title="Selected branch codes or region names",
    )
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

    @model_validator(mode="after")
    def validate_data_scope(self) -> ScopedGrantInput:
        """Refuse an unusable scope here, with a sentence, not at the CHECK.

        Values are normalised in place, so two spellings of one grant produce
        one stored row, one authority sentence and one duplicate refusal.
        """

        values = normalise_data_scope_values(self.data_scope_values)
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
            # A branch code belongs to ONE institution's core banking system, so
            # a narrow slice of "every institution in the organization" names a
            # vocabulary nobody can point at: the same code may exist in two
            # banks and mean two different books under one sentence. Refused at
            # the boundary rather than by the database, which permits the shape
            # so a future organization-wide region grant needs no migration.
            if self.institution_scope is InstitutionScope.ORGANIZATION:
                raise ValueError(
                    "Selected branches or regions belong to one institution, "
                    "so choose that institution instead of organization-wide coverage."
                )
        self.data_scope_values = list(values)
        return self


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
    data_scope_kind: DataScope
    data_scope_values: list[str]
    #: Ready-to-display copy for the grant list ("Whole institution",
    #: "Selected branches: ACC-001, TEM-002"), so no surface has to translate
    #: the stored vocabulary itself.
    data_scope_label: str
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


class InstitutionDirectoryEntryRead(ClosedModel):
    id: str
    name: str
    short_name: str | None


class InstitutionDirectoryRead(ClosedModel):
    """Every institution in the organization, for scoping grants.

    Account-plane data: it does not depend on the caller's own operational
    coverage, unlike ``/banks``.
    """

    institutions: list[InstitutionDirectoryEntryRead]


class BranchDirectoryEntryRead(ClosedModel):
    code: str
    name: str
    #: The bank's DECLARED region, absent when it has not declared one. Never
    #: inferred and never parsed out of an address — the register's optional
    #: ``region`` field is the only place a region can come from.
    region: str | None


class DataScopeOptionRead(ClosedModel):
    """One choice in the composer's scope control, with its production copy."""

    kind: DataScope
    label: str
    requires_values: bool


class BranchDirectoryRead(ClosedModel):
    """One institution's declared branch register, for scoping a grant.

    Both vocabularies are returned because the composer needs both and a region
    exists only as a declaration on this register. An institution that has
    ingested no register yet returns empty lists — never a fabricated branch.
    """

    institution_id: str
    branches: list[BranchDirectoryEntryRead]
    regions: list[str]
    scope_options: list[DataScopeOptionRead]
