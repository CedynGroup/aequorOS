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
from app.services.regulatory_reporting.common import unvalidated_book_finding
from app.services.regulatory_reporting.provenance import (
    ReportAuthority,
    build_template_provenance,
    line_status_authority,
)

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


def unit_kind(unit: str) -> str:
    """The normalised measure kind for a sheet's official unit convention.

    Deferred import: ``regulatory_reporting.generation`` imports this module at
    the bottom of its own module body, so the normaliser is fetched at call
    time rather than at import time.
    """
    from app.services.regulatory_reporting.generation import unit_kind as _kind  # noqa: PLC0415

    return _kind(unit)


def build_sections(result: FormResult) -> list[dict[str, Any]]:
    """One generic section per official sheet (csv/pdf + validation path).

    ``value`` is expressed in the SHEET's official unit (the column header says
    so) and is ``None`` — a blank cell — when the line is input_required /
    unmapped; the generic renderer treats an empty string as a corrupt
    snapshot, so absence must be None, never "".

    Each row carries the :class:`~...provenance.ReportAuthority` that owns its
    value, derived from the resolver that produced it. Row-level rather than
    section-level because on a BoG sheet the authority genuinely varies line by
    line: most cells are canonical data mapped into official inputs, a few are
    engine-run figures, and BSD2A's percentage column is computed under a Guide
    paragraph because the official grid carries no formula for it (audit CF-3).
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
                "authority": line_status_authority(lv.status, lv.source).value,
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
                # ``unit`` stays the Guide's OWN convention for this sheet
                # (¢'Million / ¢'000 / units / percent / count) — it is printed
                # on the official form and must survive verbatim. ``unit_kind``
                # normalises it onto the same closed set the generic families
                # use, so one renderer can switch on one key across every
                # return without losing the official scale.
                "unit": sheet.unit,
                "unit_kind": unit_kind(sheet.unit),
                # The sheet's declared INPUT cells. Its DERIVED cells are BoG's
                # own formulas and are recorded at package level as
                # ``template_formula`` — they are not rows here.
                "authority": ReportAuthority.TEMPLATE_INPUT_MAPPING.value,
            }
        )
    return sections


def authority_counts(result: FormResult) -> dict[str, int]:
    """Per-field authority tally for the package's provenance block.

    Counts every declared input line by its owning authority, plus every
    evaluated formula cell as ``template_formula`` — so the record states, in
    numbers, how much of the return is BoG's own arithmetic versus the
    platform's mapping, and how much is honestly not yet sourced.
    """
    counts: dict[str, int] = {}
    for lv in result.lines:
        key = line_status_authority(lv.status, lv.source).value
        counts[key] = counts.get(key, 0) + 1
    if result.unmapped_cells:
        key = ReportAuthority.NOT_YET_SOURCED.value
        counts[key] = counts.get(key, 0) + len(result.unmapped_cells)
    if result.formulas:
        counts[ReportAuthority.TEMPLATE_FORMULA.value] = len(result.formulas)
    return counts


def unvalidated_findings(result: FormResult) -> list[dict[str, str]]:
    """WARNING, once, when this return was compiled off a partly unvalidated book.

    The rule id and the severity reasoning live in
    ``common.unvalidated_book_finding`` — shared with the Large Exposures and
    LMT generators, which read the same canonical book under the same exclusion
    and must reach an approver under the same rule.
    """
    return unvalidated_book_finding(result.unvalidated_note)


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
    provenance: dict[str, Any] | None = None,
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
        # EMPTY BY DESIGN, and the ``provenance`` block below is what says so.
        # A generic family builds a headline ``totals`` list because it owns its
        # own arithmetic; an official BoG return does not — its roll-ups are the
        # template's own formula cells, evaluated as published, and a totals
        # section assembled here would be a roll-up BoG never printed on the
        # form. The package validation pipeline reads the declaration rather
        # than the generator name: ``validation._template_authoritative_rollups``
        # excuses this block only for a snapshot whose authority record is
        # ``template_formula`` AND names the committed template digest it
        # evaluated, so a family that genuinely owes headline totals still
        # ERRORs when it omits them.
        "totals": [],
        # The authority record (forensic audit §8 / §10 item 3). ``source_runs``
        # is empty for a BoG form because BoG's template produced the figures,
        # and this block SAYS so instead of leaving an examiner to infer it.
        "provenance": provenance,
        "metadata": {
            "generated_at": datetime.now(UTC).isoformat(),
            "basis": spec.basis,
            "official_workbook": spec.workbook,
            "official_sheets": list(result.layout.sheet_names),
            "line_status_counts": counts,
            "missing_dependencies": result.missing_dependencies,
            # Folded into the package validation report by
            # ``validation._generation_note_findings``, so an unvalidated book
            # reaches the approver as a WARNING they must read rather than as a
            # figure that is quietly short (forensic re-audit D-4).
            "generation_findings": unvalidated_findings(result),
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
            # Carried in the immutable payload so the export path — which never
            # recomputes — can print the same disclosure on the Completion notes
            # sheet of the artifact that is actually filed.
            "unvalidated_rows": result.unvalidated_rows,
            "unvalidated_note": result.unvalidated_note,
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
    provenance = build_template_provenance(
        definition=definition,
        bank=bank,
        effective_date=period.period_end,
        form_code=spec.code,
        workbook=spec.workbook,
        authority_counts=authority_counts(result),
        formula_cells_evaluated=len(result.formulas),
    ).to_dict()
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
        provenance=provenance,
    )
    # source_runs stays EMPTY, and that is now a statement rather than a hole:
    # no calculation run produced these figures because the official Bank of
    # Ghana workbook did, by evaluating its own formulas over official input
    # cells. snapshot["provenance"] carries the template digest, the line-map
    # digest and the evaluator version that stand in place of a run lineage.
    return GeneratedReturn(snapshot=snapshot, source_runs=[])


BOG_GENERATORS = {"bog_form": generate_bog_form}
