"""Compute one official BoG form: fill inputs → evaluate the template's formulas.

Every INPUT cell of the official layout gets a value from its :class:`LineSpec`
resolver (status ``mapped``), or ``None`` with status ``input_required`` (the
line is declared but its data does not exist yet) / ``unmapped`` (no line spec
covers the cell). Every FORMULA cell is then evaluated by the template's own
arithmetic (:mod:`formulas`), so subtotals, totals, the Summary sheet and
cross-form links (``[1]BSD2!D38``) are BoG's, never re-implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod
from app.services.regulatory_reporting.common import unvalidated_book_detail, unvalidated_book_rows

from . import sources_ext  # noqa: F401 — registers per-form resolvers
from .formulas import UnsupportedFormulaError, WorkbookEvaluator, workbook_units
from .layout import FormLayout, load_layout
from .sources import ResolveContext, get_resolver
from .spec import UNIT_DIVISOR, FormSpec, LineStatus, LineValue


@dataclass
class FormResult:
    spec: FormSpec
    layout: FormLayout
    #: resolved input lines (declared by the line map)
    lines: list[LineValue]
    #: raw input-cell values in BASE units, keyed (sheet, ref)
    inputs: dict[tuple[str, str], Any]
    #: evaluated formula-cell values in BASE units, keyed (sheet, ref)
    formulas: dict[tuple[str, str], Any]
    #: official input cells with NO line spec (emitted blank, listed in notes)
    unmapped_cells: list[tuple[str, str, str]]  # (sheet, ref, row label)
    #: cross-form links the evaluator could not resolve
    unresolved_external: list[tuple[str, str, str]]
    #: dependency forms that were unavailable
    missing_dependencies: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: current-generation canonical rows at the reporting date that did NOT pass
    #: validation and are therefore excluded from every line above, per entity
    #: per status (forensic re-audit D-4). Empty means the book is validated.
    unvalidated_rows: dict[str, dict[str, int]] = field(default_factory=dict)
    #: the disclosure sentence for :attr:`unvalidated_rows`, composed ONCE at
    #: generation so the validation report, the sealed export and the working
    #: copy cannot word the same exclusion three different ways. Empty when the
    #: book is fully validated.
    unvalidated_note: str = ""
    #: formula cells whose precedents are ALL unscaled cells (counts, percents)
    #: — exported without the sheet divisor, like their inputs
    unscaled_formulas: set[tuple[str, str]] = field(default_factory=set)

    @property
    def status_counts(self) -> dict[LineStatus, int]:
        counts: dict[LineStatus, int] = {
            "mapped": 0,
            "input_required": 0,
            "unmapped": len(self.unmapped_cells),
            "derived": len(self.formulas),
        }
        for line in self.lines:
            if line.status in ("mapped", "input_required"):
                counts[line.status] += 1
        return counts

    def value(self, sheet: str, ref: str) -> Any:
        key = (sheet, ref)
        if key in self.formulas:
            return self.formulas[key]
        return self.inputs.get(key)

    @property
    def unscaled_cells(self) -> set[tuple[str, str]]:
        """Cells whose value is already in the sheet's unit (never divided):
        unscaled input lines plus formulas built only from them."""
        return {(lv.sheet, lv.cell) for lv in self.lines if lv.unscaled} | self.unscaled_formulas

    def all_values(self) -> dict[tuple[str, str], Any]:
        merged: dict[tuple[str, str], Any] = dict(self.inputs)
        merged.update(self.formulas)
        return merged

    @classmethod
    def from_snapshot(cls, spec: FormSpec, snapshot: dict[str, Any]) -> FormResult:
        """Rebuild a result from an immutable package snapshot (export path).

        Cell values come straight from ``snapshot["bog_form"]["cells"]`` — the
        exporter never recomputes, so what was approved is what is rendered.
        """
        payload = snapshot.get("bog_form") or {}
        layout = load_layout(spec.code)
        cells: dict[tuple[str, str], Any] = {}
        for sheet, refs in (payload.get("cells") or {}).items():
            for ref, value in refs.items():
                cells[(sheet, ref)] = value
        formula_keys = {
            (s.name, c.ref) for s in layout.sheets for c in s.cells if c.kind == "formula"
        }
        inputs = {k: v for k, v in cells.items() if k not in formula_keys}
        formulas = {k: v for k, v in cells.items() if k in formula_keys}
        lines: list[LineValue] = []
        for section in snapshot.get("sections", []):
            for row in section.get("rows", []):
                raw = row.get("value")
                lines.append(
                    LineValue(
                        sheet=str(section.get("title", "")),
                        code=str(row.get("code", "")),
                        label=str(row.get("description", "")),
                        column=str(row.get("column", "")),
                        cell=str(row.get("cell", "")),
                        value=float(raw) if raw not in (None, "") else None,
                        status=row.get("status", "mapped"),  # type: ignore[arg-type]
                        source=row.get("source") or None,
                        notes=str(row.get("notes", "")),
                        unscaled=bool(row.get("unscaled", False)),
                    )
                )
        return cls(
            spec=spec,
            layout=layout,
            lines=lines,
            inputs=inputs,
            formulas=formulas,
            unmapped_cells=[tuple(item) for item in payload.get("unmapped_cells", [])],  # type: ignore[misc]
            unresolved_external=[tuple(item) for item in payload.get("unresolved_external", [])],  # type: ignore[misc]
            missing_dependencies=list(payload.get("missing_dependencies", [])),
            errors=list(payload.get("errors", [])),
            unscaled_formulas={
                (str(sheet), str(ref)) for sheet, ref in payload.get("unscaled_formulas", [])
            },
            unvalidated_rows={
                str(table): {str(k): int(v) for k, v in (statuses or {}).items()}
                for table, statuses in (payload.get("unvalidated_rows") or {}).items()
            },
            unvalidated_note=str(payload.get("unvalidated_note") or ""),
        )


def _to_number(value: Any) -> float | int | str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bool):
        return int(value)
    return value


def compute_form(  # noqa: PLR0912, PLR0913, PLR0915
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    spec: FormSpec,
    *,
    dependencies: dict[str, FormResult] | None = None,
) -> FormResult:
    """Resolve every declared line, then evaluate the workbook.

    ``dependencies`` supplies already-computed forms this one references (e.g.
    BSD8 needs BSD2). A missing dependency does not fail the form: the linked
    cells resolve to blank and the dependency is reported.
    """
    layout = load_layout(spec.code)
    dependencies = dependencies or {}
    dep_values = {code: result.all_values() for code, result in dependencies.items()}
    missing_dependencies = [code for code in spec.depends_on if code not in dependencies]

    lines: list[LineValue] = []
    inputs: dict[tuple[str, str], Any] = {}
    errors: list[str] = []
    cache: dict[str, Any] = {}

    for sheet_spec in spec.sheets:
        sheet_layout = layout.sheet(sheet_spec.name)
        for line in sheet_spec.lines:
            for column, ref in line.cells.items():
                cell = sheet_layout.by_ref.get(ref)
                if cell is not None and cell.kind == "formula":
                    errors.append(
                        f"{spec.code}/{sheet_spec.name}!{ref} ({line.code}) is a template "
                        "FORMULA cell; line maps may only bind INPUT cells"
                    )
                    continue
                if line.source is None:
                    lines.append(
                        LineValue(
                            sheet=sheet_spec.name,
                            code=line.code,
                            label=line.label,
                            column=column,
                            cell=ref,
                            value=None,
                            status="input_required",
                            source=None,
                            notes=line.notes,
                            unscaled=line.unscaled,
                        )
                    )
                    inputs[(sheet_spec.name, ref)] = None
                    continue
                rc = ResolveContext(
                    db=db,
                    ctx=ctx,
                    bank=bank,
                    period=period,
                    column=column,
                    dependencies=dep_values,
                    cache=cache,
                )
                try:
                    raw = get_resolver(line.source)(rc, dict(line.params))
                except Exception as exc:  # noqa: BLE001 — one bad line must not sink the form
                    errors.append(f"{spec.code}/{sheet_spec.name}!{ref} ({line.code}): {exc}")
                    raw = None
                value = _to_number(raw)
                status: LineStatus = "mapped" if value is not None else "input_required"
                lines.append(
                    LineValue(
                        sheet=sheet_spec.name,
                        code=line.code,
                        label=line.label,
                        column=column,
                        cell=ref,
                        value=value if isinstance(value, (int, float)) else None,
                        status=status,
                        source=line.source,
                        notes=line.notes,
                        unscaled=line.unscaled,
                    )
                )
                inputs[(sheet_spec.name, ref)] = value

    # official input cells nobody declared → blank + unmapped
    unmapped: list[tuple[str, str, str]] = []
    declared = {(lv.sheet, lv.cell) for lv in lines}
    for sheet_layout in layout.sheets:
        for cell in sheet_layout.input_cells:
            key = (sheet_layout.name, cell.ref)
            if key not in declared:
                unmapped.append((sheet_layout.name, cell.ref, sheet_layout.label_for_row(cell.row)))
                inputs.setdefault(key, None)

    formulas = {
        (s.name, c.ref): c.formula or ""
        for s in layout.sheets
        for c in s.cells
        if c.kind == "formula"
    }

    def external(index: int, sheet: str, ref: str) -> Any:
        # Template external links are numbered in workbook order; the ONLY
        # cross-form link in the official set is BSD8 → [1]BSD2, so map index
        # 1 to the first declared dependency (documented in the BSD8 line map).
        ordered = list(spec.depends_on)
        if not ordered or index - 1 >= len(ordered):
            return None
        dep = dep_values.get(ordered[index - 1])
        return None if dep is None else dep.get((sheet, ref))

    evaluator = WorkbookEvaluator(formulas, inputs, external=external)
    evaluated: dict[tuple[str, str], Any] = {}
    for key in formulas:
        try:
            evaluated[key] = _to_number(evaluator.value(*key))
        except UnsupportedFormulaError as exc:
            errors.append(f"{spec.code}/{key[0]}!{key[1]}: {exc}")
            evaluated[key] = None
    unscaled_formulas = workbook_units(
        formulas, {(lv.sheet, lv.cell) for lv in lines if lv.unscaled}, spec.depends_on
    )
    unvalidated = unvalidated_book_rows(db, ctx, bank, as_of=period.period_end)
    return FormResult(
        spec=spec,
        layout=layout,
        lines=lines,
        inputs=inputs,
        formulas=evaluated,
        unmapped_cells=unmapped,
        unresolved_external=list(evaluator.unresolved_external),
        missing_dependencies=missing_dependencies,
        errors=errors,
        unscaled_formulas=unscaled_formulas,
        # What the resolvers above REFUSED to read, measured once per form. The
        # exclusion itself is D-4's fix; recording it is the other half — a
        # return compiled off a partly unvalidated book must say so on its own
        # face, not leave an examiner to infer it from a figure that is simply
        # smaller than it should be.
        unvalidated_rows=unvalidated,
        unvalidated_note=unvalidated_book_detail(unvalidated, as_of=period.period_end) or "",
    )


def scale_for_export(spec: FormSpec, sheet_name: str, value: Any, *, unscaled: bool = False) -> Any:
    """Convert a base-unit value to the sheet's official unit (¢'Million …)."""
    if value is None or isinstance(value, str) or unscaled:
        return value
    sheet = spec.sheet(sheet_name)
    divisor = UNIT_DIVISOR[sheet.unit] if sheet is not None else 1
    if divisor == 1:
        return value
    return float(Decimal(str(value)) / Decimal(divisor))
