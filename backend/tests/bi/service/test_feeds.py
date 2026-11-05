"""The feed's three decidable parts: the registry, the cursor, and the bytes.

The route is ``tests/bi/api/test_bi_feeds_integration.py``. What is here is everything that can
be decided without a request, and the cursor is the reason this file exists: a
build-anchored cursor is the difference between a report server that follows a
restatement and one that disagrees with the platform for ever, and that property
is a function of ``bi_mart_builds`` alone.

This module's other-suite cases live in ``test_feeds_integration.py``.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from app.bi.domain.catalogue import catalogue
from app.bi.models.marts import QUERY_LOG_SURFACES
from app.bi.service.compiler import ColumnSpec
from app.bi.service.exports import policy
from app.bi.service.feeds import authorization as feed_authorization
from app.bi.service.feeds import cursor as feed_cursor
from app.bi.service.feeds import datasets, render, runner

DOC = Path(__file__).resolve().parents[3] / "docs" / "powerbi_stage_b.md"
#: The dataset table rows in the handed-to-the-bank document, as ``| `id` | …``.
_DOC_ROW = re.compile(r"^\|\s*`([a-z_]+)`\s*\|", re.MULTILINE)
AUGUST = dt.date(2026, 8, 31)
T2 = dt.datetime(2026, 9, 1, 3, 0, tzinfo=dt.UTC)


def test_every_dataset_is_declared_over_members_the_catalogue_knows() -> None:
    cat = catalogue()
    assert datasets.dataset_ids(), "an empty registry would pass every test below"
    for entry in datasets.DATASETS.values():
        for member_id in entry.member_ids:
            assert cat.member(member_id) is not None, f"{entry.id}: {member_id}"
        # Building the query is itself the wire-bound check: ``BiQuery`` caps
        # measures at 25, dimensions at 12 and sorts at 8.
        assert entry.query(datasets.SHAPE_DATE).measures


def test_every_dataset_is_summary_and_says_so_truthfully() -> None:
    """A curated feed may not disclose a record, and may not misdeclare that.

    Both directions: the declared class is ``summary``, AND it equals the class
    the catalogue's own sensitivities imply. A record-level dataset would need
    ``Permission.EXPORT``, which no machine bundle carries, so it would be
    registered and unservable — and it is the disclosure ``bi_reader`` was scoped
    to exclude. Adding one fails HERE, which is the decision point.
    """

    cat = catalogue()
    for entry in datasets.DATASETS.values():
        assert entry.disclosure_class == policy.SUMMARY, entry.id
        assert datasets.declared_class_matches(cat, entry), entry.id


def test_every_dataset_leads_with_the_reporting_date_and_sorts_by_every_key() -> None:
    """The date is the consumer's replace key; the sort is the paging guarantee."""

    for entry in datasets.DATASETS.values():
        assert entry.dimensions[0] == datasets.REPORTING_DATE_DIMENSION, entry.id
        query = entry.query(AUGUST)
        assert [sort.member for sort in query.sort] == list(entry.dimensions), entry.id


def test_every_declared_build_scope_is_one_the_mart_actually_records() -> None:
    """A dataset waiting on a scope nobody writes would report "nothing new" for ever."""

    assert datasets.unknown_build_scopes() == ()


def test_the_institution_grain_measures_are_read_from_the_catalogue() -> None:
    cat = catalogue()
    scoped = datasets.institution_grain_measures(cat, datasets.dataset("loan_book"))
    institution = datasets.institution_grain_measures(cat, datasets.dataset("regulatory_metrics"))
    assert scoped == ()
    assert institution == datasets.dataset("regulatory_metrics").measures


def test_an_unknown_dataset_refuses_without_naming_the_registry() -> None:
    with pytest.raises(datasets.UnknownDataset) as raised:
        datasets.dataset("everything")
    assert raised.value.dataset_id == "everything"
    for known in datasets.dataset_ids():
        assert known not in str(raised.value)


def test_the_document_the_bank_is_handed_names_exactly_the_registry() -> None:
    """The doc is the contract a bank's BI team builds against, so it may not drift."""

    text = DOC.read_text(encoding="utf-8")
    documented = set(_DOC_ROW.findall(text))
    assert documented == set(datasets.dataset_ids()), sorted(
        documented ^ set(datasets.dataset_ids())
    )


def test_the_feed_surface_is_in_the_query_logs_own_vocabulary() -> None:
    """``feed`` has to be storable or every pull fails at the CHECK."""

    assert feed_authorization.SURFACE_FEED in QUERY_LOG_SURFACES


def test_a_cursor_round_trips() -> None:
    original = feed_cursor.FeedCursor(at=T2, as_of=AUGUST)
    assert feed_cursor.decode(original.encode()) == original


@pytest.mark.parametrize(
    "token",
    [
        "",
        "   ",
        "aeqf0.1790000000000000.20260831",
        "aeqf1.1790000000000000",
        "aeqf1.notanumber.20260831",
        "aeqf1.1790000000000000.2026-08-31",
        "aeqf1.1790000000000000.20261332",
        "aeqf1.99999999999999999999.20260831",
        "'; DROP TABLE bi_fact_position_daily; --",
    ],
)
def test_a_malformed_cursor_is_refused_rather_than_read_as_the_beginning(token: str) -> None:
    """A typo must not be answered with the bank's whole history."""

    with pytest.raises(feed_cursor.InvalidCursor) as raised:
        feed_cursor.decode(token)
    # The token is client bytes and is echoed nowhere.
    assert token.strip() not in str(raised.value) or not token.strip()


COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec(id="time.date", label="Date", kind="dimension", format="date"),
    ColumnSpec(id="branch.code", label="Branch", kind="dimension", format="text"),
    ColumnSpec(id="loan.restructured", label="Restructured", kind="dimension", format="flag"),
    ColumnSpec(id="loans.balance_rc", label="Balance", kind="measure", format="amount"),
    ColumnSpec(id="loans.count", label="Loans", kind="measure", format="count"),
)
#: A balance with more significant digits than a float64 can hold exactly, and a
#: branch name a spreadsheet would execute.
EXACT = Decimal("12345678901234.56")
ROWS: tuple[tuple[object, ...], ...] = (
    (AUGUST, "B1", True, EXACT, 3),
    (AUGUST, "=cmd|' /c calc'!A1", False, None, 0),
)


def test_the_csv_header_is_catalogue_member_ids() -> None:
    assert render.csv_header(COLUMNS).strip().split(",") == [column.id for column in COLUMNS]


def test_ndjson_and_csv_agree_on_every_value() -> None:
    """Two formats, one statement about each figure."""

    ndjson = list(render.iter_lines(ROWS, COLUMNS, "ndjson"))
    csv_lines = list(render.iter_lines(ROWS, COLUMNS, "csv"))
    assert len(ndjson) == len(ROWS)
    assert len(csv_lines) == len(ROWS) + 1  # the header
    for row, json_line in zip(ROWS, ndjson, strict=True):
        parsed = json.loads(json_line)
        for value, column in zip(row, COLUMNS, strict=True):
            canonical = render.canonical_text(value, column)
            if value is None:
                assert parsed[column.id] is None
                continue
            assert str(parsed[column.id]).lower() == canonical.lower(), column.id


def test_an_amount_survives_the_wire_exactly() -> None:
    """A float conversion would round this; the NDJSON number literal does not."""

    line = render.ndjson_line(ROWS[0], COLUMNS)
    assert f'"loans.balance_rc":{EXACT}' in line
    assert str(EXACT) in render.csv_row(ROWS[0], COLUMNS)


def test_null_is_never_zero() -> None:
    assert '"loans.balance_rc":null' in render.ndjson_line(ROWS[1], COLUMNS)
    assert render.csv_row(ROWS[1], COLUMNS).count(",,") == 1


def test_a_flag_is_a_boolean_in_both_formats_not_english() -> None:
    assert '"loan.restructured":true' in render.ndjson_line(ROWS[0], COLUMNS)
    assert ",true," in render.csv_row(ROWS[0], COLUMNS)
    assert "Yes" not in render.csv_row(ROWS[0], COLUMNS)


def test_text_cannot_execute_in_a_spreadsheet_but_is_verbatim_in_json() -> None:
    dangerous = ROWS[1]
    assert "'=cmd" in render.csv_row(dangerous, COLUMNS)
    assert json.loads(render.ndjson_line(dangerous, COLUMNS))["branch.code"] == dangerous[1]


def test_the_payload_is_produced_lazily_and_never_assembled() -> None:
    """A feed has no row cap, so nothing may materialise the payload."""

    consumed: list[int] = []

    def rows() -> Iterator[tuple[object, ...]]:
        for index, row in enumerate(ROWS):
            consumed.append(index)
            yield row

    stream = render.iter_bytes(rows(), COLUMNS, "csv")
    assert consumed == []
    next(stream)  # the header, before any row is read
    assert consumed == []
    next(stream)
    assert consumed == [0]


def test_an_unsupported_format_refuses_rather_than_serving_nothing() -> None:
    with pytest.raises(ValueError, match="unsupported BI feed format"):
        list(render.iter_lines(ROWS, COLUMNS, "parquet"))  # type: ignore[arg-type]


def test_the_format_vocabulary_and_the_media_types_are_one_set() -> None:
    assert set(render.MEDIA_TYPES) == set(render.FEED_FORMATS)
    assert set(render.EXTENSIONS) == set(render.FEED_FORMATS)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Whole institution", "Whole institution"),
        # The interpunct the shared scope label uses. Latin-1 encodable, so
        # Starlette sends it happily and the consumer has to guess an encoding.
        ("Regions: Volta \u00b7 2 branches in scope", "Regions: Volta 2 branches in scope"),
        # Header injection through an ingested branch name.
        ("Volta\r\nX-Injected: yes", "Volta X-Injected: yes"),
        # A name in a non-Latin script must not become forty spaces.
        ("\u0410\u043a\u043a\u0440\u0430 branch", "branch"),
        ("  padded  ", "padded"),
    ],
)
def test_a_header_value_is_reduced_to_printable_ascii(raw: str, expected: str) -> None:
    """Several of these values are BANK DATA, so this guard has to be able to fire."""

    cleaned = runner.header_text(raw)
    assert cleaned == expected
    assert cleaned.isascii()
    assert "\r" not in cleaned and "\n" not in cleaned


def test_every_declared_feed_header_is_produced() -> None:
    """The header tuple is the contract; a name in it that is never sent is a lie."""

    assert len(set(runner.FEED_HEADERS)) == len(runner.FEED_HEADERS)
    for name in runner.FEED_HEADERS:
        assert name.startswith("X-Bi-Feed-"), name
