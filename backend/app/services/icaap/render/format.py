"""Turning stored block payloads into the strings an ICAAP document prints.

A block binding stores its figures exactly as the resolver captured them:
decimal strings, ISO dates, ``"true"``/``"false"``, or ``null``
(``icaap-block-payload-v1``). Nothing here changes a value — it decides how one
is *shown*, so the PDF, the DOCX and the screen all show the same string, and
so a figure quoted inline in a narrative reads the same as the same figure in
its table.

Three rules this module exists to keep:

* **An absent figure is absent.** ``null`` prints
  :data:`~app.domain.icaap.document.NOT_AVAILABLE`, never ``0``. A zero in an
  ICAAP table is a measurement; a blank is a gap; conflating them is how a
  reader concludes a bank holds no exposure it simply has not reported.
* **Nothing is computed.** No total is summed, no ratio derived, no threshold
  applied (founder directive D-024). A value the payload does not carry cannot
  appear on the page.
* **Country identity is data.** Currency, regulator wording and institution
  names arrive on the document from the jurisdiction registry; this module
  writes no currency code, regulator name or locale of its own.

Number conventions follow the platform's filed returns
(``regulatory_reporting/templates.format_cell``): thousands separators,
parenthesised negatives, money and percentages at two decimals. Dates print as
``31 Dec 2025`` — the day/month order of an ISO date written out, which no
reader can mistake for the other one.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.icaap.document import (
    COMMITTED_CONTENT_LABEL,
    NOT_AVAILABLE,
    WORKING_CONTENT_LABEL,
    BlockRender,
    TableColumn,
    TableRow,
    TableSpec,
)

#: The payload contract this module reads. A binding written under a later
#: schema is rendered on a best-effort basis and says so, rather than being
#: dropped: a block that silently disappears from a draft is worse than one
#: that prints with a note.
PAYLOAD_SCHEMA = "icaap-block-payload-v1"

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_TRUE = frozenset({"true", "t", "yes", "1"})
_FALSE = frozenset({"false", "f", "no", "0"})
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9]+")
_FILENAME_MAX_STEM = 120

#: Cell kinds that print right-aligned in a table (numbers line up on the
#: decimal point, the convention every printed financial return follows).
NUMERIC_KINDS = frozenset({"amount", "ratio_pct", "count", "years", "multiplier"})


# --------------------------------------------------------------------------
# Scalar formatting
# --------------------------------------------------------------------------
def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def _grouped(number: Decimal, *, places: int) -> str:
    """``number`` with thousands separators and parenthesised negatives."""
    quantum = Decimal(1).scaleb(-places)
    fixed = number.quantize(quantum)
    if fixed < 0:
        return f"({-fixed:,.{places}f})"
    return f"{fixed:,.{places}f}"


def format_amount(value: str, *, currency: str | None = None) -> str:
    """A money figure at two decimals, optionally carrying its currency code.

    The currency is written only where the page gives no other context — a
    figure quoted inside a sentence. Table cells omit it because the block's
    unit line states it once for the whole table.
    """
    number = _decimal(value)
    if number is None:
        return value
    text = _grouped(number, places=2)
    return f"{currency} {text}" if currency else text


def format_ratio_pct(value: str) -> str:
    """A percentage at two decimals. The value is already a percentage."""
    number = _decimal(value)
    if number is None:
        return value
    return f"{_grouped(number, places=2)}%"


def format_count(value: str) -> str:
    number = _decimal(value)
    if number is None:
        return value
    if number == number.to_integral_value():
        return f"{number.to_integral_value():,}"
    return _grouped(number, places=2)


def format_years(value: str) -> str:
    number = _decimal(value)
    if number is None:
        return value
    whole = number == number.to_integral_value()
    text = f"{number.to_integral_value():,}" if whole else _grouped(number, places=2)
    return f"{text} year" if number == 1 else f"{text} years"


def format_multiplier(value: str) -> str:
    number = _decimal(value)
    if number is None:
        return value
    return f"{_grouped(number, places=2)}×"


def format_boolean(value: str) -> str:
    """``Yes``/``No`` — the marking convention the filed tables already use."""
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return "Yes"
    if lowered in _FALSE:
        return "No"
    return value


def format_date(value: str | date) -> str:
    """``2025-12-31`` → ``31 Dec 2025``; anything unparseable prints as given."""
    if isinstance(value, date):
        return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError:
        return value
    return format_date(parsed)


#: One formatter per declared fact kind. ``amount`` is handled separately
#: because it is the only kind that takes a currency. A kind with no entry —
#: ``text``, or one a later payload schema introduces — prints as stored rather
#: than being dropped or coerced.
_FORMATTERS: Mapping[str, Callable[[str], str]] = {
    "ratio_pct": format_ratio_pct,
    "count": format_count,
    "years": format_years,
    "multiplier": format_multiplier,
    "boolean": format_boolean,
    "date": format_date,
}


def format_value(value: str | None, *, kind: str, currency: str | None = None) -> str:
    """One stored value as it prints, by its declared kind."""
    if value is None:
        return NOT_AVAILABLE
    text = value if isinstance(value, str) else str(value)
    if not text.strip():
        return NOT_AVAILABLE
    if kind == "amount":
        return format_amount(text, currency=currency)
    formatter = _FORMATTERS.get(kind)
    return formatter(text) if formatter is not None else text


def format_fact(fact: Mapping[str, Any] | None, *, fallback_currency: str | None = None) -> str:
    """One entry of a binding's ``facts`` map as it prints inline in prose.

    Facts quoted in a sentence carry their currency, because the sentence is
    the only context the reader has. A fact the binding does not hold — or
    holds with a ``null`` value — is :data:`NOT_AVAILABLE`.
    """
    if not fact:
        return NOT_AVAILABLE
    kind = str(fact.get("kind") or "text")
    raw = fact.get("value")
    currency = fact.get("currency") or (fallback_currency if kind == "amount" else None)
    value = None if raw is None else str(raw)
    return format_value(value, kind=kind, currency=currency)


# --------------------------------------------------------------------------
# Labels (production copy: no raw enum ever reaches a page)
# --------------------------------------------------------------------------
BASIS_LABELS: Mapping[str, str] = {
    "solo": "Solo",
    "consolidated": "Consolidated",
}

CYCLE_KIND_LABELS: Mapping[str, str] = {
    "annual": "Annual ICAAP",
    "material_change": "Material-change update",
    "regulator_request": "Supervisory request",
    "rehearsal": "Rehearsal",
}

REQUIREMENT_STATUS_LABELS: Mapping[str, str] = {
    "open": "Outstanding",
    "met": "Addressed",
    "not_applicable": "Not applicable",
    "auto_not_applicable": "Not applicable to this institution",
}

BLOCK_STATUS_LABELS: Mapping[str, str] = {
    "unbound": "No figures bound",
    "fresh": "Current",
    "stale": "Superseded",
    "as_of_mismatch": "Different reporting date",
    "source_withdrawn": "Source withdrawn",
    "source_missing": "Source unavailable",
    "pinned": "Pinned",
}


def humanize(value: str) -> str:
    """A stored key as readable words — the last resort for an unmapped value.

    Used so an unexpected status prints as "Source withdrawn" rather than
    ``source_withdrawn``: raw enum text on a document the Board reads is a
    defect the platform has already been pulled up on.
    """
    cleaned = value.replace("_", " ").replace("-", " ").strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else value


def basis_label(basis: str) -> str:
    return BASIS_LABELS.get(basis.strip().lower(), humanize(basis))


def cycle_kind_label(kind: str) -> str:
    return CYCLE_KIND_LABELS.get(kind.strip().lower(), humanize(kind))


def requirement_status_label(status: str) -> str:
    return REQUIREMENT_STATUS_LABELS.get(status.strip().lower(), humanize(status))


def block_status_label(status: str) -> str:
    return BLOCK_STATUS_LABELS.get(status.strip().lower(), humanize(status))


def block_status_note(status: str, *, pin_reason: str | None = None) -> str | None:
    """The honest line printed beside a table whose binding is not current.

    A superseded or mis-dated table with no note is a figure presented as
    current when it is not, which is exactly the failure the block staleness
    model exists to prevent.
    """
    key = status.strip().lower()
    if key == "fresh":
        return None
    if key == "pinned":
        reason = (pin_reason or "").strip()
        return (
            f"Pinned to this version deliberately: {reason}"
            if reason
            else "Pinned to this version deliberately."
        )
    return {
        "unbound": "No figures are bound to this block yet.",
        "stale": "Newer figures are available for this source.",
        "as_of_mismatch": "These figures are not as at the cycle's reporting date.",
        "source_withdrawn": "The data these figures were computed from has been withdrawn.",
        "source_missing": "The source these figures came from is no longer available.",
    }.get(key, humanize(key))


def content_label(*, committed_version: int | None) -> str:
    """ "Version 3 (committed)" or "Working draft — not committed"."""
    if committed_version is None:
        return WORKING_CONTENT_LABEL
    return COMMITTED_CONTENT_LABEL.format(version=committed_version)


def pending_primary_text_note(regulator_short: str) -> str:
    """Why a section's checklist is short (D-006). The regulator is named from
    the jurisdiction registry, never written here."""
    return f"Checklist incomplete — pending {regulator_short} text."


def unit_note(unit: Mapping[str, Any] | None) -> str | None:
    """ "Amounts in GHS." / "Amounts in GHS thousands." from the payload's unit.

    The scale is *stated*, never applied: the stored values are printed as
    captured, so a mis-declared scale is visible rather than silently folded
    into the numbers.
    """
    if not unit:
        return None
    currency = str(unit.get("currency") or "").strip()
    if not currency:
        return None
    raw_scale = unit.get("scale")
    scale = _decimal(str(raw_scale)) if raw_scale is not None else None
    if scale is None or scale == 1:
        return f"Amounts in {currency}."
    if scale == 1000:
        return f"Amounts in {currency} thousands."
    if scale == 1_000_000:
        return f"Amounts in {currency} millions."
    return f"Amounts in {currency}, scaled by {_grouped(scale, places=0)}."


# --------------------------------------------------------------------------
# Payload -> render model
# --------------------------------------------------------------------------
def _table_spec(raw: Mapping[str, Any], *, index: int) -> TableSpec:
    raw_columns = raw.get("columns")
    columns: list[TableColumn] = []
    if isinstance(raw_columns, list):
        for position, column in enumerate(raw_columns):
            if not isinstance(column, Mapping):
                continue
            key = str(column.get("key") or f"c{position}")
            columns.append(
                TableColumn(
                    key=key,
                    label=str(column.get("label") or humanize(key)),
                    kind=str(column.get("kind") or "text"),
                )
            )
    kinds = {column.key: column.kind for column in columns}
    raw_rows = raw.get("rows")
    rows: list[TableRow] = []
    if isinstance(raw_rows, list):
        for row in raw_rows:
            if not isinstance(row, Mapping):
                continue
            raw_cells = row.get("cells")
            cells: dict[str, str] = {}
            if isinstance(raw_cells, Mapping):
                for key, value in raw_cells.items():
                    name = str(key)
                    kind = kinds.get(name, "text")
                    # A blank cell in a table stays blank: the surrounding row
                    # and column already say what is missing, and "Not
                    # available" repeated down a column is unreadable.
                    cells[name] = (
                        "" if value is None else format_value(str(value), kind=kind, currency=None)
                    )
            emphasis = row.get("emphasis")
            rows.append(TableRow(cells=cells, emphasis=None if emphasis is None else str(emphasis)))
    key = str(raw.get("key") or f"table_{index + 1}")
    return TableSpec(
        key=key,
        title=str(raw.get("title") or humanize(key)),
        columns=tuple(columns),
        rows=tuple(rows),
    )


def block_render(  # noqa: PLR0913 - one keyword per stored column; a dict would hide them
    *,
    block_id: str,
    payload: Mapping[str, Any] | None,
    status: str,
    pin_reason: str | None = None,
    fallback_title: str,
    source_as_of: date | None = None,
    provenance: tuple[tuple[str, str], ...] = (),
) -> BlockRender:
    """A stored binding payload as the renderable block.

    An unbound block still renders — with its title, its status and no tables —
    because a draft that omitted it would hide from the author that a section
    they referenced has nothing behind it.
    """
    payload = payload or {}
    raw_tables = payload.get("tables")
    tables: list[TableSpec] = []
    if isinstance(raw_tables, list):
        for index, table in enumerate(raw_tables):
            if isinstance(table, Mapping):
                tables.append(_table_spec(table, index=index))

    notes: list[str] = []
    unit = payload.get("unit")
    note = unit_note(unit if isinstance(unit, Mapping) else None)
    if note:
        notes.append(note)
    raw_notes = payload.get("notes")
    if isinstance(raw_notes, list):
        notes.extend(str(entry) for entry in raw_notes if str(entry).strip())
    schema = payload.get("schema")
    if payload and schema is not None and str(schema) != PAYLOAD_SCHEMA:
        notes.append(f"Recorded under payload schema {schema}; this export reads {PAYLOAD_SCHEMA}.")

    as_of_raw = payload.get("as_of")
    as_of = source_as_of
    if as_of is None and isinstance(as_of_raw, str):
        try:
            as_of = date.fromisoformat(as_of_raw.strip())
        except ValueError:
            as_of = None

    return BlockRender(
        block_id=block_id,
        title=str(payload.get("title") or fallback_title),
        as_of=as_of,
        source_label=str(payload.get("source_label") or ""),
        status=status,
        status_note=block_status_note(status, pin_reason=pin_reason),
        tables=tuple(tables),
        notes=tuple(notes),
        provenance=provenance,
    )


def fact_text_map(
    bindings: Mapping[str, Mapping[str, Any]], *, fallback_currency: str | None = None
) -> dict[tuple[str, str], str]:
    """``{block_id: facts}`` flattened to the ``(block_id, fact_key)`` map an
    :class:`~app.domain.icaap.document.IcaapDocument` carries."""
    resolved: dict[tuple[str, str], str] = {}
    for block_id, facts in bindings.items():
        if not isinstance(facts, Mapping):
            continue
        for fact_key, fact in facts.items():
            value = fact if isinstance(fact, Mapping) else None
            resolved[(str(block_id), str(fact_key))] = format_fact(
                value, fallback_currency=fallback_currency
            )
    return resolved


# --------------------------------------------------------------------------
# Filenames
# --------------------------------------------------------------------------
def sanitize_filename_part(value: str) -> str:
    """One filename segment reduced to ``[A-Za-z0-9-]``.

    An institution's name reaches a ``Content-Disposition`` header, so it is
    restricted rather than escaped: quotes, semicolons, newlines and path
    separators cannot survive this, which is the point.
    """
    cleaned = _FILENAME_UNSAFE.sub("-", value).strip("-")
    return cleaned or "institution"


def draft_filename(
    *, institution_short_name: str, fiscal_year: int, basis: str, extension: str
) -> str:
    """``ICAAP-Sample-Bank-FY2025-solo-DRAFT.pdf``.

    ``DRAFT`` is in the filename on purpose: a downloaded file gets forwarded,
    and the name is the only thing visible before it is opened.
    """
    stem = "-".join(
        (
            "ICAAP",
            sanitize_filename_part(institution_short_name),
            f"FY{fiscal_year}",
            sanitize_filename_part(basis),
            "DRAFT",
        )
    )
    return f"{stem[:_FILENAME_MAX_STEM]}.{sanitize_filename_part(extension).lower()}"


__all__ = [
    "BASIS_LABELS",
    "BLOCK_STATUS_LABELS",
    "CYCLE_KIND_LABELS",
    "NUMERIC_KINDS",
    "PAYLOAD_SCHEMA",
    "REQUIREMENT_STATUS_LABELS",
    "basis_label",
    "block_render",
    "block_status_label",
    "block_status_note",
    "content_label",
    "cycle_kind_label",
    "draft_filename",
    "fact_text_map",
    "format_amount",
    "format_boolean",
    "format_count",
    "format_date",
    "format_fact",
    "format_multiplier",
    "format_ratio_pct",
    "format_value",
    "format_years",
    "humanize",
    "pending_primary_text_note",
    "requirement_status_label",
    "sanitize_filename_part",
    "unit_note",
]
