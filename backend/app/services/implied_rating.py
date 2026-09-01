"""Snapshot-driven implied bank-rating and PD calculation service.

The service joins governed financial facts, completed regulatory calculations,
and tenant-published sovereign market data at one reporting date. It never
reads a dashboard projection or a mutable current value after snapshotting.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
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
    CurrentFinancialFact,
    DeskMethodology,
    FinancialFactRow,
    ImpliedRatingRun,
    InstitutionProfile,
    Jurisdiction,
    LiveMetric,
    RegulatoryRun,
)
from app.services import institution_types, sdi_readiness
from app.services.audit import record_event
from app.services.live_types import LiveFindingSpec, LiveModuleResult

METHODOLOGY_CODE = "AEQ-GHS-BANK-PD"
SDI_METHODOLOGY_CODE = "AEQ-GH-SDI-FS"
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


def _sdi_methodology_pending(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> LiveModuleResult:
    """An SDI must never receive the Basel-bank scorecard under another name.

    ``AEQ-GHS-BANK-PD`` consumes CET1/leverage, LCR/NSFR and FX NOP inputs.
    Savings-and-loans institutions are governed on s.29 capital and LMTD
    liquidity instead, so substituting neutral values would create an
    unvalidated credit assessment. The dedicated methodology dossier defines
    the governed release gate; until it passes, no grade or PD is emitted.
    """
    from app.services import sdi_rating  # noqa: PLC0415 - breaks an import cycle

    evidence = sdi_readiness.assess_sdi_readiness(db, ctx, bank, period.period_end)
    blocked = any(item.status == sdi_readiness.BLOCKED for item in evidence)
    partial = any(item.status == sdi_readiness.PARTIAL for item in evidence)
    coverage_status = "blocked" if blocked else "partial" if partial else "ready"
    # The scorecard's own state machine is the authority on WHICH of the
    # dossier's §4 states this institution is in and why. The data-readiness
    # ledger above stays: it answers a different question (is the BOOK ready)
    # from the one the scorecard answers (is the MODEL approved), and the
    # founder-visible confusion this whole module exists to prevent is exactly
    # the two being conflated.
    assessment = sdi_rating.assessment_state(db, ctx, bank, period.period_end)
    metrics: dict[str, Any] = {
        "availability": "unavailable" if not assessment.components else "advisory",
        "reason": assessment.reason
        or (
            f"{SDI_METHODOLOGY_CODE} is pending calibration, independent model validation, "
            "and approval. The bank-only AEQ-GHS-BANK-PD scorecard is not applicable "
            "to an SDI because it requires Basel LCR/NSFR, FX NOP, and Tier-1 inputs."
        ),
        "methodology_code": SDI_METHODOLOGY_CODE,
        "methodology_version": assessment.methodology_version,
        "assessment_kind": "sdi_financial_strength",
        "assessment_state": assessment.state,
        # Never a grade, never a PD — dossier §4 states 4 and 5 are closed for v1,
        # and the scorecard computes neither.
        "releases_grade": assessment.releases_grade,
        "releases_pd": assessment.releases_pd,
        "evidence_as_of": period.period_end.isoformat(),
        "evidence_coverage_status": coverage_status,
        "evidence_readiness": [
            {"module": item.module, "status": item.status, "reasons": item.reasons}
            for item in evidence
        ],
        # Every candidate ratio, with its value or the reason there is none.
        # Nothing is imputed (§2).
        "scorecard_evidence": [
            {
                "code": item.code,
                "value": None if item.value is None else str(item.value),
                "source": item.source,
                "note": item.note,
            }
            for item in assessment.evidence
        ],
        "omitted_components": list(assessment.omitted_components),
        "limitations": list(assessment.limitations),
    }
    if assessment.issued_grade is not None:
        metrics.update(
            {
                "composite_score": str(assessment.composite_score),
                "standalone_grade": assessment.standalone_grade,
                "rating_grade": assessment.issued_grade,
                "sovereign_ceiling": assessment.sovereign_ceiling,
                "ceiling_applied": assessment.ceiling_applied,
            }
        )
    if assessment.components:
        metrics["component_scores"] = [
            {
                "code": component.code,
                "score": str(component.score),
                "weight": str(component.weight),
                "contribution": str(component.contribution),
            }
            for component in assessment.components
        ]
    return LiveModuleResult(
        metrics=metrics,
        # ``live_metrics.status`` is a RAG verdict and the column CHECKs it:
        # green | amber | red | na. "ready" is not a member — returning it made
        # the INSERT violate ``ck_live_metrics_status``, roll the whole module
        # back, and surface as a FAILED rating even though the scorecard had
        # computed successfully (the failing row carried
        # ``availability='advisory'``).
        #
        # It stays ``na`` even WITH component scores, and that is the honest
        # value rather than a placeholder: a RAG verdict is a judgement about the
        # institution's strength, which is precisely what v1 does not issue —
        # ``AEQ-GH-SDI-FS`` releases advisory component scores and no grade
        # (dossier §4 states 4 and 5). Colouring the tile would be a grade by
        # another name. The scores render from ``metrics``; the verdict stays
        # unissued until an approved score-to-grade mapping exists.
        status="na",
        input_hash=None,
        source_as_of_date=period.period_end,
    )
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
# A reporting period spanning at least this many days is treated as annual for
# the §2.2 three-year-average convention (a ~12-month year vs a quarter/half).
_ANNUAL_MIN_DAYS = 350
# §2.2 anti-manipulation: the problem-loan and profitability ratios take the
# WEAKER of the latest figure and the three-year average, so a single good year
# cannot flatter the score. Capital ratios stay latest-only (point-in-time).
_CONSERVATIVE_RATIOS: dict[str, str] = {
    "npl_pct": "lower_is_better",
    "roa_pct": "higher_is_better",
    "net_interest_margin_pct": "higher_is_better",
    "gross_income_to_assets_pct": "higher_is_better",
    "cost_to_income_pct": "lower_is_better",
}

DEFAULT_PARAMETERS_V2: dict[str, Any] = {
    "parameter_status": (
        "v2 remediation: single sourced TTC master scale; PIT derived by Vasicek "
        "systematic conditioning; anchor-centred Bayesian band. Values remain "
        "calibration placeholders pending independent validation (§8.2/§15)."
    ),
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
    # The SINGLE sourced master scale: grade -> long-run (TTC, unconditional)
    # 1-year PD central tendency, in percent. Shaped to published agency
    # idealized/observed 1-year default rates by rating (S&P/Moody's cumulative
    # default studies; e.g. CCC/C ~25-28%/yr). PIT is DERIVED from this by
    # Vasicek conditioning on the live systematic factor — there is NO separate
    # PIT table. Provenance below; values require independent validation
    # against the primary agency tables before sizing a live repo haircut.
    "master_scale_source": (
        "Aligned to the shape of S&P/Moody's average 1-year corporate default "
        "rates by rating grade; calibration placeholder pending §8.2 validation."
    ),
    "master_scale_pd_anchors_pct": {
        "aaa": "0.01",
        "aa+": "0.02",
        "aa": "0.02",
        "aa-": "0.03",
        "a+": "0.04",
        "a": "0.06",
        "a-": "0.09",
        "bbb+": "0.14",
        "bbb": "0.22",
        "bbb-": "0.35",
        "bb+": "0.55",
        "bb": "0.85",
        "bb-": "1.35",
        "b+": "2.30",
        "b": "4.50",
        "b-": "8.00",
        "ccc+": "16.00",
        "ccc": "25.00",
        "ccc-": "36.00",
        "cc": "50.00",
        "c": "100.00",
    },
    # REAL internal rated-portfolio outcomes per grade. Empty in Stage 1 (no
    # internal default history) -> the band is the anchor-centred prior credible
    # interval. Populated only by observed outcomes; NOT a synthetic population.
    "internal_grade_obligors": {grade: 0 for grade in GRADE_ORDER},
    "internal_grade_defaults": {grade: 0 for grade in GRADE_ORDER},
    "confidence_level": "0.90",
    "moc_k_sigma": "1.0",
    # Vasicek asset correlation — elevated for the Ghana sovereign-bank nexus (§6).
    "asset_correlation": "0.24",
    # Bayesian prior strength kappa (effective external observations per grade).
    "prior_strength": "25",
    # Systematic-factor mapping: Z = (op_env_score - neutral) / scale. A fragile
    # system (op-env below neutral) yields Z < 0 -> PIT above TTC. Versioned.
    "systematic_factor_neutral": "0.65",
    "systematic_factor_scale": "0.15",
    "operating_environment_index_code": "GHANA_OPERATING_ENVIRONMENT_SCORE",
    "operating_environment_fallback_score": "0.55",
    "operating_environment_matrix": [["0", "0"], ["0", "1"]],
    "ddep_haircut_pct": "30.0",
    # §4.3 support notching. Parent/systemic uplift is small and bounded, and a
    # distressed sovereign (ceiling at/below this floor grade) caps it to zero —
    # a weak sovereign cannot credibly backstop its banks. Versioned parameters.
    "support_uplift_max_notches": "1",
    "support_distress_floor_grade": "b-",
}


@dataclass(frozen=True)
class _RatingSources:
    facts: Sequence[FinancialFactRow]
    capital: dict[str, Any]
    liquidity: dict[str, Any]
    irr: dict[str, Any]
    fx: dict[str, Any]
    operating_environment_score: Decimal
    sovereign_ceiling: str
    parameters: dict[str, Any]
    # Governed problem-loan/profitability ratios over up to three prior annual
    # reporting periods (most recent first) for the §2.2 weaker-of convention.
    annual_ratio_history: list[dict[str, Decimal]]
    support_uplift_notches: int
    # The credit module's metrics (live payload or sealed baseline run) when it
    # has computed; None otherwise. OPTIONAL by design - the scorecard predates
    # the credit module and must keep rating tenants where it has not run.
    credit: dict[str, Any] | None = None


def ensure_default_methodology(db: Session) -> DeskMethodology:
    """Ensure the current (v2) scorecard parameter version is active.

    Idempotent and Track-2 safe: if the latest stored version already carries the
    v2 structure (a single master scale), return it. Otherwise append a NEW
    version with the current parameters — prior versions are never mutated, so any
    historical run stays reproducible under the version that produced it (§8.4).
    """
    latest = db.scalar(
        select(DeskMethodology)
        .where(DeskMethodology.methodology_code == METHODOLOGY_CODE)
        .order_by(DeskMethodology.version.desc())
        .limit(1)
    )
    if latest is not None and "master_scale_pd_anchors_pct" in (latest.parameters or {}):
        return latest
    next_version = (latest.version + 1) if latest is not None else 1
    row = DeskMethodology(
        methodology_code=METHODOLOGY_CODE,
        version=next_version,
        status="approved",
        parameters=DEFAULT_PARAMETERS_V2,
        change_rationale=(
            "v2 remediation of the PD band: single sourced TTC master scale; PIT "
            "derived by Vasicek systematic conditioning (not a second static "
            "table); anchor-centred Bayesian credible-interval band with k-sigma "
            "MoC and Basel floor; Pluto-Tasche retained as a zero-default "
            "diagnostic; synthetic reference population removed."
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


def _fact_values(facts: Sequence[FinancialFactRow]) -> dict[tuple[str, str], Decimal]:
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


def _optional_metric(metrics: dict[str, Any] | None, code: str) -> Decimal | None:
    """A dependency figure that may honestly be absent (module not run, value
    unstated). Missing/None/non-numeric all mean "no figure" - never zero."""
    if not metrics:
        return None
    value = metrics.get(code)
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


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
            CanonicalCounterpartyRating.withdrawn_at.is_(None),
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
                CanonicalMarketIndex.withdrawn_at.is_(None),
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


def _support_uplift(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    sovereign_ceiling: str,
    parameters: dict[str, Any],
) -> int:
    """§4.3 parent/systemic support notching from available master data.

    Parent support is inferred conservatively from the ORASS institution profile:
    a foreign strategic parent — a parent country registered abroad holding a
    majority foreign stake — is a credible source of ordinary support and earns a
    small, bounded uplift. Systemic-importance uplift is NOT inferred: the
    platform holds no D-SIB designation, and inventing one would overstate
    support.

    Support never lifts the issuer above the sovereign — a distressed sovereign
    cannot credibly backstop its banks (§4.3). That cap is enforced twice: here,
    by returning zero once the sovereign ceiling is at/below the distress floor
    grade, and in the engine, which bounds ``issuer_index`` at the ceiling
    regardless of what this returns. ``0`` is the honest default when no
    parent/systemic data exists.
    """
    floor_grade = str(parameters.get("support_distress_floor_grade", "b-"))
    if (
        floor_grade in GRADE_ORDER
        and sovereign_ceiling in GRADE_ORDER
        and GRADE_ORDER.index(sovereign_ceiling) >= GRADE_ORDER.index(floor_grade)
    ):
        return 0
    profile = db.scalar(
        select(InstitutionProfile).where(
            InstitutionProfile.organization_id == ctx.organization_id,
            InstitutionProfile.bank_id == bank.id,
        )
    )
    if profile is None:
        return 0
    foreign_pct = profile.ownership_foreign_pct
    has_foreign_parent = bool(
        profile.parent_country_code
        and profile.parent_country_code != bank.jurisdiction_code
        and foreign_pct is not None
        and Decimal(str(foreign_pct)) >= Decimal("50")
    )
    if not has_foreign_parent:
        return 0
    max_notches = int(
        _decimal(
            parameters.get("support_uplift_max_notches", "1"), "support_uplift_max_notches"
        )
    )
    return max(0, min(1, max_notches))


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


def _methodology(parameters: dict[str, Any]) -> RatingMethodology:
    """Build the engine methodology from the single sourced master scale.

    PIT/TTC are no longer two tables — one master scale (TTC, unconditional) plus
    the Vasicek asset correlation and Bayesian prior strength; PIT is derived at
    compute time from the live systematic factor.
    """
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
            grade: _decimal(value, f"master_scale_pd_anchors_pct.{grade}")
            for grade, value in parameters["master_scale_pd_anchors_pct"].items()
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
        asset_correlation=_decimal(
            parameters.get("asset_correlation", "0.24"), "asset_correlation"
        ),
        prior_strength=_decimal(parameters.get("prior_strength", "25"), "prior_strength"),
        operating_environment_matrix=environment_matrix,  # type: ignore[arg-type]
    )


def _systematic_factor(
    operating_environment_score: Decimal, parameters: dict[str, Any]
) -> Decimal:
    """Live systematic factor Z for the PIT conditioning (§6.1).

    ``Z = (operating_environment_score − neutral) / scale``. A fragile operating
    environment (score below the neutral level) yields ``Z < 0``, lifting PIT
    above the long-run TTC anchor through the Vasicek conditioning. The
    operating-environment score already folds in banking-system risk, credit
    conditions and the sovereign backdrop (§3.4), so it is the systematic proxy.
    """
    neutral = _decimal(
        parameters.get("systematic_factor_neutral", "0.65"), "systematic_factor_neutral"
    )
    scale = _decimal(
        parameters.get("systematic_factor_scale", "0.15"), "systematic_factor_scale"
    )
    if scale <= _ZERO:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="systematic_factor_scale must be positive.",
        )
    return (operating_environment_score - neutral) / scale


_COMPONENT_LABELS = {
    "capitalisation": "Capitalisation & leverage",
    "asset_quality": "Asset quality",
    "funding_liquidity": "Funding & liquidity",
    "business_profile": "Business profile",
    "earnings": "Earnings & profitability",
    "risk_profile": "Risk profile",
}


def _key_drivers(result: Any) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Explainability (§10.1): the strongest and weakest components by score.

    Each component score is in [0, 1] (1 = strongest); the top scorers are the
    rating's supports, the bottom scorers its drags. Returns up to two of each,
    with the leading ratio of each named so the bank can act on it.
    """

    def _entry(component: Any) -> dict[str, str]:
        top_ratio = max(
            component.ratios,
            key=lambda ratio: abs(ratio.adjusted_score - Decimal("0.5")),
        )
        return {
            "component": str(component.code),
            "label": _COMPONENT_LABELS.get(component.code, str(component.code)),
            "score": str(component.score),
            "weight": str(component.weight),
            "top_ratio": str(top_ratio.code),
            "top_ratio_value": str(top_ratio.value),
        }

    ordered = sorted(result.component_scores, key=lambda component: component.score, reverse=True)
    up = [_entry(component) for component in ordered[:2]]
    down = [_entry(component) for component in reversed(ordered[-2:])]
    return up, down


def _result_payload(result: Any) -> dict[str, Any]:
    band = result.pd_band
    drivers_up, drivers_down = _key_drivers(result)
    return {
        "indicative": True,
        "rating_grade": result.issuer_grade,
        "standalone_score": str(result.standalone_score),
        "standalone_grade": result.standalone_grade,
        "sovereign_ceiling": result.sovereign_ceiling,
        "ceiling_applied": result.ceiling_applied,
        "support_uplift_notches": result.support_uplift_notches,
        "key_drivers_up": drivers_up,
        "key_drivers_down": drivers_down,
        "components": [
            {
                "code": component.code,
                "label": _COMPONENT_LABELS.get(component.code, component.code),
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
            "lower_pct": str(band.lower_pct),
            "point_pct": str(band.point_pct),
            "upper_pct": str(band.upper_pct),
            "central_tendency_pct": str(band.central_tendency_pct),
            "confidence_level": str(band.confidence_level),
            "basis": band.basis,
            "systematic_factor": str(band.systematic_factor),
            "bayesian_upper_pct": str(band.bayesian_upper_pct),
            "pluto_tasche_upper_pct": (
                None if band.pluto_tasche_upper_pct is None else str(band.pluto_tasche_upper_pct)
            ),
            "margin_of_conservatism_pct": str(band.margin_of_conservatism_pct),
            "prior_strength": str(band.prior_strength),
            "internal_obligors": band.internal_obligors,
            "internal_defaults": band.internal_defaults,
        },
    }


def _period_governed_ratios(facts: Sequence[FinancialFactRow]) -> dict[str, Decimal] | None:
    """The five §2.2 problem-loan/profitability ratios for one period's facts.

    Returns ``None`` when the period lacks the balance-sheet or income-statement
    facts to compute the full set, so such a period simply does not contribute to
    the three-year average (graceful degrade rather than a hard failure).
    """
    values = _fact_values(facts)
    total_assets = _sum_group(values, "balance_sheet", _ASSET_CATEGORIES)
    loans = values.get(("balance_sheet", "loans_gross"), _ZERO)
    problem_loans = values.get(("loan_exposure", "past_due_90"), _ZERO)
    try:
        gross_income = _latest_income_value(values, "gross_income")
        net_income = _latest_income_value(values, "net_income")
        net_interest_income = _latest_income_value(values, "net_interest_income")
        operating_expenses = _latest_income_value(values, "operating_expenses")
        return {
            "npl_pct": _ratio(problem_loans, loans, "NPL ratio"),
            "roa_pct": _ratio(net_income, total_assets, "return on assets"),
            "net_interest_margin_pct": _ratio(
                net_interest_income, total_assets, "net interest margin"
            ),
            "gross_income_to_assets_pct": _ratio(
                gross_income, total_assets, "gross-income ratio"
            ),
            "cost_to_income_pct": _ratio(
                operating_expenses, gross_income, "cost-to-income ratio"
            ),
        }
    except HTTPException:
        return None


def _annual_ratio_history(
    db: Session, organization_id: str, bank_id: str, current_period: BankReportingPeriod
) -> list[dict[str, Decimal]]:
    """Governed ratios over up to three prior annual periods (§2.2), newest first.

    Only annual periods (≈12-month span) on or before the current period end are
    considered; the newest three that carry a full governed-ratio set are kept.
    Quarterly/partial periods and periods with incomplete facts are skipped, so
    the caller can degrade gracefully when fewer than two remain.
    """
    periods = list(
        db.scalars(
            select(BankReportingPeriod)
            .where(
                BankReportingPeriod.organization_id == organization_id,
                BankReportingPeriod.bank_id == bank_id,
                BankReportingPeriod.period_end <= current_period.period_end,
            )
            .order_by(BankReportingPeriod.period_end.desc())
        )
    )
    history: list[dict[str, Decimal]] = []
    for period in periods:
        if (period.period_end - period.period_start).days < _ANNUAL_MIN_DAYS:
            continue
        facts = list(
            db.scalars(
                select(BankFinancialFact).where(
                    BankFinancialFact.organization_id == organization_id,
                    BankFinancialFact.bank_id == bank_id,
                    BankFinancialFact.reporting_period_id == period.id,
                )
            )
        )
        ratios = _period_governed_ratios(facts)
        if ratios is not None:
            history.append(ratios)
        if len(history) >= 3:
            break
    return history


def _apply_conservative_basis(
    ratio_values: dict[str, Decimal], history: list[dict[str, Decimal]]
) -> dict[str, Any]:
    """§2.2 anti-manipulation convention (Moody's anti-cherry-picking).

    For the problem-loan and profitability ratios, replace the latest figure with
    the WEAKER of it and the three-year average — the more conservative (worse)
    ratio: ``max`` for a lower-is-better problem-loan/cost ratio, ``min`` for a
    higher-is-better income ratio. Capital ratios are left untouched. Mutates
    ``ratio_values`` in place and returns a transparent, value-based record of
    which figure won for each governed ratio (kept in the run snapshot so the
    input digest stays reproducible). With fewer than two usable annual periods
    the convention degrades to latest-only and records the reason.
    """
    usable = len(history)
    if usable < 2:
        return {
            "convention": "weaker_of_three_year_average_and_latest",
            "reference": "AequorOS_Implied_Rating_PD_Implementation.md §2.2",
            "applied": False,
            "annual_periods_used": usable,
            "reason": (
                "fewer than two annual periods with a full governed-ratio set; "
                "latest figure used"
            ),
            "ratios": {},
        }
    records: dict[str, Any] = {}
    for code, direction in _CONSERVATIVE_RATIOS.items():
        samples = [period[code] for period in history if code in period]
        if not samples:
            continue
        latest = ratio_values[code]
        three_year_avg = sum(samples, _ZERO) / Decimal(len(samples))
        chosen = (
            min(latest, three_year_avg)
            if direction == "higher_is_better"
            else max(latest, three_year_avg)
        )
        ratio_values[code] = chosen
        records[code] = {
            "direction": direction,
            "latest_pct": str(latest),
            "three_year_avg_pct": str(three_year_avg),
            "chosen_pct": str(chosen),
            "basis": "three_year_average" if chosen != latest else "latest",
        }
    return {
        "convention": "weaker_of_three_year_average_and_latest",
        "reference": "AequorOS_Implied_Rating_PD_Implementation.md §2.2",
        "applied": True,
        "annual_periods_used": usable,
        "ratios": records,
    }


def _rating_inputs(
    sources: _RatingSources,
) -> tuple[RatingInputs, dict[str, str], Decimal, Decimal, Decimal, dict[str, Any]]:
    values = _fact_values(sources.facts)
    total_assets = _sum_group(values, "balance_sheet", _ASSET_CATEGORIES)
    loans = values.get(("balance_sheet", "loans_gross"), _ZERO)
    deposits = sum(
        (
            amount
            for (group, category), amount in values.items()
            if group == "balance_sheet" and category.startswith(_DEPOSIT_PREFIXES)
        ),
        _ZERO,
    )
    problem_loans = values.get(("loan_exposure", "past_due_90"), _ZERO)
    staged_total = sum(
        (amount for (group, _), amount in values.items() if group == "ecl_exposure"), _ZERO
    )
    stage3 = sum(
        (
            amount
            for (group, category), amount in values.items()
            if group == "ecl_exposure" and category.endswith(":stage3")
        ),
        _ZERO,
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
    # Credit PR-9 evidence switch: when the credit module has computed, its
    # governed classification is the authority for the NPL ratio and provision
    # coverage - the past-due-90 bucket and the general-provisions capital
    # component are only PROXIES for those figures. Byte-identical fallback
    # when the module has no figure (no loan book, provisions unstated).
    # Applied BEFORE the conservative basis so the weaker-of convention judges
    # the governed current value.
    credit_npl = _optional_metric(sources.credit, "npl_ratio_pct")
    if credit_npl is not None:
        ratio_values["npl_pct"] = credit_npl
    credit_coverage = _optional_metric(sources.credit, "provision_coverage_pct")
    if credit_coverage is not None:
        ratio_values["provision_coverage_pct"] = credit_coverage
    # §2.2: fold the three-year-average / latest weaker-of choice into the
    # problem-loan and profitability ratios before scoring (capital untouched).
    conservative_basis = _apply_conservative_basis(ratio_values, sources.annual_ratio_history)
    counts = {
        grade: int(value)
        for grade, value in sources.parameters.get("internal_grade_obligors", {}).items()
    }
    defaults = {
        grade: int(value)
        for grade, value in sources.parameters.get("internal_grade_defaults", {}).items()
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
        conservative_basis,
    )


def _live_dependency_metrics(
    db: Session, ctx: TenantContext, bank: Bank
) -> dict[str, dict[str, Any]]:
    rows = {
        row.module: dict(row.metrics)
        for row in db.scalars(
            select(LiveMetric).where(
                LiveMetric.organization_id == ctx.organization_id,
                LiveMetric.bank_id == bank.id,
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
            select(CurrentFinancialFact)
            .where(
                CurrentFinancialFact.organization_id == ctx.organization_id,
                CurrentFinancialFact.bank_id == bank.id,
            )
            .order_by(CurrentFinancialFact.fact_group, CurrentFinancialFact.category)
        )
    )
    if not facts:
        raise _missing("current canonical-derived financial facts")
    source_as_of_date = facts[0].source_as_of_date
    dependencies = _live_dependency_metrics(db, ctx, bank)
    credit_row = db.scalar(
        select(LiveMetric).where(
            LiveMetric.organization_id == ctx.organization_id,
            LiveMetric.bank_id == bank.id,
            LiveMetric.module == "credit",
        )
    )
    if credit_row is not None:
        # Lands in the snapshot's live_module_metrics, so the rating's input
        # hash goes stale when the credit evidence moves.
        dependencies["credit"] = dict(credit_row.metrics)
    sovereign = _current_sovereign(db, ctx.organization_id, bank.id, source_as_of_date)
    ceiling = _normalize_grade(sovereign["rating"])
    environment, environment_source = _operating_environment(
        db, ctx.organization_id, bank.id, source_as_of_date, methodology.parameters
    )
    history = _annual_ratio_history(db, ctx.organization_id, bank.id, period)
    uplift = _support_uplift(db, ctx, bank, ceiling, methodology.parameters)
    (
        inputs,
        ratios,
        sovereign_holdings,
        capital_total,
        total_rwa,
        conservative_basis,
    ) = _rating_inputs(
        _RatingSources(
            facts=facts,
            capital=dependencies["capital"],
            liquidity=dependencies["liquidity"],
            irr=dependencies["irr"],
            fx=dependencies["fx"],
            operating_environment_score=environment,
            sovereign_ceiling=ceiling,
            parameters=methodology.parameters,
            annual_ratio_history=history,
            support_uplift_notches=uplift,
            credit=dependencies.get("credit"),
        )
    )
    engine_methodology = _methodology(methodology.parameters)
    z_now = _systematic_factor(environment, methodology.parameters)
    pit = compute_rating(
        RatingInputs(**{**inputs.__dict__, "basis": "PIT", "systematic_factor": z_now}),
        engine_methodology,
    )
    ttc = compute_rating(
        RatingInputs(**{**inputs.__dict__, "basis": "TTC", "systematic_factor": z_now}),
        engine_methodology,
    )
    stress = ddep_stress(
        sovereign_holdings,
        _decimal(methodology.parameters["ddep_haircut_pct"], "ddep_haircut_pct"),
        capital_total,
        total_rwa,
    )
    snapshot = {
        "period_end": source_as_of_date.isoformat(),
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
        "conservative_income_basis": conservative_basis,
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
    if institution_types.institution_class(db, bank) == "sdi":
        return _sdi_methodology_pending(db, ctx, bank, period)
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
    # Top rating support and drag for the live card (§10.1 explainability).
    drivers_up, drivers_down = _key_drivers(pit)
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
            "pit_pd_central_pct": str(pit.pd_band.central_tendency_pct),
            "pit_systematic_factor": str(pit.pd_band.systematic_factor),
            "ttc_pd_lower_pct": str(ttc.pd_band.lower_pct),
            "ttc_pd_point_pct": str(ttc.pd_band.point_pct),
            "ttc_pd_upper_pct": str(ttc.pd_band.upper_pct),
            "ttc_pd_central_pct": str(ttc.pd_band.central_tendency_pct),
            "confidence_level": str(pit.pd_band.confidence_level),
            "key_driver_up": str(drivers_up[0]["label"]) if drivers_up else "",
            "key_driver_down": str(drivers_down[0]["label"]) if drivers_down else "",
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
        source_as_of_date=date.fromisoformat(str(snapshot["period_end"])),
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
    if institution_types.institution_class(db, bank) == "sdi":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{SDI_METHODOLOGY_CODE} is pending calibration, independent model validation, "
                "and approval; the bank-only implied rating run cannot be used for an SDI."
            ),
        )
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
    try:
        # Optional: a sealed credit baseline upgrades the NPL/coverage evidence;
        # its absence keeps the pre-credit proxies byte-identical.
        credit = _latest_succeeded_metrics(db, ctx.organization_id, bank_id, period.id, "credit")
    except HTTPException:
        credit = None
    sovereign = _current_sovereign(db, ctx.organization_id, bank_id, period.period_end)
    ceiling = _normalize_grade(sovereign["rating"])
    environment, environment_source = _operating_environment(
        db, ctx.organization_id, bank_id, period.period_end, methodology.parameters
    )
    history = _annual_ratio_history(db, ctx.organization_id, bank_id, period)
    # An explicit analyst notching request wins; otherwise derive support (§4.3)
    # from the institution profile (0 when no parent/systemic data exists).
    model_uplift = _support_uplift(db, ctx, bank, ceiling, methodology.parameters)
    effective_uplift = support_uplift_notches if support_uplift_notches > 0 else model_uplift
    (
        inputs,
        ratios,
        sovereign_holdings,
        capital_total,
        total_rwa,
        conservative_basis,
    ) = _rating_inputs(
        _RatingSources(
            facts=facts,
            capital=capital,
            liquidity=liquidity,
            irr=irr,
            fx=fx,
            operating_environment_score=environment,
            sovereign_ceiling=ceiling,
            parameters=methodology.parameters,
            annual_ratio_history=history,
            support_uplift_notches=effective_uplift,
            credit=credit,
        )
    )
    engine_methodology = _methodology(methodology.parameters)
    z_now = _systematic_factor(environment, methodology.parameters)
    pit = compute_rating(
        RatingInputs(**{**inputs.__dict__, "basis": "PIT", "systematic_factor": z_now}),
        engine_methodology,
    )
    ttc = compute_rating(
        RatingInputs(**{**inputs.__dict__, "basis": "TTC", "systematic_factor": z_now}),
        engine_methodology,
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
        "regulatory_metrics": {
            "capital": capital,
            "liquidity": liquidity,
            "irr": irr,
            "fx": fx,
            # None when no sealed credit baseline exists for the period - the
            # snapshot then proves the rating was scored on the proxies.
            "credit": credit,
        },
        "sovereign_rating": sovereign,
        "operating_environment": {"score": str(environment), **environment_source},
        "derived_ratios_pct": ratios,
        "conservative_income_basis": conservative_basis,
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
