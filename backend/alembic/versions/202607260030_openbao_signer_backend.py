"""signer_keys.backend admits 'openbao'

Revision ID: 202607260030
Revises: 202607250029

``ck_signer_keys_backend`` enumerated the three custody backends that existed
when the attestation foundation landed. OpenBao Transit is the fourth, and the
first one a production deployment can actually sign with, so the constraint has
to admit it — otherwise enrolling the first officer's key fails on an
IntegrityError deep inside the issuing transaction.

Nothing else moves: no existing row can carry the new value, and the column is
already ``String(16)`` (``'openbao'`` is seven).

The rewrite goes through ``batch_alter_table`` because SQLite cannot drop a
CHECK constraint in place, and the hermetic test database is SQLite.
"""

from __future__ import annotations

from alembic import op

revision = "202607260030"
down_revision = "202607250029"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_signer_keys_backend"
_BEFORE = "backend IN ('pkcs11', 'kms', 'software')"
_AFTER = "backend IN ('pkcs11', 'kms', 'software', 'openbao')"


def upgrade() -> None:
    with op.batch_alter_table("signer_keys") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, _AFTER)


def downgrade() -> None:
    # Deliberately not deleting rows: a downgrade that erased an officer's key
    # row would orphan every signature made under it. If openbao keys exist, the
    # constraint creation fails and the operator has to decide what to do with
    # them — which is the right person to be making that call.
    with op.batch_alter_table("signer_keys") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, _BEFORE)
