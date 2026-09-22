"""Catalogue dimensions: every attribute a BI query may group or filter by.

Each dimension binds to ONE mart column (a conformed dimension table or a fact
column), carries the authorization module and sensitivity the authorization
layer evaluates for it (D-028: a filter on a restricted member reveals data
and counts), and enumerates its vocabulary with production labels where the
vocabulary is closed — read from the domain modules that own it, never
restated.

Module assignment: conformed dimensions shared by every fact (time, branch,
product, position book) sit under Risk & Limits (``risk``); anything that
names or groups an obligor sits under Credit; deposit / liquidity attributes
under Liquidity; the repricing ladder under IRRBB.
"""

from __future__ import annotations

from app.domain.bi.catalogue.members import (
    ColumnRef,
    DimensionDef,
    EnumValue,
    Sensitivity,
    ValueType,
)
from app.domain.bi.extract import MATURITY_BUCKETS, PRODUCT_FAMILY_LABELS
from app.domain.capital.loan_classification import (
    BANK_GRADE_ORDER,
    BASIS_DAYS_PAST_DUE,
    BASIS_RESTRUCTURE_HOLD,
    BASIS_STAGE_PROXY,
    BASIS_UNCLASSIFIED,
)
from app.domain.credit.dpd_bands import DPD_BANDS
from app.domain.ingestion.constants import (
    COUNTERPARTY_TYPES,
    DEPOSIT_ACCOUNT_TYPES,
    GL_ACCOUNT_CLASSES,
    POSITION_TYPES,
    RATE_TYPES,
)
from app.domain.irr.buckets import REPRICING_BUCKETS

POSITION_TABLE = "bi_fact_position_daily"
EVENT_TABLE = "bi_fact_loan_event"
ENGINE_TABLE = "bi_fact_engine_metric"
BRANCH_TABLE = "bi_dim_branch"
PRODUCT_TABLE = "bi_dim_product"
COUNTERPARTY_TABLE = "bi_dim_counterparty"
GL_ACCOUNT_TABLE = "bi_dim_gl_account"
DATE_TABLE = "bi_dim_date"

RISK = "risk"
CREDIT = "credit"
LIQUIDITY = "liq"
IRRBB = "irrbb"

# --- closed vocabularies, labelled ---------------------------------------------------

POSITION_TYPE_LABELS: dict[str, str] = {
    "LOAN": "Loans",
    "DEPOSIT": "Deposits",
    "SECURITY_HOLDING": "Securities",
    "DERIVATIVE": "Derivatives",
    "FX_HEDGE": "FX hedges",
    "INTEREST_RATE_SWAP": "Interest-rate swaps",
    "CASH": "Cash and balances",
    "INTERBANK_PLACEMENT": "Interbank placements",
    "INTERBANK_BORROWING": "Interbank borrowings",
    "LC_GUARANTEE": "Letters of credit and guarantees",
    "COMMITMENT_UNDRAWN": "Undrawn commitments",
    "OTHER_ASSET": "Other assets",
    "OTHER_LIABILITY": "Other liabilities",
}
COUNTERPARTY_TYPE_LABELS: dict[str, str] = {
    "RETAIL_INDIVIDUAL": "Retail individuals",
    "SME": "Small and medium enterprises",
    "CORPORATE": "Corporates",
    "BANK_OECD": "Banks (OECD)",
    "BANK_NON_OECD": "Banks (non-OECD)",
    "CENTRAL_BANK": "Central bank",
    "SOVEREIGN": "Sovereign",
    "GOVERNMENT_ENTITY": "Government entities",
    "MULTILATERAL_DEV_BANK": "Multilateral development banks",
    "NBFI": "Non-bank financial institutions",
    "OTHER": "Other",
}
DEPOSIT_ACCOUNT_TYPE_LABELS: dict[str, str] = {
    "CURRENT": "Current accounts",
    "CALL": "Call accounts",
    "SAVINGS": "Savings accounts",
    "FIXED": "Fixed deposits",
    "OTHER": "Other deposits",
}
GL_ACCOUNT_CLASS_LABELS: dict[str, str] = {
    "ASSET": "Assets",
    "LIABILITY": "Liabilities",
    "EQUITY": "Equity",
    "INCOME": "Income",
    "EXPENSE": "Expenses",
    "OFF_BALANCE": "Off balance sheet",
}
RATE_TYPE_LABELS: dict[str, str] = {"FIXED": "Fixed rate", "FLOATING": "Floating rate"}
GRADE_LABELS: dict[str, str] = {
    "standard": "Standard",
    "olem": "Other loans especially mentioned",
    "substandard": "Substandard",
    "doubtful": "Doubtful",
    "loss": "Loss",
}
BASIS_LABELS: dict[str, str] = {
    BASIS_DAYS_PAST_DUE: "Days past due",
    BASIS_STAGE_PROXY: "IFRS 9 stage proxy",
    BASIS_RESTRUCTURE_HOLD: "Restructure hold",
    BASIS_UNCLASSIFIED: "Unclassified",
}
IFRS9_STAGES: tuple[EnumValue, ...] = (
    EnumValue("1", "Stage 1"),
    EnumValue("2", "Stage 2"),
    EnumValue("3", "Stage 3"),
)
REPRICING_BUCKET_LABELS: dict[str, str] = {
    "overnight": "Overnight",
    "1-7d": "1–7 days",
    "8-30d": "8–30 days",
    "1-3m": "1–3 months",
    "3-6m": "3–6 months",
    "6-12m": "6–12 months",
    "1-3y": "1–3 years",
    "3-5y": "3–5 years",
    "5y+": "Over 5 years",
}
EVENT_TYPE_LABELS: dict[str, str] = {
    "DISBURSEMENT": "Disbursements",
    "REPAYMENT": "Repayments",
    "WRITE_OFF": "Write-offs",
    "RECOVERY": "Recoveries",
    "RESTRUCTURE": "Restructures",
}
ATTRIBUTION_LABELS: dict[str, str] = {
    "snapshot_on_or_before": "Attributed to the facility's snapshot",
    "no_snapshot": "Facility known, no snapshot by the event date",
    "unmatched": "No facility matched",
}
ENGINE_STATUS_LABELS: dict[str, str] = {
    "green": "Within limits",
    "amber": "Watch",
    "red": "Breach",
    "na": "Not assessed",
}
PIPELINE_STATE_LABELS: dict[str, str] = {
    "ready": "Ready",
    "blocked": "Blocked by reconciliation",
    "failed": "Failed",
}
TIER_LABELS: dict[str, str] = {"official": "Official", "live": "Live"}
DESIGNATION_LABELS: dict[str, str] = {
    "filed": "Filed",
    "supervisory_monitoring": "Supervisory monitoring",
    "advisory_only": "Advisory",
    "unregistered": "Unregistered",
}
BOOLEAN_VALUES: tuple[EnumValue, ...] = (EnumValue("true", "Yes"), EnumValue("false", "No"))


def _values(codes: tuple[str, ...], labels: dict[str, str]) -> tuple[EnumValue, ...]:
    return tuple(EnumValue(code, labels[code]) for code in codes)


def _dim(  # noqa: PLR0913 - one keyword per declared member attribute
    id: str,
    label: str,
    table: str,
    column: str,
    *,
    module: str,
    sensitivity: Sensitivity = "aggregated",
    value_type: ValueType = "text",
    values: tuple[EnumValue, ...] = (),
    description: str = "",
) -> DimensionDef:
    return DimensionDef(
        id=id,
        module=module,
        sensitivity=sensitivity,
        label=label,
        source=ColumnRef(table, column),
        description=description,
        value_type=value_type,
        values=values,
    )


def _time_dimensions() -> tuple[DimensionDef, ...]:
    return (
        _dim("time.date", "Date", DATE_TABLE, "date", module=RISK, value_type="date"),
        _dim(
            "time.calendar_month",
            "Calendar month",
            DATE_TABLE,
            "calendar_month",
            module=RISK,
            value_type="date",
        ),
        _dim(
            "time.calendar_quarter", "Calendar quarter", DATE_TABLE, "calendar_quarter", module=RISK
        ),
        _dim("time.calendar_year", "Calendar year", DATE_TABLE, "calendar_year", module=RISK),
        _dim("time.fiscal_year", "Fiscal year", DATE_TABLE, "fiscal_year", module=RISK),
        _dim("time.fiscal_quarter", "Fiscal quarter", DATE_TABLE, "fiscal_quarter", module=RISK),
        _dim(
            "time.is_last_in_month",
            "Month-end position",
            DATE_TABLE,
            "is_last_in_month",
            module=RISK,
            value_type="flag",
            values=BOOLEAN_VALUES,
            description="The last date with data in its calendar month (D-014).",
        ),
    )


def _branch_dimensions() -> tuple[DimensionDef, ...]:
    return (
        _dim("branch.code", "Branch code", BRANCH_TABLE, "branch_code", module=RISK),
        _dim("branch.name", "Branch", BRANCH_TABLE, "name", module=RISK),
        _dim(
            "branch.region",
            "Region",
            BRANCH_TABLE,
            "region",
            module=RISK,
            description="Declared in the bank's business-unit register; never inferred (D-020).",
        ),
        _dim("branch.outlet_type", "Outlet type", BRANCH_TABLE, "outlet_type", module=RISK),
        _dim("branch.status", "Branch status", BRANCH_TABLE, "status", module=RISK),
        _dim(
            "branch.mapped",
            "Branch mapped",
            BRANCH_TABLE,
            "mapped",
            module=RISK,
            value_type="flag",
            values=BOOLEAN_VALUES,
        ),
    )


def _product_dimensions() -> tuple[DimensionDef, ...]:
    family_values = tuple(EnumValue(code, label) for code, label in PRODUCT_FAMILY_LABELS.items())
    return (
        _dim("product.code", "Product code", PRODUCT_TABLE, "product_code", module=RISK),
        _dim("product.name", "Product", PRODUCT_TABLE, "name", module=RISK),
        _dim(
            "product.family",
            "Product family",
            PRODUCT_TABLE,
            "product_family",
            module=RISK,
            values=family_values,
        ),
        _dim(
            "product.regulatory_category",
            "Regulatory category",
            PRODUCT_TABLE,
            "regulatory_category",
            module=RISK,
        ),
        _dim(
            "product.risk_weight_code",
            "Risk weight code",
            PRODUCT_TABLE,
            "risk_weight_code",
            module=RISK,
        ),
    )


def _position_dimensions() -> tuple[DimensionDef, ...]:
    return (
        _dim(
            "position.type",
            "Position type",
            POSITION_TABLE,
            "position_type",
            module=RISK,
            values=_values(POSITION_TYPES, POSITION_TYPE_LABELS),
        ),
        _dim("position.currency", "Currency", POSITION_TABLE, "currency", module=RISK),
        _dim(
            "position.source_system", "Source system", POSITION_TABLE, "source_system", module=RISK
        ),
        _dim(
            "position.id",
            "Position",
            POSITION_TABLE,
            "position_id",
            module=RISK,
            sensitivity="confidential",
            description="Record-level: identifies one facility or account.",
        ),
        _dim(
            "position.source_reference",
            "Account reference",
            POSITION_TABLE,
            "source_reference",
            module=RISK,
            sensitivity="confidential",
            description="Record-level: the source system's own account or facility reference.",
        ),
        _dim(
            "position.fx_unconverted",
            "Unconverted foreign currency",
            POSITION_TABLE,
            "fx_unconverted",
            module=RISK,
            value_type="flag",
            values=BOOLEAN_VALUES,
        ),
        _dim(
            "position.rate_type",
            "Rate type",
            POSITION_TABLE,
            "rate_type",
            module=RISK,
            values=_values(RATE_TYPES, RATE_TYPE_LABELS),
        ),
        _dim("position.rate_index", "Rate index", POSITION_TABLE, "rate_index", module=RISK),
        _dim(
            "position.exposure_category",
            "Exposure category",
            POSITION_TABLE,
            "exposure_category",
            module=CREDIT,
            description="The capital exposure class; open because unrecognised ones are named.",
        ),
        _dim(
            "position.encumbered",
            "Encumbered",
            POSITION_TABLE,
            "encumbered",
            module=LIQUIDITY,
            value_type="flag",
            values=BOOLEAN_VALUES,
        ),
        _dim("position.hqla_level", "HQLA level", POSITION_TABLE, "hqla_level", module=LIQUIDITY),
        _dim(
            "position.deposit_account_type",
            "Deposit account type",
            POSITION_TABLE,
            "deposit_account_type",
            module=LIQUIDITY,
            values=_values(DEPOSIT_ACCOUNT_TYPES, DEPOSIT_ACCOUNT_TYPE_LABELS),
        ),
        _dim(
            "position.maturity_bucket",
            "Contractual maturity bucket",
            POSITION_TABLE,
            "maturity_bucket",
            module=LIQUIDITY,
            values=tuple(EnumValue(bucket.code, bucket.label) for bucket in MATURITY_BUCKETS),
        ),
        _dim(
            "position.repricing_bucket",
            "Repricing bucket",
            POSITION_TABLE,
            "repricing_bucket",
            module=IRRBB,
            values=tuple(
                EnumValue(name, REPRICING_BUCKET_LABELS[name]) for name, _, _ in REPRICING_BUCKETS
            ),
        ),
    )


def _loan_dimensions() -> tuple[DimensionDef, ...]:
    return (
        _dim(
            "loan.grade",
            "Classification grade",
            POSITION_TABLE,
            "grade",
            module=CREDIT,
            values=_values(BANK_GRADE_ORDER, GRADE_LABELS),
        ),
        _dim(
            "loan.non_performing",
            "Non-performing",
            POSITION_TABLE,
            "non_performing",
            module=CREDIT,
            value_type="flag",
            values=BOOLEAN_VALUES,
        ),
        _dim(
            "loan.classification_basis",
            "Classification basis",
            POSITION_TABLE,
            "classification_basis",
            module=CREDIT,
            values=tuple(EnumValue(code, label) for code, label in BASIS_LABELS.items()),
        ),
        _dim(
            "loan.dpd_band",
            "Days past due band",
            POSITION_TABLE,
            "dpd_band",
            module=CREDIT,
            values=tuple(EnumValue(band.code, band.label) for band in DPD_BANDS),
        ),
        _dim(
            "loan.ifrs9_stage",
            "IFRS 9 stage",
            POSITION_TABLE,
            "ifrs9_stage",
            module=CREDIT,
            values=IFRS9_STAGES,
        ),
        _dim(
            "loan.vintage_month",
            "Origination month",
            POSITION_TABLE,
            "vintage_month",
            module=CREDIT,
            value_type="date",
        ),
        _dim(
            "loan.restructured",
            "Restructured",
            POSITION_TABLE,
            "restructured",
            module=CREDIT,
            value_type="flag",
            values=BOOLEAN_VALUES,
        ),
        _dim(
            "loan.collateral_type",
            "Collateral type",
            POSITION_TABLE,
            "collateral_type",
            module=CREDIT,
        ),
        _dim("loan.sector", "Sector", POSITION_TABLE, "sector", module=CREDIT),
        _dim(
            "loan.employer",
            "Employer",
            POSITION_TABLE,
            "employer",
            module=CREDIT,
            sensitivity="confidential",
            description="Groups payroll-linked facilities by the borrower's employer.",
        ),
    )


def _counterparty_dimensions() -> tuple[DimensionDef, ...]:
    return (
        _dim(
            "counterparty.id",
            "Counterparty",
            COUNTERPARTY_TABLE,
            "counterparty_id",
            module=CREDIT,
            sensitivity="restricted",
            description="Identifies a single obligor.",
        ),
        _dim(
            "counterparty.name",
            "Counterparty name",
            COUNTERPARTY_TABLE,
            "name",
            module=CREDIT,
            sensitivity="restricted",
            description="Names a single obligor.",
        ),
        _dim(
            "counterparty.source_reference",
            "Counterparty reference",
            COUNTERPARTY_TABLE,
            "source_reference",
            module=CREDIT,
            sensitivity="restricted",
        ),
        _dim(
            "counterparty.type",
            "Counterparty type",
            COUNTERPARTY_TABLE,
            "counterparty_type",
            module=CREDIT,
            values=_values(COUNTERPARTY_TYPES, COUNTERPARTY_TYPE_LABELS),
        ),
        _dim(
            "counterparty.group",
            "Counterparty group",
            COUNTERPARTY_TABLE,
            "group_reference",
            module=CREDIT,
            sensitivity="restricted",
            description="A related-party group names its members; treated as obligor identity.",
        ),
        _dim(
            "counterparty.country",
            "Counterparty country",
            COUNTERPARTY_TABLE,
            "country_code",
            module=CREDIT,
        ),
        _dim("counterparty.rating", "External rating", COUNTERPARTY_TABLE, "rating", module=CREDIT),
    )


def _gl_account_dimensions() -> tuple[DimensionDef, ...]:
    return (
        _dim("gl_account.code", "GL account code", GL_ACCOUNT_TABLE, "account_code", module=RISK),
        _dim("gl_account.name", "GL account", GL_ACCOUNT_TABLE, "name", module=RISK),
        _dim(
            "gl_account.class",
            "GL account class",
            GL_ACCOUNT_TABLE,
            "account_class",
            module=RISK,
            values=_values(GL_ACCOUNT_CLASSES, GL_ACCOUNT_CLASS_LABELS),
        ),
        _dim(
            "gl_account.parent",
            "Parent GL account",
            GL_ACCOUNT_TABLE,
            "parent_account_code",
            module=RISK,
        ),
        _dim(
            "gl_account.pl_line", "Profit and loss line", GL_ACCOUNT_TABLE, "pl_line", module=RISK
        ),
    )


def _event_dimensions() -> tuple[DimensionDef, ...]:
    return (
        _dim(
            "event.type",
            "Loan event type",
            EVENT_TABLE,
            "event_type",
            module=CREDIT,
            values=tuple(EnumValue(code, label) for code, label in EVENT_TYPE_LABELS.items()),
        ),
        _dim("event.subtype", "Loan event subtype", EVENT_TABLE, "event_subtype", module=CREDIT),
        _dim(
            "event.date", "Event date", EVENT_TABLE, "event_date", module=CREDIT, value_type="date"
        ),
        _dim("event.currency", "Event currency", EVENT_TABLE, "currency", module=CREDIT),
        _dim(
            "event.attribution_basis",
            "Attribution",
            EVENT_TABLE,
            "attribution_basis",
            module=CREDIT,
            values=tuple(EnumValue(code, label) for code, label in ATTRIBUTION_LABELS.items()),
            description="How the event was tied to a facility (D-018); never a cross-system guess.",
        ),
        _dim(
            "event.fx_unconverted",
            "Unconverted foreign currency",
            EVENT_TABLE,
            "fx_unconverted",
            module=CREDIT,
            value_type="flag",
            values=BOOLEAN_VALUES,
        ),
        _dim(
            "event.product_family", "Product family", EVENT_TABLE, "product_family", module=CREDIT
        ),
        _dim("event.sector", "Sector", EVENT_TABLE, "sector", module=CREDIT),
        _dim(
            "event.position_id",
            "Facility",
            EVENT_TABLE,
            "position_id",
            module=CREDIT,
            sensitivity="confidential",
            description="Record-level: the facility the event was attributed to.",
        ),
    )


def _engine_dimensions() -> tuple[DimensionDef, ...]:
    return (
        _dim(
            "engine.status",
            "Engine status",
            ENGINE_TABLE,
            "status",
            module=RISK,
            values=tuple(EnumValue(code, label) for code, label in ENGINE_STATUS_LABELS.items()),
        ),
        _dim(
            "engine.pipeline_state",
            "Pipeline state",
            ENGINE_TABLE,
            "pipeline_state",
            module=RISK,
            values=tuple(EnumValue(code, label) for code, label in PIPELINE_STATE_LABELS.items()),
        ),
        _dim(
            "engine.reconciliation_blocked",
            "Reconciliation blocked",
            ENGINE_TABLE,
            "reconciliation_blocked",
            module=RISK,
            value_type="flag",
            values=BOOLEAN_VALUES,
        ),
        _dim(
            "engine.tier",
            "Computation tier",
            ENGINE_TABLE,
            "tier",
            module=RISK,
            values=tuple(EnumValue(code, label) for code, label in TIER_LABELS.items()),
        ),
        _dim(
            "engine.advisory_designation",
            "Designation",
            ENGINE_TABLE,
            "advisory_designation",
            module=RISK,
            values=tuple(EnumValue(code, label) for code, label in DESIGNATION_LABELS.items()),
        ),
        _dim("engine.regime", "Regime", ENGINE_TABLE, "regime", module=RISK),
        _dim("engine.module", "Engine module", ENGINE_TABLE, "module", module=RISK),
    )


def dimensions() -> tuple[DimensionDef, ...]:
    """Every dimension, in catalogue order."""
    return (
        *_time_dimensions(),
        *_branch_dimensions(),
        *_product_dimensions(),
        *_position_dimensions(),
        *_loan_dimensions(),
        *_counterparty_dimensions(),
        *_gl_account_dimensions(),
        *_event_dimensions(),
        *_engine_dimensions(),
    )


#: Dimension ids that slice the position facts (conformed + fact-local).
POSITION_DIMENSION_IDS: tuple[str, ...] = tuple(
    dim.id
    for dim in (
        *_time_dimensions(),
        *_branch_dimensions(),
        *_product_dimensions(),
        *_position_dimensions(),
        *_loan_dimensions(),
        *_counterparty_dimensions(),
        *_gl_account_dimensions(),
    )
)
#: Dimension ids that slice the loan-event facts.
EVENT_DIMENSION_IDS: tuple[str, ...] = tuple(
    dim.id
    for dim in (
        *_time_dimensions(),
        *_branch_dimensions(),
        *_product_dimensions(),
        *_counterparty_dimensions(),
        *_event_dimensions(),
    )
)
