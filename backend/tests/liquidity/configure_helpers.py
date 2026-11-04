"""Explicit configuration fixtures, never inferred from scalar roles."""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.authorization import ModuleScope, RoleBundle
from app.db.base import utc_now
from app.db.session import get_sessionmaker
from app.models import AuthorizationBinding, User
from tests.support.factories.authorization import grant_institution_authority
from tests.support.helpers import ORG_1, USER_1, headers

CONFIGURATOR = UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc1")


def grant_configure(db: Session, bank_id: str, user_id: UUID = USER_1) -> None:
    grant_institution_authority(
        db,
        organization_id=ORG_1,
        bank_id=bank_id,
        user_id=user_id,
        bundle=RoleBundle.CONFIGURATOR,
        module=ModuleScope.LIQUIDITY,
    )


def configure_headers(bank_id: str) -> dict[str, str]:
    """Use a separate fixture officer so ordinary fixture readers stay read-only."""
    with get_sessionmaker()() as db:
        if db.get(User, CONFIGURATOR) is None:
            db.add(
                User(
                    id=CONFIGURATOR,
                    organization_id=ORG_1,
                    email="configurator@example.test",
                    display_name="Configuration fixture officer",
                )
            )
            db.flush()
        now = utc_now()
        existing = db.scalar(
            select(AuthorizationBinding.id)
            .where(
                AuthorizationBinding.organization_id == ORG_1,
                AuthorizationBinding.principal_user_id == CONFIGURATOR,
                AuthorizationBinding.principal_type == "human",
                AuthorizationBinding.role_bundle == RoleBundle.CONFIGURATOR.value,
                AuthorizationBinding.institution_scope == "institution",
                AuthorizationBinding.institution_id == bank_id,
                AuthorizationBinding.module_scope == ModuleScope.LIQUIDITY.value,
                AuthorizationBinding.sensitivity_scope == "restricted",
                AuthorizationBinding.data_scope_kind == "all",
                AuthorizationBinding.status == "active",
                AuthorizationBinding.revoked_at.is_(None),
                AuthorizationBinding.valid_from <= now,
                or_(
                    AuthorizationBinding.valid_until.is_(None),
                    AuthorizationBinding.valid_until > now,
                ),
            )
            .limit(1)
        )
        if existing is None:
            grant_configure(db, bank_id, CONFIGURATOR)
        db.commit()
    return headers(user_id=CONFIGURATOR, roles=("viewer",))
