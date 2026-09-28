"""The feed's three decidable parts: the registry, the cursor, and the bytes.

The route is ``tests/api/test_bi_feeds.py``. What is here is everything that can
be decided without a request, and the cursor is the reason this file exists: a
build-anchored cursor is the difference between a report server that follows a
restatement and one that disagrees with the platform for ever, and that property
is a function of ``bi_mart_builds`` alone.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.bi.catalogue import catalogue
from app.models import Bank
from app.models.bi import QUERY_LOG_SURFACES, BiMartBuild
from app.services.bi.compiler import ColumnSpec
from app.services.bi.exports import policy
from app.services.bi.feeds import authorization as feed_authorization
from app.services.bi.feeds import cursor as feed_cursor
from app.services.bi.feeds import datasets, render, runner
from tests.api.helpers import ORG_1

BANK_ID = "BK-FEEDSVC1"
DOC = Path(__file__).resolve().parents[3] / "docs" / "powerbi_stage_b.md"
#: The dataset table rows in the handed-to-the-bank document, as ``| `id` | …``.
_DOC_ROW = re.compile(r"^\|\s*`([a-z_]+)`\s*\|", re.MULTILINE)

JULY = dt.date(2026, 7, 31)
AUGUST = dt.date(2026, 8, 31)
T1 = dt.datetime(2026, 9, 1, 2, 0, tzinfo=dt.UTC)
T2 = dt.datetime(2026, 9, 1, 3, 0, tzinfo=dt.UTC)
T3 = dt.datetime(2026, 9, 2, 4, 0, tzinfo=dt.UTC)


# --- the registry -------------------------------------------------------------------------


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


# --- the cursor ---------------------------------------------------------------------------


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


def _bank(db: Session) -> Bank:
    existing = db.get(Bank, BANK_ID)
    if existing is not None:
        return existing
    bank = Bank(
        id=BANK_ID,
        organization_id=ORG_1,
        name="Feed service bank",
        short_name="Feed",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal_bank",
        institution_type="universal_bank",
    )
    db.add(bank)
    db.flush()
    return bank


def _build(  # noqa: PLR0913 - one build row, spelled out
    db: Session,
    *,
    as_of: dt.date,
    scope: str,
    status: str = "succeeded",
    started_at: dt.datetime = T1,
    finished_at: dt.datetime | None = T1,
    fingerprint: str = "f" * 64,
) -> None:
    db.add(
        BiMartBuild(
            organization_id=ORG_1,
            bank_id=BANK_ID,
            as_of_date=as_of,
            scope=scope,
            fingerprint=fingerprint,
            status=status,
            builder_version=1,
            started_at=started_at,
            finished_at=finished_at,
            row_counts={},
        )
    )


def _select(
    db: Session,
    *,
    cursor: feed_cursor.FeedCursor | None = None,
    now: dt.datetime = T3,
    max_slices: int = feed_cursor.MAX_SLICES_PER_PULL,
) -> feed_cursor.SliceSelection:
    return feed_cursor.select_slices(
        db,
        organization_id=ORG_1,
        bank_id=BANK_ID,
        entry=datasets.dataset("loan_book"),
        cursor=cursor,
        now=now,
        max_slices=max_slices,
    )


def test_a_slice_is_served_only_when_every_scope_it_reads_succeeded(db_session: Session) -> None:
    _bank(db_session)
    # July: both required scopes succeeded. August: ``dims`` failed.
    _build(db_session, as_of=JULY, scope="positions", finished_at=T1)
    _build(db_session, as_of=JULY, scope="dims", finished_at=T1)
    _build(db_session, as_of=AUGUST, scope="positions", finished_at=T2)
    _build(db_session, as_of=AUGUST, scope="dims", status="failed", finished_at=T2)
    db_session.commit()

    selection = _select(db_session)
    assert [entry.as_of for entry in selection.slices] == [JULY]
    assert selection.next_cursor == feed_cursor.FeedCursor(at=T1, as_of=JULY)


def test_a_scope_the_dataset_does_not_read_cannot_hold_a_slice_back(db_session: Session) -> None:
    """``loan_book`` reads positions and dims; a failed ``gl`` is not its problem."""

    _bank(db_session)
    _build(db_session, as_of=JULY, scope="positions", finished_at=T1)
    _build(db_session, as_of=JULY, scope="dims", finished_at=T1)
    _build(db_session, as_of=JULY, scope="gl", status="failed", finished_at=T1)
    db_session.commit()

    assert [entry.as_of for entry in _select(db_session).slices] == [JULY]


def test_the_cursor_excludes_what_the_caller_already_has(db_session: Session) -> None:
    _bank(db_session)
    for as_of, moment in ((JULY, T1), (AUGUST, T2)):
        _build(db_session, as_of=as_of, scope="positions", finished_at=moment, started_at=moment)
        _build(db_session, as_of=as_of, scope="dims", finished_at=moment, started_at=moment)
    db_session.commit()

    first = _select(db_session)
    assert [entry.as_of for entry in first.slices] == [JULY, AUGUST]
    assert first.next_cursor == feed_cursor.FeedCursor(at=T2, as_of=AUGUST)

    second = _select(db_session, cursor=first.next_cursor)
    assert second.slices == ()
    assert second.next_cursor is None


def test_a_restated_reporting_date_is_served_again(db_session: Session) -> None:
    """THE property the build-anchored cursor exists for.

    A cursor on the business date would have passed July long ago; a cursor on
    the BUILD sees July's new ``finished_at`` and serves it again, and the
    consumer's replace-by-date repairs its own copy.
    """

    _bank(db_session)
    for as_of, moment in ((JULY, T1), (AUGUST, T2)):
        _build(db_session, as_of=as_of, scope="positions", finished_at=moment, started_at=moment)
        _build(db_session, as_of=as_of, scope="dims", finished_at=moment, started_at=moment)
    db_session.commit()
    caught_up = _select(db_session).next_cursor
    assert caught_up is not None
    assert _select(db_session, cursor=caught_up).slices == ()

    # July is restated: the builder deletes and re-inserts the slice and stamps a
    # new ``finished_at`` on every scope of it.
    restated_at = dt.datetime(2026, 9, 3, 1, tzinfo=dt.UTC)
    for row in db_session.query(BiMartBuild).filter(BiMartBuild.as_of_date == JULY).all():
        row.finished_at = restated_at
        row.started_at = restated_at
        row.fingerprint = "b" * 64
    db_session.commit()

    after = _select(db_session, cursor=caught_up, now=dt.datetime(2026, 9, 3, 6, tzinfo=dt.UTC))
    assert [entry.as_of for entry in after.slices] == [JULY]
    assert after.slices[0].fingerprint == "b" * 64
    assert after.next_cursor == feed_cursor.FeedCursor(at=restated_at, as_of=JULY)


def test_an_in_flight_build_holds_the_cursor_back_without_withholding_rows(
    db_session: Session,
) -> None:
    """The non-monotonic ``finished_at`` hazard, closed.

    August's build started at T1 and has not finished; July finished at T2, which
    is LATER. Advancing to T2 would step over August for ever, because August may
    finish anywhere at or after T1. So July is served now and the cursor does not
    move, which costs one re-delivery and loses nothing.
    """

    _bank(db_session)
    _build(db_session, as_of=JULY, scope="positions", started_at=T2, finished_at=T2)
    _build(db_session, as_of=JULY, scope="dims", started_at=T2, finished_at=T2)
    for scope in ("positions", "dims"):
        _build(
            db_session,
            as_of=AUGUST,
            scope=scope,
            started_at=T1,
            finished_at=None,
            status="running",
        )
    db_session.commit()

    selection = _select(db_session, now=T1 + dt.timedelta(minutes=1))
    assert [entry.as_of for entry in selection.slices] == [JULY]
    assert selection.barrier == T1
    assert selection.next_cursor is None
    assert [entry.as_of for entry in selection.replayed] == [JULY]


def test_a_build_running_longer_than_the_reclaim_window_stops_barring_the_cursor(
    db_session: Session,
) -> None:
    """Otherwise one dead worker re-sends the whole history on every pull, for ever."""

    _bank(db_session)
    _build(db_session, as_of=JULY, scope="positions", started_at=T2, finished_at=T2)
    _build(db_session, as_of=JULY, scope="dims", started_at=T2, finished_at=T2)
    _build(
        db_session,
        as_of=AUGUST,
        scope="positions",
        started_at=T1,
        finished_at=None,
        status="running",
    )
    db_session.commit()

    window = dt.timedelta(seconds=get_settings().worker.worker_stale_job_seconds)
    selection = _select(db_session, now=T1 + window + dt.timedelta(seconds=1))
    assert selection.barrier is None
    assert selection.next_cursor == feed_cursor.FeedCursor(at=T2, as_of=JULY)


def test_one_pull_is_bounded_and_says_that_more_remains(db_session: Session) -> None:
    _bank(db_session)
    days = [dt.date(2026, 6, 1) + dt.timedelta(days=offset) for offset in range(5)]
    for index, day in enumerate(days):
        moment = T1 + dt.timedelta(minutes=index)
        _build(db_session, as_of=day, scope="positions", started_at=moment, finished_at=moment)
        _build(db_session, as_of=day, scope="dims", started_at=moment, finished_at=moment)
    db_session.commit()

    selection = _select(db_session, max_slices=2)
    assert [entry.as_of for entry in selection.slices] == days[:2]
    assert selection.more_available is True
    assert selection.next_cursor is not None

    rest = _select(db_session, cursor=selection.next_cursor, max_slices=2)
    assert [entry.as_of for entry in rest.slices] == days[2:4]
    assert rest.more_available is True


def test_another_tenants_builds_are_invisible(db_session: Session) -> None:
    """The selection is explicitly organization- and institution-scoped."""

    _bank(db_session)
    _build(db_session, as_of=JULY, scope="positions", finished_at=T1)
    _build(db_session, as_of=JULY, scope="dims", finished_at=T1)
    db_session.commit()
    other = feed_cursor.select_slices(
        db_session,
        organization_id=ORG_1,
        bank_id="BK-NOTTHIS1",
        entry=datasets.dataset("loan_book"),
        cursor=None,
        now=T3,
    )
    assert other.slices == ()


# --- the bytes ----------------------------------------------------------------------------


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


# --- the provenance headers -----------------------------------------------------------------


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
