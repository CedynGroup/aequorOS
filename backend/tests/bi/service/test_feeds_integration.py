"""The feed's three decidable parts: the registry, the cursor, and the bytes.

The route is ``tests/bi/api/test_bi_feeds_integration.py``. What is here is everything that can
be decided without a request, and the cursor is the reason this file exists: a
build-anchored cursor is the difference between a report server that follows a
restatement and one that disagrees with the platform for ever, and that property
is a function of ``bi_mart_builds`` alone.

This module's other-suite cases live in ``test_feeds.py``.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.bi.models.marts import BiMartBuild
from app.bi.service.feeds import cursor as feed_cursor
from app.bi.service.feeds import datasets
from app.core.config import get_settings
from app.models import Bank
from tests.bi.service.test_feeds import AUGUST, T2
from tests.support.helpers import ORG_1

BANK_ID = "BK-FEEDSVC1"
JULY = dt.date(2026, 7, 31)
T1 = dt.datetime(2026, 9, 1, 2, 0, tzinfo=dt.UTC)
T3 = dt.datetime(2026, 9, 2, 4, 0, tzinfo=dt.UTC)


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
