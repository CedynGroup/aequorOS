"""Database models for tenant-scoped authorization bindings."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.authorization import (
    BindingStatus,
    InstitutionScope,
    ModuleScope,
    OwnerAssignmentBasis,
    OwnerAssignmentStatus,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin, utc_now

GRANTOR_TYPES: tuple[str, ...] = ("system", "tenant_user", "operator")


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class AuthorizationBinding(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """One user, one role bundle, and one exact scope stored as a single row.

    Multiple bindings for the same user combine with OR: if any one matches,
    the permission is granted. But every scope field within a single binding
    must match. There are no separate role or scope arrays that could widen
    access in unexpected combinations.
    """

    __tablename__ = "authorization_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["principal_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            ondelete="CASCADE",
            name="fk_authorization_bindings_principal_tenant",
        ),
        ForeignKeyConstraint(
            ["institution_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
            ondelete="RESTRICT",
            name="fk_authorization_bindings_institution_tenant",
        ),
        CheckConstraint(
            f"principal_type IN ({_values(tuple(PrincipalType))})",
            name="ck_authorization_bindings_principal_type",
        ),
        CheckConstraint(
            f"role_bundle IN ({_values(tuple(RoleBundle))})",
            name="ck_authorization_bindings_role_bundle",
        ),
        CheckConstraint(
            "(principal_type = 'machine' AND role_bundle = 'integration_writer') OR "
            "(principal_type = 'human' AND role_bundle <> 'integration_writer')",
            name="ck_authorization_bindings_principal_bundle",
        ),
        CheckConstraint(
            f"institution_scope IN ({_values(tuple(InstitutionScope))})",
            name="ck_authorization_bindings_institution_scope",
        ),
        CheckConstraint(
            "(institution_scope = 'organization' AND institution_id IS NULL) OR "
            "(institution_scope = 'institution' AND institution_id IS NOT NULL)",
            name="ck_authorization_bindings_institution_target",
        ),
        CheckConstraint(
            f"module_scope IN ({_values(tuple(ModuleScope))})",
            name="ck_authorization_bindings_module_scope",
        ),
        CheckConstraint(
            f"sensitivity_scope IN ({_values(tuple(SensitivityScope))})",
            name="ck_authorization_bindings_sensitivity_scope",
        ),
        CheckConstraint(
            f"status IN ({_values(tuple(BindingStatus))})",
            name="ck_authorization_bindings_status",
        ),
        CheckConstraint(
            f"granted_by_type IN ({_values(GRANTOR_TYPES)})",
            name="ck_authorization_bindings_grantor_type",
        ),
        CheckConstraint(
            "length(trim(granted_by_id)) > 0",
            name="ck_authorization_bindings_grantor",
        ),
        CheckConstraint(
            "length(trim(grant_reason)) > 0",
            name="ck_authorization_bindings_grant_reason",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from",
            name="ck_authorization_bindings_validity_window",
        ),
        CheckConstraint(
            "(status = 'revoked' AND revoked_at IS NOT NULL AND "
            "revoked_reason IS NOT NULL AND length(trim(revoked_reason)) > 0) OR "
            "(status <> 'revoked' AND revoked_at IS NULL AND revoked_reason IS NULL)",
            name="ck_authorization_bindings_revocation_state",
        ),
        Index(
            "ix_authorization_bindings_principal",
            "organization_id",
            "principal_user_id",
            "status",
        ),
        Index(
            "ix_authorization_bindings_institution",
            "organization_id",
            "institution_id",
        ),
        Index(
            "uq_authorization_bindings_active_org_owner",
            "organization_id",
            unique=True,
            postgresql_where=sql_text("role_bundle = 'org_owner' AND status = 'active'"),
            sqlite_where=sql_text("role_bundle = 'org_owner' AND status = 'active'"),
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    principal_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    principal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    role_bundle: Mapped[str] = mapped_column(String(32), nullable=False)

    # Broad coverage is named, never inferred from a nullable column.  NULL is
    # legal only when institution_scope explicitly says "organization".
    institution_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    institution_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    module_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    sensitivity_scope: Mapped[str] = mapped_column(String(32), nullable=False)

    granted_by_type: Mapped[str] = mapped_column(String(16), nullable=False)
    granted_by_id: Mapped[str] = mapped_column(String(255), nullable=False)
    grant_reason: Mapped[str] = mapped_column(Text, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    status: Mapped[str] = mapped_column(String(16), default=BindingStatus.ACTIVE, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrganizationOwnerAssignment(TimestampMixin, Base):
    """Records whether an organization has an owner and why.

    The owner binding is the source of truth for ownership. This row is the
    control record that makes every unresolved organization findable without
    reading deploy logs: it says why designation is still needed and lists
    the eligible candidates that staff must choose between.
    """

    __tablename__ = "organization_owner_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            ondelete="RESTRICT",
            name="fk_organization_owner_assignments_owner_tenant",
        ),
        CheckConstraint(
            f"status IN ({_values(tuple(OwnerAssignmentStatus))})",
            name="ck_organization_owner_assignments_status",
        ),
        CheckConstraint(
            f"basis IN ({_values(tuple(OwnerAssignmentBasis))})",
            name="ck_organization_owner_assignments_basis",
        ),
        CheckConstraint(
            "eligible_candidate_count >= 0",
            name="ck_organization_owner_assignments_candidate_count",
        ),
        CheckConstraint(
            "(status = 'assigned' AND owner_user_id IS NOT NULL AND "
            "owner_binding_id IS NOT NULL) OR "
            "(status = 'designation_required' AND owner_user_id IS NULL AND "
            "owner_binding_id IS NULL)",
            name="ck_organization_owner_assignments_resolution",
        ),
        CheckConstraint(
            "(basis = 'exactly_one_eligible_active_human_administrator' AND "
            "status = 'assigned' AND eligible_candidate_count = 1) OR "
            "(basis = 'zero_eligible_active_human_administrators' AND "
            "status = 'designation_required' AND eligible_candidate_count = 0) OR "
            "(basis = 'multiple_eligible_active_human_administrators' AND "
            "status = 'designation_required' AND eligible_candidate_count > 1) OR "
            "(basis = 'explicit_designation' AND status = 'assigned')",
            name="ck_organization_owner_assignments_basis_count",
        ),
        Index(
            "ix_organization_owner_assignments_status",
            "status",
            "organization_id",
        ),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    basis: Mapped[str] = mapped_column(String(64), nullable=False)
    eligible_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    eligible_candidates: Mapped[list[dict[str, str | None]]] = mapped_column(
        JSON, default=list, server_default=sql_text("'[]'"), nullable=False
    )
    owner_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    owner_binding_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("authorization_bindings.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
