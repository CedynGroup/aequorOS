"""The nightly capture's REQUEST BUDGET: what the desk asks BoG for, and why.

On 2026-08-23 the desk POSTed ``bog.gov.gh`` roughly every two seconds for
eleven hours. The pacing was never the defect — 2s between requests is
correct and is enforced unchanged. The defect was the request COUNT: table 40
(historical interbank FX) is the whole published archive, 144,647 rows on
2026-08-19, and ``fetch_wdt_table`` walked all 145 pages of it every single
night. Both the requests and the stored bytes grew with HISTORY, not with
news — 870 of the primary's 960 capture rows and 20 of its 27 MB were that
one source re-downloading rates first published in 1996.

Two facts fix it, and this module pins both:

1. the fetch is bounded by the newest date the desk already holds from that
   source, so a night costs what the news costs — and a COLD START, which has
   no such date, still walks the archive in full rather than invent one;
2. the nightly FX read is table 31, which publishes the same six columns for
   the latest day in ONE page. Table 40 is the backfill path.

Every request here runs the REAL capture client (nonce scrape, paged
``admin-ajax`` POSTs, watermark bound) over ``httpx.MockTransport``. Nothing
in this suite touches bog.gov.gh.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.ids import new_uuid7
from app.models import DeskObservation, DeskSourceCapture, Job
from app.services import job_queue
from app.services.market_desk import capture_job
from app.services.market_desk.capture_job import run_desk_capture
from app.services.market_desk.sources.core import QF_STALE_SOURCE
from app.services.market_desk.sources.fetch import DeskSession, Pacer
from tests.api.helpers import ORG_1

#: The harvest's newest published day (fixtures README, 2026-08-09 harvest
#: refreshed through 2026-08-19 on the primary).
NEWEST = date(2026, 8, 19)

#: Enough published days to cross a page boundary at the production page
#: length, so "one page" and "the whole archive" are genuinely different
#: walks — 1,005 days is 2 pages, exactly as 144,647 rows is 145.
ARCHIVE_DAYS = 1005

FX_HISTORICAL = "bog_fx_historical"
FX_DAILY = "bog_fx_daily"


@pytest.fixture(autouse=True)
def _settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _enable(monkeypatch: pytest.MonkeyPatch, sources: str) -> None:
    monkeypatch.setenv("DESK_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("DESK_CAPTURE_SOURCES", sources)
    get_settings.cache_clear()


def _desk_job(db: Session, payload: dict[str, Any]) -> Job:
    job = job_queue.enqueue(db, ORG_1, "desk_capture", payload=payload)
    db.commit()
    return job


class _FakeBogFxTables:
    """The two BoG FX views, served from ONE day-indexed book.

    Table 40 is the archive: every published day, newest first (the order the
    request asks for), paged by ``start``/``length`` exactly like
    ``admin-ajax``. Table 31 is the same six columns for the latest day only.
    Each day's rate is derived from its own date, so a row can never silently
    end up filed under the wrong day.
    """

    def __init__(self, *, newest: date = NEWEST, days: int = ARCHIVE_DAYS) -> None:
        self.newest = newest
        self.days = days
        self.posts: dict[int, list[int]] = {31: [], 40: []}
        self.page_gets = 0

    def publish_next_day(self) -> None:
        self.newest += timedelta(days=1)
        self.days += 1

    def _row(self, day: date) -> list[str]:
        rate = f"11.{day.toordinal() % 10000:04d}"
        return [day.strftime("%d %b %Y"), "US Dollar", "USDGHS", rate, rate, rate]

    def rows(self, table_id: int) -> list[list[str]]:
        if table_id == 31:
            return [self._row(self.newest)]
        return [self._row(self.newest - timedelta(days=n)) for n in range(self.days)]

    @property
    def requests(self) -> int:
        """Everything this run asked the site for — host pages and data pages."""
        return self.page_gets + sum(len(starts) for starts in self.posts.values())

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            self.page_gets += 1
            return httpx.Response(
                200,
                text=" ".join(
                    f'name="wdtNonceFrontendServerSide_{table_id}" value="c2041d81bf"'
                    for table_id in (31, 40)
                ),
            )
        table_id = int(request.url.params["table_id"])
        form = parse_qs(request.content.decode())
        start = int(form["start"][0])
        length = int(form["length"][0])
        self.posts[table_id].append(start)
        rows = self.rows(table_id)
        return httpx.Response(
            200,
            json={
                "draw": int(form["draw"][0]),
                # recordsTotal LIES by design (the shared underlying table).
                "recordsTotal": 144647,
                "recordsFiltered": len(rows),
                "data": rows[start : start + length],
            },
        )


def _offline_site(monkeypatch: pytest.MonkeyPatch, site: _FakeBogFxTables) -> None:
    """Point the REAL capture client at a mock transport.

    ``client_kwargs`` is the module's injected-client seam, so the whole
    wpDataTables protocol executes for real and only the socket is replaced.
    The pacer is zeroed because these tests measure request COUNT; the 2s
    minimum interval is pinned in
    ``tests/services/market_desk_sources/test_fetch.py`` and is never relaxed
    in production — the incident was a count defect, not a rate defect.
    """
    monkeypatch.setattr(
        capture_job,
        "client_kwargs",
        lambda host: {"transport": httpx.MockTransport(site.handler)},
    )
    monkeypatch.setattr(
        capture_job,
        "DeskSession",
        lambda client: DeskSession(client, pacer=Pacer(0.0)),
    )


def _run(db: Session, site: _FakeBogFxTables, **payload: Any) -> dict[str, Any]:
    job = _desk_job(db, {"cob_date": site.newest.isoformat(), **payload})
    run_desk_capture(db, job)
    return job.progress["sources"]


def _current_observations(
    db: Session, *, keep_staleness: bool = False
) -> set[tuple[Any, ...]]:
    """Every CURRENT observation, fingerprinted down to its quality flags.

    ``keep_staleness=False`` drops ``stale_source`` and its bookkeeping
    attribute. That flag is not a property of an observation but of the PAGE
    it arrived on: ``apply_staleness`` runs once per capture and marks the
    newest row IN THAT PAGE, so which historical row carries it is decided by
    where a page boundary happens to fall — and boundaries shift by one row
    every time the publisher adds a day, watermark or no watermark.
    ``test_page_boundary_staleness_flags_already_moved_every_night`` pins that
    this predates the bound; nothing here changes it.
    """
    rows = db.scalars(
        select(DeskObservation).where(DeskObservation.superseded_by.is_(None))
    )
    fingerprints: set[tuple[Any, ...]] = set()
    for row in rows:
        attributes = dict(row.attributes)
        flags = list(row.quality_flags)
        if not keep_staleness:
            attributes.pop("staleness_gap_days", None)
            flags = [flag for flag in flags if flag != QF_STALE_SOURCE]
        fingerprints.add(
            (
                row.series_code,
                row.as_of_date.isoformat(),
                str(Decimal(row.value)),
                row.unit,
                json.dumps(attributes, sort_keys=True),
                tuple(sorted(flags)),
            )
        )
    return fingerprints


def _stale_flagged_dates(db: Session) -> set[str]:
    rows = db.scalars(
        select(DeskObservation).where(DeskObservation.superseded_by.is_(None))
    )
    return {r.as_of_date.isoformat() for r in rows if QF_STALE_SOURCE in r.quality_flags}


def _reset_desk(db: Session) -> None:
    """Clear the desk planes so the next arm starts from a true cold start."""
    db.execute(delete(DeskObservation))
    db.execute(delete(DeskSourceCapture))
    db.commit()


def _seed_history(db: Session, source_key: str, as_of: date) -> None:
    """One parsed capture and one observation from it — the minimum state
    that makes a source 'already captured' with a watermark of ``as_of``."""
    capture = DeskSourceCapture(
        id=new_uuid7(),
        source_key=source_key,
        as_of_date=as_of,
        content_sha256=hashlib.sha256(b"seed").hexdigest(),
        parser_version="bog_wdt/1",
        status="parsed",
        created_by="desk-analyst@aequoros.com",
    )
    db.add(capture)
    db.flush()
    db.add(
        DeskObservation(
            id=new_uuid7(),
            capture_id=capture.id,
            series_code="GHS.FX.USDGHS.MID",
            as_of_date=as_of,
            value=Decimal("11.7615"),
            unit="rate",
        )
    )
    db.commit()


# ---------------------------------------------------------------------------
# The bound itself
# ---------------------------------------------------------------------------


def test_cold_start_walks_the_whole_archive(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With nothing stored there is no watermark, and the only honest bound
    is no bound: the archive is fetched in full.

    A watermark invented to make a first run cheap would silently truncate
    the history the desk then claims to hold — worse than the bug it avoids.
    """
    _enable(monkeypatch, FX_HISTORICAL)
    site = _FakeBogFxTables()
    _offline_site(monkeypatch, site)

    summary = _run(db_session, site)[FX_HISTORICAL]

    assert summary["status"] == "captured"
    assert summary["due"] == "never_captured"
    assert summary["since"] is None
    assert site.posts[40] == [0, 1000]  # both pages
    assert summary["captures"] == 2
    assert summary["observations"] == ARCHIVE_DAYS * 4  # BUY/SELL/MID + alias


def test_a_watermarked_walk_costs_one_page(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The nightly shape: one new day published, one page fetched.

    On the real table 40 this is the difference between 145 data pages and 1.
    """
    _enable(monkeypatch, FX_HISTORICAL)
    site = _FakeBogFxTables()
    _offline_site(monkeypatch, site)
    _run(db_session, site)  # cold start
    cold_requests = site.requests

    site.publish_next_day()
    site.posts[40].clear()
    site.page_gets = 0

    summary = _run(db_session, site, backfill_sources=[FX_HISTORICAL])[FX_HISTORICAL]

    assert summary["since"] == NEWEST.isoformat()
    assert site.posts[40] == [0]  # one data page reaches back past the watermark
    assert site.requests == 2  # host page + that one data page
    assert site.requests < cold_requests


def test_the_watermark_is_this_source_own_newest_observation(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bound is read from the observations' lineage, not from a capture's
    as-of date: a capture is stamped with the COB it RAN on, an observation
    with the date the publisher PRINTED, and only the latter can bound a
    fetch. Another source's history must never bound this one."""
    _enable(monkeypatch, FX_HISTORICAL)
    _seed_history(db_session, FX_HISTORICAL, NEWEST - timedelta(days=3))
    _seed_history(db_session, "bog_interbank_daily", NEWEST)
    site = _FakeBogFxTables()
    _offline_site(monkeypatch, site)

    summary = _run(db_session, site, backfill_sources=[FX_HISTORICAL])[FX_HISTORICAL]

    assert summary["since"] == (NEWEST - timedelta(days=3)).isoformat()
    assert site.posts[40] == [0]


# ---------------------------------------------------------------------------
# The bound changes no number
# ---------------------------------------------------------------------------


def test_watermarked_capture_derives_the_same_observations_as_a_full_walk(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The proof that this narrows the FETCH and nothing else.

    Arm A is how the desk will now run: a cold-start walk of the archive,
    then a watermarked walk after one more day is published. Arm B is the
    same site walked in full from scratch. The two must leave the desk
    holding an identical set of observations — same series, dates, values,
    units, attributes and quality flags — because the pages that arm A did
    not re-fetch carry rows it already stored, byte for byte.

    Two things this deliberately does NOT claim. A bounded walk cannot see a
    RETROACTIVE edit to an old row — that is inherent to bounding a fetch,
    and it is why the archive walk stays available as a deliberate backfill
    rather than being deleted. And the ``stale_source`` flag is excluded,
    because it belongs to a PAGE rather than to an observation; the next test
    shows it already moved nightly before any of this.
    """
    _enable(monkeypatch, FX_HISTORICAL)
    site = _FakeBogFxTables()
    _offline_site(monkeypatch, site)

    # Arm A — cold start, then one watermarked night.
    _run(db_session, site)
    site.publish_next_day()
    site.posts[40].clear()
    _run(db_session, site, backfill_sources=[FX_HISTORICAL])
    incremental = _current_observations(db_session)
    incremental_pages = list(site.posts[40])

    # Arm B — the same site, walked in full from nothing.
    _reset_desk(db_session)
    site.posts[40].clear()
    _run(db_session, site)
    full_walk = _current_observations(db_session)
    full_walk_pages = list(site.posts[40])

    assert incremental == full_walk
    assert len(incremental) == (ARCHIVE_DAYS + 1) * 4
    # ... and it cost a fraction of the requests.
    assert incremental_pages == [0]
    assert full_walk_pages == [0, 1000]


def test_page_boundary_staleness_flags_already_moved_every_night(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one thing the equivalence proof excludes, shown to predate it.

    ``apply_staleness`` runs once per CAPTURE and flags the newest row in
    that page, so on a paged archive the flag lands on whichever historical
    row a page boundary happens to fall on. Publishing one more day shifts
    every boundary by one row — and here BOTH arms are unbounded full walks,
    no watermark involved, yet the flag moves. That is why the equivalence
    test compares economic content and leaves this artifact alone: it is a
    property of per-page staleness evaluation, and changing it would change
    how observations are derived, which this work must not do.
    """
    _enable(monkeypatch, FX_HISTORICAL)
    site = _FakeBogFxTables()
    _offline_site(monkeypatch, site)

    _run(db_session, site)
    before = _stale_flagged_dates(db_session)

    _reset_desk(db_session)
    site.publish_next_day()
    _run(db_session, site)
    after = _stale_flagged_dates(db_session)

    # Both arms walked the archive in full; only the publisher moved.
    assert before and after
    assert before != after
    assert len(before) == len(after) == 1  # one page boundary, one flagged day
    moved_by = date.fromisoformat(next(iter(after))) - date.fromisoformat(next(iter(before)))
    assert moved_by == timedelta(days=1)


# ---------------------------------------------------------------------------
# Which table the nightly read uses
# ---------------------------------------------------------------------------


def test_table_40_sits_out_the_nightly_walk_and_table_31_serves_it(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Table 31 publishes the same six columns for the latest day in one
    page, so the nightly FX read is table 31 and the archive is left alone.

    The skip is reported with the date the archive reaches, because "we did
    not walk the archive tonight" is only reassuring next to how far it goes.
    """
    _enable(monkeypatch, f"{FX_DAILY},{FX_HISTORICAL}")
    _seed_history(db_session, FX_HISTORICAL, NEWEST - timedelta(days=1))
    site = _FakeBogFxTables()
    _offline_site(monkeypatch, site)

    sources = _run(db_session, site)

    assert sources[FX_DAILY]["status"] == "captured"
    assert sources[FX_DAILY]["observations"] == 4  # one day, one pair
    assert site.posts[31] == [0]

    assert sources[FX_HISTORICAL] == {
        "status": "skipped_not_due",
        "reason": "backfill_only",
        "covered_through": (NEWEST - timedelta(days=1)).isoformat(),
    }
    assert site.posts[40] == []
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(DeskSourceCapture)
            .where(DeskSourceCapture.source_key == FX_HISTORICAL)
        )
        == 1  # only the seeded row; tonight added nothing
    )


def test_a_named_backfill_is_the_only_thing_that_reopens_the_archive(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deliberate, not incidental: the archive walk happens when a run asks
    for it by name (and a cold start, which has no history to be cheap
    about)."""
    _enable(monkeypatch, FX_HISTORICAL)
    _seed_history(db_session, FX_HISTORICAL, NEWEST - timedelta(days=1))
    site = _FakeBogFxTables()
    _offline_site(monkeypatch, site)

    assert _run(db_session, site)[FX_HISTORICAL]["reason"] == "backfill_only"
    assert site.posts[40] == []

    summary = _run(db_session, site, backfill_sources=[FX_HISTORICAL])[FX_HISTORICAL]
    assert summary["status"] == "captured"
    assert summary["due"] == "backfill_requested"
    assert site.posts[40] == [0]


def test_the_allow_list_still_outranks_a_backfill_request(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``DESK_CAPTURE_SOURCES`` is the deployment's hard boundary on what may
    be scraped at all; a job payload cannot argue with it."""
    _enable(monkeypatch, FX_DAILY)
    site = _FakeBogFxTables()
    _offline_site(monkeypatch, site)

    sources = _run(db_session, site, backfill_sources=[FX_HISTORICAL])

    assert sources[FX_HISTORICAL] == {"status": "skipped_not_allowed"}
    assert site.posts[40] == []
