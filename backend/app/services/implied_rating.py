"""Snapshot-driven implied bank-rating and PD calculation service.

The service joins governed financial facts, completed regulatory calculations,
and tenant-published sovereign market data at one reporting date. It never
reads a dashboard projection or a mutable current value after snapshotting.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.domain.rating.engine import (
    ComponentDefinition,
    RatingInputs,
    RatingMethodology,
    RatioDefinition,
    compute_rating,
    ddep_stress,
)
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CanonicalCounterpartyRating,
    CanonicalMarketIndex,
    DeskMethodology,
    ImpliedRatingRun,
    Jurisdiction,
    LiveMetric,
    RegulatoryRun,
)
from app.services.audit import record_event
from app.services.live_types import LiveFindingSpec, LiveModuleResult

METHODOLOGY_CODE = "AEQ-GHS-BANK-PD"
ENGINE_VERSION = "rating-scorecard/1.0"
_BOOTSTRAP_PROPOSER = "rating-bootstrap@aequoros.system"
GRADE_ORDER = (
    "aaa",
    "aa+",
    "aa",
    "aa-",
    "a+",
    "a",
    "a-",
    "bbb+",
    "bbb",
    "bbb-",
    "bb+",
    "bb",
    "bb-",
    "b+",
    "b",
    "b-",
    "ccc+",
    "ccc",
    "ccc-",
    "cc",
    "c",
)
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_ASSET_CATEGORIES = frozenset(
    {
        "cash_vault",
        "bog_required_reserves",
        "bog_excess_reserves",
        "securities_bog_bills",
        "securities_gog_bonds",
        "loans_gross",
        "other_assets",
    }
)
_LIQUID_ASSET_CATEGORIES = frozenset(
    {"cash_vault", "bog_required_reserves", "bog_excess_reserves", "securities_bog_bills"}
)
_SOVEREIGN_ASSET_CATEGORIES = frozenset({"securities_bog_bills", "securities_gog_bonds"})
_DEPOSIT_PREFIXES = ("retail_deposits", "wholesale_")
_LIVE_DEPENDENCIES = ("capital", "liquidity", "irr", "fx")
_LIVE_PD_AMBER_PCT = Decimal("5")
_LIVE_PD_RED_PCT = Decimal("15")
_MOODYS_GRADES = {
    "aaa": "aaa",
    "aa1": "aa+",
    "aa2": "aa",
    "aa3": "aa-",
    "a1": "a+",
    "a2": "a",
    "a3": "a-",
    "baa1": "bbb+",
    "baa2": "bbb",
    "baa3": "bbb-",
    "ba1": "bb+",
    "ba2": "bb",
    "ba3": "bb-",
    "b1": "b+",
    "b2": "b",
    "b3": "b-",
    "caa1": "ccc+",
    "caa2": "ccc",
    "caa3": "ccc-",
    "ca": "cc",
    "c": "c",
}

DEFAULT_PARAMETERS_V1: dict[str, Any] = {
    "parameter_status": "initial expert-anchored template; active automatically",
    "components": [
        {"code": "capitalisation", "weight": "0.25"},
        {"code": "asset_quality", "weight": "0.20"},
        {"code": "funding_liquidity", "weight": "0.20"},
        {"code": "business_profile", "weight": "0.20"},
        {"code": "earnings", "weight": "0.15"},
        {"code": "risk_profile", "weight": "0.10"},
    ],
    "ratios": [
        {
            "code": "cet1_pct",
            "component": "capitalisation",
            "weight": "0.50",
            "direction": "higher_is_better",
            "floor": "6.5",
            "cap": "20",
        },
        {
            "code": "car_pct",
            "component": "capitalisation",
            "weight": "0.30",
            "direction": "higher_is_better",
            "floor": "10",
            "cap": "24",
        },
        {
            "code": "leverage_pct",
            "component": "capitalisation",
            "weight": "0.20",
            "direction": "higher_is_better",
            "floor": "3",
            "cap": "12",
        },
        {
            "code": "npl_pct",
            "component": "asset_quality",
            "weight": "0.45",
            "direction": "lower_is_better",
            "floor": "2",
            "cap": "25",
            "transform": "logistic",
            "steepness": "0.25",
            "midpoint": "8",
        },
        {
            "code": "provision_coverage_pct",
            "component": "asset_quality",
            "weight": "0.30",
            "direction": "higher_is_better",
            "floor": "20",
            "cap": "150",
        },
        {
            "code": "stage3_pct",
            "component": "asset_quality",
            "weight": "0.25",
            "direction": "lower_is_better",
            "floor": "1",
            "cap": "25",
        },
        {
            "code": "lcr_pct",
            "component": "funding_liquidity",
            "weight": "0.25",
            "direction": "higher_is_better",
            "floor": "100",
            "cap": "200",
        },
        {
            "code": "nsfr_pct",
            "component": "funding_liquidity",
            "weight": "0.20",
            "direction": "higher_is_better",
            "floor": "100",
            "cap": "150",
        },
        {
            "code": "loan_to_deposit_pct",
            "component": "funding_liquidity",
            "weight": "0.20",
            "direction": "lower_is_better",
            "floor": "50",
            "cap": "130",
        },
        {
            "code": "liquid_assets_to_assets_pct",
            "component": "funding_liquidity",
            "weight": "0.15",
            "direction": "higher_is_better",
            "floor": "5",
            "cap": "50",
        },
        {
            "code": "cashflow_coverage_pct",
            "component": "funding_liquidity",
            "weight": "0.20",
            "direction": "higher_is_better",
            "floor": "75",
            "cap": "125",
        },
        {
            "code": "gross_income_to_assets_pct",
            "component": "business_profile",
            "weight": "0.60",
            "direction": "higher_is_better",
            "floor": "1",
            "cap": "15",
        },
        {
            "code": "income_growth_pct",
            "component": "business_profile",
            "weight": "0.40",
            "direction": "higher_is_better",
            "floor": "-10",
            "cap": "20",
        },
        {
            "code": "roa_pct",
            "component": "earnings",
            "weight": "0.50",
            "direction": "higher_is_better",
            "floor": "0",
            "cap": "3",
        },
        {
            "code": "net_interest_margin_pct",
            "component": "earnings",
            "weight": "0.25",
            "direction": "higher_is_better",
            "floor": "1",
            "cap": "10",
        },
        {
            "code": "cost_to_income_pct",
            "component": "earnings",
            "weight": "0.25",
            "direction": "lower_is_better",
            "floor": "30",
            "cap": "90",
        },
        {
            "code": "eve_sensitivity_pct",
            "component": "risk_profile",
            "weight": "0.55",
            "direction": "lower_is_better",
            "floor": "5",
            "cap": "35",
        },
        {
            "code": "fx_nop_pct",
            "component": "risk_profile",
            "weight": "0.45",
            "direction": "lower_is_better",
            "floor": "5",
            "cap": "30",
        },
    ],
    "grade_cutpoints": {
        "aaa": "0.95",
        "aa+": "0.91",
        "aa": "0.87",
        "aa-": "0.83",
        "a+": "0.79",
        "a": "0.75",
        "a-": "0.71",
        "bbb+": "0.67",
        "bbb": "0.63",
        "bbb-": "0.59",
        "bb+": "0.55",
        "bb": "0.51",
        "bb-": "0.47",
        "b+": "0.43",
        "b": "0.39",
        "b-": "0.35",
        "ccc+": "0.30",
        "ccc": "0.25",
        "ccc-": "0.20",
        "cc": "0.12",
        "c": "0",
    },
    "ttc_grade_pd_anchors_pct": {
        "aaa": "0.01",
        "aa+": "0.02",
        "aa": "0.03",
        "aa-": "0.04",
        "a+": "0.05",
        "a": "0.07",
        "a-": "0.10",
        "bbb+": "0.15",
        "bbb": "0.22",
        "bbb-": "0.32",
        "bb+": "0.50",
        "bb": "0.75",
        "bb-": "1.10",
        "b+": "1.70",
        "b": "2.60",
        "b-": "4.00",
        "ccc+": "7.00",
        "ccc": "12.00",
        "ccc-": "20.00",
        "cc": "35.00",
        "c": "100.00",
    },
    "pit_grade_pd_anchors_pct": {
        "aaa": "0.02",
        "aa+": "0.03",
        "aa": "0.05",
        "aa-": "0.06",
        "a+": "0.08",
        "a": "0.11",
        "a-": "0.15",
        "bbb+": "0.23",
        "bbb": "0.33",
        "bbb-": "0.48",
        "bb+": "0.75",
        "bb": "1.13",
        "bb-": "1.65",
        "b+": "2.55",
        "b": "3.90",
        "b-": "6.00",
        "ccc+": "10.50",
        "ccc": "18.00",
        "ccc-": "30.00",
        "cc": "52.50",
        "c": "100.00",
    },
    "reference_grade_obligors": {grade: 800 for grade in GRADE_ORDER},
    "reference_grade_defaults": {grade: 0 for grade in GRADE_ORDER},
    "confidence_level": "0.90",
    "moc_k_sigma": "1.0",
    "bayesian_prior_alpha": "1.0",
    "bayesian_prior_beta": "99.0",
    "operating_environment_index_code": "GHANA_OPERATING_ENVIRONMENT_SCORE",
    "operating_environment_fallback_score": "0.55",
    "operating_environment_matrix": [["0", "0"], ["0", "1"]],
    "ddep_haircut_pct": "30.0",
}


@dataclass(frozen=True)
class _RatingSources:
    facts: list[BankFinancialFact]
    capital: dict[str, Any]
    liquidity: dict[str, Any]
    irr: dict[str, Any]
    fx: dict[str, Any]
    operating_environment_score: Decimal
    sovereign_ceiling: str
    parameters: dict[str, Any]
    support_uplift_notches: int


def ensure_default_methodology(db: Session) -> DeskMethodology:
    """Create the automatically active initial scorecard parameter version."""
    existing = db.scalar(
        select(DeskMethodology)
        .where(DeskMethodology.methodology_code == METHODOLOGY_CODE)
        .order_by(DeskMethodology.version)
        .limit(1)
    )
    if existing is not None:
        return existing
    row = DeskMethodology(
        methodology_code=METHODOLOGY_CODE,
        version=1,
        status="approved",
        parameters=DEFAULT_PARAMETERS_V1,
        change_rationale=(
            "Initial transparent CAMELS/Fitch-weighted Ghana bank scorecard with "
            "Pluto-Tasche/Bayesian low-default PD bands, Basel floor, and DDEP stress."
        ),
        proposed_by=_BOOTSTRAP_PROPOSER,
        approved_by="system:auto",
        approved_at=utc_now(),
        effective_from=date(2000, 1, 1),
    )
    db.add(row)
    db.flush()
    return row


def _decimal(value: Any, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 - configuration is external JSON
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Rating methodology parameter {label!r} must be numeric.",
        ) from exc


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(snapshot).encode("utf-8")).hexdigest()


def _missing(label: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"Rating run requires {label}; calculate or publish it before retrying.",
    )


def _normalize_grade(value: str) -> str:
    normalized = value.strip().lower().replace("−", "-").replace(" ", "")
    normalized = _MOODYS_GRADES.get(normalized, normalized)
    if normalized not in GRADE_ORDER:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Sovereign rating {value!r} cannot be mapped to the AequorOS master scale.",
        )
    return normalized


def _ratio(value: Decimal, denominator: Decimal, label: str) -> Decimal:
    if denominator <= _ZERO:
        raise _missing(f"a positive denominator for {label}")
    return value / denominator * _HUNDRED


def _fact_values(facts: list[BankFinancialFact]) -> dict[tuple[str, str], Decimal]:
    return {(fact.fact_group, fact.category): Decimal(str(fact.amount)) for fact in facts}


def _sum_group(
    values: dict[tuple[str, str], Decimal], group: str, categories: frozenset[str]
) -> Decimal:
    return sum((values.get((group, category), _ZERO) for category in categories), _ZERO)


def _latest_income_value(values: dict[tuple[str, str], Decimal], metric: str) -> Decimal:
    prefix = f"{metric}_"
    matches = [
        (category.removeprefix(prefix), amount)
        for (group, category), amount in values.items()
        if group == "operational_income" and category.startswith(prefix)
    ]
    if not matches:
        raise _missing(f"canonical income-statement metric {metric}")
    return max(matches, key=lambda item: item[0])[1]


def _income_growth_pct(values: dict[tuple[str, str], Decimal]) -> Decimal:
    prefix = "gross_income_"
    history = sorted(
        (
            (category.removeprefix(prefix), amount)
            for (group, category), amount in values.items()
            if group == "operational_income" and category.startswith(prefix)
        ),
        key=lambda item: item[0],
    )
    if len(history) < 2:
        raise _missing("two annual canonical gross-income observations")
    previous, latest = history[-2][1], history[-1][1]
    return _ratio(latest - previous, previous, "gross-income growth")


def _latest_succeeded_metrics(
    db: Session, organization_id: str, bank_id: str, period_id: UUID, module: str
) -> dict[str, Any]:
    run = db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == organization_id,
            RegulatoryRun.bank_id == bank_id,
            RegulatoryRun.reporting_period_id == period_id,
            RegulatoryRun.module == module,
            RegulatoryRun.scenario_code == "baseline",
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.completed_at.desc(), RegulatoryRun.created_at.desc())
        .limit(1)
    )
    if run is None:
        raise _missing(f"a successful baseline {module} run for the reporting period")
    return dict(run.metrics)


def _get_metric(metrics: dict[str, Any], code: str, module: str) -> Decimal:
    value = metrics.get(code)
    if value is None:
        raise _missing(f"{module} metric {code}")
    return _decimal(value, f"{module}.{code}")


def _current_sovereign(
    db: Session, organization_id: str, bank_id: str, as_of: date
) -> dict[str, str]:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    jurisdiction = db.get(Jurisdiction, bank.jurisdiction_code)
    issuer = jurisdiction.sovereign_rating_issuer if jurisdiction is not None else None
    if not issuer:
        raise _missing(
            f"a sovereign_rating_issuer in the {bank.jurisdiction_code} jurisdiction registry"
        )
    row = db.scalar(
        select(CanonicalCounterpartyRating)
        .where(
            CanonicalCounterpartyRating.organization_id == organization_id,
            CanonicalCounterpartyRating.bank_id == bank_id,
            CanonicalCounterpartyRating.issuer == issuer,
            CanonicalCounterpartyRating.as_of_date <= as_of,
            CanonicalCounterpartyRating.superseded_by.is_(None),
            CanonicalCounterpartyRating.validation_status.in_(("accepted", "warning")),
        )
        .order_by(
            CanonicalCounterpartyRating.as_of_date.desc(),
            CanonicalCounterpartyRating.ingested_at.desc(),
        )
        .limit(1)
    )
    if row is None:
        raise _missing(f"a published {issuer} credit rating")
    return {
        "issuer": row.issuer,
        "agency": row.agency,
        "rating": row.rating,
        "rating_date": row.rating_date.isoformat(),
        "as_of_date": row.as_of_date.isoformat(),
    }


def _operating_environment(
    db: Session,
    organization_id: str,
    bank_id: str,
    as_of: date,
    parameters: dict[str, Any],
) -> tuple[Decimal, dict[str, str]]:
    index_code = str(parameters.get("operating_environment_index_code", ""))
    if index_code:
        row = db.scalar(
            select(CanonicalMarketIndex)
            .where(
                CanonicalMarketIndex.organization_id == organization_id,
                CanonicalMarketIndex.bank_id == bank_id,
                CanonicalMarketIndex.index_code == index_code,
                CanonicalMarketIndex.scenario == "base",
                CanonicalMarketIndex.as_of_date <= as_of,
                CanonicalMarketIndex.superseded_by.is_(None),
                CanonicalMarketIndex.validation_status.in_(("accepted", "warning")),
            )
            .order_by(
                CanonicalMarketIndex.as_of_date.desc(),
                CanonicalMarketIndex.ingested_at.desc(),
            )
            .limit(1)
        )
        if row is not None:
            value = _decimal(row.value, index_code)
            if not _ZERO <= value <= Decimal("1"):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Operating-environment index {index_code!r} must be in [0, 1].",
                )
            return value, {
                "source": "market_index",
                "index_code": index_code,
                "as_of": row.as_of_date.isoformat(),
            }
    fallback = parameters.get("operating_environment_fallback_score")
    if fallback is None:
        raise _missing("an operating-environment market index or methodology fallback")
    value = _decimal(fallback, "operating_environment_fallback_score")
    if not _ZERO <= value <= Decimal("1"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Operating-environment fallback score must be in [0, 1].",
        )
    return value, {"source": "methodology_fallback"}


def _definition(raw: dict[str, Any]) -> RatioDefinition:
    return RatioDefinition(
        code=str(raw["code"]),
        component=str(raw["component"]),
        weight=_decimal(raw["weight"], f"ratio {raw['code']} weight"),
        direction=str(raw["direction"]),
        floor=_decimal(raw["floor"], f"ratio {raw['code']} floor"),
        cap=_decimal(raw["cap"], f"ratio {raw['code']} cap"),
        transform=str(raw.get("transform", "piecewise_linear")),
        steepness=(
            _decimal(raw["steepness"], f"ratio {raw['code']} steepness")
            if raw.get("steepness") is not None
            else None
        ),
        midpoint=(
            _decimal(raw["midpoint"], f"ratio {raw['code']} midpoint")
            if raw.get("midpoint") is not None
            else None
        ),
    )


def _methodology(parameters: dict[str, Any], anchor_key: str) -> RatingMethodology:
    try:
        ratio_definitions = tuple(_definition(raw) for raw in parameters["ratios"])
        components = tuple(
            ComponentDefinition(str(raw["code"]), _decimal(raw["weight"], "component weight"))
            for raw in parameters["components"]
        )
        cutpoints = {
            grade: _decimal(value, f"grade_cutpoints.{grade}")
            for grade, value in parameters["grade_cutpoints"].items()
        }
        anchors = {
            grade: _decimal(value, f"{anchor_key}.{grade}")
            for grade, value in parameters[anchor_key].items()
        }
        environment_matrix = tuple(
            tuple(_decimal(value, "operating_environment_matrix") for value in row)
            for row in parameters.get("operating_environment_matrix", (("0", "0"), ("0", "1")))
        )
    except (KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Rating methodology has an incomplete scorecard parameter set.",
        ) from exc
    return RatingMethodology(
        ratio_definitions=ratio_definitions,
        components=components,
        grade_cutpoints=cutpoints,
        grade_order=GRADE_ORDER,
        grade_pd_anchors_pct=anchors,
        confidence_level=_decimal(parameters["confidence_level"], "confidence_level"),
        moc_k_sigma=_decimal(parameters["moc_k_sigma"], "moc_k_sigma"),
        operating_environment_matrix=environment_matrix,  # type: ignore[arg-type]
        bayesian_prior_alpha=_decimal(
            parameters.get("bayesian_prior_alpha", "1"), "bayesian_prior_alpha"
        ),
        bayesian_prior_beta=_decimal(
            parameters.get("bayesian_prior_beta", "99"), "bayesian_prior_beta"
        ),
    )


def _result_payload(result: Any) -> dict[str, Any]:
    return {
        "indicative": True,
        "rating_grade": result.issuer_grade,
        "standalone_score": str(result.standalone_score),
        "standalone_grade": result.standalone_grade,
        "sovereign_ceiling": result.sovereign_ceiling,
        "ceiling_applied": result.ceiling_applied,
        "support_uplift_notches": result.support_uplift_notches,
        "components": [
            {
                "code": component.code,
                "weight": str(component.weight),
                "score": str(component.score),
                "contribution": str(component.contribution),
                "ratios": [
                    {
                        "code": ratio.code,
                        "value": str(ratio.value),
                        "raw_score": str(ratio.raw_score),
                        "adjusted_score": str(ratio.adjusted_score),
                    }
                    for ratio in component.ratios
                ],
            }
            for component in result.component_scores
        ],
        "pd_band": {
            "lower_pct": str(result.pd_band.lower_pct),
            "point_pct": str(result.pd_band.point_pct),
            "upper_pct": str(result.pd_band.upper_pct),
            "confidence_level": str(result.pd_band.confidence_level),
            "basis": result.pd_band.basis,
            "pluto_tasche_upper_pct": str(result.pd_band.pluto_tasche_upper_pct),
            "bayesian_upper_pct": str(result.pd_band.bayesian_upper_pct),
            "margin_of_conservatism_pct": str(result.pd_band.margin_of_conservatism_pct),
        },
    }


def _rating_inputs(
    sources: _RatingSources,
) -> tuple[RatingInputs, dict[str, str], Decimal, Decimal, Decimal]:
    values = _fact_values(sources.facts)
    total_assets = _sum_group(values, "balance_sheet", _ASSET_CATEGORIES)
    loans = values.get(("balance_sheet", "loans_gross"), _ZERO)
    deposits = sum(
        amount
        for (group, category), amount in values.items()
        if group == "balance_sheet" and category.startswith(_DEPOSIT_PREFIXES)
    )
    problem_loans = values.get(("loan_exposure", "past_due_90"), _ZERO)
    staged_total = sum(amount for (group, _), amount in values.items() if group == "ecl_exposure")
    stage3 = sum(
        amount
        for (group, category), amount in values.items()
        if group == "ecl_exposure" and category.endswith(":stage3")
    )
    if staged_total <= _ZERO:
        # Older governed fact vintages carry the Basel past-due bucket but not
        # yet granular IFRS 9 staging; use that conservative public-data proxy.
        staged_total = loans
        stage3 = problem_loans
    provisions = values.get(("capital_component", "general_provisions"), _ZERO)
    gross_income = _latest_income_value(values, "gross_income")
    net_income = _latest_income_value(values, "net_income")
    net_interest_income = _latest_income_value(values, "net_interest_income")
    operating_expenses = _latest_income_value(values, "operating_expenses")
    cashflow_inflows = values.get(("cashflow", "inflows_90d"), _ZERO)
    cashflow_outflows = values.get(("cashflow", "outflows_90d"), _ZERO)
    liquid_assets = _sum_group(values, "balance_sheet", _LIQUID_ASSET_CATEGORIES)
    sovereign_holdings = _sum_group(values, "balance_sheet", _SOVEREIGN_ASSET_CATEGORIES)
    capital_total = _get_metric(sources.capital, "total_capital_ghs", "capital")
    total_rwa = _get_metric(sources.capital, "total_rwa_ghs", "capital")
    ratio_values = {
        "cet1_pct": _get_metric(sources.capital, "cet1_ratio_pct", "capital"),
        "car_pct": _get_metric(sources.capital, "car_pct", "capital"),
        "leverage_pct": _get_metric(sources.capital, "leverage_ratio_pct", "capital"),
        "npl_pct": _ratio(problem_loans, loans, "NPL ratio"),
        "provision_coverage_pct": _ratio(provisions, problem_loans, "provision coverage"),
        "stage3_pct": _ratio(stage3, staged_total, "IFRS 9 stage 3 ratio"),
        "lcr_pct": _get_metric(sources.liquidity, "lcr_pct", "liquidity"),
        "nsfr_pct": _get_metric(sources.liquidity, "nsfr_pct", "liquidity"),
        "loan_to_deposit_pct": _ratio(loans, deposits, "loan-to-deposit ratio"),
        "liquid_assets_to_assets_pct": _ratio(liquid_assets, total_assets, "liquid-assets ratio"),
        "gross_income_to_assets_pct": _ratio(gross_income, total_assets, "gross-income ratio"),
        "income_growth_pct": _income_growth_pct(values),
        "roa_pct": _ratio(net_income, total_assets, "return on assets"),
        "net_interest_margin_pct": _ratio(
            net_interest_income, total_assets, "net interest margin"
        ),
        "cost_to_income_pct": _ratio(operating_expenses, gross_income, "cost-to-income ratio"),
        "cashflow_coverage_pct": _ratio(
            cashflow_inflows, cashflow_outflows, "90-day cash-flow coverage"
        ),
        "eve_sensitivity_pct": abs(_get_metric(sources.irr, "worst_eve_change_pct_tier1", "irr")),
        "fx_nop_pct": _get_metric(sources.fx, "nop_pct_tier1", "fx"),
    }
    counts = {
        grade: int(value) for grade, value in sources.parameters["reference_grade_obligors"].items()
    }
    defaults = {
        grade: int(value) for grade, value in sources.parameters["reference_grade_defaults"].items()
    }
    inputs = RatingInputs(
        ratio_values=ratio_values,
        operating_environment_score=sources.operating_environment_score,
        sovereign_ceiling=sources.sovereign_ceiling,
        grade_obligors=counts,
        grade_defaults=defaults,
        basis="PIT",
        support_uplift_notches=sources.support_uplift_notches,
    )
    return (
        inputs,
        {key: str(value) for key, value in ratio_values.items()},
        sovereign_holdings,
        capital_total,
        total_rwa,
    )


def _live_dependency_metrics(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> dict[str, dict[str, Any]]:
    rows = {
        row.module: dict(row.metrics)
        for row in db.scalars(
            select(LiveMetric).where(
                LiveMetric.organization_id == ctx.organization_id,
                LiveMetric.bank_id == bank.id,
                LiveMetric.reporting_period_id == period.id,
                LiveMetric.module.in_(_LIVE_DEPENDENCIES),
            )
        )
    }
    missing = [module for module in _LIVE_DEPENDENCIES if module not in rows]
    if missing:
        raise _missing("live " + ", ".join(missing) + " metrics")
    return rows


def _live_calculation(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> tuple[Any, Any, Any, dict[str, Any], dict[str, str], Decimal]:
    """Score the current live book without persisting an immutable run."""
    ensure_default_methodology(db)
    methodology = db.scalar(
        select(DeskMethodology)
        .where(DeskMethodology.methodology_code == METHODOLOGY_CODE)
        .order_by(DeskMethodology.version.desc())
        .limit(1)
    )
    if methodology is None:
        raise _missing(f"a {METHODOLOGY_CODE} methodology parameter version")
    facts = list(
        db.scalars(
            select(BankFinancialFact)
            .where(
                BankFinancialFact.organization_id == ctx.organization_id,
                BankFinancialFact.bank_id == bank.id,
                BankFinancialFact.reporting_period_id == period.id,
            )
            .order_by(BankFinancialFact.fact_group, BankFinancialFact.category)
        )
    )
    if not facts:
        raise _missing("current canonical-derived financial facts")
    dependencies = _live_dependency_metrics(db, ctx, bank, period)
    sovereign = _current_sovereign(db, ctx.organization_id, bank.id, period.period_end)
    environment, environment_source = _operating_environment(
        db, ctx.organization_id, bank.id, period.period_end, methodology.parameters
    )
    inputs, ratios, sovereign_holdings, capital_total, total_rwa = _rating_inputs(
        _RatingSources(
            facts=facts,
            capital=dependencies["capital"],
            liquidity=dependencies["liquidity"],
            irr=dependencies["irr"],
            fx=dependencies["fx"],
            operating_environment_score=environment,
            sovereign_ceiling=_normalize_grade(sovereign["rating"]),
            parameters=methodology.parameters,
            support_uplift_notches=0,
        )
    )
    pit = compute_rating(
        inputs, _methodology(methodology.parameters, "pit_grade_pd_anchors_pct")
    )
    ttc = compute_rating(
        RatingInputs(**{**inputs.__dict__, "basis": "TTC"}),
        _methodology(methodology.parameters, "ttc_grade_pd_anchors_pct"),
    )
    stress = ddep_stress(
        sovereign_holdings,
        _decimal(methodology.parameters["ddep_haircut_pct"], "ddep_haircut_pct"),
        capital_total,
        total_rwa,
    )
    snapshot = {
        "period_end": period.period_end.isoformat(),
        "financial_facts": [
            {
                "fact_group": fact.fact_group,
                "category": fact.category,
                "amount": str(fact.amount),
                "attributes": fact.attributes,
            }
            for fact in facts
        ],
        "live_module_metrics": dependencies,
        "sovereign_rating": sovereign,
        "operating_environment": {"score": str(environment), **environment_source},
        "derived_ratios_pct": ratios,
        "methodology": {
            "code": methodology.methodology_code,
            "version": methodology.version,
        },
    }
    return pit, ttc, stress, snapshot, ratios, environment


def current_input_hash(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> str | None:
    """Current live scorecard input hash for freshness comparison."""
    try:
        _, _, _, snapshot, _, _ = _live_calculation(db, ctx, bank, period)
    except HTTPException:
        return None
    return _digest(snapshot)


def compute_live(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> LiveModuleResult:
    """Live Treasury/ALM rating view; never writes an immutable rating run."""
    try:
        pit, ttc, stress, snapshot, _, _ = _live_calculation(db, ctx, bank, period)
    except HTTPException as exc:
        return LiveModuleResult(
            metrics={"availability": "unavailable", "reason": str(exc.detail)},
            status="na",
            input_hash=None,
        )
    upper = pit.pd_band.upper_pct
    if not stress.eligible or upper >= _LIVE_PD_RED_PCT:
        live_status = "red"
    elif upper >= _LIVE_PD_AMBER_PCT:
        live_status = "amber"
    else:
        live_status = "green"
    findings: list[LiveFindingSpec] = []
    if not stress.eligible:
        findings.append(
            LiveFindingSpec(
                rule_id="ddep_capital_absorption",
                severity="critical",
                metric="ddep_eligible",
                message="Current capital cannot absorb the governed sovereign DDEP stress haircut.",
            )
        )
    elif upper >= _LIVE_PD_RED_PCT:
        findings.append(
            LiveFindingSpec(
                rule_id="pd_upper_critical",
                severity="high",
                metric="pit_pd_upper_pct",
                message=f"Conservative PIT PD upper band is {upper}%.",
            )
        )
    elif upper >= _LIVE_PD_AMBER_PCT:
        findings.append(
            LiveFindingSpec(
                rule_id="pd_upper_elevated",
                severity="medium",
                metric="pit_pd_upper_pct",
                message=f"Conservative PIT PD upper band is elevated at {upper}%.",
            )
        )
    return LiveModuleResult(
        metrics={
            "pit_rating_grade": pit.issuer_grade,
            "ttc_rating_grade": ttc.issuer_grade,
            "standalone_grade": pit.standalone_grade,
            "sovereign_ceiling": pit.sovereign_ceiling,
            "ceiling_applied": str(pit.ceiling_applied).lower(),
            "pit_pd_lower_pct": str(pit.pd_band.lower_pct),
            "pit_pd_point_pct": str(pit.pd_band.point_pct),
            "pit_pd_upper_pct": str(pit.pd_band.upper_pct),
            "ttc_pd_lower_pct": str(ttc.pd_band.lower_pct),
            "ttc_pd_point_pct": str(ttc.pd_band.point_pct),
            "ttc_pd_upper_pct": str(ttc.pd_band.upper_pct),
            "confidence_level": str(pit.pd_band.confidence_level),
            "methodology_version": str(snapshot["methodology"]["version"]),
            "ddep_eligible": str(stress.eligible).lower(),
            "ddep_post_stress_capital_ratio_pct": (
                str(stress.post_stress_capital_ratio_pct)
                if stress.post_stress_capital_ratio_pct is not None
                else ""
            ),
        },
        status=live_status,
        input_hash=_digest(snapshot),
        findings=tuple(findings),
    )


def run(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    reporting_period_id: UUID,
    *,
    support_uplift_notches: int = 0,
) -> ImpliedRatingRun:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    period = db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.id == reporting_period_id,
            BankReportingPeriod.bank_id == bank_id,
            BankReportingPeriod.organization_id == ctx.organization_id,
        )
    )
    if period is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reporting period not found."
        )
    if ctx.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required."
        )
    ensure_default_methodology(db)
    methodology = db.scalar(
        select(DeskMethodology)
        .where(
            DeskMethodology.methodology_code == METHODOLOGY_CODE,
        )
        .order_by(DeskMethodology.version.desc())
        .limit(1)
    )
    if methodology is None:
        raise _missing(f"a {METHODOLOGY_CODE} methodology parameter version")
    facts = list(
        db.scalars(
            select(BankFinancialFact)
            .where(
                BankFinancialFact.organization_id == ctx.organization_id,
                BankFinancialFact.bank_id == bank_id,
                BankFinancialFact.reporting_period_id == period.id,
            )
            .order_by(BankFinancialFact.fact_group, BankFinancialFact.category)
        )
    )
    if not facts:
        raise _missing("financial facts for the reporting period")
    capital = _latest_succeeded_metrics(db, ctx.organization_id, bank_id, period.id, "capital")
    liquidity = _latest_succeeded_metrics(db, ctx.organization_id, bank_id, period.id, "liquidity")
    irr = _latest_succeeded_metrics(db, ctx.organization_id, bank_id, period.id, "irr")
    fx = _latest_succeeded_metrics(db, ctx.organization_id, bank_id, period.id, "fx")
    sovereign = _current_sovereign(db, ctx.organization_id, bank_id, period.period_end)
    environment, environment_source = _operating_environment(
        db, ctx.organization_id, bank_id, period.period_end, methodology.parameters
    )
    inputs, ratios, sovereign_holdings, capital_total, total_rwa = _rating_inputs(
        _RatingSources(
            facts=facts,
            capital=capital,
            liquidity=liquidity,
            irr=irr,
            fx=fx,
            operating_environment_score=environment,
            sovereign_ceiling=_normalize_grade(sovereign["rating"]),
            parameters=methodology.parameters,
            support_uplift_notches=support_uplift_notches,
        )
    )
    pit = compute_rating(inputs, _methodology(methodology.parameters, "pit_grade_pd_anchors_pct"))
    ttc_inputs = RatingInputs(**{**inputs.__dict__, "basis": "TTC"})
    ttc = compute_rating(
        ttc_inputs, _methodology(methodology.parameters, "ttc_grade_pd_anchors_pct")
    )
    stress = ddep_stress(
        sovereign_holdings,
        _decimal(methodology.parameters["ddep_haircut_pct"], "ddep_haircut_pct"),
        capital_total,
        total_rwa,
    )
    snapshot = {
        "period_end": period.period_end.isoformat(),
        "financial_facts": [
            {
                "fact_group": fact.fact_group,
                "category": fact.category,
                "amount": str(fact.amount),
                "attributes": fact.attributes,
            }
            for fact in facts
        ],
        "regulatory_metrics": {"capital": capital, "liquidity": liquidity, "irr": irr, "fx": fx},
        "sovereign_rating": sovereign,
        "operating_environment": {"score": str(environment), **environment_source},
        "derived_ratios_pct": ratios,
    }
    run_row = ImpliedRatingRun(
        organization_id=ctx.organization_id,
        bank_id=bank_id,
        reporting_period_id=period.id,
        methodology_code=methodology.methodology_code,
        methodology_version=methodology.version,
        status="succeeded",
        engine_version=ENGINE_VERSION,
        input_hash=_digest(snapshot),
        input_snapshot=snapshot,
        results={
            "pit": _result_payload(pit),
            "ttc": _result_payload(ttc),
            "ddep_stress": {
                "sovereign_holdings_ghs": str(sovereign_holdings),
                "sovereign_loss_ghs": str(stress.sovereign_loss),
                "post_stress_capital_ghs": str(stress.post_stress_capital),
                "post_stress_capital_ratio_pct": (
                    str(stress.post_stress_capital_ratio_pct)
                    if stress.post_stress_capital_ratio_pct is not None
                    else None
                ),
                "eligible": stress.eligible,
            },
        },
        completed_at=utc_now(),
        created_by=ctx.actor_user_id,
    )
    db.add(run_row)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="implied_rating_run.succeeded",
        entity_type="implied_rating_run",
        entity_id=run_row.id,
        details={
            "bank_id": bank_id,
            "reporting_period_id": str(period.id),
            "methodology": f"{methodology.methodology_code}/v{methodology.version}",
            "input_hash": run_row.input_hash,
            "pit_grade": pit.issuer_grade,
            "pit_pd_upper_pct": str(pit.pd_band.upper_pct),
        },
    )
    db.commit()
    return run_row


def get_run(db: Session, ctx: TenantContext, bank_id: str, run_id: UUID) -> ImpliedRatingRun:
    row = db.scalar(
        select(ImpliedRatingRun).where(
            ImpliedRatingRun.id == run_id,
            ImpliedRatingRun.organization_id == ctx.organization_id,
            ImpliedRatingRun.bank_id == bank_id,
        )
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Implied rating run not found."
        )
    return row


def list_runs(db: Session, ctx: TenantContext, bank_id: str) -> list[ImpliedRatingRun]:
    return list(
        db.scalars(
            select(ImpliedRatingRun)
            .where(
                ImpliedRatingRun.organization_id == ctx.organization_id,
                ImpliedRatingRun.bank_id == bank_id,
            )
            .order_by(ImpliedRatingRun.completed_at.desc())
        )
    )
