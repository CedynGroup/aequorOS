"""Allow governed post-freeze returns to reopen the freeze stage itself.

In-review returns still require an earlier stage. Only a return referencing a
sealed package may target its own freeze stage. Downgrade refuses if that new
history exists; it must never erase decisions to make the old CHECK fit.
"""

from alembic import op

revision = "202610110092"
down_revision = "202610100091"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_icaap_stage_decisions_return"
_PREFIX = "decision <> 'returned' OR (return_to_seq IS NOT NULL AND return_to_seq >= 1 AND "
_SUFFIX = " AND comment IS NOT NULL)"


def upgrade() -> None:
    with op.batch_alter_table("icaap_stage_decisions") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(
            _CONSTRAINT,
            _PREFIX + "(return_to_seq < stage_seq OR "
            "(return_to_seq = stage_seq AND package_id IS NOT NULL))" + _SUFFIX,
        )


def downgrade() -> None:
    with op.batch_alter_table("icaap_stage_decisions") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, _PREFIX + "return_to_seq < stage_seq" + _SUFFIX)
