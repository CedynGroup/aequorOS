"""The three governed-export artifacts and the policy that decides who gets them.

Unit-level: the renderers and the classifier, with a hand-built table and
context so every expected value is derivable by reading the file rather than
matched against a golden. The route-level behaviour — the authorization
decision, the query log, the audit event and the async hand-off — is
``tests/api/test_bi_exports.py``.

What is pinned here, and why each one matters:

* **the PDF is byte-deterministic.** ``docs/bi.md`` §Exports requires it, and the
  two things that break it (reportlab's creation timestamp and its random
  document id) are silent when they come back — the file still opens.
* **the spreadsheet's metadata sheet carries all six spec fields.** A renderer
  that drops one produces a file that looks right and cannot be traced.
* **both sheets are protected**, the way the sealed regulatory workbook is.
* **the classifier reads sensitivity, not a flag**, and ``restricted`` is
  record-level (D-066) — the mapping the whole export policy rests on.
* **text cannot execute.** A counterparty name is bank data and reaches a cell
  verbatim.
"""

from __future__ import annotations

import datetime as dt
import io
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from app.core.authorization import Permission
from app.core.authorization import Sensitivity as PlatformSensitivity
from app.domain.bi.catalogue import ColumnRef, DimensionDef, MeasureDef, catalogue
from app.domain.bi.catalogue.members import Sensitivity
from app.models.bi import QUERY_LOG_SURFACES
from app.schemas.bi import BiExportFormat, BiQuery, BiTime
from app.services.bi import exports
from app.services.bi.exports import context as export_context
from app.services.bi.exports import policy
from app.services.bi.exports import xlsx as xlsx_export

AS_OF = dt.date(2026, 8, 31)

COLUMNS = (
    export_context.ExportColumn(id="branch.code", label="Branch", format="text"),
    export_context.ExportColumn(id="loans.balance_rc", label="Loan balance (XTS)", format="amount"),
    export_context.ExportColumn(id="positions.count", label="Positions", format="count"),
    export_context.ExportColumn(id="time.date", label="Date", format="date"),
)

ROWS = (
    ("Head office", Decimal("400.00"), 2, AS_OF),
    # A name that a spreadsheet would treat as a formula, and one the standard
    # PDF font cannot draw. Both are things a real counterparty file contains.
    ('=HYPERLINK("http://x")', Decimal("-200.50"), 1, AS_OF),
    ("Brancĥ ₵", None, 0, AS_OF),
)


def table(*, truncated: bool = False) -> export_context.ExportTable:
    return export_context.ExportTable(columns=COLUMNS, rows=ROWS, truncated=truncated)


def context(
    *, export_class: policy.ExportClass = policy.SUMMARY, user_label: str = "analyst@bank.test"
) -> export_context.ExportContext:
    return export_context.ExportContext(
        institution_id="BK-EXPORT01",
        institution_name="Export Test Bank",
        # Never a literal anywhere in the export package: the unit arrives from
        # ``jurisdictions.base_currency``. The fixture uses a reserved ISO code
        # so nothing here can read as a country decision.
        unit="XTS",
        query_lines=("Measures: Loan balance", "Grouped by: Branch"),
        as_of_label=AS_OF.isoformat(),
        catalogue_version="bi-catalogue-test",
        data_scope_label="Whole institution",
        user_label=user_label,
        export_class=export_class,
        build_fingerprint="f" * 64,
    )


# --- determinism -------------------------------------------------------------------------


def test_the_pdf_is_byte_identical_for_identical_input() -> None:
    """The spec's word. ``invariant=1`` removes the timestamp and the document id."""

    first = exports.render("pdf", table(), context())
    second = exports.render("pdf", table(), context())
    assert first == second
    assert first.startswith(b"%PDF-")


def test_the_pdf_changes_when_the_provenance_changes() -> None:
    """A determinism test that cannot tell two documents apart proves nothing."""

    assert exports.render("pdf", table(), context()) != exports.render(
        "pdf", table(), context(user_label="reviewer@bank.test")
    )


def test_the_csv_is_byte_identical_for_identical_input() -> None:
    assert exports.render("csv", table(), context()) == exports.render("csv", table(), context())


# --- the spreadsheet ---------------------------------------------------------------------


def workbook(**kwargs: object):
    payload = exports.render("xlsx", table(), context(**kwargs))  # type: ignore[arg-type]
    return load_workbook(io.BytesIO(payload))


def test_the_metadata_sheet_carries_every_field_the_spec_names() -> None:
    """Query, as-of, catalogue version, scope, user — all five, by label. No
    "Data confidence" row: BI issues no verdict on its own figures (2026-09-29)."""

    sheet = workbook()[xlsx_export.METADATA_SHEET]
    fields = {
        str(row[0].value): row[1].value
        for row in sheet.iter_rows(min_row=1, max_col=2)
        if row[0].value is not None
    }
    assert set(exports.METADATA_FIELDS) <= set(fields), sorted(fields)
    assert fields["Query"] == "Measures: Loan balance; Grouped by: Branch"
    assert fields["As at"] == AS_OF.isoformat()
    assert "Data confidence" not in fields
    assert fields["Catalogue version"] == "bi-catalogue-test"
    assert fields["Data scope"] == "Whole institution"
    assert fields["Exported by"] == "analyst@bank.test"


def test_the_five_metadata_fields_lead_the_block_in_the_spec_order() -> None:
    """Order is contract: "as at" is the first thing a reader checks."""

    labels = [field for field, _ in context().metadata_rows()]
    assert tuple(labels[: len(exports.METADATA_FIELDS)]) == exports.METADATA_FIELDS


def test_both_sheets_are_protected_and_watermarked() -> None:
    """The sealed BoG workbook's rule: a downloaded figure is not editable in place."""

    book = workbook()
    assert book.sheetnames == [xlsx_export.DATA_SHEET, xlsx_export.METADATA_SHEET]
    for name in book.sheetnames:
        sheet = book[name]
        assert sheet.protection.sheet is True, name
        header = sheet.oddHeader.center  # pyright: ignore[reportOptionalMemberAccess]
        assert header.text == "Export Test Bank · analyst@bank.test", name


def test_the_data_sheet_keeps_the_compilers_column_order_and_native_types() -> None:
    sheet = workbook()[xlsx_export.DATA_SHEET]
    rows = list(sheet.iter_rows(values_only=True))
    assert list(rows[0]) == [column.label for column in COLUMNS]
    assert rows[1][1] == 400.0
    assert rows[1][3] == dt.datetime(AS_OF.year, AS_OF.month, AS_OF.day)
    # A NULL measure stays NULL. A zero would say the branch has no balance.
    assert rows[3][1] is None


def test_a_formula_looking_value_is_inert_in_the_spreadsheet() -> None:
    sheet = workbook()[xlsx_export.DATA_SHEET]
    rows = list(sheet.iter_rows(values_only=True))
    assert str(rows[2][0]).startswith("'=")


def test_a_truncated_export_says_so_on_the_metadata_sheet() -> None:
    payload = exports.render("xlsx", table(truncated=True), context())
    sheet = load_workbook(io.BytesIO(payload))[xlsx_export.METADATA_SHEET]
    fields = {
        str(row[0].value): row[1].value
        for row in sheet.iter_rows(min_row=1, max_col=2)
        if row[0].value is not None
    }
    assert "Completeness" in fields
    assert "row cap" in str(fields["Completeness"])


# --- the CSV -----------------------------------------------------------------------------


def csv_text(**kwargs: object) -> str:
    return exports.render("csv", table(), context(**kwargs)).decode("utf-8")  # type: ignore[arg-type]


def test_the_csv_leads_with_the_provenance_block_then_the_grid() -> None:
    lines = csv_text().splitlines()
    assert lines[0] == "field,value"
    blank = lines.index("")
    assert lines[blank + 1] == "Branch,Loan balance (XTS),Positions,Date"
    assert lines[blank + 2] == "Head office,400.00,2,2026-08-31"


def test_the_csv_streams_the_same_bytes_it_renders() -> None:
    streamed = b"".join(exports.iter_bytes("csv", table(), context()))
    assert streamed == exports.render("csv", table(), context())
    # More than one chunk, or "streaming" is a word rather than a behaviour.
    assert len(list(exports.iter_bytes("csv", table(), context()))) > len(ROWS)


def test_csv_cells_are_machine_readable_and_inert() -> None:
    body = csv_text()
    # No thousands separator, a real minus sign, and the formula made inert.
    assert ",-200.50," in body
    assert "'=HYPERLINK" in body
    # A NULL is blank, never zero, and non-Latin text survives the CSV intact.
    assert body.splitlines()[-1] == "Brancĥ ₵,,0,2026-08-31"


def test_every_format_names_a_media_type_and_an_extension() -> None:
    for fmt in exports.EXPORT_FORMATS:
        assert exports.MEDIA_TYPES[fmt]
        assert exports.EXTENSIONS[fmt]
    assert exports.EXPORT_FORMATS == ("csv", "xlsx", "pdf")


def test_the_wire_vocabulary_matches_the_renderers() -> None:
    """A format a client can ask for and no renderer implements is a 500."""

    assert set(BiExportFormat.__args__) == set(exports.EXPORT_FORMATS)


def test_the_export_surface_is_one_of_the_query_logs_own() -> None:
    from app.features import read_bi  # noqa: PLC0415 - the route-side constant

    assert exports.QUERY_LOG_SURFACE == read_bi.SURFACE_EXPORT
    assert exports.QUERY_LOG_SURFACE in QUERY_LOG_SURFACES


def test_the_filename_carries_no_clock_and_no_unsafe_character() -> None:
    name = exports.filename_for(
        "csv", bank_id="BK-EXPORT01", as_of_label="2026-08-31, compared with 2026-07-31"
    )
    assert name == "BK-EXPORT01-analytics-2026-08-31--compared-with-2026-07-31.csv"
    assert name == exports.filename_for(
        "csv", bank_id="BK-EXPORT01", as_of_label="2026-08-31, compared with 2026-07-31"
    )


# --- the policy ---------------------------------------------------------------------------


def member(sensitivity: Sensitivity) -> DimensionDef:
    return DimensionDef(
        id=f"probe.{sensitivity}",
        module="credit",
        sensitivity=sensitivity,
        label="Probe",
        source=ColumnRef("bi_fact_position_daily", "branch_code"),
    )


@pytest.mark.parametrize(
    ("sensitivity", "expected"),
    [
        ("published", policy.SUMMARY),
        ("aggregated", policy.SUMMARY),
        # D-066: a spreadsheet of named obligors leaving the building is exactly
        # what the elevated authority is for, and it is ``restricted``.
        ("restricted", policy.RECORD_LEVEL),
        ("confidential", policy.RECORD_LEVEL),
    ],
)
def test_the_class_is_read_from_the_members_own_sensitivity(
    sensitivity: Sensitivity, expected: policy.ExportClass
) -> None:
    assert policy.classify([member(sensitivity)]) == expected


def test_one_record_level_member_makes_the_whole_export_record_level() -> None:
    members = [member("aggregated"), member("confidential")]
    assert policy.classify(members) == policy.RECORD_LEVEL
    assert policy.record_level_members(members) == ("probe.confidential",)


def test_the_catalogue_sensitivities_are_the_platforms_own() -> None:
    """The classifier keys on these strings; a new level must be decided, not inherited."""

    assert set(Sensitivity.__args__) == {level.value for level in PlatformSensitivity}
    assert set(Sensitivity.__args__) > policy.SUMMARY_SENSITIVITIES


def test_a_summary_needs_view_and_a_record_level_export_needs_both() -> None:
    assert policy.permissions_for(policy.SUMMARY) == (Permission.VIEW,)
    assert policy.permissions_for(policy.RECORD_LEVEL) == (Permission.VIEW, Permission.EXPORT)


def test_an_unknown_member_id_classifies_as_record_level() -> None:
    """Deny-by-default: a catalogue that changed under the request must not widen."""

    assert policy.classify_ids(catalogue(), ["nothing.at.all"]) == policy.RECORD_LEVEL


def test_the_real_catalogue_puts_a_position_reference_above_a_branch_total() -> None:
    """The classifier against the product's own catalogue, not a probe."""

    cat = catalogue()
    assert policy.classify_ids(cat, ["loans.balance_rc", "branch.code"]) == policy.SUMMARY
    assert policy.classify_ids(cat, ["position.source_reference"]) == policy.RECORD_LEVEL
    assert policy.classify_ids(cat, ["counterparty.name"]) == policy.RECORD_LEVEL


def test_every_catalogue_measure_and_dimension_classifies() -> None:
    """No member falls outside the two classes, so no export is unclassified."""

    cat = catalogue()
    for item in cat.members():
        assert policy.classify([item]) in (policy.SUMMARY, policy.RECORD_LEVEL)
        assert isinstance(item, MeasureDef | DimensionDef)


# --- the query as a reader sees it ---------------------------------------------------------


def test_the_query_block_uses_labels_and_states_the_window_separately() -> None:
    cat = catalogue()
    query = BiQuery(
        measures=["loans.balance_rc"],
        dimensions=["branch.code"],
        time=BiTime(as_of=AS_OF, compare_to=dt.date(2026, 7, 31)),
    )
    lines = exports.query_lines(cat, query)
    assert lines[0] == f"Measures: {cat.member('loans.balance_rc').label}"
    assert lines[1] == f"Grouped by: {cat.member('branch.code').label}"
    assert exports.window_label(query) == "2026-08-31, compared with 2026-07-31"
    # Member IDS are the wire contract and are not production copy.
    assert "loans.balance_rc" not in "; ".join(lines)
