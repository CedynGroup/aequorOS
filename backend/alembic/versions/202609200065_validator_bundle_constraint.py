"""Permit the ``validator`` role bundle in the bindings CHECK constraint.

``RoleBundle.VALIDATOR`` shipped with the filing-submit split (2026-09-20) and
deliberately without a data migration: backfilling Validator onto the existing
approvers would have re-encoded the very defect the bundle exists to close.
What was missed is smaller and purely structural — the CHECK constraint that
decides which values the column will ACCEPT was last written by
``202609160053`` and does not list ``validator``. Granting one therefore passed
the evaluator, passed the separation-of-duties decision, and then failed in the
database with a constraint violation surfaced as a 500.

It did not fail in the test suite because the hermetic stack builds its schema
with ``Base.metadata.create_all``, and the model derives this constraint from
``tuple(RoleBundle)`` — so a fresh database always had the new value while every
migrated database did not. Any future bundle needs this migration too.

No data moves: the constraint is widened, never narrowed, so nothing that is
currently stored can fail it.
"""

from __future__ import annotations

from alembic import op
from app.core.authorization import RoleBundle
from app.db.session import force_rls_suspended

revision = "202609200065"
down_revision = "202609200064"
branch_labels = None
depends_on = None

_BINDINGS = "authorization_bindings"
_CONSTRAINT = "ck_authorization_bindings_role_bundle"

#: The bundles this constraint accepted before ``validator`` existed. Written
#: out rather than derived, so the downgrade restores the historical shape even
#: after the enum grows again.
_PREVIOUS = (
    "member",
    "viewer",
    "auditor",
    "analyst",
    "approver",
    "account_admin",
    "org_owner",
    "integration_writer",
)


def _check(values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"role_bundle IN ({joined})"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _BINDINGS, type_="check")
    op.create_check_constraint(
        _CONSTRAINT, _BINDINGS, _check(tuple(bundle.value for bundle in RoleBundle))
    )


def downgrade() -> None:
    # A Validator binding cannot satisfy the older constraint, so the rows have
    # to go before the constraint is narrowed — revoking them is not enough,
    # because a revoked row is still a row and the CHECK applies to all of
    # them. A downgrade past this revision therefore takes filing authority
    # away from whoever held it; it is not a routine operation.
    #
    # `authorization_bindings` is FORCE-RLS and alembic runs as a
    # tenant-scoped role, so this DELETE would otherwise match zero rows across
    # every tenant and report success — the constraint would then fail to
    # apply, or worse apply while rows survived. `force_rls_suspended` lifts
    # FORCE for the owner inside this transaction only (audit P0-18), and is
    # loud when the role cannot lift it.
    with force_rls_suspended(op.get_bind(), _BINDINGS):
        op.execute(f"DELETE FROM {_BINDINGS} WHERE role_bundle = '{RoleBundle.VALIDATOR.value}'")
    op.drop_constraint(_CONSTRAINT, _BINDINGS, type_="check")
    op.create_check_constraint(_CONSTRAINT, _BINDINGS, _check(_PREVIOUS))
