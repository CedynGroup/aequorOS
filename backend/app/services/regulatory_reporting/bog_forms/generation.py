"""``bog_form`` generator: one entry point for every official BoG return.

Registered returns with ``generator="bog_form"`` dispatch here. The generator
computes the form (dependency forms first — BSD8 needs BSD2, BSD6 needs BSD2 …),
then emits the standard ``regulatory-package-v1`` snapshot so the existing
package pipeline (immutable content hash, maker-checker approval, artifact
versions, lineage, signing policy, submission channels) applies unchanged:

- ``sections``: one per official sheet — rows are the declared input lines
  (code, description, value, column, cell, status, source), so csv/pdf and the
  validation rules work generically;
- ``bog_form``: the template-faithful payload — every cell value (inputs AND
  evaluated formulas, base units), unmapped cells, missing dependencies,
  errors — from which the xlsx exporter rebuilds the official workbook WITHOUT
  recomputing (the snapshot is immutable).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod

from .catalog import form_spec
from .engine import FormResult, compute_form, scale_for_export
from .spec import FormSpec

SNAPSHOT_SCHEMA_VERSION = "regulatory-package-v1"
BOG_FORM_SCHEMA_VERSION = "bog-form-v1"


def compute_with_dependencies(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    code: str,
    *,
    _seen: dict[str, FormResult] | None = None,
) -> FormResult:
    """Compute ``code`` after (recursively) computing every form it depends on."""
    seen = _seen if _seen is not None else {}
    if code in seen:
        return seen[code]
    spec = form_spec(code)
    for dep in spec.depends_on:
        if dep not in seen:
            compute_with_dependencies(db, ctx, bank, period, dep, _seen=seen)
    result = compute_form(
        db, ctx, bank, period, spec, dependencies={d: seen[d] for d in spec.depends_on if d in seen}
    )
    seen[code] = result
    return result


def _cells_payload(result: FormResult) -> dict[str, dict[str, Any]]:
    cells: dict[str, dict[str, Any]] = {}
    for (sheet, ref), value in result.all_values().items():
        cells.setdefault(sheet, {})[ref] = value
    return cells


def build_sections(result: FormResult) -> list[dict[str, Any]]:
    """One generic section per official sheet (csv/pdf + validation path).

    ``value`` is expressed in the SHEET's official unit (the column header says
    so) and is ``None`` — a blank cell — when the line is input_required /
    unmapped; the generic renderer treats an empty string as a corrupt
    snapshot, so absence must be None, never "".
    """
    sections: list[dict[str, Any]] = []
    for sheet in result.spec.sheets:
        rows = [
            {
                "code": f"{lv.code}.{lv.column}",
                "description": lv.label,
                "value": (
                    None
                    if lv.value is None
                    else str(
                        scale_for_export(result.spec, sheet.name, lv.value, unscaled=lv.unscaled)
                    )
                ),
                "unscaled": lv.unscaled,
                "column": lv.column,
                "cell": lv.cell,
                "status": lv.status,
                "source": lv.source or "",
                "notes": lv.notes,
            }
            for lv in result.lines
            if lv.sheet == sheet.name
        ]
        sections.append(
            {
                "code": _section_code(sheet.name),
                "title": sheet.name,
                "optional": True,
                "rows": rows,
                "total": None,
                "unit": sheet.unit,
            }
        )
    return sections


def _section_code(sheet_name: str) -> str:
    return "sheet_" + "".join(ch.lower() if ch.isalnum() else "_" for ch in sheet_name).strip("_")


def build_snapshot(  # noqa: PLR0913
    bank: Bank,
    period: BankReportingPeriod,
    spec: FormSpec,
    result: FormResult,
    *,
    definition_code: str,
    family: str,
    regulator: str,
    template_id: str,
    fidelity: str,
) -> dict[str, Any]:
    counts = result.status_counts
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "return_code": definition_code,
        "return_family": family,
        "regulator": regulator,
        "template_id": template_id,
        "fidelity": fidelity,
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
        "sections": build_sections(result),
        "totals": [],
        "metadata": {
            "generated_at": datetime.now(UTC).isoformat(),
            "basis": spec.basis,
            "official_workbook": spec.workbook,
            "official_sheets": list(result.layout.sheet_names),
            "line_status_counts": counts,
            "missing_dependencies": result.missing_dependencies,
        },
        "bog_form": {
            "schema_version": BOG_FORM_SCHEMA_VERSION,
            "code": spec.code,
            "basis": spec.basis,
            "workbook": spec.workbook,
            "sheet_units": {sheet.name: sheet.unit for sheet in spec.sheets},
            "cells": _cells_payload(result),
            "unmapped_cells": [list(item) for item in result.unmapped_cells],
            "unresolved_external": [list(item) for item in result.unresolved_external],
            "unscaled_formulas": sorted(list(item) for item in result.unscaled_formulas),
            "missing_dependencies": result.missing_dependencies,
            "errors": result.errors,
            "status_counts": counts,
        },
    }


def generate_bog_form(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    definition: Any,
) -> Any:
    """Generator entry used by the registry (``generator="bog_form"``)."""
    # Local import: generation.py imports this module's registry at import time.
    from app.services.regulatory_reporting.generation import GeneratedReturn  # noqa: PLC0415

    spec = form_spec(definition.code)
    result = compute_with_dependencies(db, ctx, bank, period, spec.code)
    snapshot = build_snapshot(
        bank,
        period,
        spec,
        result,
        definition_code=definition.code,
        family=definition.family,
        regulator=definition.regulator,
        template_id=definition.template_id,
        fidelity=definition.fidelity,
    )
    return GeneratedReturn(snapshot=snapshot, source_runs=[])


BOG_GENERATORS = {"bog_form": generate_bog_form}
