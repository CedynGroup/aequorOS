"""Package generation (docs/regulatory_reporting.md §5, ``generation.py``).

Generators pull ONLY from existing computed state — module previews and
succeeded ``RegulatoryRun`` snapshots — and never recompute engine outputs.
Every generated snapshot embeds the values that will be exported plus
``source_runs`` ({module, run_id, input_hash, engine_version}) so each number
traces back through the lineage substrate.

``generate_package`` mints the immutable package row: status ``generated``,
version = prior version + 1, and the prior current version (if any) flips to
``superseded`` in the same transaction. Regeneration never mutates a snapshot.

Snapshot shape (``regulatory-package-v1``): sections are
``{code, title, optional, rows: [{code, description, value, ...}], total}``
where a ``total`` carrying ``equals_sum_of_rows=True`` declares that the
validation pipeline must cross-foot it against the row values. Top-level
``totals`` rows are the stable headline figures the prior-period movement
check compares across reporting dates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod, RegulatoryPackage, RegulatoryRun
from app.schemas.regulatory_liquidity import Bsd3SummaryRowRead
from app.schemas.regulatory_reporting import RegulatoryPackageCreate, RegulatoryPackageRead
from app.services import regulatory_capital, regulatory_liquidity
from app.services.audit import record_event
from app.services.regulatory_reporting.common import (
    get_bank_or_404,
    get_period_for_reporting_date_or_404,
    read_package,
    require_actor,
)
from app.services.regulatory_reporting.registry import REGISTRY, ReturnDefinition

SNAPSHOT_SCHEMA_VERSION = "regulatory-package-v1"
BASELINE_SCENARIO = "baseline"

MODULE_LIQUIDITY = "liquidity"
MODULE_CAPITAL = "capital"
MODULE_IRR = "irr"
MODULE_FX = "fx"
MODULE_FORECAST = "forecast"

_FORECAST_SUMMARY_FIELDS = (
    "avg_roe_pct",
    "year5_car_pct",
    "year5_lcr_pct",
    "year5_nsfr_pct",
    "cumulative_net_income",
    "min_car_pct",
    "min_lcr_pct",
    "min_nsfr_pct",
)


@dataclass(frozen=True)
class GeneratedReturn:
    """One generator output: the export-ready snapshot + its source runs."""

    snapshot: dict[str, Any]
    source_runs: list[dict[str, Any]]


def generate_package(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: RegulatoryPackageCreate
) -> RegulatoryPackageRead:
    actor_user_id = require_actor(ctx)
    bank = get_bank_or_404(db, ctx, bank_id)
    definition = REGISTRY.get(payload.return_code)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Return code '{payload.return_code}' is not registered. "
                "List the available templates via the return-template endpoint."
            ),
        )
    period = get_period_for_reporting_date_or_404(db, ctx, bank, payload.reporting_date)

    generated = _GENERATORS[definition.generator](db, ctx, bank, period, definition)

    prior_current = db.scalar(
        select(RegulatoryPackage).where(
            RegulatoryPackage.organization_id == ctx.organization_id,
            RegulatoryPackage.bank_id == bank.id,
            RegulatoryPackage.return_code == definition.code,
            RegulatoryPackage.reporting_date == payload.reporting_date,
            RegulatoryPackage.status != "superseded",
        )
    )
    prior_version = db.scalar(
        select(RegulatoryPackage.version)
        .where(
            RegulatoryPackage.organization_id == ctx.organization_id,
            RegulatoryPackage.bank_id == bank.id,
            RegulatoryPackage.return_code == definition.code,
            RegulatoryPackage.reporting_date == payload.reporting_date,
        )
        .order_by(RegulatoryPackage.version.desc())
        .limit(1)
    )
    if prior_current is not None:
        prior_current.status = "superseded"
        record_event(
            db,
            ctx,
            event_type="regulatory_package.superseded",
            entity_type="regulatory_package",
            entity_id=prior_current.id,
            details={
                "return_code": definition.code,
                "reporting_date": payload.reporting_date.isoformat(),
                "version": prior_current.version,
            },
        )
        db.flush()

    package = RegulatoryPackage(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        return_family=definition.family,
        return_code=definition.code,
        reporting_date=payload.reporting_date,
        frequency=definition.frequency,
        status="generated",
        version=(prior_version or 0) + 1,
        supersedes_id=prior_current.id if prior_current is not None else None,
        snapshot=generated.snapshot,
        source_runs=generated.source_runs,
        validation_report=None,
        generated_by=actor_user_id,
        generated_at=datetime.now(UTC),
        notes=payload.notes,
    )
    db.add(package)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="regulatory_package.generated",
        entity_type="regulatory_package",
        entity_id=package.id,
        details={
            "bank_id": str(bank.id),
            "return_code": definition.code,
            "return_family": definition.family,
            "reporting_date": payload.reporting_date.isoformat(),
            "version": package.version,
            "supersedes_id": (
                str(prior_current.id) if prior_current is not None else None
            ),
            "source_runs": [entry["run_id"] for entry in generated.source_runs],
        },
    )
    db.commit()
    return read_package(db, package)


def _row(code: str, description: str, value: Any, **extra: Any) -> dict[str, Any]:
    return {"code": code, "description": description, "value": str(value), **extra}


def _total(
    code: str, description: str, value: Any, *, equals_sum_of_rows: bool = False
) -> dict[str, Any]:
    return {
        "code": code,
        "description": description,
        "value": str(value),
        "equals_sum_of_rows": equals_sum_of_rows,
    }


def _section(
    code: str,
    title: str,
    rows: list[dict[str, Any]],
    total: dict[str, Any] | None = None,
    *,
    optional: bool = False,
) -> dict[str, Any]:
    return {"code": code, "title": title, "optional": optional, "rows": rows, "total": total}


def _summary_total(row: Bsd3SummaryRowRead, code: str, *, equals_sum: bool) -> dict[str, Any]:
    return _total(code, row.description, row.value, equals_sum_of_rows=equals_sum)


def _envelope(  # noqa: PLR0913
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
    sections: list[dict[str, Any]],
    totals: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "return_code": definition.code,
        "return_family": definition.family,
        "regulator": definition.regulator,
        "template_id": definition.template_id,
        "fidelity": definition.fidelity,
        "reporting_date": period.period_end.isoformat(),
        "institution": {
            "bank_id": str(bank.id),
            "name": bank.name,
            "short_name": bank.short_name,
            "currency": bank.currency,
            "jurisdiction_code": bank.jurisdiction_code,
            "license_type": bank.license_type,
        },
        "reporting_period": {
            "id": str(period.id),
            "label": period.label,
            "period_start": period.period_start.isoformat(),
            "period_end": period.period_end.isoformat(),
        },
        "sections": sections,
        "totals": totals,
        "metadata": {"generated_at": datetime.now(UTC).isoformat(), **metadata},
    }


def _source_run_entry(run: RegulatoryRun) -> dict[str, Any]:
    return {
        "module": run.module,
        "run_id": str(run.id),
        "input_hash": run.input_hash,
        "engine_version": run.engine_version,
    }


def _latest_succeeded_runs_by_scenario(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod, module: str
) -> dict[str, RegulatoryRun]:
    runs = db.scalars(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == period.id,
            RegulatoryRun.module == module,
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
    )
    latest: dict[str, RegulatoryRun] = {}
    for run in runs:
        latest.setdefault(run.scenario_code, run)
    return latest


def _baseline_run_or_409(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    module: str,
    *,
    artifact: str,
) -> RegulatoryRun:
    run = db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == period.id,
            RegulatoryRun.module == module,
            RegulatoryRun.scenario_code == BASELINE_SCENARIO,
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
        .limit(1)
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "no_baseline_run",
                "message": (
                    f"A successful baseline {module} run is required before {artifact} "
                    "can be generated for this reporting period. Run the engine first."
                ),
            },
        )
    return run


def _generate_liquidity(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    preview = regulatory_liquidity.get_bsd3_preview(db, ctx, bank.id, period.id)
    summary = {row.row_code: row for row in preview.summary_rows}
    sections = [
        _section(
            "hqla",
            "High Quality Liquid Assets",
            [_row(row.row_code, row.description, row.amount) for row in preview.hqla_rows],
            _summary_total(summary["3.0"], "hqla_total_ghs", equals_sum=True),
        ),
        _section(
            "outflows",
            "Cash Outflows (30 days)",
            [
                _row(
                    row.row_code,
                    row.description,
                    row.weighted_amount,
                    balance=str(row.balance),
                    rate_pct=str(row.rate_pct),
                )
                for row in preview.outflow_rows
            ],
            _summary_total(summary["5.0"], "total_outflows_ghs", equals_sum=True),
        ),
        _section(
            "inflows",
            "Cash Inflows (30 days)",
            [
                _row(
                    row.row_code,
                    row.description,
                    row.weighted_amount,
                    balance=str(row.balance),
                    rate_pct=str(row.rate_pct),
                )
                for row in preview.inflow_rows
            ],
            _summary_total(summary["7.0"], "capped_inflows_ghs", equals_sum=False),
        ),
        _section(
            "lcr_summary",
            "Liquidity Coverage Ratio Summary",
            [
                _row(row.row_code, row.description, row.value, unit=row.unit)
                for row in preview.summary_rows
            ],
        ),
        _section(
            "nsfr_asf",
            "Available Stable Funding",
            [
                _row(
                    row.row_code,
                    row.description,
                    row.weighted_amount,
                    balance=str(row.balance),
                    rate_pct=str(row.rate_pct),
                )
                for row in preview.nsfr.asf_rows
            ],
            _summary_total(preview.nsfr.asf_total, "asf_total_ghs", equals_sum=True),
        ),
        _section(
            "nsfr_rsf",
            "Required Stable Funding",
            [
                _row(
                    row.row_code,
                    row.description,
                    row.weighted_amount,
                    balance=str(row.balance),
                    rate_pct=str(row.rate_pct),
                )
                for row in preview.nsfr.rsf_rows
            ],
            _summary_total(preview.nsfr.rsf_total, "rsf_total_ghs", equals_sum=True),
        ),
        _section(
            "nsfr_summary",
            "Net Stable Funding Ratio Summary",
            [
                _row(
                    preview.nsfr.nsfr_ratio.row_code,
                    preview.nsfr.nsfr_ratio.description,
                    preview.nsfr.nsfr_ratio.value,
                    unit=preview.nsfr.nsfr_ratio.unit,
                )
            ],
        ),
    ]
    totals = [
        _row("hqla_total_ghs", summary["3.0"].description, summary["3.0"].value, unit="ghs"),
        _row("total_outflows_ghs", summary["5.0"].description, summary["5.0"].value, unit="ghs"),
        _row(
            "net_outflows_30d_ghs", summary["8.0"].description, summary["8.0"].value, unit="ghs"
        ),
        _row("lcr_pct", summary["9.0"].description, summary["9.0"].value, unit="pct"),
        _row(
            "asf_total_ghs",
            preview.nsfr.asf_total.description,
            preview.nsfr.asf_total.value,
            unit="ghs",
        ),
        _row(
            "rsf_total_ghs",
            preview.nsfr.rsf_total.description,
            preview.nsfr.rsf_total.value,
            unit="ghs",
        ),
        _row(
            "nsfr_pct",
            preview.nsfr.nsfr_ratio.description,
            preview.nsfr.nsfr_ratio.value,
            unit="pct",
        ),
    ]
    metadata = {
        "form_code": preview.header.form_code,
        "form_title": preview.header.form_title,
        "regulator_name": preview.header.regulator,
        "preview_note": preview.header.preview_note,
        "baseline_run_id": str(preview.run_id),
        "engine_validations": [item.model_dump(mode="json") for item in preview.validations],
    }
    runs = _latest_succeeded_runs_by_scenario(db, ctx, bank, period, MODULE_LIQUIDITY)
    return GeneratedReturn(
        snapshot=_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[_source_run_entry(run) for _, run in sorted(runs.items())],
    )


def _generate_capital(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    preview = regulatory_capital.get_bsd2_preview(db, ctx, bank.id, period.id)
    cet1_rows = [
        _row(row.row_code, row.description, row.amount)
        for row in (*preview.cet1_rows, *preview.deduction_rows)
    ]
    sections = [
        _section(
            "cet1",
            "Common Equity Tier 1 (components and deductions)",
            cet1_rows,
            _total(
                "cet1_total_ghs",
                preview.cet1_total.description,
                preview.cet1_total.value,
                equals_sum_of_rows=True,
            ),
        ),
        _section(
            "at1",
            "Additional Tier 1 Capital",
            [_row(row.row_code, row.description, row.amount) for row in preview.at1_rows],
            optional=True,
        ),
        _section(
            "tier2",
            "Tier 2 Capital",
            [_row(row.row_code, row.description, row.amount) for row in preview.tier2_rows],
            optional=True,
        ),
        _section(
            "credit_rwa",
            "Credit Risk-Weighted Assets",
            [
                _row(
                    row.row_code,
                    row.description,
                    row.weighted_amount,
                    balance=str(row.balance),
                    rate_pct=str(row.rate_pct),
                )
                for row in preview.credit_rwa_rows
            ],
        ),
        _section(
            "market_rwa",
            "Market Risk-Weighted Assets",
            [
                _row(
                    row.row_code,
                    row.description,
                    row.weighted_amount,
                    balance=str(row.balance),
                    rate_pct=str(row.rate_pct),
                )
                for row in preview.market_rwa_rows
            ],
            optional=True,
        ),
        _section(
            "operational_rwa",
            "Operational Risk-Weighted Assets",
            [
                _row(
                    row.row_code,
                    row.description,
                    row.weighted_amount,
                    balance=str(row.balance),
                    rate_pct=str(row.rate_pct),
                )
                for row in preview.operational_rwa_rows
            ],
            optional=True,
        ),
        _section(
            "capital_ratios",
            "Capital Adequacy Ratios",
            [
                _row(
                    row.row_code,
                    row.description,
                    row.value_pct,
                    unit="pct",
                    minimum_pct=str(row.minimum_pct),
                    passed=row.passed,
                )
                for row in preview.ratio_rows
            ],
        ),
    ]
    totals = [
        _row(
            "cet1_total_ghs", preview.cet1_total.description, preview.cet1_total.value, unit="ghs"
        ),
        _row(
            "tier1_total_ghs",
            preview.tier1_total.description,
            preview.tier1_total.value,
            unit="ghs",
        ),
        _row(
            "total_capital_ghs",
            preview.total_capital.description,
            preview.total_capital.value,
            unit="ghs",
        ),
        _row("total_rwa_ghs", preview.total_rwa.description, preview.total_rwa.value, unit="ghs"),
    ]
    metadata = {
        "form_code": preview.header.form_code,
        "form_title": preview.header.form_title,
        "regulator_name": preview.header.regulator,
        "preview_note": preview.header.preview_note,
        "baseline_run_id": str(preview.run_id),
        "engine_validations": [item.model_dump(mode="json") for item in preview.validations],
    }
    runs = _latest_succeeded_runs_by_scenario(db, ctx, bank, period, MODULE_CAPITAL)
    return GeneratedReturn(
        snapshot=_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[_source_run_entry(run) for _, run in sorted(runs.items())],
    )


def _generate_irrbb(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    run = _baseline_run_or_409(
        db, ctx, bank, period, MODULE_IRR, artifact="the IRRBB pilot return"
    )
    metrics = run.metrics
    gap_rows = [
        _row(
            str(bucket["bucket"]),
            f"Repricing gap {bucket['bucket']}",
            bucket["gap_ghs"],
            rsa_ghs=str(bucket["rsa_ghs"]),
            rsl_ghs=str(bucket["rsl_ghs"]),
            cumulative_gap_ghs=str(bucket["cumulative_gap_ghs"]),
            within_12m=bool(bucket["within_12m"]),
        )
        for bucket in metrics.get("gap_buckets", [])
    ]
    eve_rows = [
        _row(
            str(scenario["scenario_code"]),
            f"ΔEVE under {scenario['scenario_code']}",
            scenario["delta_eve_ghs"],
            eve_ghs=str(scenario["eve_ghs"]),
            delta_eve_pct_tier1=str(scenario["delta_eve_pct_tier1"]),
            breach=bool(scenario["breach"]),
        )
        for scenario in metrics.get("eve_by_scenario", [])
    ]
    ear_rows = [
        _row("ear_up_200_ghs", "Earnings at risk, +200 bp parallel", metrics["ear_up_200_ghs"]),
        _row(
            "ear_down_200_ghs", "Earnings at risk, -200 bp parallel", metrics["ear_down_200_ghs"]
        ),
        _row("nii_base_ghs", "Base net interest income", metrics["nii_base_ghs"]),
    ]
    summary_rows = [
        _row("eve_base_ghs", "Economic value of equity (base)", metrics["eve_base_ghs"]),
        _row(
            "worst_eve_change_ghs",
            f"Worst-case EVE change ({metrics['worst_scenario']})",
            metrics["worst_eve_change_ghs"],
        ),
        _row(
            "worst_eve_change_pct_tier1",
            "Worst-case EVE change as % of Tier 1",
            metrics["worst_eve_change_pct_tier1"],
        ),
        _row("duration_gap", "Duration gap (years)", metrics["duration_gap"]),
        _row("tier1_ghs", "Tier 1 capital", metrics["tier1_ghs"]),
    ]
    sections = [
        _section("repricing_gap", "Repricing Gap by Bucket", gap_rows),
        _section("eve_scenarios", "ΔEVE by Supervisory Shock", eve_rows),
        _section("earnings_at_risk", "ΔNII / Earnings at Risk", ear_rows),
        _section("summary", "IRRBB Summary", summary_rows),
    ]
    totals = [
        _row("eve_base_ghs", "Economic value of equity (base)", metrics["eve_base_ghs"]),
        _row("worst_eve_change_ghs", "Worst-case EVE change", metrics["worst_eve_change_ghs"]),
        _row(
            "worst_eve_change_pct_tier1",
            "Worst-case EVE change as % of Tier 1",
            metrics["worst_eve_change_pct_tier1"],
        ),
        _row(
            "cumulative_12m_gap_ghs",
            "Cumulative 12-month repricing gap",
            metrics["cumulative_12m_gap_ghs"],
        ),
        _row("tier1_ghs", "Tier 1 capital", metrics["tier1_ghs"]),
    ]
    metadata = {
        "worst_scenario": metrics.get("worst_scenario"),
        "eve_limit_pct": metrics.get("eve_limit_pct"),
        "baseline_run_id": str(run.id),
    }
    runs = _latest_succeeded_runs_by_scenario(db, ctx, bank, period, MODULE_IRR)
    return GeneratedReturn(
        snapshot=_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[_source_run_entry(item) for _, item in sorted(runs.items())],
    )


def _generate_fx(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    run = _baseline_run_or_409(
        db, ctx, bank, period, MODULE_FX, artifact="the Net Open Position return"
    )
    metrics = run.metrics
    position_rows = [
        _row(
            str(currency["currency"]),
            f"Net open position in {currency['currency']}",
            currency["net_ghs"],
            side=str(currency["side"]),
            net_ccy=str(currency["net_ccy"]),
            spot_ghs=str(currency["spot_ghs"]),
            abs_pct_tier1=str(currency["abs_pct_tier1"]),
            within_single_limit=bool(currency["within_single_limit"]),
        )
        for currency in metrics.get("currencies", [])
    ]
    var_rows = [
        _row(
            str(item["currency"]),
            f"Standalone VaR for {item['currency']}",
            item["standalone_var_ghs"],
            net_ghs=str(item["net_ghs"]),
        )
        for item in metrics.get("standalone_vars", [])
    ]
    hedge_rows = [
        _row(
            str(hedge["hedge_id"]),
            f"{hedge['instrument']} hedge on {hedge['pair']}",
            hedge["mtm_ghs"],
            prospective_r2_pct=str(hedge["prospective_r2_pct"]),
            dollar_offset_pct=str(hedge["dollar_offset_pct"]),
            effective=bool(hedge["effective"]),
        )
        for hedge in metrics.get("hedges", [])
    ]
    scenario_rows = [
        _row(
            str(scenario["scenario_code"]),
            f"NOP under {scenario['scenario_code']}",
            scenario["nop_ghs"],
            shock_pct=str(scenario["shock_pct"]),
            nop_pct_tier1=str(scenario["nop_pct_tier1"]),
            within_aggregate_limit=bool(scenario["within_aggregate_limit"]),
        )
        for scenario in metrics.get("nop_by_scenario", [])
    ]
    summary_rows = [
        _row("nop_ghs", "Aggregate net open position", metrics["nop_ghs"]),
        _row("nop_pct_tier1", "Aggregate NOP as % of Tier 1", metrics["nop_pct_tier1"]),
        _row("sum_long_ghs", "Sum of long positions", metrics["sum_long_ghs"]),
        _row("sum_short_ghs", "Sum of short positions", metrics["sum_short_ghs"]),
        _row("var_99_1d_ghs", "Portfolio VaR (99%, 1-day)", metrics["var_99_1d_ghs"]),
        _row("stressed_var_ghs", "Stressed VaR (cedi crisis)", metrics["stressed_var_ghs"]),
        _row("tier1_ghs", "Tier 1 capital", metrics["tier1_ghs"]),
    ]
    sections = [
        _section("currency_positions", "Net Open Position by Currency", position_rows),
        _section("standalone_var", "Standalone Value at Risk by Currency", var_rows),
        _section("hedges", "Hedge Effectiveness", hedge_rows, optional=True),
        _section("scenario_nop", "NOP under Depreciation Scenarios", scenario_rows),
        _section("nop_summary", "Net Open Position Summary", summary_rows),
    ]
    totals = [
        _row("nop_ghs", "Aggregate net open position", metrics["nop_ghs"]),
        _row("nop_pct_tier1", "Aggregate NOP as % of Tier 1", metrics["nop_pct_tier1"]),
        _row("var_99_1d_ghs", "Portfolio VaR (99%, 1-day)", metrics["var_99_1d_ghs"]),
        _row("tier1_ghs", "Tier 1 capital", metrics["tier1_ghs"]),
    ]
    metadata = {
        "single_ccy_max_currency": metrics.get("single_ccy_max_currency"),
        "nop_single_limit_pct": metrics.get("nop_single_limit_pct"),
        "nop_aggregate_limit_pct": metrics.get("nop_aggregate_limit_pct"),
        "baseline_run_id": str(run.id),
    }
    runs = _latest_succeeded_runs_by_scenario(db, ctx, bank, period, MODULE_FX)
    return GeneratedReturn(
        snapshot=_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[_source_run_entry(item) for _, item in sorted(runs.items())],
    )


def _generate_icaap_stress(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    forecast_run = db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == period.id,
            RegulatoryRun.module == MODULE_FORECAST,
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
        .limit(1)
    )
    if forecast_run is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "no_forecast_run",
                "message": (
                    "A successful 5-year forecast run is required before the ICAAP data "
                    "companion can be generated for this reporting period. Run the "
                    "forecast module first."
                ),
            },
        )
    metrics = forecast_run.metrics
    summary_rows = [
        _row(code, f"Forecast summary: {code}", metrics[code])
        for code in _FORECAST_SUMMARY_FIELDS
        if code in metrics
    ]
    path_rows = [
        _row(
            f"year_{entry['year']}",
            f"Projected position for {entry['period_label']}",
            entry["total_assets"],
            **{
                key: value
                for key, value in entry.items()
                if key not in ("year", "period_label")
            },
        )
        for entry in metrics.get("path", [])
    ]

    stress_runs: list[RegulatoryRun] = []
    stress_rows: list[dict[str, Any]] = []
    for module, headline in ((MODULE_LIQUIDITY, "lcr_pct"), (MODULE_CAPITAL, "car_pct")):
        latest = _latest_succeeded_runs_by_scenario(db, ctx, bank, period, module)
        for scenario_code, run in sorted(latest.items()):
            if scenario_code == BASELINE_SCENARIO:
                continue
            stress_runs.append(run)
            stress_rows.append(
                _row(
                    f"{module}:{scenario_code}",
                    f"{module} stress scenario '{scenario_code}' ({headline})",
                    run.metrics.get(headline, "0"),
                    module=module,
                    scenario_code=scenario_code,
                    input_hash=run.input_hash,
                )
            )

    sections = [
        _section("forecast_summary", "5-Year Forecast Summary", summary_rows),
        _section("forecast_path", "Projected Balance-Sheet Path", path_rows),
        _section("stress_summary", "Stress Scenario Outcomes", stress_rows, optional=True),
    ]
    totals = [
        _row(
            code,
            f"Forecast summary: {code}",
            metrics[code],
        )
        for code in ("cumulative_net_income", "min_car_pct", "min_lcr_pct", "min_nsfr_pct")
        if code in metrics
    ]
    metadata = {
        "forecast_run_id": str(forecast_run.id),
        "forecast_scenario_code": forecast_run.scenario_code,
        "assumptions": metrics.get("assumptions", {}),
        "stress_run_count": len(stress_runs),
    }
    source_runs = [_source_run_entry(forecast_run)] + [
        _source_run_entry(run) for run in stress_runs
    ]
    return GeneratedReturn(
        snapshot=_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=source_runs,
    )


_GENERATORS = {
    "liquidity": _generate_liquidity,
    "capital": _generate_capital,
    "irrbb": _generate_irrbb,
    "fx": _generate_fx,
    "icaap_stress": _generate_icaap_stress,
}

__all__ = [
    "GeneratedReturn",
    "SNAPSHOT_SCHEMA_VERSION",
    "generate_package",
]
