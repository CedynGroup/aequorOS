"""What state a BI answer was read from, and whether that state reconciles.

Three reads that every BI answer needs and that no surface may compute for
itself: the window of dates a query touches, the fingerprint of the mart build
those dates were read from, and the reconciliation verdict for the same window.

They live here rather than on the read route because the ROUTE is no longer the
only caller. A governed export renders the same three values onto the artifact —
the spreadsheet's metadata sheet and the PDF's footer are a provenance record —
and the asynchronous export runs them in the ``bi`` worker lane, where there is
no request, no ``Response`` and no ETag. Two implementations of "which build did
this come from" would be two answers to the same question, and the one on the
artifact is the one a reviewer keeps.

Nothing here decides anything: no authorization, no formatting, no wire model.
:class:`TrustVerdict` is a plain value the caller shapes for its own surface, so
this module stays usable from the worker.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bi import BiMartBuild, BiReconciliationResult
from app.schemas.bi import BiTime
from app.services.bi import reconciliation

#: Separator for digest material. A unit separator cannot occur in a build
#: fingerprint, a scope name or an ISO date, so the digest is unambiguous.
_SEPARATOR = "\x1f"


def data_window(time: BiTime) -> tuple[date, date]:
    """Every date the query can read, comparison window included.

    Mirrors the compiler's own ``_windows`` (pinned by a test): a single as-of
    is one day, a range is itself, and a comparison adds the prior date or an
    equal-length prior window. The badge and the fingerprint must cover the
    prior period too — a comparison reads it.
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
    window over several dates, or a date whose scopes did not all succeed, has
    no single fingerprint: those return one deterministic digest over the build
    state instead, which changes whenever any part of it does. Nothing built at
    all returns ``None``, and the ETag then rests on the catalogue version and
    the principal.
    """

    rows = db.execute(
        select(
            BiMartBuild.as_of_date, BiMartBuild.scope, BiMartBuild.status, BiMartBuild.fingerprint
        )
        .where(
            BiMartBuild.organization_id == organization_id,
            BiMartBuild.bank_id == bank_id,
            BiMartBuild.as_of_date >= window[0],
            BiMartBuild.as_of_date <= window[1],
        )
        .order_by(BiMartBuild.as_of_date, BiMartBuild.scope)
    ).all()
    if not rows:
        return None
    fingerprints = {row.fingerprint for row in rows if row.status == "succeeded"}
    if not fingerprints:
        return None
    if len(fingerprints) == 1 and all(row.status == "succeeded" for row in rows):
        return fingerprints.pop()
    material = _SEPARATOR.join(
        f"{row.as_of_date.isoformat()}:{row.scope}:{row.status}:{row.fingerprint}" for row in rows
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TrustVerdict:
    """The reconciliation verdict for one window, and what is failing in it."""

    status: str
    failing_checks: tuple[str, ...]


def stored_checks(
    db: Session, *, organization_id: str, bank_id: str, window: tuple[date, date]
) -> list[BiReconciliationResult]:
    """Every stored reconciliation result inside the window, in a stable order."""

    return list(
        db.scalars(
            select(BiReconciliationResult)
            .where(
                BiReconciliationResult.organization_id == organization_id,
                BiReconciliationResult.bank_id == bank_id,
                BiReconciliationResult.as_of_date >= window[0],
                BiReconciliationResult.as_of_date <= window[1],
            )
            .order_by(BiReconciliationResult.as_of_date, BiReconciliationResult.check_id)
        )
    )


def trust_verdict(
    db: Session, *, organization_id: str, bank_id: str, window: tuple[date, date]
) -> TrustVerdict:
    """The verdict for everything the window covers; a missing check is grey.

    For a single date this is ``reconciliation.trust_for`` (pinned by a test).
    For a window it is the worst verdict in it — a badge may understate
    confidence, never overstate it — which is also why a date in the window with
    no stored result greys the whole badge rather than being skipped.
    """

    rows = stored_checks(db, organization_id=organization_id, bank_id=bank_id, window=window)
    by_date: dict[date, dict[str, str]] = {}
    for row in rows:
        by_date.setdefault(row.as_of_date, {})[row.check_id] = row.status
    if not by_date:
        return TrustVerdict(status=reconciliation.GREY, failing_checks=())
    overalls: list[str] = []
    failing: set[str] = set()
    for stored in by_date.values():
        statuses = {
            check_id: stored.get(check_id, reconciliation.GREY)
            for check_id in reconciliation.STORABLE_CHECK_IDS
        }
        overalls.append(reconciliation.overall_trust(statuses.values()))
        failing.update(
            check_id
            for check_id, value in statuses.items()
            if value in {reconciliation.RED, reconciliation.AMBER}
        )
    return TrustVerdict(
        status=reconciliation.overall_trust(overalls), failing_checks=tuple(sorted(failing))
    )


__all__ = [
    "TrustVerdict",
    "build_fingerprint",
    "data_window",
    "stored_checks",
    "trust_verdict",
]
