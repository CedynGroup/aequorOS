"""Permit the ``credit`` and ``institution`` module scopes in the bindings CHECK constraint.

``Module.CREDIT`` and ``Module.INSTITUTION`` extend the v1 authorization
vocabulary so that Credit analytics and institution master data can later be
cut over to scoped bindings (enforcement matrix PRs 20 and 21). This revision
is vocabulary only: no route consults either module yet, no bundle changes,
and no binding is created — an Org Owner may grant one, and it confers nothing
until the consuming surface is enforced.

The CHECK constraint that decides which values the column will ACCEPT was last
written by ``202608250044`` from a literal list, while the model derives it
from ``tuple(ModuleScope)``. The hermetic suite therefore always agrees with
the enum and only a MIGRATED database can refuse the new values, exactly as
``202609200065`` found for the ``validator`` bundle. Any future module needs
this migration too.

No data moves: the constraint is widened, never narrowed, so nothing that is
currently stored can fail it.
"""

from __future__ import annotations

from alembic import op
from app.core.authorization import ModuleScope
from app.db.session import force_rls_suspended

revision = "202609200066"
down_revision = "202609200065"
branch_labels = None
depends_on = None

_BINDINGS = "authorization_bindings"
_CONSTRAINT = "ck_authorization_bindings_module_scope"

#: The module scopes this constraint accepted before ``credit`` and
#: ``institution`` existed. Written out rather than derived, so the downgrade
#: restores the historical shape even after the enum grows again.
_PREVIOUS = (
    "all",
    "liq",
    "cap",
    "irrbb",
    "fx",
    "ftp",
    "fcst",
    "beh",
    "data",
    "reg",
    "risk",
    "markets",
    "account",
    "audit",
)
_ADDED = (ModuleScope.CREDIT.value, ModuleScope.INSTITUTION.value)


def _check(values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"module_scope IN ({joined})"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _BINDINGS, type_="check")
    op.create_check_constraint(
        _CONSTRAINT, _BINDINGS, _check(tuple(scope.value for scope in ModuleScope))
    )


def downgrade() -> None:
    # A Credit or Institution binding cannot satisfy the older constraint, so
    # the rows have to go before the constraint is narrowed — revoking them is
    # not enough, because a revoked row is still a row and the CHECK applies
    # to all of them. A downgrade past this revision therefore takes that
    # authority away from whoever held it; it is not a routine operation.
    #
    # `authorization_bindings` is FORCE-RLS and alembic runs as a
    # tenant-scoped role, so this DELETE would otherwise match zero rows across
    # every tenant and report success — the constraint would then fail to
    # apply, or worse apply while rows survived. `force_rls_suspended` lifts
    # FORCE for the owner inside this transaction only (audit P0-18), and is
    # loud when the role cannot lift it.
    removed = ", ".join(f"'{value}'" for value in _ADDED)
    with force_rls_suspended(op.get_bind(), _BINDINGS):
        op.execute(f"DELETE FROM {_BINDINGS} WHERE module_scope IN ({removed})")
    op.drop_constraint(_CONSTRAINT, _BINDINGS, type_="check")
    op.create_check_constraint(_CONSTRAINT, _BINDINGS, _check(_PREVIOUS))
