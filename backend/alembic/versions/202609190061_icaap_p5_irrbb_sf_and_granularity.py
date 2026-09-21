"""Seed the governed numbers of the IRRBB Standardised Framework and the
granularity adjustment, and admit the ``irr_sf`` module on the run table.

Founder directive D-024 — "Don't hardcode any number but fetch from console" —
means the Standardised Framework engine holds no calibration of its own. The
nineteen time buckets, the six shock tables, the rotation coefficients, the
behavioural multipliers, the deposit caps, the materiality threshold, the
scenario sets, the earnings horizon and the commencement date all reach it from
this control plane, and an absent row is a typed ``missing_parameter`` refusal
rather than a default (``app/domain/irr/standardised_params.py``).

Nine further rows calibrate the full Gordy-Lutkebohmert granularity adjustment.
It is not regulator-specific, so those carry Basel IRB provenance where one
exists and say REPRESENTATIVE where none does — ``ga_min_effective_names``
above all, because it decides whether the method engages at all (D-059 F9).

**Deliberately NOT seeded here:**

* ``irrbb_outlier_threshold_pct_tier1`` — the Standardised Framework shares the
  Pillar 2 row seeded by ``202609190056``. Re-seeding a code does not fail, it
  BACK-DATES the live row and silently wins (D-053), so a shared code is seeded
  once and reused.
* ``irrbb_sf_option_vol_uplift_pct`` — reserved for the automatic-option
  valuation that does not exist yet. A seeded calibration would suggest options
  are priced; a book that holds them is refused outright (DV-010).

The BoG IRRBB Guideline is an exposure draft, so every Standardised Framework
row ships ``confirmation_status='pending'`` and every output that quotes one
says so (D-039).

As in ``202609190054``-``202609190059`` the rows are PINNED here rather than
imported from ``app.services.regulatory_parameters``: a later catalogue edit
must not change what this revision seeded. A test holds the pinned rows equal
to the catalogue's today.

A code is seeded unless an ACTIVE approved row already exists for the same
(scope, code, jurisdiction). A draft operator row governs nothing and so must
not suppress the seed, but it can occupy the seed's generation date — in which
case the seed takes the day before and the draft is left untouched. Re-running
inserts nothing.

The constraint half admits ``irr_sf`` on ``regulatory_runs.module`` and the
three Standardised Framework sections on ``regulatory_line_items.section``.
Constraint rewrite only — no existing row is touched, and no live-plane
vocabulary is widened, because the Standardised Framework is not a live module:
it mints immutable filing evidence on request and writes no ``live_metrics``.

``regulatory_parameter`` is a global, non-RLS reference table, so the data step
needs no BYPASSRLS role.

Revision ID: 202609190061
Revises: 202609190060
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa

from alembic import op
from app.core.ids import new_uuid4
from app.db.session import force_rls_suspended

revision = "202609190061"
down_revision = "202609190060"
branch_labels = None
depends_on = None

TABLE = "regulatory_parameter"
SEED_ACTOR = "platform_seed"
JURISDICTION = "GH"
SCOPE_TYPE = "institution_class"
SCOPE_KEY = "bank"
EFFECTIVE_FROM = date(2020, 1, 1)

_RUNS_OLD = (
    "module IN ('liquidity', 'capital', 'forecast', 'optimizer', 'whatif', "
    "'irr', 'fx', 'ftp', 'reverse_stress', 'enterprise_stress', 'credit')"
)
_RUNS_NEW = (
    "module IN ('liquidity', 'capital', 'forecast', 'optimizer', 'whatif', "
    "'irr', 'irr_sf', 'fx', 'ftp', 'reverse_stress', 'enterprise_stress', "
    "'credit')"
)
_SECTIONS_OLD = (
    "section IN ('hqla', 'outflow', 'inflow', 'asf', 'rsf', 'credit_rwa', "
    "'market_rwa', 'operational_rwa', 'capital_component', 'ratio', "
    "'irr_gap', 'irr_eve', 'irr_ear', 'fx_position', 'fx_var', 'fx_hedge', "
    "'ftp_curve', 'ftp_product', 'ftp_branch')"
)
_SECTIONS_NEW = (
    "section IN ('hqla', 'outflow', 'inflow', 'asf', 'rsf', 'credit_rwa', "
    "'market_rwa', 'operational_rwa', 'capital_component', 'ratio', "
    "'irr_gap', 'irr_eve', 'irr_ear', 'irr_sf_ladder', 'irr_sf_eve', "
    "'irr_sf_nii', 'fx_position', 'fx_var', 'fx_hedge', "
    "'ftp_curve', 'ftp_product', 'ftp_branch')"
)

#: (code, value, value_json, unit, confirmation_status, citation) — pinned here.
SEEDS: tuple[tuple[str, str | None, dict[str, Any] | None, str, str, str], ...] = (
    (
        "irrbb_sf_time_buckets",
        None,
        {
            "schema": "irrbb-sf-buckets-v1",
            "buckets": [
                {"key": "b01", "label": "Overnight", "upper": "1D", "midpoint_years": "0.0028"},
                {
                    "key": "b02",
                    "label": "Overnight to 1 month",
                    "upper": "1M",
                    "midpoint_years": "0.0417",
                },
                {"key": "b03", "label": "1 to 3 months", "upper": "3M", "midpoint_years": "0.1667"},
                {"key": "b04", "label": "3 to 6 months", "upper": "6M", "midpoint_years": "0.375"},
                {"key": "b05", "label": "6 to 9 months", "upper": "9M", "midpoint_years": "0.625"},
                {
                    "key": "b06",
                    "label": "9 months to 1 year",
                    "upper": "12M",
                    "midpoint_years": "0.875",
                },
                {"key": "b07", "label": "1 to 1.5 years", "upper": "18M", "midpoint_years": "1.25"},
                {"key": "b08", "label": "1.5 to 2 years", "upper": "24M", "midpoint_years": "1.75"},
                {"key": "b09", "label": "2 to 3 years", "upper": "3Y", "midpoint_years": "2.5"},
                {"key": "b10", "label": "3 to 4 years", "upper": "4Y", "midpoint_years": "3.5"},
                {"key": "b11", "label": "4 to 5 years", "upper": "5Y", "midpoint_years": "4.5"},
                {"key": "b12", "label": "5 to 6 years", "upper": "6Y", "midpoint_years": "5.5"},
                {"key": "b13", "label": "6 to 7 years", "upper": "7Y", "midpoint_years": "6.5"},
                {"key": "b14", "label": "7 to 8 years", "upper": "8Y", "midpoint_years": "7.5"},
                {"key": "b15", "label": "8 to 9 years", "upper": "9Y", "midpoint_years": "8.5"},
                {"key": "b16", "label": "9 to 10 years", "upper": "10Y", "midpoint_years": "9.5"},
                {"key": "b17", "label": "10 to 15 years", "upper": "15Y", "midpoint_years": "12.5"},
                {"key": "b18", "label": "15 to 20 years", "upper": "20Y", "midpoint_years": "17.5"},
                {"key": "b19", "label": "Over 20 years", "upper": None, "midpoint_years": "25"},
            ],
        },
        "tenor_table",
        "pending",
        "BoG IRRBB Guideline (Exposure Draft, February 2026) Appendix I ¶8-9, Table 1: nineteen "
        "upper-inclusive buckets with their midpoints",
    ),
    (
        "irrbb_sf_parallel_shock_bp",
        None,
        {
            "schema": "irrbb-sf-currency-bp-v1",
            "GHS": "450",
            "USD": "200",
            "EUR": "225",
            "GBP": "275",
            "CNY": "225",
            "OTHER": "325",
        },
        "bps",
        "pending",
        "BoG IRRBB Guideline (Exposure Draft, February 2026) Appendix II ¶1-3, Table 5: prescribed "
        "parallel shocks per currency, with the regulator's own 'Other' column",
    ),
    (
        "irrbb_sf_short_shock_bp",
        None,
        {
            "schema": "irrbb-sf-currency-bp-v1",
            "GHS": "500",
            "USD": "300",
            "EUR": "350",
            "GBP": "425",
            "CNY": "300",
            "OTHER": "500",
        },
        "bps",
        "pending",
        "BoG IRRBB Guideline (Exposure Draft, February 2026) Appendix III, Table 6: prescribed "
        "short-rate shocks per currency",
    ),
    (
        "irrbb_sf_long_shock_bp",
        None,
        {
            "schema": "irrbb-sf-currency-bp-v1",
            "GHS": "300",
            "USD": "225",
            "EUR": "200",
            "GBP": "250",
            "CNY": "150",
            "OTHER": "300",
        },
        "bps",
        "pending",
        "BoG IRRBB Guideline (Exposure Draft, February 2026) App III Table 6: long-rate shocks, "
        "used "
        "by the two rotations only. Reproduced AS PRINTED; three printed values differ from the "
        "Basel "
        "standard, which is open for the supervisor",
    ),
    (
        "irrbb_sf_short_decay_x",
        "4",
        None,
        "years",
        "pending",
        "BoG IRRBB Guideline (Exposure Draft, February 2026) Appendix III ¶2(i), footnote 23: the "
        "short-rate scalar decays as exp(-t/x)",
    ),
    (
        "irrbb_sf_rotation_coefficients",
        None,
        {
            "schema": "irrbb-sf-rotation-v1",
            "steepener": {"short": "-0.65", "long": "0.9"},
            "flattener": {"short": "0.8", "long": "-0.6"},
        },
        "multiplier",
        "pending",
        "BoG IRRBB Guideline (Exposure Draft, February 2026) Appendix III ¶2(iii): the steepener "
        "and "
        "flattener weights on the short and long shock components",
    ),
    (
        "irrbb_sf_cpr_multipliers",
        None,
        {
            "schema": "irrbb-sf-scenario-scalars-v1",
            "parallel_up": "0.8",
            "parallel_down": "1.2",
            "steepener": "0.8",
            "flattener": "1.2",
            "short_up": "0.8",
            "short_down": "1.2",
        },
        "multiplier",
        "pending",
        "BoG IRRBB Guideline (Exposure Draft, February 2026) Appendix I ¶27-28, Table 3: the "
        "scenario multipliers applied to the bank's own base prepayment rate",
    ),
    (
        "irrbb_sf_tdrr_scalars",
        None,
        {
            "schema": "irrbb-sf-scenario-scalars-v1",
            "parallel_up": "1.2",
            "parallel_down": "0.8",
            "steepener": "0.8",
            "flattener": "1.2",
            "short_up": "1.2",
            "short_down": "0.8",
        },
        "multiplier",
        "pending",
        "BoG IRRBB Guideline (Exposure Draft, February 2026) Appendix I ¶32-34, Table 4: the "
        "scenario scalars applied to the bank's own term-deposit redemption rate",
    ),
    (
        "irrbb_sf_nmd_caps",
        None,
        {
            "schema": "irrbb-sf-nmd-caps-v1",
            "retail_transactional": {"core_cap_pct": "90", "avg_maturity_cap_years": "5"},
            "retail_non_transactional": {"core_cap_pct": "70", "avg_maturity_cap_years": "4.5"},
            "wholesale": {"core_cap_pct": "50", "avg_maturity_cap_years": "4"},
        },
        "percent_years",
        "pending",
        "BoG IRRBB Guideline (Exposure Draft, February 2026) Appendix I ¶16-21, Table 2: the "
        "core-share and average repricing-maturity caps per deposit category",
    ),
    (
        "irrbb_sf_nmd_history_years",
        "10",
        None,
        "years",
        "pending",
        "BoG IRRBB Guideline (Exposure Draft, February 2026) Appendix I ¶18: the observation "
        "period "
        "a core-deposit estimate is expected to rest on. A shorter history is disclosed, never a "
        "refusal",
    ),
    (
        "irrbb_sf_major_currency_threshold_pct",
        "5",
        None,
        "percent",
        "pending",
        "BoG IRRBB Guideline (Exposure Draft, February 2026) Appendix I ¶37 and ¶56 footnote 16: a "
        "currency is material when it exceeds this share of banking-book assets or liabilities",
    ),
    (
        "irrbb_sf_outlier_scenario_set",
        None,
        {
            "schema": "icaap-code-list-v1",
            "codes": [
                "parallel_up",
                "parallel_down",
                "steepener",
                "flattener",
                "short_up",
                "short_down",
            ],
            "required": [],
        },
        "code_list",
        "pending",
        "BoG IRRBB Guideline (Exposure Draft, February 2026) ¶38 prints the outlier test over i in "
        "{1..6}; App II ¶1 mandates the two parallel shocks. Seeded as all six, the ¶38 reading, "
        "with the two-scenario measure reported alongside",
    ),
    (
        "irrbb_sf_mandatory_scenarios",
        None,
        {
            "schema": "icaap-code-list-v1",
            "codes": ["parallel_up", "parallel_down"],
            "required": ["parallel_up", "parallel_down"],
        },
        "code_list",
        "pending",
        "BoG IRRBB Guideline (Exposure Draft, February 2026) Appendix II ¶1: the parallel shocks "
        "are "
        "reported in every case",
    ),
    (
        "irrbb_sf_nii_horizon_months",
        "12",
        None,
        "months",
        "pending",
        "BoG IRRBB Guideline (Exposure Draft, February 2026) Appendix IV, Table 8 definitions "
        "(ii): "
        "the earnings horizon",
    ),
    (
        "irrbb_sf_cpr_time_scaling",
        None,
        {"schema": "irrbb-sf-cpr-scaling-v1", "mode": "annual_rate_scaled_to_bucket_width"},
        "method",
        "pending",
        "BoG IRRBB Guideline (Exposure Draft, February 2026) App I ¶29 does not settle whether the "
        "prepayment rate is annual and scaled to each bucket's width or applied per bucket as "
        "printed. The scaled reading is seeded and is switchable here",
    ),
    (
        "irrbb_sf_default_cash_flow_profile",
        None,
        {
            "schema": "irrbb-sf-cash-flow-profile-v1",
            "LOAN": {
                "amortisation": "annuity",
                "frequency_months": "1",
                "horizonless_bucket": "b13",
            },
            "SECURITY_HOLDING": {
                "amortisation": "bullet",
                "frequency_months": "6",
                "horizonless_bucket": "b13",
            },
            "INTERBANK_PLACEMENT": {
                "amortisation": "bullet",
                "frequency_months": "0",
                "horizonless_bucket": "b01",
            },
            "INTERBANK_BORROWING": {
                "amortisation": "bullet",
                "frequency_months": "0",
                "horizonless_bucket": "b01",
            },
            "DEPOSIT_TERM": {
                "amortisation": "bullet",
                "frequency_months": "0",
                "horizonless_bucket": "b01",
            },
            "OTHER_LIABILITY": {
                "amortisation": "bullet",
                "frequency_months": "0",
                "horizonless_bucket": "b01",
            },
            "CAPITAL_INSTRUMENT": {
                "amortisation": "bullet",
                "frequency_months": "6",
                "horizonless_bucket": "b13",
            },
            "INTEREST_RATE_SWAP": {"fixed_leg_frequency_months": "6"},
        },
        "profile",
        "pending",
        "AequorOS platform methodology (REPRESENTATIVE): default cash-flow profiles are applied "
        "ONLY "
        "where an ingested position carries no amortisation, payment frequency or repricing "
        "horizon; "
        "every application is tallied and disclosed",
    ),
    (
        "irrbb_sf_mandatory_from_as_of",
        None,
        {"schema": "icaap-effective-date-v1", "date": "2026-12-31"},
        "date",
        "pending",
        "BoG IRRBB Guideline (Exposure Draft, February 2026) ¶9 (effective 1 January 2027) and ¶60 "
        "(annual ICAAP). Which year-end it first bites on is INFERRED as the 31 December 2026 "
        "position, aligned with the first ICAAP report as-of",
    ),
    (
        "ga_confidence_q",
        "0.999",
        None,
        "ratio",
        "pending",
        "Gordy-Lutkebohmert (2013); the Basel IRB 99.9% solvency confidence level",
    ),
    (
        "ga_delta",
        "4.83",
        None,
        "multiplier",
        "pending",
        "Gordy-Lutkebohmert (2013) gamma-factor multiplier; derived as 4.833601 for an LGD "
        "variance "
        "coefficient of 0.25 at the 99.9% level and seeded rounded",
    ),
    (
        "ga_lgd_variance_gamma",
        "0.25",
        None,
        "ratio",
        "pending",
        "Gordy-Lutkebohmert (2013): VLGD = gamma x ELGD x (1 - ELGD)",
    ),
    (
        "ga_default_elgd_pct",
        "45",
        None,
        "percent",
        "pending",
        "Basel IRB foundation senior unsecured loss given default; the LAST fallback, used only "
        "where an exposure and its segment state none",
    ),
    (
        "ga_min_effective_names",
        "50",
        None,
        "count",
        "pending",
        "REPRESENTATIVE: AequorOS platform methodology: below this many EFFECTIVE names (1/HHI) "
        "the "
        "adjustment is declined rather than approximated. No published basis, and it decides "
        "whether "
        "the method engages at all, so every result says so",
    ),
    (
        "ga_asset_correlation",
        None,
        {
            "schema": "ga-asset-correlation-v1",
            "corporate": {"r_min": "0.12", "r_max": "0.24", "k": "50"},
            "retail_other": {"r_min": "0.03", "r_max": "0.16", "k": "35"},
        },
        "correlation",
        "pending",
        "Basel IRB CRE31.5 (corporate) and CRE31.14 (other retail): the asset correlation "
        "interpolates between r_min and r_max on exp(-k x PD)",
    ),
    (
        "ga_maturity_adjustment",
        None,
        {
            "schema": "ga-maturity-adjustment-v1",
            "apply": False,
            "b_intercept": "0.11852",
            "b_slope": "0.05478",
            "m_centre": "2.5",
            "m_scale": "1.5",
        },
        "method",
        "pending",
        "Basel IRB CRE31.7 maturity adjustment. Seeded OFF: with no maturity the capital function "
        "is "
        "the one-year form, which is the conservative reading until the exposure book carries "
        "reliable effective maturities",
    ),
    (
        "ga_proxy_pd_by_rw_code",
        None,
        {
            "schema": "ga-proxy-pd-v1",
            "RW20": "0.03",
            "RW35": "0.5",
            "RW50": "0.1",
            "RW75": "1",
            "RW100": "2",
            "RW150": "10",
        },
        "percent",
        "pending",
        "REPRESENTATIVE: AequorOS platform methodology: indicative long-run default rates by risk "
        "weight, used ONLY where an exposure states no PD. RW0 is excluded rather than proxied: a "
        "zero-weighted sovereign is the sovereign component's",
    ),
    (
        "ga_counterparty_segment_map",
        None,
        {
            "schema": "ga-segment-map-v1",
            "RETAIL_INDIVIDUAL": "retail_other",
            "SME": "corporate",
            "SME_MANAGED_AS_RETAIL": "retail_other",
            "CORPORATE": "corporate",
            "BANK_OECD": "corporate",
            "BANK_NON_OECD": "corporate",
            "NBFI": "corporate",
            "OTHER": "corporate",
            "UNSTATED": "corporate",
            "CENTRAL_BANK": "excluded",
            "SOVEREIGN": "excluded",
            "GOVERNMENT_ENTITY": "excluded",
            "MULTILATERAL_DEV_BANK": "excluded",
        },
        "mapping",
        "pending",
        "REPRESENTATIVE: AequorOS platform methodology: which correlation segment each canonical "
        "counterparty type takes. Sovereign-family types map to 'excluded' because they are "
        "measured "
        "by the sovereign component, not by name granularity",
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


def _swap(table: str, constraint: str, expression: str) -> None:
    with op.batch_alter_table(table) as batch_op:
        batch_op.drop_constraint(constraint, type_="check")
        batch_op.create_check_constraint(constraint, expression)


def _seed() -> None:
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
            "confirmation_status": confirmation_status,
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
        for code, value, value_json, unit, confirmation_status, citation in SEEDS
        if code not in governed
    ]
    if rows:
        op.bulk_insert(_seed_table(), rows)


def upgrade() -> None:
    _swap("regulatory_runs", "ck_regulatory_runs_module", _RUNS_NEW)
    _swap("regulatory_line_items", "ck_regulatory_line_items_section", _SECTIONS_NEW)
    _seed()


def downgrade() -> None:
    op.execute(
        sa.text(
            f"DELETE FROM {TABLE} WHERE param_code IN ({_codes_sql()}) "
            f"AND proposed_by = '{SEED_ACTOR}'"
        )
    )
    # The runs themselves are deleted first: a sealed ``irr_sf`` run would fail
    # the narrowed CHECK, and a downgrade that cannot complete is worse than
    # one that states what it removes. ``regulatory_runs`` is a FORCE-RLS
    # tenant table, so under the tenant-scoped alembic role the DELETE would
    # otherwise match zero rows and succeed silently.
    with force_rls_suspended(op.get_bind(), "regulatory_runs"):
        op.execute(sa.text("DELETE FROM regulatory_runs WHERE module = 'irr_sf'"))
    _swap("regulatory_line_items", "ck_regulatory_line_items_section", _SECTIONS_OLD)
    _swap("regulatory_runs", "ck_regulatory_runs_module", _RUNS_OLD)
