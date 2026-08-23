"""Database-enforced immutability for the governance tables this programme added.

Audit 2026-08-22 D-17. ``202607250027`` gave the attestation evidence tables a
DB-level append-only guard and ``202608220031`` extended it to the regulatory
approval/submission events. The governance tables built since — the
regulatory-parameter control plane (``202608200025``), the reconciliation escape
valve (``202608220032``) and the canonical-withdrawal ledger with its
system-of-record register (``202608230035``) — got none, so on the primary today
a granted breach ceiling can be widened in place and an approved regulatory
number can be edited after the runs that consumed it were sealed, leaving only
the original audit event.

Measured read-only on the primary on 2026-08-22, ``pg_trigger`` carried
append-only triggers on exactly seven tables — ``audit_events``,
``operator_audit_log``, ``attestation_signatures``, ``signer_identities``,
``regulatory_artifact_versions``, ``regulatory_package_approvals``,
``regulatory_submission_events`` — and on none of the four below.

Which tier each table gets, and why
-----------------------------------

The existing pattern is two-tiered (``202607250027``):

IMMUTABLE
    no UPDATE, no DELETE — ``audit_events``.
UNALTERABLE
    no UPDATE; DELETE stays reachable through the owning package's CASCADE —
    the signature/identity/artifact-version tables.

**Neither tier transfers verbatim to these four, and saying otherwise would ship
a trigger that breaks the product on its first approval.** Each of these rows
carries a governed, one-way LIFECYCLE that is expressed as an UPDATE: a
regulatory parameter is proposed as ``draft`` and later approved; approving a
successor closes the prior generation's ``effective_to``; a withdrawal moves
``pending -> applied -> reversed``; a declaration is approved and may later be
revoked. A blanket UPDATE ban would forbid
``operator/services/regulatory_parameters.approve``,
``canonical_withdrawal.apply``/``reverse`` and
``system_of_record.approve``/``revoke`` outright.

So they take the third tier this migration introduces, which is the same
principle applied to a row that becomes authoritative rather than one that is
born authoritative:

SEALED
    the row is UNALTERABLE from the moment it becomes authoritative. Only the
    one-way lifecycle markers the schema itself defines may still be written,
    each of them write-once, and the sealing state may only advance along the
    declared transitions. Everything else — the VALUE, the scope, the window
    opening, the approval evidence — is frozen against in-place edit exactly as
    the UNALTERABLE tier freezes a signature.

Per table:

``regulatory_parameter`` — sealed at ``status='approved'``
    An approved generation is what a sealed run resolved. After approval only
    ``effective_to`` (supersession by a later generation) and ``updated_at`` may
    move; ``value_numeric``/``value_json``, the scope key, ``effective_from``,
    ``unit``, ``source_citation``, ``confirmation_status`` and the four-eyes
    evidence (``proposed_by``/``approved_by``/``approved_at``) are frozen, and
    ``status`` may not leave ``approved``. A draft is still being written and is
    not sealed. ``effective_to`` is deliberately NOT write-once: approving a
    generation that lands between two existing ones legitimately re-closes the
    earlier row.

``reconciliation_exceptions`` — sealed from birth (no lifecycle column)
    ``reconciliation.grant_exception`` requires a named approver, an approval
    timestamp and a maker who is not the checker at INSERT, so the row is
    authoritative the moment it exists and there is no draft state. Only
    revocation (``revoked_at``/``revoked_by``, write-once) and ``updated_at``
    may move. This is the finding's own example: the ceiling
    (``max_gap_fraction``) and the window (``effective_from``/``effective_to``)
    can no longer be widened in place — a wider allowance needs a new, separately
    approved row.

``system_of_record_declarations`` — sealed at ``status='approved'``
    Which source system owns a position type, four-eyed and effective-dated.
    After approval only ``effective_to`` (supersession), the revocation triple
    (write-once) and ``updated_at`` may move.

``canonical_withdrawals`` — sealed at ``status IN ('applied','reversed')``
    A ``pending`` request is not yet an act. Once applied, the scope of what was
    withdrawn (``source_system``/``as_of_date``/``entity``/``position_type``),
    the reason, the requester and the approval evidence — including
    ``withdrawal_batch_id`` and ``rows_withdrawn``, which are the link to the
    canonical rows themselves — are frozen; the only permitted advance is
    ``applied -> reversed``, and the reversal evidence is write-once so a
    reversal cannot be rewritten or undone. ``rows_restored`` is left mutable
    because it is a NOT NULL integer defaulting to 0, so "already recorded"
    cannot be expressed as "not null"; the write-once ``reversal_batch_id`` is
    what pins the reversal to its batch.

DELETE is deliberately NOT blocked on any of the four
-----------------------------------------------------

The same reasoning ``202607250027`` gives for its UNALTERABLE tier, plus two
concrete paths that would break: the hermetic bank fixture purges
``reconciliation_exceptions`` between cases
(``tests/fixtures/canonical_bank_fixture.py``), and five service tests delete
``regulatory_parameter`` rows to exercise the fail-loud "unseeded code" path
that proves a regulatory number is never invented. ALTERATION and FORGERY are
what these registers must prevent and both are now impossible: a row can never
be rewritten, and a deleted generation is *detectable* — the operator audit
trail (itself IMMUTABLE) retains the propose/approve events, and the resolver
raises rather than substituting a value when a mandatory code has no row.

TRUNCATE **is** revoked, on all four: it bypasses row triggers entirely, so
leaving it grantable would leave the whole register erasable in one statement.

Postgres-only. The hermetic suite builds its schema with
``Base.metadata.create_all`` and runs no migration; SQLite has neither triggers
of this shape nor role privileges. The Postgres-gated suites exercise the real
thing (``tests/db/test_governance_append_only.py``).

No column is added and no row is rewritten, so no calculation input, hash or
output changes.

Revision ID: 202608230038
Revises: 202608230041
"""

from __future__ import annotations

from alembic import op

revision = "202608230038"
down_revision = "202608230041"
branch_labels = None
depends_on = None

_GUARD_FUNCTION = "aequoros_governed_row_guard"


class _Sealed:
    """One table's seal rule, in the shape the trigger takes as arguments."""

    __slots__ = ("mutable", "next_states", "seal_column", "seal_states", "table", "write_once")

    def __init__(  # noqa: PLR0913 - the rule is six explicit, named parts
        self,
        table: str,
        *,
        mutable: tuple[str, ...],
        write_once: tuple[str, ...] = (),
        seal_column: str = "",
        seal_states: tuple[str, ...] = (),
        next_states: tuple[str, ...] = (),
    ) -> None:
        self.table = table
        self.mutable = mutable
        self.write_once = write_once
        self.seal_column = seal_column
        self.seal_states = seal_states
        self.next_states = next_states

    @property
    def trigger(self) -> str:
        return f"{self.table}_governed_row"

    def arguments(self) -> tuple[str, str, str, str, str]:
        return (
            ",".join(self.mutable),
            ",".join(self.write_once),
            self.seal_column,
            ",".join(self.seal_states),
            ",".join(self.next_states),
        )


SEALED_TABLES: tuple[_Sealed, ...] = (
    _Sealed(
        "regulatory_parameter",
        mutable=("effective_to", "updated_at"),
        seal_column="status",
        seal_states=("approved",),
    ),
    _Sealed(
        "reconciliation_exceptions",
        mutable=("revoked_at", "revoked_by", "updated_at"),
        write_once=("revoked_at", "revoked_by"),
    ),
    _Sealed(
        "system_of_record_declarations",
        mutable=(
            "effective_to",
            "revoked_at",
            "revoked_by",
            "revocation_reason",
            "updated_at",
        ),
        write_once=("revoked_at", "revoked_by", "revocation_reason"),
        seal_column="status",
        seal_states=("approved",),
    ),
    _Sealed(
        "canonical_withdrawals",
        mutable=(
            "status",
            "reversed_at",
            "reversed_by",
            "reversed_by_user_id",
            "reversal_reason",
            "reversal_batch_id",
            "rows_restored",
            "updated_at",
        ),
        write_once=(
            "reversed_at",
            "reversed_by",
            "reversed_by_user_id",
            "reversal_reason",
            "reversal_batch_id",
        ),
        seal_column="status",
        seal_states=("applied", "reversed"),
        next_states=("reversed",),
    ),
)

# ``string_to_array('', ',')`` yields ``{""}`` on some server versions and ``{}``
# on others, so every list is normalised with ``array_remove(..., '')`` rather
# than relying on either behaviour.
_GUARD_BODY = f"""
CREATE FUNCTION {_GUARD_FUNCTION}() RETURNS trigger
LANGUAGE plpgsql AS $guard$
DECLARE
    mutable_cols text[] := array_remove(
        string_to_array(coalesce(TG_ARGV[0], ''), ','), '');
    write_once_cols text[] := array_remove(
        string_to_array(coalesce(TG_ARGV[1], ''), ','), '');
    seal_column text := coalesce(TG_ARGV[2], '');
    seal_states text[] := array_remove(
        string_to_array(coalesce(TG_ARGV[3], ''), ','), '');
    next_states text[] := array_remove(
        string_to_array(coalesce(TG_ARGV[4], ''), ','), '');
    old_row jsonb := to_jsonb(OLD);
    new_row jsonb := to_jsonb(NEW);
    current_state text;
    next_state text;
    col text;
BEGIN
    IF seal_column <> '' THEN
        current_state := old_row ->> seal_column;
        IF current_state IS NULL OR NOT (current_state = ANY (seal_states)) THEN
            -- Not yet authoritative (a draft, or a request not yet acted on).
            RETURN NEW;
        END IF;
        next_state := new_row ->> seal_column;
        IF next_state IS DISTINCT FROM current_state
           AND NOT (next_state = ANY (next_states)) THEN
            RAISE EXCEPTION
                '% is governed: % may not move from % to % once the row is authoritative',
                TG_TABLE_NAME, seal_column, current_state, coalesce(next_state, 'NULL');
        END IF;
        old_row := old_row - seal_column;
        new_row := new_row - seal_column;
    END IF;

    FOREACH col IN ARRAY write_once_cols LOOP
        IF (old_row ->> col) IS NOT NULL
           AND (new_row ->> col) IS DISTINCT FROM (old_row ->> col) THEN
            RAISE EXCEPTION
                '% is governed: %.% is write-once and is already recorded',
                TG_TABLE_NAME, TG_TABLE_NAME, col;
        END IF;
    END LOOP;

    FOREACH col IN ARRAY mutable_cols LOOP
        old_row := old_row - col;
        new_row := new_row - col;
    END LOOP;

    IF old_row IS DISTINCT FROM new_row THEN
        RAISE EXCEPTION
            '% is governed and sealed: only (%) may change after the row becomes '
            'authoritative. Record a new generation instead of editing this one.',
            TG_TABLE_NAME, array_to_string(mutable_cols, ', ');
    END IF;
    RETURN NEW;
END;
$guard$
"""


def _revoke_truncate(table: str) -> None:
    """TRUNCATE bypasses row triggers, so the trigger alone is not enough."""
    op.execute(f"REVOKE TRUNCATE ON {table} FROM PUBLIC")
    op.execute(
        f"""
        DO $revoke$
        DECLARE r record;
        BEGIN
            FOR r IN SELECT rolname FROM pg_roles
                     WHERE rolname NOT LIKE 'pg\\_%' AND NOT rolsuper
            LOOP
                EXECUTE format('REVOKE TRUNCATE ON {table} FROM %I', r.rolname);
            END LOOP;
        END $revoke$
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(_GUARD_BODY)
    for rule in SEALED_TABLES:
        arguments = ", ".join(f"'{value}'" for value in rule.arguments())
        op.execute(
            f"""
            CREATE TRIGGER {rule.trigger}
            BEFORE UPDATE ON {rule.table}
            FOR EACH ROW EXECUTE FUNCTION {_GUARD_FUNCTION}({arguments})
            """
        )
        _revoke_truncate(rule.table)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for rule in SEALED_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {rule.trigger} ON {rule.table}")
    op.execute(f"DROP FUNCTION IF EXISTS {_GUARD_FUNCTION}()")
