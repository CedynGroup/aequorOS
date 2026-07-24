"""DBK daily-return generator (docs/regulatory_reporting.md §5; Notice
BG/FMD/2026/07).

The Daily Bank Return (DBK) family reconstructs the FX Net Open Position and
contingents figures the bank must file with BoG each business day by 10:00 a.m.
via ORASS. BoG names the DBK 102/300/400/700 forms but has not published their
layouts (research §9, gap G5), so the family is graded REPRESENTATIVE and its
section layout_ids end in ``_representative``.

Figures are pulled from the FX engine's latest succeeded baseline
``RegulatoryRun`` for the effective reporting period — never recomputed here
(same discipline as ``generation._generate_fx``, which reuses the FX run
metrics). No FX run means no canonical data, so generation fails with a 409
``no_canonical_data`` rather than emitting an empty return.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod, RegulatoryRun
from app.services.regulatory_reporting.generation import (
    BASELINE_SCENARIO,
    MODULE_FX,
    GeneratedReturn,
    build_envelope,
    snapshot_row,
    snapshot_section,
    source_run_entry,
)
from app.services.regulatory_reporting.registry import ReturnDefinition


def _latest_fx_run_or_409(
    db: Session, ctx: TenantContext, bank: Bank, period: BankReportingPeriod
) -> RegulatoryRun:
    run = db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == period.id,
            RegulatoryRun.module == MODULE_FX,
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
                "error_code": "no_canonical_data",
                "message": (
                    "The Daily Bank Return needs FX net-open-position data. Run a "
                    "successful baseline FX analysis for this reporting period "
                    "before generating the DBK return."
                ),
            },
        )
    return run


def generate_dbk(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    run = _latest_fx_run_or_409(db, ctx, bank, period)
    metrics = run.metrics

    nop_by_currency_rows = [
        snapshot_row(
            str(currency["currency"]),
            f"Net open position in {currency['currency']}",
            currency["net_ghs"],
            side=str(currency["side"]),
            net_ccy=str(currency["net_ccy"]),
            spot_ghs=str(currency["spot_ghs"]),
            abs_pct_nof=str(currency["abs_pct_tier1"]),
            within_single_limit=bool(currency["within_single_limit"]),
        )
        for currency in metrics.get("currencies", [])
    ]
    nop_aggregate_rows = [
        snapshot_row("nop_ghs", "Aggregate net open position", metrics["nop_ghs"], unit="ghs"),
        snapshot_row(
            "nop_pct_nof",
            "Aggregate NOP as % of Net Own Funds",
            metrics["nop_pct_tier1"],
            unit="pct",
            aggregate_limit_pct=str(metrics.get("nop_aggregate_limit_pct", "20")),
            within_aggregate_limit=bool(metrics.get("within_aggregate_limit", False)),
        ),
        snapshot_row("sum_long_ghs", "Sum of long positions", metrics["sum_long_ghs"], unit="ghs"),
        snapshot_row(
            "sum_short_ghs", "Sum of short positions", metrics["sum_short_ghs"], unit="ghs"
        ),
        snapshot_row("nof_ghs", "Net Own Funds (Tier 1 proxy)", metrics["tier1_ghs"], unit="ghs"),
    ]
    # DBK 102 covers off-balance-sheet / letter-of-credit contingent exposures.
    # The FX run snapshot carries no contingents data, so this schedule is
    # honestly empty (optional) rather than fabricated (research gap G5).
    contingents_rows: list[dict[str, Any]] = []

    sections = [
        snapshot_section(
            "nop_by_currency",
            "Net Open Position by Currency (single-currency 0% to −10% NOF limit)",
            nop_by_currency_rows,
        ),
        snapshot_section(
            "nop_aggregate",
            "Aggregate Net Open Position (≤ 20% NOF limit)",
            nop_aggregate_rows,
        ),
        snapshot_section(
            "contingents",
            "DBK 102 — Off-Balance-Sheet & Contingent Exposures",
            contingents_rows,
            optional=True,
        ),
    ]
    totals = [
        snapshot_row("nop_ghs", "Aggregate net open position", metrics["nop_ghs"], unit="ghs"),
        snapshot_row(
            "nop_pct_nof",
            "Aggregate NOP as % of Net Own Funds",
            metrics["nop_pct_tier1"],
            unit="pct",
        ),
        snapshot_row("sum_long_ghs", "Sum of long positions", metrics["sum_long_ghs"], unit="ghs"),
        snapshot_row(
            "sum_short_ghs", "Sum of short positions", metrics["sum_short_ghs"], unit="ghs"
        ),
        snapshot_row("nof_ghs", "Net Own Funds (Tier 1 proxy)", metrics["tier1_ghs"], unit="ghs"),
    ]
    metadata = {
        "single_ccy_max_currency": metrics.get("single_ccy_max_currency"),
        "nop_single_limit_pct": metrics.get("nop_single_limit_pct"),
        "nop_aggregate_limit_pct": metrics.get("nop_aggregate_limit_pct"),
        "baseline_run_id": str(run.id),
        "data_period_end": period.period_end.isoformat(),
        "daily_source_note": (
            "Daily NOP reconstructed from the FX engine's latest baseline run for the "
            "effective reporting period; the official DBK 102/300/400/700 layouts are "
            "unpublished (BG/FMD/2026/07, gap G5)."
        ),
        "contingents_note": (
            "DBK 102 off-balance-sheet / LC contingent exposures are not carried by the "
            "FX snapshot and are shown blank rather than fabricated."
        ),
    }
    return GeneratedReturn(
        snapshot=build_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[source_run_entry(run)],
    )


DBK_GENERATORS = {"dbk": generate_dbk}

__all__ = ["DBK_GENERATORS", "generate_dbk"]
