"""Every catalogue value type must be recognised by the export renderers.

The failure this closes is silent, which is what makes it worth its own file.
A value type the export layer does not know is not rejected: it falls through
to the text branch, so a numeric column is written to XLSX as a STRING and a
reviewer's spreadsheet cannot total it, while the PDF and CSV show the figure
unformatted. Nothing raises, no test fails, and the artifact is the audit twin
of a filed return.

It has already happened once. The catalogue retired ``ratio`` for ``fraction``,
``index`` and ``duration_years``; both format maps still said ``ratio``, so
every one of those columns would have exported as text. These tests derive their
expectations from the catalogue's own vocabulary rather than restating it, so the
next change to that vocabulary fails HERE instead of in a bank's spreadsheet.
"""

from __future__ import annotations

from decimal import Decimal
from typing import get_args

from app.domain.bi.catalogue.members import NUMERIC_VALUE_TYPES, ValueType
from app.services.bi.exports.context import ExportColumn, display_cell, machine_cell, native_cell
from app.services.bi.exports.xlsx import _NUMBER_FORMATS


def _column(value_type: str) -> ExportColumn:
    return ExportColumn(id="m", label="Measure", format=value_type)


def test_every_numeric_value_type_is_recognised_as_numeric() -> None:
    """``ExportColumn.numeric`` decides whether a cell keeps its type at all."""
    not_numeric = sorted(t for t in NUMERIC_VALUE_TYPES if not _column(t).numeric)
    assert not not_numeric, (
        "these catalogue value types are numeric but the export layer treats them as "
        f"text, so the column is written as a string: {not_numeric}"
    )


def test_every_numeric_value_type_has_an_xlsx_number_format() -> None:
    """A missing entry writes the cell with no format, not with a default one."""
    missing = sorted(t for t in NUMERIC_VALUE_TYPES if t not in _NUMBER_FORMATS)
    assert not missing, (
        "these catalogue value types have no XLSX number format, so their columns "
        f"lose their formatting in the audit twin: {missing}"
    )


def test_no_format_map_names_a_value_type_the_catalogue_retired() -> None:
    """The reverse direction: a stale name is dead code that reads as coverage."""
    declared = set(get_args(ValueType)) | {"int"}
    stale = sorted(key for key in _NUMBER_FORMATS if key not in declared)
    assert not stale, (
        "these keys are not catalogue value types, so they format nothing and make "
        f"the map look more complete than it is: {stale}"
    )


def test_a_fraction_is_shown_multiplied_by_a_hundred_and_marked_as_one() -> None:
    """The catalogue defines ``fraction`` as a proportion of one.

    A reader shown ``0.06`` where the platform means ``6.20 %`` is the defect the
    value-type split exists to fix, and fixing it only on screen would make the
    export disagree with the browser about the same member.
    """
    shown = display_cell(Decimal("0.062"), _column("fraction"))
    assert shown == "6.20 %", shown


def test_a_fraction_keeps_its_exact_value_in_the_spreadsheet_cell() -> None:
    """Scaled for the eye, unscaled for the file.

    The XLSX carries a percent NUMBER FORMAT, so the reader sees the same figure
    while the cell still holds what the mart holds. Pre-multiplying on the way in
    would put a number in the audit twin that exists nowhere in the platform.
    """
    assert native_cell(Decimal("0.062"), _column("fraction")) == float(Decimal("0.062"))
    assert "%" in _NUMBER_FORMATS["fraction"]


def test_a_null_is_blank_in_every_numeric_type_and_never_zero() -> None:
    """A measure with no rows and a measure that is zero are different statements."""
    for value_type in sorted(NUMERIC_VALUE_TYPES):
        column = _column(value_type)
        assert display_cell(None, column) == "", value_type
        assert native_cell(None, column) is None, value_type
        assert machine_cell(None, column) == "", value_type
