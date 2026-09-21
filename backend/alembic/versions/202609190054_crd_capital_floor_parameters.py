"""Seed the CRD capital-tier floors, buffers and recognition caps (banks).

Regulatory audit B2 / M21 (ICAAP P0, 2026-09-19). Only ``car_min`` had a
control-plane row, so the tighten-only clamp could not enforce any other
capital floor: a board's ``cet1_min``/``tier1_min``/``leverage_min`` passed
straight through, and the default board register carried a 3% leverage floor
against the Capital Requirements Directive's 6% (¶88–90). The capital run, the
ICAAP enterprise stress (¶77 "stays above all minima") and Appendix II's
residual-capital line all measured against that 3%.

This seeds, for ``institution_class='bank'`` (an SDI runs the Act 930 s.29
regime, which structurally zeroes the Basel sub-tier and leverage floors):

* ``cet1_min`` 6.5 and ``tier1_min`` 8 — governed at their current values, so
  no behaviour change, and seeded ``confirmation_status='pending'`` (founder
  directive D-024): whether these minima should also carry the conservation
  buffer awaits stakeholder confirmation (audit item M20), which the citation
  says as "pending stakeholder confirmation". Pending is informational — the
  value still resolves and applies everywhere — and staff confirm or change it
  in the operator console without a code change;
* ``leverage_min`` 6 — a board value below it is now raised to it;
* ``ccb1_pct`` 3, ``ccyb_pct`` 0, ``dsib_buffer_pct`` 0 — the buffers;
* ``at1_cap_pct_rwa`` 1.5 and ``tier2_cap_pct_rwa`` 2 — recognition ceilings.

and, for ``institution_class='sdi'`` (founder directive D-042), the same two
recognition ceilings at the values the platform applied to every institution as
literals until 2026-09-19, ``pending`` because no SDI-specific regulatory basis
has been identified — so an SDI's management-action results do not move when the
literals are replaced by governed rows.

The values and citations are PINNED below rather than imported from the live
``regulatory_parameters.SEED_PARAMETERS`` catalogue (security audit L-4): a later
edit to the catalogue must not change what this revision seeds. A test holds
the pinned rows equal to the catalogue's ``CRD_CAPITAL_FLOOR_SEEDS`` today.

A code is seeded unless an ACTIVE governed row already exists for the same
(scope, code, jurisdiction) — ``status='approved'`` with no ``effective_to``.
A draft operator row does not count: it governs nothing, and letting it
suppress the seed would leave the code ungoverned (QA P0-QA-003). A draft can
only occupy the seed's own generation date by direct insert (the operator
console refuses a past ``effective_from``); if one does, the seed is dated the
day before so the governed floor still exists and the draft is left untouched.
Re-running the step inserts nothing. ``regulatory_parameter`` is a global,
non-RLS reference table, so the data step is not subject to FORCE-RLS and needs
no BYPASSRLS role.

Downgrade removes only the platform seed rows for these eight codes, in either
class (``proposed_by = 'platform_seed'``); an operator-approved generation of the
same code is left alone.

Revision ID: 202609190054
Revises: 202609160053
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa

from alembic import op
from app.core.ids import new_uuid4

revision = "202609190054"
down_revision = "202609160053"
branch_labels = None
depends_on = None

TABLE = "regulatory_parameter"
SEED_ACTOR = "platform_seed"
JURISDICTION = "GH"
SCOPE_TYPE = "institution_class"
SCOPE_KEY = "bank"
EFFECTIVE_FROM = date(2020, 1, 1)

#: (code, value, confirmation_status, citation) — pinned at this revision.
SEEDS: tuple[tuple[str, str, str, str], ...] = (
    (
        "cet1_min",
        "6.5",
        "pending",
        "BoG Capital Requirements Directive 2018, minimum-ratios table (¶71–91) row 1: "
        "minimum CET1 6.5% of RWA (¶73(a)); pending stakeholder confirmation of the "
        "conservation-buffer treatment",
    ),
    (
        "tier1_min",
        "8",
        "pending",
        "BoG Capital Requirements Directive 2018, minimum-ratios table (¶71–91) row 5: "
        "minimum Tier 1 capital ratio 8.0% of RWA (¶73(b)); pending stakeholder "
        "confirmation of the conservation-buffer treatment",
    ),
    (
        "leverage_min",
        "6",
        "confirmed",
        "BoG Capital Requirements Directive 2018 ¶88–90: leverage ratio on a Tier 1 "
        "definition of capital, 'should be a minimum of 6% for all banks' (¶90)",
    ),
    (
        "ccb1_pct",
        "3",
        "confirmed",
        "BoG Capital Requirements Directive 2018, minimum-ratios table (¶71–91) row 2: "
        "capital conservation buffer (CCB1) 3.0%, CET1 only (¶81)",
    ),
    (
        "ccyb_pct",
        "0",
        "confirmed",
        "BoG Capital Requirements Directive 2018, minimum-ratios table (¶71–91) row 9: "
        "countercyclical buffer (CCB2) 0 (¶85)",
    ),
    (
        "dsib_buffer_pct",
        "0",
        "confirmed",
        "BoG Capital Requirements Directive 2018, minimum-ratios table (¶71–91) row 10: "
        "D-SIB buffer 0 (¶86–87)",
    ),
    (
        "at1_cap_pct_rwa",
        "1.5",
        "confirmed",
        "BoG Capital Requirements Directive 2018, minimum-ratios table (¶71–91) row 4: "
        "maximum AT1 1.5% of RWA (¶73(b))",
    ),
    (
        "tier2_cap_pct_rwa",
        "2",
        "confirmed",
        "BoG Capital Requirements Directive 2018, minimum-ratios table (¶71–91) row 6: "
        "maximum Tier 2 2.0% of RWA (¶73(c))",
    ),
)
#: The SDI class's AT1 / Tier 2 recognition caps (D-042) — pinned likewise.
SDI_SEEDS: tuple[tuple[str, str, str, str], ...] = (
    (
        "at1_cap_pct_rwa",
        "1.5",
        "pending",
        "Carried from pre-2026-09-19 platform behaviour; no SDI-specific regulatory basis "
        "identified",
    ),
    (
        "tier2_cap_pct_rwa",
        "2",
        "pending",
        "Carried from pre-2026-09-19 platform behaviour; no SDI-specific regulatory basis "
        "identified",
    ),
)
#: (scope_key, seeds) — every row this revision owns.
SEEDS_BY_SCOPE: tuple[tuple[str, tuple[tuple[str, str, str, str], ...]], ...] = (
    (SCOPE_KEY, SEEDS),
    ("sdi", SDI_SEEDS),
)
PARAM_CODES = tuple(code for code, _value, _status, _citation in SEEDS)


def _seed_table() -> sa.TableClause:
    return sa.table(
        TABLE,
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("scope_type", sa.String),
        sa.column("scope_key", sa.String),
        sa.column("param_code", sa.String),
        sa.column("jurisdiction_code", sa.String),
        sa.column("value_numeric", sa.Numeric),
        sa.column("value_json", sa.JSON),
        sa.column("unit", sa.String),
        sa.column("source_citation", sa.String),
        sa.column("confirmation_status", sa.String),
        sa.column("effective_from", sa.Date),
        sa.column("effective_to", sa.Date),
        sa.column("status", sa.String),
        sa.column("proposed_by", sa.String),
        sa.column("approved_by", sa.String),
        sa.column("approved_at", sa.DateTime(timezone=True)),
        sa.column("change_rationale", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _codes_sql() -> str:
    return ", ".join(f"'{code}'" for code in PARAM_CODES)


def upgrade() -> None:
    bind = op.get_bind()
    governed = {
        (row[0], row[1])
        for row in bind.execute(
            sa.text(
                f"SELECT scope_key, param_code FROM {TABLE} "
                f"WHERE param_code IN ({_codes_sql()}) "
                "AND scope_type = :scope_type AND scope_key IN ('bank', 'sdi') "
                "AND jurisdiction_code = :jurisdiction "
                "AND status = 'approved' AND effective_to IS NULL"
            ),
            {"scope_type": SCOPE_TYPE, "jurisdiction": JURISDICTION},
        )
    }
    # A non-approved row at the seed's own generation date would collide with it
    # on ``uq_regulatory_parameter_generation``; the seed then takes the day
    # before (see the module docstring).
    occupied = {
        (row[0], row[1])
        for row in bind.execute(
            sa.text(
                f"SELECT scope_key, param_code FROM {TABLE} "
                f"WHERE param_code IN ({_codes_sql()}) "
                "AND scope_type = :scope_type AND scope_key IN ('bank', 'sdi') "
                "AND jurisdiction_code = :jurisdiction AND effective_from = :effective_from"
            ),
            {
                "scope_type": SCOPE_TYPE,
                "jurisdiction": JURISDICTION,
                "effective_from": EFFECTIVE_FROM,
            },
        )
    }
    now = datetime.now(UTC)
    rows = [
        {
            "id": new_uuid4(),
            "scope_type": SCOPE_TYPE,
            "scope_key": scope_key,
            "param_code": code,
            "jurisdiction_code": JURISDICTION,
            "value_numeric": Decimal(value),
            "value_json": None,
            "unit": "percent",
            "source_citation": citation,
            "confirmation_status": status,
            "effective_from": (
                EFFECTIVE_FROM - timedelta(days=1)
                if (scope_key, code) in occupied
                else EFFECTIVE_FROM
            ),
            "effective_to": None,
            "status": "approved",
            "proposed_by": SEED_ACTOR,
            "approved_by": SEED_ACTOR,
            "approved_at": now,
            "change_rationale": None,
            "created_at": now,
            "updated_at": now,
        }
        for scope_key, seeds in SEEDS_BY_SCOPE
        for code, value, status, citation in seeds
        if (scope_key, code) not in governed
    ]
    if rows:
        op.bulk_insert(_seed_table(), rows)


def downgrade() -> None:
    op.execute(
        sa.text(
            f"DELETE FROM {TABLE} WHERE param_code IN ({_codes_sql()}) "
            f"AND proposed_by = '{SEED_ACTOR}'"
        )
    )
