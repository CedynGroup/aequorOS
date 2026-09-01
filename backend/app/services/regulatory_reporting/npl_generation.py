"""NPL-MONTHLY generator (credit PR-6) — Notice BG/GOV/SEC/2025/23 Appendix II.

Assembles the monthly NPL report from three sources, each with its own honesty
rule:

* **Levels** — the SEALED baseline credit run for the reporting period (never a
  live recomputation: a filed figure cites immutable provenance). Coverage and
  net-NPL rows render only when the run carries held provisions.
* **Migration** — the two consecutive month-end canonical books, classified
  under the current grid. One ingested month ⇒ the section is OMITTED and the
  omission is stated in the metadata; nothing is zero-filled.
* **Write-offs / recoveries / restructuring activity** — ingested loan events
  in the reporting month. No events ⇒ those sections are omitted with the
  omission stated: flows the platform has not been told about are unknown.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod
from app.services.jurisdictions import base_currency
from app.services.regulatory_credit import (
    MODULE_CREDIT,
    _classified_loan_rows,
    _event_amount_ghs,
    _load_events,
    _loan_states,
    _previous_month_end_as_of,
)
from app.services.regulatory_reporting.generation import (
    GeneratedReturn,
    baseline_run_or_409,
    build_envelope,
    headline_comparative_section,
    snapshot_row,
    snapshot_section,
    source_run_entry,
)
from app.services.regulatory_reporting.registry import ReturnDefinition

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")

_STATE_LABELS = {
    "performing": "Performing (not restructured)",
    "performing_restructured": "Performing restructured",
    "npl": "Non-performing",
    "new": "New in month",
    "departed": "Departed (settled / written off / withdrawn)",
}

_RESTRUCTURE_MEASURES = (
    "interest_only",
    "reduced_payment",
    "moratorium",
    "arrears_capitalization",
    "rate_reduction",
    "maturity_extension",
    "assisted_sale",
    "rescheduled",
)


def _dec(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


def _generate_npl_monthly(  # noqa: PLR0912, PLR0915 - one branch/statement per Appendix II table
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    run = baseline_run_or_409(db, ctx, bank, period, MODULE_CREDIT, artifact="the NPL report")
    metrics = run.metrics or {}
    omissions: list[str] = []

    # --- table 1: levels (from the sealed run) -----------------------------
    gross = _dec(metrics.get("gross_loans_ghs")) or _ZERO
    npl = _dec(metrics.get("npl_exposure_ghs")) or _ZERO
    ratio_pct = _dec(metrics.get("npl_ratio_pct")) or _ZERO
    specific = _dec(metrics.get("provision_specific_ghs"))
    level_rows = [
        snapshot_row("total_gross_loans_ghs", "Total gross loans", gross, unit="ghs"),
        snapshot_row("npl_stock_ghs", "Stock of NPLs", npl, unit="ghs"),
        snapshot_row(
            "npl_ratio_pct",
            "NPLs as a proportion of total gross loans",
            ratio_pct,
            unit="pct",
        ),
    ]
    if specific is not None:
        level_rows.append(
            snapshot_row(
                "specific_provisions_ghs",
                "Stock of specific provisions (stage 3 / classified NPL)",
                specific,
                unit="ghs",
            )
        )
        if gross > _ZERO:
            level_rows.append(
                snapshot_row(
                    "net_npl_ratio_pct",
                    "NPLs net of specific provisions as a proportion of total gross loans",
                    ((npl - specific) / gross * _HUNDRED),
                    unit="pct",
                )
            )
        if npl > _ZERO:
            level_rows.append(
                snapshot_row(
                    "npl_coverage_pct",
                    "Stock of specific provisions as a proportion of the stock of NPL",
                    (specific / npl * _HUNDRED),
                    unit="pct",
                )
            )
    else:
        omissions.append(
            "Coverage and net-NPL rows omitted: no loan states a held provision "
            "(ecl_provision_ghs), so provisions held are unknown — never zero."
        )
    sections = [snapshot_section("npl_levels", "NPL Level and Flows", level_rows)]

    # --- table 2: migration ------------------------------------------------
    from app.domain.credit.migration import compute_migration  # noqa: PLC0415

    opening_as_of = _previous_month_end_as_of(db, ctx, bank, period.period_end)
    if opening_as_of is None:
        omissions.append(
            "Credit-migration section omitted: only one month-end book is ingested; "
            "the matrix needs the previous month-end as well."
        )
    else:
        migration = compute_migration(
            _loan_states(_classified_loan_rows(db, ctx, bank, opening_as_of)),
            _loan_states(_classified_loan_rows(db, ctx, bank, period.period_end)),
        )
        migration_rows = [
            snapshot_row(
                f"{cell.from_state}__{cell.to_state}",
                f"{_STATE_LABELS[cell.from_state]} → {_STATE_LABELS[cell.to_state]}",
                cell.exposure_ghs,
                unit="ghs",
                loan_count=str(cell.loan_count),
            )
            for cell in (*migration.matrix, *migration.entries, *migration.exits)
        ]
        sections.append(
            snapshot_section(
                "credit_migration",
                "Credit Migration Over the Month",
                migration_rows,
            )
        )

    # --- tables 3-5: events in the reporting month -------------------------
    month_start = period.period_end.replace(day=1)
    events = _load_events(db, ctx, bank, start=month_start, end=period.period_end)
    ccy = base_currency(bank)

    def total(kind: str, subtype: str | None = None) -> tuple[Decimal, int]:
        amount = _ZERO
        count = 0
        for event in events:
            if event.event_type != kind:
                continue
            if subtype is not None and event.event_subtype != subtype:
                continue
            converted = _event_amount_ghs(event, ccy)
            if converted is None:
                continue
            amount += converted
            count += 1
        return amount, count

    if any(event.event_type == "WRITE_OFF" for event in events):
        wilful, _ = total("WRITE_OFF", "wilful")
        non_wilful, _ = total("WRITE_OFF", "non_wilful")
        write_off_rows = [
            snapshot_row(
                "write_offs_wilful_ghs", "Write-offs — wilful defaulters", wilful, unit="ghs"
            ),
            snapshot_row(
                "write_offs_non_wilful_ghs",
                "Write-offs — non-wilful defaulters",
                non_wilful,
                unit="ghs",
            ),
            snapshot_row(
                "write_offs_total_ghs", "Total write-offs", wilful + non_wilful, unit="ghs"
            ),
        ]
        if npl > _ZERO:
            write_off_rows.append(
                snapshot_row(
                    "write_offs_pct_of_npl",
                    "Write-offs as a percentage of the stock of NPLs",
                    ((wilful + non_wilful) / npl * _HUNDRED),
                    unit="pct",
                )
            )
        sections.append(
            snapshot_section("write_offs", "Write-offs (Wilful / Non-wilful)", write_off_rows)
        )
    else:
        omissions.append(
            "Write-off section omitted: no WRITE_OFF loan events in the reporting month."
        )

    if any(event.event_type == "RECOVERY" for event in events):
        prop, _ = total("RECOVERY", "property_collateral")
        non_prop, _ = total("RECOVERY", "non_property_collateral")
        unsecured, _ = total("RECOVERY", "unsecured")
        recovery_rows = [
            snapshot_row(
                "recovery_property_ghs",
                "Realisation from property-related collaterals",
                prop,
                unit="ghs",
            ),
            snapshot_row(
                "recovery_non_property_ghs",
                "Realisation from non-property-related collaterals",
                non_prop,
                unit="ghs",
            ),
            snapshot_row(
                "recovery_unsecured_ghs", "Cash recovery — unsecured", unsecured, unit="ghs"
            ),
            snapshot_row(
                "recovery_total_ghs",
                "Total recoveries and realisation",
                prop + non_prop + unsecured,
                unit="ghs",
            ),
        ]
        sections.append(
            snapshot_section("recoveries", "Cash Recovery from NPLs", recovery_rows)
        )
    else:
        omissions.append(
            "Recovery section omitted: no RECOVERY loan events in the reporting month."
        )

    restructured_stock = _dec(metrics.get("restructured_exposure_ghs"))
    restructure_events = [event for event in events if event.event_type == "RESTRUCTURE"]
    if restructure_events or restructured_stock is not None:
        restructuring_rows = []
        if restructured_stock is not None:
            restructuring_rows.append(
                snapshot_row(
                    "restructured_stock_ghs",
                    "Stock of restructured facilities (classified book)",
                    restructured_stock,
                    unit="ghs",
                    loan_count=str(metrics.get("restructured_count", "")),
                )
            )
        for measure in _RESTRUCTURE_MEASURES:
            amount, count = total("RESTRUCTURE", measure)
            if count:
                restructuring_rows.append(
                    snapshot_row(
                        f"restructure_{measure}_ghs",
                        f"Newly restructured — {measure.replace('_', ' ')}",
                        amount,
                        unit="ghs",
                        loan_count=str(count),
                    )
                )
        sections.append(
            snapshot_section("restructuring", "Restructuring Activity", restructuring_rows)
        )
    else:
        omissions.append(
            "Restructuring section omitted: no restructured stock on the book and no "
            "RESTRUCTURE loan events in the reporting month."
        )

    totals = [
        snapshot_row("npl_ratio_pct", "NPL ratio", ratio_pct, unit="pct"),
        snapshot_row("npl_stock_ghs", "Stock of NPLs", npl, unit="ghs"),
    ]
    sections.append(headline_comparative_section(totals))

    metadata: dict[str, Any] = {
        "baseline_credit_input_hash": run.input_hash,
        "npl_limit_pct": metrics.get("npl_limit_pct"),
        "npl_restriction_level_pct": metrics.get("npl_restriction_level_pct"),
        "omissions": omissions,
        "loan_event_count_in_month": len(events),
    }
    return GeneratedReturn(
        snapshot=build_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=[source_run_entry(run)],
    )


NPL_GENERATORS = {"npl_monthly": _generate_npl_monthly}
