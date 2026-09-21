"""The governed parameters the ICAAP FILING plane reads.

Founder directive D-024 — "Don't hardcode any number but fetch from console" —
and D-032, which applies it to dates. Two rows:

``icaap_report_first_as_of_date``
    The first as-of date an ``ICAAP-REPORT`` may be filed for. The BoG ICAAP
    Guideline's exposure draft is effective 1 January 2027 and does not say
    which year end it first bites on; 31 December 2026 is the platform's
    reading, NOT BoG's, so it ships ``confirmation_status='pending'`` and is
    corrected in the operator console the day BoG answers. Carried as a
    structural body (``{"schema": "icaap-effective-date-v1", "date": …}``)
    because a date is not a number and a day-count would be unreadable in the
    console this row exists to be edited from.

``icaap_stress_severe_scenarios_min``
    How many severe-but-plausible scenarios an ICAAP stress annex must report.

The other codes the filing plane reads are already governed:
``icaap_submission_months`` / ``icaap_disclosure_submission_months`` /
``icaap_stress_horizon_years_min`` were seeded by ``202609190055``. Re-seeding
them here would create a second generation of the same code on the same day.

As in ``202609190054``–``202609190056`` the values are PINNED in this file
rather than imported from the live catalogue, so a later catalogue edit cannot
change what this revision seeded; a test holds the two equal.

``regulatory_parameter`` is a global, non-RLS reference table, so the data step
needs no BYPASSRLS role.

Revision ID: 202609190059
Revises: 202609190058
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from alembic import op
from app.core.ids import new_uuid4

revision = "202609190059"
down_revision = "202609190058"
branch_labels = None
depends_on = None

PARAM_TABLE = "regulatory_parameter"
SEED_ACTOR = "platform_seed"
JURISDICTION = "GH"
SCOPE_TYPE = "institution_class"
SCOPE_KEY = "bank"
EFFECTIVE_FROM = date(2020, 1, 1)

_FIRST_AS_OF: dict[str, Any] = {
    "schema": "icaap-effective-date-v1",
    "date": "2026-12-31",
}

#: (code, value, value_json, unit, confirmation_status, citation) — pinned here.
SEEDS: tuple[tuple[str, str | None, dict[str, Any] | None, str, str, str], ...] = (
    (
        "icaap_report_first_as_of_date",
        None,
        _FIRST_AS_OF,
        "date",
        "pending",
        "BoG Guideline on ICAAP (Exposure Draft, February 2026) ¶9: effective "
        "1 January 2027. The first annual as-of date is INFERRED as the 31 December "
        "2026 year end, pending confirmation with BoG; an earlier as-of is refused, "
        "never back-dated",
    ),
    (
        "icaap_stress_severe_scenarios_min",
        "1",
        None,
        "count",
        "pending",
        "BoG Stress Testing Guideline (Exposure Draft Feb 2026) ¶35, ¶75: at least "
        "one severe but plausible scenario is run and reported; pending final text",
    ),
)
PARAM_CODES = tuple(code for code, *_rest in SEEDS)


def _seed_table() -> sa.TableClause:
    return sa.table(
        PARAM_TABLE,
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


def _seed_parameters() -> None:
    """Insert each code unless an APPROVED row already governs it.

    Same rule as ``202609190055``: a draft operator row governs nothing, so it
    must not suppress the seed, but it can occupy the seed's generation date —
    in which case the seed takes the day before and the draft is left alone.
    """
    bind = op.get_bind()
    scope = {"scope_type": SCOPE_TYPE, "scope_key": SCOPE_KEY, "jurisdiction": JURISDICTION}
    governed = {
        row[0]
        for row in bind.execute(
            sa.text(
                f"SELECT param_code FROM {PARAM_TABLE} "
                f"WHERE param_code IN ({_codes_sql()}) "
                "AND scope_type = :scope_type AND scope_key = :scope_key "
                "AND jurisdiction_code = :jurisdiction "
                "AND status = 'approved' AND effective_to IS NULL"
            ),
            scope,
        )
    }
    occupied = {
        row[0]
        for row in bind.execute(
            sa.text(
                f"SELECT param_code FROM {PARAM_TABLE} "
                f"WHERE param_code IN ({_codes_sql()}) "
                "AND scope_type = :scope_type AND scope_key = :scope_key "
                "AND jurisdiction_code = :jurisdiction AND effective_from = :effective_from"
            ),
            {**scope, "effective_from": EFFECTIVE_FROM},
        )
    }
    now = datetime.now(UTC)
    rows = [
        {
            "id": new_uuid4(),
            "scope_type": SCOPE_TYPE,
            "scope_key": SCOPE_KEY,
            "param_code": code,
            "jurisdiction_code": JURISDICTION,
            "value_numeric": None if value is None else Decimal(value),
            "value_json": value_json,
            "unit": unit,
            "source_citation": citation,
            "confirmation_status": status,
            "effective_from": (
                EFFECTIVE_FROM - timedelta(days=1) if code in occupied else EFFECTIVE_FROM
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
        for code, value, value_json, unit, status, citation in SEEDS
        if code not in governed
    ]
    if rows:
        op.bulk_insert(_seed_table(), rows)


def upgrade() -> None:
    _seed_parameters()


def downgrade() -> None:
    op.execute(
        sa.text(
            f"DELETE FROM {PARAM_TABLE} WHERE param_code IN ({_codes_sql()}) "
            f"AND proposed_by = '{SEED_ACTOR}'"
        )
    )
