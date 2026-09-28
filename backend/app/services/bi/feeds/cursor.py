"""The feed cursor, and why it is anchored on the BUILD and not on the row.

The obvious cursor for an incremental feed is the business date: "give me
everything after 30 June". It is also wrong here, and wrong in the way that does
not announce itself.

**A bank's book is restated.** A correction to March arrives in September; a
withdrawal is reversed; a register changes and the whole month is re-derived. The
mart is rebuilt accordingly — ``mart_builder`` deletes and re-inserts the
``(bank, reporting date)`` slice — so the ROWS for an old date change while their
date does not. A cursor on the business date would have passed that date long
ago, the report server would never read it again, and its numbers would disagree
with the platform's for as long as the model lived. Nothing would fail. The bank
would simply have two sets of figures, and the one in the BI tool would be the
one on the slide.

**So the cursor is anchored on the build.** ``bi_mart_builds`` carries one row
per ``(bank, reporting date, scope)`` with the build's ``finished_at``, and every
rebuild of a date stamps a NEW ``finished_at`` on every scope of it. The cursor
is therefore a position in build time, the unit of delivery is a whole reporting
date (never a partial diff of one), and a restatement of March re-appears in the
next pull because March's build is newer than the cursor. The consumer's
contract, which the documentation states as the one thing it must implement, is
**replace by reporting date**: delete its own rows for every date in the payload,
then insert the payload. That is idempotent, so re-delivery is free, which is
what lets everything below err towards re-sending.

**A slice is servable only when every scope the dataset reads has SUCCEEDED**
for that date. A dataset declares its scopes (``datasets.py``); a date whose
``positions`` build failed is not offered as an empty or partial slice, because a
feed row that is missing is read by the consumer as a figure that is zero.

**The barrier: why the cursor lags an in-flight build.** ``finished_at`` is not
monotonic across concurrent builds. If date A finishes at 10:05 and date B — which
started at 10:00 — finishes at 10:03, a pull at 10:04 that served A and advanced
to 10:05 would step over B for ever. So the cursor advances only as far as the
OLDEST build still running (:data:`BARRIER`): slices at or after that instant are
served now and served again next time. A build that has been running longer than
the worker's own reclaim window is ignored for this purpose — it is dead or has
already been reclaimed, and the reclaimed run will stamp its own later
``finished_at`` — because otherwise one stuck build would freeze the cursor and
re-send the whole history on every pull, for ever.

The cursor token is deliberately opaque-but-readable (``aeqf1.<micros>.<date>``)
and is VALIDATED, not parsed permissively: a malformed cursor is refused rather
than treated as "start from the beginning", because silently re-sending a
bank's whole history in response to a typo is a denial of service dressed as
helpfulness.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utc_now
from app.models.bi import BiMartBuild
from app.services.bi.feeds.datasets import FeedDataset

#: Token version. A change in what the cursor MEANS changes this prefix, so an
#: old token is refused loudly rather than reinterpreted against new semantics.
CURSOR_PREFIX: Final = "aeqf1"
_TOKEN = re.compile(r"\Aaeqf1\.(\d{1,19})\.(\d{8})\Z")
#: The build status a slice must be in, for every scope its dataset reads.
SUCCEEDED: Final = "succeeded"
_MICROS_PER_SECOND = 1_000_000
#: How many reporting dates one pull will serve. A bank's first sync spans years
#: of daily builds; answering that in ONE response would hold a connection open
#: for hours and lose everything if it broke. The consumer pulls again with the
#: cursor it was handed, which is the same loop it already runs.
MAX_SLICES_PER_PULL: Final = 40


class InvalidCursor(Exception):  # noqa: N818 - a refusal, raised and rendered as 422
    """The cursor token is not one this feed issued."""

    def __init__(self, token: str) -> None:
        # The token is client bytes and is NOT repeated into the message or any
        # log: it is echoed nowhere, exactly as a filter value never reaches
        # ``bi_query_log``.
        super().__init__("the cursor is not a token this feed issued")
        self.length = len(token)


@dataclass(frozen=True, slots=True, order=True)
class FeedCursor:
    """A position in BUILD time: the instant, then the reporting date.

    The reporting date is the tiebreaker, so two slices whose builds finished in
    the same microsecond still have a strict order and neither can be skipped.
    """

    at: dt.datetime
    as_of: dt.date

    def encode(self) -> str:
        micros = int(self.at.timestamp() * _MICROS_PER_SECOND)
        return f"{CURSOR_PREFIX}.{micros}.{self.as_of:%Y%m%d}"


def decode(token: str) -> FeedCursor:
    """The cursor a token names, or :class:`InvalidCursor`."""

    match = _TOKEN.match(token.strip())
    if match is None:
        raise InvalidCursor(token)
    micros, day = match.groups()
    try:
        moment = dt.datetime.fromtimestamp(int(micros) / _MICROS_PER_SECOND, tz=dt.UTC)
        as_of = dt.datetime.strptime(day, "%Y%m%d").replace(tzinfo=dt.UTC).date()
    except (ValueError, OverflowError, OSError) as exc:
        raise InvalidCursor(token) from exc
    return FeedCursor(at=moment, as_of=as_of)


def _aware(value: dt.datetime) -> dt.datetime:
    """SQLite hands back naive datetimes; every comparison here must not raise."""

    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


@dataclass(frozen=True, slots=True)
class FeedSlice:
    """One reporting date's worth of a dataset, and where its build sits."""

    as_of: dt.date
    #: The newest ``finished_at`` across the scopes the dataset reads.
    position: dt.datetime
    fingerprint: str

    @property
    def cursor(self) -> FeedCursor:
        return FeedCursor(at=self.position, as_of=self.as_of)


@dataclass(frozen=True, slots=True)
class SliceSelection:
    """What this pull serves, and how far the caller's cursor may advance."""

    slices: tuple[FeedSlice, ...]
    #: Where the caller should resume. ``None`` means "nothing may be committed
    #: yet": either nothing was served, or everything served sits at or after the
    #: barrier and must be re-read next time.
    next_cursor: FeedCursor | None
    #: The oldest still-running build the cursor must not step over.
    barrier: dt.datetime | None
    #: Whether reporting dates newer than ``next_cursor`` remain unserved because
    #: this pull hit :data:`MAX_SLICES_PER_PULL`. A first sync of a bank with ten
    #: years of daily builds is several pulls, not one six-hour response.
    more_available: bool = False

    @property
    def replayed(self) -> tuple[FeedSlice, ...]:
        """Slices served now that the next pull will serve again."""

        if self.next_cursor is None:
            return self.slices
        return tuple(entry for entry in self.slices if entry.cursor > self.next_cursor)

    @property
    def window(self) -> tuple[dt.date, dt.date] | None:
        """The first and last reporting date served, for the provenance block."""

        if not self.slices:
            return None
        days = [entry.as_of for entry in self.slices]
        return min(days), max(days)


def select_slices(  # noqa: PLR0913 - the selection's own inputs, all explicit
    db: Session,
    *,
    organization_id: str,
    bank_id: str,
    entry: FeedDataset,
    cursor: FeedCursor | None,
    now: dt.datetime | None = None,
    max_slices: int = MAX_SLICES_PER_PULL,
) -> SliceSelection:
    """Every reporting date whose build is newer than ``cursor``, oldest first.

    Reads ``bi_mart_builds`` only — no mart row is touched here, so a caller that
    is refused authorization never reaches a figure. Serving MORE than strictly
    necessary is safe (the consumer replaces by date); serving less is not, which
    is the asymmetry every rule in the module docstring is built around.
    """

    required = set(entry.build_scopes)
    rows = db.execute(
        select(
            BiMartBuild.as_of_date,
            BiMartBuild.scope,
            BiMartBuild.status,
            BiMartBuild.started_at,
            BiMartBuild.finished_at,
            BiMartBuild.fingerprint,
        ).where(
            BiMartBuild.organization_id == organization_id,
            BiMartBuild.bank_id == bank_id,
        )
    ).all()

    moment = now or utc_now()
    reclaim_after = dt.timedelta(seconds=get_settings().worker.worker_stale_job_seconds)
    barrier: dt.datetime | None = None
    ready: dict[dt.date, dict[str, tuple[dt.datetime, str]]] = {}
    for row in rows:
        if row.finished_at is None:
            # Still running. It bars the cursor unless it has been running longer
            # than the reclaim window, in which case the worker has already given
            # up on it and its replacement will stamp a later ``finished_at``.
            started = _aware(row.started_at)
            if moment - started <= reclaim_after and (barrier is None or started < barrier):
                barrier = started
            continue
        if row.scope not in required or row.status != SUCCEEDED:
            continue
        ready.setdefault(row.as_of_date, {})[row.scope] = (
            _aware(row.finished_at),
            row.fingerprint,
        )

    slices: list[FeedSlice] = []
    for as_of, scopes in ready.items():
        if set(scopes) != required:
            continue
        newest = max(scopes.values(), key=lambda pair: pair[0])
        candidate = FeedSlice(as_of=as_of, position=newest[0], fingerprint=newest[1])
        if cursor is None or candidate.cursor > cursor:
            slices.append(candidate)
    slices.sort(key=lambda entry_: (entry_.position, entry_.as_of))
    if max_slices < 1:
        raise ValueError("max_slices must be positive")
    more_available = len(slices) > max_slices
    served = slices[:max_slices]

    committable = [
        entry_.cursor for entry_ in served if barrier is None or entry_.position < barrier
    ]
    return SliceSelection(
        slices=tuple(served),
        next_cursor=max(committable) if committable else None,
        barrier=barrier,
        more_available=more_available,
    )


__all__ = [
    "CURSOR_PREFIX",
    "MAX_SLICES_PER_PULL",
    "SUCCEEDED",
    "FeedCursor",
    "FeedSlice",
    "InvalidCursor",
    "SliceSelection",
    "decode",
    "select_slices",
]
