"""Compile, page and describe one feed pull. Reads only; writes nothing.

Everything a pull needs that is not a decision and not a byte format:

* **the columns**, which come from the compiler's own ``ColumnSpec`` list and are
  therefore the catalogue's, not a second declaration of the same figures;
* **the rows**, one slice at a time, paged so the memory cost of a pull is a page
  rather than the payload;
* **the provenance**, which travels in the response headers because the payload
  itself must stay a homogeneous table a report server can map one schema over.

**Why paged reads rather than one server-side cursor.** The only guarded executor
in the BI plane is ``execution.run_select``: it runs a statement inside a
savepoint with a transaction-local ``statement_timeout`` and
``transaction_read_only``, and the two ``SET`` statements it issues are the ONLY
SQL text allowed anywhere under ``app/services/bi`` (an AST guard forbids
``text()``, ``literal_column()`` and ``exec_driver_sql()`` everywhere else). A
streaming server-side cursor would need its own version of those guards, so this
module pages through ``run_select`` instead: every page is a fully guarded,
read-only, timeout-bounded statement, and no new SQL exists.

Paging is safe here BECAUSE of the cursor design. Offset paging over a mutable
table can repeat or skip a row if the table changes between pages — and the mart
slice being paged could be rebuilt mid-pull. That rebuild stamps a NEW
``finished_at`` on the slice's build rows, which is strictly later than the
cursor this pull hands back, so the next pull serves the whole slice again and the
consumer's replace-by-reporting-date makes the repair. A torn page is therefore
corrected by the next pull rather than living in the report model for ever, which
is the property the build-anchored cursor exists to provide.

**Header values are sanitised, and that is a security property rather than
tidiness.** Several of them carry BANK DATA — a data-scope label names branch
codes and region names, which arrive through the Data Engine from a core banking
system. A value containing CR or LF would inject a header; a value containing any
non-ASCII character is written by Starlette as latin-1 and read back by whatever
the report server uses, which is a silent mojibake at best and a decode failure at
worst (the platform's own test client raises on it). :func:`header_text` therefore
reduces every value to printable ASCII before it is sent.

**The shape may not change mid-pull.** Each slice is compiled separately (each is
its own reporting date), and the column list of every slice is asserted against
the first. A payload whose columns changed halfway is worse for a report server
than a refusal, so it IS a refusal.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from sqlalchemy.orm import Session

from app.domain.bi.catalogue import CATALOGUE_VERSION, Catalogue
from app.models import Bank
from app.schemas.bi import BiFilter
from app.services import jurisdictions
from app.services.bi import provenance
from app.services.bi.compiler import ColumnSpec, compile_query
from app.services.bi.execution import run_select
from app.services.bi.exports import policy
from app.services.bi.feeds.cursor import FeedSlice, SliceSelection
from app.services.bi.feeds.datasets import FeedDataset
from app.services.bi.feeds.render import FeedFormat

#: Rows read per statement. Large enough that a slice of a few thousand grouped
#: rows is one or two statements, small enough that the memory cost of a pull is
#: bounded whatever the payload.
PAGE_ROWS: Final = 5_000

#: What a header says when the thing it names does not exist. A word rather than
#: an empty value, so a consumer reading the header cannot confuse "absent" with
#: "not sent".
NONE_SENTINEL: Final = "none"

#: The response headers every pull carries. Named as a tuple so a test can hold
#: the response to the WHOLE set rather than to the ones it happens to check.
FEED_HEADERS: Final[tuple[str, ...]] = (
    "X-Bi-Feed-Dataset",
    "X-Bi-Feed-Grain",
    "X-Bi-Feed-Disclosure-Class",
    "X-Bi-Feed-Columns",
    "X-Bi-Feed-Cursor",
    "X-Bi-Feed-Next-Cursor",
    "X-Bi-Feed-More-Available",
    "X-Bi-Feed-Reporting-Dates",
    "X-Bi-Feed-Reporting-Date-Count",
    "X-Bi-Feed-Data-Scope",
    "X-Bi-Feed-Catalogue-Version",
    "X-Bi-Feed-Build",
    "X-Bi-Feed-Unit",
)


#: The only characters a feed header value may contain: printable ASCII. Anything
#: else — a CR or LF that would inject a header, a non-ASCII character in a branch
#: name that Starlette writes as latin-1 — is replaced.
_HEADER_SAFE = frozenset(chr(code) for code in range(0x20, 0x7F))
#: What an unprintable or non-ASCII character becomes. A space rather than a
#: removal, so two distinct labels cannot collapse into one string.
_HEADER_REPLACEMENT = " "


def header_text(value: str) -> str:
    """One header value, reduced to printable ASCII.

    Applied to EVERY value in :meth:`FeedProvenance.headers`, not only the ones
    that look like data, because the point is that no future field can reintroduce
    the hazard by being added to the dictionary without a thought. Runs of
    replacements collapse so a name written in a non-Latin script does not become
    a header of forty spaces, and the result is stripped.
    """

    cleaned = "".join(
        character if character in _HEADER_SAFE else _HEADER_REPLACEMENT for character in value
    )
    return " ".join(cleaned.split())


class FeedShapeChanged(Exception):  # noqa: N818 - a refusal, raised and rendered as 409
    """Two slices of one pull compiled to different columns.

    Reachable only if the catalogue changed under a running pull. Refused rather
    than served, because a report server whose dataset silently changed shape is
    worse off than one that was told to try again.
    """


def columns_for(  # noqa: PLR0913 - one compile, its scope and its guards, all explicit
    db: Session,
    *,
    cat: Catalogue,
    entry: FeedDataset,
    organization_id: str,
    bank_id: str,
    injected: Sequence[BiFilter],
    as_of: date,
) -> tuple[ColumnSpec, ...]:
    """The column list one slice of this dataset compiles to."""

    compiled = compile_query(
        db,
        cat,
        entry.query(as_of),
        organization_id=organization_id,
        bank_id=bank_id,
        injected_filters=tuple(injected),
    )
    return compiled.columns


def iter_rows(  # noqa: PLR0913 - one read, its guards and its scope, all explicit
    db: Session,
    *,
    cat: Catalogue,
    entry: FeedDataset,
    slices: Sequence[FeedSlice],
    organization_id: str,
    bank_id: str,
    injected: Sequence[BiFilter],
    timeout_ms: int,
    columns: Sequence[ColumnSpec],
    page_rows: int = PAGE_ROWS,
) -> Iterator[tuple[Any, ...]]:
    """Every row of every slice, oldest build first, in the compiler's order.

    ``columns`` is the shape the caller has already announced to the client; a
    slice that compiles to anything else raises :class:`FeedShapeChanged` rather
    than emitting a row that does not fit the header already on the wire.
    """

    if page_rows < 1:
        raise ValueError("page_rows must be positive")
    expected = tuple(column.id for column in columns)
    for slice_ in slices:
        compiled = compile_query(
            db,
            cat,
            entry.query(slice_.as_of),
            organization_id=organization_id,
            bank_id=bank_id,
            injected_filters=tuple(injected),
        )
        if tuple(column.id for column in compiled.columns) != expected:
            raise FeedShapeChanged(entry.id)
        offset = 0
        while True:
            statement = compiled.select.limit(page_rows + 1).offset(offset)
            rows = run_select(db, statement, organization_id=organization_id, timeout_ms=timeout_ms)
            for row in rows[:page_rows]:
                yield tuple(row)
            if len(rows) <= page_rows:
                break
            offset += page_rows


@dataclass(frozen=True, slots=True)
class FeedProvenance:
    """What the pull covered, for the response headers.

    The same six facts a governed export prints on its own artifact, minus the
    ones that only make sense on a document a person opens. It is deliberately
    NOT in the payload: an NDJSON stream with a metadata record would stop being
    a homogeneous table, and a CSV with a preamble stops being parseable by a
    report server's own CSV reader without a magic skip count.
    """

    dataset: FeedDataset
    columns: tuple[ColumnSpec, ...]
    requested_cursor: str | None
    selection: SliceSelection
    data_scope_label: str
    unit: str
    build_fingerprint: str | None

    def headers(self) -> dict[str, str]:
        """The provenance block, every value reduced to printable ASCII."""

        next_cursor = self.selection.next_cursor
        dates = tuple(slice_.as_of.isoformat() for slice_ in self.selection.slices)
        values = {
            "X-Bi-Feed-Dataset": self.dataset.id,
            "X-Bi-Feed-Grain": self.dataset.grain,
            "X-Bi-Feed-Disclosure-Class": policy.CLASS_LABELS[self.dataset.disclosure_class],
            "X-Bi-Feed-Columns": ",".join(column.id for column in self.columns),
            "X-Bi-Feed-Cursor": self.requested_cursor or NONE_SENTINEL,
            "X-Bi-Feed-Next-Cursor": (
                next_cursor.encode() if next_cursor is not None else NONE_SENTINEL
            ),
            "X-Bi-Feed-More-Available": "true" if self.selection.more_available else "false",
            "X-Bi-Feed-Reporting-Dates": ",".join(dates) if dates else NONE_SENTINEL,
            "X-Bi-Feed-Reporting-Date-Count": str(len(dates)),
            "X-Bi-Feed-Data-Scope": self.data_scope_label,
            "X-Bi-Feed-Catalogue-Version": CATALOGUE_VERSION,
            "X-Bi-Feed-Build": self.build_fingerprint or NONE_SENTINEL,
            "X-Bi-Feed-Unit": self.unit,
            "Cache-Control": "private, no-store",
        }
        return {name: header_text(value) for name, value in values.items()}


def build_provenance(  # noqa: PLR0913 - the provenance block's own inputs, all explicit
    db: Session,
    *,
    bank: Bank,
    entry: FeedDataset,
    columns: tuple[ColumnSpec, ...],
    selection: SliceSelection,
    requested_cursor: str | None,
    data_scope_label: str,
) -> FeedProvenance:
    """The provenance for one pull, read from the same source an export uses."""

    window = selection.window
    fingerprint: str | None = None
    if window is not None:
        fingerprint = provenance.build_fingerprint(
            db, organization_id=bank.organization_id, bank_id=bank.id, window=window
        )
    return FeedProvenance(
        dataset=entry,
        columns=columns,
        requested_cursor=requested_cursor,
        selection=selection,
        data_scope_label=data_scope_label,
        unit=jurisdictions.base_currency(bank),
        build_fingerprint=fingerprint,
    )


def filename_for(entry: FeedDataset, fmt: FeedFormat, bank_id: str) -> str:
    """A stable name for a saved pull: institution, dataset, format. No clock."""

    return f"{bank_id}-{entry.id}.{fmt}"


__all__ = [
    "FEED_HEADERS",
    "NONE_SENTINEL",
    "PAGE_ROWS",
    "FeedProvenance",
    "FeedShapeChanged",
    "build_provenance",
    "columns_for",
    "filename_for",
    "header_text",
    "iter_rows",
]
