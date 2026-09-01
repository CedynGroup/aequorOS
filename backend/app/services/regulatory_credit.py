"""Credit / Loan Book calculation module (credit PR-2; plan of 2026-09-01).

The ``credit`` module makes the loan book a first-class calculation module on
both computation tiers:

* **live** — ``compute_live`` classifies the current canonical LOAN book under
  the tenant's class grid (bank 5-grade / SDI NBFI 4-grade, every number from
  the regulatory-parameter control plane) and publishes portfolio-quality
  metrics plus limit findings to ``live_metrics`` / the alerts surface;
* **official** — ``run_all_credit_scenarios`` seals the same figures into an
  immutable ``RegulatoryRun(module="credit", scenario="baseline")`` with a
  value-based input hash, the provenance every filed credit figure will cite
  (the NPL-MONTHLY return registers against ``run:credit:baseline``).

The prudential anchor is BoG Notice BG/GOV/SEC/2025/23: the NPL ratio ceiling
(``npl_limit_pct``, 10%) and the immediate-restriction level
(``npl_restriction_level_pct``, 15%) resolve from the control plane — never a
literal — and an unseeded limit yields status ``na`` with a named validation,
not an invented threshold.

Orchestration only: classification itself stays in the pure engine
(``app.domain.capital.loan_classification``) via ``classify_loan_book``. The
snapshot both tiers hash is the ordered per-loan value list (reference,
exposure, DPD, stage, stated classification, stated provisions) plus every
resolved parameter — no row ids, no timestamps — so the live hash equals the
official hash whenever the underlying book and parameters are unchanged.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.errors import ModuleDataUnavailable
from app.models import (
    Bank,
    BankReportingPeriod,
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    RegulatoryMetricResult,
    RegulatoryRun,
    RegulatoryValidation,
)
from app.schemas.banks import BankRead, BankReportingPeriodRead
from app.schemas.regulatory_credit import (
    CreditDashboardRead,
    CreditFacetCountRead,
    CreditLoanFacetsRead,
    CreditLoanRead,
    CreditLoansPageRead,
    CreditMetricsRead,
    CreditScenarioBatchCreate,
    CreditValidationRead,
)
from app.schemas.regulatory_liquidity import RegulatoryRunBatchRead, RegulatoryRunRead
from app.schemas.sdi import (
    DelinquencyBucketRead,
    LoanGradeBucketRead,
    PortfolioAtRiskRead,
    ProvisionsHeldRead,
)
from app.services import filing_reconciliation
from app.services import regulatory_parameters as rp
from app.services.audit import record_event
from app.services.live_block import live_block
from app.services.live_state import current_fact_period_or_409
from app.services.live_types import LiveModuleResult, findings_from_validations
from app.services.loan_classification import LoanClassificationReport, classify_loan_book
from app.services.regulatory_liquidity import get_regulatory_run

ENGINE_VERSION = "regulatory-credit-v1.0.0"
INPUT_SCHEMA_VERSION = "credit-loans-v1"
OUTPUT_SCHEMA_VERSION = "credit-metrics-v1"
MODULE_CREDIT = "credit"
BASELINE_SCENARIO = "baseline"
CREDIT_RUN_SCENARIO_CODES = (BASELINE_SCENARIO,)

#: Notice BG/GOV/SEC/2025/23 prudential codes (seeded per institution class).
NPL_LIMIT_PARAM = "npl_limit_pct"
NPL_RESTRICTION_PARAM = "npl_dividend_restriction_pct"

#: The conventional watch band below the ceiling: amber from 80% of the limit.
#: A presentation convention (like the FX limit colouring), not a regulatory
#: number — the regulatory numbers are the two resolved parameters above.
_AMBER_FRACTION = Decimal("0.8")

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")

_INCLUDED_VALIDATION_STATUSES = ("accepted", "warning")


class CreditRunError(Exception):
    """Domain input failure persisted onto the run instead of raising HTTP 500."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------


def _analyse(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> tuple[LoanClassificationReport, Decimal | None, Decimal | None, str]:
    """Classify the book and resolve the Notice 2025/23 limits.

    Returns ``(report, npl_limit_pct, restriction_pct, npl_status)``. A book
    with zero loans raises ``ModuleDataUnavailable`` — a tenant that has not
    ingested a loan book has no credit position to report, which is the
    graceful-empty envelope, not an error.
    """
    report = classify_loan_book(db, ctx, bank, as_of)
    if report.loan_count == 0:
        raise ModuleDataUnavailable(
            error_code="no_loan_book",
            reason="No LOAN positions are in the current canonical book; the credit "
            "module has nothing to classify. Ingest the loan book through the Data Engine.",
        )
    limit = rp.try_resolve(db, bank, NPL_LIMIT_PARAM, as_of=as_of)
    restriction = rp.try_resolve(db, bank, NPL_RESTRICTION_PARAM, as_of=as_of)
    limit_pct = limit.decimal if limit is not None else None
    restriction_pct = restriction.decimal if restriction is not None else None
    ratio_pct = report.result.npl_ratio * _HUNDRED
    if limit_pct is None:
        npl_status = "na"
    elif (restriction_pct is not None and ratio_pct >= restriction_pct) or ratio_pct > limit_pct:
        npl_status = "red"
    elif ratio_pct >= limit_pct * _AMBER_FRACTION:
        npl_status = "amber"
    else:
        npl_status = "green"
    return report, limit_pct, restriction_pct, npl_status


def _validation_rows(
    report: LoanClassificationReport,
    limit_pct: Decimal | None,
    restriction_pct: Decimal | None,
) -> list[tuple[str, bool, str, str]]:
    """(rule_code, passed, severity, message) rows — the findings source."""
    ratio_pct = report.result.npl_ratio * _HUNDRED
    rows: list[tuple[str, bool, str, str]] = []
    if limit_pct is None:
        rows.append(
            (
                "npl_limit_resolved",
                False,
                "warning",
                f"The prudential NPL limit ({NPL_LIMIT_PARAM}) is not seeded for this "
                "institution class; the NPL ratio is reported without a compliance status.",
            )
        )
    else:
        breached = ratio_pct > limit_pct
        rows.append(
            (
                "npl_ratio_within_limit",
                not breached,
                "error",
                (
                    f"NPL ratio {ratio_pct:.2f}% is within the {limit_pct}% prudential limit."
                    if not breached
                    else f"NPL ratio {ratio_pct:.2f}% exceeds the {limit_pct}% prudential "
                    "limit (Notice BG/GOV/SEC/2025/23: notify the regulator within 10 "
                    "working days and submit a Board-approved reduction plan within 30 days)."
                ),
            )
        )
        if restriction_pct is not None:
            at_restriction = ratio_pct >= restriction_pct
            rows.append(
                (
                    "npl_ratio_below_restriction_level",
                    not at_restriction,
                    "error",
                    (
                        f"NPL ratio {ratio_pct:.2f}% is below the {restriction_pct}% "
                        "immediate-restriction level."
                        if not at_restriction
                        else f"NPL ratio {ratio_pct:.2f}% is at or above the "
                        f"{restriction_pct}% level at which dividend, bonus and "
                        "loan-growth restrictions apply immediately."
                    ),
                )
            )
    unclassified = report.result.unclassified_exposure_ghs
    rows.append(
        (
            "loan_book_fully_classified",
            report.unclassified_count == 0,
            "warning",
            (
                "Every loan carries a days-past-due or an IFRS 9 stage."
                if report.unclassified_count == 0
                else f"{report.unclassified_count} loan(s) ({unclassified} in exposure) "
                "carry neither a days-past-due nor an IFRS 9 stage; they are excluded "
                "from both the performing and the NPL legs — never booked performing."
            ),
        )
    )
    rows.append(
        (
            "provisions_held_stated",
            report.provisions_held is not None,
            "info",
            (
                "The book states held provisions; coverage is computable."
                if report.provisions_held is not None
                else "No loan states a held provision (ecl_provision_ghs), so provision "
                "coverage is unavailable — reported as unknown, never zero."
            ),
        )
    )
    if report.has_pending_parameters:
        rows.append(
            (
                "classification_parameters_confirmed",
                False,
                "warning",
                "Classification parameters awaiting confirmation: "
                + ", ".join(report.pending_parameters),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# snapshot / hash (value-based; shared by both tiers)
# ---------------------------------------------------------------------------


def _load_snapshot_rows(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> list[dict[str, Any]]:
    """Ordered per-loan value rows for the input hash (no ids, no timestamps)."""
    records = db.execute(
        select(CanonicalPositionSnapshot)
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date == as_of,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
            CanonicalPosition.position_type == "LOAN",
        )
        .order_by(CanonicalPositionSnapshot.source_reference)
    ).scalars()
    rows: list[dict[str, Any]] = []
    for snapshot in records:
        attributes = snapshot.attributes or {}
        rows.append(
            {
                "ref": snapshot.source_reference,
                "balance": str(snapshot.balance),
                "balance_ghs": _str_or_none(attributes.get("balance_ghs")),
                "dpd": _str_or_none(attributes.get("days_past_due")),
                "stage": snapshot.ifrs9_stage,
                "classification": _str_or_none(attributes.get("bog_classification")),
                "provision": _str_or_none(attributes.get("ecl_provision_ghs")),
                "suspense": _str_or_none(attributes.get("interest_in_suspense_ghs")),
            }
        )
    return rows


def _str_or_none(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def _build_snapshot(  # noqa: PLR0913 - mirrors the sibling modules' snapshot arity
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    scenario_code: str,
    report: LoanClassificationReport,
) -> dict[str, Any]:
    parameters = {item.param_code: str(item.value) for item in report.parameters}
    for code in (NPL_LIMIT_PARAM, NPL_RESTRICTION_PARAM):
        resolved = rp.try_resolve(db, bank, code, as_of=as_of)
        if resolved is not None:
            parameters[code] = str(resolved.decimal)
    return {
        "schema": INPUT_SCHEMA_VERSION,
        "bank_id": bank.id,
        "currency": bank.currency,
        "as_of": as_of.isoformat(),
        "scenario_code": scenario_code,
        "institution_class": report.institution_class,
        "parameters": dict(sorted(parameters.items())),
        "loans": _load_snapshot_rows(db, ctx, bank, as_of),
    }


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# live tier
# ---------------------------------------------------------------------------


def current_input_hash(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> str | None:
    """The baseline hash of the current canonical loan book for this period."""
    try:
        report = classify_loan_book(db, ctx, bank, period.period_end)
    except rp.RegulatoryParameterError:
        return None
    if report.loan_count == 0:
        return None
    return _snapshot_hash(
        _build_snapshot(db, ctx, bank, period.period_end, BASELINE_SCENARIO, report)
    )


def _live_metrics_payload(
    report: LoanClassificationReport,
    limit_pct: Decimal | None,
    restriction_pct: Decimal | None,
) -> dict[str, Any]:
    result = report.result
    metrics: dict[str, Any] = {
        "gross_loans_ghs": str(result.total_exposure_ghs),
        "loan_count": str(report.loan_count),
        "npl_exposure_ghs": str(result.npl_exposure_ghs),
        "npl_ratio_pct": str(result.npl_ratio * _HUNDRED),
        "total_provision_required_ghs": str(result.total_provision_required_ghs),
        "unclassified_exposure_ghs": str(result.unclassified_exposure_ghs),
        "grades": [
            {
                "grade": bucket.grade,
                "count": str(bucket.count),
                "exposure_ghs": str(bucket.exposure_ghs),
                "provision_required_ghs": str(bucket.provision_required_ghs),
                "non_performing": bucket.non_performing,
            }
            for bucket in result.buckets
        ],
    }
    for metric in report.portfolio_at_risk:
        metrics[f"{metric.code}_pct"] = str(metric.ratio * _HUNDRED)
    if limit_pct is not None:
        metrics["npl_limit_pct"] = str(limit_pct)
    if restriction_pct is not None:
        metrics["npl_restriction_level_pct"] = str(restriction_pct)
    if report.provisions_held is not None:
        metrics["provision_held_ghs"] = str(report.provisions_held.total_ghs)
        metrics["provision_specific_ghs"] = str(report.provisions_held.specific_ghs)
    if report.provision_coverage_pct is not None:
        metrics["provision_coverage_pct"] = str(report.provision_coverage_pct)
    return metrics


def compute_live(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> LiveModuleResult:
    """The live credit view over the current canonical book (no run written).

    The metrics mapping is a LITERAL (the authority gate reads the published
    keys from the AST). Figures that can be genuinely unavailable — the
    prudential limit on an unseeded class, provisions on an unstated book —
    carry an explicit ``None``: a stated unknown, never a fabricated zero.
    """
    as_of = period.period_end
    report, limit_pct, restriction_pct, npl_status = _analyse(db, ctx, bank, as_of)
    result = report.result
    par = {metric.code: metric.ratio * _HUNDRED for metric in report.portfolio_at_risk}
    held = report.provisions_held
    snapshot = _build_snapshot(db, ctx, bank, as_of, BASELINE_SCENARIO, report)
    findings = findings_from_validations(
        tuple(_validation_rows(report, limit_pct, restriction_pct)), npl_status
    )
    metrics = {
        "gross_loans_ghs": str(result.total_exposure_ghs),
        "loan_count": str(report.loan_count),
        "npl_exposure_ghs": str(result.npl_exposure_ghs),
        "npl_ratio_pct": str(result.npl_ratio * _HUNDRED),
        "npl_limit_pct": _opt(limit_pct),
        "npl_restriction_level_pct": _opt(restriction_pct),
        "total_provision_required_ghs": str(result.total_provision_required_ghs),
        "provision_held_ghs": _opt(held.total_ghs if held is not None else None),
        "provision_specific_ghs": _opt(held.specific_ghs if held is not None else None),
        "provision_coverage_pct": _opt(report.provision_coverage_pct),
        "unclassified_exposure_ghs": str(result.unclassified_exposure_ghs),
        "par_30_pct": _opt(par.get("par_30")),
        "par_60_pct": _opt(par.get("par_60")),
        "par_90_pct": _opt(par.get("par_90")),
        "par_180_pct": _opt(par.get("par_180")),
        "par_360_pct": _opt(par.get("par_360")),
        "grades": [
            {
                "grade": bucket.grade,
                "count": str(bucket.count),
                "exposure_ghs": str(bucket.exposure_ghs),
                "provision_required_ghs": str(bucket.provision_required_ghs),
                "non_performing": bucket.non_performing,
            }
            for bucket in result.buckets
        ],
    }
    return LiveModuleResult(
        metrics=metrics,
        status=npl_status,
        input_hash=_snapshot_hash(snapshot),
        engine_version=ENGINE_VERSION,
        findings=findings,
        source_as_of_date=as_of,
    )


def _opt(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


# ---------------------------------------------------------------------------
# official tier
# ---------------------------------------------------------------------------


def run_all_credit_scenarios(
    db: Session, ctx: TenantContext, bank_id: str, payload: CreditScenarioBatchCreate
) -> RegulatoryRunBatchRead:
    _require_actor(ctx)
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    filing_reconciliation.assert_filing_reconciled(
        db, ctx, bank, as_of=period.period_end, period_id=period.id, purpose="official_run"
    )
    runs = [
        _create_and_execute(db, ctx, bank, period, scenario_code)
        for scenario_code in CREDIT_RUN_SCENARIO_CODES
    ]
    return RegulatoryRunBatchRead(bank_id=bank.id, reporting_period_id=period.id, runs=runs)


def _create_and_execute(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    scenario_code: str,
) -> RegulatoryRunRead:
    report: LoanClassificationReport | None
    limit_pct: Decimal | None = None
    restriction_pct: Decimal | None = None
    npl_status = "na"
    try:
        report, limit_pct, restriction_pct, npl_status = _analyse(db, ctx, bank, period.period_end)
    except ModuleDataUnavailable as exc:
        report = None
        prefailure = CreditRunError(exc.error_code, exc.reason)
        snapshot: dict[str, Any] = {
            "schema": INPUT_SCHEMA_VERSION,
            "bank_id": bank.id,
            "as_of": period.period_end.isoformat(),
            "scenario_code": scenario_code,
            "loans": [],
        }
    else:
        prefailure = None
        snapshot = _build_snapshot(db, ctx, bank, period.period_end, scenario_code, report)

    run = RegulatoryRun(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        reporting_period_id=period.id,
        module=MODULE_CREDIT,
        scenario_code=scenario_code,
        status="queued",
        engine_version=ENGINE_VERSION,
        input_schema_version=INPUT_SCHEMA_VERSION,
        output_schema_version=OUTPUT_SCHEMA_VERSION,
        input_hash=_snapshot_hash(snapshot),
        inputs=snapshot,
        parameter_provenance=rp.consume_parameter_provenance(db),
        metrics={},
        created_by=ctx.actor_user_id,
    )
    db.add(run)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="regulatory_run.started",
        entity_type="regulatory_run",
        entity_id=run.id,
        details={
            "bank_id": str(bank.id),
            "reporting_period_id": str(period.id),
            "module": MODULE_CREDIT,
            "scenario_code": scenario_code,
            "input_hash": run.input_hash,
            "engine_version": ENGINE_VERSION,
        },
    )
    db.commit()

    run.status = "running"
    run.started_at = datetime.now(UTC)
    db.commit()

    try:
        if prefailure is not None:
            raise prefailure
        assert report is not None
        _persist_success(db, ctx, run, report, limit_pct, restriction_pct, npl_status)
    except CreditRunError as exc:
        _persist_failure(db, ctx, run.id, exc)
    except rp.RegulatoryParameterError as exc:
        _persist_failure(
            db,
            ctx,
            run.id,
            CreditRunError("missing_parameter", str(exc)),
        )
    except Exception as exc:  # noqa: BLE001 - a failed run is data, not a 500
        _persist_failure(db, ctx, run.id, CreditRunError("calculation_error", str(exc)))
    db.expire_all()
    return get_regulatory_run(db, ctx, bank.id, run.id)


def _persist_success(  # noqa: PLR0913 - one call site; the analysis tuple spread
    db: Session,
    ctx: TenantContext,
    run: RegulatoryRun,
    report: LoanClassificationReport,
    limit_pct: Decimal | None,
    restriction_pct: Decimal | None,
    npl_status: str,
) -> None:
    result = report.result
    ratio_pct = result.npl_ratio * _HUNDRED
    run.metrics = _live_metrics_payload(report, limit_pct, restriction_pct)

    par = {metric.code: metric.ratio * _HUNDRED for metric in report.portfolio_at_risk}
    metric_rows: list[tuple[str, Decimal, str, Decimal | None, str]] = [
        ("npl_ratio_pct", ratio_pct, "pct", limit_pct, npl_status),
        ("gross_loans_ghs", result.total_exposure_ghs, "ghs", None, "na"),
        ("npl_exposure_ghs", result.npl_exposure_ghs, "ghs", None, "na"),
        ("total_provision_required_ghs", result.total_provision_required_ghs, "ghs", None, "na"),
        ("par_30_pct", par.get("par_30", _ZERO), "pct", None, "na"),
        ("par_90_pct", par.get("par_90", _ZERO), "pct", None, "na"),
    ]
    if report.provisions_held is not None:
        metric_rows.append(
            ("provision_held_ghs", report.provisions_held.total_ghs, "ghs", None, "na")
        )
    if report.provision_coverage_pct is not None:
        metric_rows.append(
            ("provision_coverage_pct", report.provision_coverage_pct, "pct", None, "na")
        )
    for position, (code, value, unit, threshold_min, metric_status) in enumerate(
        metric_rows, start=1
    ):
        db.add(
            RegulatoryMetricResult(
                organization_id=run.organization_id,
                bank_id=run.bank_id,
                run_id=run.id,
                metric_code=code,
                metric_value=value,
                unit=unit,
                threshold_min=threshold_min,
                status=metric_status,
                position=position,
            )
        )
    for position, (rule_code, passed, severity, message) in enumerate(
        _validation_rows(report, limit_pct, restriction_pct), start=1
    ):
        db.add(
            RegulatoryValidation(
                organization_id=run.organization_id,
                bank_id=run.bank_id,
                run_id=run.id,
                rule_code=rule_code,
                passed=passed,
                severity=severity,
                message=message,
                position=position,
            )
        )
    run.status = "succeeded"
    run.completed_at = datetime.now(UTC)
    record_event(
        db,
        ctx,
        event_type="regulatory_run.succeeded",
        entity_type="regulatory_run",
        entity_id=run.id,
        details={"module": MODULE_CREDIT, "npl_ratio_pct": str(ratio_pct)},
    )
    db.commit()


def _persist_failure(db: Session, ctx: TenantContext, run_id: UUID, error: CreditRunError) -> None:
    db.rollback()
    run = db.get(RegulatoryRun, run_id)
    if run is None:  # pragma: no cover - the queued commit made it durable
        return
    run.status = "failed"
    run.completed_at = datetime.now(UTC)
    run.error_code = error.code
    run.error_details = {"message": error.message, **(error.details or {})}
    record_event(
        db,
        ctx,
        event_type="regulatory_run.failed",
        entity_type="regulatory_run",
        entity_id=run.id,
        details={"module": MODULE_CREDIT, "error_code": error.code},
    )
    db.commit()


# ---------------------------------------------------------------------------
# dashboard read
# ---------------------------------------------------------------------------


def get_credit_dashboard(
    db: Session, ctx: TenantContext, bank_id: str, reporting_period_id: UUID | None = None
) -> CreditDashboardRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = (
        current_fact_period_or_409(db, ctx, bank, MODULE_CREDIT)
        if reporting_period_id is None
        else _get_period_or_404(db, ctx, bank, reporting_period_id)
    )
    latest_run = (
        _latest_succeeded_baseline_run(db, ctx, bank, period.id)
        if reporting_period_id is not None
        else None
    )
    # The dashboard always classifies inline: the classification read is cheap,
    # and the stored run's role here is provenance (latest_run_id + stored flag)
    # for the period view, mirroring how the SDI diagnostics read behaves.
    report, limit_pct, restriction_pct, npl_status = _analyse(db, ctx, bank, period.period_end)
    result = report.result
    return CreditDashboardRead(
        bank=BankRead.model_validate(bank, from_attributes=True),
        period=BankReportingPeriodRead.model_validate(period, from_attributes=True),
        stored=latest_run is not None,
        latest_run_id=latest_run.id if latest_run is not None else None,
        institution_class=report.institution_class,
        metrics=CreditMetricsRead(
            gross_loans_ghs=result.total_exposure_ghs,
            loan_count=report.loan_count,
            npl_exposure_ghs=result.npl_exposure_ghs,
            npl_ratio_pct=result.npl_ratio * _HUNDRED,
            npl_limit_pct=limit_pct,
            npl_restriction_level_pct=restriction_pct,
            npl_status=npl_status,  # type: ignore[arg-type]
            total_provision_required_ghs=result.total_provision_required_ghs,
            provisions_held=(
                ProvisionsHeldRead(
                    specific_ghs=report.provisions_held.specific_ghs,
                    general_ghs=report.provisions_held.general_ghs,
                    total_ghs=report.provisions_held.total_ghs,
                    interest_in_suspense_ghs=report.provisions_held.interest_in_suspense_ghs,
                    stated_loan_count=report.provisions_held.stated_loan_count,
                )
                if report.provisions_held is not None
                else None
            ),
            provision_coverage_pct=report.provision_coverage_pct,
            unclassified_exposure_ghs=result.unclassified_exposure_ghs,
            unclassified_count=report.unclassified_count,
            stage_proxy_count=report.stage_proxy_count,
            dpd_covered_count=report.dpd_covered_count,
        ),
        grades=[
            LoanGradeBucketRead(
                grade=bucket.grade,
                count=bucket.count,
                exposure_ghs=bucket.exposure_ghs,
                provision_required_ghs=bucket.provision_required_ghs,
                non_performing=bucket.non_performing,
            )
            for bucket in result.buckets
        ],
        delinquency_buckets=[
            DelinquencyBucketRead(
                code=bucket.code,
                label=bucket.label,
                count=bucket.count,
                exposure_ghs=bucket.exposure_ghs,
            )
            for bucket in report.delinquency_buckets
        ],
        portfolio_at_risk=[
            PortfolioAtRiskRead(
                code=metric.code,
                label=metric.label,
                exposure_ghs=metric.exposure_ghs,
                ratio=metric.ratio,
            )
            for metric in report.portfolio_at_risk
        ],
        validations=[
            CreditValidationRead(
                rule_code=rule_code,
                passed=passed,
                severity=severity,  # type: ignore[arg-type]
                message=message,
            )
            for rule_code, passed, severity, message in _validation_rows(
                report, limit_pct, restriction_pct
            )
        ],
        pending_parameters=list(report.pending_parameters),
        live=live_block(db, ctx, bank.id, MODULE_CREDIT),
    )


# ---------------------------------------------------------------------------
# loan blotter
# ---------------------------------------------------------------------------


def list_credit_loans(  # noqa: PLR0913 - one keyword per blotter filter
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
    grade: str | None = None,
    product: str | None = None,
    branch: str | None = None,
    q: str | None = None,
) -> CreditLoansPageRead:
    """The classified loan blotter, filtered and paged.

    The whole current-generation LOAN book is loaded and classified in memory —
    the same cost every classification read already pays — because the grade is
    a DERIVED value (DPD × the tenant's grid) that no portable SQL predicate can
    compute, and the per-tenant loan book is orders of magnitude smaller than
    the full position blotter. Revisit with SQL-side classification only if a
    tenant's LOAN book alone approaches the six-figure row counts that made
    ``/positions`` server-paginated.
    """
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = current_fact_period_or_409(db, ctx, bank, MODULE_CREDIT)
    as_of = period.period_end
    rows = _classified_loan_rows(db, ctx, bank, as_of)
    total = len(rows)
    needle = (q or "").strip().lower()
    filtered_rows = [
        row
        for row in rows
        if (grade is None or row.grade == grade)
        and (product is None or row.product_code == product)
        and (branch is None or row.branch_id == branch)
        and (
            not needle
            or needle in row.source_reference.lower()
            or needle in (row.counterparty_name or "").lower()
        )
    ]
    page = filtered_rows[offset : offset + limit]
    return CreditLoansPageRead(
        as_of=as_of.isoformat(),
        total=total,
        filtered=len(filtered_rows),
        limit=limit,
        offset=offset,
        rows=page,
    )


def get_credit_loan_facets(db: Session, ctx: TenantContext, bank_id: str) -> CreditLoanFacetsRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = current_fact_period_or_409(db, ctx, bank, MODULE_CREDIT)
    rows = _classified_loan_rows(db, ctx, bank, period.period_end)

    def counts(values: list[str | None]) -> list[CreditFacetCountRead]:
        tally: dict[str, int] = {}
        for value in values:
            if value:
                tally[value] = tally.get(value, 0) + 1
        return [
            CreditFacetCountRead(value=value, count=count) for value, count in sorted(tally.items())
        ]

    return CreditLoanFacetsRead(
        as_of=period.period_end.isoformat(),
        grades=counts([row.grade for row in rows]),
        products=counts([row.product_code for row in rows]),
        branches=counts([row.branch_id for row in rows]),
        sectors=counts([row.sector for row in rows]),
    )


def _classified_loan_rows(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> list[CreditLoanRead]:
    report = classify_loan_book(db, ctx, bank, as_of)
    if report.loan_count == 0:
        raise ModuleDataUnavailable(
            error_code="no_loan_book",
            reason="No LOAN positions are in the current canonical book.",
        )
    records = db.execute(
        select(
            CanonicalPositionSnapshot, CanonicalPosition, CanonicalCounterparty, CanonicalProduct
        )
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .outerjoin(
            CanonicalCounterparty,
            CanonicalPositionSnapshot.counterparty_id == CanonicalCounterparty.id,
        )
        .outerjoin(CanonicalProduct, CanonicalPositionSnapshot.product_id == CanonicalProduct.id)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date == as_of,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
            CanonicalPosition.position_type == "LOAN",
        )
        .order_by(CanonicalPositionSnapshot.source_reference)
    ).all()
    # The report's per-loan classifications are positional over the SAME ordered
    # slice (classify_loan_book orders by source_reference too), so zip is safe;
    # a count mismatch would mean the two queries diverged and must fail loud.
    loans = report.result.loans
    if len(loans) != len(records):
        msg = (
            f"classified {len(loans)} loans but the blotter query returned "
            f"{len(records)} — the two canonical slices diverged"
        )
        raise RuntimeError(msg)
    rows: list[CreditLoanRead] = []
    for classified, (snapshot, position, counterparty, product_row) in zip(
        loans, records, strict=True
    ):
        attributes = snapshot.attributes or {}
        rows.append(
            CreditLoanRead(
                source_reference=snapshot.source_reference,
                counterparty_name=counterparty.name if counterparty is not None else None,
                product_code=product_row.product_code if product_row is not None else None,
                branch_id=_str_or_none(attributes.get("branch_id")),
                sector=_str_or_none(attributes.get("sector")),
                currency=position.currency,
                exposure_ghs=classified.exposure_ghs,
                days_past_due=_int_or_none(attributes.get("days_past_due")),
                ifrs9_stage=snapshot.ifrs9_stage,
                grade=classified.grade,
                non_performing=classified.non_performing,
                classification_basis=classified.classification_basis,
                provision_required_ghs=classified.provision_required_ghs,
                provision_held_ghs=_dec_or_none(attributes.get("ecl_provision_ghs")),
                interest_rate=(
                    Decimal(str(snapshot.interest_rate))
                    if snapshot.interest_rate is not None
                    else None
                ),
                contractual_maturity=(
                    snapshot.contractual_maturity.isoformat()
                    if snapshot.contractual_maturity is not None
                    else None
                ),
                origination_date=(
                    position.origination_date.isoformat()
                    if position.origination_date is not None
                    else None
                ),
            )
        )
    return rows


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(Decimal(str(value)))
    except ArithmeticError:
        return None
    return parsed if parsed >= 0 else None


def _dec_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


# ---------------------------------------------------------------------------
# shared private helpers (module-local by convention — every module has its own)
# ---------------------------------------------------------------------------


def _latest_succeeded_baseline_run(
    db: Session, ctx: TenantContext, bank: Bank, reporting_period_id: UUID
) -> RegulatoryRun | None:
    return db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == reporting_period_id,
            RegulatoryRun.module == MODULE_CREDIT,
            RegulatoryRun.scenario_code == BASELINE_SCENARIO,
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
        .limit(1)
    )


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.organization_id == ctx.organization_id, Bank.id == bank_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found")
    return bank


def _get_period_or_404(
    db: Session, ctx: TenantContext, bank: Bank, reporting_period_id: UUID
) -> BankReportingPeriod:
    period = db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
            BankReportingPeriod.id == reporting_period_id,
        )
    )
    if period is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reporting period not found"
        )
    return period


def _require_actor(ctx: TenantContext) -> None:
    if ctx.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="An acting user is required to mint official runs",
        )
