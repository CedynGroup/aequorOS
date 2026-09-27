"""The rows an export carries, and the provenance printed beside them.

An export is the one BI artifact that leaves the platform. Once it has, nothing
the product knows travels with it — not the as-of date it was read at, not
whether the book reconciled that day, not which catalogue defined the measures,
not who took it. So every renderer in this package prints the same block, built
here once:

* **the query** — the measures, dimensions, filters and window that produced the
  rows, in the catalogue's own labels rather than member ids, so a reader who
  does not know the wire contract can still say what the sheet is;
* **the as-of date** — the window, not "today";
* **the trust status** — the reconciliation verdict for that window, with the
  failing checks named. Grey is "not assessed" and is never dressed up as a pass;
* **the catalogue version** — which definitions were in force;
* **the data scope** — the slice of the institution the caller was authorized
  over, so a partial book cannot be read as the whole one;
* **the user** — who took it.

Those six are ``docs/bi.md`` §Exports' list, in its order, and
:data:`METADATA_FIELDS` names them so a renderer cannot quietly drop one.
Everything after them is supplementary.

**Determinism.** Nothing here reads the clock. The watermark, the metadata block
and the cell text are functions of the query, the data and the principal alone,
which is what lets the PDF renderer promise byte-identical output for identical
input. A "generated at" line would be the one field that makes two otherwise
identical exports differ, and the value it would add — *when* — is already in
``bi_query_log`` and ``audit_events``, both of which are append-only and neither
of which a recipient can edit.

**The unit is the bank's.** Amount columns are labelled with the institution's
own reporting currency, resolved through ``jurisdictions.base_currency``. There
is no literal anywhere in this package.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.domain.bi.catalogue import Catalogue
from app.domain.bi.catalogue import UnknownMember as CatalogueUnknownMember
from app.domain.bi.catalogue.members import NUMERIC_VALUE_TYPES
from app.schemas.bi import BiFilter, BiQuery
from app.services.bi.compiler import ColumnSpec
from app.services.bi.exports.policy import CLASS_LABELS, ExportClass

#: The six provenance fields ``docs/bi.md`` §Exports requires on every export,
#: in its order. A renderer emits these before anything else, and a test asserts
#: all six reach the spreadsheet's metadata sheet.
METADATA_FIELDS: tuple[str, ...] = (
    "Query",
    "As at",
    "Data confidence",
    "Catalogue version",
    "Data scope",
    "Exported by",
)

#: The title every export carries. Neutral by construction: it names neither a
#: regulator nor a return, because a BI export is management information and is
#: not a filed artifact.
EXPORT_TITLE = "Business intelligence export"

#: What the artifact says about its own standing, on every page and in the
#: metadata block. Production copy, and the reason the regulatory exports and
#: these can never be confused for one another.
STANDING_NOTE = "Management information. Not a regulatory return and not a signed record of filing."

#: How a trust status reads to a banker. The vocabulary is
#: ``reconciliation``'s; the wording is the dashboard's badge wording, so the
#: file and the screen say the same thing.
TRUST_LABELS: Mapping[str, str] = {
    "green": "Reconciled",
    "amber": "Reconciled with exceptions",
    "red": "Does not reconcile",
    "grey": "Not assessed",
}

#: Displayed for a value the mart holds as NULL. Never a zero: a measure with no
#: rows and a measure that is zero are different statements.
BLANK = ""

#: Column formats whose values are numeric. DERIVED from the catalogue's own
#: vocabulary rather than restated: a value type added there and forgotten here
#: does not raise — it falls through to text, so the column exports as a STRING
#: and a reviewer's spreadsheet cannot total it. That is exactly what happened
#: when ``ratio`` was retired for ``fraction`` / ``index`` / ``duration_years``.
#: ``int`` is not a catalogue type; it is the subtotal marker the grid adds.
_NUMERIC_FORMATS: frozenset[str] = frozenset(NUMERIC_VALUE_TYPES) | {"int"}

#: A proportion of one, which a reader is shown multiplied by a hundred (the
#: catalogue's own definition of ``fraction``). Stated here so the export and
#: the screen cannot disagree about the same member: a figure that reads 6.20 %
#: in the browser and 0.06 in the audit twin is unciteable.
_FRACTION_SCALE = Decimal(100)

#: Leading characters a spreadsheet treats as the start of a formula. A
#: counterparty name is bank data and reaches a cell verbatim, so text is made
#: inert before it is written — the same defence the regulatory CSV exporter
#: applies to narrative cells.
_FORMULA_TRIGGERS: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")


def inert(value: str) -> str:
    """Text that cannot execute when the file is opened in a spreadsheet."""

    return f"'{value}" if value.startswith(_FORMULA_TRIGGERS) else value


@dataclass(frozen=True, slots=True)
class ExportColumn:
    """One column of the export: its heading and how its cells are typed."""

    id: str
    label: str
    #: A catalogue value type (``amount`` / ``pct`` / ``fraction`` / ``index`` /
    #: ``duration_years`` / ``count`` / ``text`` / ``date`` / ``flag``) or
    #: ``int`` for the subtotal marker.
    format: str

    @property
    def numeric(self) -> bool:
        return self.format in _NUMERIC_FORMATS


@dataclass(frozen=True, slots=True)
class ExportTable:
    """The rows an export carries, in the compiler's own column order.

    The order is the compiler's and is therefore deterministic for a given
    query: dimensions in the order requested, then measures, then comparison
    and pivot columns. No renderer may re-order or drop a column — a spreadsheet
    whose columns move between exports cannot be diffed.
    """

    columns: tuple[ExportColumn, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool

    @property
    def row_count(self) -> int:
        return len(self.rows)


def table_from(
    columns: Sequence[ColumnSpec], rows: Sequence[Sequence[Any]], *, truncated: bool, unit: str
) -> ExportTable:
    """The executor's result as an export table, with amount columns given a unit."""

    return ExportTable(
        columns=tuple(
            ExportColumn(
                id=spec.id,
                label=f"{spec.label} ({unit})" if spec.format == "amount" else spec.label,
                format=spec.format,
            )
            for spec in columns
        ),
        rows=tuple(tuple(row) for row in rows),
        truncated=truncated,
    )


@dataclass(frozen=True, slots=True)
class ExportContext:
    """Everything printed beside the rows. Built once, rendered three ways."""

    institution_id: str
    institution_name: str
    #: The institution's reporting currency, from ``jurisdictions.base_currency``.
    unit: str
    #: One readable sentence per clause of the query.
    query_lines: tuple[str, ...]
    as_of_label: str
    trust_status: str
    failing_checks: tuple[str, ...]
    catalogue_version: str
    data_scope_label: str
    user_label: str
    export_class: ExportClass
    build_fingerprint: str | None

    @property
    def trust_label(self) -> str:
        """The verdict, with the failing checks named when there are any."""

        label = TRUST_LABELS.get(self.trust_status, TRUST_LABELS["grey"])
        if not self.failing_checks:
            return label
        return f"{label} ({', '.join(self.failing_checks)})"

    @property
    def query_label(self) -> str:
        return "; ".join(self.query_lines)

    @property
    def watermark(self) -> str:
        """The attribution a leaked page still carries.

        Institution and recipient, and nothing else. It is deliberately not a
        timestamp: a watermark that changed every render would make two
        identical exports differ, and the time this file was produced is held in
        ``bi_query_log`` and ``audit_events`` where the recipient cannot edit it.
        """

        return f"{self.institution_name} · {self.user_label}"

    def metadata_rows(self) -> tuple[tuple[str, str], ...]:
        """``(field, value)`` for the artifact, spec fields first.

        The first six entries are :data:`METADATA_FIELDS` in order; the rest are
        supplementary and may grow without a renderer changing.
        """

        rows: list[tuple[str, str]] = [
            ("Query", self.query_label),
            ("As at", self.as_of_label),
            ("Data confidence", self.trust_label),
            ("Catalogue version", self.catalogue_version),
            ("Data scope", self.data_scope_label),
            ("Exported by", self.user_label),
            ("Institution", f"{self.institution_name} ({self.institution_id})"),
            ("Reporting unit", self.unit),
            ("Disclosure class", CLASS_LABELS[self.export_class]),
            ("Analytics build", self.build_fingerprint or "No build recorded"),
            ("Standing", STANDING_NOTE),
        ]
        return tuple(rows)


# --- turning a query into something a reader can check ------------------------------------


def _member_label(cat: Catalogue, member_id: str) -> str:
    try:
        return cat.member(member_id).label
    except (KeyError, CatalogueUnknownMember):
        return member_id


def _filter_line(cat: Catalogue, predicate: BiFilter) -> str:
    label = _member_label(cat, predicate.member)
    values = ", ".join(_scalar(value) for value in predicate.values)
    if not values:
        return f"{label} {predicate.op.replace('_', ' ')}"
    return f"{label} {predicate.op.replace('_', ' ')} {values}"


def _scalar(value: object) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, date | datetime):
        return value.isoformat()
    return str(value)


def window_label(query: BiQuery) -> str:
    """The as-of or range the query reads, and the period it compares against."""

    time = query.time
    if time.as_of is not None:
        label = time.as_of.isoformat()
    else:
        assert time.range is not None  # noqa: S101 - BiTime validates exactly one window
        label = f"{time.range.start.isoformat()} to {time.range.end.isoformat()}"
    if time.compare_to is not None:
        label = f"{label}, compared with {time.compare_to.isoformat()}"
    return label


def query_lines(cat: Catalogue, query: BiQuery) -> tuple[str, ...]:
    """One readable clause per part of the query, in labels rather than ids.

    The reader of an exported sheet is a banker, and the wire contract is not
    production copy. The window is NOT included: it is its own metadata field
    (:func:`window_label`), because "as at" is the first thing a reader checks.
    """

    parts: list[str] = [
        "Measures: " + ", ".join(_member_label(cat, member) for member in query.measures)
    ]
    if query.dimensions:
        parts.append(
            "Grouped by: " + ", ".join(_member_label(cat, member) for member in query.dimensions)
        )
    if query.pivot is not None:
        parts.append(f"Pivoted on: {_member_label(cat, query.pivot.dimension)}")
    if query.top_n is not None:
        parts.append(
            f"Top {query.top_n.n} by {_member_label(cat, query.top_n.dimension)}"
            + (" with the remainder collapsed" if query.top_n.other else "")
        )
    if query.filters:
        parts.append("Filtered to: " + "; ".join(_filter_line(cat, item) for item in query.filters))
    if query.sort:
        parts.append(
            "Sorted by: "
            + ", ".join(
                f"{_member_label(cat, item.member)} {item.direction}" for item in query.sort
            )
        )
    if query.subtotals:
        parts.append("Subtotals included")
    return tuple(parts)


# --- cell values ---------------------------------------------------------------------------


def machine_cell(value: Any, column: ExportColumn) -> str:
    """One cell as text a machine can read back: no grouping, no symbols."""

    if value is None:
        return BLANK
    if column.format == "flag":
        return "Yes" if value else "No"
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if column.numeric:
        return str(value)
    return inert(str(value))


def display_cell(value: Any, column: ExportColumn) -> str:  # noqa: PLR0911 - one branch per value type
    """One cell as a reader sees it: thousands grouped, two decimals on figures."""

    if value is None:
        return BLANK
    if column.format == "flag":
        return "Yes" if value else "No"
    if isinstance(value, date | datetime):
        return value.isoformat()
    if column.format in {"count", "int"}:
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return str(value)
    if column.format == "fraction":
        try:
            return f"{Decimal(str(value)) * _FRACTION_SCALE:,.2f} %"
        except (TypeError, ValueError, ArithmeticError):
            return str(value)
    if column.numeric:
        try:
            return f"{Decimal(str(value)):,.2f}"
        except (TypeError, ValueError, ArithmeticError):
            return str(value)
    return str(value)


def native_cell(value: Any, column: ExportColumn) -> Any:
    """One cell as a spreadsheet's own type, so a total still adds up in it."""

    if value is None:
        return None
    if column.format == "flag":
        return "Yes" if value else "No"
    if isinstance(value, datetime | date):
        return value
    if column.numeric and isinstance(value, Decimal):
        return float(value)
    if column.numeric and isinstance(value, int | float):
        return value
    return inert(str(value))


__all__ = [
    "BLANK",
    "EXPORT_TITLE",
    "METADATA_FIELDS",
    "STANDING_NOTE",
    "TRUST_LABELS",
    "ExportColumn",
    "ExportContext",
    "ExportTable",
    "display_cell",
    "inert",
    "machine_cell",
    "native_cell",
    "query_lines",
    "table_from",
    "window_label",
]
