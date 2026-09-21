"""The governed parameters the Nigerian and Kenyan ICAAP frameworks read.

The CBN and CBK frameworks shipped as data in P5-E and have been **published
but non-functional** ever since: they quote the same parameter codes as Ghana,
resolution matches ``jurisdiction_code`` exactly and never falls back, and no
row existed for ``NG`` or ``KE``. So every read — the filing deadline, the
review intervals, the materiality matrix, the Kenyan horizons — answered
``missing_parameter`` and no cycle could be opened. This revision seeds those
rows. Twenty of them: nine Nigerian, eleven Kenyan. Two per jurisdiction are
not quoted by any checklist sentence at all — the Pillar 2 register resolves
``icaap_diversification_benefit_allowed`` and
``icaap_pillar2_source_tolerance_pct`` for every cycle whatever the framework,
so without them readiness refused before it reached a single finding. Both are
platform policy, not a regulator's number, in every jurisdiction.

**Sourcing, stated plainly because the rows will outlive this message.**
Neither primary text is in this repository and nobody on this platform has read
either one. The values come from the extraction record behind
``app/domain/icaap/frameworks/{ng,ke}/SOURCES.md`` — a whole-document reading
made in the P5 design session of 2026-09-19 whose ``sha256`` identifies a copy
to obtain and cannot be re-verified from a checkout (D-076). Every row is
therefore ``confirmation_status='pending'`` and says so in its own citation, in
words that describe the actual gap: *we have not read the regulator*, which is
not what Ghana's "awaiting stakeholder confirmation" rows say. What is the
platform's judgement rather than a regulator's number — the 5x5 materiality
matrix, which per that extraction neither instrument prescribes — carries the
``REPRESENTATIVE:`` prefix, which is load-bearing: ``services/icaap/params.py``
tests for it at the START of the string and that flag is what labels the value
on readiness, in the Pillar 2 register and in a frozen report's provenance.

**Not seeded here, deliberately.** The two "ideally five years" Kenyan horizon
codes (no framework quotes them; reinstating that wording is a new framework
version, not a row), the disclosure period (neither instrument requires
publication — both frameworks declare ``disclosure: null``), and every IRRBB
standardised-framework, granularity, CCR and capital-floor calibration: no
CBN/CBK figure has been read for any of those, so the methods must keep
refusing rather than quietly borrow Ghana's numbers.

**D-053.** A duplicate seed does not fail — it takes the day before and
silently becomes the ACTIVE generation of a row nobody proposed. Every code
here already exists for ``GH``, so both the "already governed" probe and the
downgrade are scoped to ``jurisdiction_code IN ('NG','KE')``; a Ghanaian row
must be neither suppressed by nor deleted with this revision.

As in ``202609190054``–``202609190061`` the values are PINNED in this file
rather than imported from the live catalogue, so a later catalogue edit cannot
change what this revision seeded; a test holds the two equal.

``regulatory_parameter`` is a global, non-RLS reference table, so the data step
needs no BYPASSRLS role.

Revision ID: 202609200063
Revises: 202609190062
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from alembic import op
from app.core.ids import new_uuid4

revision = "202609200063"
down_revision = "202609190062"
branch_labels = None
depends_on = None

PARAM_TABLE = "regulatory_parameter"
SEED_ACTOR = "platform_seed"
JURISDICTIONS = ("NG", "KE")
SCOPE_TYPE = "institution_class"
SCOPE_KEY = "bank"
EFFECTIVE_FROM = date(2020, 1, 1)

_MATERIALITY_BANDS: dict[str, Any] = {
    "schema": "icaap-score-bands-v1",
    "bands": [
        {"key": "low", "label": "Low", "min_score": 1, "max_score": 4},
        {"key": "medium", "label": "Medium", "min_score": 5, "max_score": 9},
        {"key": "high", "label": "High", "min_score": 10, "max_score": 16},
        {"key": "very_high", "label": "Very high", "min_score": 17, "max_score": 25},
    ],
}

_NG_UNREAD = (
    "Per the 2026-09-19 extraction (ng/SOURCES.md); the CBN text is not held or read "
    "here. Pending verification."
)
_KE_UNREAD = (
    "Per the 2026-09-19 extraction (ke/SOURCES.md); the CBK text is not held or read "
    "here. Pending verification."
)
_MATRIX_SCORE = (
    "REPRESENTATIVE: AequorOS default 5x5 materiality matrix; a risk is material at or "
    "above this likelihood x impact score. "
)
_MATRIX_IMPACT = (
    "REPRESENTATIVE: AequorOS default 5x5 materiality matrix; a risk is material at or "
    "above this impact score whatever its likelihood. "
)
_MATRIX_BANDS = (
    "REPRESENTATIVE: AequorOS default 5x5 materiality matrix rating bands over likelihood "
    "x impact scores. "
)
_NG_NO_MATRIX = "The CBN Guideline prescribes no matrix (2026-09-19 extraction, not re-read)."
_KE_NO_MATRIX = "The CBK Guidance Note prescribes no matrix (2026-09-19 extraction, not re-read)."
_AMBER = (
    "AequorOS platform policy: days before the filing deadline from which ICAAP readiness "
    "shows amber; not a regulatory value"
)
_NO_DIVERSIFICATION = (
    "AequorOS platform policy: Pillar 2 risks are summed with no inter-risk diversification "
    "benefit, the conservative default. No allowance for one has been read for this regulator."
)
_SOURCE_TOLERANCE = (
    "REPRESENTATIVE: AequorOS internal-control tolerance for Pillar 2 source consistency; "
    "relative difference; no published value"
)

#: (jurisdiction, code, value, value_json, unit, confirmation_status, citation).
SEEDS: tuple[tuple[str, str, str | None, dict[str, Any] | None, str, str, str], ...] = (
    (
        "NG",
        "icaap_submission_months",
        "4",
        None,
        "months",
        "pending",
        "CBN Revised SRP/ICAAP Guidelines (Sep 2021) para 51: annual report submitted by "
        f"end of April. {_NG_UNREAD}",
    ),
    (
        "NG",
        "icaap_review_max_months",
        "12",
        None,
        "months",
        "pending",
        "CBN Revised SRP/ICAAP Guidelines (Sep 2021) para 52: the ICAAP is updated at least "
        f"annually. {_NG_UNREAD}",
    ),
    (
        "NG",
        "icaap_independent_review_max_months",
        "12",
        None,
        "months",
        "pending",
        "CBN Revised SRP/ICAAP Guidelines (Sep 2021) para 47: independent review and audit, "
        f"frequency stated as varying; read as annual. {_NG_UNREAD}",
    ),
    (
        "NG",
        "icaap_materiality_material_min_score",
        "10",
        None,
        "score",
        "pending",
        f"{_MATRIX_SCORE}{_NG_NO_MATRIX}",
    ),
    (
        "NG",
        "icaap_materiality_material_min_impact",
        "4",
        None,
        "score",
        "pending",
        f"{_MATRIX_IMPACT}{_NG_NO_MATRIX}",
    ),
    (
        "NG",
        "icaap_materiality_rating_bands",
        None,
        _MATERIALITY_BANDS,
        "score_bands",
        "pending",
        f"{_MATRIX_BANDS}{_NG_NO_MATRIX}",
    ),
    ("NG", "icaap_deadline_amber_days", "30", None, "days", "pending", _AMBER),
    (
        "NG",
        "icaap_diversification_benefit_allowed",
        "0",
        None,
        "boolean",
        "pending",
        _NO_DIVERSIFICATION,
    ),
    (
        "NG",
        "icaap_pillar2_source_tolerance_pct",
        "1",
        None,
        "percent",
        "pending",
        _SOURCE_TOLERANCE,
    ),
    (
        "KE",
        "icaap_submission_months",
        "4",
        None,
        "months",
        "pending",
        "CBK Guidance Note on ICAAP (Nov 2016) para 5(b): not later than 30 April for the "
        f"31 December position, month-end clamped. {_KE_UNREAD}",
    ),
    (
        "KE",
        "icaap_review_max_months",
        "12",
        None,
        "months",
        "pending",
        "CBK Guidance Note on ICAAP (Nov 2016) para 4(i)(c): Board review of the ICAAP "
        f"policies, read as at least annual. {_KE_UNREAD}",
    ),
    (
        "KE",
        "icaap_independent_review_max_months",
        "12",
        None,
        "months",
        "pending",
        "CBK Guidance Note on ICAAP (Nov 2016) para 4(vi)(c): independent review and audit, "
        f"frequency stated as varying; read as annual. {_KE_UNREAD}",
    ),
    (
        "KE",
        "icaap_stress_horizon_years_min",
        "3",
        None,
        "years",
        "pending",
        "CBK Guidance Note on ICAAP (Nov 2016) para 4(iv)(a): forward-looking stress tests "
        f"over a minimum of three years. {_KE_UNREAD}",
    ),
    (
        "KE",
        "icaap_capital_planning_horizon_years_min",
        "3",
        None,
        "years",
        "pending",
        "CBK Guidance Note on ICAAP (Nov 2016) para 2(b): capital forecast over a minimum of "
        f"three years. {_KE_UNREAD}",
    ),
    (
        "KE",
        "icaap_materiality_material_min_score",
        "10",
        None,
        "score",
        "pending",
        f"{_MATRIX_SCORE}{_KE_NO_MATRIX}",
    ),
    (
        "KE",
        "icaap_materiality_material_min_impact",
        "4",
        None,
        "score",
        "pending",
        f"{_MATRIX_IMPACT}{_KE_NO_MATRIX}",
    ),
    (
        "KE",
        "icaap_materiality_rating_bands",
        None,
        _MATERIALITY_BANDS,
        "score_bands",
        "pending",
        f"{_MATRIX_BANDS}{_KE_NO_MATRIX}",
    ),
    ("KE", "icaap_deadline_amber_days", "30", None, "days", "pending", _AMBER),
    (
        "KE",
        "icaap_diversification_benefit_allowed",
        "0",
        None,
        "boolean",
        "pending",
        _NO_DIVERSIFICATION,
    ),
    (
        "KE",
        "icaap_pillar2_source_tolerance_pct",
        "1",
        None,
        "percent",
        "pending",
        _SOURCE_TOLERANCE,
    ),
)
PARAM_CODES = tuple(sorted({code for _jurisdiction, code, *_rest in SEEDS}))


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


def _jurisdictions_sql() -> str:
    return ", ".join(f"'{code}'" for code in JURISDICTIONS)


def _seed_parameters() -> None:
    """Insert each (jurisdiction, code) unless an APPROVED row already governs it.

    Same rule as ``202609190055``, with the KEY widened to include the
    jurisdiction. Every code here is also a Ghanaian row, so a code-only probe
    would find Ghana's and skip the whole revision. A draft operator row governs
    nothing and must not suppress the seed, but it can occupy the seed's
    generation date — in which case the seed takes the day before.
    """
    bind = op.get_bind()
    scope = {"scope_type": SCOPE_TYPE, "scope_key": SCOPE_KEY}
    governed = {
        (row[0], row[1])
        for row in bind.execute(
            sa.text(
                f"SELECT jurisdiction_code, param_code FROM {PARAM_TABLE} "
                f"WHERE param_code IN ({_codes_sql()}) "
                f"AND jurisdiction_code IN ({_jurisdictions_sql()}) "
                "AND scope_type = :scope_type AND scope_key = :scope_key "
                "AND status = 'approved' AND effective_to IS NULL"
            ),
            scope,
        )
    }
    occupied = {
        (row[0], row[1])
        for row in bind.execute(
            sa.text(
                f"SELECT jurisdiction_code, param_code FROM {PARAM_TABLE} "
                f"WHERE param_code IN ({_codes_sql()}) "
                f"AND jurisdiction_code IN ({_jurisdictions_sql()}) "
                "AND scope_type = :scope_type AND scope_key = :scope_key "
                "AND effective_from = :effective_from"
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
            "jurisdiction_code": jurisdiction,
            "value_numeric": None if value is None else Decimal(value),
            "value_json": value_json,
            "unit": unit,
            "source_citation": citation,
            "confirmation_status": confirmation,
            "effective_from": (
                EFFECTIVE_FROM - timedelta(days=1)
                if (jurisdiction, code) in occupied
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
        for jurisdiction, code, value, value_json, unit, confirmation, citation in SEEDS
        if (jurisdiction, code) not in governed
    ]
    if rows:
        op.bulk_insert(_seed_table(), rows)


def upgrade() -> None:
    _seed_parameters()


def downgrade() -> None:
    # Scoped to NG and KE: the same codes govern Ghana, and this revision did
    # not seed those rows (D-053).
    op.execute(
        sa.text(
            f"DELETE FROM {PARAM_TABLE} WHERE param_code IN ({_codes_sql()}) "
            f"AND jurisdiction_code IN ({_jurisdictions_sql()}) "
            f"AND proposed_by = '{SEED_ACTOR}'"
        )
    )
