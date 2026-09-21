"""Seed the governed numbers the ICAAP Pillar 2 engine applies.

Founder directive D-024 — "Don't hardcode any number but fetch from console" —
means the Pillar 2 engine may hold no threshold, band, shock or severity of its
own. Twenty codes reach it from this control plane: the two review-age windows,
the diversification switch, the internal-consistency tolerance, the
already-includes-CCB1 declaration, and then the tables the engine reads —
concentration benchmark bands, the IRRBB interim scenario set and its outlier
threshold, the FX shock table, the operational severity map and the sovereign
haircut grid.

Eight further ICAAP codes belong to the WORKSPACE (the filing deadline, the
readiness amber window, the materiality thresholds and bands, the two horizons)
and are seeded by ``202609190055``. The two sets are disjoint: each ICAAP code
is seeded exactly once, and a test holds the migrations, the catalogue and the
hermetic fixture to that.

**Most of these are REPRESENTATIVE calibrations** (D-039). The Bank of Ghana has
published no benchmark for concentration bands, FX shocks, operational
severities or sovereign haircuts, so their citations begin "REPRESENTATIVE:" and
they ship ``confirmation_status='pending'``. Every output that consumes one says
so, and staff replace them in the operator console — which now validates the
SHAPE of a table-valued value before accepting it
(``app/domain/policy/parameter_shapes.py``) — without a code change.

As in ``202609190054`` and ``202609190055`` the rows are PINNED here rather than
imported from ``app.services.regulatory_parameters``: a later catalogue edit must
not change what this revision seeded. A test holds the pinned rows equal to the
catalogue's today.

A code is seeded unless an ACTIVE approved row already exists for the same
(scope, code, jurisdiction). A draft operator row governs nothing and so must not
suppress the seed, but it can occupy the seed's generation date — in which case
the seed takes the day before and the draft is left untouched. Re-running
inserts nothing. On a database built from scratch, ``202608200025`` has already
inserted the whole live catalogue (it imports ``seed_rows()``), so this revision
inserts nothing there — which is the same "unless active" rule doing its job.

``regulatory_parameter`` is a global, non-RLS reference table, so the data step
needs no BYPASSRLS role.

Revision ID: 202609190056
Revises: 202609190055
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from alembic import op
from app.core.ids import new_uuid4

revision = "202609190056"
down_revision = "202609190055"
branch_labels = None
depends_on = None

TABLE = "regulatory_parameter"
SEED_ACTOR = "platform_seed"
JURISDICTION = "GH"
SCOPE_TYPE = "institution_class"
SCOPE_KEY = "bank"
EFFECTIVE_FROM = date(2020, 1, 1)

#: (code, value, value_json, unit, confirmation_status, citation) — pinned here.
SEEDS: tuple[tuple[str, str | None, dict[str, Any] | None, str, str, str], ...] = (
    (
        "icaap_independent_review_max_months",
        "12",
        None,
        "months",
        "pending",
        "BoG ICAAP Guideline (Exposure Draft Feb 2026) ¶42: independent review and audit of "
        "the ICAAP at least annually; pending final text",
    ),
    (
        "icaap_review_max_months",
        "12",
        None,
        "months",
        "pending",
        "BoG ICAAP Guideline (Exposure Draft Feb 2026) ¶73: the ICAAP is reviewed and "
        "updated at least annually; pending final text",
    ),
    (
        "icaap_diversification_benefit_allowed",
        "0",
        None,
        "boolean",
        "pending",
        "AequorOS platform policy (regulatory audit M19): Pillar 2 risks are summed as in "
        "Stress Testing Guideline Appendix II Table 5; no inter-risk diversification unless "
        "confirmed",
    ),
    (
        "icaap_pillar2_source_tolerance_pct",
        "1",
        None,
        "percent",
        "pending",
        "REPRESENTATIVE: AequorOS internal-control tolerance for Pillar 2 source "
        "consistency (regulatory audit M1); relative difference; no published value",
    ),
    (
        "icaap_car_min_includes_ccb1",
        "1",
        None,
        "boolean",
        "confirmed",
        "BoG Capital Requirements Directive 2018 ¶71 (10%) + ¶75 CCB1 (3%): the governed "
        "car_min for banks already includes the capital conservation buffer",
    ),
    (
        "ccr_min_dimension_coverage_pct",
        "80",
        None,
        "percent",
        "pending",
        "REPRESENTATIVE: minimum share of the credit book stating a concentration dimension "
        "before the top benchmark band applies (regulatory audit M5); no published value",
    ),
    (
        "ccr_metric_set",
        None,
        {"schema": "icaap-ccr-metric-set-v1", "single_name": ["hhi", "crn"], "sector": ["hhi"]},
        "metric_set",
        "pending",
        "BoG Credit Concentration Guidelines (Sept 2025) definitions: model-free measures "
        "are HHI, concentration ratios and Gini; which ones drive the add-on is platform "
        "methodology",
    ),
    (
        "ccr_name_cr_n",
        "20",
        None,
        "count",
        "pending",
        "REPRESENTATIVE: number of largest obligor groups in the name concentration ratio "
        "(CRn); no published value",
    ),
    (
        "ccr_name_bands_hhi",
        None,
        {
            "schema": "icaap-band-table-v1",
            "metric": "hhi",
            "dimension": "single_name",
            "scale": "unit_interval",
            "mode": "step",
            "basis": "pct_pillar1_credit_capital",
            "bands": [
                {"lower": "0", "upper": "0.01", "addon": "0"},
                {"lower": "0.01", "upper": "0.02", "addon": "2"},
                {"lower": "0.02", "upper": "0.05", "addon": "5"},
                {"lower": "0.05", "upper": "0.10", "addon": "10"},
                {"lower": "0.10", "upper": None, "addon": "15"},
            ],
        },
        "band_table",
        "pending",
        "REPRESENTATIVE: benchmark bands for name HHI (0-1 scale); add-on as % of Pillar 1 "
        "credit capital; BoG has published no benchmark",
    ),
    (
        "ccr_name_bands_gini",
        None,
        {
            "schema": "icaap-band-table-v1",
            "metric": "gini",
            "dimension": "single_name",
            "scale": "unit_interval",
            "mode": "step",
            "basis": "pct_pillar1_credit_capital",
            "bands": [
                {"lower": "0", "upper": "0.85", "addon": "0"},
                {"lower": "0.85", "upper": "0.95", "addon": "2"},
                {"lower": "0.95", "upper": None, "addon": "5"},
            ],
        },
        "band_table",
        "pending",
        "REPRESENTATIVE: benchmark bands for name Gini (raw, 0-1); add-on as % of Pillar 1 "
        "credit capital; used only if ccr_metric_set lists gini; no published benchmark",
    ),
    (
        "ccr_name_bands_crn",
        None,
        {
            "schema": "icaap-band-table-v1",
            "metric": "crn",
            "dimension": "single_name",
            "scale": "unit_interval",
            "mode": "step",
            "basis": "pct_pillar1_credit_capital",
            "bands": [
                {"lower": "0", "upper": "0.20", "addon": "0"},
                {"lower": "0.20", "upper": "0.35", "addon": "3"},
                {"lower": "0.35", "upper": "0.50", "addon": "6"},
                {"lower": "0.50", "upper": None, "addon": "10"},
            ],
        },
        "band_table",
        "pending",
        "REPRESENTATIVE: benchmark bands for the name concentration ratio CRn (share of "
        "book); add-on as % of Pillar 1 credit capital; no published benchmark",
    ),
    (
        "ccr_sector_bands_hhi",
        None,
        {
            "schema": "icaap-band-table-v1",
            "metric": "hhi",
            "dimension": "sector",
            "scale": "unit_interval",
            "mode": "step",
            "basis": "pct_pillar1_credit_capital",
            "bands": [
                {"lower": "0", "upper": "0.15", "addon": "0"},
                {"lower": "0.15", "upper": "0.25", "addon": "3"},
                {"lower": "0.25", "upper": "0.40", "addon": "7"},
                {"lower": "0.40", "upper": None, "addon": "12"},
            ],
            "taxonomy": "canonical_sector_attribute",
        },
        "band_table",
        "pending",
        "REPRESENTATIVE: benchmark bands for sector HHI (0-1) over the canonical sector "
        "attribute; add-on as % of Pillar 1 credit capital; no published benchmark",
    ),
    (
        "ccr_name_hhi_coeff",
        "0.5",
        None,
        "ratio",
        "pending",
        "REPRESENTATIVE: name-HHI coefficient of the HHI-proportional heuristic (legacy "
        "stress default); not a granularity adjustment (D-016)",
    ),
    (
        "ccr_sector_hhi_coeff",
        "0.5",
        None,
        "ratio",
        "pending",
        "REPRESENTATIVE: sector-HHI coefficient of the HHI-proportional heuristic (legacy "
        "stress default); not a granularity adjustment (D-016)",
    ),
    (
        "irrbb_outlier_threshold_pct_tier1",
        "15",
        None,
        "percent",
        "pending",
        "BoG IRRBB Guideline (Exposure Draft Feb 2026) preamble: IRRBB exposure above 15% "
        "of Tier 1 capital marks an outlier bank; pending final text",
    ),
    (
        "icaap_irrbb_interim_scenarios",
        None,
        {
            "schema": "icaap-code-list-v1",
            "codes": [
                "parallel_up_200",
                "parallel_down_200",
                "short_up_250",
                "short_down_250",
                "steepener",
                "flattener",
                "parallel_up_450",
                "parallel_down_450",
            ],
            "required": ["parallel_up_450", "parallel_down_450"],
        },
        "code_list",
        "pending",
        "BoG IRRBB Guideline (Exposure Draft Feb 2026) App II: parallel shocks mandatory "
        "for the annual ICAAP; interim set over the legacy engine scenario codes (D-013)",
    ),
    (
        "fx_p2_shock_pct",
        None,
        {
            "schema": "icaap-fx-shocks-v1",
            "horizon": "one_year",
            "depreciation": {"default": "30"},
            "appreciation": {"default": "10"},
        },
        "shock_table",
        "pending",
        "REPRESENTATIVE: one-year reporting-currency depreciation and appreciation shocks "
        "for the ICAAP FX add-on (regulatory audit M8); no published value",
    ),
    (
        "op_p2_scenario_severity_pct_gross_income",
        None,
        {
            "schema": "icaap-severity-map-v1",
            "basis": "pct_annual_gross_income",
            "scenarios": {
                "cloud_outage": "4",
                "cyber_data_corruption": "12",
                "payments_outage": "8",
                "telecom_failure": "3",
                "staff_unavailability": "5",
                "flood_epidemic": "7",
                "civil_strife": "9",
            },
        },
        "severity_map",
        "pending",
        "REPRESENTATIVE: severities as % of annual gross income; the seven scenarios follow "
        "BoG Stress Testing Guideline (ED Feb 2026) Appendix I ¶12 (audit M7)",
    ),
    (
        "sov_p2_haircut_pct",
        None,
        {
            "schema": "icaap-haircut-grid-v1",
            "tenor_buckets": [
                {"key": "up_to_1y", "max_years": "1"},
                {"key": "1y_to_5y", "max_years": "5"},
                {"key": "over_5y", "max_years": None},
            ],
            "currency_kinds": ["reporting", "foreign"],
            "haircut_pct": {
                "reporting": {"up_to_1y": "10", "1y_to_5y": "25", "over_5y": "35"},
                "foreign": {"up_to_1y": "15", "1y_to_5y": "35", "over_5y": "45"},
            },
        },
        "haircut_grid",
        "pending",
        "REPRESENTATIVE: sovereign haircuts by tenor and currency for the ICAAP sovereign "
        "add-on (audit M2); Stress Guideline App III names sovereign restructuring; no "
        "published haircut",
    ),
    (
        "sov_p2_exposure_categories",
        None,
        {"schema": "icaap-code-list-v1", "codes": ["gov_securities"], "required": []},
        "code_list",
        "pending",
        "AequorOS platform methodology: canonical fact categories treated as sovereign "
        "exposure when no manual sovereign grid is entered (derived mode)",
    ),
)
PARAM_CODES = tuple(code for code, *_rest in SEEDS)


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
        row[0]
        for row in bind.execute(
            sa.text(
                f"SELECT param_code FROM {TABLE} "
                f"WHERE param_code IN ({_codes_sql()}) "
                "AND scope_type = :scope_type AND scope_key = :scope_key "
                "AND jurisdiction_code = :jurisdiction "
                "AND status = 'approved' AND effective_to IS NULL"
            ),
            {"scope_type": SCOPE_TYPE, "scope_key": SCOPE_KEY, "jurisdiction": JURISDICTION},
        )
    }
    occupied = {
        row[0]
        for row in bind.execute(
            sa.text(
                f"SELECT param_code FROM {TABLE} "
                f"WHERE param_code IN ({_codes_sql()}) "
                "AND scope_type = :scope_type AND scope_key = :scope_key "
                "AND jurisdiction_code = :jurisdiction AND effective_from = :effective_from"
            ),
            {
                "scope_type": SCOPE_TYPE,
                "scope_key": SCOPE_KEY,
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


def downgrade() -> None:
    op.execute(
        sa.text(
            f"DELETE FROM {TABLE} WHERE param_code IN ({_codes_sql()}) "
            f"AND proposed_by = '{SEED_ACTOR}'"
        )
    )
