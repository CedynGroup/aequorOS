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

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    Bank,
    BankReportingPeriod,
    InstitutionProfile,
    RegulatoryMetricResult,
    RegulatoryPackage,
    RegulatoryRun,
)
from app.schemas.regulatory_liquidity import Bsd3SummaryRowRead
from app.schemas.regulatory_reporting import RegulatoryPackageCreate, RegulatoryPackageRead
from app.services import regulatory_capital, regulatory_liquidity
from app.services.attestation import digests, register_state
from app.services.audit import record_event
from app.services.regulatory_reporting.common import (
    get_bank_or_404,
    get_effective_period_or_404,
    get_period_for_reporting_date_or_404,
    read_package,
    require_actor,
)
from app.services.regulatory_reporting.registry import REGISTRY, ReturnDefinition

SNAPSHOT_SCHEMA_VERSION = "regulatory-package-v1"
BASELINE_SCENARIO = "baseline"


def snapshot_content_hash(snapshot: dict[str, Any]) -> str:
    """SHA-256 over the canonical-JSON snapshot — the package's content seal.

    Value-based and key-sorted, mirroring the regulatory ``input_hash``
    discipline: identical content always hashes identically regardless of
    insertion order.
    """
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    db: Session, ctx: TenantContext, bank_id: str, payload: RegulatoryPackageCreate
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
    # Daily returns file on business days that seldom coincide with a monthly
    # reporting-period end, so they draw on the latest effective period.
    if definition.frequency == "daily":
        period = get_effective_period_or_404(db, ctx, bank, payload.reporting_date)
    else:
        period = get_period_for_reporting_date_or_404(db, ctx, bank, payload.reporting_date)

    generated = _GENERATORS[definition.generator](db, ctx, bank, period, definition)
    _enrich_institution_block(db, ctx, bank, generated.snapshot)
    _stamp_basis(generated.snapshot, payload.basis)
    _apply_prior_period_comparative(db, ctx, bank, definition, payload, generated.snapshot)

    # Supersession and versioning are per-basis: solo and consolidated are
    # independent current-version chains for the same (return, reporting date).
    prior_current = db.scalar(
        select(RegulatoryPackage).where(
            RegulatoryPackage.organization_id == ctx.organization_id,
            RegulatoryPackage.bank_id == bank.id,
            RegulatoryPackage.return_code == definition.code,
            RegulatoryPackage.reporting_date == payload.reporting_date,
            RegulatoryPackage.basis == payload.basis,
            RegulatoryPackage.status != "superseded",
        )
    )
    # ORASS parity: an ACKNOWLEDGED return is final at the regulator; a
    # correcting version exists only under a granted resubmission request
    # (LRT guide §5.3). The grant is one-shot — consumed by this regeneration.
    resubmission_authorization = None
    if prior_current is not None and prior_current.status == "acknowledged":
        from app.services.regulatory_reporting.workflow import (  # noqa: PLC0415
            granted_unconsumed_resubmission,
        )

        resubmission_authorization = granted_unconsumed_resubmission(db, prior_current)
        if resubmission_authorization is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This return was acknowledged by the regulator; regenerating "
                    "it requires a GRANTED resubmission request (Request "
                    "Resubmission) on the acknowledged package first."
                ),
            )
    prior_version = db.scalar(
        select(RegulatoryPackage.version)
        .where(
            RegulatoryPackage.organization_id == ctx.organization_id,
            RegulatoryPackage.bank_id == bank.id,
            RegulatoryPackage.return_code == definition.code,
            RegulatoryPackage.reporting_date == payload.reporting_date,
            RegulatoryPackage.basis == payload.basis,
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

    # Attestation binding, sealed with the snapshot (docs/attestation_esignature.md
    # §3.1). Every return gets a content_digest — snapshot_sha256 embeds
    # metadata.generated_at and therefore seals a VERSION, not content (gap G13).
    # Packs that bind no engine run additionally get the master-data provenance
    # analogue, without which a signature over them would bind figures with
    # nothing behind them (gap G16).
    register_digest = (
        digests.register_state_digest(register_state.register_state_rows(db, ctx, bank.id))
        if not generated.source_runs
        else None
    )
    package = RegulatoryPackage(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        return_family=definition.family,
        return_code=definition.code,
        reporting_date=payload.reporting_date,
        frequency=definition.frequency,
        basis=payload.basis,
        status="generated",
        version=(prior_version or 0) + 1,
        supersedes_id=prior_current.id if prior_current is not None else None,
        snapshot=generated.snapshot,
        source_runs=generated.source_runs,
        validation_report=None,
        generated_by=actor_user_id,
        generated_at=datetime.now(UTC),
        notes=payload.notes,
        snapshot_sha256=snapshot_content_hash(generated.snapshot),
        content_digest=digests.content_digest(generated.snapshot),
        register_state_digest=register_digest,
    )
    db.add(package)
    db.flush()
    if resubmission_authorization is not None:
        resubmission_authorization.consumed_by_package_id = package.id
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
            "basis": payload.basis,
            "version": package.version,
            "supersedes_id": (str(prior_current.id) if prior_current is not None else None),
            "source_runs": [entry["run_id"] for entry in generated.source_runs],
            "content_digest": package.content_digest,
            "register_state_digest": package.register_state_digest,
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


def _headline_comparative_section(totals: list[dict[str, Any]]) -> dict[str, Any]:
    """A T vs T−1 section carrying the headline totals plus a ``prior_value``
    slot the caller fills from the immediately-prior period (None until then).

    The BoG monthly returns show a Reporting-Month-vs-Previous-Month column
    (LMTD Table 1); this section is the snapshot's honest carrier for that
    convention. Values that have no prior stay blank — never fabricated.
    """
    rows = [
        {
            "code": total["code"],
            "description": total["description"],
            "value": total["value"],
            "unit": total.get("unit", ""),
            "prior_value": None,
        }
        for total in totals
    ]
    return _section(
        "headline_comparative",
        "Headline Totals — Reporting Period vs Previous Period",
        rows,
    )


def _stamp_basis(snapshot: dict[str, Any], basis: str) -> None:
    """Record the solo/consolidated reporting basis on the snapshot in place."""
    snapshot.setdefault("institution", {})["basis"] = basis
    snapshot.setdefault("metadata", {})["basis"] = basis


def _apply_prior_period_comparative(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    definition: ReturnDefinition,
    payload: RegulatoryPackageCreate,
    snapshot: dict[str, Any],
) -> None:
    """Fill the ``headline_comparative`` section's ``prior_value`` cells from the
    immediately-prior reporting period's current package for the same
    (bank, return_code, basis). No-op when the snapshot has no comparative
    section or no prior package exists — prior figures are never invented."""
    section = next(
        (
            item
            for item in snapshot.get("sections", [])
            if item.get("code") == "headline_comparative"
        ),
        None,
    )
    if section is None:
        return
    prior = db.scalar(
        select(RegulatoryPackage)
        .where(
            RegulatoryPackage.organization_id == ctx.organization_id,
            RegulatoryPackage.bank_id == bank.id,
            RegulatoryPackage.return_code == definition.code,
            RegulatoryPackage.basis == payload.basis,
            RegulatoryPackage.reporting_date < payload.reporting_date,
            RegulatoryPackage.status != "superseded",
        )
        .order_by(
            RegulatoryPackage.reporting_date.desc(),
            RegulatoryPackage.version.desc(),
        )
        .limit(1)
    )
    if prior is None:
        return
    prior_totals = {row.get("code"): row.get("value") for row in prior.snapshot.get("totals", [])}
    for row in section.get("rows", []):
        row["prior_value"] = prior_totals.get(row.get("code"))
    snapshot.setdefault("metadata", {})["prior_period_reporting_date"] = (
        prior.reporting_date.isoformat()
    )


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


def _enrich_institution_block(
    db: Session, ctx: TenantContext, bank: Bank, snapshot: dict[str, Any]
) -> None:
    """Fold corporate-profile master data (plan W4) into the envelope.

    When an ``InstitutionProfile`` row exists for the bank, the snapshot's
    institution block additionally carries its ORASS institution code (the
    identifier the regulator's portal knows the institution by). None-safe:
    no profile row means no key, and an unset code rides as ``null``.
    """
    profile = db.scalar(
        select(InstitutionProfile).where(
            InstitutionProfile.organization_id == ctx.organization_id,
            InstitutionProfile.bank_id == bank.id,
        )
    )
    if profile is None:
        return
    institution = snapshot.get("institution")
    if isinstance(institution, dict):
        institution["orass_institution_code"] = profile.orass_institution_code


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


def _lcr_sections(preview: Any) -> list[dict[str, Any]]:
    """The LCR-tool sections shared by BSD3 and the LMT return (Table 11 subset)."""
    summary = {row.row_code: row for row in preview.summary_rows}
    return [
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
    ]


def _lcr_totals(preview: Any) -> list[dict[str, Any]]:
    """Headline LCR totals shared by BSD3 and the LMT return."""
    summary = {row.row_code: row for row in preview.summary_rows}
    return [
        _row("hqla_total_ghs", summary["3.0"].description, summary["3.0"].value, unit="ghs"),
        _row("total_outflows_ghs", summary["5.0"].description, summary["5.0"].value, unit="ghs"),
        _row("net_outflows_30d_ghs", summary["8.0"].description, summary["8.0"].value, unit="ghs"),
        _row("lcr_pct", summary["9.0"].description, summary["9.0"].value, unit="pct"),
    ]


def _liquidity_metadata(preview: Any) -> dict[str, Any]:
    """Envelope metadata shared by BSD3 and the LMT return."""
    return {
        "form_code": preview.header.form_code,
        "form_title": preview.header.form_title,
        "regulator_name": preview.header.regulator,
        "preview_note": preview.header.preview_note,
        "baseline_run_id": str(preview.run_id),
        "engine_validations": [item.model_dump(mode="json") for item in preview.validations],
    }


def _generate_liquidity(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    preview = regulatory_liquidity.get_bsd3_preview(db, ctx, bank.id, period.id)
    sections = [
        *_lcr_sections(preview),
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
        *_lcr_totals(preview),
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
    sections.append(_headline_comparative_section(totals))
    metadata = _liquidity_metadata(preview)
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
    sections.append(_headline_comparative_section(totals))
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


# BoG GHS IRRBB calibration (plan W6.4): the IRRBB Guideline (exposure draft,
# Feb 2026) Appendix II–III Tables 5–6 prescribe a ±450 bp parallel GHS shock
# (short 500 / long 300). The IRR engine currently computes only the six Basel
# scenarios (app/domain/irr/engine.py IRR_SCENARIO_CODES) and hard-coded
# ±200 bp earnings-at-risk, so these rows are emitted ONLY when a run's
# metrics actually carry them — never fabricated. The BoG ±450 param rows
# (module='irr', parallel_up_450 / parallel_down_450) are stored effective-
# dated in param_stress_shock awaiting engine-side adoption (documented gap).
_BOG_GHS_450_EVE_ROWS: tuple[tuple[str, str], ...] = (
    ("eve_up_450_ghs", "ΔEVE under +450 bp parallel (BoG GHS calibration)"),
    ("eve_down_450_ghs", "ΔEVE under -450 bp parallel (BoG GHS calibration)"),
)
_BOG_GHS_450_EAR_ROWS: tuple[tuple[str, str], ...] = (
    ("ear_up_450_ghs", "Earnings at risk, +450 bp parallel (BoG GHS calibration)"),
    ("ear_down_450_ghs", "Earnings at risk, -450 bp parallel (BoG GHS calibration)"),
)


def _generate_irrbb(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    run = _baseline_run_or_409(db, ctx, bank, period, MODULE_IRR, artifact="the IRRBB pilot return")
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
    eve_rows.extend(
        _row(code, description, metrics[code])
        for code, description in _BOG_GHS_450_EVE_ROWS
        if code in metrics
    )
    ear_rows = [
        _row("ear_up_200_ghs", "Earnings at risk, +200 bp parallel", metrics["ear_up_200_ghs"]),
        _row("ear_down_200_ghs", "Earnings at risk, -200 bp parallel", metrics["ear_down_200_ghs"]),
        *(
            _row(code, description, metrics[code])
            for code, description in _BOG_GHS_450_EAR_ROWS
            if code in metrics
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
    bog_450_keys = {code for code, _ in (*_BOG_GHS_450_EVE_ROWS, *_BOG_GHS_450_EAR_ROWS)}
    metadata = {
        "worst_scenario": metrics.get("worst_scenario"),
        "eve_limit_pct": metrics.get("eve_limit_pct"),
        "baseline_run_id": str(run.id),
        # True only when the engine actually computed the BoG ±450 bp GHS
        # shock set; while False, the ±450 rows are honestly absent (engine
        # gap — see the IRRBB template notes).
        "bog_ghs_450_rows_present": any(code in metrics for code in bog_450_keys),
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
            **{key: value for key, value in entry.items() if key not in ("year", "period_label")},
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


def _stress_traffic_lights(
    db: Session, scenario_runs: list[tuple[str, str, RegulatoryRun]]
) -> list[dict[str, Any]]:
    """One row per stored headline metric of every consumed run — value,
    threshold and green/amber/red status exactly as the engine persisted them
    (``RegulatoryMetricResult``); the pack classifies nothing itself."""
    rows: list[dict[str, Any]] = []
    for module, scenario_code, run in scenario_runs:
        results = db.scalars(
            select(RegulatoryMetricResult)
            .where(RegulatoryMetricResult.run_id == run.id)
            .order_by(RegulatoryMetricResult.position)
        ).all()
        rows.extend(
            _row(
                f"{module}:{scenario_code}:{result.metric_code}",
                f"{result.metric_code} under '{scenario_code}' ({module})",
                result.metric_value,
                unit=result.unit,
                threshold=str(result.threshold_min) if result.threshold_min is not None else None,
                status=result.status,
                module=module,
                scenario_code=scenario_code,
            )
            for result in results
        )
    return rows


def _stress_ratio_evolution(capital_runs: dict[str, RegulatoryRun]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scenario_code, run in sorted(capital_runs.items()):
        if scenario_code == BASELINE_SCENARIO:
            continue
        for quarter in run.metrics.get("stress_path", []):
            rows.append(
                _row(
                    f"{scenario_code}:q{quarter['quarter']}",
                    f"'{scenario_code}' quarter {quarter['quarter']}",
                    quarter["car"],
                    cet1_ratio_pct=quarter["cet1_ratio"],
                    tier1_ratio_pct=quarter["tier1_ratio"],
                    leverage_ratio_pct=quarter["leverage_ratio"],
                    total_rwa_ghs=quarter["total_rwa"],
                    scenario_code=scenario_code,
                )
            )
    return rows


def _stress_pro_forma(capital_runs: dict[str, RegulatoryRun]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scenario_code, run in sorted(capital_runs.items()):
        if scenario_code == BASELINE_SCENARIO:
            continue
        path = run.metrics.get("stress_path", [])
        if not path:
            continue
        end_state = path[-1]
        rows.append(
            _row(
                scenario_code,
                f"End-state capital position under '{scenario_code}'",
                end_state["total_capital"],
                cet1_capital_ghs=end_state["cet1_capital"],
                tier1_capital_ghs=end_state["tier1_capital"],
                total_rwa_ghs=end_state["total_rwa"],
                credit_rwa_ghs=end_state["credit_rwa"],
                market_rwa_ghs=end_state["market_rwa"],
                operational_rwa_ghs=end_state["operational_rwa"],
                end_car_pct=end_state["car"],
            )
        )
    return rows


def _stress_attribution(
    liquidity_runs: dict[str, RegulatoryRun],
    capital_runs: dict[str, RegulatoryRun],
    liq_baseline: RegulatoryRun,
    cap_baseline: RegulatoryRun,
) -> list[dict[str, Any]]:
    """Scenario-vs-baseline deltas over the SAME canonical book: both runs
    anchor to their engine's baseline input hash, so the delta is pure shock
    attribution rather than a data-vintage difference."""
    rows: list[dict[str, Any]] = []
    for metric in ("lcr_pct", "nsfr_pct"):
        base = Decimal(str(liq_baseline.metrics[metric]))
        for scenario_code, run in sorted(liquidity_runs.items()):
            if scenario_code == BASELINE_SCENARIO or metric not in run.metrics:
                continue
            stressed = Decimal(str(run.metrics[metric]))
            rows.append(
                _row(
                    f"liquidity:{scenario_code}:{metric}",
                    f"{metric} impact of '{scenario_code}' vs baseline",
                    stressed - base,
                    baseline_pct=str(base),
                    stressed_pct=str(stressed),
                    module=MODULE_LIQUIDITY,
                    scenario_code=scenario_code,
                )
            )
    base_car = Decimal(str(cap_baseline.metrics["car_pct"]))
    for scenario_code, run in sorted(capital_runs.items()):
        if scenario_code == BASELINE_SCENARIO:
            continue
        path = run.metrics.get("stress_path", [])
        if not path:
            continue
        end_car = Decimal(str(path[-1]["car"]))
        rows.append(
            _row(
                f"capital:{scenario_code}:car_pct",
                f"End-state CAR impact of '{scenario_code}' vs baseline",
                end_car - base_car,
                baseline_pct=str(base_car),
                stressed_pct=str(end_car),
                module=MODULE_CAPITAL,
                scenario_code=scenario_code,
            )
        )
    return rows


def _stress_frontier_rows(reverse_run: RegulatoryRun) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for axis_code, floor_key, ratio_key in (
        ("liquidity_frontier", "lcr_min_pct", "lcr_at_breach_pct"),
        ("capital_frontier", "cet1_min_pct", "worst_cet1_at_breach_pct"),
    ):
        axis = reverse_run.metrics[axis_code.replace("_frontier", "_axis")]
        breached = axis["breached"]
        rows.append(
            _row(
                axis_code,
                (
                    f"Severity multiplier breaching the {axis['scenario_code']} floor"
                    if breached
                    else f"No breach up to {axis['k_max']}x scenario severity"
                ),
                axis.get("breach_multiplier", axis.get("k_max", "")),
                breached="true" if breached else "false",
                floor_pct=axis.get(floor_key),
                ratio_at_breach_pct=axis.get(ratio_key),
                scenario_code=axis["scenario_code"],
            )
        )
    return rows


def _stress_recommended_actions(
    capital_runs: dict[str, RegulatoryRun], traffic_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Deterministic remedial actions: the capital engine's own fired triggers
    (which carry prescribed action text) plus a funding-remediation line for
    every liquidity ratio the stored classification marked amber/red."""
    rows: list[dict[str, Any]] = []
    for scenario_code, run in sorted(capital_runs.items()):
        if scenario_code == BASELINE_SCENARIO:
            continue
        for trigger in run.metrics.get("triggers", []):
            if not trigger.get("fired"):
                continue
            rows.append(
                _row(
                    f"capital:{scenario_code}:{trigger['code']}",
                    trigger["action"],
                    trigger["threshold_pct"],
                    unit="pct",
                    first_quarter=str(trigger.get("first_quarter") or ""),
                    scenario_code=scenario_code,
                    module=MODULE_CAPITAL,
                )
            )
    for light in traffic_rows:
        if light["module"] != MODULE_LIQUIDITY or light["status"] == "green":
            continue
        if light["scenario_code"] == BASELINE_SCENARIO:
            continue
        metric = light["code"].rsplit(":", 1)[-1]
        if metric not in ("lcr_pct", "nsfr_pct"):
            continue
        rows.append(
            _row(
                f"liquidity:{light['scenario_code']}:{metric}",
                (
                    f"Restore {metric} above the regulatory minimum: extend the "
                    "high-quality liquid asset buffer or term out the funding "
                    f"profile stressed by scenario '{light['scenario_code']}'."
                ),
                light["value"],
                unit="pct",
                status=light["status"],
                scenario_code=light["scenario_code"],
                module=MODULE_LIQUIDITY,
            )
        )
    return rows


def _generate_template_pending(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    """Honest refusal for returns whose BoG form is not yet published.

    The obligation is real (it sits in the registry and the calendar), but
    the standing order is to obtain the official form, never infer it — so
    generation names the gap instead of fabricating a layout (product.md
    §Phase 2 items 12/14).
    """
    _ = (db, ctx, bank, period)
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error_code": "template_pending",
            "message": (
                f"The official form for {definition.code} has not been obtained "
                "yet; this return cannot be generated until the regulator's "
                "template is registered (the layout is never inferred)."
            ),
        },
    )


def _generate_stress_pack(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: ReturnDefinition,
) -> GeneratedReturn:
    """Stress Test Output Report pack (product.md §Phase 2 item 6).

    Assembles ONLY stored engine state — the latest succeeded liquidity and
    capital runs per scenario, their persisted headline metric results, and
    the latest reverse-stress frontier — into the standardized Board/ALCO
    output report: traffic lights, ratio evolution, pro-forma capital,
    baseline attribution, recommended actions.
    """
    liq_baseline = _baseline_run_or_409(
        db, ctx, bank, period, MODULE_LIQUIDITY, artifact="the stress pack"
    )
    cap_baseline = _baseline_run_or_409(
        db, ctx, bank, period, MODULE_CAPITAL, artifact="the stress pack"
    )
    liquidity_runs = _latest_succeeded_runs_by_scenario(db, ctx, bank, period, MODULE_LIQUIDITY)
    capital_runs = _latest_succeeded_runs_by_scenario(db, ctx, bank, period, MODULE_CAPITAL)
    scenario_runs = [
        (MODULE_LIQUIDITY, code, run) for code, run in sorted(liquidity_runs.items())
    ] + [(MODULE_CAPITAL, code, run) for code, run in sorted(capital_runs.items())]
    stressed = [entry for entry in scenario_runs if entry[1] != BASELINE_SCENARIO]
    if not stressed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "no_stress_scenarios",
                "message": (
                    "No succeeded stress-scenario runs exist for this reporting "
                    "period. Run at least one non-baseline liquidity or capital "
                    "scenario before generating the stress pack."
                ),
            },
        )

    reverse_run = db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == period.id,
            RegulatoryRun.module == "reverse_stress",
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc(), RegulatoryRun.id.desc())
        .limit(1)
    )

    traffic_rows = _stress_traffic_lights(db, scenario_runs)
    evolution_rows = _stress_ratio_evolution(capital_runs)
    pro_forma_rows = _stress_pro_forma(capital_runs)
    attribution_rows = _stress_attribution(
        liquidity_runs, capital_runs, liq_baseline, cap_baseline
    )
    frontier_rows = _stress_frontier_rows(reverse_run) if reverse_run is not None else []
    action_rows = _stress_recommended_actions(capital_runs, traffic_rows)

    sections = [
        _section("traffic_lights", "Stress Outcome Traffic Lights", traffic_rows),
        _section(
            "ratio_evolution", "Capital Ratio Evolution Under Stress", evolution_rows,
            optional=True,
        ),
        _section(
            "pro_forma_capital", "Pro-Forma Capital Position (Stress End-State)",
            pro_forma_rows, optional=True,
        ),
        _section("attribution", "Scenario Attribution vs Baseline", attribution_rows),
        _section(
            "reverse_stress_frontier", "Reverse-Stress Frontier", frontier_rows,
            optional=True,
        ),
        _section("recommended_actions", "Recommended Actions", action_rows, optional=True),
    ]

    stressed_lcrs = [
        Decimal(str(run.metrics["lcr_pct"]))
        for code, run in liquidity_runs.items()
        if code != BASELINE_SCENARIO and "lcr_pct" in run.metrics
    ]
    end_cars = [
        Decimal(str(run.metrics["stress_path"][-1]["car"]))
        for code, run in capital_runs.items()
        if code != BASELINE_SCENARIO and run.metrics.get("stress_path")
    ]
    red_count = sum(1 for light in traffic_rows if light["status"] == "red")
    totals = [_row("red_light_count", "Metrics classified red across scenarios", red_count)]
    if stressed_lcrs:
        totals.append(
            _row(
                "worst_stressed_lcr_pct",
                "Worst stressed LCR across scenarios",
                min(stressed_lcrs),
            )
        )
    if end_cars:
        totals.append(
            _row("worst_end_car_pct", "Worst end-state CAR across scenarios", min(end_cars))
        )

    metadata: dict[str, Any] = {
        "liquidity_scenarios": sorted(liquidity_runs),
        "capital_scenarios": sorted(capital_runs),
        "baseline_liquidity_input_hash": liq_baseline.input_hash,
        "baseline_capital_input_hash": cap_baseline.input_hash,
        "fired_trigger_count": sum(
            1
            for code, run in capital_runs.items()
            if code != BASELINE_SCENARIO
            for trigger in run.metrics.get("triggers", [])
            if trigger.get("fired")
        ),
    }
    if reverse_run is not None:
        metadata["reverse_stress_run_id"] = str(reverse_run.id)
        metadata["reverse_stress_narrative"] = reverse_run.metrics.get("narrative", "")

    source_runs = [_source_run_entry(run) for _, _, run in scenario_runs]
    if reverse_run is not None:
        source_runs.append(_source_run_entry(reverse_run))
    return GeneratedReturn(
        snapshot=_envelope(bank, period, definition, sections, totals, metadata),
        source_runs=source_runs,
    )


# Public seams for sibling generator modules: the LRT corporate packs (plan
# W5, ``lrt_generation.py``) and the W6 canonical-position returns
# (``le_generation.py``) assemble the same regulatory-package-v1
# envelope/section/row shapes without reaching into private helpers.
snapshot_row = _row
snapshot_section = _section
snapshot_total = _total
build_envelope = _envelope
baseline_run_or_409 = _baseline_run_or_409
source_run_entry = _source_run_entry
latest_succeeded_runs_by_scenario = _latest_succeeded_runs_by_scenario
lcr_snapshot_sections = _lcr_sections
lcr_snapshot_totals = _lcr_totals
liquidity_snapshot_metadata = _liquidity_metadata

# Imported at the bottom — after the seams above are defined — so the module
# pairs load cleanly regardless of which side is imported first.
from app.services.regulatory_reporting.dbk_generation import DBK_GENERATORS  # noqa: E402
from app.services.regulatory_reporting.le_generation import LE_GENERATORS  # noqa: E402
from app.services.regulatory_reporting.lrt_generation import LRT_GENERATORS  # noqa: E402

_GENERATORS = {
    "liquidity": _generate_liquidity,
    "capital": _generate_capital,
    "irrbb": _generate_irrbb,
    "fx": _generate_fx,
    "icaap_stress": _generate_icaap_stress,
    "stress_pack": _generate_stress_pack,
    "template_pending": _generate_template_pending,
    **LRT_GENERATORS,
    **LE_GENERATORS,
    **DBK_GENERATORS,
}

__all__ = [
    "GeneratedReturn",
    "SNAPSHOT_SCHEMA_VERSION",
    "generate_package",
]
