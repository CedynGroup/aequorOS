"""ICAAP rehearsal cycles run the full lifecycle (lead ruling D-068).

D-029 puts a REHEARSAL cycle through freeze -> package -> validate -> sign ->
attach -> submit, so a bank can dry-run the riskiest part of the regime while
the BoG text is still pending (D-006). ``202609190055`` then added two CHECKs
that made exactly that impossible:

    ck_icaap_cycles_rehearsal_no_package     cycle_kind <> 'rehearsal' OR package_id IS NULL
    ck_icaap_cycles_rehearsal_never_sealed   cycle_kind <> 'rehearsal' OR status IN (draft, ...)

Both designs want the same thing: a rehearsal must never be mistaken for, or
become, a filing. The CHECKs got there by forbidding the SAFE act; D-068 rules
that the platform forbids the DANGEROUS one instead, at the point it would
happen. This revision moves the guarantee rather than removing it.

What moves to the package row, where the danger actually is:

* ``regulatory_packages.is_rehearsal`` — a rehearsal package SAYS SO in the
  database, not only in a renderer that reads the snapshot. Every downstream
  surface (calendar, obligations, disclosure, submission) can therefore ask one
  column instead of re-deriving the answer from nested JSON, and a query that
  forgets to exclude rehearsals is a visible bug rather than an invisible one.
* ``ck_regulatory_packages_rehearsal_is_icaap`` — only the ICAAP filing family
  has rehearsals. A BSD return marked as a rehearsal would be a filing nobody
  filed.
* ``ck_regulatory_packages_rehearsal_never_acknowledged`` — a rehearsal may
  reach ``submitted`` (recorded on a non-transmitting channel, which is the
  whole point of the dry run) but NEVER ``acknowledged`` or ``rejected``: those
  are the regulator's own words about a document the regulator never received.
  This is the DB half of "a rehearsal is never mistaken for a filing".
* ``ck_regulatory_packages_rehearsal_supersedes_nothing`` — a rehearsal cannot
  retire a real version. This is the expressible half of D-029's "a rehearsal
  can never supersede or satisfy a real obligation".

What a CHECK CANNOT express, stated here rather than left implicit — both are
cross-table or cross-row, and both are pinned by tests in
``tests/services/test_package_plane.py``:

1. **A real package must not supersede a rehearsal.** ``supersedes_id`` points
   at another row, and a row-level CHECK cannot read it. Enforced in
   ``generation._supersede_prior``, which keeps the rehearsal and the real
   chains apart, and pinned.
2. **A rehearsal must not reach a transmitting channel.** The channel lives on
   ``regulatory_submission_events``, a different table. Enforced in
   ``workflow._ensure_channel_submittable``, and pinned. (The ICAAP family is
   already ``allowed_channels=("manual",)``, which is non-transmitting by
   construction, so this is defence in depth rather than the only line.)

Downgrade restores the two original CHECKs and drops the column. It will FAIL
if any rehearsal cycle has been sealed by then, and that is correct: silently
deleting the evidence of a dry run to make an old constraint fit is not an
acceptable downgrade (same rule as ``202609010050``).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202609190062"
down_revision = "202609190061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. The two CHECKs that forbade the safe act.
    op.drop_constraint(
        "ck_icaap_cycles_rehearsal_no_package", "icaap_cycles", type_="check"
    )
    op.drop_constraint(
        "ck_icaap_cycles_rehearsal_never_sealed", "icaap_cycles", type_="check"
    )

    # 2. The package says what it is.
    op.add_column(
        "regulatory_packages",
        sa.Column(
            "is_rehearsal",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # 3. The invariants a row-level CHECK can carry.
    op.create_check_constraint(
        "ck_regulatory_packages_rehearsal_is_icaap",
        "regulatory_packages",
        "NOT is_rehearsal OR return_family = 'icaap'",
    )
    op.create_check_constraint(
        "ck_regulatory_packages_rehearsal_never_acknowledged",
        "regulatory_packages",
        "NOT is_rehearsal OR status NOT IN ('acknowledged', 'rejected', 'declined')",
    )
    op.create_check_constraint(
        "ck_regulatory_packages_rehearsal_supersedes_nothing",
        "regulatory_packages",
        "NOT is_rehearsal OR supersedes_id IS NULL",
    )

    # 4. The one-current-version index has to know about the third chain, or a
    #    rehearsal and the real filing for the SAME (return, date, basis) cannot
    #    coexist at all — which would have made D-029 impossible for a second
    #    time, one layer down, and would have surfaced as a duplicate-key error
    #    the first time a bank filed a year it had rehearsed. Found by running
    #    the migration suite, not by reading the design.
    op.drop_index("uq_regulatory_packages_current", table_name="regulatory_packages")
    op.create_index(
        "uq_regulatory_packages_current",
        "regulatory_packages",
        [
            "organization_id",
            "bank_id",
            "return_code",
            "reporting_date",
            "basis",
            "is_rehearsal",
        ],
        unique=True,
        postgresql_where=sa.text("status != 'superseded'"),
        sqlite_where=sa.text("status != 'superseded'"),
    )


def downgrade() -> None:
    op.drop_index("uq_regulatory_packages_current", table_name="regulatory_packages")
    op.create_index(
        "uq_regulatory_packages_current",
        "regulatory_packages",
        ["organization_id", "bank_id", "return_code", "reporting_date", "basis"],
        unique=True,
        postgresql_where=sa.text("status != 'superseded'"),
        sqlite_where=sa.text("status != 'superseded'"),
    )
    op.drop_constraint(
        "ck_regulatory_packages_rehearsal_supersedes_nothing",
        "regulatory_packages",
        type_="check",
    )
    op.drop_constraint(
        "ck_regulatory_packages_rehearsal_never_acknowledged",
        "regulatory_packages",
        type_="check",
    )
    op.drop_constraint(
        "ck_regulatory_packages_rehearsal_is_icaap",
        "regulatory_packages",
        type_="check",
    )
    op.drop_column("regulatory_packages", "is_rehearsal")
    # Deliberately NOT preceded by a cleanup: if a rehearsal cycle has been
    # sealed, these re-creations fail and the downgrade stops. Erasing a dry
    # run's evidence to make an old constraint fit is not a downgrade.
    op.create_check_constraint(
        "ck_icaap_cycles_rehearsal_never_sealed",
        "icaap_cycles",
        "cycle_kind <> 'rehearsal' OR status IN "
        "('draft', 'in_review', 'returned', 'superseded', 'archived')",
    )
    op.create_check_constraint(
        "ck_icaap_cycles_rehearsal_no_package",
        "icaap_cycles",
        "cycle_kind <> 'rehearsal' OR package_id IS NULL",
    )
