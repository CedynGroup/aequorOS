"""Normalized, tenant-owned authorization role bindings."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.authorization import (
    BindingStatus,
    InstitutionScope,
    ModuleScope,
    PrincipalType,
    RoleBundle,
    SensitivityScope,
)
from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin, utc_now

GRANTOR_TYPES: tuple[str, ...] = ("system", "tenant_user", "operator")


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class AuthorizationBinding(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """One indivisible principal + bundle + exact-scope grant.

    Rows combine with OR.  Every scope column inside one row combines with AND;
    there are no independent role arrays or scope arrays to form a privilege
    widening Cartesian product.
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
            "(status = 'revoked' AND revoked_at IS NOT NULL) OR "
            "(status <> 'revoked' AND revoked_at IS NULL)",
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

    status: Mapped[str] = mapped_column(
        String(16), default=BindingStatus.ACTIVE, nullable=False
    )
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
