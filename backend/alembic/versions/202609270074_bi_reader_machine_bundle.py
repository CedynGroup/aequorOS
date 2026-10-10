"""The ``bi_reader`` machine bundle, for the Power BI Stage B feed.

Revision ID: 202609270074
Revises: 202609270073

``docs/bi.md`` §Phase 4 asks that the feed be "authenticated by a new
``bi_reader`` machine bundle issued through the existing integration-key flow".
This revision is the vocabulary half: two CHECK constraints on
``authorization_bindings`` widen to admit the bundle, and to admit it ONLY for
a machine principal.

Why a second machine bundle rather than reusing ``integration_writer``
---------------------------------------------------------------------
``integration_writer`` carries ``Permission.INGEST`` and nothing else — it is
the credential a bank's own systems PUSH with. A report server pulls. Giving
the feed's credential the writer bundle would hand every Power BI gateway the
authority to write canonical facts, which is the exact inversion of what the
integration is for; giving the writer bundle a read permission would widen
every existing push key in the estate. So the bundles stay disjoint by
construction: ``bi_reader`` reads, ``integration_writer`` writes, and neither
implies the other.

``ck_authorization_bindings_principal_bundle`` generalises rather than
lengthens
---------------------------------------------------------------------------
The old constraint named one bundle twice::

    (machine AND role_bundle =  'integration_writer')
 OR (human   AND role_bundle <> 'integration_writer')

which reads as "the machine bundle" — a shape that cannot hold a second one.
It becomes a set on both sides, so the invariant it was really expressing
survives intact and stays complete: a machine principal holds a MACHINE bundle
and a human holds a HUMAN one, with no row able to straddle. That second half
is the load-bearing one. A human identity holding ``bi_reader`` would be a
person authenticating with a long-lived bearer key against a route that logs
every pull as a machine pull, so the person's reads would be attributed to an
integration and their leaving the bank would not revoke them.

Nothing is backfilled
---------------------
No existing row can become a ``bi_reader``: the bundle is minted only by
issuing a feed key, which creates its own service identity in the same
transaction (``app/identity/service/integration_keys.py``). A tenant that has issued
no feed key has no ``bi_reader`` binding and therefore no feed access, which
is the intended starting state.
"""

from __future__ import annotations

from alembic import op

revision = "202609270074"
down_revision = "202609270073"
branch_labels = None
depends_on = None

_TABLE = "authorization_bindings"

#: Every bundle name the column may hold after this revision. Kept as a literal
#: list rather than read from ``app.core.authorization`` so that replaying this
#: migration reproduces the 2026-09-27 vocabulary even after the enum grows
#: again — a migration must describe the schema it produced, not today's code.
_BUNDLES_AFTER = (
    "member",
    "viewer",
    "auditor",
    "analyst",
    "approver",
    "validator",
    "account_admin",
    "org_owner",
    "integration_writer",
    "bi_reader",
)
_BUNDLES_BEFORE = tuple(name for name in _BUNDLES_AFTER if name != "bi_reader")

#: Bundles a MACHINE principal may hold; equivalently, bundles a human may not.
_MACHINE_AFTER = ("integration_writer", "bi_reader")
_MACHINE_BEFORE = ("integration_writer",)


def _values(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{name}'" for name in names)


def _apply(bundles: tuple[str, ...], machine: tuple[str, ...]) -> None:
    op.drop_constraint("ck_authorization_bindings_role_bundle", _TABLE, type_="check")
    op.drop_constraint("ck_authorization_bindings_principal_bundle", _TABLE, type_="check")
    op.create_check_constraint(
        "ck_authorization_bindings_role_bundle",
        _TABLE,
        f"role_bundle IN ({_values(bundles)})",
    )
    op.create_check_constraint(
        "ck_authorization_bindings_principal_bundle",
        _TABLE,
        f"(principal_type = 'machine' AND role_bundle IN ({_values(machine)})) OR "
        f"(principal_type = 'human' AND role_bundle NOT IN ({_values(machine)}))",
    )


def upgrade() -> None:
    _apply(_BUNDLES_AFTER, _MACHINE_AFTER)


def downgrade() -> None:
    """Narrow the vocabulary again.

    This FAILS by design while any ``bi_reader`` binding exists: the narrowed
    CHECK is validated against the existing rows, so a deployment that has
    issued feed keys cannot silently lose the constraint that described them.
    Revoke the feed keys first — which is the correct order anyway, because
    revocation is what deactivates their service identities.
    """

    _apply(_BUNDLES_BEFORE, _MACHINE_BEFORE)
