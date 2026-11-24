"""The AequorOS Reporting Gateway's return catalogue, derived from AequorOS templates.

The gateway (``gateway/``) validates every filing against a published return
definition. For ``local``, ``test`` and ``sandbox`` those definitions are this
module's output: ``scripts/export_gateway_catalog.py`` writes one JSON document
per return into ``gateway/api/app/returns/catalog/``, and the gateway loads them as
data (it imports nothing from AequorOS). The same definitions build every filing
AequorOS sends (:func:`gateway_payload`), so what AequorOS files and what the gateway
expects are the same structure by construction; a freshness test fails when a
template changes and the exported catalogue does not.

Two shapes, matching what the templates themselves fix:

* **Official BoG forms** (``bog_forms``, BSD1 to BSD17). Every declared input cell
  is a line, coded exactly as the AequorOS snapshot codes it
  (``BSD2.R7.domestic``). Every template formula that is a plain sum or difference
  of other cells is a line too (coded ``<sheet>!<cell>``, e.g. ``BSD2!D7``), and the
  formula itself becomes an arithmetic rule, so the gateway checks a filing with the
  workbook's own totals. Formulas the workbook computes some other way (ratios,
  ``IF``, links to another form) are left to the workbook and are not filed.
* **Platform returns** (LMT, CAR-RWA, the daily bank return, ...). Their rows come
  from the bank's own data (row ``1.1`` of the HQLA table is the bank's first HQLA
  item), so the template fixes the sections and columns but not the rows. Each
  section is *itemised*: the filer sends its rows with their labels, coded
  ``<section>:<row>`` (other numeric columns as ``<section>:<row>:<column>``), and the
  gateway checks the structure.

Values travel as exact decimal strings in the unit the section declares (the
official sheet's own unit for BSD forms; currency units for platform returns),
never as floats and never rounded.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import cache
from typing import Any, Literal, cast

from app.reporting.models.packages import RegulatoryPackage
from app.reporting.service.bog_forms.catalog import all_form_specs, form_spec
from app.reporting.service.bog_forms.engine import FormResult
from app.reporting.service.bog_forms.formulas import (
    UnsupportedFormulaError,
    expand_range,
    split_reference,
    tokenize,
)
from app.reporting.service.bog_forms.layout import FormLayout, SheetLayout, load_layout
from app.reporting.service.bog_forms.registry_entries import sheet_section_code
from app.reporting.service.bog_forms.spec import UNIT_DIVISOR, FormSpec, SheetSpec
from app.reporting.service.channels.errors import ChannelPreconditionError
from app.reporting.service.registry import REGISTRY, ReturnDefinition
from app.reporting.service.templates import ColumnSpec, ReturnTemplate, get_template

FORMAT = "aequoros-gateway-return/1"
#: The catalogue release. Bump it whenever an exported structure changes, so the
#: gateway publishes the change as a new version instead of rewriting one in use.
CATALOG_VERSION = "2026.2"
#: Every exported version applies from this reporting date on.
FIRST_REPORTING_DATE = date(2025, 1, 1)

type LineKind = Literal["number", "value", "text"]
type JsonObject = dict[str, object]

#: AequorOS institution classes → the gateway's institution types.
_APPLIES_TO: dict[str, tuple[str, ...]] = {
    "bank": ("universal_bank",),
    "sdi": ("rural_bank", "savings_and_loans", "microfinance"),
}
#: Families whose filings are documents (reports, narrative packs), not line returns.
_DOCUMENT_FAMILIES = frozenset({"icaap", "icaap_stress", "stress", "corporate"})
_DECIMAL = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")
_ROW_TOKEN = re.compile(r"^[^:\s][^:]*$")
_SPACES = re.compile(r"\s+")
#: Rounding allowance per figure in an arithmetic check: each figure may be
#: rounded to two decimal places of its unit.
_ROUNDING = Decimal("0.005")

_COLUMN_LABELS: dict[str, str] = {
    "lt_1m": "up to 1 month",
    "1m_lt_3m": "1 to 3 months",
    "3m_lt_6m": "3 to 6 months",
    "6m_lt_1y": "6 to 12 months",
    "1y_lt_3y": "1 to 3 years",
    "3y_lt_5y": "3 to 5 years",
    "5y_plus": "over 5 years",
    "fcy_amount": "foreign currency amount",
    "pct_of_net_worth": "% of net worth",
    "no": "number",
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
    "sun": "Sunday",
}


# ---------------------------------------------------------------------------
# Definition model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogLine:
    code: str
    label: str
    kind: LineKind
    total: bool = False
    #: Only when it differs from the section's unit.
    unit: str | None = None
    #: Where the value comes from in an AequorOS snapshot: an input row, or a
    #: template formula cell ``(sheet, ref)``.
    formula_cell: tuple[str, str] | None = None
    #: The line's figure is already in its unit (the sheet divisor never applies).
    unscaled: bool = False
    #: For a formula line: what its base-unit value is divided by when filed.
    divisor: int = 1


@dataclass(frozen=True)
class CatalogColumn:
    """One column of an itemised section (``value`` is the row's own key)."""

    key: str
    label: str
    kind: LineKind
    unit: str


@dataclass(frozen=True)
class CatalogSection:
    code: str
    title: str
    unit: str
    itemised: bool
    optional: bool = False
    lines: tuple[CatalogLine, ...] = ()
    columns: tuple[CatalogColumn, ...] = ()


@dataclass(frozen=True)
class CatalogDefinition:
    code: str
    name: str
    cadence: str
    basis: str
    applies_to: tuple[str, ...]
    template_id: str
    fidelity: str
    workbook: str | None
    sections: tuple[CatalogSection, ...]
    rules: tuple[JsonObject, ...] = field(default=())

    @property
    def fixed_lines(self) -> list[CatalogLine]:
        return [line for section in self.sections for line in section.lines]

    def to_json(self) -> JsonObject:
        return {
            "format": FORMAT,
            "code": self.code,
            "name": self.name,
            "cadence": self.cadence,
            "basis": self.basis,
            "applies_to": list(self.applies_to),
            "version": CATALOG_VERSION,
            "first_reporting_date": FIRST_REPORTING_DATE.isoformat(),
            "source": {
                "system": "AequorOS",
                "template": self.template_id,
                "fidelity": self.fidelity,
                "workbook": self.workbook,
            },
            "sections": [_section_json(section) for section in self.sections],
            "rules": list(self.rules),
        }


def _section_json(section: CatalogSection) -> JsonObject:
    out: JsonObject = {
        "code": section.code,
        "title": section.title,
        "unit": section.unit,
        "itemised": section.itemised,
    }
    if section.optional:
        out["optional"] = True
    if section.itemised:
        out["columns"] = [
            {"key": c.key, "label": c.label, "kind": c.kind, "unit": c.unit}
            for c in section.columns
        ]
        return out
    lines: list[JsonObject] = []
    for line in section.lines:
        item: JsonObject = {"code": line.code, "label": line.label, "kind": line.kind}
        if line.total:
            item["total"] = True
        if line.unit is not None:
            item["unit"] = line.unit
        lines.append(item)
    out["lines"] = lines
    return out


# ---------------------------------------------------------------------------
# Official BoG forms
# ---------------------------------------------------------------------------


def _clean(text: object) -> str:
    return _SPACES.sub(" ", str(text)).strip()


def _column_label(key: str) -> str:
    parts = [_COLUMN_LABELS.get(part, part.replace("_", " ")) for part in key.split(".")]
    return ", ".join(parts)


@dataclass(frozen=True)
class _Term:
    sign: int
    sheet: str
    ref: str
    in_range: bool


class _LinearParser:
    """Reads a template formula as signed cells, or gives up (``None``) on anything else."""

    def __init__(self, tokens: list[tuple[str, str]], sheet: str) -> None:
        self.tokens = tokens
        self.position = 0
        self.sheet = sheet

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.tokens[self.position]
        self.position += 1
        return token

    def reference(self, token: str, *, in_range: bool, sign: int) -> list[_Term] | None:
        cell = split_reference(token)
        if cell.external is not None:
            return None
        target = cell.sheet or self.sheet
        if ":" in cell.ref:
            first, last = cell.ref.split(":", 1)
            return [_Term(sign, target, ref, True) for ref in expand_range(first, last)]
        return [_Term(sign, target, cell.ref, in_range)]

    def closes(self, text: str) -> bool:
        token = self.peek()
        if token is None or token[1] != text:
            return False
        self.take()
        return True

    def sum_arguments(self, sign: int) -> list[_Term] | None:
        if not self.closes("("):
            return None
        collected: list[_Term] = []
        while True:
            current = self.peek()
            if current is None:
                return None
            if current[0] == "range":
                self.take()
                part = self.reference(current[1], in_range=True, sign=sign)
            else:
                part = self.expression(sign)
            if part is None:
                return None
            collected.extend(part)
            if self.closes(")"):
                return collected
            if not self.closes(","):
                return None

    def term(self, sign: int) -> list[_Term] | None:
        if self.peek() is None:
            return None
        kind, text = self.take()
        if kind == "ref":
            return self.reference(text, in_range=False, sign=sign)
        if kind == "op" and text == "(":
            inner = self.expression(sign)
            return inner if inner is not None and self.closes(")") else None
        if kind == "func" and text.upper() == "SUM":
            return self.sum_arguments(sign)
        return None

    def sign_token(self) -> str | None:
        token = self.peek()
        if token is not None and token[0] == "op" and token[1] in ("+", "-"):
            self.take()
            return token[1]
        return None

    def expression(self, sign: int) -> list[_Term] | None:
        collected: list[_Term] = []
        lead = self.sign_token()
        current = -sign if lead == "-" else sign
        while True:
            part = self.term(current)
            if part is None:
                return None
            collected.extend(part)
            operator = self.sign_token()
            if operator is None:
                return collected
            current = -sign if operator == "-" else sign


def _linear_terms(formula: str, sheet: str) -> list[_Term] | None:
    """A formula's cells with their signs when it is a plain sum or difference.

    ``None`` for anything else: products, ratios, ``IF``, constants, links to
    another workbook. An empty formula (``=``) has no terms.
    """
    try:
        tokens = tokenize(formula)
    except UnsupportedFormulaError:
        return None
    if not tokens:
        return []
    parser = _LinearParser(tokens, sheet)
    terms = parser.expression(1)
    if terms is None or parser.position != len(tokens):
        return None
    return terms


@dataclass
class _FormIndex:
    spec: FormSpec
    layout: FormLayout
    #: (sheet, ref) → (input line code, unscaled)
    inputs: dict[tuple[str, str], tuple[str, bool]]
    formulas: dict[tuple[str, str], str]
    #: memo: (sheet, ref) → resolved state
    states: dict[tuple[str, str], _Resolved | None] = field(default_factory=dict)
    visiting: set[tuple[str, str]] = field(default_factory=set)


@dataclass(frozen=True)
class _Resolved:
    """A cell as a filed line: zero (always blank) or a line with its scale."""

    code: str | None
    divisor: int
    plus: tuple[str, ...] = ()
    minus: tuple[str, ...] = ()
    formula: str | None = None


def _sheet(index: _FormIndex, name: str) -> SheetLayout | None:
    for sheet in index.layout.sheets:
        if sheet.name == name:
            return sheet
    return None


def _sheet_spec(index: _FormIndex, name: str) -> SheetSpec | None:
    return index.spec.sheet(name)


def _divisor(index: _FormIndex, sheet: str, *, unscaled: bool) -> int:
    spec = _sheet_spec(index, sheet)
    if unscaled or spec is None:
        return 1
    return UNIT_DIVISOR[spec.unit]


def _resolve(index: _FormIndex, key: tuple[str, str], *, in_range: bool) -> _Resolved | None:
    """``key`` as part of an arithmetic check, or ``None`` when it cannot be one.

    An input line is itself; a blank or unmapped input cell is zero; a text label
    is zero inside ``SUM`` (as in the workbook) and disqualifying elsewhere; a
    linear formula is a line of its own; any other formula disqualifies.
    """
    if key in index.inputs:
        code, unscaled = index.inputs[key]
        return _Resolved(code, _divisor(index, key[0], unscaled=unscaled))
    if key in index.formulas:
        return _formula(index, key)
    sheet = _sheet(index, key[0])
    if sheet is None:
        return None
    cell = sheet.by_ref.get(key[1])
    if cell is not None and cell.kind == "label" and not in_range:
        return None
    return _Resolved(None, 0)


def _formula(index: _FormIndex, key: tuple[str, str]) -> _Resolved | None:
    if key in index.states:
        return index.states[key]
    if key in index.visiting:
        return None
    index.visiting.add(key)
    try:
        resolved = _formula_uncached(index, key)
    finally:
        index.visiting.discard(key)
    index.states[key] = resolved
    return resolved


def _formula_uncached(index: _FormIndex, key: tuple[str, str]) -> _Resolved | None:
    formula = index.formulas[key]
    terms = _linear_terms(formula, key[0])
    if terms is None:
        return None
    plus: list[str] = []
    minus: list[str] = []
    divisors: set[int] = set()
    for term in terms:
        part = _resolve(index, (term.sheet, term.ref), in_range=term.in_range)
        if part is None:
            return None
        if part.code is None:
            continue
        divisors.add(part.divisor)
        (plus if term.sign > 0 else minus).append(part.code)
    if not plus and not minus:
        return _Resolved(None, 0)
    if len(divisors) != 1:
        return None
    divisor = divisors.pop()
    own = _divisor(index, key[0], unscaled=False)
    if divisor not in (1, own):
        return None
    return _Resolved(f"{key[0]}!{key[1]}", divisor, tuple(plus), tuple(minus), formula.lstrip("="))


def _row_number(ref: str) -> int:
    return int("".join(ch for ch in ref if ch.isdigit()))


def _column_number(ref: str) -> int:
    number = 0
    for ch in ref:
        if not ch.isalpha():
            break
        number = number * 26 + (ord(ch.upper()) - 64)
    return number


def _form_definition(spec: FormSpec, definition: ReturnDefinition) -> CatalogDefinition:
    layout = load_layout(spec.code)
    inputs: dict[tuple[str, str], tuple[str, bool]] = {}
    for sheet in spec.sheets:
        for line in sheet.lines:
            for column, ref in line.cells.items():
                inputs[(sheet.name, ref)] = (f"{line.code}.{column}", line.unscaled)
    formulas = {
        (sheet.name, cell.ref): cell.formula or "="
        for sheet in layout.sheets
        for cell in sheet.cells
        if cell.kind == "formula"
    }
    index = _FormIndex(spec, layout, inputs, formulas)
    resolved = {
        key: state
        for key in sorted(formulas, key=lambda k: (k[0], _row_number(k[1]), _column_number(k[1])))
        if (state := _formula(index, key)) is not None and state.code is not None
    }
    used: set[str] = set()
    rules: list[JsonObject] = []
    for number, state in enumerate(resolved.values(), start=1):
        assert state.code is not None
        used.update((state.code, *state.plus, *state.minus))
        figures = 1 + len(state.plus) + len(state.minus)
        rule: JsonObject = {
            "id": f"{spec.code}-ARI-{number:04d}",
            "severity": "error",
            "kind": "sum",
            "target": state.code,
            "terms": list(state.plus),
            "tolerance": _decimal_text(_ROUNDING * figures),
            "description": f"{state.code} follows the template formula ={state.formula}",
        }
        if state.minus:
            rule["subtract"] = list(state.minus)
        rules.append(rule)

    sections: list[CatalogSection] = []
    for sheet_spec in spec.sheets:
        sheet_layout = _sheet(index, sheet_spec.name)
        entries: list[tuple[tuple[int, int], CatalogLine]] = []
        for line in sheet_spec.lines:
            label = _clean(line.label)
            several = len(line.cells) > 1
            for column, ref in line.cells.items():
                code = f"{line.code}.{column}"
                kind: LineKind = (
                    "text" if sheet_spec.unit == "text" else "number" if code in used else "value"
                )
                unit = "units" if line.unscaled and UNIT_DIVISOR[sheet_spec.unit] != 1 else None
                entries.append(
                    (
                        (_row_number(ref), _column_number(ref)),
                        CatalogLine(
                            code=code,
                            label=f"{label} — {_column_label(column)}" if several else label,
                            kind=kind,
                            unit=unit,
                            unscaled=line.unscaled,
                        ),
                    )
                )
        for (sheet_name, ref), state in resolved.items():
            if sheet_name != sheet_spec.name:
                continue
            assert state.code is not None
            row_label = sheet_layout.label_for_row(_row_number(ref)) if sheet_layout else ""
            unscaled = state.divisor == 1 and UNIT_DIVISOR[sheet_spec.unit] != 1
            entries.append(
                (
                    (_row_number(ref), _column_number(ref)),
                    CatalogLine(
                        code=state.code,
                        label=_clean(row_label) or f"Cell {ref}",
                        kind="number",
                        total=True,
                        unit="units" if unscaled else None,
                        formula_cell=(sheet_name, ref),
                        unscaled=unscaled,
                        divisor=state.divisor,
                    ),
                )
            )
        entries.sort(key=lambda item: item[0])
        sections.append(
            CatalogSection(
                code=sheet_section_code(sheet_spec.name),
                title=sheet_spec.name,
                unit=sheet_spec.unit,
                itemised=False,
                lines=tuple(line for _, line in entries),
            )
        )
    return CatalogDefinition(
        code=spec.code,
        name=spec.title,
        cadence=spec.frequency,
        basis=spec.basis,
        applies_to=_applies_to(definition),
        template_id=definition.template_id,
        fidelity=definition.fidelity,
        workbook=spec.workbook,
        sections=tuple(sections),
        rules=tuple(rules),
    )


# ---------------------------------------------------------------------------
# Platform returns (itemised sections)
# ---------------------------------------------------------------------------

_COLUMN_KIND: dict[str, LineKind] = {
    "ghs": "number",
    "pct": "number",
    "number": "number",
    "auto": "value",
    "text": "text",
    "bool": "text",
}
_COLUMN_UNIT: dict[str, str] = {"ghs": "units", "pct": "percent"}
TOTALS_SECTION = "totals"


def _itemised_columns(columns: Sequence[ColumnSpec]) -> tuple[CatalogColumn, ...]:
    return tuple(
        CatalogColumn(
            key=column.key,
            label=column.header,
            kind=_COLUMN_KIND.get(column.kind, "value"),
            unit=_COLUMN_UNIT.get(column.kind, "units"),
        )
        for column in columns
        if column.key not in ("code", "description")
    )


def _platform_definition(
    definition: ReturnDefinition, template: ReturnTemplate
) -> CatalogDefinition:
    sections = [
        CatalogSection(
            code=layout.section_code,
            title=layout.sheet_title,
            unit="units",
            itemised=True,
            optional=layout.optional,
            columns=_itemised_columns(layout.columns),
        )
        for layout in template.sections
    ]
    sections.append(
        CatalogSection(
            code=TOTALS_SECTION,
            title="Headline totals",
            unit="units",
            itemised=True,
            optional=True,
            columns=(CatalogColumn("value", "Value", "value", "units"),),
        )
    )
    rules: list[JsonObject] = [
        {
            "id": f"{definition.code}-STR-{number:03d}",
            "severity": "error",
            "kind": "present",
            "section": section.code,
            "description": f"{section.title} is reported",
        }
        for number, section in enumerate((s for s in sections if not s.optional), start=1)
    ]
    return CatalogDefinition(
        code=definition.code,
        name=_clean(definition.title),
        cadence=definition.frequency,
        basis="solo",
        applies_to=_applies_to(definition),
        template_id=definition.template_id,
        fidelity=definition.fidelity,
        workbook=None,
        sections=tuple(sections),
        rules=tuple(rules),
    )


def _platform_eligible(definition: ReturnDefinition, template: ReturnTemplate | None) -> bool:
    return (
        template is not None
        and definition.generator not in ("bog_form", "template_pending")
        and not definition.event_driven
        and definition.filing_format != "pdf"
        and definition.family not in _DOCUMENT_FAMILIES
        and (
            definition.allowed_channels is None
            or "reporting_gateway" in definition.allowed_channels
        )
        and all(layout.presentation == "table" for layout in template.sections)
    )


def _applies_to(definition: ReturnDefinition) -> tuple[str, ...]:
    types: list[str] = []
    for institution_class in definition.institution_classes:
        for gateway_type in _APPLIES_TO.get(institution_class, ()):
            if gateway_type not in types:
                types.append(gateway_type)
    return tuple(types)


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


@cache
def accepted_codes() -> frozenset[str]:
    """The returns the gateway carries a definition for (cheap: no template parsing)."""
    codes = {spec.code for spec in all_form_specs() if spec.code in REGISTRY}
    for code, definition in REGISTRY.items():
        if code not in codes and _platform_eligible(
            definition, get_template(definition.template_id)
        ):
            codes.add(code)
    return frozenset(codes)


@cache
def definition_for(return_code: str) -> CatalogDefinition | None:
    """The gateway definition for ``return_code``, built on first use and kept."""
    if return_code not in accepted_codes():
        return None
    definition = REGISTRY[return_code]
    if definition.generator == "bog_form":
        return _form_definition(form_spec(return_code), definition)
    template = get_template(definition.template_id)
    assert template is not None
    return _platform_definition(definition, template)


def catalogue() -> dict[str, CatalogDefinition]:
    """Every return AequorOS can file through the gateway, by code."""
    out: dict[str, CatalogDefinition] = {}
    for code in sorted(accepted_codes()):
        built = definition_for(code)
        if built is not None:
            out[code] = built
    return out


def accepts(return_code: str) -> bool:
    """Whether the gateway carries a definition for ``return_code``."""
    return return_code in accepted_codes()


def exported_documents() -> dict[str, JsonObject]:
    """``{file name: JSON document}`` exactly as the gateway stores them."""
    return {f"{code}.json": definition.to_json() for code, definition in catalogue().items()}


# ---------------------------------------------------------------------------
# Filing payloads
# ---------------------------------------------------------------------------


def _decimal_text(value: Decimal) -> str:
    """``value`` as a plain decimal string: no exponent, no trailing zeros."""
    if value == value.to_integral_value():
        text = f"{value.quantize(Decimal(1)):f}"
    else:
        text = f"{value.normalize():f}"
    return "0" if text in ("-0", "") else text


def _number(code: str, raw: object) -> str:
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation as exc:
        raise ChannelPreconditionError(
            f"Line {code} must be a number for the gateway, but its value is {raw!r}."
        ) from exc
    if not value.is_finite():
        raise ChannelPreconditionError(f"Line {code} is not a finite number.")
    return _decimal_text(value)


def _value(code: str, raw: object, kind: LineKind) -> str | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if kind == "number":
        return _number(code, raw)
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if isinstance(raw, int | float | Decimal):
        return _number(code, raw)
    text = str(raw)
    return _number(code, text) if _DECIMAL.match(text.strip()) else text


def _rows(snapshot: Mapping[str, object]) -> Iterable[tuple[str, Mapping[str, object]]]:
    for section in cast(Sequence[Mapping[str, object]], snapshot.get("sections") or []):
        code = str(section.get("code") or "")
        for row in cast(Sequence[Mapping[str, object]], section.get("rows") or []):
            yield code, row


def _form_payload(
    definition: CatalogDefinition, snapshot: Mapping[str, object]
) -> dict[str, str | None]:
    spec = form_spec(definition.code)
    rows = {str(row.get("code")): row for _, row in _rows(snapshot)}
    result = FormResult.from_snapshot(spec, cast(dict[str, Any], snapshot))
    lines: dict[str, str | None] = {}
    for section in definition.sections:
        for line in section.lines:
            if line.formula_cell is None:
                row = rows.get(line.code)
                lines[line.code] = (
                    None if row is None else _value(line.code, row.get("value"), line.kind)
                )
                continue
            raw = cast(object, result.formulas.get(line.formula_cell))
            if raw is None or isinstance(raw, str):
                lines[line.code] = None if raw in (None, "") else _number(line.code, raw)
                continue
            lines[line.code] = _decimal_text(Decimal(str(raw)) / Decimal(line.divisor))
    return lines


def _platform_payload(
    definition: CatalogDefinition, snapshot: Mapping[str, object]
) -> tuple[dict[str, str | None], dict[str, str]]:
    sections = {section.code: section for section in definition.sections}
    lines: dict[str, str | None] = {}
    labels: dict[str, str] = {}

    def put(section: CatalogSection, row_code: str, row: Mapping[str, object]) -> None:
        if not _ROW_TOKEN.match(row_code):
            raise ChannelPreconditionError(
                f"Row {row_code!r} of {section.title} cannot be coded for the gateway."
            )
        key = f"{section.code}:{row_code}"
        labels[key] = _clean(row.get("description") or row_code)
        for column in section.columns:
            target = key if column.key == "value" else f"{key}:{column.key}"
            lines[target] = _value(target, row.get(column.key), column.kind)

    for section_code, row in _rows(snapshot):
        section = sections.get(section_code)
        code = str(row.get("code") or "").strip()
        if section is None or not code:
            continue
        put(section, code, row)
    totals = sections[TOTALS_SECTION]
    for total in cast(Sequence[Mapping[str, object]], snapshot.get("totals") or []):
        code = str(total.get("code") or "").strip()
        if code:
            put(totals, code, total)
    return lines, labels


def gateway_payload(package: RegulatoryPackage, currency: str) -> JsonObject:
    """The filing payload for ``package``, in the shape its gateway definition declares.

    Refuses (never rounds or guesses) a return the gateway does not carry, a row
    that cannot be coded, or a non-numeric value where a number is required.
    """
    definition = definition_for(package.return_code)
    if definition is None:
        raise ChannelPreconditionError(
            f"The AequorOS Reporting Gateway does not take {package.return_code} returns yet. "
            "File this return through another channel."
        )
    snapshot = cast(Mapping[str, object], package.snapshot or {})
    if definition.workbook is not None:
        lines = _form_payload(definition, snapshot)
        labels: dict[str, str] = {}
    else:
        lines, labels = _platform_payload(definition, snapshot)
    if not any(value is not None for value in lines.values()):
        raise ChannelPreconditionError("The package has no figures to file.")
    payload: JsonObject = {"currency": currency, "lines": lines}
    if labels:
        payload["labels"] = labels
    return payload


__all__ = [
    "CATALOG_VERSION",
    "FIRST_REPORTING_DATE",
    "FORMAT",
    "CatalogDefinition",
    "accepted_codes",
    "accepts",
    "catalogue",
    "definition_for",
    "exported_documents",
    "gateway_payload",
]
