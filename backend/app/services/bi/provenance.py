"""What state a BI answer was read from, and whether that state is current.

Two reads that every BI answer needs and that no surface may compute for
itself: the window of dates a query touches, and the mart build those dates
were read from — its fingerprint, and which dates in the window are being
served from a build that did not succeed.

They live here rather than on the read route because the ROUTE is no longer the
only caller. A governed export renders the same values onto the artifact — the
spreadsheet's metadata sheet and the PDF's footer are a provenance record — and
the asynchronous export runs them in the ``bi`` worker lane, where there is no
request, no ``Response`` and no ETag. Two implementations of "which build did
this come from" would be two answers to the same question, and the one on the
artifact is the one a reviewer keeps.

This module says nothing about whether the figures AGREE with anything. BI is
intelligence over the bank's own treasury and ALM book; grading that book
against the returns the platform files was the regulatory plane's question, and
it left BI on 2026-09-29 (migration ``202609290080``). What remains is about the
bank's OWN data being current: a reader must not take yesterday's rows for
today's, and :func:`stale_dates` is how the alert evaluator and the subscription
runner refuse to act on rows a later build has disowned.

Nothing here decides anything: no authorization, no formatting, no wire model,
so this module stays usable from the worker.
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bi import BiMartBuild
from app.schemas.bi import BiTime

#: Separator for digest material. A unit separator cannot occur in a build
#: fingerprint, a scope name or an ISO date, so the digest is unambiguous.
_SEPARATOR = "\x1f"

#: The one ``bi_mart_builds.status`` under which the rows on file describe the
#: book the build read.
MART_BUILD_SUCCEEDED = "succeeded"


def data_window(time: BiTime) -> tuple[date, date]:
    """Every date the query can read, comparison window included.

    Mirrors the compiler's own ``_windows`` (pinned by a test): a single as-of
    is one day, a range is itself, and a comparison adds the prior date or an
    equal-length prior window. The fingerprint must cover the prior period too —
    a comparison reads it.
    """

    if time.as_of is not None:
        if time.compare_to is None:
            return time.as_of, time.as_of
        return min(time.as_of, time.compare_to), max(time.as_of, time.compare_to)
    assert time.range is not None  # noqa: S101 - BiTime validates exactly one window
    start, end = time.range.start, time.range.end
    if time.compare_to is None:
        return start, end
    prior_start = time.compare_to - (end - start)
    return min(start, prior_start), max(end, time.compare_to)


def build_fingerprint(
    db: Session, *, organization_id: str, bank_id: str, window: tuple[date, date]
) -> str | None:
    """The fingerprint of the mart state the window was read from.

    One successful build stamps every scope of a date with the SAME value-based
    fingerprint, so the common case — one date, fully built — returns that value
    verbatim and an ETag can be compared against the builder's own record. A
    window over several dates, or a date whose scopes did not all succeed — or
    whose latest build FAILED outright — has no single fingerprint: those return
    one deterministic digest over the build state instead, which changes whenever
    any part of it does. Only a window with no build record at all returns
    ``None``, and the ETag then rests on the catalogue version and the principal.

    The failed case used to return ``None`` too (audit A360 H2), which made a
    stale serve unattributable: the builder rolls a failed rebuild back to the
    previous rows, so figures ARE served, and the query log recorded them against
    no build at all. The digest over ``date:scope:failed:<attempted fingerprint>``
    identifies that state exactly — it joins back to the failed
    ``bi_mart_builds`` rows and their error — and it differs from every
    fingerprint a successful build ever stamped, so a cached answer from before
    the failure cannot be mistaken for one served after it.
    """

    rows = _build_rows(db, organization_id=organization_id, bank_id=bank_id, window=window)
    if not rows:
        return None
    fingerprints = {row.fingerprint for row in rows if row.status == MART_BUILD_SUCCEEDED}
    if len(fingerprints) == 1 and all(row.status == MART_BUILD_SUCCEEDED for row in rows):
        return fingerprints.pop()
    material = _SEPARATOR.join(
        f"{row.as_of_date.isoformat()}:{row.scope}:{row.status}:{row.fingerprint}" for row in rows
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _build_rows(
    db: Session, *, organization_id: str, bank_id: str, window: tuple[date, date]
) -> list[Any]:
    """Every ``bi_mart_builds`` record inside the window, in a stable order."""
    return list(
        db.execute(
            select(
                BiMartBuild.as_of_date,
                BiMartBuild.scope,
                BiMartBuild.status,
                BiMartBuild.fingerprint,
            )
            .where(
                BiMartBuild.organization_id == organization_id,
                BiMartBuild.bank_id == bank_id,
                BiMartBuild.as_of_date >= window[0],
                BiMartBuild.as_of_date <= window[1],
            )
            .order_by(BiMartBuild.as_of_date, BiMartBuild.scope)
        ).all()
    )


def stale_dates(
    db: Session, *, organization_id: str, bank_id: str, window: tuple[date, date]
) -> tuple[date, ...]:
    """Dates in the window whose LATEST build did not succeed, in order.

    For such a date the rows being served (if any) were written by an earlier
    build — the builder rolls a failed rebuild back to them — against a canonical
    book that has since moved. Nothing about the bank's current position is
    described by those rows, so no judgement may be made from them (audit A360
    H2): ``alerts.evaluate_bank`` raises no threshold alert and
    ``subscriptions.run_subscription`` mails no pack for a stale date. A
    ``running`` record counts too: it is never visible from another session in
    practice (the builder commits only on success or failure), but "not
    succeeded" is the rule, and a record left mid-flight by a dead process would
    otherwise read as current.
    """
    rows = _build_rows(db, organization_id=organization_id, bank_id=bank_id, window=window)
    return tuple(sorted({row.as_of_date for row in rows if row.status != MART_BUILD_SUCCEEDED}))


__all__ = [
    "MART_BUILD_SUCCEEDED",
    "build_fingerprint",
    "data_window",
    "stale_dates",
]
