"""Persist named stress-signoff routing and one-shot attestation evidence.

Existing rows remain valid with NULL ceremony fields. New pending and attested
rows are populated by the stress signing service; no authority or financial
value is created or changed by this migration.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202611070113"
down_revision = "202611070112"
branch_labels = None
depends_on = None

_TABLE = "enterprise_stress_signoffs"
_RECIPIENT = "attestation_recipient_user_id"
_DIGEST = "signing_digest"
_AUTHORIZATION = "signing_authorization_id"
_FK = "fk_enterprise_stress_signoffs_signing_authorization"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_RECIPIENT, sa.Uuid(), nullable=True))
    op.add_column(_TABLE, sa.Column(_DIGEST, sa.String(length=64), nullable=True))
    op.add_column(_TABLE, sa.Column(_AUTHORIZATION, sa.Uuid(), nullable=True))
    op.create_foreign_key(
        _FK,
        _TABLE,
        "signing_authorizations",
        [_AUTHORIZATION],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(_FK, _TABLE, type_="foreignkey")
    op.drop_column(_TABLE, _AUTHORIZATION)
    op.drop_column(_TABLE, _DIGEST)
    op.drop_column(_TABLE, _RECIPIENT)
